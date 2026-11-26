"""Requires a dedicated migrated PostgreSQL test database. Never truncates data."""
import os,uuid
from concurrent.futures import ThreadPoolExecutor
import pytest
from agent.postgres import PostgresStore
from agent.contracts import Principal,GoalContract,StockWorkArgs,LostClaim
from agent.session import SessionService
from agent.runtime import Runtime
from agent.provider import FixtureProvider
from agent.tools import FixtureStoneService,make_registry

@pytest.fixture
def pg():
    dsn=os.getenv('TEST_POSTGRES_DSN')
    if not dsn: pytest.skip('TEST_POSTGRES_DSN not configured')
    s=PostgresStore(dsn); p=Principal('test-'+uuid.uuid4().hex,'test',frozenset({1}))
    return s,p

def test_pg_confirm_claim_recover(pg):
    s,p=pg; svc=SessionService(s); sid=svc.create(p)['session_id']; proposal=svc.propose(p,sid,'p','查询仓库1任务103'); args=StockWorkArgs(warehouse_id=1,task_id=103)
    with ThreadPoolExecutor(4) as ex: ids=list(ex.map(lambda _:svc.confirm(p,sid,proposal['proposal_id'],args)['run_id'],range(4)))
    assert len(set(ids))==1
    rid=ids[0]
    with ThreadPoolExecutor(4) as ex: claims=list(ex.map(lambda _:s.claim_next(p),range(4)))
    assert len([x for x in claims if x])==1
    _,f=next(x for x in claims if x); s.heartbeat(rid,f)
    with s.tx() as c: c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))
    _,f2=s.claim_next(p)
    with pytest.raises(LostClaim): s.heartbeat(rid,f)
    r=Runtime(s,FixtureProvider(),make_registry(FixtureStoneService()))
    assert r.execute_claim(p,rid,f2)['state']=='COMPLETED'
    assert s.events_after(p,rid)[1][-1]['kind']=='COMPLETED'

def test_pg_idempotent_submit(pg):
    s,p=pg; g=GoalContract(kind='READ_STOCK_WORK',entity=StockWorkArgs(warehouse_id=1,task_id=103),provenance='CONFIRMED_STRUCTURED_INPUT')
    with ThreadPoolExecutor(8) as ex: ids=list(ex.map(lambda _:s.submit(p,'unmanaged','key','query',g),range(8)))
    assert len(set(ids))==1

def test_pg_multi_step_recovery(pg):
    s,p=pg; g=GoalContract(kind='DIAGNOSE_STOCK_WORK',entity=StockWorkArgs(warehouse_id=1,task_id=103),provenance='CONFIRMED_STRUCTURED_INPUT')
    rid=s.submit(p,'unmanaged','multi','diagnose',g); r=Runtime(s,FixtureProvider(),make_registry(FixtureStoneService()))
    count=0
    def crash(stage):
        nonlocal count
        if stage=='AFTER_ACCEPT':
            count+=1
            if count==2: raise RuntimeError('crash')
    with pytest.raises(RuntimeError): r.advance(p,rid,crash)
    with s.tx() as c: c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))
    out=r.advance(p,rid); assert out['state']=='COMPLETED'
    assert s.invocation(rid,1)['state']==s.invocation(rid,2)['state']=='SUCCEEDED'

def test_pg_rule_call_recovery(pg):
    from agent.store import RULES
    s,p=pg; s=PostgresStore(s.dsn,RULES)
    g=GoalContract(kind='ANALYZE_STOCK_WORK',entity=StockWorkArgs(warehouse_id=1,task_id=103),provenance='CONFIRMED_STRUCTURED_INPUT')
    rid=s.submit(p,'unmanaged','rule','analyze',g); m=FixtureProvider(); w=FixtureStoneService(); r=Runtime(s,m,make_registry(w))
    def crash(stage):
        if stage=='AFTER_READ_RESULT': raise RuntimeError('crash')
    with pytest.raises(RuntimeError): r.advance(p,rid,crash)
    with s.tx() as c: c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))
    assert r.advance(p,rid)['state']=='COMPLETED' and m.calls==0 and w.calls==1
    audit=s.inspect(p,rid); assert audit['attempts']==[] and all(x['origin']=='RULE' for x in audit['invocations'])
