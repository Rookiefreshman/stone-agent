import json
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor
import pytest
from agent.contracts import Principal, GoalContract, StockWorkArgs, Conflict, Forbidden, digest, canonical, strict_json
from agent.operations import OperationService, DraftRequest, ReviewRequest
from agent.store import Store, RULES
from agent.runtime import Runtime
from agent.provider import FixtureProvider
from agent.tools import FixtureStoneService, make_registry, StoneHttpService

P=Principal('demo','local-dev',frozenset({1}))
R=Principal('demo','local-reviewer',frozenset({1}))
A=StockWorkArgs(warehouse_id=1,task_id=101)

def setup(s,p=P,args=A,domain=None):
    domain=domain or FixtureStoneService()
    rt=Runtime(s,FixtureProvider(),make_registry(domain))
    goal=GoalContract(kind='READ_STOCK_WORK',entity=args,provenance='CONFIRMED_STRUCTURED_INPUT')
    rid=s.submit(p,'unmanaged','read-'+str(args.task_id),'read',goal)
    rt.advance(p,rid)
    op=OperationService(s,rt.registry,rehearsal_enabled=True)
    req=DraftRequest(**args.model_dump(),run_id=rid,client_request_id='draft',reason='取消重复申请')
    return op,req,domain

@pytest.fixture
def ctx(tmp_path):
    s=Store(tmp_path/'op.sqlite',RULES); op,req,domain=setup(s)
    return s,op,req,domain

def approve(d,decision='APPROVE_REHEARSAL'):
    return ReviewRequest(payload_hash=d['payload_hash'],decision=decision)

def test_ready_draft_approval_and_withdraw(ctx):
    s,op,req,domain=ctx
    assert op.preflight(P,req.run_id,A)['eligible_for_draft']
    d=op.create(P,req); assert d['state']=='AWAITING_REVIEW' and d['payload']['task']['state']=='READY'
    assert d['payload_hash']==digest(d['payload']) and not d['write_allowed']
    a=op.review(R,d['operation_id'],approve(d)); assert a['state']=='REHEARSAL_APPROVED' and a['dispatch_state']=='NEVER_DISPATCHED'
    assert not a['write_allowed'] and domain.calls==1
    assert op.review(R,d['operation_id'],approve(d))==a
    assert op.withdraw(P,d['operation_id'])['state']=='WITHDRAWN'
    assert op.get(P,d['operation_id'])['review_decision']=='APPROVE_REHEARSAL'
    assert domain.calls==1
    with s.tx() as c: assert c.execute('SELECT COUNT(*) FROM run').fetchone()[0]==1

@pytest.mark.parametrize('task,state',[(102,'IN_FLIGHT'),(103,'UNKNOWN'),(104,'SUCCEEDED'),(105,'FAILED'),(106,'CANCELLED'),(107,'NOT_FOUND')])
def test_only_ready_eligible(tmp_path,task,state):
    args=StockWorkArgs(warehouse_id=1,task_id=task); op,req,_=setup(Store(tmp_path/'op.sqlite',RULES),args=args)
    assert op.preflight(P,req.run_id,args)['observed_state']==state
    assert not op.preflight(P,req.run_id,args)['eligible_for_draft']
    with pytest.raises(Conflict,match='NOT_READY_FOR_DRAFT'): op.create(P,req)

def test_idempotent_concurrent_create_and_review(ctx):
    s,op,req,_=ctx
    with ThreadPoolExecutor(6) as ex: ds=list(ex.map(lambda _:op.create(P,req),range(6)))
    assert len({d['operation_id'] for d in ds})==1
    d=ds[0]; oid=d['operation_id']
    with ThreadPoolExecutor(6) as ex: results=list(ex.map(lambda _:op.review(R,oid,approve(d)),range(6)))
    assert all(x['state']=='REHEARSAL_APPROVED' for x in results)
    assert len(op.get(P,oid)['events'])==2

def test_idempotency_conflict(ctx):
    s,op,req,_=ctx; op.create(P,req)
    with pytest.raises(Conflict,match='IDEMPOTENCY_CONFLICT'): op.create(P,req.model_copy(update={'reason':'other'}))

def test_mismatch_and_cross_tenant(ctx):
    s,op,req,_=ctx
    with pytest.raises(Conflict,match='READ_GOAL_MISMATCH'): op.create(P,req.model_copy(update={'task_id':103}))
    with pytest.raises(Forbidden): op.create(Principal('other','local-dev',frozenset({1})),req)
    d=op.create(P,req)
    with pytest.raises(Forbidden): op.get(Principal('other','local-dev',frozenset({1})),d['operation_id'])
    with pytest.raises(Forbidden): op.get(Principal('demo','other-user',frozenset({1})),d['operation_id'])
    with pytest.raises(Forbidden): op.review(Principal('other','local-reviewer',frozenset({1})),d['operation_id'],approve(d))

def test_warehouse_revocation(ctx):
    s,op,req,_=ctx; d=op.create(P,req)
    with pytest.raises(Forbidden): op.get(Principal(P.tenant,P.actor,frozenset()),d['operation_id'])
    with pytest.raises(Forbidden): op.review(Principal(R.tenant,R.actor,frozenset()),d['operation_id'],approve(d))

def test_self_review_and_hash_tamper(ctx):
    s,op,req,_=ctx; d=op.create(P,req)
    with pytest.raises(Forbidden,match='SELF_REVIEW'): op.review(P,d['operation_id'],approve(d))
    with pytest.raises(Conflict,match='PAYLOAD_HASH_MISMATCH'): op.review(R,d['operation_id'],ReviewRequest(payload_hash='0'*64,decision='APPROVE_REHEARSAL'))
    with s.tx() as c: c.execute('UPDATE operation_draft SET payload=? WHERE id=?',('{}',d['operation_id']))
    with pytest.raises(Conflict,match='DRAFT_INTEGRITY_ERROR'): op.get(P,d['operation_id'])

def test_stale_and_future_evidence(ctx):
    s,op,req,_=ctx
    for dt in (-121,30):
        with s.tx() as c:
            raw=strict_json(c.execute('SELECT result FROM invocation WHERE run_id=?',(req.run_id,)).fetchone()[0]); raw['observed_at']=(datetime.now(timezone.utc)+timedelta(seconds=dt)).isoformat()
            c.execute('UPDATE invocation SET result=?,evidence_hash=? WHERE run_id=?',(canonical(raw),digest(raw),req.run_id))
        with pytest.raises(Conflict,match='READ_EVIDENCE_STALE'): op.create(P,req)

def test_evidence_tamper(ctx):
    s,op,req,_=ctx
    with s.tx() as c: c.execute('UPDATE invocation SET evidence_hash=? WHERE run_id=?',('0'*64,req.run_id))
    with pytest.raises(Conflict,match='READ_EVIDENCE_INVALID'): op.create(P,req)

def test_binding_change(ctx):
    s,op,req,_=ctx; d=op.create(P,req)
    changed=OperationService(s,make_registry(StoneHttpService('http://127.0.0.1:9999')),rehearsal_enabled=True)
    with pytest.raises(Conflict,match='READ_BINDING_CHANGED'): changed.create(P,req.model_copy(update={'client_request_id':'another'}))
    with pytest.raises(Conflict,match='READ_BINDING_CHANGED'): changed.review(R,d['operation_id'],approve(d))

def test_expiry_and_replay(ctx):
    s,op,req,_=ctx; d=op.create(P,req); oid=d['operation_id']
    with s.tx() as c: c.execute('UPDATE operation_draft SET expires_at=0 WHERE id=?',(oid,))
    assert op.get(P,oid)['state']=='EXPIRED' and op.create(P,req)['state']=='EXPIRED'
    with pytest.raises(Conflict,match='DRAFT_NOT_REVIEWABLE'): op.review(R,oid,approve(d))

def test_reject_and_revoke(ctx):
    s,op,req,_=ctx; d=op.create(P,req); oid=d['operation_id']
    assert op.review(R,oid,approve(d,'REJECT'))['state']=='REJECTED'
    with pytest.raises(Conflict,match='DRAFT_NOT_REVIEWABLE'): op.review(R,oid,approve(d))
    d=op.create(P,req.model_copy(update={'client_request_id':'second'}));oid=d['operation_id']
    op.withdraw(P,oid)
    with pytest.raises(Conflict,match='DRAFT_NOT_REVIEWABLE'): op.review(R,oid,approve(d))

def test_reopen_and_no_worker_write(ctx):
    s,op,req,domain=ctx;d=op.create(P,req)
    reopened=OperationService(Store(s.path,RULES),make_registry(domain),rehearsal_enabled=True)
    assert reopened.get(P,d['operation_id'])==d
    assert reopened.review(R,d['operation_id'],approve(d))['dispatch_state']=='NEVER_DISPATCHED'
    assert s.claim_next(P) is None
    assert domain.calls==1

def test_live_approval_rejected(tmp_path):
    from test_stone_http import server
    from agent.tools import FixtureStoneService
    raw=FixtureStoneService().get_detail(P,A); raw={k:raw[k] for k in ('task','ledger')}
    with server(body={'success':True,'message':'success','data':raw}) as (url,seen):
        op,req,_=setup(Store(tmp_path/'op.sqlite',RULES),domain=StoneHttpService(url,'test'))
        d=op.create(P,req); assert d['payload']['source']=='STONE_HTTP'
        with pytest.raises(Forbidden,match='LIVE_APPROVAL_DISABLED'): op.review(R,d['operation_id'],approve(d))
        assert len(seen)==1 and seen[0][0]=='GET'

def test_disabled_rehearsal(ctx):
    s,op,req,_=ctx; d=op.create(P,req)
    op.rehearsal_enabled=False
    with pytest.raises(Forbidden,match='REHEARSAL_DISABLED'): op.review(R,d['operation_id'],approve(d))

def test_api_separate_reviewer(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    for key in ('AGENT_DATABASE_URL','POC_LIVE_MODEL','STONE_LIVE_DOMAIN'): monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('POC_DEV_TOKEN','requester-test-token');monkeypatch.setenv('POC_REVIEW_TOKEN','reviewer-test-token');monkeypatch.setenv('POC_REHEARSAL_ENABLED','1');monkeypatch.setenv('POC_REVIEW_WAREHOUSES','1');monkeypatch.setenv('STONE_ALLOWED_WAREHOUSES','1');monkeypatch.setenv('POC_DB',str(tmp_path/'op.sqlite'))
    client=TestClient(create_app()); user={'Authorization':'Bearer requester-test-token'}; reviewer={'Authorization':'Bearer reviewer-test-token'}
    g=GoalContract(kind='READ_STOCK_WORK',entity=A,provenance='CONFIRMED_STRUCTURED_INPUT')
    res=client.post('/api/v1/sessions/unmanaged/runs',headers=user,json={'client_request_id':'read','message':'read','goal':g.model_dump()});assert res.status_code==202
    rid=res.json()['run_id']; assert client.post(f'/api/v1/runs/{rid}/advance',headers=user).status_code==200
    assert client.get(f'/api/v1/operations/preflight/{rid}?warehouse_id=1&task_id=101',headers=user).json()['eligible_for_draft']
    req=dict(run_id=rid,client_request_id='draft',warehouse_id=1,task_id=101,reason='test');r=client.post('/api/v1/operations/cancel-drafts',headers=user,json=req);assert r.status_code==201
    d=r.json();oid=d['operation_id'];path=f'/api/v1/rehearsal/reviews/{oid}'
    assert client.post(path,headers=user,json=approve(d).model_dump()).status_code==403
    assert client.get(path,headers=reviewer).status_code==200
    assert client.post(path,headers=reviewer,json=approve(d).model_dump()).json()['state']=='REHEARSAL_APPROVED'
    assert client.post(f'/api/v1/operations/{oid}/execute',headers=user).status_code==404
    assert client.get(f'/api/v1/operations/{oid}',headers=reviewer).status_code==401


def test_offline_operation_migration():
    from pathlib import Path
    sql=Path('migrations/0004_agent_head_offline.sql').read_text()
    assert 'CREATE TABLE IF NOT EXISTS operation_draft' in sql and '0004_cancel_drafts' in sql

@pytest.mark.skipif(not __import__('os').getenv('TEST_POSTGRES_DSN'),reason='TEST_POSTGRES_DSN not configured')
def test_pg_draft_review_serialization():
    import os,uuid
    from agent.postgres import PostgresStore
    s=PostgresStore(os.environ['TEST_POSTGRES_DSN'],RULES)
    p=Principal('op-test-'+uuid.uuid4().hex,'requester',frozenset({1})); r=Principal(p.tenant,'reviewer',frozenset({1}))
    op,req,_=setup(s,p=p)
    with ThreadPoolExecutor(4) as ex: ds=list(ex.map(lambda _:op.create(p,req),range(4)))
    assert len({d['operation_id'] for d in ds})==1
    d=ds[0]; oid=d['operation_id']
    with ThreadPoolExecutor(4) as ex: result=list(ex.map(lambda _:op.review(r,oid,approve(d)),range(4)))
    assert all(x['state']=='REHEARSAL_APPROVED' and x['dispatch_state']=='NEVER_DISPATCHED' for x in result)
    assert len(op.get(p,oid)['events'])==2
