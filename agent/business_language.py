"""Typed natural-language proposal for existing read-only diagnostic goals.
Fixture grammar is deliberately bounded. Model output is a proposal, not authority.
"""
import re
from .contracts import StockWorkArgs, OutboundArgs, WesArgs
from .tools import authorize

DEEP='DEEP_DIAGNOSE_STOCK_WORK'
OUT='DIAGNOSE_OUTBOUND_ALLOCATION'
WES='DIAGNOSE_WES_DISPATCH'
EXTENDED={DEEP:StockWorkArgs,OUT:OutboundArgs,WES:WesArgs}
FIELDS={DEEP:('warehouse_id','task_id'),OUT:('warehouse_id','order_id'),WES:('warehouse_id','device_code','requested_limit')}
LABELS={DEEP:'查询库存作业详情及历史设备回执，分析证据（不确定实际根因）',OUT:'检查出库分配的当前库存、库位及回执证据（不执行分配）',WES:'按指定请求数量分析当前仓库和设备的领取容量（不实际领取）'}

# Explicit field roles, not bare number matching. No guessed defaults.
W=r'(?:仓库\s*(?P<w1>[0-9]{1,19})(?![0-9])\s*号?|(?<![0-9])(?P<w2>[0-9]{1,19})(?![0-9])\s*号仓库)'
T=r'(?:库存作业|库存任务|任务)\s*(?P<task_id>[0-9]{1,19})(?![0-9])\s*号?'
O=r'(?:出库订单|出库单|订单)\s*(?P<order_id>[0-9]{1,19})(?![0-9])\s*号?'
D=r'设备(?:编号|编码)?\s*(?P<device_code>[A-Za-z0-9._:/-]{1,64})(?![A-Za-z0-9._:/-])'
L=r'(?:请求|计划领取|拟领取)\s*(?P<requested_limit>[0-9]{1,3})(?![0-9])\s*个?'
PREFIX=r'(?:请帮我|帮我|请)?\s*'
END=r'\s*[。？?]?'
GAP=r'\s*(?:的|中)?\s*'
GRAMMARS=[
    (DEEP, PREFIX+r'(?:诊断|分析|查询|查看)\s*'+W+GAP+T+GAP+r'(?:历史回执|设备回执|历史设备回执|异常回执)'+END),
    (DEEP, PREFIX+r'(?:诊断|分析|查询|查看)\s*'+T+GAP+r'(?:历史回执|设备回执|历史设备回执|异常回执)'+END),
    (OUT, PREFIX+r'(?:诊断|分析|查看)\s*'+W+GAP+O+GAP+r'(?:分配失败|分配异常|分配诊断|分配阻塞|无法分配原因)'+END),
    (OUT, PREFIX+r'(?:诊断|分析|查看)\s*'+O+GAP+r'(?:分配失败|分配异常|分配诊断|分配阻塞|无法分配原因)'+END),
    (OUT, PREFIX+W+GAP+O+GAP+r'为什么(?:无法|不能)分配'+END),
    (WES, PREFIX+r'(?:诊断|分析|查看)\s*'+W+GAP+D+GAP+L+r'\s*(?:任务)?'+GAP+r'(?:调度容量|领取容量|领取上限)'+END),
    (WES, PREFIX+r'(?:诊断|分析|查看)\s*'+W+GAP+D+GAP+r'(?:调度容量|领取容量|领取上限)'+END),
    (WES, PREFIX+r'(?:诊断|分析|查看)\s*'+D+GAP+r'(?:调度容量|领取容量|领取上限)'+END),
    (WES, PREFIX+r'(?:诊断|分析|查看)\s*'+W+GAP+r'(?:调度容量|领取容量|领取上限)'+END),
]
GRAMMARS=[(k,re.compile(g)) for k,g in GRAMMARS]
ROLE_PATTERNS={'warehouse_id':re.compile(W),'task_id':re.compile(T),'order_id':re.compile(O),'device_code':re.compile(D),'requested_limit':re.compile(L)}

def fields_from_match(m):
    d=m.groupdict()
    out={}
    w=d.get('w1') or d.get('w2')
    if w is not None: out['warehouse_id']=int(w)
    for k in ('task_id','order_id','requested_limit','device_code'):
        if d.get(k) is not None: out[k]=d[k] if k=='device_code' else int(d[k])
    return out

def fixture_extended(message):
    for kind,grammar in GRAMMARS:
        m=grammar.fullmatch(message.strip())
        if m: return kind,fields_from_match(m)
    return None

def explicit_roles(message):
    result={}
    for field,pattern in ROLE_PATTERNS.items():
        matches=list(pattern.finditer(message))
        # More than one mention is ambiguous even if the value is the same.
        if len(matches)>1:
            raise ValueError('ambiguous field role')
        if len(matches)==1:
            result.update(fields_from_match(matches[0]))
    return result

def result_for(kind,fields,principal,source):
    expected=FIELDS[kind]
    data={k:fields.get(k) for k in expected}
    if data['warehouse_id'] is not None and data['warehouse_id'] not in principal.warehouses:
        from .contracts import Forbidden
        raise Forbidden('FORBIDDEN')
    missing=[k for k,v in data.items() if v is None]
    # Validate even partial parameters, using safe placeholders only for validation;
    # placeholders are never returned or confirmed as actual data.
    defaults={'warehouse_id':1,'task_id':1,'order_id':1,'device_code':'validation-only','requested_limit':1}
    try:
        EXTENDED[kind].model_validate({k:v if v is not None else defaults[k] for k,v in data.items()})
    except ValueError:
        return {'status':'UNSUPPORTED','source':source,'candidate':None,'execution_allowed':False,'message':'业务参数不合法，请重新明确只读目标。'}
    if missing:
        return {'status':'NEEDS_INPUT','source':source,'candidate':data,'goal_kind':kind,'missing_fields':missing,'execution_allowed':False,'message':'请补充：'+', '.join(missing)+'；不会自动猜测参数。'}
    entity=EXTENDED[kind].model_validate(data)
    authorize(principal,entity)
    return {'status':'NEEDS_CONFIRMATION','source':source,'candidate':entity.model_dump(),'goal_kind':kind,'execution_allowed':False,'message':'请确认仅执行：'+LABELS[kind]+'；原始文本中其他诉求不执行。'}

def extended_followup(message,principal,confirmed,pending):
    # Only same-domain, full-matched follow-ups; never inherit an unconfirmed
    # different goal, a new warehouse, or fields from model prose.
    base=pending or confirmed
    if not base or base.get('goal_kind') not in EXTENDED: return None
    kind=base['goal_kind']; candidate=base['candidate']
    m=None
    if pending:
        for field in FIELDS[kind]:
            if candidate.get(field) is None:
                match=ROLE_PATTERNS[field].fullmatch(message.strip().rstrip('。？?').strip())
                if match:
                    m=match
                    break
    else:
        # New entity explicitly named; same warehouse, same goal, re-confirm.
        patterns={DEEP:T,OUT:O,WES:D}
        m=re.fullmatch(r'(?:那|再查|再看|再诊断)\s*'+patterns[kind]+r'\s*(?:呢)?'+END,message.strip())
    if not m: return None
    data=dict(candidate,**fields_from_match(m))
    return result_for(kind,data,principal,'PENDING_CLARIFICATION' if pending else 'CONFIRMED_CONTEXT')
