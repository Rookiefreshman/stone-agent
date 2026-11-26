import os, uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from agent.contracts import Principal, Forbidden, Conflict, LostClaim
from agent.receipt_check import ReceiptCheckService, OPERATOR
from agent.store import Store
from test_receipts import KEY, DATA, PATH, envelope
from test_stone_http import server

P=Principal("demo",OPERATOR,frozenset({1}))
TOKEN="isolated-receipt-token-123456789"

def svc(s,url,p=P,realm="ci",**kwargs):
    return ReceiptCheckService(s,p,url,TOKEN,realm,enabled=True,**kwargs)

def due(s,cid):
    with s.tx() as c:
        c.execute("UPDATE receipt_check SET next_at=0,lease_until=0 WHERE id=?",(cid,))

def test_persistent_read_only_match(tmp_path):
    s=Store(tmp_path/"checks.sqlite")
    with server(body=envelope()) as (url,seen):
        j=svc(s,url); d=j.register(KEY); cid=d["check_id"]
        assert d["state"]=="UNKNOWN" and d["dispatch_state"]=="NOT_TRACKED"
        assert j.register(KEY)==d
        out=svc(Store(s.path),url).once()
        assert out["state"]=="RECEIPT_MATCHED" and out["receipt"]==DATA and not out["write_allowed"]
        assert j.once() is None
        assert j.view(cid)["attempt_history"][0]["state"]=="OBSERVED"
        assert seen==[("GET",PATH,"Bearer "+TOKEN)]

def test_crash_after_http_recovery_and_old_fence(tmp_path):
    s=Store(tmp_path/"checks.sqlite")
    with server(body=envelope()) as (url,seen):
        j=svc(s,url); cid=j.register(KEY)["check_id"]
        def crash(): raise RuntimeError("simulated process crash")
        with pytest.raises(RuntimeError,match="simulated"): j.once(after_read=crash)
        assert j.view(cid)["state"]=="UNKNOWN"
        stale=j.reader.query(P,KEY)
        due(s,cid); restarted=svc(Store(s.path),url)
        assert restarted.once()["state"]=="RECEIPT_MATCHED"
        with pytest.raises(LostClaim):j._finish(cid,1,stale)
        history=restarted.view(cid)["attempt_history"]
        assert [r["state"] for r in history]==["ABANDONED","OBSERVED"]
        assert all(x[0]=="GET" for x in seen)

def test_missing_budget_no_resubmit(tmp_path):
    s=Store(tmp_path/"checks.sqlite")
    with server(404,{"success":False,"message":"OPERATION_RECEIPT_NOT_FOUND","data":None}) as (url,seen):
        j=svc(s,url); cid=j.register(KEY)["check_id"]
        for i in range(3):
            due(s,cid); out=j.once()
            assert out["state"]=="UNKNOWN" and out["attempts"]==i+1
        assert out["manual_review"] and j.once() is None
        assert j.register(KEY)["attempts"]==3
        assert len(seen)==3 and all(x[0]=="GET" for x in seen)

@pytest.mark.parametrize("status,body",[(403,{}),(401,{}),(200,envelope(dict(DATA,taskId=102)))])
def test_nonretryable_observation_requires_review(tmp_path,status,body):
    s=Store(tmp_path/"checks.sqlite")
    with server(status,body) as (url,seen):
        j=svc(s,url); cid=j.register(KEY)["check_id"]
        out=j.once(); assert out["state"]=="UNKNOWN" and out["manual_review"]
        due(s,cid); assert j.once() is None and len(seen)==1

def test_reference_scope_and_environment(tmp_path):
    s=Store(tmp_path/"checks.sqlite")
    with server(body=envelope()) as (url,seen),server(body=envelope()) as (other,other_seen):
        j=svc(s,url); cid=j.register(KEY)["check_id"]
        with pytest.raises(Conflict,match="REFERENCE_CONFLICT"):j.register(KEY.model_copy(update={"task_id":102}))
        with pytest.raises(Conflict,match="BINDING_CHANGED"):svc(s,other).view(cid)
        assert svc(s,other).once() is None
        with pytest.raises(Forbidden):svc(s,url,Principal("other",OPERATOR,frozenset({1}))).view(cid)
        with pytest.raises(Forbidden):svc(s,url,Principal("demo",OPERATOR,frozenset({2}))).view(cid)
        assert svc(s,url,Principal("demo",OPERATOR,frozenset({2}))).once() is None
        assert not seen and not other_seen

def test_gate_and_integrity(tmp_path):
    s=Store(tmp_path/"checks.sqlite")
    with server(body=envelope()) as (url,seen):
        with pytest.raises(Forbidden):ReceiptCheckService(s,P,url,TOKEN,"ci")
        with pytest.raises(Forbidden):svc(s,url,Principal("demo","local-dev",frozenset({1})))
        with pytest.raises(ValueError):svc(s,"http://example.org")
        j=svc(s,url); cid=j.register(KEY)["check_id"]
        with s.tx() as c:c.execute("UPDATE receipt_check SET reference_hash='tampered' WHERE id=?",(cid,))
        with pytest.raises(Conflict,match="INTEGRITY_ERROR"):j.once()
        assert not seen

def test_last_claim_crash_budget(tmp_path):
    s=Store(tmp_path/"checks.sqlite")
    with server(body=envelope()) as (url,seen):
        j=svc(s,url); cid=j.register(KEY)["check_id"]
        for _ in range(3):
            due(s,cid); assert j._claim()
        due(s,cid)
        assert j.once() is None and j.view(cid)["manual_review"] and not seen

def test_no_business_dispatch_or_model_tool(tmp_path):
    from agent.tools import FixtureStoneService,make_registry
    assert not any("receipt" in k or "cancel" in k for k in make_registry(FixtureStoneService()).snapshot())
    assert not hasattr(ReceiptCheckService,"submit") and not hasattr(ReceiptCheckService,"execute")

def test_offline_schema():
    from pathlib import Path
    sql=Path("migrations/0006_agent_head_offline.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS receipt_check" in sql and "0006_receipt_check" in sql

@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_DSN"),reason="TEST_POSTGRES_DSN not configured")
def test_pg_concurrent_register_and_claim():
    from agent.postgres import PostgresStore
    s=PostgresStore(os.environ["TEST_POSTGRES_DSN"])
    p=Principal("receipt-"+uuid.uuid4().hex,OPERATOR,frozenset({1}))
    with server(body=envelope()) as (url,seen):
        j=svc(s,url,p)
        with ThreadPoolExecutor(4) as ex: ids=list(ex.map(lambda _:j.register(KEY)["check_id"],range(4)))
        assert len(set(ids))==1
        with ThreadPoolExecutor(4) as ex: results=list(ex.map(lambda _:j.once(),range(4)))
        assert len([x for x in results if x])==1 and j.view(ids[0])["state"]=="RECEIPT_MATCHED"
        assert len(seen)==1
