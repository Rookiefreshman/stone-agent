"""Untrusted goal proposal only. No business tool, run, or write is invoked here."""
import re
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .contracts import StockWorkArgs, strict_json, ModelAttemptError, Forbidden
from .tools import authorize
from .business_language import EXTENDED, FIELDS, fixture_extended, explicit_roles, result_for

ALIAS = 'propose_stock_work_goal'
MAX_ID = 9223372036854775807

class Candidate(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['READ_STOCK_WORK', 'DIAGNOSE_STOCK_WORK', 'ANALYZE_STOCK_WORK', 'DEEP_DIAGNOSE_STOCK_WORK', 'DIAGNOSE_OUTBOUND_ALLOCATION', 'DIAGNOSE_WES_DISPATCH', 'CLARIFY', 'UNSUPPORTED']
    warehouse_id: int | None = Field(default=None, ge=1, le=MAX_ID)
    task_id: int | None = Field(default=None, ge=1, le=MAX_ID)
    order_id: int | None = Field(default=None, ge=1, le=MAX_ID)
    device_code: str | None = Field(default=None, pattern=r'^[A-Za-z0-9._:/-]{1,64}
    def complete(self):
        if self.kind in ('READ_STOCK_WORK','DIAGNOSE_STOCK_WORK','ANALYZE_STOCK_WORK') and (self.warehouse_id is None or self.task_id is None):
            raise ValueError('read proposal requires both IDs')
        allowed=set(FIELDS.get(self.kind,('warehouse_id','task_id')))
        if any(v is not None and k not in allowed for k,v in self.model_dump(exclude={'kind'}).items()):
            raise ValueError('mixed entity types')
        return self

# Fixture deliberately supports a small grammar, not arbitrary natural language.
# Full match prevents silently dropping extra actions or conditions.
GRAMMAR = re.compile(r'(?:请|帮我|请帮我)?\s*(?:查询|查看)\s*(?:(?:仓库\s*(?P<w1>[0-9]{1,19})|(?P<w2>[0-9]{1,19})\s*号仓库)\s*(?:中|的)?\s*)?(?:(?:库存作业|库存任务|任务)\s*(?P<t1>[0-9]{1,19})\s*号?|(?P<t2>[0-9]{1,19})\s*号\s*(?:库存作业|库存任务|任务))?\s*(?:的)?\s*(?:状态(?:及|和|与)库存流水|状态(?:及|和|与)流水|状态|详情|库存流水|流水)?\s*[。？?]?')

def fixture_candidate(message):
    extended=fixture_extended(message)
    if extended:
        try: return Candidate(kind=extended[0],**extended[1])
        except ValueError: return Candidate(kind='UNSUPPORTED')
    # Complete diagnostic requests only; no implicit downgrade of conditions or writes.
    diagnostic = re.fullmatch(r'(?:请|帮我|请帮我)?\s*(诊断|分析)\s*(.+)',message.strip())
    if diagnostic:
        m=GRAMMAR.fullmatch('查询'+diagnostic[2])
        if m and (m['w1'] or m['w2']) and (m['t1'] or m['t2']):
            try:
                return Candidate(kind='DIAGNOSE_STOCK_WORK' if diagnostic[1]=='诊断' else 'ANALYZE_STOCK_WORK',warehouse_id=int(m['w1'] or m['w2']),task_id=int(m['t1'] or m['t2']))
            except ValueError: return Candidate(kind='UNSUPPORTED')
        return Candidate(kind='UNSUPPORTED')
    m = GRAMMAR.fullmatch(message.strip())
    if not m:
        return Candidate(kind='UNSUPPORTED')
    w, t = m['w1'] or m['w2'], m['t1'] or m['t2']
    try:
        return Candidate(kind='READ_STOCK_WORK' if w and t else 'CLARIFY', warehouse_id=int(w) if w else None, task_id=int(t) if t else None)
    except ValueError:
        return Candidate(kind='CLARIFY')


def model_candidate(provider, message):
    # This is a separate one-shot model operation, with no dispatcher/registry.
    # Do not send business results, actor credentials, or authorization grants.
    body = {'messages': [{'role':'system','content': 'Extract a PROPOSAL only. Supported goals: READ_STOCK_WORK (one detail), DIAGNOSE_STOCK_WORK (explain observed state and missing evidence), ANALYZE_STOCK_WORK (state and ledger movements). DEEP_DIAGNOSE_STOCK_WORK (one task, detail plus historical device receipts), DIAGNOSE_OUTBOUND_ALLOCATION (one outbound order, current blockers, no allocation), DIAGNOSE_WES_DISPATCH (one warehouse, device_code and requested_limit 1..100; capacity only, never claim). For these three goals return the same kind with absent fields null if clarification is needed. Preserve explicitly named field roles; never map order_id to task_id or infer a device or requested limit. Diagnostics cannot establish the actual root cause. Do not execute. For legacy stock work goals, if missing positive numeric warehouse_id or task_id, choose CLARIFY; do not reinterpret a diagnostic as a read. If a write, conditional action, multiple tasks, definite root-cause determination, or another goal is requested, choose UNSUPPORTED. Never invent an ID or treat taskKey as task_id. Return exactly one propose_stock_work_goal call.'}, {'role':'user','content': message}], 'tools':[{'type':'function','function':{'name':ALIAS,'description':'Non-executable candidate for user confirmation','parameters': Candidate.model_json_schema()}}], 'tool_choice':'auto','max_tokens':256}
    body['model'] = provider.model
    turn = provider.generate(body)
    if turn.finish != 'TOOL_CALLS' or len(turn.calls)!=1 or turn.calls[0].name!=ALIAS:
        raise ModelAttemptError('invalid goal proposal protocol')
    try:
        raw = turn.calls[0].arguments_json
        if len(raw.encode()) > 8192: raise ValueError('oversize')
        return Candidate.model_validate(strict_json(raw))
    except (ValueError,TypeError,AttributeError) as exc:
        raise ModelAttemptError('invalid goal proposal payload') from exc


def propose(message, principal, provider=None):
    if not isinstance(message,str) or not message.strip() or len(message)>4000:
        raise ValueError('message must contain 1–4000 characters')
    source = 'MODEL_PROPOSED' if provider is not None else 'FIXTURE_GRAMMAR'
    try:
        c = model_candidate(provider,message) if provider is not None else fixture_candidate(message)
    except ModelAttemptError:
        return {'status':'PROPOSAL_FAILED','source':source,'candidate':None,'execution_allowed':False,'message':'目标提取失败，请明确仓库 ID 和任务 ID 后重新提交。'}
    if c.kind in EXTENDED:
        roles=explicit_roles(message) if provider is not None else c.model_dump()
        # A model cannot supply an ID merely because that number appears in
        # another role. Missing/conflicting role evidence becomes clarification.
        fields={k:getattr(c,k) if getattr(c,k)==roles.get(k) else None for k in FIELDS[c.kind]}
        # Additional entity mentions of a different type cannot be silently dropped.
        if provider is not None and any(k not in FIELDS[c.kind] for k in roles):
            return {'status':'UNSUPPORTED','source':source,'candidate':None,'execution_allowed':False,'message':'存在其他业务实体，需明确单一只读目标。'}
        return result_for(c.kind,fields,principal,source)
    # Even a model-proposed ID must be explicitly present in the input. This is
    # only a lexical guard, not evidence that the ID has the right semantic role.
    mentioned = {int(x) for x in re.findall(r'(?<![0-9])[0-9]{1,19}(?![0-9])',message)}
    w = c.warehouse_id if c.warehouse_id in mentioned else None
    t = c.task_id if c.task_id in mentioned else None
    if w is not None and w not in principal.warehouses:
        raise Forbidden('FORBIDDEN')
    if c.kind == 'UNSUPPORTED':
        return {'status':'UNSUPPORTED','source':source,'candidate':None,'execution_allowed':False,'message':'当前支持单个库存作业、历史回执、出库分配和 WES 调度只读诊断；其他目标未执行。'}
    missing = [k for k,v in [('warehouse_id',w),('task_id',t)] if v is None]
    if c.kind in ('DIAGNOSE_STOCK_WORK','ANALYZE_STOCK_WORK') and missing:
        return {'status':'UNSUPPORTED','source':source,'candidate':None,'execution_allowed':False,'message':'诊断需要明确仓库和任务 ID，请完整重述诊断目标。'}
    if c.kind == 'CLARIFY' or missing:
        return {'status':'NEEDS_INPUT','source':source,'candidate':{'warehouse_id':w,'task_id':t},'missing_fields':missing,'execution_allowed':False,'message':'请明确仓库 ID、库存作业任务 ID 及只读查询目标。'}
    args=StockWorkArgs(warehouse_id=w,task_id=t)
    authorize(principal,args)
    label={'READ_STOCK_WORK':'查询状态和流水','DIAGNOSE_STOCK_WORK':'解释状态、业务影响及待核对证据（不确定实际根因）','ANALYZE_STOCK_WORK':'解释状态及汇总该作业流水变化（不是库存余额）'}[c.kind]
    return {'status':'NEEDS_CONFIRMATION','source':source,'candidate':args.model_dump(),'goal_kind':c.kind,'execution_allowed':False,'message':f'请确认仅执行：{label}；原始文本中的其他诉求不会执行或视为完成。'}
)
    requested_limit: int | None = Field(default=None, ge=1, le=100)
    @model_validator(mode='after')
    def complete(self):
        if self.kind in ('READ_STOCK_WORK','DIAGNOSE_STOCK_WORK','ANALYZE_STOCK_WORK') and (self.warehouse_id is None or self.task_id is None):
            raise ValueError('read proposal requires both IDs')
        return self

# Fixture deliberately supports a small grammar, not arbitrary natural language.
# Full match prevents silently dropping extra actions or conditions.
GRAMMAR = re.compile(r'(?:请|帮我|请帮我)?\s*(?:查询|查看)\s*(?:(?:仓库\s*(?P<w1>[0-9]{1,19})|(?P<w2>[0-9]{1,19})\s*号仓库)\s*(?:中|的)?\s*)?(?:(?:库存作业|库存任务|任务)\s*(?P<t1>[0-9]{1,19})\s*号?|(?P<t2>[0-9]{1,19})\s*号\s*(?:库存作业|库存任务|任务))?\s*(?:的)?\s*(?:状态(?:及|和|与)库存流水|状态(?:及|和|与)流水|状态|详情|库存流水|流水)?\s*[。？?]?')

def fixture_candidate(message):
    # Complete diagnostic requests only; no implicit downgrade of conditions or writes.
    diagnostic = re.fullmatch(r'(?:请|帮我|请帮我)?\s*(诊断|分析)\s*(.+)',message.strip())
    if diagnostic:
        m=GRAMMAR.fullmatch('查询'+diagnostic[2])
        if m and (m['w1'] or m['w2']) and (m['t1'] or m['t2']):
            try:
                return Candidate(kind='DIAGNOSE_STOCK_WORK' if diagnostic[1]=='诊断' else 'ANALYZE_STOCK_WORK',warehouse_id=int(m['w1'] or m['w2']),task_id=int(m['t1'] or m['t2']))
            except ValueError: return Candidate(kind='UNSUPPORTED')
        return Candidate(kind='UNSUPPORTED')
    m = GRAMMAR.fullmatch(message.strip())
    if not m:
        return Candidate(kind='UNSUPPORTED')
    w, t = m['w1'] or m['w2'], m['t1'] or m['t2']
    try:
        return Candidate(kind='READ_STOCK_WORK' if w and t else 'CLARIFY', warehouse_id=int(w) if w else None, task_id=int(t) if t else None)
    except ValueError:
        return Candidate(kind='CLARIFY')


def model_candidate(provider, message):
    # This is a separate one-shot model operation, with no dispatcher/registry.
    # Do not send business results, actor credentials, or authorization grants.
    body = {'messages': [{'role':'system','content': 'Extract a PROPOSAL only. Supported goals: READ_STOCK_WORK (one detail), DIAGNOSE_STOCK_WORK (explain observed state and missing evidence), ANALYZE_STOCK_WORK (state and ledger movements). Diagnostics cannot establish the actual root cause. Do not execute. If missing positive numeric warehouse_id or task_id, choose CLARIFY; do not reinterpret a diagnostic as a read. If a write, conditional action, multiple tasks, definite root-cause determination, or another goal is requested, choose UNSUPPORTED. Never invent an ID or treat taskKey as task_id. Return exactly one propose_stock_work_goal call.'}, {'role':'user','content': message}], 'tools':[{'type':'function','function':{'name':ALIAS,'description':'Non-executable candidate for user confirmation','parameters': Candidate.model_json_schema()}}], 'tool_choice':'auto','max_tokens':256}
    body['model'] = provider.model
    turn = provider.generate(body)
    if turn.finish != 'TOOL_CALLS' or len(turn.calls)!=1 or turn.calls[0].name!=ALIAS:
        raise ModelAttemptError('invalid goal proposal protocol')
    try:
        raw = turn.calls[0].arguments_json
        if len(raw.encode()) > 8192: raise ValueError('oversize')
        return Candidate.model_validate(strict_json(raw))
    except (ValueError,TypeError,AttributeError) as exc:
        raise ModelAttemptError('invalid goal proposal payload') from exc


def propose(message, principal, provider=None):
    if not isinstance(message,str) or not message.strip() or len(message)>4000:
        raise ValueError('message must contain 1–4000 characters')
    source = 'MODEL_PROPOSED' if provider is not None else 'FIXTURE_GRAMMAR'
    try:
        c = model_candidate(provider,message) if provider is not None else fixture_candidate(message)
    except ModelAttemptError:
        return {'status':'PROPOSAL_FAILED','source':source,'candidate':None,'execution_allowed':False,'message':'目标提取失败，请明确仓库 ID 和任务 ID 后重新提交。'}
    # Even a model-proposed ID must be explicitly present in the input. This is
    # only a lexical guard, not evidence that the ID has the right semantic role.
    mentioned = {int(x) for x in re.findall(r'(?<![0-9])[0-9]{1,19}(?![0-9])',message)}
    w = c.warehouse_id if c.warehouse_id in mentioned else None
    t = c.task_id if c.task_id in mentioned else None
    if w is not None and w not in principal.warehouses:
        raise Forbidden('FORBIDDEN')
    if c.kind == 'UNSUPPORTED':
        return {'status':'UNSUPPORTED','source':source,'candidate':None,'execution_allowed':False,'message':'当前支持单个库存作业查询、状态诊断或状态与流水分析；其他目标未执行。'}
    missing = [k for k,v in [('warehouse_id',w),('task_id',t)] if v is None]
    if c.kind in ('DIAGNOSE_STOCK_WORK','ANALYZE_STOCK_WORK') and missing:
        return {'status':'UNSUPPORTED','source':source,'candidate':None,'execution_allowed':False,'message':'诊断需要明确仓库和任务 ID，请完整重述诊断目标。'}
    if c.kind == 'CLARIFY' or missing:
        return {'status':'NEEDS_INPUT','source':source,'candidate':{'warehouse_id':w,'task_id':t},'missing_fields':missing,'execution_allowed':False,'message':'请明确仓库 ID、库存作业任务 ID 及只读查询目标。'}
    args=StockWorkArgs(warehouse_id=w,task_id=t)
    authorize(principal,args)
    label={'READ_STOCK_WORK':'查询状态和流水','DIAGNOSE_STOCK_WORK':'解释状态、业务影响及待核对证据（不确定实际根因）','ANALYZE_STOCK_WORK':'解释状态及汇总该作业流水变化（不是库存余额）'}[c.kind]
    return {'status':'NEEDS_CONFIRMATION','source':source,'candidate':args.model_dump(),'goal_kind':c.kind,'execution_allowed':False,'message':f'请确认仅执行：{label}；原始文本中的其他诉求不会执行或视为完成。'}
