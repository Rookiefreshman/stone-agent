"""Stone-specific goals and deterministic analysis. No provider or storage dependency."""
from ..contracts import StockWorkResult, StockWorkDiagnosis, StockWorkLedgerSummary, DispatchHistory, digest
from ..tools import explain, diagnose, summarize_ledger
from .outbound import OutboundResult, analyze as analyze_outbound
from .wes import WesResult, analyze as analyze_wes
WES='get_wes_dispatch_snapshot'
OUTBOUND='get_outbound_allocation_snapshot'

READ='get_stock_work_detail'
EVENTS='get_stock_work_events'
DIAG='diagnose_stock_work'
LEDGER='summarize_stock_work_ledger'
LOCAL={DIAG:(StockWorkDiagnosis,diagnose),LEDGER:(StockWorkLedgerSummary,summarize_ledger)}

class StockWorkPolicy:
    @staticmethod
    def entities(goal):
        return goal.entities if goal.kind=='COMPARE_STOCK_WORK' else ([goal.entity] if goal.entity else [])

    @staticmethod
    def plan(goal, rule_mode=False):
        if goal.kind=='DIAGNOSE_WES_DISPATCH': return [({WES},goal.entity)]
        if goal.kind=='DIAGNOSE_OUTBOUND_ALLOCATION': return [({OUTBOUND},goal.entity)]
        if goal.kind=='DEEP_DIAGNOSE_STOCK_WORK': return [({READ},goal.entity),({EVENTS},goal.entity)]
        if goal.kind=='COMPARE_STOCK_WORK': return [({READ},a) for a in goal.entities]
        if goal.kind=='DIAGNOSE_STOCK_WORK': return [({READ},goal.entity),({DIAG},goal.entity)]
        if goal.kind=='ANALYZE_STOCK_WORK':
            return [({READ},goal.entity),({DIAG},goal.entity),({LEDGER},goal.entity)] if rule_mode else [({READ},goal.entity),({DIAG,LEDGER},goal.entity),({DIAG,LEDGER},goal.entity)]
        return [({READ},goal.entity)]

    @staticmethod
    def validate_result(p,args,alias,raw,prior):
        if alias==WES:
            result=WesResult.model_validate(raw)
            if (result.warehouse_id,result.device_code,result.requested_limit)!=(args.warehouse_id,args.device_code,args.requested_limit): raise ValueError('dispatch entity mismatch')
            return result.model_dump()
        if alias==OUTBOUND:
            result=OutboundResult.model_validate(raw)
            if (result.warehouse_id,result.order_id)!=(args.warehouse_id,args.order_id): raise ValueError('entity mismatch')
            return result.model_dump()
        result=(LOCAL[alias][0] if alias in LOCAL else DispatchHistory if alias==EVENTS else StockWorkResult).model_validate(raw)
        if (result.warehouse_id,result.task_id)!=(args.warehouse_id,args.task_id): raise ValueError('entity mismatch')
        if alias in LOCAL:
            expected=LOCAL[alias][1](p,args,prior)
            if result.model_dump()!=expected: raise ValueError('diagnosis evidence mismatch')
        return result.model_dump()

    @staticmethod
    def next_checks(raw):
        """Known business rules, not claims about live hardware or inventory balance."""
        r=StockWorkResult.model_validate(raw)
        if r.status=='NOT_FOUND': return {'observed_state':None,'business_rule_effects':[], 'next_checks':['确认仓库和作业编号；当前授权范围内未查询到作业。'],'missing_evidence':['有效库存作业详情'],'root_cause':'UNDETERMINED','source_evidence_hash':digest(raw),'source':r.source}
        state=r.task.state
        effects={
            'READY':['尚未领取；待移入容量按 Stone 规则保留。','移库同时预占源库存；不能由状态推断当前余额。'],
            'IN_FLIGHT':['已领取；占用仓和设备在途容量。','不能确认实物作业已经完成。'],
            'UNKNOWN':['继续保留仓与设备在途槽及目标库容。','移库按规则保留源库存预占。','禁止自动重新派发；须现场核对后结案。'],
            'SUCCEEDED':['成功状态表示领域作业已结算；接口流水只反映该作业。'],
            'FAILED':['按规则须确认零作业后失败结案；不推断具体故障。'],
            'CANCELLED':['仅未下发 READY 库存作业允许取消；按规则释放预占。'],
        }[state]
        if r.task.work.type!='TRANSFER': effects=[x for x in effects if not x.startswith('移库')]
        checks={
            'READY':['检查仓和设备剩余容量、待派队列及任务优先级。'],
            'IN_FLIGHT':['检查最近执行回执、设备状态与现场执行进度。'],
            'UNKNOWN':['核对源位与目标位实物、设备执行回执；不得自动认定成功或失败。','如有部分作业，须按 Stone 核对规则处理至全量完成或恢复原状。'],
            'SUCCEEDED':['如需核对实际库存，另查相关库存余额及库位状态。'],
            'FAILED':['查历史失败回执及核对原因，重新计划须由业务人员决定。'],
            'CANCELLED':['如需确认释放结果，查询库存余额及库位容量。'],
        }[state]
        return {'observed_state':state,'business_rule_effects':effects,'next_checks':checks,'missing_evidence':['设备执行回执及现场核对记录','相关库存余额和库位状态'],'root_cause':'UNDETERMINED','source_evidence_hash':digest(raw),'source':r.source}

    @staticmethod
    def should_stop(goal,completed):
        return goal.kind=='DEEP_DIAGNOSE_STOCK_WORK' and len(completed)==1 and completed[0]['facts']['status']=='NOT_FOUND'

    @classmethod
    def deep_outcome(cls,completed):
        detail=completed[0]['facts']; triage=cls.next_checks(detail)
        if detail['status']=='NOT_FOUND':
            return dict(goal_status='PARTIAL',completed_steps=completed,triage=triage,unresolved_items=['作业不存在或无法定位；未请求历史回执'],explanation='未取得有效作业详情。')
        history=DispatchHistory.model_validate(completed[1]['facts'])
        same=history.task_state==detail['task']['state'] and history.source==detail['source']
        events=[e.model_dump() for e in history.events]
        missing=['相关库存余额和库位状态','独立现场实物核对证据']
        if not events: missing.append('设备历史回执未记录；不能推断设备未执行')
        if history.truncated: missing.append('历史回执超出查询上限')
        if not same: missing.append('两次查询状态或来源不一致；需重新查询确认')
        terminal={'SUCCEEDED','FAILED'}
        coherent=(not events and history.task_state in ('READY','IN_FLIGHT','CANCELLED')) or (bool(events) and history.task_state in ('UNKNOWN','SUCCEEDED','FAILED') and any(e.targetState==history.task_state for e in history.events) and not any(e.targetState in terminal and e.targetState!=history.task_state for e in history.events))
        if not coherent: missing.append('回执与作业状态存在缺失或冲突；需核对原始业务记录')
        triage['missing_evidence']=missing
        return dict(goal_status='SATISFIED' if same and events and coherent and not history.truncated else 'PARTIAL',completed_steps=completed,
            triage=triage,history_analysis={'detail_state':detail['task']['state'],'history_state':history.task_state,'state_agrees':same,'history_state_consistent':coherent,'same_snapshot':False,
            'reported_events':events,'has_reconciliation_report':any(e.reconciled for e in history.events),'truncated':history.truncated,
            'source_evidence_hashes':[x['evidence_hash'] for x in completed], 'root_cause':'UNDETERMINED'},
            missing_evidence=missing,explanation='回执原因是设备或人工上报的记录，不是独立证实的根因；回执与详情为两次独立查询。时间为 Stone 数据库记录的本地时间，未声明时区；相同时间按回执键稳定排序，不代表实际因果先后。')

    @classmethod
    def outcome(cls,goal,completed,rule_mode=False):
        if goal.kind=='DIAGNOSE_WES_DISPATCH':
            raw=completed[0]['facts']; result=WesResult.model_validate(raw)
            return dict(goal_status='SATISFIED' if result.status=='FOUND' else 'PARTIAL',completed_steps=completed,diagnosis=analyze_wes(result),evidence_hash=digest(raw))
        if goal.kind=='DIAGNOSE_OUTBOUND_ALLOCATION':
            raw=completed[0]['facts']; result=OutboundResult.model_validate(raw)
            return dict(goal_status='SATISFIED' if result.status=='FOUND' else 'PARTIAL',completed_steps=completed,diagnosis=analyze_outbound(result),evidence_hash=digest(raw))
        if goal.kind=='DEEP_DIAGNOSE_STOCK_WORK': return cls.deep_outcome(completed)
        if goal.kind=='READ_STOCK_WORK':
            raw=completed[0]['facts']; result=StockWorkResult.model_validate(raw)
            return dict(goal_status='SATISFIED',facts=raw,explanation=explain(result),evidence_hash=digest(raw))
        if goal.kind=='COMPARE_STOCK_WORK':
            comparison=[{'warehouse_id':x['facts']['warehouse_id'],'task_id':x['facts']['task_id'],'status':x['facts']['status'],'task_state':x['facts']['task']['state'] if x['facts']['task'] else None,'observed_at':x['facts']['observed_at'],'source':x['facts']['source']} for x in completed]
            return dict(goal_status='SATISFIED',completed_steps=completed,comparison=comparison,explanation='逐项展示授权查询结果及观测时间；不同查询不是同一事务快照，不推断全仓库存。')
        diagnosis=StockWorkDiagnosis.model_validate(next(x['facts'] for x in completed if x['tool']==DIAG))
        extras={'ledger_summary':next(x['facts'] for x in completed if x['tool']==LEDGER)} if goal.kind=='ANALYZE_STOCK_WORK' else {}
        if rule_mode: extras['triage']=cls.next_checks(completed[0]['facts'])
        return dict(goal_status='SATISFIED' if diagnosis.status=='FOUND' else 'PARTIAL',completed_steps=completed,diagnosis=diagnosis.model_dump(),missing_evidence=diagnosis.missing_evidence,unresolved_items=[] if diagnosis.status=='FOUND' else ['有效库存作业详情缺失，无法解释实际作业'],explanation=diagnosis.interpretation+' 根因尚未确定。流水汇总（如有）仅代表该作业变动，不是当前库存余额。',**extras)
