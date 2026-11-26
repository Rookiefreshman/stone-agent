from concurrent.futures import ThreadPoolExecutor
from threading import Event
import pytest
from agent.session import SessionService
from agent.store import Store
from agent.contracts import Principal, StockWorkArgs, GoalContract, Conflict, Forbidden, ModelTurn, ToolCall
from agent.runtime import Runtime
from agent.provider import FixtureProvider
from agent.tools import FixtureStoneService, make_registry

P=Principal('demo','u',frozenset({1,2}))
OTHER=Principal('other','u',frozenset({1,2}))
REVOKED=Principal('demo','u',frozenset({2}))
A=StockWorkArgs(warehouse_id=1,task_id=103)

@pytest.fixture
def env(tmp_path):
    s=Store(tmp_path/'session.sqlite'); svc=SessionService(s); sid=svc.create(P)['session_id']; return s,svc,sid

def first(svc,sid): return svc.propose(P,sid,'a','查询仓库1任务103')
def confirmed(svc,sid):
    r=first(svc,sid); return svc.confirm(P,sid,r['proposal_id'],A)
def complete(s,rid): return Runtime(s,FixtureProvider(),make_registry(FixtureStoneService())).advance(P,rid)
def count(s):
    with s.tx() as c: return c.execute('SELECT count(*) FROM run').fetchone()[0]

def test_no_run_until_confirm(env):
    s,svc,sid=env; r=first(svc,sid); assert r['status']=='NEEDS_CONFIRMATION' and count(s)==0
    assert svc.history(P,sid)['confirmed_entity'] is None
    c=svc.confirm(P,sid,r['proposal_id'],A); assert count(s)==1
    assert complete(s,c['run_id'])['state']=='COMPLETED'

def test_persistent_followup(env):
    s,svc,sid=env; c=confirmed(svc,sid); complete(s,c['run_id'])
    reopened=SessionService(Store(s.path)); r=reopened.propose(P,sid,'b','那104号呢？')
    assert r['source']=='CONFIRMED_CONTEXT' and r['candidate']=={'warehouse_id':1,'task_id':104}
    assert count(s)==1 and reopened.history(P,sid)['confirmed_entity']==A.model_dump()

def test_clarify_warehouse(env):
    s,svc,sid=env; r=svc.propose(P,sid,'a','查询103号库存作业'); assert r['status']=='NEEDS_INPUT'
    r=svc.propose(P,sid,'b','1号仓库'); assert r['source']=='PENDING_CLARIFICATION' and r['candidate']==A.model_dump(); assert count(s)==0

def test_clarify_task(env):
    s,svc,sid=env; assert svc.propose(P,sid,'a','查询仓库1')['status']=='NEEDS_INPUT'
    r=svc.propose(P,sid,'b','任务103'); assert r['candidate']==A.model_dump() and r['status']=='NEEDS_CONFIRMATION'

def test_unconfirmed_not_memory(env):
    s,svc,sid=env; first(svc,sid); r=svc.propose(P,sid,'b','那104号呢？'); assert r['status']=='NEEDS_INPUT' and r['candidate']['warehouse_id'] is None

def test_unconfirmed_new_warehouse_blocks_old_context(env):
    s,svc,sid=env; c=confirmed(svc,sid); complete(s,c['run_id']); svc.propose(P,sid,'b','查询仓库2任务105')
    assert svc.propose(P,sid,'c','那104号呢')['status']=='NEEDS_INPUT'

def test_no_context_cross_session(env):
    s,svc,sid=env; c=confirmed(svc,sid); complete(s,c['run_id']); new=svc.create(P)['session_id']; assert svc.propose(P,new,'a','那104号呢')['status']=='NEEDS_INPUT'

@pytest.mark.parametrize('message',['那104号呢然后冻结库位','那104号和105号呢','取消任务103','如果失败就取消'])
def test_no_followup_write_or_multiple(env,message):
    s,svc,sid=env; c=confirmed(svc,sid); complete(s,c['run_id']); r=svc.propose(P,sid,'b',message); assert r['status']=='UNSUPPORTED' and count(s)==1

def test_idempotent_proposal(env):
    s,svc,sid=env; r=first(svc,sid); assert first(svc,sid)==r
    with pytest.raises(Conflict): svc.propose(P,sid,'a','查询仓库2任务103')

def test_exact_confirmation(env):
    s,svc,sid=env; r=first(svc,sid)
    with pytest.raises(Conflict): svc.confirm(P,sid,r['proposal_id'],StockWorkArgs(warehouse_id=1,task_id=104))
    assert count(s)==0

def test_confirm_idempotent_concurrent(env):
    s,svc,sid=env; r=first(svc,sid)
    with ThreadPoolExecutor(4) as pool: out=list(pool.map(lambda _:svc.confirm(P,sid,r['proposal_id'],A),range(4)))
    assert len({x['run_id'] for x in out})==1 and count(s)==1

def test_superseded(env):
    s,svc,sid=env; r=first(svc,sid); svc.propose(P,sid,'b','查询仓库1任务104')
    with pytest.raises(Conflict): svc.confirm(P,sid,r['proposal_id'],A)
    assert first(svc,sid)['status']=='SUPERSEDED'

def test_expiry(env):
    s,_,sid=env; now=[100.]; svc=SessionService(s,ttl=5,clock=lambda:now[0]); r=first(svc,sid); now[0]=106
    assert first(svc,sid)['status']=='EXPIRED'
    with pytest.raises(Conflict): svc.confirm(P,sid,r['proposal_id'],A)

def test_confirm_replay_after_expiry(env):
    s,_,sid=env; now=[100.]; svc=SessionService(s,ttl=5,clock=lambda:now[0]); r=confirmed(svc,sid); now[0]=106
    assert svc.confirm(P,sid,r['proposal_id'],A)==r

def test_scope_revocation(env):
    s,svc,sid=env; r=first(svc,sid)
    with pytest.raises(Forbidden): svc.confirm(REVOKED,sid,r['proposal_id'],A)
    assert svc.history(REVOKED,sid)['entries'][0]['status']=='REDACTED'
    c=svc.confirm(P,sid,r['proposal_id'],A); complete(s,c['run_id'])
    assert svc.history(REVOKED,sid)['confirmed_entity'] is None
    assert svc.propose(REVOKED,sid,'b','那104号呢')['status']=='FORBIDDEN'

def test_tenant_isolation(env):
    s,svc,sid=env; r=first(svc,sid)
    for fn in [lambda:svc.history(OTHER,sid),lambda:svc.propose(OTHER,sid,'a','查询'),lambda:svc.confirm(OTHER,sid,r['proposal_id'],A)]:
        with pytest.raises(Forbidden): fn()

def test_active_run(env):
    s,svc,sid=env; confirmed(svc,sid)
    with pytest.raises(Conflict): svc.propose(P,sid,'b','那104号呢')

def test_direct_submit_cannot_bypass(env):
    s,svc,sid=env; g=GoalContract(kind='READ_STOCK_WORK',entity=A,provenance='CONFIRMED_STRUCTURED_INPUT')
    with pytest.raises(Conflict): s.submit(P,sid,'k','msg',g)
    with pytest.raises(Forbidden): s.submit(OTHER,sid,'k','msg',g)

def test_late_proposal_ignored(env):
    s,_,sid=env; entered=Event(); release=Event()
    class Slow:
        model='fake'
        def generate(self,body):
            entered.set(); assert release.wait(5)
            return ModelTurn('TOOL_CALLS',(ToolCall('c','propose_stock_work_goal','{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":103}'),),20)
    slow=SessionService(s,Slow()); fast=SessionService(s)
    with ThreadPoolExecutor(1) as pool:
        f=pool.submit(first,slow,sid); assert entered.wait(5)
        newer=fast.propose(P,sid,'b','查询仓库2任务104'); release.set(); old=f.result(5)
    assert old['status']=='SUPERSEDED' and newer['status']=='NEEDS_CONFIRMATION' and count(s)==0

def test_history_pagination(env):
    s,svc,sid=env; first(svc,sid); svc.propose(P,sid,'b','查询仓库1任务104'); svc.propose(P,sid,'c','查询仓库2任务105')
    a=svc.history(P,sid,2); b=svc.history(P,sid,2,a['next_before']); assert len(a['entries'])==2 and len(b['entries'])==1
    assert a['entries'][0]['revision']>b['entries'][0]['revision']

def test_http_end_to_end(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    monkeypatch.setenv('POC_DEV_TOKEN','test-token-1234567890'); monkeypatch.setenv('POC_DB',str(tmp_path/'api.sqlite')); monkeypatch.delenv('POC_LIVE_MODEL',raising=False); monkeypatch.delenv('STONE_LIVE_DOMAIN',raising=False)
    c=TestClient(create_app()); h={'Authorization':'Bearer test-token-1234567890'}
    assert c.post('/api/v1/sessions').status_code==401
    sid=c.post('/api/v1/sessions',headers=h).json()['session_id']; url=f'/api/v1/sessions/{sid}/goal-proposals'
    r=c.post(url,headers=h,json={'client_request_id':'a','message':'查询仓库1任务103'}).json()
    assert r['status']=='NEEDS_CONFIRMATION'
    rr=c.post(url+'/'+r['proposal_id']+'/confirm',headers=h,json={'entity':r['candidate']}); assert rr.status_code==202
    assert c.post('/api/v1/runs/'+rr.json()['run_id']+'/advance',headers=h).json()['state']=='COMPLETED'
    assert c.post(url,headers=h,json={'client_request_id':'b','message':'那104号呢'}).json()['candidate']=={'warehouse_id':1,'task_id':104}
    assert c.get(f'/api/v1/sessions/{sid}',headers=h).status_code==200
