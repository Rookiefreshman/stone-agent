import json
import pytest
from agent.business_language import DEEP,OUT,WES
from agent.goal_proposal import propose
from agent.contracts import Principal,StockWorkArgs,OutboundArgs,WesArgs,Conflict,Forbidden
from agent.session import SessionService
from agent.store import Store,RULES
from agent.runtime import Runtime
from agent.provider import FixtureProvider
from agent.tools import FixtureStoneService,make_registry
from test_goal_proposal import Provider,turn

P=Principal('demo','u',frozenset({1}))
CASES=[
 ('诊断仓库1任务103历史回执',DEEP,StockWorkArgs(warehouse_id=1,task_id=103)),
 ('分析1号仓库的出库单201分配失败',OUT,OutboundArgs(warehouse_id=1,order_id=201)),
 ('仓库1出库订单201为什么无法分配？',OUT,OutboundArgs(warehouse_id=1,order_id=201)),
 ('诊断仓库1设备station-01请求8个任务的调度容量',WES,WesArgs(warehouse_id=1,device_code='station-01',requested_limit=8)),
]
@pytest.mark.parametrize('message,kind,entity',CASES)
def test_typed_goal_and_run(tmp_path,message,kind,entity):
    s=Store(tmp_path/'db.sqlite',RULES); svc=SessionService(s); sid=svc.create(P)['session_id']
    domain=FixtureStoneService(); model=FixtureProvider(); runtime=Runtime(s,model,make_registry(domain))
    r=svc.propose(P,sid,'a',message)
    assert r['status']=='NEEDS_CONFIRMATION' and r['goal_kind']==kind and r['candidate']==entity.model_dump()
    assert domain.calls==0
    with s.tx() as c: assert c.execute('SELECT COUNT(*) FROM run').fetchone()[0]==0
    rid=svc.confirm(P,sid,r['proposal_id'],entity,kind)['run_id']
    assert runtime.advance(P,rid)['state']=='COMPLETED'
    audit=s.inspect(P,rid)
    assert not audit['attempts'] and model.calls==0
    assert len(audit['invocations'])==(2 if kind==DEEP else 1)
    assert all(i['origin']=='RULE' and i['state']=='SUCCEEDED' for i in audit['invocations'])

@pytest.mark.parametrize('message',[
 '分析仓库1出库单201分配失败然后重新分配',
 '诊断仓库1任务103历史回执后取消',
 '诊断仓库1设备station-01请求8个任务的领取容量并领取',
 '分析仓库1出库单201和202分配失败',
 '分析仓库1订单201分配失败，如果不足就调拨',
 '诊断仓库1设备station-01请求101个任务的调度容量',
])
def test_fixture_does_not_drop_extra_intents(message):
    assert propose(message,P)['status']=='UNSUPPORTED'

def test_typed_clarification_keeps_intent(tmp_path):
    s=Store(tmp_path/'db.sqlite',RULES); svc=SessionService(s); sid=svc.create(P)['session_id']
    a=svc.propose(P,sid,'a','诊断设备station-01调度容量')
    assert a['goal_kind']==WES and set(a['missing_fields'])=={'warehouse_id','requested_limit'}
    b=svc.propose(P,sid,'b','仓库1')
    assert b['goal_kind']==WES and b['missing_fields']==['requested_limit']
    c=svc.propose(P,sid,'c','请求8个')
    assert c['goal_kind']==WES and c['candidate']==dict(warehouse_id=1,device_code='station-01',requested_limit=8)
    with pytest.raises(Conflict): svc.confirm(P,sid,c['proposal_id'],WesArgs(**c['candidate']),'READ_STOCK_WORK')

def test_followup_same_domain_and_no_cross_domain(tmp_path):
    s=Store(tmp_path/'db.sqlite',RULES); svc=SessionService(s); sid=svc.create(P)['session_id']
    message,kind,entity=CASES[1]; r=svc.propose(P,sid,'a',message)
    rid=svc.confirm(P,sid,r['proposal_id'],entity,kind)['run_id']
    Runtime(s,FixtureProvider(),make_registry(FixtureStoneService())).advance(P,rid)
    reopened=SessionService(Store(s.path,RULES))
    r=reopened.propose(P,sid,'b','那出库单202呢？')
    assert r['source']=='CONFIRMED_CONTEXT' and r['goal_kind']==OUT and r['candidate']==dict(warehouse_id=1,order_id=202)
    r=reopened.propose(P,sid,'c','那任务103呢')
    assert r['status']=='NEEDS_INPUT' and r['candidate']['warehouse_id'] is None

def test_revoke_and_wrong_entity_confirmation(tmp_path):
    s=Store(tmp_path/'db.sqlite',RULES); svc=SessionService(s); sid=svc.create(P)['session_id']
    r=svc.propose(P,sid,'a',CASES[1][0])
    with pytest.raises(Conflict): svc.confirm(P,sid,r['proposal_id'],StockWorkArgs(warehouse_id=1,task_id=201),OUT)
    with pytest.raises(Forbidden): svc.confirm(Principal('demo','u',frozenset({2})),sid,r['proposal_id'],CASES[1][2],OUT)
    with s.tx() as c: assert c.execute('SELECT COUNT(*) FROM run').fetchone()[0]==0

@pytest.mark.parametrize('message,kind,entity',CASES)
def test_model_typed_proposal(message,kind,entity):
    provider=Provider(turn(json.dumps(dict(kind=kind,**entity.model_dump()))))
    r=propose(message,P,provider)
    assert r['status']=='NEEDS_CONFIRMATION' and r['candidate']==entity.model_dump() and r['source']=='MODEL_PROPOSED'
    assert not r['execution_allowed']

def test_model_role_mismatch_not_confirmation():
    p=Provider(turn('{"kind":"DIAGNOSE_OUTBOUND_ALLOCATION","warehouse_id":1,"order_id":1}'))
    r=propose('分析仓库1出库单201分配失败',P,p)
    assert r['status']=='NEEDS_INPUT' and r['candidate']['order_id'] is None
    p=Provider(turn('{"kind":"DIAGNOSE_WES_DISPATCH","warehouse_id":1,"device_code":"station-99","requested_limit":8}'))
    r=propose(CASES[-1][0],P,p)
    assert r['status']=='NEEDS_INPUT' and r['candidate']['device_code'] is None

def test_model_mixed_fields_rejected():
    p=Provider(turn('{"kind":"DIAGNOSE_OUTBOUND_ALLOCATION","warehouse_id":1,"order_id":201,"task_id":103}'))
    assert propose(CASES[1][0],P,p)['status']=='PROPOSAL_FAILED'

def test_api_typed_confirmation(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    monkeypatch.setenv('POC_DEV_TOKEN','test-token-1234567890')
    monkeypatch.setenv('POC_DB',str(tmp_path/'api.sqlite'))
    monkeypatch.delenv('POC_LIVE_MODEL',raising=False)
    monkeypatch.delenv('STONE_LIVE_DOMAIN',raising=False)
    c=TestClient(create_app()); h={'Authorization':'Bearer test-token-1234567890'}
    sid=c.post('/api/v1/sessions',headers=h).json()['session_id']; url=f'/api/v1/sessions/{sid}/goal-proposals'
    r=c.post(url,headers=h,json={'client_request_id':'a','message':CASES[1][0]}).json()
    res=c.post(url+'/'+r['proposal_id']+'/confirm',headers=h,json={'entity':r['candidate'],'kind':r['goal_kind']})
    assert res.status_code==202
    assert c.post('/api/v1/runs/'+res.json()['run_id']+'/advance',headers=h).json()['state']=='COMPLETED'

def test_model_cannot_downgrade_or_drop_compound():
    model=Provider(turn('{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":201}'))
    assert propose(CASES[1][0],P,model)['status']=='UNSUPPORTED'
    model=Provider(turn('{"kind":"DIAGNOSE_OUTBOUND_ALLOCATION","warehouse_id":1,"order_id":201}'))
    assert propose('分析仓库1出库单201分配失败然后重新分配',P,model)['status']=='UNSUPPORTED'
    assert propose('分析仓库1出库单201及出库单202分配失败',P,model)['status']=='UNSUPPORTED'
