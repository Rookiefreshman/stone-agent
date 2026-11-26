"""Tests for production-default development path, not model quality or live warehouse tests."""
import sqlite3
import pytest
from agent.contracts import Principal, GoalContract, StockWorkArgs, Conflict, Forbidden, LostClaim, DomainReadError, canonical, digest
from agent.store import Store, RULES, LEGACY, DDL
from agent.runtime import Runtime
from agent.provider import FixtureProvider
from agent.tools import FixtureStoneService, make_registry
from agent.session import SessionService
from agent.worker import Worker
P=Principal('demo','local-dev',frozenset({1}))
A=StockWorkArgs(warehouse_id=1,task_id=103)
def goal(kind='ANALYZE_STOCK_WORK',task=103):
    return GoalContract(kind=kind,entity=StockWorkArgs(warehouse_id=1,task_id=task) if kind!='COMPARE_STOCK_WORK' else None,entities=[A,StockWorkArgs(warehouse_id=1,task_id=104)] if kind=='COMPARE_STOCK_WORK' else [],provenance='CONFIRMED_STRUCTURED_INPUT')
@pytest.fixture
def env(tmp_path):
    s=Store(tmp_path/'rules.sqlite',RULES); m=FixtureProvider(); w=FixtureStoneService(); return s,m,w,Runtime(s,m,make_registry(w))
def submit(env,g=None,key='a'):
    return env[0].submit(P,'unmanaged',key,'confirmed',g or goal())
def expire(s,rid):
    with s.tx() as c: c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))

@pytest.mark.parametrize('kind,steps,reads',[('READ_STOCK_WORK',1,1),('DIAGNOSE_STOCK_WORK',2,1),('ANALYZE_STOCK_WORK',3,1),('COMPARE_STOCK_WORK',2,2)])
def test_confirmed_goals_skip_llm(env,kind,steps,reads):
    s,m,w,r=env; rid=submit(env,goal(kind)); out=r.advance(P,rid)
    assert out['state']=='COMPLETED' and m.calls==0 and w.calls==reads
    audit=s.inspect(P,rid); assert audit['attempts']==[] and len(audit['invocations'])==steps
    assert all(x['origin']=='RULE' and x['state']=='SUCCEEDED' for x in audit['invocations'])
    assert s.get(P,rid)['execution_mode']==RULES
    assert all(s.invocation(rid,n)['request_id'] is None and s.invocation(rid,n)['provider_call_id'] is None for n in range(1,steps+1))

@pytest.mark.parametrize('task,state',[(101,'READY'),(102,'IN_FLIGHT'),(103,'UNKNOWN'),(104,'SUCCEEDED'),(105,'FAILED'),(106,'CANCELLED'),(999,None)])
def test_state_evidence(env,task,state):
    s,m,w,r=env; rid=submit(env,goal('DIAGNOSE_STOCK_WORK',task)); out=r.advance(P,rid)['outcome']; triage=out['triage']
    assert triage['observed_state']==state and triage['root_cause']=='UNDETERMINED'
    assert triage['source_evidence_hash']==out['completed_steps'][0]['evidence_hash'] and triage['source']=='SYNTHETIC_FIXTURE'
    assert triage['missing_evidence'] and triage['next_checks']
    if state=='UNKNOWN': assert '禁止自动重新派发' in ''.join(triage['business_rule_effects']) and '预占' in ''.join(triage['business_rule_effects'])
    if state is None: assert out['goal_status']=='PARTIAL' and not triage['business_rule_effects']

@pytest.mark.parametrize('point,reads',[('AFTER_RULE_RESERVE',1),('AFTER_READ_INTENT',1),('AFTER_HANDLER_BEFORE_COMMIT',2),('AFTER_READ_RESULT',1)])
def test_direct_fault_recovery(env,point,reads):
    s,m,w,r=env; rid=submit(env)
    def crash(stage):
        if stage==point: raise RuntimeError('injected')
    with pytest.raises(RuntimeError): r.advance(P,rid,crash)
    expire(s,rid); assert Worker(r,lambda:P).once()
    assert r.view(P,rid)['state']=='COMPLETED' and w.calls==reads and m.calls==0 and len(s.inspect(P,rid)['invocations'])==3

@pytest.mark.parametrize('point',['AFTER_RULE_RESERVE','AFTER_READ_INTENT'])
def test_direct_cancel(env,point):
    s,m,w,r=env; rid=submit(env)
    def hook(stage):
        if stage==point: s.cancel(P,rid)
    assert r.advance(P,rid,hook)['state']=='CANCELLED' and w.calls==m.calls==0

def test_direct_binding_drift(env):
    s,m,w,r=env; rid=submit(env)
    def crash(stage):
        if stage=='AFTER_RULE_RESERVE': raise RuntimeError('injected')
    with pytest.raises(RuntimeError): r.advance(P,rid,crash)
    expire(s,rid); w.revision='changed'; r.registry=make_registry(w)
    assert r.advance(P,rid)['outcome']['code']=='TOOL_NOT_AVAILABLE' and w.calls==m.calls==0

def test_cross_warehouse_preflight(env):
    s,m,w,r=env; g=goal('COMPARE_STOCK_WORK'); g.entities[1]=StockWorkArgs(warehouse_id=2,task_id=104); rid=submit(env,g)
    with pytest.raises(Forbidden): r.advance(P,rid)
    assert w.calls==m.calls==0 and s.get(P,rid)['state']=='FAILED'

def test_second_read_partial(env):
    s,m,w,r=env; orig=w.get_detail
    def fail(p,a):
        if a.task_id==104: raise DomainReadError('UPSTREAM_UNAVAILABLE')
        return orig(p,a)
    w.get_detail=fail; r.registry=make_registry(w); rid=submit(env,goal('COMPARE_STOCK_WORK')); out=r.advance(P,rid)['outcome']
    assert out['goal_status']=='PARTIAL' and out['code']=='UPSTREAM_UNAVAILABLE' and len(out['completed_steps'])==1 and m.calls==0

def test_mode_pinned_on_idempotent_replay(tmp_path):
    path=tmp_path/'mode.sqlite'; s=Store(path); g=goal('READ_STOCK_WORK'); rid=s.submit(P,'s','k','confirmed',g)
    s2=Store(path,RULES); assert s2.submit(P,'s','k','confirmed',g)==rid and s2.get(P,rid)['execution_mode']==LEGACY
    m=FixtureProvider(); w=FixtureStoneService(); r=Runtime(s2,m,make_registry(w)); assert r.advance(P,rid)['state']=='COMPLETED' and m.calls==1
    new=s2.submit(P,'s','new','confirmed',g); assert r.advance(P,new)['state']=='COMPLETED' and m.calls==1

def test_legacy_v05_migration(tmp_path):
    path=tmp_path/'old.sqlite'; g=goal('READ_STOCK_WORK'); gd=g.model_dump(exclude={'entities'})
    with sqlite3.connect(path) as c:
        c.executescript(DDL)
        c.execute('ALTER TABLE model_attempt ADD COLUMN step_no INTEGER NOT NULL DEFAULT 1')
        c.execute('DROP INDEX one_accepted_turn'); c.execute('CREATE UNIQUE INDEX one_accepted_turn ON model_attempt(run_id,step_no) WHERE accepted=1')
        c.execute('DROP TABLE invocation'); c.execute('CREATE TABLE invocation(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES run(id),request_id TEXT NOT NULL REFERENCES model_attempt(id),provider_call_id TEXT NOT NULL,alias TEXT NOT NULL,args TEXT NOT NULL,args_hash TEXT NOT NULL,state TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,result TEXT,evidence_hash TEXT,step_no INTEGER NOT NULL DEFAULT 1,UNIQUE(run_id,step_no))')
        c.execute('INSERT INTO run(id,tenant,actor,session,request_key,fingerprint,message,goal) VALUES(?,?,?,?,?,?,?,?)',('old',P.tenant,P.actor,'s','k',digest(['confirmed',gd]),'confirmed',canonical(gd)))
    s=Store(path,RULES); assert s.get(P,'old')['execution_mode']==LEGACY
    m=FixtureProvider(); w=FixtureStoneService(); r=Runtime(s,m,make_registry(w)); assert r.advance(P,'old')['state']=='COMPLETED' and m.calls==1
    assert Store(path,RULES).invocation('old')['origin']=='MODEL'
    rid=s.submit(P,'s','new','confirmed',g); r.advance(P,rid); assert s.invocation(rid)['origin']=='RULE' and m.calls==1

@pytest.mark.parametrize('msg,kind',[('诊断仓库1任务103','DIAGNOSE_STOCK_WORK'),('帮我分析1号仓库103号库存作业状态及流水','ANALYZE_STOCK_WORK')])
def test_managed_diagnostic(env,msg,kind):
    s,m,w,r=env; svc=SessionService(s); sid=svc.create(P)['session_id']; prop=svc.propose(P,sid,'p',msg)
    assert prop['goal_kind']==kind and prop['status']=='NEEDS_CONFIRMATION' and not prop['execution_allowed']
    with pytest.raises(Conflict): svc.confirm(P,sid,prop['proposal_id'],A)
    rid=svc.confirm(P,sid,prop['proposal_id'],A,kind)['run_id']; assert svc.confirm(P,sid,prop['proposal_id'],A,kind)['run_id']==rid
    assert r.advance(P,rid)['outcome']['triage']['observed_state']=='UNKNOWN' and m.calls==0
    assert svc.propose(P,sid,'q','那104号呢')['status']=='NEEDS_INPUT'

@pytest.mark.parametrize('msg',['诊断仓库1任务103然后取消','分析仓库1任务103和104','分析任务103'])
def test_no_diagnostic_downgrade(env,msg):
    s,*_=env; svc=SessionService(s); sid=svc.create(P)['session_id']; assert svc.propose(P,sid,'p',msg)['status']=='UNSUPPORTED'

def test_api_defaults_to_rule_mode(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    path=tmp_path/'api.sqlite'; monkeypatch.setenv('POC_DEV_TOKEN','test-token-long-enough'); monkeypatch.setenv('POC_DB',str(path)); monkeypatch.delenv('AGENT_DATABASE_URL',raising=False); monkeypatch.delenv('POC_LIVE_MODEL',raising=False); monkeypatch.delenv('STONE_LIVE_DOMAIN',raising=False)
    c=TestClient(create_app()); h={'Authorization':'Bearer test-token-long-enough'}; sid=c.post('/api/v1/sessions',headers=h).json()['session_id']; url=f'/api/v1/sessions/{sid}/goal-proposals'
    prop=c.post(url,headers=h,json={'client_request_id':'p','message':'诊断仓库1任务103'}).json(); confirm=url+'/'+prop['proposal_id']+'/confirm'
    assert c.post(confirm,headers=h,json={'entity':prop['candidate']}).status_code==409
    rid=c.post(confirm,headers=h,json={'entity':prop['candidate'],'kind':prop['goal_kind']}).json()['run_id']
    assert c.post(f'/api/v1/runs/{rid}/advance',headers=h).json()['outcome']['triage']['observed_state']=='UNKNOWN'
    s=Store(path); assert s.inspect(P,rid)['attempts']==[] and s.get(P,rid)['execution_mode']==RULES
