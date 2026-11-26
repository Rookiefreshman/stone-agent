"""Isolated, durable cancellation fault rehearsal. Never sends to Stone.
Only synthetic approvals and locally generated synthetic receipts are accepted.
No HTTP route, model tool, or production dispatcher uses this module.
"""
from datetime import datetime, timezone
from .contracts import StockWorkArgs, Conflict, Forbidden, LostClaim, canonical, digest, strict_json
from .receipts import CancelCommand, ReceiptKey, Receipt, ReceiptObservation, ReconcileCheckpoint, unknown
from .tools import FixtureStoneService, authorize

REHEARSAL_DDL = (
    """CREATE TABLE IF NOT EXISTS rehearsal_execution (
        operation_id TEXT PRIMARY KEY REFERENCES operation_draft(id),
        tenant TEXT NOT NULL, warehouse_id BIGINT NOT NULL,
        command TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
        checkpoint TEXT NOT NULL, fixture_receipt TEXT,
        state TEXT NOT NULL CHECK(state IN ('REHEARSAL_UNKNOWN','REHEARSAL_COMMITTED')),
        fence BIGINT NOT NULL DEFAULT 0, attempts BIGINT NOT NULL DEFAULT 0,
        lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
        next_at DOUBLE PRECISION NOT NULL, last_code TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS rehearsal_due_idx ON rehearsal_execution(tenant,state,next_at,operation_id)",
)

MAX_ATTEMPTS = 3

class RehearsalJournal:
    def __init__(self, operations, *, enabled=False):
        self.op=operations; self.store=operations.store
        snapshot=operations.registry.snapshot({'get_stock_work_detail'})
        spec=operations.registry.resolve('get_stock_work_detail',snapshot)
        if not enabled or not operations.rehearsal_enabled or type(getattr(spec.handler,'__self__',None)) is not FixtureStoneService:
            raise Forbidden('FIXTURE_REHEARSAL_ONLY')

    def _load(self,c,p,oid):
        row=self.store.locked(c,'SELECT * FROM rehearsal_execution WHERE operation_id=? AND tenant=?',(oid,p.tenant))
        if not row: raise Forbidden('NOT_ACCESSIBLE')
        cmd=CancelCommand.model_validate(strict_json(row['command'])); authorize(p,cmd)
        cp=ReconcileCheckpoint.model_validate(strict_json(row['checkpoint']))
        if (cmd.operation_id,cmd.tenant,cmd.warehouse_id,cmd.fingerprint())!=(oid,row['tenant'],row['warehouse_id'],row['request_fingerprint']) or cp.key!=self._key(cmd) or row['state']!='REHEARSAL_'+cp.state:
            raise Conflict('REHEARSAL_INTEGRITY_ERROR')
        return row,cmd,cp

    @staticmethod
    def _key(cmd):
        return ReceiptKey(operation_id=cmd.operation_id,warehouse_id=cmd.warehouse_id,task_id=cmd.task_id,request_fingerprint=cmd.fingerprint())

    def _view(self,c,row,cp):
        return dict(operation_id=row['operation_id'],state=row['state'],source='SYNTHETIC_FIXTURE',
            write_allowed=False,dispatch_state='NEVER_DISPATCHED',attempts=row['attempts'],fence=row['fence'],
            manual_review=row['state']=='REHEARSAL_UNKNOWN' and row['attempts']>=MAX_ATTEMPTS and row['lease_until']<=self.store.now(c),
            last_code=row['last_code'],simulated_receipt=cp.receipt.model_dump() if cp.receipt else None)

    def begin(self,p,oid,payload_hash):
        """Atomically consume one rehearsal approval and persist a simulated intent."""
        with self.store.tx() as c:
            draft,payload=self.op._load(c,p,oid)
            if payload_hash!=draft['payload_hash']: raise Conflict('PAYLOAD_HASH_MISMATCH')
            if c.execute('SELECT operation_id FROM rehearsal_execution WHERE operation_id=?',(oid,)).fetchone():
                row,_,cp=self._load(c,p,oid); return self._view(c,row,cp)
            if draft['state']!='REHEARSAL_APPROVED' or draft['review_decision']!='APPROVE_REHEARSAL' or not draft['reviewer'] or draft['reviewer']==p.actor or self.store.now(c)>=draft['expires_at']:
                raise Conflict('REHEARSAL_APPROVAL_REQUIRED')
            args=StockWorkArgs.model_validate(payload['entity'])
            result,h,_,binding=self.op._evidence(c,p,draft['run_id'],args)
            if result.source!='SYNTHETIC_FIXTURE' or payload['source']!='SYNTHETIC_FIXTURE' or result.status!='FOUND' or result.task.state!='READY' or h!=payload['evidence_hash'] or binding!=payload['binding'] or result.task.model_dump()!=payload['task']:
                raise Conflict('REHEARSAL_EVIDENCE_CHANGED')
            task=result.task; w=task.work
            cmd=CancelCommand(**args.model_dump(),operation_id=oid,tenant=p.tenant,actor=p.actor,task_key=task.taskKey,work_type=w.type,sku_id=w.skuId,lot_no=w.lotNo,source_id=w.sourceId,destination_id=w.destinationId,quantity=w.quantity,reason=payload['reason'],approval_hash=payload_hash)
            cp=ReconcileCheckpoint(key=self._key(cmd))
            c.execute("INSERT INTO rehearsal_execution(operation_id,tenant,warehouse_id,command,request_fingerprint,checkpoint,state,next_at,last_code) VALUES(?,?,?,?,?,?,'REHEARSAL_UNKNOWN',?,'REHEARSAL_SEND_INTENT')",(oid,p.tenant,args.warehouse_id,canonical(cmd.model_dump()),cmd.fingerprint(),canonical(cp.model_dump()),self.store.now(c)))
            self.op._event(c,draft,p.actor,'REHEARSAL_SEND_INTENT',{'fingerprint':cmd.fingerprint(),'real_dispatch':False})
            row,_,cp=self._load(c,p,oid); return self._view(c,row,cp)

    def view(self,p,oid):
        with self.store.tx() as c:
            self.op._load(c,p,oid)
            row,_,cp=self._load(c,p,oid); return self._view(c,row,cp)

    def fixture_commit(self,p,oid):
        """Test-only independent transaction; creates a fake receipt, not inventory changes."""
        if p.actor!='local-fixture': raise Forbidden('FIXTURE_IDENTITY_REQUIRED')
        with self.store.tx() as c:
            row,cmd,cp=self._load(c,p,oid)
            if row['fixture_receipt'] is not None: return
            receipt=Receipt(operationId=oid,warehouseId=cmd.warehouse_id,taskId=cmd.task_id,requestFingerprint=cmd.fingerprint(),businessState='CANCELLED',recordedAt=datetime.now(timezone.utc).isoformat())
            c.execute('UPDATE rehearsal_execution SET fixture_receipt=? WHERE operation_id=?',(canonical(receipt.model_dump()),oid))

    def claim(self,p,lease=30):
        if p.actor!='local-reconciler': raise Forbidden('RECONCILER_IDENTITY_REQUIRED')
        if not p.warehouses: return None
        if not 1<=lease<=120: raise ValueError('invalid lease')
        warehouses=sorted(p.warehouses); placeholders=','.join('?' for _ in warehouses)
        with self.store.tx() as c:
            now=self.store.now(c)
            row=self.store.locked(c,"SELECT * FROM rehearsal_execution WHERE tenant=? AND warehouse_id IN ("+placeholders+") AND state='REHEARSAL_UNKNOWN' AND attempts<? AND next_at<=? AND lease_until<=? ORDER BY next_at,operation_id LIMIT 1",(p.tenant,*warehouses,MAX_ATTEMPTS,now,now),skip=True)
            if not row:return None
            oid=row['operation_id']; self._load(c,p,oid); fence=row['fence']+1
            c.execute('UPDATE rehearsal_execution SET fence=?,attempts=attempts+1,lease_until=? WHERE operation_id=?',(fence,now+lease,oid))
            return oid,fence

    def _guard(self,c,p,oid,fence):
        if p.actor!='local-reconciler': raise Forbidden('RECONCILER_IDENTITY_REQUIRED')
        row,cmd,cp=self._load(c,p,oid)
        if row['state']!='REHEARSAL_UNKNOWN' or row['fence']!=fence or row['lease_until']<=self.store.now(c):raise LostClaim('REHEARSAL_CLAIM_LOST')
        return row,cmd,cp

    def observe(self,p,oid,fence):
        with self.store.tx() as c:
            row,_,_=self._guard(c,p,oid,fence)
            if row['fixture_receipt'] is None:return unknown('FIXTURE_RECEIPT_NOT_OBSERVED')
            return ReceiptObservation(status='COMMITTED',code='FIXTURE_RECEIPT_MATCH',receipt=Receipt.model_validate(strict_json(row['fixture_receipt'])))

    def finish(self,p,oid,fence,obs):
        with self.store.tx() as c:
            row,_,cp=self._guard(c,p,oid,fence)
            if obs.status=='COMMITTED' and (row['fixture_receipt'] is None or canonical(obs.receipt.model_dump())!=row['fixture_receipt']):raise Conflict('FIXTURE_RECEIPT_MISMATCH')
            cp=cp.apply(obs); state='REHEARSAL_'+cp.state
            c.execute('UPDATE rehearsal_execution SET state=?,checkpoint=?,last_code=?,lease_until=0,next_at=? WHERE operation_id=?',(state,canonical(cp.model_dump()),obs.code,self.store.now(c)+2**row['attempts'],oid))
            draft=c.execute('SELECT * FROM operation_draft WHERE id=?',(oid,)).fetchone()
            self.op._event(c,draft,p.actor,'REHEARSAL_RECONCILED',{'state':state,'code':obs.code,'fence':fence,'evidence_hash':cp.evidence_hash})
            row,_,cp=self._load(c,p,oid); return self._view(c,row,cp)

    def once(self,p):
        claimed=self.claim(p)
        if claimed is None:return None
        return self.finish(p,*claimed,self.observe(p,*claimed))
