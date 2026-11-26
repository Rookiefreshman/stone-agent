"""Read-only dispatch diagnostics using Stone's claim capacity rules."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class WesArgs(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    warehouse_id: int=Field(ge=1,le=9223372036854775807)
    device_code: str=Field(pattern=r'^[A-Za-z0-9._:/-]{1,64}$')
    requested_limit: int=Field(ge=1,le=100)

class WesSnapshot(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    warehouseId: int=Field(ge=1,le=9223372036854775807)
    deviceCode: str=Field(pattern=r'^[A-Za-z0-9._:/-]{1,64}$')
    maxReady: int=Field(ge=0,le=2147483647)
    maxActive: int=Field(ge=0,le=2147483647)
    maxBatch: int=Field(ge=0,le=2147483647)
    deviceMaxActive: int=Field(ge=0,le=2147483647)
    ready: int=Field(ge=0,le=2147483647)
    inFlight: int=Field(ge=0,le=2147483647)
    unknown: int=Field(ge=0,le=2147483647)
    deviceInFlight: int=Field(ge=0,le=2147483647)
    deviceUnknown: int=Field(ge=0,le=2147483647)

    @model_validator(mode='after')
    def check(self):
        if self.deviceInFlight>self.inFlight or self.deviceUnknown>self.unknown:
            raise ValueError('device counts exceed warehouse counts')
        if self.inFlight+self.unknown>2147483647:
            raise ValueError('active count exceeds Stone signed int range')
        return self

class WesResult(WesArgs):
    status: Literal['FOUND','NOT_FOUND']
    snapshot: WesSnapshot|None
    source: Literal['STONE_HTTP','SYNTHETIC_FIXTURE']
    observed_at: str

    @model_validator(mode='after')
    def check(self):
        if self.status=='NOT_FOUND':
            if self.snapshot is not None: raise ValueError('unexpected missing scope payload')
        elif self.snapshot is None or (self.snapshot.warehouseId,self.snapshot.deviceCode)!=(self.warehouse_id,self.device_code):
            raise ValueError('warehouse or device identity mismatch')
        return self


def analyze(result: WesResult):
    """Estimate a NEW claim at observed snapshot; never issue or replay one."""
    base=dict(source=result.source,observed_at=result.observed_at,root_cause='UNDETERMINED',write_allowed=False)
    if result.status=='NOT_FOUND':
        return {**base,'assessment':'NOT_FOUND','claim_upper_bound':None,'limiting_factors':[], 'notes':['授权范围内未找到已配置的仓库和设备组合。']}
    s=result.snapshot
    active=s.inFlight+s.unknown; device_active=s.deviceInFlight+s.deviceUnknown
    warehouse_remaining=s.maxActive-active; device_remaining=s.deviceMaxActive-device_active
    factors={'REQUEST_LIMIT':result.requested_limit,'MAX_BATCH':s.maxBatch,'WAREHOUSE_ACTIVE_SLOTS':max(0,warehouse_remaining),'DEVICE_ACTIVE_SLOTS':max(0,device_remaining),'READY_TASKS':s.ready}
    limit=min(factors.values()); limiting=[k for k,v in factors.items() if v==limit]
    blockers=[k for k,v in factors.items() if v==0]
    notes=['仅为当前快照的新领取数量上限；不适用于已有领取键重放，实际领取须由 Stone 锁仓事务重新判断。','未观察到真实设备在线状态、现场执行或历史失败原因；容量限制不等于硬件故障。']
    if s.unknown: notes.append('UNKNOWN 按规则占用仓库在途槽；不得自动释放或重新派发。')
    if s.deviceUnknown: notes.append('该设备存在 UNKNOWN 任务，须依据设备回执和现场核对处理。')
    if s.ready>=s.maxReady: notes.append('READY 队列达到入队上限；maxReady 限制新入队，不限制已有任务领取。')
    if warehouse_remaining<0 or device_remaining<0: notes.append('当前在途量超过配置上限（例如缩容）；暂停新领取，不撤销现有任务。')
    return {**base,'assessment':'NO_NEW_CLAIM_CAPACITY' if limit==0 else 'CAPACITY_AVAILABLE_AT_SNAPSHOT',
            'claim_upper_bound':limit,'limiting_factors':limiting,'blocking_factors':blockers,'factors':factors,
            'warehouse_active':active,'warehouse_unknown':s.unknown,'warehouse_remaining_raw':warehouse_remaining,
            'device_active':device_active,'device_unknown':s.deviceUnknown,'device_remaining_raw':device_remaining,
            'queue_admission_full':s.ready>=s.maxReady,'ready':s.ready,'notes':notes}


def fixture(args: WesArgs, observed_at: str):
    """Synthetic example: warehouse has 3 slots left, selected device has none."""
    snapshot=None
    if (args.warehouse_id,args.device_code)==(1,'station-01'):
        snapshot=WesSnapshot(warehouseId=1,deviceCode='station-01',maxReady=200,maxActive=16,maxBatch=8,deviceMaxActive=4,ready=25,inFlight=9,unknown=4,deviceInFlight=2,deviceUnknown=2)
    return WesResult(**args.model_dump(),status='FOUND' if snapshot else 'NOT_FOUND',snapshot=snapshot,source='SYNTHETIC_FIXTURE',observed_at=observed_at)
