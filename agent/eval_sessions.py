"""Multi-turn session benchmark. All confirmed runs use local SQLite + FixtureStoneService only."""
import argparse
from collections import defaultdict
import json
import math
import os
import tempfile
import time
from pathlib import Path

from .business_language import EXTENDED
from .contracts import (
    Conflict, Forbidden, ModelAttemptError, Principal, StockWorkArgs,
    digest, strict_json,
)
from .goal_proposal import ALIAS, Candidate
from .provider import ChatCompatibleProvider, FixtureProvider
from .runtime import Runtime
from .session import SessionService
from .store import RULES, Store
from .tools import FixtureStoneService, make_registry

DEFAULT_GOLD=Path(__file__).resolve().parents[1]/"evals"/"business_sessions_v1.json"
MAX_TURNS=200
STATUSES={"NEEDS_CONFIRMATION","NEEDS_INPUT","UNSUPPORTED","FORBIDDEN","PROPOSAL_FAILED"}

class CountingProvider:
    """Count model attempts and preserve returned turns without logging secrets or request bodies."""
    def __init__(self,provider):
        self.provider=provider
        self.model=provider.model
        self.revision=provider.revision
        self.requests=0
        self.turns=[]
    def generate(self,body):
        self.requests+=1
        turn=self.provider.generate(body)
        self.turns.append(turn)
        return turn

def load_dataset(path):
    data=strict_json(Path(path).read_text(encoding="utf-8"))
    if set(data)!={"version","origin","scenarios"} or not isinstance(data["scenarios"],list) or not data["scenarios"]:
        raise ValueError("invalid session gold dataset")
    scenario_ids=set()
    for scenario in data["scenarios"]:
        if set(scenario)-{"id","category","warehouses","steps"} or not {"id","category","warehouses","steps"}<=set(scenario):
            raise ValueError("invalid scenario schema")
        if not isinstance(scenario["id"],str) or not scenario["id"] or scenario["id"] in scenario_ids:
            raise ValueError("duplicate or empty scenario id")
        scenario_ids.add(scenario["id"])
        if not isinstance(scenario["category"],str) or not scenario["category"]:
            raise ValueError("invalid scenario category")
        if not isinstance(scenario["warehouses"],list) or not scenario["warehouses"] or any(type(x) is not int or x<1 for x in scenario["warehouses"]):
            raise ValueError("invalid scenario warehouse scope")
        if not isinstance(scenario["steps"],list) or not scenario["steps"]:
            raise ValueError("scenario requires turns")
        step_ids=set()
        for step in scenario["steps"]:
            allowed={"id","message","expected","expected_live","confirm","complete_fixture","session","warehouses","advance_seconds"}
            if set(step)-allowed or not {"id","message","expected"}<=set(step):
                raise ValueError("invalid turn schema")
            if not isinstance(step["id"],str) or not step["id"] or step["id"] in step_ids:
                raise ValueError("duplicate or empty turn id")
            step_ids.add(step["id"])
            msg=step["message"]
            if not isinstance(msg,dict) or set(msg)-{"fixture","live"} or not isinstance(msg.get("fixture"),str) or not msg["fixture"].strip() or ("live" in msg and (not isinstance(msg["live"],str) or not msg["live"].strip())):
                raise ValueError("invalid turn messages")
            for expected in (step["expected"],step.get("expected_live",step["expected"])):
                if not isinstance(expected,dict):
                    raise ValueError("invalid turn expectation")
                if set(expected)=={"error"}:
                    if expected["error"]!="ACTIVE_RUN": raise ValueError("unsupported expected error")
                    continue
                if set(expected)!={"status","kind","candidate"} or expected["status"] not in STATUSES:
                    raise ValueError("invalid expected proposal")
                candidate=expected["candidate"]
                if expected["status"] in ("NEEDS_CONFIRMATION","NEEDS_INPUT"):
                    if not isinstance(candidate,dict): raise ValueError("proposal candidate required")
                    if expected["kind"] in EXTENDED:
                        fields=set(EXTENDED[expected["kind"]].model_fields)
                    elif expected["kind"] in ("READ_STOCK_WORK","DIAGNOSE_STOCK_WORK","ANALYZE_STOCK_WORK"):
                        fields={"warehouse_id","task_id"}
                    elif expected["kind"] is None:
                        fields={"warehouse_id","task_id"}
                    else: raise ValueError("invalid expected goal kind")
                    if set(candidate)!=fields: raise ValueError("candidate fields do not match goal type")
                    if expected["status"]=="NEEDS_CONFIRMATION":
                        model=EXTENDED[expected["kind"]] if expected["kind"] in EXTENDED else StockWorkArgs
                        model.model_validate(candidate)
                    else:
                        for k,v in candidate.items():
                            if v is not None and (type(v) is not int and k!="device_code"):
                                raise ValueError("invalid partial candidate field")
                elif candidate is not None or expected["kind"] is not None:
                    raise ValueError("rejected proposals cannot contain a candidate")
            if step.get("confirm",False) and step["expected"]["status"]!="NEEDS_CONFIRMATION":
                raise ValueError("only an expected confirmable proposal may be confirmed")
            if step.get("complete_fixture",False) and not step.get("confirm",False):
                raise ValueError("fixture completion requires explicit confirmation")
            if "advance_seconds" in step and (type(step["advance_seconds"]) is not int or step["advance_seconds"]<0):
                raise ValueError("invalid clock advance")
            if "warehouses" in step and (not isinstance(step["warehouses"],list) or any(type(x) is not int or x<1 for x in step["warehouses"])):
                raise ValueError("invalid per-turn warehouse scope")
    return data

def raw_candidate(turn):
    if turn is None or turn.finish!="TOOL_CALLS" or len(turn.calls)!=1 or turn.calls[0].name!=ALIAS:
        return None
    try:
        raw=turn.calls[0].arguments_json
        if not isinstance(raw,str) or len(raw.encode())>8192: return None
        return Candidate.model_validate(strict_json(raw)).model_dump(exclude_none=True)
    except (ValueError,TypeError,AttributeError):
        return None

def normalize(result):
    return {"status":result["status"],"kind":result.get("goal_kind"),"candidate":result.get("candidate")}

def percentile(values,q):
    return round(sorted(values)[math.ceil(len(values)*q)-1],3) if values else None

def _expected(step,live):
    return step.get("expected_live",step["expected"]) if live else step["expected"]

def _entity(kind,candidate):
    model=EXTENDED[kind] if kind in EXTENDED else StockWorkArgs
    return model.model_validate(candidate)

def evaluate(data,provider=None,repeats=1):
    total_turns=sum(len(s["steps"]) for s in data["scenarios"])
    if type(repeats) is not int or not 1<=repeats<=3 or total_turns*repeats>MAX_TURNS:
        raise ValueError("session evaluation exceeds bounded turn budget")
    capture=CountingProvider(provider) if provider is not None else None
    rows=[]
    scenario_hashes=defaultdict(list)
    for repeat in range(1,repeats+1):
        for scenario in data["scenarios"]:
            with tempfile.TemporaryDirectory(prefix="stone-session-eval-") as tmp:
                store=Store(Path(tmp)/"eval.sqlite",RULES)
                service=SessionService(store,provider=capture,ttl=300,clock=lambda:clock[0] if clock else 0)
                domain=FixtureStoneService()
                runtime_provider=FixtureProvider()
                runtime=Runtime(store,runtime_provider,make_registry(domain))
                clock=[1_000_000.0]
                sessions={}
                scenario_rows=[]
                failed=False
                for index,step in enumerate(scenario["steps"]):
                    if failed:
                        rows.append({"scenario_id":scenario["id"],"category":scenario["category"],"step_id":step["id"],"repeat":repeat,"skipped":True,"exact":False})
                        continue
                    if step.get("advance_seconds"): clock[0]+=step["advance_seconds"]
                    session_key=step.get("session","main")
                    if session_key not in sessions:
                        principal=Principal("eval-tenant","eval-actor",frozenset(step.get("warehouses",scenario["warehouses"])))
                        sessions[session_key]=service.create(principal)["session_id"]
                    principal=Principal("eval-tenant","eval-actor",frozenset(step.get("warehouses",scenario["warehouses"])))
                    sid=sessions[session_key]
                    message=step["message"].get("live",step["message"]["fixture"]) if provider is not None else step["message"]["fixture"]
                    expected=_expected(step,provider is not None)
                    request_before=capture.requests if capture else 0
                    turns_before=len(capture.turns) if capture else 0
                    started=time.perf_counter()
                    try:
                        result=service.propose(principal,sid,f'{scenario["id"]}:{repeat}:{step["id"]}',message)
                        if result.get("execution_allowed") is not False:
                            raise RuntimeError("session proposal unexpectedly permits execution")
                        actual=normalize(result)
                    except Conflict as exc:
                        actual={"error":str(exc)}
                        result=None
                    except Forbidden:
                        actual={"error":"FORBIDDEN"}
                        result=None
                    elapsed=(time.perf_counter()-started)*1000
                    new_turns=capture.turns[turns_before:] if capture else []
                    raw=raw_candidate(new_turns[0]) if len(new_turns)==1 else None
                    tokens=new_turns[0].usage if len(new_turns)==1 and type(new_turns[0].usage) is int and new_turns[0].usage>=0 else None
                    exact=actual==expected
                    confirmation=None
                    execution_ok=True
                    if exact and step.get("confirm",False):
                        kind=result.get("goal_kind") or "READ_STOCK_WORK"
                        try:
                            entity=_entity(kind,result["candidate"])
                            confirmation=service.confirm(principal,sid,result["proposal_id"],entity,kind)
                            if step.get("complete_fixture",False):
                                executed=runtime.advance(principal,confirmation["run_id"])
                                execution_ok=executed["state"]=="COMPLETED"
                                if runtime_provider.calls:
                                    execution_ok=False
                            if not execution_ok:
                                failed=True
                        except (Conflict,Forbidden,ValueError) as exc:
                            execution_ok=False
                            confirmation={"error":str(exc)}
                            failed=True
                    elif not exact:
                        failed=True
                    row={"scenario_id":scenario["id"],"category":scenario["category"],"step_id":step["id"],"repeat":repeat,
                        "message":message,"expected":expected,"actual":actual,"source":result.get("source") if result else None,
                        "raw":raw,"raw_schema_valid":raw is not None,"exact":exact,"execution_ok":execution_ok,
                        "confirmation_created":confirmation is not None and "run_id" in confirmation,
                        "latency_ms":round(elapsed,3),"tokens":tokens,"model_requests":(capture.requests-request_before) if capture else 0,
                        "fixture_calls_total":domain.calls,"runtime_model_calls":runtime_provider.calls}
                    rows.append(row); scenario_rows.append(row)
                    if not exact or not execution_ok: failed=True
                scenario_hashes[scenario["id"]].append(digest([{"actual":r.get("actual"),"execution_ok":r.get("execution_ok"),"skipped":r.get("skipped",False)} for r in rows if r.get("scenario_id")==scenario["id"] and r.get("repeat")==repeat]))
    executed=[r for r in rows if not r.get("skipped")]
    by_category=defaultdict(list)
    for r in rows: by_category[r["category"]].append(r)
    positives=[r for r in executed if r["expected"]["status"]=="NEEDS_CONFIRMATION"]
    negatives=[r for r in executed if r["expected"].get("status")!="NEEDS_CONFIRMATION"]
    model_positive=[r for r in positives if r["model_requests"]>0]
    tokens=[r["tokens"] for r in executed if r["tokens"] is not None]
    exact_turns=sum(r["exact"] and r.get("execution_ok",True) for r in executed)
    false_confirm=sum(r.get("actual",{}).get("status")=="NEEDS_CONFIRMATION" for r in negatives)
    wrong_confirm=sum(r.get("actual",{}).get("status")=="NEEDS_CONFIRMATION" and not r["exact"] for r in positives)
    raw_exact=sum(r.get("raw") is not None and r["raw"].get("kind")==r["expected"].get("kind") and {k:v for k,v in r["raw"].items() if k!="kind"}==r["expected"].get("candidate") for r in model_positive)
    def ratio(n,d): return round(n/d,6) if d else None
    def summary(items):
        scenario_ids={r["scenario_id"] for r in items}
        exact_scenarios=sum(1 for sid in scenario_ids if all(x.get("exact",False) and x.get("execution_ok",True) and not x.get("skipped",False) for x in items if x["scenario_id"]==sid))
        run_items=[r for r in items if not r.get("skipped")]
        return {"scenarios":len(scenario_ids),"exact_scenarios":exact_scenarios,"scenario_exact_rate":ratio(exact_scenarios,len(scenario_ids)),
            "turns":len(items),"executed_turns":len(run_items),"exact_turns":sum(r.get("exact",False) and r.get("execution_ok",True) for r in run_items),
            "turn_exact_rate":ratio(sum(r.get("exact",False) and r.get("execution_ok",True) for r in run_items),len(run_items))}
    scenario_total=len(data["scenarios"])*repeats
    scenario_exact=sum(1 for sid,hashes in scenario_hashes.items() for _ in [] ) # replaced below
    scenario_exact_count=sum(1 for repeat in range(1,repeats+1) for s in data["scenarios"] if all(r.get("exact",False) and r.get("execution_ok",True) and not r.get("skipped",False) for r in rows if r["scenario_id"]==s["id"] and r["repeat"]==repeat))
    metrics={"scenarios":scenario_total,"exact_scenarios":scenario_exact_count,"scenario_exact_rate":ratio(scenario_exact_count,scenario_total),
        "expected_turns":total_turns*repeats,"executed_turns":len(executed),"skipped_turns":sum(r.get("skipped",False) for r in rows),
        "exact_turns":exact_turns,"turn_exact_rate":ratio(exact_turns,len(executed)),
        "confirmable_goal_entity_exact_rate":ratio(sum(r["exact"] for r in positives),len(positives)),
        "false_confirmation_count":false_confirm,"wrong_confirmation_count":wrong_confirm,"critical_proposal_count":false_confirm+wrong_confirm,
        "confirmation_count":sum(r.get("confirmation_created",False) for r in executed),
        "fixture_completed_runs":sum(1 for r in executed if r.get("confirmation_created") and r.get("execution_ok") and r.get("confirmation_created") and r.get("actual",{}).get("status")=="NEEDS_CONFIRMATION"),
        "fixture_service_invocations":max((r.get("fixture_calls_total",0) for r in executed),default=0),
        "runtime_model_calls":sum(r.get("runtime_model_calls",0) for r in executed),
        "model_requests":capture.requests if capture else 0,"raw_schema_valid_count":sum(r.get("raw_schema_valid",False) for r in executed),
        "raw_confirmable_exact_rate":ratio(raw_exact,len(model_positive)),
        "reported_token_sum":sum(tokens) if tokens else None,"reported_token_coverage":ratio(len(tokens),capture.requests if capture else 0),
        "latency_ms_p50":percentile([r["latency_ms"] for r in executed],.5),"latency_ms_p95":percentile([r["latency_ms"] for r in executed],.95)}
    return {"mode":"LIVE_MODEL" if provider else "FIXTURE_SESSION","dataset_version":data["version"],"dataset_hash":digest(data),
        "model":provider.model if provider else None,"repeats":repeats,"metrics":metrics,
        "by_category":{k:summary(v) for k,v in by_category.items()},
        "unstable_scenarios":sorted(sid for sid,hashes in scenario_hashes.items() if len(set(hashes))>1) if repeats>1 else None,
        "stone_http_calls":0,"stone_business_writes":0,"scope":"multi-turn proposal/session contract evaluation; local SQLite and FixtureStoneService only; not production business completion",
        "rows":rows}

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode",choices=["fixture","live"],required=True)
    parser.add_argument("--confirm-isolated-dev",action="store_true")
    parser.add_argument("--dataset",default=str(DEFAULT_GOLD))
    parser.add_argument("--repeats",type=int,default=1)
    parser.add_argument("--min-exact",type=float)
    parser.add_argument("--fail-on-critical",action="store_true")
    args=parser.parse_args()
    if args.min_exact is not None and not 0<=args.min_exact<=1: parser.error("min-exact must be 0..1")
    provider=None
    if args.mode=="live":
        if not args.confirm_isolated_dev or not os.getenv("MODEL_BASE_URL") or not os.getenv("MODEL_NAME"):
            parser.error("live requires explicit isolated dev acknowledgement and MODEL_BASE_URL / MODEL_NAME; no fixture fallback")
        provider=ChatCompatibleProvider(os.environ["MODEL_BASE_URL"],os.environ["MODEL_NAME"],os.getenv("MODEL_API_KEY",""))
    report=evaluate(load_dataset(args.dataset),provider,args.repeats)
    print(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))
    m=report["metrics"]
    if (args.fail_on_critical and m["critical_proposal_count"]) or (args.min_exact is not None and m["scenario_exact_rate"]<args.min_exact):
        raise SystemExit(1)

if __name__=="__main__":
    main()
