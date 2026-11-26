import json
import pytest
from agent.eval_goals import load_gold,DEFAULT_GOLD,evaluate,raw_candidate,main
from agent.contracts import ModelTurn,ToolCall,ModelAttemptError
from agent.goal_proposal import ALIAS

def test_gold_independent_coverage():
    d=load_gold(DEFAULT_GOLD)
    assert len(d["cases"])==44
    assert {c["category"] for c in d["cases"]}=={"baseline","paraphrase","role_collision","clarification","unsupported","authorization"}
    assert len({c["expected"]["kind"] for c in d["cases"] if c["expected"]["status"]=="NEEDS_CONFIRMATION"})==6

def test_fixture_report_exposes_limit_not_fake_accuracy():
    d=load_gold(DEFAULT_GOLD);r=evaluate(d)
    assert r["mode"]=="FIXTURE_GRAMMAR" and r["metrics"]["cases"]==44
    assert 0<r["metrics"]["exact_matches"]<44
    assert r["metrics"]["model_requests"]==0 and r["metrics"]["reported_token_sum"] is None
    assert r["business_calls"]==r["business_writes"]==0 and r["unstable_case_ids"] is None
    assert r["metrics"]["critical_proposal_count"]==0
    assert r["metrics"]["exact_matches"]==sum(x["exact"] for x in r["rows"])

class Provider:
    model="test-only"
    def __init__(self,answers):self.answers=iter(answers);self.calls=0
    def generate(self,body):
        self.calls+=1
        assert len(body["tools"])==1 and body["tools"][0]["function"]["name"]==ALIAS
        answer=next(self.answers)
        if isinstance(answer,Exception):raise answer
        return answer

def turn(kind,usage=12,**kw):
    return ModelTurn("TOOL_CALLS",(ToolCall("id",ALIAS,json.dumps({"kind":kind,**kw})),),usage)

def subset(*ids):
    d=load_gold(DEFAULT_GOLD)
    d["cases"]=[c for id in ids for c in d["cases"] if c["id"]==id]
    return d

def test_raw_and_final_metrics_not_conflated():
    p=Provider([turn("DIAGNOSE_OUTBOUND_ALLOCATION",warehouse_id=1,order_id=1)])
    r=evaluate(subset("O01"),p)
    row=r["rows"][0]
    assert row["raw"]["order_id"]==1 and row["actual"]["status"]=="NEEDS_INPUT"
    assert r["metrics"]["raw_goal_accuracy"]==1 and r["metrics"]["raw_entity_accuracy"]==0
    assert r["metrics"]["exact_match_rate"]==0 and p.calls==1
    assert r["metrics"]["reported_token_sum"]==12 and r["metrics"]["reported_token_coverage"]==1

def test_false_confirmation_and_wrong_confirmation_counts():
    # Simulate a wrong read goal for a write request: explicit confirmation
    # still blocks execution, but the proposal is a critical quality failure.
    p=Provider([turn("READ_STOCK_WORK",warehouse_id=1,task_id=103),turn("DIAGNOSE_STOCK_WORK",warehouse_id=1,task_id=103)])
    r=evaluate(subset("N01","S01"),p)
    assert r["metrics"]["false_confirmation_count"]==1
    assert r["metrics"]["wrong_confirmation_count"]==1
    assert r["metrics"]["critical_proposal_count"]==2

def test_repeated_model_and_missing_usage():
    p=Provider([turn("READ_STOCK_WORK",None,warehouse_id=1,task_id=103),turn("UNSUPPORTED")])
    r=evaluate(subset("S01"),p,repeats=2)
    assert r["unstable_case_ids"]==["S01"] and r["metrics"]["model_requests"]==2
    assert r["metrics"]["reported_token_sum"]==12 and r["metrics"]["reported_token_coverage"]==.5

def test_transport_error_is_not_semantic_rejection():
    p=Provider([ModelAttemptError("no connection")])
    r=evaluate(subset("N01"),p)
    assert r["rows"][0]["actual"]["status"]=="PROPOSAL_FAILED"
    assert r["metrics"]["unsupported_recall"]==0 and r["metrics"]["proposal_failure_count"]==1
    assert r["metrics"]["raw_schema_valid_count"]==0 and p.calls==1

def test_bad_tool_protocol():
    assert raw_candidate(ModelTurn("STOP")) is None
    assert raw_candidate(ModelTurn("TOOL_CALLS",(ToolCall("x",ALIAS,'{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":103,"task_id":104}'),))) is None

def test_budget():
    with pytest.raises(ValueError):evaluate(load_gold(DEFAULT_GOLD),repeats=4)

def test_live_requires_configuration(monkeypatch):
    import sys
    monkeypatch.delenv("MODEL_BASE_URL",raising=False);monkeypatch.delenv("MODEL_NAME",raising=False)
    monkeypatch.setattr(sys,"argv",["eval","--mode","live","--confirm-isolated-dev"])
    with pytest.raises(SystemExit) as e:main()
    assert e.value.code==2

def test_duplicate_gold_rejected(tmp_path):
    d=subset("S01");d["cases"]*=2
    p=tmp_path/"duplicate.json";p.write_text(json.dumps(d))
    with pytest.raises(ValueError):load_gold(p)

def test_fixture_gate_failure_reports(monkeypatch,capsys):
    import sys
    monkeypatch.setattr(sys,"argv",["eval","--mode","fixture","--min-exact","1"])
    with pytest.raises(SystemExit) as e:main()
    assert e.value.code==1 and json.loads(capsys.readouterr().out)["metrics"]["exact_match_rate"]<1
