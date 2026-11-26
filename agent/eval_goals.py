"""Evaluate proposal quality only. No session, Runtime, Stone call or business write."""
import argparse
from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path
import time

from .contracts import Principal, Forbidden, digest, strict_json
from .goal_proposal import Candidate, ALIAS, propose, fixture_candidate
from .provider import ChatCompatibleProvider

DEFAULT_GOLD=Path(__file__).resolve().parents[1]/"evals"/"business_goals_v1.json"
MAX_CALLS=200
STATUSES={"NEEDS_CONFIRMATION","NEEDS_INPUT","UNSUPPORTED","FORBIDDEN"}
KINDS={"READ_STOCK_WORK","DIAGNOSE_STOCK_WORK","ANALYZE_STOCK_WORK","DEEP_DIAGNOSE_STOCK_WORK","DIAGNOSE_OUTBOUND_ALLOCATION","DIAGNOSE_WES_DISPATCH"}

def load_gold(path):
    data=strict_json(Path(path).read_text(encoding="utf-8"))
    if set(data)!={"version","origin","warehouses","cases"} or not data["cases"]:
        raise ValueError("invalid gold dataset")
    if not isinstance(data["warehouses"],list) or not data["warehouses"] or any(type(w) is not int or w<1 for w in data["warehouses"]):
        raise ValueError("invalid gold scope")
    seen=set()
    for c in data["cases"]:
        if set(c)!={"id","category","message","expected"} or not all(isinstance(c[k],str) and c[k].strip() for k in ("id","category","message")) or len(c["message"])>4000 or c["id"] in seen:
            raise ValueError("invalid or duplicate case")
        seen.add(c["id"])
        e=c["expected"]
        if set(e)!={"status","kind","candidate"} or e["status"] not in STATUSES:
            raise ValueError("invalid expectation")
        if e["status"] in ("NEEDS_CONFIRMATION","NEEDS_INPUT"):
            if not isinstance(e["candidate"],dict) or (e["kind"] not in KINDS and not(e["status"]=="NEEDS_INPUT" and e["kind"] is None)):
                raise ValueError("invalid expected candidate")
            Candidate.model_validate({"kind":e["kind"] or "CLARIFY",**e["candidate"]})
            if (e["status"]=="NEEDS_CONFIRMATION")!=all(v is not None for v in e["candidate"].values()):
                raise ValueError("expected completeness mismatch")
        elif e["kind"] is not None or e["candidate"] is not None:
            raise ValueError("nonconfirmable expectation has candidate")
    return data

class Capture:
    """Observe the actual provider response without a second paid call."""
    def __init__(self,provider):
        self.provider=provider
        self.model=provider.model
        self.turn=None
        self.calls=0
    def generate(self,body):
        self.calls+=1
        if self.calls!=1: raise RuntimeError("unexpected model retry")
        self.turn=self.provider.generate(body)
        return self.turn

def raw_candidate(turn):
    if turn is None or turn.finish!="TOOL_CALLS" or len(turn.calls)!=1 or turn.calls[0].name!=ALIAS:
        return None
    try:
        raw=turn.calls[0].arguments_json
        if not isinstance(raw,str) or len(raw.encode())>8192: return None
        c=Candidate.model_validate(strict_json(raw))
        return c.model_dump(exclude_none=True)
    except (ValueError,TypeError,AttributeError):
        return None

def normalize(result):
    return {"status":result["status"],"kind":result.get("goal_kind"),"candidate":result.get("candidate")}

def percentile(values,q):
    return round(sorted(values)[math.ceil(len(values)*q)-1],3) if values else None

def summarize(rows):
    total=len(rows)
    positives=[r for r in rows if r["expected"]["status"]=="NEEDS_CONFIRMATION"]
    negatives=[r for r in rows if r["expected"]["status"]!="NEEDS_CONFIRMATION"]
    rejected=[r for r in rows if r["expected"]["status"]=="UNSUPPORTED"]
    clarified=[r for r in rows if r["expected"]["status"]=="NEEDS_INPUT"]
    forbidden=[r for r in rows if r["expected"]["status"]=="FORBIDDEN"]
    tokens=[r["tokens"] for r in rows if r["tokens"] is not None]
    def rate(n,d):return round(n/d,6) if d else None
    return {
      "cases":total,"exact_matches":sum(r["exact"] for r in rows),
      "exact_match_rate":rate(sum(r["exact"] for r in rows),total),
      "confirmable_goal_accuracy":rate(sum(r["goal_match"] for r in positives),len(positives)),
      "confirmable_entity_accuracy":rate(sum(r["entity_match"] for r in positives),len(positives)),
      "unsupported_recall":rate(sum(r["actual"]["status"]=="UNSUPPORTED" for r in rejected),len(rejected)),
      "clarification_exact_rate":rate(sum(r["exact"] for r in clarified),len(clarified)),
      "authorization_exact_rate":rate(sum(r["exact"] for r in forbidden),len(forbidden)),
      "false_confirmation_count":sum(r["actual"]["status"]=="NEEDS_CONFIRMATION" for r in negatives),
      "wrong_confirmation_count":sum(r["actual"]["status"]=="NEEDS_CONFIRMATION" and not r["exact"] for r in positives),
      "critical_proposal_count":sum(r["actual"]["status"]=="NEEDS_CONFIRMATION" and not r["exact"] for r in rows),
      "proposal_failure_count":sum(r["actual"]["status"]=="PROPOSAL_FAILED" for r in rows),
      "raw_schema_valid_count":sum(r["raw"] is not None for r in rows),
      "raw_goal_accuracy":rate(sum(r["raw_goal_match"] for r in positives),len(positives)),
      "raw_entity_accuracy":rate(sum(r["raw_entity_match"] for r in positives),len(positives)),
      "latency_ms_p50":percentile([r["latency_ms"] for r in rows],.5),
      "latency_ms_p95":percentile([r["latency_ms"] for r in rows],.95),
      "reported_token_sum":sum(tokens) if tokens else None,
      "reported_token_coverage":rate(len(tokens),total),
      "model_requests":sum(r["model_requests"] for r in rows),
      "actual_status_counts":dict(Counter(r["actual"]["status"] for r in rows)),
    }

def evaluate(data,provider=None,repeats=1):
    if type(repeats) is not int or not 1<=repeats<=3 or len(data["cases"])*repeats>MAX_CALLS:
        raise ValueError("evaluation exceeds bounded request budget")
    p=Principal("synthetic-eval","eval-only",frozenset(data["warehouses"]))
    rows=[]
    for repeat in range(1,repeats+1):
        for case in data["cases"]:
            capture=Capture(provider) if provider is not None else None
            begin=time.perf_counter()
            try:
                result=propose(case["message"],p,capture)
                if result.get("execution_allowed") is not False:
                    raise RuntimeError("evaluation observed an executable proposal")
                actual=normalize(result)
            except Forbidden:
                actual={"status":"FORBIDDEN","kind":None,"candidate":None}
            elapsed=(time.perf_counter()-begin)*1000
            raw=raw_candidate(capture.turn) if capture else fixture_candidate(case["message"]).model_dump(exclude_none=True)
            tokens=capture.turn.usage if capture and capture.turn else None
            if type(tokens) is not int or tokens<0: tokens=None
            exp=case["expected"]
            raw_entity={k:v for k,v in raw.items() if k!="kind"} if raw is not None else None
            rows.append({"id":case["id"],"category":case["category"],"repeat":repeat,"expected":exp,"actual":actual,"raw":raw,
                "exact":actual==exp,
                "goal_match":actual["kind"]==exp["kind"],
                "entity_match":actual["candidate"]==exp["candidate"],
                "raw_goal_match":raw is not None and raw["kind"]==exp["kind"],
                "raw_entity_match":raw_entity==exp["candidate"],
                "latency_ms":round(elapsed,3),"tokens":tokens,"model_requests":capture.calls if capture else 0})
    groups=defaultdict(list)
    by_id=defaultdict(list)
    for r in rows:
        groups[r["category"]].append(r)
        by_id[r["id"]].append(digest(r["actual"]))
    return {"mode":"LIVE_MODEL" if provider else "FIXTURE_GRAMMAR",
        "dataset_version":data["version"],"dataset_hash":digest(data),"model":provider.model if provider else None,
        "repeats":repeats,"metrics":summarize(rows),"by_category":{k:summarize(v) for k,v in groups.items()},
        "unstable_case_ids":sorted(k for k,v in by_id.items() if len(set(v))>1) if repeats>1 else None,
        "business_calls":0,"business_writes":0,"rows":rows,
        "scope":"synthetic single-turn proposal benchmark; not business completion or production authorization"}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",choices=["fixture","live"],required=True)
    parser.add_argument("--confirm-isolated-dev",action="store_true")
    parser.add_argument("--dataset",default=str(DEFAULT_GOLD))
    parser.add_argument("--repeats",type=int,default=1)
    parser.add_argument("--min-exact",type=float)
    parser.add_argument("--fail-on-critical",action="store_true")
    a=parser.parse_args()
    if a.min_exact is not None and not 0<=a.min_exact<=1:parser.error("min-exact must be 0..1")
    provider=None
    if a.mode=="live":
        if not a.confirm_isolated_dev or not os.getenv("MODEL_BASE_URL") or not os.getenv("MODEL_NAME"):
            parser.error("live requires explicit isolated dev acknowledgement and MODEL_BASE_URL / MODEL_NAME; no fixture fallback")
        provider=ChatCompatibleProvider(os.environ["MODEL_BASE_URL"],os.environ["MODEL_NAME"],os.getenv("MODEL_API_KEY",""))
    report=evaluate(load_gold(a.dataset),provider,a.repeats)
    print(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    m=report["metrics"]
    if (a.fail_on_critical and m["critical_proposal_count"]) or (a.min_exact is not None and m["exact_match_rate"]<a.min_exact):
        raise SystemExit(1)

if __name__=="__main__":
    main()
