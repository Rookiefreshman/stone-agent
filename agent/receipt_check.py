"""Durable read-only receipt investigation for a configured local operator.

The submitted reference is an independently supplied expectation, NOT an authorized
dispatch record. No business POST, model tool, API route, or rehearsal conversion.
"""
import argparse
import json
import os
import re
import urllib.parse
import uuid

from .contracts import Principal, Conflict, Forbidden, LostClaim, canonical, digest, strict_json
from .receipts import ReceiptKey, ReceiptReader, ReconcileCheckpoint
from .tools import authorize

CHECK_DDL = (
    """CREATE TABLE IF NOT EXISTS receipt_check (
        id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
        realm TEXT NOT NULL, binding_hash TEXT NOT NULL,
        warehouse_id BIGINT NOT NULL, operation_id TEXT NOT NULL,
        reference TEXT NOT NULL, reference_hash TEXT NOT NULL, checkpoint TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('UNKNOWN','RECEIPT_MATCHED')),
        attempts BIGINT NOT NULL DEFAULT 0, fence BIGINT NOT NULL DEFAULT 0,
        lease_until DOUBLE PRECISION NOT NULL DEFAULT 0, next_at DOUBLE PRECISION NOT NULL,
        held BIGINT NOT NULL DEFAULT 0, last_code TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
        UNIQUE(tenant,actor,realm,warehouse_id,operation_id))""",
    "CREATE INDEX IF NOT EXISTS receipt_check_due_idx ON receipt_check(tenant,actor,realm,binding_hash,state,next_at,id)",
    """CREATE TABLE IF NOT EXISTS receipt_check_attempt (
        check_id TEXT NOT NULL REFERENCES receipt_check(id), fence BIGINT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('CLAIMED','OBSERVED','ABANDONED')),
        claimed_at DOUBLE PRECISION NOT NULL, finished_at DOUBLE PRECISION,
        code TEXT, evidence_hash TEXT, PRIMARY KEY(check_id,fence))""",
)

MAX_ATTEMPTS = 3
MAX_MANUAL_ATTEMPTS = 2
CHECK_DDL += (
    """CREATE TABLE IF NOT EXISTS receipt_check_review (
        check_id TEXT NOT NULL REFERENCES receipt_check(id),
        request_id TEXT NOT NULL, expected_fence BIGINT NOT NULL, fence BIGINT NOT NULL,
        actor TEXT NOT NULL, reason TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY(check_id,request_id), UNIQUE(check_id,fence),
        FOREIGN KEY(check_id,fence) REFERENCES receipt_check_attempt(check_id,fence))""",
)
OPERATOR = "local-receipt-operator"


class ReceiptCheckService:
    """Local development/maintenance trust boundary; not enterprise IAM."""
    def __init__(self, store, principal, origin, token, realm, *, enabled=False, timeout=8):
        if not enabled or principal.actor != OPERATOR or not principal.tenant.strip() or not principal.warehouses:
            raise Forbidden("RECEIPT_CHECK_OPERATOR_REQUIRED")
        if not isinstance(realm, str) or not realm or len(realm)>128 or any(c.isspace() or not c.isprintable() for c in realm):
            raise ValueError("stable service realm required")
        if not isinstance(token,str) or len(token)<24 or any(c.isspace() or not c.isascii() or not c.isprintable() for c in token):
            raise ValueError("dedicated receipt read credential required")
        u=urllib.parse.urlsplit(origin)
        if u.scheme!="https" and not (u.scheme=="http" and u.hostname in ("127.0.0.1","::1")):
            raise ValueError("HTTPS required except literal loopback")
        if not 0<timeout<=8:
            raise ValueError("invalid socket timeout")
        self.store=store
        self.principal=principal
        self.reader=ReceiptReader(origin,token,timeout)
        self.realm=realm
        self.binding=digest(["STONE_RECEIPT_CHECK_V1",realm,self.reader.origin])

    def _load(self,c,cid):
        p=self.principal
        row=self.store.locked(c,"SELECT * FROM receipt_check WHERE id=? AND tenant=? AND actor=? AND realm=?",(cid,p.tenant,p.actor,self.realm))
        if not row:
            raise Forbidden("NOT_ACCESSIBLE")
        if row["binding_hash"]!=self.binding:
            raise Conflict("RECEIPT_SERVICE_BINDING_CHANGED")
        reference=strict_json(row["reference"])
        key=ReceiptKey.model_validate(reference)
        authorize(p,key)
        cp=ReconcileCheckpoint.model_validate(strict_json(row["checkpoint"]))
        if digest(reference)!=row["reference_hash"] or cp.key!=key or (key.warehouse_id,key.operation_id)!=(row["warehouse_id"],row["operation_id"]) or row["state"]!=("RECEIPT_MATCHED" if cp.state=="COMMITTED" else "UNKNOWN"):
            raise Conflict("RECEIPT_CHECK_INTEGRITY_ERROR")
        return row,key,cp

    def _view(self,c,row,cp):
        now=self.store.now(c)
        manual_count=c.execute("SELECT COUNT(*) FROM receipt_check_review WHERE check_id=?",(row["id"],)).fetchone()[0]
        return dict(check_id=row["id"],state=row["state"],reference_source="OPERATOR_SUPPLIED",
            expected_source="STONE_HTTP",realm=row["realm"],reference=cp.key.model_dump(),
            dispatch_state="NOT_TRACKED",write_allowed=False,
            attempts=row["attempts"],fence=row["fence"],
            manual_review=row["state"]=="UNKNOWN" and row["lease_until"]<=now and (bool(row["held"]) or row["attempts"]>=MAX_ATTEMPTS),
            manual_attempts=manual_count, manual_attempts_remaining=MAX_MANUAL_ATTEMPTS-manual_count,
            last_code=row["last_code"],receipt=cp.receipt.model_dump() if cp.receipt else None,
            evidence_hash=cp.evidence_hash,updated_at=row["updated_at"])

    def register(self,key):
        key=ReceiptKey.model_validate(key.model_dump())
        p=self.principal
        authorize(p,key)
        reference=key.model_dump()
        fp=digest(reference)
        with self.store.tx() as c:
            now=self.store.now(c)
            c.execute("INSERT INTO receipt_check(id,tenant,actor,realm,binding_hash,warehouse_id,operation_id,reference,reference_hash,checkpoint,state,next_at,last_code,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,'UNKNOWN',?,'PENDING_QUERY',?,?) ON CONFLICT(tenant,actor,realm,warehouse_id,operation_id) DO NOTHING",
                (str(uuid.uuid4()),p.tenant,p.actor,self.realm,self.binding,key.warehouse_id,key.operation_id,canonical(reference),fp,canonical(ReconcileCheckpoint(key=key).model_dump()),now,now,now))
            found=c.execute("SELECT id FROM receipt_check WHERE tenant=? AND actor=? AND realm=? AND warehouse_id=? AND operation_id=?",(p.tenant,p.actor,self.realm,key.warehouse_id,key.operation_id)).fetchone()
            row,old,cp=self._load(c,found["id"])
            if old!=key:
                raise Conflict("RECEIPT_REFERENCE_CONFLICT")
            return self._view(c,row,cp)

    def view(self,cid):
        with self.store.tx() as c:
            row,_,cp=self._load(c,cid)
            out=self._view(c,row,cp)
            out["attempt_history"]=[dict(r) for r in c.execute("SELECT fence,state,claimed_at,finished_at,code,evidence_hash FROM receipt_check_attempt WHERE check_id=? ORDER BY fence",(cid,))]
            out["review_history"]=[dict(r) for r in c.execute("SELECT request_id,expected_fence,fence,actor,reason,created_at FROM receipt_check_review WHERE check_id=? ORDER BY fence",(cid,))]
            return out

    def pending_reviews(self, *, after="", limit=50):
        """Bounded keyset scan; only the configured operator and service realm."""
        if type(limit) is not int or not 1<=limit<=100 or not isinstance(after,str) or len(after)>64:
            raise ValueError("invalid review page")
        p=self.principal
        warehouses=sorted(p.warehouses)
        placeholders=",".join("?" for _ in warehouses)
        with self.store.tx() as c:
            now=self.store.now(c)
            rows=c.execute("SELECT id FROM receipt_check WHERE tenant=? AND actor=? AND realm=? AND binding_hash=? AND warehouse_id IN ("+placeholders+") AND id>? AND state='UNKNOWN' AND lease_until<=? AND (held=1 OR attempts>=?) ORDER BY id LIMIT ?",
                (p.tenant,p.actor,self.realm,self.binding,*warehouses,after,now,MAX_ATTEMPTS,limit+1)).fetchall()
            items=[]
            for candidate in rows[:limit]:
                row,_,cp=self._load(c,candidate["id"])
                out=self._view(c,row,cp)
                if out["manual_review"]:
                    items.append({k:out[k] for k in ("check_id","state","reference","fence","attempts","manual_attempts_remaining","last_code","updated_at")})
            return {"items":items,"next_after":rows[limit-1]["id"] if len(rows)>limit else None}

    def manual_retry(self,cid,*,request_id,expected_fence,reason):
        """Consume exactly one explicitly requested GET attempt, never re-enable auto retry.
        Replay reports current status, not another GET. The local operator is trusted
        by deployment; this is neither a business approval nor an enterprise identity.
        """
        if not isinstance(request_id,str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}",request_id):
            raise ValueError("invalid review request id")
        if type(expected_fence) is not int or not 0<=expected_fence<=9223372036854775807:
            raise ValueError("invalid expected fence")
        if not isinstance(reason,str) or not reason.strip() or len(reason)>240 or not reason.isprintable():
            raise ValueError("invalid review reason")
        with self.store.tx() as c:
            row,key,cp=self._load(c,cid)
            previous=c.execute("SELECT * FROM receipt_check_review WHERE check_id=? AND request_id=?",(cid,request_id)).fetchone()
            if previous:
                if previous["expected_fence"]!=expected_fence or previous["reason"]!=reason:
                    raise Conflict("REVIEW_REQUEST_CONFLICT")
                return dict(self._view(c,row,cp),review_request_id=request_id,review_fence=previous["fence"],replayed=True)
            now=self.store.now(c)
            if row["fence"]!=expected_fence:
                raise Conflict("REVIEW_FENCE_CHANGED")
            out=self._view(c,row,cp)
            if not out["manual_review"]:
                raise Conflict("REVIEW_NOT_AVAILABLE")
            if out["manual_attempts_remaining"]<=0:
                raise Conflict("MANUAL_REVIEW_BUDGET_EXHAUSTED")
            fence=row["fence"]+1
            c.execute("UPDATE receipt_check_attempt SET state='ABANDONED',finished_at=?,code='LEASE_EXPIRED' WHERE check_id=? AND state='CLAIMED'",(now,cid))
            c.execute("UPDATE receipt_check SET attempts=attempts+1,fence=?,held=1,lease_until=?,updated_at=?,last_code='MANUAL_READ_CLAIMED' WHERE id=?",(fence,now+30,now,cid))
            c.execute("INSERT INTO receipt_check_attempt(check_id,fence,state,claimed_at) VALUES(?,?,'CLAIMED',?)",(cid,fence,now))
            c.execute("INSERT INTO receipt_check_review(check_id,request_id,expected_fence,fence,actor,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                (cid,request_id,expected_fence,fence,self.principal.actor,reason,now))
        obs=self.reader.query(self.principal,key)
        return dict(self._finish(cid,fence,obs),review_request_id=request_id,review_fence=fence,replayed=False)

    def _claim(self,lease=30):
        if not 1<=lease<=120:
            raise ValueError("invalid lease")
        p=self.principal
        warehouses=sorted(p.warehouses)
        placeholders=",".join("?" for _ in warehouses)
        with self.store.tx() as c:
            now=self.store.now(c)
            row=self.store.locked(c,"SELECT id FROM receipt_check WHERE tenant=? AND actor=? AND realm=? AND binding_hash=? AND warehouse_id IN ("+placeholders+") AND state='UNKNOWN' AND held=0 AND attempts<? AND next_at<=? AND lease_until<=? ORDER BY next_at,id LIMIT 1",
                (p.tenant,p.actor,self.realm,self.binding,*warehouses,MAX_ATTEMPTS,now,now),skip=True)
            if not row:
                return None
            cid=row["id"]
            row,key,_=self._load(c,cid)
            fence=row["fence"]+1
            c.execute("UPDATE receipt_check_attempt SET state='ABANDONED',finished_at=?,code='LEASE_EXPIRED' WHERE check_id=? AND state='CLAIMED'",(now,cid))
            c.execute("UPDATE receipt_check SET attempts=attempts+1,fence=?,lease_until=?,updated_at=?,last_code='READ_CLAIMED' WHERE id=?",(fence,now+lease,now,cid))
            c.execute("INSERT INTO receipt_check_attempt(check_id,fence,state,claimed_at) VALUES(?,?,'CLAIMED',?)",(cid,fence,now))
            return cid,fence,key

    def _finish(self,cid,fence,obs):
        with self.store.tx() as c:
            row,_,cp=self._load(c,cid)
            now=self.store.now(c)
            if row["state"]!="UNKNOWN" or row["fence"]!=fence or row["lease_until"]<=now:
                raise LostClaim("RECEIPT_CHECK_CLAIM_LOST")
            cp=cp.apply(obs)
            state="RECEIPT_MATCHED" if cp.state=="COMMITTED" else "UNKNOWN"
            retryable=obs.code in ("RECEIPT_NOT_OBSERVED","RECEIPT_UNVERIFIED","RECEIPT_HTTP_429") or obs.code.startswith("RECEIPT_HTTP_5")
            manual=c.execute("SELECT 1 FROM receipt_check_review WHERE check_id=? AND fence=?",(cid,fence)).fetchone() is not None
            held=int(state=="UNKNOWN" and (manual or not retryable))
            n=c.execute("UPDATE receipt_check_attempt SET state='OBSERVED',finished_at=?,code=?,evidence_hash=? WHERE check_id=? AND fence=? AND state='CLAIMED'",(now,obs.code,cp.evidence_hash,cid,fence)).rowcount
            if n!=1:
                raise Conflict("RECEIPT_ATTEMPT_MISSING")
            c.execute("UPDATE receipt_check SET state=?,checkpoint=?,held=?,last_code=?,lease_until=0,next_at=?,updated_at=? WHERE id=?",
                (state,canonical(cp.model_dump()),held,obs.code,now+2**row["attempts"],now,cid))
            row,_,cp=self._load(c,cid)
            return self._view(c,row,cp)

    def once(self, *, after_read=None):
        claim=self._claim()
        if claim is None:
            return None
        cid,fence,key=claim
        # No database transaction is held across HTTP. A lost claim cannot update state.
        obs=self.reader.query(self.principal,key)
        if after_read is not None:
            after_read()
        return self._finish(cid,fence,obs)


def configured_service():
    from .store import Store
    if os.getenv("STONE_RECEIPT_CHECK_ENABLED")!="1":
        raise Forbidden("RECEIPT_CHECK_DISABLED")
    p=Principal(os.environ["STONE_RECEIPT_TENANT"],OPERATOR,
        frozenset(int(x) for x in os.environ["STONE_RECEIPT_WAREHOUSES"].split(",")))
    if any(w<=0 or w>9223372036854775807 for w in p.warehouses):
        raise ValueError("invalid warehouse scope")
    dsn=os.getenv("AGENT_DATABASE_URL")
    if dsn:
        from .postgres import PostgresStore
        store=PostgresStore(dsn)
    else:
        store=Store(os.environ["POC_DB"])
    return ReceiptCheckService(store,p,os.environ["STONE_RECEIPT_ORIGIN"],
        os.environ["STONE_RECEIPT_TOKEN"],os.environ["STONE_RECEIPT_REALM"],enabled=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest="action",required=True)
    add=sub.add_parser("register")
    add.add_argument("--warehouse",type=int,required=True)
    add.add_argument("--task",type=int,required=True)
    add.add_argument("--operation-id",required=True)
    add.add_argument("--fingerprint",required=True)
    view=sub.add_parser("status")
    view.add_argument("check_id")
    sub.add_parser("once")
    pending=sub.add_parser("pending-reviews")
    pending.add_argument("--after",default="")
    pending.add_argument("--limit",type=int,default=50)
    retry=sub.add_parser("review-retry")
    retry.add_argument("check_id")
    retry.add_argument("--request-id",required=True)
    retry.add_argument("--expected-fence",type=int,required=True)
    retry.add_argument("--reason",required=True)
    args=parser.parse_args()
    service=configured_service()
    if args.action=="register":
        out=service.register(ReceiptKey(warehouse_id=args.warehouse,task_id=args.task,
            operation_id=args.operation_id,request_fingerprint=args.fingerprint))
    elif args.action=="status":
        out=service.view(args.check_id)
    elif args.action=="pending-reviews":
        out=service.pending_reviews(after=args.after,limit=args.limit)
    elif args.action=="review-retry":
        out=service.manual_retry(args.check_id,request_id=args.request_id,expected_fence=args.expected_fence,reason=args.reason)
    else:
        out=service.once()
    print(json.dumps(out,ensure_ascii=False))


if __name__=="__main__":
    main()
