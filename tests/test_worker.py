import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from agent.store import Store
from agent.contracts import Principal,GoalContract,StockWorkArgs,LostClaim,Forbidden,Conflict
from agent.runtime import Runtime
from agent.provider import FixtureProvider
from agent.tools import make_registry,FixtureStoneService
from agent.worker import Worker
from agent.session import SessionService

P=Principal('demo','local-dev',frozenset({1}))
OTHER=Principal('other','local-dev',frozenset({1}))
G=GoalContract(kind='READ_STOCK_WORK',entity=StockWorkArgs(warehouse_id=1,task_id=103),provenance='CONFIRMED_STRUCTURED_INPUT')

def runtime(s): return Runtime(s,FixtureProvider(),make_registry(FixtureStoneService()))
def add(s,key='a',p=P): return s.submit(p,'worker-tests',key,'query',G)
@pytest.fixture
def store(tmp_path): return Store(tmp_path/'worker.sqlite')

def test_worker_drives_confirmed_session(store):
    svc=SessionService(store); sid=svc.create(P)['session_id']; prop=svc.propose(P,sid,'p','查询仓库1任务103')
    rid=svc.confirm(P,sid,prop['proposal_id'],G.entity)['run_id']
    w=Worker(runtime(store),lambda:P)
    assert w.once() and store.get(P,rid)['state']=='COMPLETED'
    assert not w.once()

def test_claim_race(store):
    rid=add(store)
    with ThreadPoolExecutor(8) as ex: claims=list(ex.map(lambda _:store.claim_next(P),range(8)))
    assert [c[0] for c in claims if c]==[rid]

def test_parallel_worker_distinct_runs(store):
    ids=[add(store,str(i)) for i in range(8)]
    with ThreadPoolExecutor(4) as ex: assert all(ex.map(lambda _:Worker(runtime(store),lambda:P).once(),range(8)))
    assert all(store.get(P,x)['state']=='COMPLETED' for x in ids)

def test_scope(store):
    rid=add(store,p=OTHER)
    assert not Worker(runtime(store),lambda:P).once()
    assert store.get(OTHER,rid)['state']=='QUEUED'
    with pytest.raises(Forbidden): store.events_after(P,rid)

def test_heartbeat_and_fence(store):
    rid=add(store); _,f=store.claim_next(P,lease=5); old=store.get(P,rid)['lease_until']
    store.heartbeat(rid,f,lease=10); assert store.get(P,rid)['lease_until']>old
    with store.tx() as c: c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))
    _,f2=store.claim_next(P); assert f2>f
    with pytest.raises(LostClaim): store.heartbeat(rid,f)
    with pytest.raises(LostClaim): runtime(store).execute_claim(P,rid,f)
    assert runtime(store).execute_claim(P,rid,f2)['state']=='COMPLETED'

def test_expired_recovery(store):
    rid=add(store); r=runtime(store)
    def fail(point):
        if point=='AFTER_ACCEPT': raise RuntimeError('injected')
    with pytest.raises(RuntimeError): r.advance(P,rid,fail)
    with store.tx() as c: c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))
    assert Worker(r,lambda:P).once() and r.provider.calls==1
    assert store.get(P,rid)['state']=='COMPLETED'

def test_queued_cancel(store):
    rid=add(store); store.cancel(P,rid)
    assert not Worker(runtime(store),lambda:P).once()
    assert store.get(P,rid)['state']=='CANCELLED'
    state,events=store.events_after(P,rid)
    assert state=='CANCELLED' and events[-1]['kind']=='CANCELLED'

def test_event_resume(store):
    rid=add(store); Worker(runtime(store),lambda:P).once()
    state,events=store.events_after(P,rid,limit=2)
    _,tail=store.events_after(P,rid,after=events[-1]['seq'])
    assert state=='COMPLETED' and tail[-1]['kind']=='COMPLETED'
    assert len({x['seq'] for x in events+tail})==len(events+tail)

def test_sse_completed(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    path=tmp_path/'events.sqlite'; monkeypatch.setenv('POC_DB',str(path)); monkeypatch.setenv('POC_DEV_TOKEN','a'*24); monkeypatch.delenv('AGENT_DATABASE_URL',raising=False)
    s=Store(path); rid=add(s); Worker(runtime(s),lambda:P).once()
    c=TestClient(create_app()); h={'Authorization':'Bearer '+'a'*24}
    url=f'/api/v1/runs/{rid}/events'
    resp=c.get(url,headers=h); assert resp.status_code==200 and 'event: run_event' in resp.text and 'COMPLETED' in resp.text
    seq=s.events_after(P,rid)[1][-1]['seq']; assert c.get(url,headers={**h,'Last-Event-ID':str(seq)}).text==''
    assert c.get(url).status_code==401

def test_worker_cli_process(tmp_path,monkeypatch):
    import os,sys,subprocess
    path=tmp_path/'process.sqlite'; s=Store(path); rid=add(s)
    env=dict(os.environ,POC_DB=str(path),STONE_ALLOWED_WAREHOUSES='1',POC_LIVE_MODEL='0',STONE_LIVE_DOMAIN='0')
    env.pop('AGENT_DATABASE_URL',None)
    subprocess.run([sys.executable,'-m','agent.worker','--once'],env=env,check=True,timeout=15)
    assert s.get(P,rid)['state']=='COMPLETED'
