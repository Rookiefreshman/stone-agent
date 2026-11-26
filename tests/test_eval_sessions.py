import json
import pytest
from agent.eval_sessions import DEFAULT_GOLD,load_dataset,evaluate,main
from agent.contracts import ModelTurn,ToolCall,ModelAttemptError
from agent.goal_proposal import ALIAS

def subset(*ids):
    d=load_dataset(DEFAULT_GOLD)
    d["scenarios"]=[s for s in d["scenarios"] if s["id"] in ids]
    return d

def test_dataset_coverage_and_turn_budget():
    d=load_dataset(DEFAULT_GOLD)
    assert len(d["scenarios"])==14
    assert {"confirmed_context","pending_clarification","unconfirmed_context","cross_domain","cross_session","authorization","expiry","active_run"}=={s["category"] for s in d["scenarios"]}
    assert sum(len(s["steps"]) for s in d["scenarios"])==30

def test_fixture_multi_turn_session_baseline():
    r=evaluate(load_dataset(DEFAULT_GOLD))
    m=r["metrics"]
    assert r["mode"]=="FIXTURE_SESSION"
    assert m["scenarios"]==14 and m["exact_scenarios"]==14 and m["scenario_exact_rate"]==1
    assert m["expected_turns"]==30 and m["executed_turns"]==30 and m["skipped_turns"]==0
    assert m["critical_proposal_count"]==0
    assert m["confirmation_count"]==16 and m["fixture_completed_runs"]==15
    assert m["fixture_service_invocations"]==17 and m["local_rule_invocations"]==17
    assert m["runtime_model_calls"]==0 and m["model_requests"]==0
    assert r["stone_http_calls"]==r["stone_business_writes"]==0
    assert r["unstable_scenarios"] is None

class Provider:
    model="evaluation-only"
    revision="provider-fixture-v1"
    def __init__(self,turn=None,error=None): self.turn=turn; self.error=error; self.calls=0
    def generate(self,body):
        self.calls+=1
        assert body["tools"][0]["function"]["name"]==ALIAS
        if self.error: raise self.error
        return self.turn

def model_turn(kind,**fields):
    return ModelTurn("TOOL_CALLS",(ToolCall("call-1",ALIAS,json.dumps({"kind":kind,**fields})),),17)

def test_wrong_model_candidate_never_creates_local_run_or_fixture_call():
    p=Provider(model_turn("READ_STOCK_WORK",warehouse_id=1,task_id=104))
    r=evaluate(subset("M01"),p)
    assert r["metrics"]["critical_proposal_count"]==0
    assert r["metrics"]["confirmation_count"]==0
    assert r["metrics"]["fixture_service_invocations"]==0
    assert r["metrics"]["executed_turns"]==1 and r["metrics"]["skipped_turns"]==1
    assert r["rows"][0]["actual"]["status"]=="NEEDS_INPUT"
    assert r["stone_http_calls"]==0

def test_provider_failure_is_not_counted_as_semantic_rejection():
    p=Provider(error=ModelAttemptError("transport"))
    r=evaluate(subset("M01"),p)
    assert r["metrics"]["model_requests"]==1
    assert r["rows"][0]["actual"]["status"]=="PROPOSAL_FAILED"
    assert r["metrics"]["critical_proposal_count"]==0
    assert r["metrics"]["skipped_turns"]==1

def test_active_run_and_context_expiry_are_exercised():
    r=evaluate(subset("M13","M14"))
    rows={x["step_id"]:x for x in r["rows"] if not x.get("skipped")}
    assert rows["M13-2"]["actual"]["status"]=="UNSUPPORTED"
    assert rows["M14-2"]["actual"]=={"error":"ACTIVE_RUN"}
    assert r["metrics"]["critical_proposal_count"]==0

def test_repeat_stability_and_budget():
    r=evaluate(subset("M01","M08"),repeats=2)
    assert r["metrics"]["scenarios"]==4 and r["metrics"]["exact_scenarios"]==4
    assert r["unstable_scenarios"]==[]
    with pytest.raises(ValueError): evaluate(load_dataset(DEFAULT_GOLD),repeats=4)

def test_live_requires_explicit_isolated_configuration(monkeypatch):
    import sys
    monkeypatch.delenv("MODEL_BASE_URL",raising=False)
    monkeypatch.delenv("MODEL_NAME",raising=False)
    monkeypatch.setattr(sys,"argv",["eval","--mode","live","--confirm-isolated-dev"])
    with pytest.raises(SystemExit) as exc: main()
    assert exc.value.code==2

def test_dataset_rejects_duplicate_turns(tmp_path):
    d=subset("M01")
    d["scenarios"][0]["steps"].append(dict(d["scenarios"][0]["steps"][0]))
    p=tmp_path/"bad.json";p.write_text(json.dumps(d))
    with pytest.raises(ValueError):load_dataset(p)
