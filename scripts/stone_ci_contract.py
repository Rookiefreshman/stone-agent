"""Opt-in read-only contract check against Stone's disposable CI MySQL fixtures.

Run as python -m scripts.stone_ci_contract. Not an API, worker, or production smoke.
The Stone workflow supplies only identifiers from its actual Java integration tests.
"""
import argparse
import json
import os
import tempfile
import subprocess
import sys
from pathlib import Path

from agent.contracts import Principal, StockWorkArgs, StockWorkResult, DispatchHistory, Forbidden, DomainReadError
from agent.integrations.outbound import OutboundArgs, OutboundResult, analyze as outbound_analyze
from agent.integrations.wes import WesArgs, WesResult, analyze as wes_analyze
from agent.receipts import CancelCommand, ReceiptKey, ReceiptReader, ReconcileCheckpoint
from agent.tools import StoneHttpService

ORIGIN = "http://127.0.0.1:18081"

def require(condition, code):
    if not condition:
        raise RuntimeError(code)

def verify(receipt_task, operation_id, history_task, query_token, receipt_token):
    p = Principal("test", "contract-reader", frozenset({403, 97001, 98001, 99101, 99102}))
    service = StoneHttpService(ORIGIN, query_token)
    checks = []
    args = StockWorkArgs(warehouse_id=99101, task_id=receipt_task)
    detail = StockWorkResult.model_validate(service.get_detail(p, args))
    require(detail.status == "FOUND" and detail.source == "STONE_HTTP" and detail.task.state == "CANCELLED", "STOCK_WORK_STATE")
    require(detail.task.taskKey == "receipt-test" and detail.task.work.quantity == 6, "STOCK_WORK_IDENTITY")
    release = [x for x in detail.ledger if x.action == "MOVE_RELEASE"]
    require(len(release) == 1 and release[0].locationId == 1 and release[0].deltaReserved == -6 and release[0].deltaAvailable == 6 and release[0].deltaOnHand == 0, "RELEASE_LEDGER")
    checks.append("stock_work_cancelled_and_single_release")

    history = DispatchHistory.model_validate(service.get_events(p, StockWorkArgs(warehouse_id=403, task_id=history_task)))
    require(history.source == "STONE_HTTP" and history.task_state == "FAILED" and not history.truncated, "HISTORY_STATE")
    events = {e.eventKey: e for e in history.events}
    require(set(events) == {"timeout", "failure"} and events["timeout"].targetState == "UNKNOWN" and events["failure"].targetState == "FAILED" and events["failure"].reconciled and events["failure"].completedQty == 0, "HISTORY_EVENTS")
    checks.append("stock_work_history_and_reconciliation")

    outbound = OutboundResult.model_validate(service.get_outbound(p, OutboundArgs(warehouse_id=97001, order_id=97001)))
    require(outbound.source == "STONE_HTTP" and outbound.status == "FOUND" and len(outbound.snapshot.lines) == 3, "OUTBOUND_SNAPSHOT")
    lines = outbound.snapshot.lines
    require(lines[0].inventoryId == lines[1].inventoryId == 97001 and lines[0].availableQty == 10 and lines[0].locationState == "FROZEN" and lines[2].inventoryId is None, "OUTBOUND_JOIN")
    require(outbound_analyze(outbound)["assessment"] == "ALLOCATION_RECEIPT_EXISTS", "OUTBOUND_RECEIPT")
    checks.append("outbound_snapshot_exact_inventory_and_receipt")

    wes = WesResult.model_validate(service.get_wes_dispatch(p, WesArgs(warehouse_id=98001, device_code="a", requested_limit=8)))
    diagnosis = wes_analyze(wes)
    require(wes.source == "STONE_HTTP" and wes.status == "FOUND" and diagnosis["claim_upper_bound"] == 0 and diagnosis["warehouse_unknown"] == 2 and diagnosis["device_unknown"] == 1, "WES_CAPACITY")
    checks.append("wes_unknown_occupancy_and_capacity")

    # Independently reconstruct the command from the known Java test fixture,
    # not from the receipt's fingerprint. This exercises cross-language encoding.
    cmd = CancelCommand(operation_id=operation_id, warehouse_id=99101, task_id=receipt_task,
        tenant="test", actor="requester", task_key="receipt-test", work_type="TRANSFER",
        sku_id=11, lot_no="B1", source_id=1, destination_id=2, quantity=6,
        reason="test-only", approval_hash="a"*64)
    key = ReceiptKey(operation_id=operation_id, warehouse_id=99101, task_id=receipt_task,
        request_fingerprint=cmd.fingerprint())
    reader = ReceiptReader(ORIGIN, receipt_token)
    cp = ReconcileCheckpoint(key=key).apply(reader.query(p, key))
    require(cp.state == "COMMITTED" and cp.evidence_hash is not None, "RECEIPT_FINGERPRINT")
    require(reader.query(p, key.model_copy(update={"request_fingerprint": "0"*64})).code == "RECEIPT_IDENTITY_MISMATCH", "RECEIPT_WRONG_FINGERPRINT")
    absent = key.model_copy(update={"operation_id": "00000000-0000-0000-0000-000000000000"})
    missing = reader.query(p, absent)
    require(missing.status == "UNKNOWN" and missing.code == "RECEIPT_NOT_OBSERVED", "RECEIPT_MISSING")
    require(cp.apply(missing) == cp, "COMMITTED_STICKY")
    checks.append("real_receipt_fingerprint_missing_and_sticky")

    # Persist an independently supplied reference; read actual Stone HTTP receipt,
    # lose the first result before persisting it, then recover in a fresh process.
    from agent.store import Store
    from agent.receipt_check import ReceiptCheckService, OPERATOR
    with tempfile.TemporaryDirectory() as folder:
        db=str(Path(folder)/"receipt-check.sqlite")
        store=Store(db)
        operator=Principal("test",OPERATOR,frozenset({99101}))
        journal=ReceiptCheckService(store,operator,ORIGIN,receipt_token,"stone-disposable-ci",enabled=True)
        cid=journal.register(key)["check_id"]
        def crash():
            raise RuntimeError("CI_INJECTED_AFTER_GET")
        try:
            journal.once(after_read=crash)
        except RuntimeError as exc:
            require(str(exc)=="CI_INJECTED_AFTER_GET","RECEIPT_UNEXPECTED_FAILURE")
        else:
            raise RuntimeError("RECEIPT_CRASH_NOT_INJECTED")
        require(journal.view(cid)["state"]=="UNKNOWN","RECEIPT_PREMATURE_MATCH")
        with store.tx() as c:
            c.execute("UPDATE receipt_check SET lease_until=0 WHERE id=?",(cid,))
        env=dict(os.environ,STONE_RECEIPT_CHECK_ENABLED="1",STONE_RECEIPT_TENANT="test",
            STONE_RECEIPT_WAREHOUSES="99101",STONE_RECEIPT_ORIGIN=ORIGIN,
            STONE_RECEIPT_TOKEN=receipt_token,STONE_RECEIPT_REALM="stone-disposable-ci",POC_DB=db)
        env.pop("AGENT_DATABASE_URL",None)
        result=subprocess.run([sys.executable,"-m","agent.receipt_check","once"],env=env,check=True,capture_output=True,text=True,timeout=30)
        recovered=json.loads(result.stdout)
        require(recovered["state"]=="RECEIPT_MATCHED" and recovered["attempts"]==2 and recovered["dispatch_state"]=="NOT_TRACKED" and not recovered["write_allowed"],"DURABLE_RECEIPT_RECOVERY")
        history=journal.view(cid)["attempt_history"]
        require([x["state"] for x in history]==["ABANDONED","OBSERVED"],"RECEIPT_ATTEMPT_AUDIT")
    checks.append("durable_receipt_recovery_after_read_result_loss")

    # Actual server denies the first read. A local operator fixes the read
    # credential and explicitly authorizes one more GET, not a cancellation.
    with tempfile.TemporaryDirectory() as folder:
        store=Store(str(Path(folder)/"manual-review.sqlite"))
        operator=Principal("test",OPERATOR,frozenset({99101}))
        denied=ReceiptCheckService(store,operator,ORIGIN,"invalid-ci-receipt-token-123456","stone-disposable-ci",enabled=True)
        cid=denied.register(key)["check_id"]
        first=denied.once()
        require(first["manual_review"] and first["last_code"]=="RECEIPT_HTTP_401","MANUAL_REVIEW_HOLD")
        journal=ReceiptCheckService(store,operator,ORIGIN,receipt_token,"stone-disposable-ci",enabled=True)
        require(journal.pending_reviews()["items"][0]["check_id"]==cid,"MANUAL_REVIEW_DISCOVERY")
        retry=journal.manual_retry(cid,request_id="ci-credential-fixed",expected_fence=first["fence"],reason="Restore test read credential")
        require(retry["state"]=="RECEIPT_MATCHED" and retry["manual_attempts"]==1 and not retry["write_allowed"],"MANUAL_REVIEW_RECOVERY")
        require(journal.manual_retry(cid,request_id="ci-credential-fixed",expected_fence=first["fence"],reason="Restore test read credential")["replayed"],"MANUAL_REVIEW_REPLAY")
        require(journal.pending_reviews()["items"]==[] and len(journal.view(cid)["review_history"])==1,"MANUAL_REVIEW_AUDIT")
    checks.append("manual_receipt_review_after_real_permission_denial")

    # Local data-domain denial, and independent server-side receipt reader scope.
    try:
        reader.query(Principal("test", "denied", frozenset()), key)
    except Forbidden:
        pass
    else:
        raise RuntimeError("LOCAL_SCOPE_NOT_ENFORCED")
    wrong_warehouse = key.model_copy(update={"warehouse_id": 99102})
    require(reader.query(p, wrong_warehouse).code == "RECEIPT_HTTP_403", "SERVER_SCOPE_NOT_ENFORCED")
    require(ReceiptReader(ORIGIN, "invalid-ci-reader-token").query(p, key).code == "RECEIPT_HTTP_401", "SERVER_TOKEN_NOT_ENFORCED")
    try:
        StoneHttpService(ORIGIN, "invalid-ci-query-token").get_outbound(p, OutboundArgs(warehouse_id=97001, order_id=97001))
    except DomainReadError as exc:
        require(str(exc) == "OUTBOUND_HTTP_401", "OUTBOUND_AUTH_WRONG_ERROR")
    else:
        raise RuntimeError("OUTBOUND_AUTH_NOT_ENFORCED")
    checks.append("local_and_server_read_authorization")
    return {"environment": "DISPOSABLE_STONE_CI", "checks": checks, "write_allowed": False, "business_posts": 0}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-task", type=int, required=True)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--history-task", type=int, required=True)
    args = parser.parse_args()
    require(os.getenv("STONE_CI_CONTRACT") == "1", "CI_ONLY_EXPLICIT_OPT_IN_REQUIRED")
    query_token = os.environ["STONE_CI_QUERY_TOKEN"]
    receipt_token = os.environ["STONE_CI_RECEIPT_TOKEN"]
    require(len(query_token) >= 24 and len(receipt_token) >= 24 and query_token != receipt_token, "SEPARATE_CI_READ_TOKENS_REQUIRED")
    print(json.dumps(verify(args.receipt_task, args.operation_id, args.history_task, query_token, receipt_token), ensure_ascii=False))

if __name__ == "__main__":
    main()
