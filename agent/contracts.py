from __future__ import annotations
import hashlib, json
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .integrations.outbound import OutboundArgs
from .integrations.wes import WesArgs


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(obj):
    return hashlib.sha256(canonical(obj).encode()).hexdigest()


def strict_json(raw):
    def pairs(xs):
        result = {}
        for k, v in xs:
            if k in result:
                raise ValueError('duplicate JSON key')
            result[k] = v
        return result
    def invalid(_):
        raise ValueError('non-finite JSON value')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


class StockWorkArgs(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    warehouse_id: int = Field(ge=1, le=9223372036854775807)
    task_id: int = Field(ge=1, le=9223372036854775807)

class Work(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    type: Literal['PUTAWAY','TRANSFER']
    skuId: int = Field(gt=0)
    lotNo: str = Field(min_length=1,max_length=64)
    sourceId: int | None = None
    destinationId: int = Field(gt=0)
    quantity: int = Field(gt=0)

class Task(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    id: int = Field(gt=0)
    taskKey: str = Field(min_length=1,max_length=64)
    priority: int = Field(ge=0,le=100)
    state: Literal['READY','IN_FLIGHT','UNKNOWN','SUCCEEDED','FAILED','CANCELLED']
    work: Work

class LedgerEntry(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    locationId: int = Field(gt=0)
    action: str = Field(min_length=1,max_length=128)
    deltaOnHand: int
    deltaAvailable: int
    deltaReserved: int
    deltaFrozen: int

class StockWorkResult(StockWorkArgs):
    status: Literal['FOUND','NOT_FOUND']
    task: Task | None = None
    ledger: list[LedgerEntry] = Field(default_factory=list,max_length=2000)
    source: Literal['STONE_HTTP','SYNTHETIC_FIXTURE']
    observed_at: str
    @model_validator(mode='after')
    def check_status(self):
        if self.status=='FOUND' and (self.task is None or self.task.id!=self.task_id):
            raise ValueError('task mismatch')
        if self.status=='NOT_FOUND' and (self.task is not None or self.ledger):
            raise ValueError('invalid missing task')
        return self

class GoalContract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['READ_STOCK_WORK', 'DIAGNOSE_STOCK_WORK', 'ANALYZE_STOCK_WORK', 'COMPARE_STOCK_WORK', 'DEEP_DIAGNOSE_STOCK_WORK', 'DIAGNOSE_OUTBOUND_ALLOCATION', 'DIAGNOSE_WES_DISPATCH', 'CLARIFY', 'UNSUPPORTED']
    entity: StockWorkArgs | OutboundArgs | WesArgs | None = None
    entities: list[StockWorkArgs] = Field(default_factory=list,max_length=3)
    missing_fields: list[str] = Field(default_factory=list)
    unresolved_items: list[str] = Field(default_factory=list)
    provenance: Literal['CONFIRMED_STRUCTURED_INPUT', 'FIXTURE_ONLY']

    @model_validator(mode='after')
    def validate_goal(self):
        if self.kind=='DIAGNOSE_WES_DISPATCH':
            if not isinstance(self.entity,WesArgs) or self.entities or self.missing_fields or self.unresolved_items:
                raise ValueError('dispatch diagnosis requires confirmed warehouse, device and limit')
        elif isinstance(self.entity,WesArgs): raise ValueError('dispatch entity requires dispatch goal')
        if self.kind=='DIAGNOSE_OUTBOUND_ALLOCATION':
            if not isinstance(self.entity,OutboundArgs) or self.entities or self.missing_fields or self.unresolved_items:
                raise ValueError('outbound diagnosis requires confirmed order')
        elif isinstance(self.entity,OutboundArgs): raise ValueError('outbound entity requires outbound goal')
        if self.kind in ('READ_STOCK_WORK','DIAGNOSE_STOCK_WORK','ANALYZE_STOCK_WORK','DEEP_DIAGNOSE_STOCK_WORK') and (self.entity is None or self.entities or self.missing_fields or self.unresolved_items):
            raise ValueError('single goal requires a complete entity')
        if self.kind=='COMPARE_STOCK_WORK':
            if self.entity is not None or len(self.entities)<2 or self.missing_fields or self.unresolved_items or len({(a.warehouse_id,a.task_id) for a in self.entities})!=len(self.entities):
                raise ValueError('comparison requires 2–3 distinct confirmed entities')
        elif self.entities: raise ValueError('entities only allowed for comparison')
        return self


@dataclass(frozen=True)
class Principal:
    tenant: str
    actor: str
    warehouses: frozenset[int]


@dataclass(frozen=True)
class ToolCall:
    provider_call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ModelTurn:
    finish: str
    calls: tuple[ToolCall, ...] = ()
    usage: int | None = None


class Forbidden(Exception): pass
class Conflict(Exception): pass
class LostClaim(Exception): pass
class ProposalError(Exception): pass
class ModelAttemptError(Exception): pass

class DomainReadError(Exception): pass

class StockWorkDiagnosis(StockWorkArgs):
    model_config = ConfigDict(extra='forbid',strict=True)
    source_evidence_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    status: Literal['FOUND','NOT_FOUND']
    task_state: Literal['READY','IN_FLIGHT','UNKNOWN','SUCCEEDED','FAILED','CANCELLED'] | None
    interpretation: str
    ledger_entries: int = Field(ge=0)
    root_cause: Literal['UNDETERMINED'] = 'UNDETERMINED'
    missing_evidence: list[str]
    source: Literal['STONE_HTTP','SYNTHETIC_FIXTURE']


class LedgerMovement(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    locationId: int = Field(gt=0)
    deltaOnHand: int
    deltaAvailable: int
    deltaReserved: int
    deltaFrozen: int
    entries: int = Field(gt=0)

class StockWorkLedgerSummary(StockWorkArgs):
    model_config = ConfigDict(extra='forbid',strict=True)
    source_evidence_hash: str = Field(pattern=r'^[a-f0-9]{64}$')
    status: Literal['FOUND','NOT_FOUND']
    ledger_entries: int = Field(ge=0)
    movements: list[LedgerMovement] = Field(max_length=2000)
    source: Literal['STONE_HTTP','SYNTHETIC_FIXTURE']


class DispatchEvent(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    eventKey: str = Field(min_length=1,max_length=64)
    targetState: Literal['UNKNOWN','SUCCEEDED','FAILED']
    reconciled: bool
    reason: str | None = Field(default=None,max_length=240)
    completedQty: int | None = Field(default=None,ge=0)
    createdAt: str = Field(pattern=r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}$')

class DispatchHistory(StockWorkArgs):
    task_state: Literal['READY','IN_FLIGHT','UNKNOWN','SUCCEEDED','FAILED','CANCELLED']
    events: list[DispatchEvent] = Field(max_length=100)
    truncated: bool
    source: Literal['STONE_HTTP','SYNTHETIC_FIXTURE']
    observed_at: str

    @model_validator(mode='after')
    def validate_events(self):
        keys=[(e.createdAt,e.eventKey) for e in self.events]
        if keys!=sorted(keys) or len({e.eventKey for e in self.events})!=len(keys):
            raise ValueError('invalid history ordering or duplicate key')
        if self.truncated and len(keys)!=100: raise ValueError('invalid truncation marker')
        return self
