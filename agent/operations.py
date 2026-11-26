"""v0.9: cancellation PREVIEW and rehearsal approval only. No write dispatcher."""
from datetime import datetime
from typing import Literal
import uuid
from pydantic import BaseModel, ConfigDict, Field
from .contracts import (StockWorkArgs, StockWorkResult, GoalContract, Conflict, Forbidden,
                        canonical, digest, strict_json)
from .tools import authorize

OP_DDL = (
    """CREATE TABLE IF NOT EXISTS operation_draft (
        id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
        request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES run(id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('AWAITING_REVIEW','REHEARSAL_APPROVED','REJECTED','WITHDRAWN')),
        created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
        reviewer TEXT, review_decision TEXT,
        UNIQUE(tenant,actor,request_key))""",
    """CREATE TABLE IF NOT EXISTS operation_event (
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operation_draft(id),
        kind TEXT NOT NULL, actor TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
        detail TEXT NOT NULL)""",
    "CREATE INDEX IF NOT EXISTS operation_event_idx ON operation_event(operation_id,created_at,id)",
)

class DraftRequest(StockWorkArgs):
    run_id: str = Field(min_length=1,max_length=64)
    client_request_id: str = Field(min_length=1,max_length=128)
    reason: str = Field(min_length=1,max_length=240)

class ReviewRequest(BaseModel):
    model_config=ConfigDict(strict=True,extra='forbid')
    payload_hash: str=Field(pattern=r'^[a-f0-9]{64}$')
    decision: Literal['APPROVE_REHEARSAL','REJECT']

class OperationService:
    """Trusted server-side entry; separate from model tools and worker queue."""
    def __init__(self, store, registry, *, rehearsal_enabled=False):
        self.store=store; self.registry=registry; self.rehearsal_enabled=rehearsal_enabled

    def _event(self,c,row,actor,kind,detail):
        c.execute('INSERT INTO operation_event VALUES(?,?,?,?,?,?)',
            (str(uuid.uuid4()),row['id'],kind,actor,self.store.now(c),canonical(detail)))

    def _evidence(self,c,p,run_id,args):
        authorize(p,args)
        run=c.execute('SELECT * FROM run WHERE id=? AND tenant=? AND actor=?',(run_id,p.tenant,p.actor)).fetchone()
        if not run: raise Forbidden('NOT_ACCESSIBLE')
        if run['state']!='COMPLETED' or run['cancelled']: raise Conflict('READ_NOT_COMPLETED')
        try:
            g=GoalContract.model_validate(strict_json(run['goal']))
            if g.kind!='READ_STOCK_WORK' or g.entity!=args or g.provenance!='CONFIRMED_STRUCTURED_INPUT':
                raise Conflict('READ_GOAL_MISMATCH')
            inv=c.execute('SELECT * FROM invocation WHERE run_id=? AND step_no=1',(run_id,)).fetchone()
            if not inv or inv['state']!='SUCCEEDED' or inv['alias']!='get_stock_work_detail' or inv['origin']!='RULE':
                raise Conflict('READ_EVIDENCE_MISSING')
            raw=strict_json(inv['result']); saved=strict_json(inv['args']); snapshot=strict_json(inv['direct_snapshot'])
            if saved!=args.model_dump() or inv['args_hash']!=digest(saved) or inv['evidence_hash']!=digest(raw):
                raise Conflict('READ_EVIDENCE_INVALID')
            if snapshot!=self.registry.snapshot({'get_stock_work_detail'}) or not snapshot:
                raise Conflict('READ_BINDING_CHANGED')
            result=StockWorkResult.model_validate(raw)
            if (result.warehouse_id,result.task_id)!=(args.warehouse_id,args.task_id): raise Conflict('READ_EVIDENCE_INVALID')
            stamp=datetime.fromisoformat(result.observed_at)
            if stamp.tzinfo is None: raise ValueError('timezone required')
            observed=stamp.timestamp(); now=self.store.now(c)
            if observed>now+5 or now-observed>=120: raise Conflict('READ_EVIDENCE_STALE')
        except (ValueError,TypeError,KeyError) as exc: raise Conflict('READ_EVIDENCE_INVALID') from exc
        return result,inv['evidence_hash'],observed,snapshot

    def preflight(self,p,run_id,args):
        with self.store.tx() as c:
            result,h,stamp,_=self._evidence(c,p,run_id,args)
            return self._preview(result,h,stamp)

    @staticmethod
    def _preview(result,h,stamp):
        state=result.task.state if result.task else 'NOT_FOUND'
        effects=[]
        if state=='READY':
            effects=['若 Stone 取消事务成功，待执行作业不再占用目标库位待移入容量。']
            if result.task.work.type=='TRANSFER': effects.append('若取消提交成功，释放该移库作业源库存预占；不代表已经释放。')
            else: effects.append('若取消提交成功，作业不再占用收货明细的已承诺上架量。')
        return dict(observed_state=state,eligible_for_draft=state=='READY',source=result.source,
            observed_at=result.observed_at,evidence_hash=h,evidence_expires_at=stamp+120,
            conditional_effects=effects,write_allowed=False,
            note='仅状态预检；库位、库存、权限及实时状态仍需 Stone 写事务重新检查。CANCELLED 不证明本次操作已提交。')

    def _view(self,c,row):
        payload=strict_json(row['payload'])
        if digest(payload)!=row['payload_hash']: raise Conflict('DRAFT_INTEGRITY_ERROR')
        state=row['state']
        if state in ('AWAITING_REVIEW','REHEARSAL_APPROVED') and self.store.now(c)>=row['expires_at']: state='EXPIRED'
        return dict(operation_id=row['id'],state=state,payload=payload,payload_hash=row['payload_hash'],
            expires_at=row['expires_at'],reviewer=row['reviewer'],review_decision=row['review_decision'],
            write_allowed=False,dispatch_state='NEVER_DISPATCHED',
            events=[dict(e) for e in c.execute('SELECT kind,actor,created_at,detail FROM operation_event WHERE operation_id=? ORDER BY created_at,id',(row['id'],))])

    def _load(self,c,p,oid,reviewer=False):
        row=self.store.locked(c,'SELECT * FROM operation_draft WHERE id=? AND tenant=?',(oid,p.tenant))
        if not row or (not reviewer and row['actor']!=p.actor): raise Forbidden('NOT_ACCESSIBLE')
        payload=strict_json(row['payload'])
        if digest(payload)!=row['payload_hash']: raise Conflict('DRAFT_INTEGRITY_ERROR')
        authorize(p,StockWorkArgs.model_validate(payload['entity']))
        return row,payload

    def create(self,p,req):
        args=StockWorkArgs(warehouse_id=req.warehouse_id,task_id=req.task_id); authorize(p,args)
        reason=req.reason.strip()
        if not reason: raise Conflict('REASON_REQUIRED')
        fp=digest([req.run_id,args.model_dump(),reason])
        with self.store.tx() as c:
            def existing(): return c.execute('SELECT * FROM operation_draft WHERE tenant=? AND actor=? AND request_key=?',(p.tenant,p.actor,req.client_request_id)).fetchone()
            old=existing()
            if old:
                if old['request_hash']!=fp: raise Conflict('IDEMPOTENCY_CONFLICT')
                return self._view(c,old)
            result,h,stamp,snapshot=self._evidence(c,p,req.run_id,args)
            if result.status!='FOUND' or result.task.state!='READY': raise Conflict('NOT_READY_FOR_DRAFT')
            oid=str(uuid.uuid4()); now=self.store.now(c)
            payload=dict(operation='CANCEL_READY_STOCK_WORK',entity=args.model_dump(),reason=reason,
                tenant=p.tenant,requester=p.actor,operation_id=oid,read_run_id=req.run_id,
                task=result.task.model_dump(),evidence_hash=h,observed_at=result.observed_at,
                source=result.source,binding=snapshot,policy='CANCEL_REHEARSAL_V1')
            ph=digest(payload)
            n=c.execute('INSERT INTO operation_draft(id,tenant,actor,request_key,request_hash,run_id,payload,payload_hash,state,created_at,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant,actor,request_key) DO NOTHING',
                (oid,p.tenant,p.actor,req.client_request_id,fp,req.run_id,canonical(payload),ph,'AWAITING_REVIEW',now,stamp+120)).rowcount
            row=existing()
            if not n:
                if row['request_hash']!=fp: raise Conflict('IDEMPOTENCY_CONFLICT')
            else: self._event(c,row,p.actor,'DRAFT_CREATED',{'payload_hash':ph})
            return self._view(c,row)

    def get(self,p,oid,*,reviewer=False):
        with self.store.tx() as c:
            row,_=self._load(c,p,oid,reviewer)
            return self._view(c,row)

    def review(self,p,oid,req):
        if not self.rehearsal_enabled: raise Forbidden('REHEARSAL_DISABLED')
        with self.store.tx() as c:
            row,payload=self._load(c,p,oid,True)
            if row['actor']==p.actor: raise Forbidden('SELF_REVIEW_FORBIDDEN')
            if req.payload_hash!=row['payload_hash']: raise Conflict('PAYLOAD_HASH_MISMATCH')
            if payload['source']!='SYNTHETIC_FIXTURE': raise Forbidden('LIVE_APPROVAL_DISABLED')
            if row['reviewer']==p.actor and row['review_decision']==req.decision: return self._view(c,row)
            if row['state']!='AWAITING_REVIEW' or self.store.now(c)>=row['expires_at']: raise Conflict('DRAFT_NOT_REVIEWABLE')
            if payload['binding']!=self.registry.snapshot({'get_stock_work_detail'}): raise Conflict('READ_BINDING_CHANGED')
            state='REHEARSAL_APPROVED' if req.decision=='APPROVE_REHEARSAL' else 'REJECTED'
            c.execute('UPDATE operation_draft SET state=?,reviewer=?,review_decision=? WHERE id=?',(state,p.actor,req.decision,oid))
            self._event(c,row,p.actor,state,{'payload_hash':req.payload_hash})
            return self._view(c,self._load(c,p,oid,True)[0])

    def withdraw(self,p,oid):
        with self.store.tx() as c:
            row,_=self._load(c,p,oid)
            if c.execute('SELECT operation_id FROM rehearsal_execution WHERE operation_id=?',(oid,)).fetchone(): raise Conflict('REHEARSAL_ALREADY_CONSUMED')
            if row['state']=='WITHDRAWN': return self._view(c,row)
            if row['state'] not in ('AWAITING_REVIEW','REHEARSAL_APPROVED') or self.store.now(c)>=row['expires_at']:
                raise Conflict('DRAFT_NOT_WITHDRAWABLE')
            c.execute("UPDATE operation_draft SET state='WITHDRAWN' WHERE id=?",(oid,))
            self._event(c,row,p.actor,'WITHDRAWN',{'payload_hash':row['payload_hash']})
            return self._view(c,self._load(c,p,oid)[0])
