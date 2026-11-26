"""Read-only operation receipt protocol. Not registered as a model tool.
No transition from rehearsal approval; no send/write API. A future trusted dispatcher
must persist its own send intent and exact fingerprint before a reconciliation lookup.
"""
from datetime import datetime, timezone
import hashlib, struct, urllib.request, urllib.parse, urllib.error
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import StockWorkArgs, strict_json, digest, Conflict
from .tools import authorize, NoRedirect

UUID_PATTERN=r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
HASH_PATTERN=r'^[a-f0-9]{64}$'

class CancelCommand(StockWorkArgs):
    operation_id: str=Field(pattern=UUID_PATTERN)
    actor: str=Field(min_length=1,max_length=128)
    tenant: str=Field(min_length=1,max_length=128)
    task_key: str=Field(pattern=r'^[A-Za-z0-9._:/-]{1,64}$')
    work_type: Literal['PUTAWAY','TRANSFER']
    sku_id: int=Field(gt=0,le=9223372036854775807)
    lot_no: str=Field(pattern=r'^[A-Za-z0-9._-]{1,64}$')
    source_id: int|None=Field(default=None,gt=0,le=9223372036854775807)
    destination_id: int=Field(gt=0,le=9223372036854775807)
    quantity: int=Field(gt=0,le=2147483647)
    reason: str=Field(min_length=1,max_length=240)
    approval_hash: str=Field(pattern=HASH_PATTERN)

    @model_validator(mode='after')
    def check(self):
        if not self.reason.strip() or (self.work_type=='PUTAWAY' and self.source_id is not None) or (self.work_type=='TRANSFER' and (self.source_id is None or self.source_id==self.destination_id)):
            raise ValueError('invalid work or reason')
        return self

    def fingerprint(self):
        fields=['CANCEL_READY_V1',self.operation_id,str(self.warehouse_id),str(self.task_id),self.tenant,self.actor,self.task_key,self.work_type,str(self.sku_id),self.lot_no,'' if self.source_id is None else str(self.source_id),str(self.destination_id),str(self.quantity),self.reason,self.approval_hash]
        h=hashlib.sha256()
        for field in fields:
            raw=field.encode('utf-8');h.update(struct.pack('>I',len(raw)));h.update(raw)
        return h.hexdigest()

class ReceiptKey(StockWorkArgs):
    operation_id: str=Field(pattern=UUID_PATTERN)
    request_fingerprint: str=Field(pattern=HASH_PATTERN)

class Receipt(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    operationId: str=Field(pattern=UUID_PATTERN)
    warehouseId: int=Field(gt=0)
    taskId: int=Field(gt=0)
    requestFingerprint: str=Field(pattern=HASH_PATTERN)
    businessState: Literal['CANCELLED']
    recordedAt: str=Field(max_length=32)

    @model_validator(mode='after')
    def check_time(self):
        t=datetime.fromisoformat(self.recordedAt.replace('Z','+00:00'))
        if t.tzinfo is None or t.utcoffset().total_seconds()!=0: raise ValueError('UTC receipt time required')
        return self

class ReceiptObservation(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    status: Literal['COMMITTED','UNKNOWN']
    code: str=Field(min_length=1,max_length=80)
    receipt: Receipt|None=None
    @model_validator(mode='after')
    def check(self):
        if (self.status=='COMMITTED')!=(self.receipt is not None): raise ValueError('invalid observation')
        return self

def unknown(code): return ReceiptObservation(status='UNKNOWN',code=code)

def matches(key,receipt):
    return (receipt.operationId,receipt.warehouseId,receipt.taskId,receipt.requestFingerprint)==(key.operation_id,key.warehouse_id,key.task_id,key.request_fingerprint)

class ReceiptReader:
    """Dedicated read credential; origin is configured, never supplied by the model."""
    def __init__(self,origin,token,timeout=8):
        u=urllib.parse.urlsplit(origin)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/') or not token or not token.strip(): raise ValueError('configured receipt origin and credential required')
        self.origin=origin.rstrip('/');self.token=token;self.timeout=timeout
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect)

    def query(self,principal,key):
        authorize(principal,key)
        url=f'{self.origin}/api/wms/warehouses/{key.warehouse_id}/cancel-operation-receipts/{key.operation_id}'
        try:
            req=urllib.request.Request(url,headers={'Accept':'application/json','Authorization':'Bearer '+self.token},method='GET')
            with self.opener.open(req,timeout=self.timeout) as response: raw=response.read(16385)
            if len(raw)>16384:return unknown('RECEIPT_TOO_LARGE')
            body=strict_json(raw.decode('utf-8'))
            if not isinstance(body,dict) or set(body)!= {'success','message','data'} or body['success'] is not True:return unknown('RECEIPT_ENVELOPE_INVALID')
            receipt=Receipt.model_validate(body['data'])
            if not matches(key,receipt):return unknown('RECEIPT_IDENTITY_MISMATCH')
            return ReceiptObservation(status='COMMITTED',code='MATCHING_RECEIPT',receipt=receipt)
        except urllib.error.HTTPError as exc:
            if exc.code==404:
                try:
                    body=strict_json(exc.read(16385).decode('utf-8'))
                    if body=={'success':False,'message':'OPERATION_RECEIPT_NOT_FOUND','data':None}:return unknown('RECEIPT_NOT_OBSERVED')
                except (ValueError,UnicodeError,OSError):pass
            return unknown('RECEIPT_HTTP_'+str(exc.code))
        except (ValueError,TypeError,KeyError,UnicodeError,OSError):return unknown('RECEIPT_UNVERIFIED')

class ReconcileCheckpoint(BaseModel):
    """Persistable future send-intent checkpoint. No constructor from rehearsal draft.
    Callers must load this only from a trusted, authorized dispatch ledger (not implemented).
    """
    model_config=ConfigDict(extra='forbid',strict=True)
    key: ReceiptKey
    state: Literal['UNKNOWN','COMMITTED']='UNKNOWN'
    receipt: Receipt|None=None
    evidence_hash: str|None=Field(default=None,pattern=HASH_PATTERN)
    @model_validator(mode='after')
    def check(self):
        if self.state=='COMMITTED':
            if self.receipt is None or not matches(self.key,self.receipt) or self.evidence_hash!=digest(self.receipt.model_dump()):raise ValueError('invalid committed evidence')
        elif self.receipt is not None or self.evidence_hash is not None:raise ValueError('unexpected receipt')
        return self

    def apply(self,obs:ReceiptObservation):
        # No receipt/404/timeout cannot prove NOT_COMMITTED, nor authorize retransmission.
        if obs.status=='UNKNOWN':return self
        if not matches(self.key,obs.receipt):raise Conflict('RECEIPT_IDENTITY_MISMATCH')
        h=digest(obs.receipt.model_dump())
        if self.state=='COMMITTED':
            if h!=self.evidence_hash:raise Conflict('COMMITTED_RECEIPT_CHANGED')
            return self
        return ReconcileCheckpoint(key=self.key,state='COMMITTED',receipt=obs.receipt,evidence_hash=h)
