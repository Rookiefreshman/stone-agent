"""Read-only allocation snapshot analysis, independent of model and storage."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)

class OutboundArgs(Strict):
    warehouse_id: int=Field(ge=1,le=9223372036854775807)
    order_id: int=Field(ge=1,le=9223372036854775807)

class Line(Strict):
    lineId: int=Field(gt=0)
    locationId: int=Field(gt=0)
    skuId: int=Field(gt=0)
    lotNo: str=Field(max_length=64)
    orderedQty: int=Field(gt=0,le=2147483647)
    allocatedQty: int=Field(ge=0,le=2147483647)
    inventoryId: int|None=Field(gt=0)
    onHandQty: int|None=Field(ge=0,le=2147483647)
    availableQty: int|None=Field(ge=0,le=2147483647)
    reservedQty: int|None=Field(ge=0,le=2147483647)
    frozenQty: int|None=Field(ge=0,le=2147483647)
    inventoryVersion: int|None=Field(ge=0)
    locationState: Literal['OPEN','FROZEN','UNCONFIGURED']

    @model_validator(mode='after')
    def check(self):
        if self.allocatedQty>self.orderedQty: raise ValueError('invalid allocated quantity')
        quantities=(self.onHandQty,self.availableQty,self.reservedQty,self.frozenQty,self.inventoryVersion)
        if self.inventoryId is None:
            if any(x is not None for x in quantities): raise ValueError('missing inventory carries balances')
        elif any(x is None for x in quantities) or self.onHandQty!=self.availableQty+self.reservedQty+self.frozenQty:
            raise ValueError('inventory quantity invariant')
        return self

class Snapshot(Strict):
    warehouseId: int=Field(gt=0)
    orderId: int=Field(gt=0)
    orderNo: str=Field(min_length=1,max_length=64)
    orderStatus: Literal['DRAFT','APPROVED','ALLOCATED','PICKING','PICKED','CHECKED','SHIPPED','COMPLETED','CANCELLED','EXCEPTION']
    receiptPresent: bool
    lines: list[Line]=Field(min_length=1,max_length=200)

    @model_validator(mode='after')
    def check(self):
        if len({x.lineId for x in self.lines})!=len(self.lines): raise ValueError('duplicate line')
        seen={}; locations={}; dimensions={}
        for line in self.lines:
            if locations.setdefault(line.locationId,line.locationState)!=line.locationState: raise ValueError('location state mismatch')
            dimension=(line.locationId,line.skuId,line.lotNo)
            if dimensions.setdefault(dimension,line.inventoryId)!=line.inventoryId: raise ValueError('dimension identity mismatch')
            if line.inventoryId is not None:
                # Use the joined inventory ID, not Python lot-string equality (MySQL collation may differ).
                signature=(line.locationId,line.skuId,line.onHandQty,line.availableQty,line.reservedQty,line.frozenQty,line.inventoryVersion)
                if seen.setdefault(line.inventoryId,signature)!=signature: raise ValueError('inventory snapshot mismatch')
        return self

class OutboundResult(OutboundArgs):
    status: Literal['FOUND','NOT_FOUND']
    snapshot: Snapshot|None
    source: Literal['STONE_HTTP','SYNTHETIC_FIXTURE']
    observed_at: str

    @model_validator(mode='after')
    def check(self):
        if self.status=='NOT_FOUND':
            if self.snapshot is not None: raise ValueError('unexpected missing order payload')
        elif self.snapshot is None or (self.snapshot.warehouseId,self.snapshot.orderId)!=(self.warehouse_id,self.order_id):
            raise ValueError('order identity mismatch')
        return self


def analyze(result: OutboundResult):
    """Current snapshot only. Never claim a historical failure cause or authorize writes."""
    base={'historical_root_cause':'UNDETERMINED','write_allowed':False,'source':result.source,'observed_at':result.observed_at}
    if result.status=='NOT_FOUND':
        return {**base,'assessment':'NOT_FOUND','blockers':[],'groups':[],'notes':['授权范围内未找到订单。']}
    s=result.snapshot; blockers=[]; groups={}; notes=[]
    def block(code,**kw): blockers.append({'code':code,**kw})
    if s.receiptPresent:
        notes.append('存在成功分配回执；同键重放由 Stone 判定，新键不能重复分配。')
        return {**base,'assessment':'ALLOCATION_RECEIPT_EXISTS','blockers':[], 'groups':[], 'notes':notes}
    if s.orderStatus!='APPROVED': block('ORDER_STATUS_NOT_APPROVED',status=s.orderStatus)
    for line in s.lines:
        remaining=line.orderedQty-line.allocatedQty
        if remaining==0: block('LINE_ALREADY_ALLOCATED',line_id=line.lineId)
        if line.locationState=='FROZEN': block('SOURCE_LOCATION_FROZEN',line_id=line.lineId,location_id=line.locationId)
        if line.locationState=='UNCONFIGURED': notes.append(f'库位 {line.locationId} 未配置；当前出库分配逻辑不将此情况视为冻结。')
        if line.inventoryId is None:
            block('EXACT_AVAILABLE_INVENTORY_MISSING',line_id=line.lineId,location_id=line.locationId,sku_id=line.skuId,lot_no=line.lotNo)
            continue
        g=groups.setdefault(line.inventoryId,{'inventory_id':line.inventoryId,'location_id':line.locationId,'sku_id':line.skuId,'available_qty':line.availableQty,'required_qty':0,'line_ids':[],'inventory_version':line.inventoryVersion})
        g['required_qty']+=remaining; g['line_ids'].append(line.lineId)
    for g in groups.values():
        g['shortage_qty']=max(0,g['required_qty']-g['available_qty'])
        if g['shortage_qty']: block('INSUFFICIENT_AVAILABLE',inventory_id=g['inventory_id'],shortage_qty=g['shortage_qty'],line_ids=g['line_ids'])
    notes.extend(['仅诊断当前数据库快照，不还原历史失败原因。','未验证实物库存、并发版本冲突及未来分配事务；无阻塞也不等于允许写入。'])
    return {**base,'assessment':'CURRENT_BLOCKERS' if blockers else 'NO_CURRENT_BLOCKER_OBSERVED','blockers':blockers,'groups':list(groups.values()),'notes':list(dict.fromkeys(notes))}


def fixture(args: OutboundArgs, observed_at: str):
    """Synthetic demo only: two lines sharing one inventory record (6+5 > 10)."""
    if args.warehouse_id!=1 or args.order_id!=201:
        return OutboundResult(**args.model_dump(),status='NOT_FOUND',snapshot=None,source='SYNTHETIC_FIXTURE',observed_at=observed_at)
    row=dict(locationId=10,skuId=8,lotNo='L01',allocatedQty=0,inventoryId=99,onHandQty=15,availableQty=10,reservedQty=5,frozenQty=0,inventoryVersion=3,locationState='OPEN')
    snap=Snapshot(warehouseId=1,orderId=201,orderNo='DEMO:201',orderStatus='APPROVED',receiptPresent=False,lines=[dict(row,lineId=1,orderedQty=6),dict(row,lineId=2,orderedQty=5)])
    return OutboundResult(**args.model_dump(),status='FOUND',snapshot=snap,source='SYNTHETIC_FIXTURE',observed_at=observed_at)
