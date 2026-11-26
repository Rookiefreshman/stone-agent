import os, uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from agent.contracts import Principal, Conflict, Forbidden, LostClaim
from agent.receipt_check import OPERATOR, ReceiptCheckService
from agent.store import Store
from test_receipt_check import P,TOKEN,svc,due
from test_receipts import KEY,DATA,envelope
from test_stone_http import server

def manual(j,cid,fence,req="review-1",reason="核对原始操作记录"):
    return j.manual_retry(cid,request_id=req,expected_fence=fence,reason=reason)

def exhaust(s,j,cid):
    for _ in range(3):
        due(s,cid)
        assert j.once()["state"]=="UNKNOWN"

def test_review_after_budget_and_late_receipt(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    body={"success":False,"message":"OPERATION_RECEIPT_NOT_FOUND","data":None}
    with server(404,body) as (url,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"]
        exhaust(s,j,cid)
        assert j.pending_reviews()["items"][0]["check_id"]==cid
        assert j.once() is None
        out=manual(j,cid,3)
        assert out["state"]=="UNKNOWN" and out["attempts"]==4 and out["manual_review"]
        assert out["manual_attempts_remaining"]==1 and j.once() is None
        replay=manual(j,cid,3)
        assert replay["replayed"] and len(seen)==4
        with pytest.raises(Conflict,match="REVIEW_REQUEST_CONFLICT"):manual(j,cid,3,reason="changed")
        out=manual(j,cid,4,"review-2")
        assert out["manual_attempts_remaining"]==0
        with pytest.raises(Conflict,match="BUDGET_EXHAUSTED"):manual(j,cid,5,"review-3")
        assert j.once() is None and len(seen)==5
        assert len(j.view(cid)["review_history"])==2

def test_permission_hold_recovery(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    with server(body=envelope()) as (url,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"]
        from agent.receipts import unknown
        claim=j._claim()
        j._finish(cid,claim[1],unknown("RECEIPT_HTTP_403"))
        assert j.pending_reviews()["items"][0]["fence"]==1
        out=manual(j,cid,1)
        assert out["state"]=="RECEIPT_MATCHED" and out["dispatch_state"]=="NOT_TRACKED"
        assert not out["write_allowed"] and out["receipt"]==DATA
        assert j.pending_reviews()["items"]==[]
        with pytest.raises(Conflict,match="REVIEW_NOT_AVAILABLE"):manual(j,cid,2,"new")
        assert len(seen)==1 and seen[0][0]=="GET"

def test_review_reject_active_and_stale_fence(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    with server(body=envelope()) as (url,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"]
        with pytest.raises(Conflict,match="REVIEW_NOT_AVAILABLE"):manual(j,cid,0)
        claim=j._claim()
        with pytest.raises(Conflict,match="REVIEW_NOT_AVAILABLE"):manual(j,cid,1)
        assert j.pending_reviews()["items"]==[]
        with pytest.raises(Conflict,match="REVIEW_FENCE_CHANGED"):manual(j,cid,0)
        assert not seen

def test_manual_crash_recovery_does_not_replay_get(tmp_path,monkeypatch):
    s=Store(tmp_path/"check.sqlite")
    from agent.receipts import unknown
    with server(body=envelope()) as (url,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"]; claim=j._claim()
        j._finish(cid,claim[1],unknown("RECEIPT_HTTP_403"))
        def crash(*a):raise RuntimeError("crash")
        monkeypatch.setattr(j.reader,"query",crash)
        with pytest.raises(RuntimeError,match="crash"):manual(j,cid,1)
        assert j.view(cid)["manual_attempts"]==1 and not j.view(cid)["manual_review"]
        assert j.pending_reviews()["items"]==[]
        due(s,cid)
        j2=svc(Store(s.path),url)
        assert manual(j2,cid,1)["replayed"] and not seen
        out=manual(j2,cid,2,"review-2")
        assert out["state"]=="RECEIPT_MATCHED"
        assert [x["state"] for x in j2.view(cid)["attempt_history"]]==["OBSERVED","ABANDONED","OBSERVED"]
        with pytest.raises(LostClaim):j._finish(cid,2,j2.reader.query(P,KEY))

def test_review_scope_and_validation(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    with server(403,{}) as (url,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"]; j.once()
        other=svc(s,url,Principal("other",OPERATOR,frozenset({1})))
        assert other.pending_reviews()["items"]==[]
        with pytest.raises(Forbidden):manual(other,cid,1)
        wrong=svc(s,url,Principal("demo",OPERATOR,frozenset({2})))
        assert wrong.pending_reviews()["items"]==[]
        with pytest.raises(Forbidden):manual(wrong,cid,1)
        for kwargs in (dict(req="bad request"),dict(reason=""),dict(reason="bad\nreason")):
            with pytest.raises(ValueError):manual(j,cid,1,**kwargs)
        with pytest.raises(ValueError):manual(j,cid,True)
        assert len(seen)==1

def test_pending_pagination(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    with server(403,{}) as (url,seen):
        j=svc(s,url)
        ids=[j.register(KEY.model_copy(update={"operation_id":str(uuid.uuid4())}))["check_id"] for _ in range(3)]
        for _ in ids:j.once()
        first=j.pending_reviews(limit=2)
        second=j.pending_reviews(after=first["next_after"],limit=2)
        assert [x["check_id"] for x in first["items"]+second["items"]]==sorted(ids)
        assert second["next_after"] is None

def test_review_migration():
    from pathlib import Path
    sql=Path("migrations/0007_agent_head_offline.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS receipt_check_review" in sql and "0007_receipt_review" in sql

@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_DSN"),reason="TEST_POSTGRES_DSN not configured")
def test_pg_concurrent_manual_review():
    from agent.postgres import PostgresStore
    s=PostgresStore(os.environ["TEST_POSTGRES_DSN"])
    p=Principal("review-"+uuid.uuid4().hex,OPERATOR,frozenset({1}))
    with server(403,{}) as (url,seen):
        j=svc(s,url,p);cid=j.register(KEY)["check_id"];j.once()
        with ThreadPoolExecutor(4) as ex: results=list(ex.map(lambda _:manual(j,cid,1),range(4)))
        assert sum(not x["replayed"] for x in results)==1
        assert len(seen)==2 and j.view(cid)["manual_attempts"]==1
        assert len(j.view(cid)["review_history"])==1
