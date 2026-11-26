import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from agent.contracts import (GoalContract,StockWorkArgs,Principal,ToolCall,ModelTurn,Forbidden,Conflict,DomainReadError,canonical,digest)
from agent.store import Store,DDL
from agent.runtime import Runtime
from agent.tools import FixtureStoneService,make_registry
from agent.provider import FixtureProvider
from agent.worker import Worker

@pytest.fixture
def env(tmp_path):
    s=Store(tmp_path/'multi.sqlite'); p=Principal('demo','u',frozenset({1})); m=FixtureProvider(); w=FixtureStoneService()
    return s,p,m,w,Runtime(s,m,make_registry(w))

def entity(task=103,warehouse=1): return StockWorkArgs(warehouse_id=warehouse,task_id=task)
def goal(kind='DIAGNOSE_STOCK_WORK',tasks=(103,104)):
    return GoalContract(kind=kind,entity=entity(tasks[0]) if kind!='COMPARE_STOCK_WORK' else None,entities=[entity(t) for t in tasks] if kind=='COMPARE_STOCK_WORK' else [],provenance='CONFIRMED_STRUCTURED_INPUT')
def submit(env,g=None,key='k'):
    s,p,*_=env; return s.submit(p,'test',key,'confirmed task query',g or goal())
def expire(s,rid):
    with s.tx() as c: c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))

@pytest.mark.parametrize('task,state',[(101,'READY'),(102,'IN_FLIGHT'),(103,'UNKNOWN'),(104,'SUCCEEDED'),(105,'FAILED'),(106,'CANCELLED'),(999,None)])
def test_diagnosis_states(env,task,state):
    s,p,m,w,r=env; rid=submit(env,goal(tasks=(task,))); out=r.advance(p,rid)['outcome']
    assert out['diagnosis']['task_state']==state and out['diagnosis']['root_cause']=='UNDETERMINED'
    assert out['goal_status']==('SATISFIED' if state else 'PARTIAL')
    assert out['diagnosis']['source_evidence_hash']==out['completed_steps'][0]['evidence_hash']
    assert out['diagnosis']['source']=='SYNTHETIC_FIXTURE' and m.calls==2 and w.calls==1
    assert [x['tool'] for x in out['completed_steps']]==['get_stock_work_detail','diagnose_stock_work']
    assert [x['step_no'] for x in s.inspect(p,rid)['attempts']]==[1,2]
    assert r.advance(p,rid)['outcome']==out and m.calls==2 and w.calls==1

@pytest.mark.parametrize('tasks',[(103,104),(103,104,105),(103,999)])
def test_compare(env,tasks):
    s,p,m,w,r=env; rid=submit(env,goal('COMPARE_STOCK_WORK',tasks)); out=r.advance(p,rid)
    assert out['state']=='COMPLETED'; steps=out['outcome']['completed_steps']
    assert [x['facts']['task_id'] for x in steps]==list(tasks)
    assert m.calls==w.calls==len(tasks)
    assert len([a for a in s.inspect(p,rid)['attempts'] if a['accepted']])==len(tasks)

@pytest.mark.parametrize('tasks',[(103,), (103,103), (103,104,105,106)])
def test_invalid_compare(tasks):
    with pytest.raises(ValueError): goal('COMPARE_STOCK_WORK',tasks)

def test_all_permissions_before_network(env):
    s,p,m,w,r=env; g=GoalContract(kind='COMPARE_STOCK_WORK',entities=[entity(),entity(104,2)],provenance='CONFIRMED_STRUCTURED_INPUT'); rid=submit(env,g)
    with pytest.raises(Forbidden): r.advance(p,rid)
    assert m.calls==w.calls==0 and s.get(p,rid)['state']=='FAILED'

@pytest.mark.parametrize('point', ['AFTER_PREPARE','AFTER_SEND_INTENT','AFTER_ACCEPT','AFTER_READ_INTENT','AFTER_HANDLER_BEFORE_COMMIT','AFTER_READ_RESULT'])
def test_crash_second_step(env,point):
    s,p,m,w,r=env; rid=submit(env,goal('COMPARE_STOCK_WORK')); hits=0
    def crash(stage):
        nonlocal hits
        if stage==point:
            hits+=1
            if hits==2: raise RuntimeError('crash')
    with pytest.raises(RuntimeError): r.advance(p,rid,crash)
    first=s.invocation(rid,1); assert first['state']=='SUCCEEDED'
    expire(s,rid); out=r.advance(p,rid); assert out['state']=='COMPLETED'
    assert s.invocation(rid,1)==first and w.calls==(3 if point=='AFTER_HANDLER_BEFORE_COMMIT' else 2)
    assert sum(a['accepted'] for a in s.inspect(p,rid)['attempts'])==2

def test_partial_second_model_wrong_target(env):
    s,p,m,w,r=env; rid=submit(env,goal('COMPARE_STOCK_WORK')); orig=m.generate
    def bad(body):
        if m.calls: return ModelTurn('TOOL_CALLS',(ToolCall('bad','get_stock_work_detail',canonical(entity(999).model_dump())),),1)
        return orig(body)
    m.generate=bad; out=r.advance(p,rid); assert out['state']=='FAILED'
    assert out['outcome']['code']=='GOAL_MISMATCH' and out['outcome']['goal_status']=='PARTIAL' and len(out['outcome']['completed_steps'])==1 and w.calls==1

def test_diagnosis_cannot_select_write(env):
    s,p,m,w,r=env; rid=submit(env); orig=m.generate
    def bad(body):
        if m.calls: return ModelTurn('TOOL_CALLS',(ToolCall('bad','cancel_stock_work',canonical(entity().model_dump())),),1)
        return orig(body)
    m.generate=bad; out=r.advance(p,rid); assert out['outcome']['code']=='TOOL_NOT_AVAILABLE' and w.calls==1

def test_diagnosis_cannot_call_read_again(env):
    s,p,m,w,r=env; rid=submit(env); orig=m.generate
    def bad(body):
        if m.calls: return ModelTurn('TOOL_CALLS',(ToolCall('bad','get_stock_work_detail',canonical(entity().model_dump())),),1)
        return orig(body)
    m.generate=bad; assert r.advance(p,rid)['outcome']['code']=='TOOL_NOT_AVAILABLE' and w.calls==1

def test_second_read_failure_keeps_evidence(env):
    s,p,m,w,r=env; orig=w.get_detail
    def fail(p,a):
        if a.task_id==104: raise DomainReadError('UPSTREAM_UNAVAILABLE')
        return orig(p,a)
    w.get_detail=fail; r.registry=make_registry(w); rid=submit(env,goal('COMPARE_STOCK_WORK')); out=r.advance(p,rid)['outcome']
    assert out['code']=='UPSTREAM_UNAVAILABLE' and out['goal_status']=='PARTIAL' and len(out['completed_steps'])==1

def test_cancel_between_steps(env):
    s,p,m,w,r=env; rid=submit(env)
    def hook(stage):
        if stage=='AFTER_READ_RESULT': s.cancel(p,rid)
    assert r.advance(p,rid,hook)['state']=='CANCELLED'; assert m.calls==w.calls==1 and s.invocation(rid,2) is None

def test_worker_multiple_steps(env):
    s,p,m,w,r=env; rid=submit(env,goal('COMPARE_STOCK_WORK',(101,102,103)))
    assert Worker(r,lambda:p).once(); assert r.view(p,rid)['state']=='COMPLETED' and m.calls==w.calls==3

def test_sqlite_v04_upgrade_preserves_legacy_rows(tmp_path):
    db=tmp_path/'old.sqlite'; p=Principal('demo','u',frozenset({1})); g=goal('READ_STOCK_WORK'); old=g.model_dump(exclude={'entities'}); body={}; args=entity().model_dump()
    with sqlite3.connect(db) as c:
        c.executescript(DDL)
        c.execute('INSERT INTO run(id,tenant,actor,session,request_key,fingerprint,message,goal,state) VALUES(?,?,?,?,?,?,?,?,?)',('r','demo','u','test','k',digest(['query',old]),'query',canonical(old),'COMPLETED'))
        c.execute('INSERT INTO model_attempt(id,run_id,attempt_no,fence,state,body,request_hash,snapshot,provider_revision,reserved,accepted) VALUES(?,?,?,?,?,?,?,?,?,?,?)',('m','r',1,1,'RECEIVED',canonical(body),digest(body),'{}','old',4096,1))
        c.execute('INSERT INTO invocation(id,run_id,request_id,provider_call_id,alias,args,args_hash,state) VALUES(?,?,?,?,?,?,?,?)',('i','r','m','c','get_stock_work_detail',canonical(args),digest(args),'SUCCEEDED'))
    s=Store(db); assert s.invocation('r',1)['id']=='i'; assert s.submit(p,'test','k','query',g)=='r'
    s=Store(db); assert s.invocation('r',1)['step_no']==1
    r=Runtime(s,FixtureProvider(),make_registry(FixtureStoneService())); rid=s.submit(p,'test','new','diagnose',goal()); assert r.advance(p,rid)['state']=='COMPLETED'

def test_api_structured_comparison(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    monkeypatch.setenv('POC_DEV_TOKEN','test-token-long-enough'); monkeypatch.setenv('POC_DB',str(tmp_path/'api.sqlite')); monkeypatch.delenv('AGENT_DATABASE_URL',raising=False)
    client=TestClient(create_app()); headers={'Authorization':'Bearer test-token-long-enough'}
    body={'client_request_id':'multi','message':'confirmed comparison','goal':goal('COMPARE_STOCK_WORK').model_dump()}
    res=client.post('/api/v1/sessions/structured-dev/runs',json=body,headers=headers); assert res.status_code==202
    rid=res.json()['run_id']; res=client.post(f'/api/v1/runs/{rid}/advance',headers=headers); assert res.status_code==200 and res.json()['state']=='COMPLETED'
    body['client_request_id']='forbidden'; body['goal']['entities'][1]['warehouse_id']=2
    assert client.post('/api/v1/sessions/structured-dev/runs',json=body,headers=headers).status_code==403

@pytest.mark.parametrize('reverse',[False,True])
def test_dynamic_local_tool_order(env,reverse):
    s,p,m,w,r=env; rid=submit(env,goal('ANALYZE_STOCK_WORK')); orig=m.generate; seen=[]
    def choose(body):
        aliases=[x['function']['name'] for x in body['tools']]; seen.append(aliases)
        turn=orig(body)
        if reverse and len(aliases)==2:
            return ModelTurn(turn.finish,(ToolCall('reverse',aliases[-1],turn.calls[0].arguments_json),),turn.usage)
        return turn
    m.generate=choose; out=r.advance(p,rid); assert out['state']=='COMPLETED' and w.calls==1 and m.calls==3
    steps=out['outcome']['completed_steps']; assert len(seen[1])==2 and len(seen[2])==1
    assert [x['tool'] for x in steps]==['get_stock_work_detail',*(['summarize_stock_work_ledger','diagnose_stock_work'] if reverse else ['diagnose_stock_work','summarize_stock_work_ledger'])]
    assert out['outcome']['ledger_summary']['source_evidence_hash']==steps[0]['evidence_hash']
    assert out['outcome']['diagnosis']['source_evidence_hash']==steps[0]['evidence_hash']

def test_dynamic_order_restored_after_accept(env):
    s,p,m,w,r=env; rid=submit(env,goal('ANALYZE_STOCK_WORK')); orig=m.generate
    def choose(body):
        turn=orig(body)
        if len(body['tools'])==2: return ModelTurn(turn.finish,(ToolCall('reverse','summarize_stock_work_ledger',turn.calls[0].arguments_json),),turn.usage)
        return turn
    m.generate=choose; hits=0
    def crash(stage):
        nonlocal hits
        if stage=='AFTER_ACCEPT':
            hits+=1
            if hits==2: raise RuntimeError('crash')
    with pytest.raises(RuntimeError): r.advance(p,rid,crash)
    expire(s,rid); out=r.advance(p,rid)['outcome']; assert m.calls==3 and w.calls==1
    assert [x['tool'] for x in out['completed_steps']]==['get_stock_work_detail','summarize_stock_work_ledger','diagnose_stock_work']

def test_no_duplicate_analysis(env):
    s,p,m,w,r=env; rid=submit(env,goal('ANALYZE_STOCK_WORK')); orig=m.generate
    def choose(body):
        if m.calls==2: return ModelTurn('TOOL_CALLS',(ToolCall('repeat','diagnose_stock_work',canonical(entity().model_dump())),),1)
        return orig(body)
    m.generate=choose; out=r.advance(p,rid)['outcome']; assert out['code']=='TOOL_NOT_AVAILABLE' and out['goal_status']=='PARTIAL' and len(out['completed_steps'])==2
