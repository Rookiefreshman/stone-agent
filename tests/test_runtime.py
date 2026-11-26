import time,pytest
from agent.contracts import *
from agent.store import Store
from agent.runtime import Runtime
from agent.provider import FixtureProvider
from agent.tools import FixtureStoneService,make_registry

@pytest.fixture
def env(tmp_path):
    s=Store(tmp_path/'test.sqlite'); m=FixtureProvider(); w=FixtureStoneService(); r=Runtime(s,m,make_registry(w)); p=Principal('demo','u',frozenset({1}))
    return s,m,w,r,p

def submit(env,task=103,key='k',goal=None):
    s,m,w,r,p=env
    goal=goal or GoalContract(kind='READ_STOCK_WORK',entity=StockWorkArgs(warehouse_id=1,task_id=task),provenance='CONFIRMED_STRUCTURED_INPUT')
    return s.submit(p,'session',key,'test request',goal)

def expire(s,rid):
    with s.tx() as c: c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))

@pytest.mark.parametrize('task,expected',[(101,'READY'),(102,'IN_FLIGHT'),(103,'UNKNOWN'),(104,'SUCCEEDED'),(105,'FAILED'),(106,'CANCELLED'),(107,None)])
def test_result_states(env,task,expected):
    s,m,w,r,p=env; rid=submit(env,task); out=r.advance(p,rid)
    assert out['state']=='COMPLETED'; facts=out['outcome']['facts']
    assert facts['source']=='SYNTHETIC_FIXTURE'
    assert (facts['task']['state'] if facts['task'] else None)==expected
    assert m.calls==w.calls==1
    assert r.advance(p,rid)==out and m.calls==w.calls==1

@pytest.mark.parametrize('alias,args,finish,code',[
 ('unknown','{"warehouse_id":1,"task_id":103}','TOOL_CALLS','TOOL_NOT_AVAILABLE'),
 ('cancel_stock_work','{"warehouse_id":1,"task_id":103}','TOOL_CALLS','TOOL_NOT_AVAILABLE'),
 ('get_stock_work_detail','{"warehouse_id":1,"task_id":104}','TOOL_CALLS','GOAL_MISMATCH'),
 ('get_stock_work_detail','{"warehouse_id":1,"task_id":"103"}','TOOL_CALLS','INVALID_ARGUMENTS'),
 ('get_stock_work_detail','{"warehouse_id":1,"task_id":103,"task_id":104}','TOOL_CALLS','INVALID_ARGUMENTS'),
 ('get_stock_work_detail','{"warehouse_id":1,"task_id":103,"tenant_id":"other"}','TOOL_CALLS','INVALID_ARGUMENTS'),
 ('get_stock_work_detail','{"warehouse_id":1,"task_id":103}','INCOMPLETE','EXPECTED_ONE_READ')])
def test_reject(env,alias,args,finish,code):
    s,m,w,r,p=env; rid=submit(env)
    m.generate=lambda body:ModelTurn(finish,(ToolCall('c',alias,args),),10)
    out=r.advance(p,rid); assert out['outcome']['code']==code; assert w.calls==0; assert s.invocation(rid) is None

def test_multiple_calls(env):
    s,m,w,r,p=env; rid=submit(env); call=ToolCall('c','get_stock_work_detail','{"warehouse_id":1,"task_id":103}')
    m.generate=lambda body:ModelTurn('TOOL_CALLS',(call,call),10)
    assert r.advance(p,rid)['outcome']['code']=='EXPECTED_ONE_READ'; assert w.calls==0

def test_idempotency(env):
    assert submit(env)==submit(env)
    with pytest.raises(Conflict): submit(env,104)

def test_cross_tenant_and_revoked(env):
    s,m,w,r,p=env; rid=submit(env); r.advance(p,rid)
    with pytest.raises(Forbidden): r.view(Principal('other','u',frozenset({1})),rid)
    with pytest.raises(Forbidden): r.view(Principal('demo','u',frozenset()),rid)

@pytest.mark.parametrize('point,model_count,read_count',[('AFTER_PREPARE',1,1),('AFTER_SEND_INTENT',1,1),('AFTER_ACCEPT',1,1),('AFTER_READ_INTENT',1,1),('AFTER_READ_RESULT',1,1),('AFTER_HANDLER_BEFORE_COMMIT',1,2)])
def test_crash_recovery(env,point,model_count,read_count):
    s,m,w,r,p=env; rid=submit(env)
    def crash(stage):
        if stage==point: raise RuntimeError('simulated process crash')
    with pytest.raises(RuntimeError): r.advance(p,rid,crash)
    expire(s,rid); assert r.advance(p,rid)['state']=='COMPLETED'; assert m.calls==model_count and w.calls==read_count
    attempts=s.inspect(p,rid)['attempts']; assert sum(a['accepted'] for a in attempts)==1
    if point=='AFTER_SEND_INTENT': assert attempts[0]['state']=='UNKNOWN' and attempts[0]['usage']==4096
    if point=='AFTER_PREPARE': assert attempts[0]['state']=='ABANDONED_NOT_SENT' and attempts[0]['usage']==0

def test_stale_fence(env):
    s,m,w,r,p=env; rid=submit(env); f=s.claim(p,rid); expire(s,rid); new=s.claim(p,rid); assert new>f
    with pytest.raises(LostClaim): s.finish(rid,f,'COMPLETED',{'code':'bad'})

def test_cancel_before_dispatch(env):
    s,m,w,r,p=env; rid=submit(env)
    def cancel(stage):
        if stage=='AFTER_ACCEPT': s.cancel(p,rid)
    assert r.advance(p,rid,cancel)['state']=='CANCELLED'; assert w.calls==0

def test_missing_and_compound(env):
    s,m,w,r,p=env
    for kind,fields in [('CLARIFY',{'missing_fields':['warehouse_id']}),('UNSUPPORTED',{'unresolved_items':['conditional cancel']})]:
        goal=GoalContract(kind=kind,provenance='FIXTURE_ONLY',**fields); rid=submit(env,key=kind,goal=goal); out=r.advance(p,rid)
        assert out['state']==('NEEDS_INPUT' if kind=='CLARIFY' else 'FAILED')
    assert m.calls==w.calls==0

def test_model_timeout_budget(env):
    s,m,w,r,p=env; rid=submit(env)
    def fail(body): raise ModelAttemptError('timeout')
    m.generate=fail; out=r.advance(p,rid); assert out['outcome']['code']=='MODEL_BUDGET_EXHAUSTED'; assert len(s.inspect(p,rid)['attempts'])==2 and w.calls==0

def test_binding_drift(env):
    s,m,w,r,p=env; rid=submit(env)
    def crash(stage):
        if stage=='AFTER_ACCEPT': raise RuntimeError('crash')
    with pytest.raises(RuntimeError): r.advance(p,rid,crash)
    expire(s,rid); w.revision='changed'; r.registry=make_registry(w)
    assert r.advance(p,rid)['outcome']['code']=='TOOL_NOT_AVAILABLE'; assert w.calls==0

def test_api(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    monkeypatch.setenv('POC_DEV_TOKEN','test-token-1234567890'); monkeypatch.setenv('POC_DB',str(tmp_path/'api.sqlite')); monkeypatch.delenv('POC_LIVE_MODEL',raising=False)
    client=TestClient(create_app()); h={'Authorization':'Bearer test-token-1234567890'}
    data={'client_request_id':'one','message':'查询仓库1任务103','goal':{'kind':'READ_STOCK_WORK','entity':{'warehouse_id':1,'task_id':103},'provenance':'CONFIRMED_STRUCTURED_INPUT'}}
    assert client.post('/api/v1/sessions/s/runs',json=data).status_code==401
    res=client.post('/api/v1/sessions/s/runs',json=data,headers=h); assert res.status_code==202
    rid=res.json()['run_id']; assert client.post(f'/api/v1/runs/{rid}/advance',headers=h).json()['state']=='COMPLETED'


def test_cancel_after_read_intent(env):
    s,m,w,r,p=env; rid=submit(env)
    def cancel(stage):
        if stage=='AFTER_READ_INTENT': s.cancel(p,rid)
    assert r.advance(p,rid,cancel)['state']=='CANCELLED'; assert w.calls==0

def test_wrong_domain_result(env):
    s,m,w,r,p=env; rid=submit(env)
    from agent.tools import ToolSpec,Registry
    spec=r.registry._specs['get_stock_work_detail']
    r.registry=Registry([ToolSpec(spec.alias,spec.tool_id,spec.version,spec.binding_revision,lambda p,a: {'warehouse_id':1,'task_id':104,'status':'NOT_FOUND','source':'SYNTHETIC_FIXTURE','observed_at':'2026-01-01T00:00:00+00:00'},spec.description)])
    assert r.advance(p,rid)['outcome']['code']=='READ_CONTRACT_OR_POLICY_ERROR'
