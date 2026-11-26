"""Operator review preflight and request-specific recovery evidence."""
import json
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.contracts import Conflict, Forbidden, Principal
from agent.receipt_check import OPERATOR
from agent.store import Store
from test_receipt_check import P, TOKEN, due, svc
from test_receipt_review import exhaust, manual
from test_receipts import DATA, KEY, PATH, envelope

MISSING={"success":False,"message":"OPERATION_RECEIPT_NOT_FOUND","data":None}


@contextmanager
def changing_receipt_server():
    reply={"status":404,"body":MISSING}
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.command,self.path,self.headers.get("Authorization")))
            raw=json.dumps(reply["body"]).encode()
            self.send_response(reply["status"])
            self.send_header("Content-Type","application/json")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_POST(self):
            seen.append((self.command,self.path,None))
            self.send_error(405)
        def log_message(self,*args):
            pass
    http=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{http.server_port}",reply,seen
    finally:
        http.shutdown()
        http.server_close()
        thread.join()


def test_preview_lifecycle_and_manual_budget(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    with changing_receipt_server() as (url,reply,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"]
        preview=j.review_preview(cid)
        assert preview["review_readiness"]=="AUTOMATIC_RETRY_PENDING"
        assert not preview["manual_retry_allowed"] and not seen
        claim=j._claim()
        assert j.review_preview(cid)["review_readiness"]=="LEASE_ACTIVE"
        j._finish(cid,claim[1],j.reader.query(P,KEY))
        for _ in range(2):
            due(s,cid);j.once()
        preview=j.review_preview(cid)
        assert preview["manual_review"] and preview["manual_retry_allowed"]
        assert preview["review_readiness"]=="MANUAL_RETRY_AVAILABLE"
        for i in range(2):
            out=manual(j,cid,3+i,f"review-{i}")
            assert out["review_attempt_status"]=="OBSERVED"
            assert out["review_code"]=="RECEIPT_NOT_OBSERVED"
            assert out["review_evidence_hash"] is None
        preview=j.review_preview(cid)
        assert preview["review_readiness"]=="MANUAL_BUDGET_EXHAUSTED"
        assert preview["manual_review"] and not preview["manual_retry_allowed"]
        item=j.pending_reviews()["items"][0]
        assert item["review_readiness"]==preview["review_readiness"]
        assert item["manual_attempts_remaining"]==0 and not item["manual_retry_allowed"]
        assert len(seen)==5 and j.once() is None


def test_late_receipt_after_budget_restart_and_replay(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    with changing_receipt_server() as (url,reply,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"];exhaust(s,j,cid)
        before=j.view(cid)
        reply.update(status=200,body=envelope())
        restarted=svc(Store(s.path),url)
        preview=restarted.review_preview(cid)
        assert preview["manual_retry_allowed"] and len(seen)==3
        assert restarted.view(cid)==before
        out=manual(restarted,cid,preview["fence"])
        assert out["state"]=="RECEIPT_MATCHED" and out["receipt"]==DATA
        assert out["review_readiness"]=="RECEIPT_ALREADY_MATCHED"
        assert out["review_attempt_status"]=="OBSERVED"
        assert out["review_code"]=="MATCHING_RECEIPT"
        assert out["review_evidence_hash"]==out["evidence_hash"]
        assert not out["manual_retry_allowed"] and not out["write_allowed"]
        assert out["dispatch_state"]=="NOT_TRACKED"
        replay=manual(restarted,cid,preview["fence"])
        assert replay["replayed"] and replay["review_attempt_status"]=="OBSERVED"
        assert replay["review_evidence_hash"]==out["evidence_hash"]
        assert seen==[("GET",PATH,"Bearer "+TOKEN)]*4


@pytest.mark.parametrize("field,value",[
    ("operationId","00000000-0000-0000-0000-000000000002"),
    ("warehouseId",2),("taskId",999),("requestFingerprint","b"*64),
])
def test_manual_read_requires_exact_late_receipt(tmp_path,field,value):
    s=Store(tmp_path/"check.sqlite")
    with changing_receipt_server() as (url,reply,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"];exhaust(s,j,cid)
        changed=dict(DATA);changed[field]=value
        assert changed!=DATA
        reply.update(status=200,body=envelope(changed))
        out=manual(j,cid,3)
        assert out["state"]=="UNKNOWN" and out["review_code"]=="RECEIPT_IDENTITY_MISMATCH"
        assert out["receipt"] is None and out["review_evidence_hash"] is None
        assert out["manual_retry_allowed"] and j.once() is None
        reply["body"]=envelope()
        out=manual(j,cid,4,"correct-receipt")
        assert out["state"]=="RECEIPT_MATCHED" and out["attempts"]==5
        assert len(j.view(cid)["review_history"])==2
        assert len(seen)==5 and all(x[0]=="GET" for x in seen)


def test_preview_is_a_snapshot_not_a_reservation(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    with changing_receipt_server() as (url,reply,seen):
        reply.update(status=403,body={})
        j=svc(s,url);cid=j.register(KEY)["check_id"];j.once()
        old=j.review_preview(cid)
        reply.update(status=404,body=MISSING)
        manual(j,cid,old["fence"],"other-review")
        with pytest.raises(Conflict,match="REVIEW_FENCE_CHANGED"):
            manual(j,cid,old["fence"],"stale-preview")
        assert len(seen)==2 and j.view(cid)["manual_attempts"]==1
        assert j.review_preview(cid)["fence"]==old["fence"]+1


def test_preview_and_replay_of_crashed_manual_read(tmp_path,monkeypatch):
    s=Store(tmp_path/"check.sqlite")
    with changing_receipt_server() as (url,reply,seen):
        reply.update(status=403,body={})
        j=svc(s,url);cid=j.register(KEY)["check_id"];j.once()
        def crash(*args):
            raise RuntimeError("isolated crash")
        monkeypatch.setattr(j.reader,"query",crash)
        with pytest.raises(RuntimeError,match="isolated crash"):
            manual(j,cid,1)
        assert j.review_preview(cid)["review_readiness"]=="LEASE_ACTIVE"
        replay=manual(j,cid,1)
        assert replay["replayed"] and replay["review_attempt_status"]=="CLAIMED"
        original=j.view(cid)
        for _ in range(5):
            j.review_preview(cid)
        assert j.view(cid)==original and len(seen)==1
        due(s,cid)
        assert j.review_preview(cid)["manual_retry_allowed"]
        replay=manual(j,cid,1)
        assert replay["review_attempt_status"]=="LEASE_EXPIRED" and replay["review_code"] is None
        reply.update(status=200,body=envelope())
        restarted=svc(Store(s.path),url)
        assert manual(restarted,cid,2,"new-review")["state"]=="RECEIPT_MATCHED"
        old=manual(restarted,cid,1)
        assert old["state"]=="RECEIPT_MATCHED" and old["review_attempt_status"]=="ABANDONED"
        assert old["review_code"]=="LEASE_EXPIRED" and old["review_evidence_hash"] is None
        assert len(seen)==2 and restarted.view(cid)["manual_attempts"]==2


def test_preview_scope_binding_and_integrity(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    with changing_receipt_server() as (url,reply,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"]
        for p in (Principal("other",OPERATOR,frozenset({1})),Principal(P.tenant,OPERATOR,frozenset({2}))):
            with pytest.raises(Forbidden):
                svc(s,url,p).review_preview(cid)
        with pytest.raises(Conflict,match="BINDING_CHANGED"):
            svc(s,"https://example.invalid").review_preview(cid)
        with pytest.raises(Forbidden):
            svc(s,url,realm="other").review_preview(cid)
        with s.tx() as c:
            c.execute("UPDATE receipt_check SET reference_hash='tampered' WHERE id=?",(cid,))
        with pytest.raises(Conflict,match="INTEGRITY_ERROR"):
            j.review_preview(cid)
        assert not seen


def test_preview_cli_performs_no_http(tmp_path,monkeypatch,capsys):
    import agent.receipt_check as module
    s=Store(tmp_path/"check.sqlite")
    with changing_receipt_server() as (url,reply,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"]
        monkeypatch.setattr(module,"configured_service",lambda:j)
        monkeypatch.setattr("sys.argv",["receipt_check","review-preview",cid])
        module.main()
        out=json.loads(capsys.readouterr().out)
        assert out["check_id"]==cid and out["review_readiness"]=="AUTOMATIC_RETRY_PENDING"
        assert not out["manual_retry_allowed"] and not seen


def test_exhausted_budget_cannot_resume_on_late_receipt(tmp_path):
    s=Store(tmp_path/"check.sqlite")
    with changing_receipt_server() as (url,reply,seen):
        j=svc(s,url);cid=j.register(KEY)["check_id"];exhaust(s,j,cid)
        manual(j,cid,3,"first");manual(j,cid,4,"second")
        reply.update(status=200,body=envelope())
        restarted=svc(Store(s.path),url)
        preview=restarted.review_preview(cid)
        assert preview["review_readiness"]=="MANUAL_BUDGET_EXHAUSTED"
        with pytest.raises(Conflict,match="BUDGET_EXHAUSTED"):
            manual(restarted,cid,5,"third")
        replay=manual(restarted,cid,3,"first")
        assert replay["replayed"] and replay["review_code"]=="RECEIPT_NOT_OBSERVED"
        assert replay["state"]=="UNKNOWN" and restarted.once() is None
        assert len(seen)==5 and restarted.register(KEY)["attempts"]==5


@pytest.mark.skipif(not os.getenv("TEST_POSTGRES_DSN"),reason="TEST_POSTGRES_DSN not configured")
def test_pg_preview_distinct_review_requests_race():
    from agent.postgres import PostgresStore
    s=PostgresStore(os.environ["TEST_POSTGRES_DSN"])
    p=Principal("preflight-"+uuid.uuid4().hex,OPERATOR,frozenset({1}))
    with changing_receipt_server() as (url,reply,seen):
        reply.update(status=403,body={})
        j=svc(s,url,p);cid=j.register(KEY)["check_id"];j.once()
        preview=j.review_preview(cid)
        reply.update(status=404,body=MISSING)
        def retry(request):
            try:
                return manual(j,cid,preview["fence"],request)
            except Conflict as exc:
                return str(exc)
        with ThreadPoolExecutor(2) as pool:
            results=list(pool.map(retry,["concurrent-a","concurrent-b"]))
        assert sum(isinstance(out,dict) for out in results)==1
        assert "REVIEW_FENCE_CHANGED" in next(out for out in results if isinstance(out,str))
        assert len(seen)==2 and j.view(cid)["manual_attempts"]==1
        reply.update(status=200,body=envelope())
        out=manual(j,cid,2,"late-receipt")
        assert out["state"]=="RECEIPT_MATCHED" and len(seen)==3
        assert j.review_preview(cid)["review_readiness"]=="RECEIPT_ALREADY_MATCHED"
