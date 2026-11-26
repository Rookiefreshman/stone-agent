from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
import json, urllib.request, urllib.parse, urllib.error
from .integrations.outbound import OutboundArgs, OutboundResult, fixture as outbound_fixture
from .integrations.wes import WesArgs, WesResult, fixture as wes_fixture
from .contracts import StockWorkArgs, StockWorkResult, StockWorkDiagnosis, StockWorkLedgerSummary, DispatchHistory, Task, Work, LedgerEntry, Principal, Forbidden, ProposalError, DomainReadError, strict_json, digest

@dataclass(frozen=True)
class ToolSpec:
    alias: str
    tool_id: str
    version: int
    binding_revision: str
    handler: Callable
    description: str
    args_model: type=StockWorkArgs
    def schema(self):
        return {'type':'function','function':{'name':self.alias,'description':self.description,'parameters':self.args_model.model_json_schema()}}
    def ref(self):
        return {'tool_id':self.tool_id,'version':self.version,'binding_hash':digest([self.tool_id,self.version,self.binding_revision,self.schema()])}

class Registry:
    def __init__(self,specs):
        self._specs={s.alias:s for s in specs}
        if len(self._specs)!=len(specs): raise ValueError('duplicate alias')
    def snapshot(self,aliases=None): return {k:v.ref() for k,v in self._specs.items() if aliases is None or k in aliases}
    def schemas(self,aliases=None): return [s.schema() for k,s in self._specs.items() if aliases is None or k in aliases]
    def resolve(self,alias,frozen):
        spec=self._specs.get(alias)
        if not spec or frozen.get(alias)!=spec.ref(): raise ProposalError('TOOL_NOT_AVAILABLE')
        return spec

def authorize(principal,args):
    if args.warehouse_id not in principal.warehouses: raise Forbidden('FORBIDDEN')

def validate_call(goal,call,principal):
    try:
        if len(call.arguments_json.encode())>16384: raise ValueError('oversized')
        args=StockWorkArgs.model_validate(strict_json(call.arguments_json))
    except (ValueError,TypeError) as e: raise ProposalError('INVALID_ARGUMENTS') from e
    if goal.kind!='READ_STOCK_WORK' or args!=goal.entity: raise ProposalError('GOAL_MISMATCH')
    authorize(principal,args); return args

def observed(): return datetime.now(timezone.utc).isoformat()

def project(args,data,source):
    # Only the observed DTO fields approved for display. Drop deviceCode, claimKey and receiptKey.
    try:
        t=data['task']; w=t['work']
        work={k:w[k] for k in ('type','skuId','lotNo','destinationId','quantity')}; work['sourceId']=w.get('sourceId')
        task={k:t[k] for k in ('id','taskKey','priority','state')}; task['work']=work
        result=StockWorkResult(**args.model_dump(),status='FOUND',task=task,ledger=data['ledger'],source=source,observed_at=observed())
        return result.model_dump()
    except (KeyError,TypeError,ValueError) as exc: raise DomainReadError('UPSTREAM_CONTRACT_ERROR') from exc

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**kw): return None

class StoneHttpService:
    def __init__(self,base_url,token='',timeout=8):
        u=urllib.parse.urlsplit(base_url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment or u.path not in ('','/'):
            raise ValueError('STONE_BASE_URL must be an operator-configured origin')
        self.base=base_url.rstrip('/'); self.token=token; self.timeout=timeout
        self.revision='stone-stock-work-v1:'+self.base
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect)
    def get_detail(self,principal,args):
        authorize(principal,args)
        # Only this fixed GET endpoint is implemented. No arbitrary URL or method.
        url=f'{self.base}/api/wms/warehouses/{args.warehouse_id}/stock-work/{args.task_id}'
        headers={'Accept':'application/json'}
        if self.token: headers['Authorization']='Bearer '+self.token
        try:
            with self.opener.open(urllib.request.Request(url,headers=headers,method='GET'),timeout=self.timeout) as response:
                raw=response.read(1_000_001)
            if len(raw)>1_000_000: raise DomainReadError('UPSTREAM_RESPONSE_TOO_LARGE')
            body=strict_json(raw.decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code==404:
                # Require the expected Stone failure envelope, not a proxy/router 404.
                try: envelope=strict_json(exc.read(16385).decode('utf-8'))
                except (ValueError,UnicodeError): raise DomainReadError('UPSTREAM_HTTP_404') from exc
                if isinstance(envelope,dict) and envelope.get('success') is False and envelope.get('data') is None and isinstance(envelope.get('message'),str):
                    return StockWorkResult(**args.model_dump(),status='NOT_FOUND',source='STONE_HTTP',observed_at=observed()).model_dump()
            raise DomainReadError('UPSTREAM_HTTP_'+str(exc.code)) from exc
        except (OSError,ValueError,UnicodeError) as exc: raise DomainReadError('UPSTREAM_TRANSPORT_OR_JSON_ERROR') from exc
        if not isinstance(body,dict) or body.get('success') is not True: raise DomainReadError('UPSTREAM_BUSINESS_ERROR')
        return project(args,body.get('data'),'STONE_HTTP')

    def get_wes_dispatch(self,principal,args):
        authorize(principal,args)
        if not self.token: raise DomainReadError('WES_SERVICE_TOKEN_REQUIRED')
        query=urllib.parse.urlencode({'deviceCode':args.device_code})
        url=f'{self.base}/api/wes/warehouses/{args.warehouse_id}/dispatch-diagnostic?{query}'
        try:
            request=urllib.request.Request(url,headers={'Accept':'application/json','Authorization':'Bearer '+self.token},method='GET')
            with self.opener.open(request,timeout=self.timeout) as response:
                raw=response.read(1_000_001)
            if len(raw)>1_000_000: raise DomainReadError('UPSTREAM_RESPONSE_TOO_LARGE')
            body=strict_json(raw.decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code==404:
                try: body=strict_json(exc.read(16385).decode('utf-8'))
                except (ValueError,UnicodeError): body=None
                if body=={'success':False,'message':'DISPATCH_SCOPE_NOT_FOUND','data':None}:
                    return WesResult(**args.model_dump(),status='NOT_FOUND',snapshot=None,source='STONE_HTTP',observed_at=observed()).model_dump()
            raise DomainReadError('WES_HTTP_'+str(exc.code)) from exc
        except (OSError,ValueError,UnicodeError) as exc: raise DomainReadError('WES_TRANSPORT_OR_JSON_ERROR') from exc
        if not isinstance(body,dict) or body.get('success') is not True: raise DomainReadError('WES_BUSINESS_ERROR')
        try:
            return WesResult(**args.model_dump(),status='FOUND',snapshot=body['data'],source='STONE_HTTP',observed_at=observed()).model_dump()
        except (KeyError,TypeError,ValueError) as exc: raise DomainReadError('WES_CONTRACT_ERROR') from exc

    def get_outbound(self,principal,args):
        authorize(principal,args)
        if not self.token: raise DomainReadError('OUTBOUND_SERVICE_TOKEN_REQUIRED')
        url=f'{self.base}/api/wms/warehouses/{args.warehouse_id}/outbound-orders/{args.order_id}/allocation-diagnostic'
        try:
            request=urllib.request.Request(url,headers={'Accept':'application/json','Authorization':'Bearer '+self.token},method='GET')
            with self.opener.open(request,timeout=self.timeout) as response:
                raw=response.read(1_000_001)
            if len(raw)>1_000_000: raise DomainReadError('UPSTREAM_RESPONSE_TOO_LARGE')
            body=strict_json(raw.decode('utf-8'))
        except urllib.error.HTTPError as exc:
            if exc.code==404:
                try: body=strict_json(exc.read(16385).decode('utf-8'))
                except (ValueError,UnicodeError): body=None
                if body=={'success':False,'message':'ORDER_NOT_FOUND','data':None}:
                    return OutboundResult(**args.model_dump(),status='NOT_FOUND',snapshot=None,source='STONE_HTTP',observed_at=observed()).model_dump()
            raise DomainReadError('OUTBOUND_HTTP_'+str(exc.code)) from exc
        except (OSError,ValueError,UnicodeError) as exc: raise DomainReadError('OUTBOUND_TRANSPORT_OR_JSON_ERROR') from exc
        if not isinstance(body,dict) or body.get('success') is not True: raise DomainReadError('OUTBOUND_BUSINESS_ERROR')
        try:
            return OutboundResult(**args.model_dump(),status='FOUND',snapshot=body['data'],source='STONE_HTTP',observed_at=observed()).model_dump()
        except (KeyError,TypeError,ValueError) as exc: raise DomainReadError('OUTBOUND_CONTRACT_ERROR') from exc

    def get_events(self,principal,args):
        authorize(principal,args)
        url=f'{self.base}/api/wes/warehouses/{args.warehouse_id}/tasks/{args.task_id}/events'
        headers={'Accept':'application/json'}
        if self.token: headers['Authorization']='Bearer '+self.token
        try:
            with self.opener.open(urllib.request.Request(url,headers=headers,method='GET'),timeout=self.timeout) as response:
                raw=response.read(1_000_001)
            if len(raw)>1_000_000: raise DomainReadError('UPSTREAM_RESPONSE_TOO_LARGE')
            body=strict_json(raw.decode('utf-8'))
        except urllib.error.HTTPError as exc: raise DomainReadError('EVENT_HISTORY_HTTP_'+str(exc.code)) from exc
        except (OSError,ValueError,UnicodeError) as exc: raise DomainReadError('EVENT_HISTORY_TRANSPORT_OR_JSON_ERROR') from exc
        if not isinstance(body,dict) or body.get('success') is not True: raise DomainReadError('EVENT_HISTORY_BUSINESS_ERROR')
        try:
            data=body['data']
            if set(data)!={'warehouseId','taskId','taskState','events','truncated'}: raise ValueError('unexpected DTO')
            result=DispatchHistory(warehouse_id=data['warehouseId'],task_id=data['taskId'],task_state=data['taskState'],events=data['events'],truncated=data['truncated'],source='STONE_HTTP',observed_at=observed())
            if (result.warehouse_id,result.task_id)!=(args.warehouse_id,args.task_id): raise ValueError('entity mismatch')
            return result.model_dump()
        except (KeyError,TypeError,ValueError) as exc: raise DomainReadError('EVENT_HISTORY_CONTRACT_ERROR') from exc

class FixtureStoneService:
    def get_wes_dispatch(self,principal,args):
        authorize(principal,args); self.calls+=1
        return wes_fixture(args,observed()).model_dump()
    def get_outbound(self,principal,args):
        authorize(principal,args); self.calls+=1
        return outbound_fixture(args,observed()).model_dump()
    revision='stone-fixture-v1'
    def __init__(self): self.calls=0
    def get_detail(self,principal,args):
        authorize(principal,args); self.calls+=1
        states={101:'READY',102:'IN_FLIGHT',103:'UNKNOWN',104:'SUCCEEDED',105:'FAILED',106:'CANCELLED'}
        state=states.get(args.task_id) if args.warehouse_id==1 else None
        if not state: return StockWorkResult(**args.model_dump(),status='NOT_FOUND',source='SYNTHETIC_FIXTURE',observed_at=observed()).model_dump()
        data={'task':{'id':args.task_id,'taskKey':f'DEMO:{args.task_id}','priority':10,'state':state,'work':{'type':'TRANSFER','skuId':8,'lotNo':'L01','sourceId':10,'destinationId':20,'quantity':12}},'ledger':[]}
        return project(args,data,'SYNTHETIC_FIXTURE')

    def get_events(self,principal,args):
        authorize(principal,args)
        states={101:'READY',102:'IN_FLIGHT',103:'UNKNOWN',104:'SUCCEEDED',105:'FAILED',106:'CANCELLED'}
        state=states.get(args.task_id) if args.warehouse_id==1 else None
        if not state: raise DomainReadError('EVENT_HISTORY_HTTP_404')
        events=[]
        if state in ('UNKNOWN','FAILED','SUCCEEDED'):
            events=[dict(eventKey='DEMO:1',targetState=state,reconciled=False,reason='模拟设备上报：等待回执超时' if state=='UNKNOWN' else ('模拟零作业失败' if state=='FAILED' else None),completedQty=4 if state=='UNKNOWN' else (0 if state=='FAILED' else 12),createdAt='2026-01-01T00:00:00.000000')]
        return DispatchHistory(**args.model_dump(),task_state=state,events=events,truncated=False,source='SYNTHETIC_FIXTURE',observed_at=observed()).model_dump()

def make_registry(service):
    specs=[ToolSpec('get_stock_work_detail','stone.wms.stock_work.detail',1,service.revision,service.get_detail,'读取指定仓库及库存作业ID的任务状态、作业载荷和库存流水，不修改数据。'), ToolSpec('diagnose_stock_work','stone.agent.stock_work.diagnosis',1,'diagnosis-rules-v1',diagnose,'基于本任务已验证的库存作业证据解释状态及缺失证据；不推断实际根因，不访问外部系统。'), ToolSpec('summarize_stock_work_ledger','stone.agent.stock_work.ledger_summary',1,'ledger-summary-v1',summarize_ledger,'基于本任务已验证的库存作业流水，按库位汇总在库、可用、预占及冻结变化；不是当前库存余额，不访问外部系统。')]
    if hasattr(service,'get_events'):
        specs.append(ToolSpec('get_stock_work_events','stone.wes.stock_work.events',1,service.revision+':events-v1',service.get_events,'只读查询指定仓库库存作业的历史设备回执及核对记录。'))
    if hasattr(service,'get_outbound'):
        specs.append(ToolSpec('get_outbound_allocation_snapshot','stone.wms.outbound.allocation_snapshot',1,service.revision+':outbound-v1',service.get_outbound,'只读出库单及指定库存维度、库位冻结和分配回执存在状态。',OutboundArgs))
    if hasattr(service,'get_wes_dispatch'):
        specs.append(ToolSpec('get_wes_dispatch_snapshot','stone.wes.dispatch.snapshot',1,service.revision+':wes-dispatch-v1',service.get_wes_dispatch,'只读查询指定仓库及设备的调度容量和任务数量，不领取任务。',WesArgs))
    return Registry(specs)

def explain(result):
    if result.status=='NOT_FOUND': return '在当前授权仓库范围内未查到该库存作业。'
    desc={'READY':'待领取；不表示已开始实物作业。','IN_FLIGHT':'已领取、正在执行或等待回执；不表示已完成。','UNKNOWN':'作业结果未知。按 Stone 规则保留预占，需现场核对；不自动重派或提交反馈。','SUCCEEDED':'业务任务状态为成功；流水仅展示接口实际返回的记录。','FAILED':'业务任务状态为失败；不能从状态推断具体故障原因。','CANCELLED':'业务任务状态为已取消；以接口返回的实际流水为准。'}
    return desc[result.task.state]


def diagnose(principal,args,evidence):
    """LOCAL read-only rule interpretation, not a second Stone endpoint."""
    authorize(principal,args)
    result=StockWorkResult.model_validate(evidence)
    if (result.warehouse_id,result.task_id)!=(args.warehouse_id,args.task_id):
        raise DomainReadError('EVIDENCE_ENTITY_MISMATCH')
    missing=['设备执行回执及现场核对记录','相关库存余额和库位状态'] if result.status=='FOUND' else ['有效库存作业详情']
    return StockWorkDiagnosis(**args.model_dump(),source_evidence_hash=digest(evidence),status=result.status,
        task_state=result.task.state if result.task else None,interpretation=explain(result),
        ledger_entries=len(result.ledger),missing_evidence=missing,source=result.source).model_dump()


def summarize_ledger(principal,args,evidence):
    authorize(principal,args)
    result=StockWorkResult.model_validate(evidence)
    if (result.warehouse_id,result.task_id)!=(args.warehouse_id,args.task_id):
        raise DomainReadError('EVIDENCE_ENTITY_MISMATCH')
    by_location={}
    fields=('deltaOnHand','deltaAvailable','deltaReserved','deltaFrozen')
    for item in result.ledger:
        acc=by_location.setdefault(item.locationId,{'locationId':item.locationId,**{k:0 for k in fields},'entries':0})
        for k in fields: acc[k]+=getattr(item,k)
        acc['entries']+=1
    return StockWorkLedgerSummary(**args.model_dump(),source_evidence_hash=digest(evidence),status=result.status,
        ledger_entries=len(result.ledger),movements=[by_location[k] for k in sorted(by_location)],source=result.source).model_dump()
