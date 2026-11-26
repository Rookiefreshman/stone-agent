from concurrent.futures import ThreadPoolExecutor
import os, uuid
import pytest
from agent.contracts import Principal, Conflict, Forbidden, LostClaim
from agent.operations import OperationService
from agent.receipts import unknown
from agent.rehearsal import RehearsalJournal
from agent.store import Store, RULES
from agent.tools import make_registry, StoneHttpService
from test_operations import setup, approve, P, R

M=Principal(P.tenant,'local-reconciler',frozenset({1}))
F=Principal(P.tenant,'local-fixture',frozenset({1}))

def prepare(store,p=P,r=R):
    op,req,domain=setup(store,p=p)
    d=op.create(p,req); op.review(r,d['operation_id'],approve(d))
    return op,d,domain

@pytest.fixture
def ctx(tmp_path):
    s=Store(tmp_path/'rehearsal.sqlite',RULES); op,d,domain=prepare(s)
    return s,op,d,domain,RehearsalJournal(op,enabled=True)

def begin(ctx):
    _,_,d,_,j=ctx
    return j.begin(P,d['operation_id'],d['payload_hash'])

def due(s,oid):
    with s.tx() as c:c.execute('UPDATE rehearsal_execution SET next_at=0,lease_until=0 WHERE operation_id=?',(oid,))

def test_one_approval_one_simulated_intent(ctx):
    s,op,d,domain,j=ctx; oid=d['operation_id']
    with ThreadPoolExecutor(6) as pool: values=list(pool.map(lambda _:begin(ctx),range(6)))
    assert all(v==values[0] for v in values)
    assert values[0]['state']=='REHEARSAL_UNKNOWN' and values[0]['dispatch_state']=='NEVER_DISPATCHED' and not values[0]['write_allowed']
    assert domain.calls==1
    with pytest.raises(Conflict,match='REHEARSAL_ALREADY_CONSUMED'):op.withdraw(P,oid)
    assert sum(e['kind']=='REHEARSAL_SEND_INTENT' for e in op.get(P,oid)['events'])==1
    assert s.claim_next(P) is None

def test_missing_then_late_receipt_restart(ctx):
    s,op,d,domain,j=ctx; oid=d['operation_id']; begin(ctx)
    assert j.once(M)['state']=='REHEARSAL_UNKNOWN'
    due(s,oid); j.fixture_commit(F,oid); j.fixture_commit(F,oid)
    op2=OperationService(Store(s.path,RULES),op.registry,rehearsal_enabled=True)
    j2=RehearsalJournal(op2,enabled=True)
    out=j2.once(M); assert out['state']=='REHEARSAL_COMMITTED' and out['attempts']==2
    assert out['simulated_receipt']['operationId']==oid and not out['write_allowed'] and out['dispatch_state']=='NEVER_DISPATCHED'
    assert j2.once(M) is None and j2.view(P,oid)==out and domain.calls==1

def test_lost_worker_fence(ctx):
    s,op,d,domain,j=ctx; oid=d['operation_id']; begin(ctx); a=j.claim(M)
    old=j.observe(M,*a); due(s,oid); b=j.claim(M); assert b[1]>a[1]
    with pytest.raises(LostClaim):j.finish(M,*a,old)
    j.fixture_commit(F,oid); assert j.finish(M,*b,j.observe(M,*b))['state']=='REHEARSAL_COMMITTED'

def test_budget_and_unknown(ctx):
    s,op,d,domain,j=ctx; oid=d['operation_id']; begin(ctx)
    for i in range(3):
        due(s,oid); out=j.once(M); assert out['state']=='REHEARSAL_UNKNOWN' and out['attempts']==i+1
    assert out['manual_review']; due(s,oid); assert j.once(M) is None
    j.fixture_commit(F,oid); assert j.once(M) is None and j.view(P,oid)['manual_review']

def test_scope_and_gate(ctx):
    s,op,d,domain,j=ctx; oid=d['operation_id']
    with pytest.raises(Forbidden):RehearsalJournal(op)
    with pytest.raises(Forbidden):RehearsalJournal(OperationService(s,make_registry(StoneHttpService('http://127.0.0.1:9999')),rehearsal_enabled=True),enabled=True)
    with pytest.raises(Conflict,match='PAYLOAD_HASH_MISMATCH'):j.begin(P,oid,'0'*64)
    begin(ctx)
    with pytest.raises(Forbidden):j.claim(P)
    with pytest.raises(Forbidden):j.fixture_commit(P,oid)
    assert j.claim(Principal('other',M.actor,frozenset({1}))) is None
    assert j.claim(Principal(M.tenant,M.actor,frozenset({2}))) is None
    with pytest.raises(Forbidden):j.view(Principal(P.tenant,'other',frozenset({1})),oid)

def test_expiry_and_withdrawal(ctx):
    s,op,d,domain,j=ctx; oid=d['operation_id']
    op.withdraw(P,oid)
    with pytest.raises(Conflict,match='REHEARSAL_APPROVAL_REQUIRED'):begin(ctx)
    with s.tx() as c:c.execute("UPDATE operation_draft SET state='REHEARSAL_APPROVED',expires_at=0 WHERE id=?",(oid,))
    with pytest.raises(Conflict,match='REHEARSAL_APPROVAL_REQUIRED'):begin(ctx)

def test_no_false_receipt(ctx):
    s,op,d,domain,j=ctx; oid=d['operation_id']; begin(ctx); a=j.claim(M)
    j.fixture_commit(F,oid); obs=j.observe(M,*a)
    from agent.receipts import ReceiptObservation
    fake=ReceiptObservation(status='COMMITTED',code='fake',receipt=obs.receipt.model_copy(update={'requestFingerprint':'b'*64}))
    with pytest.raises(Conflict,match='FIXTURE_RECEIPT_MISMATCH'):j.finish(M,*a,fake)
    assert j.finish(M,*a,obs)['state']=='REHEARSAL_COMMITTED'

def test_offline_schema():
    from pathlib import Path
    sql=Path('migrations/0005_agent_head_offline.sql').read_text()
    assert 'CREATE TABLE IF NOT EXISTS rehearsal_execution' in sql and '0005_rehearsal' in sql

@pytest.mark.skipif(not os.getenv('TEST_POSTGRES_DSN'),reason='TEST_POSTGRES_DSN not configured')
def test_pg_rehearsal_claim_and_consume():
    from agent.postgres import PostgresStore
    s=PostgresStore(os.environ['TEST_POSTGRES_DSN'],RULES)
    tenant='rehearsal-'+uuid.uuid4().hex
    p=Principal(tenant,'requester',frozenset({1})); r=Principal(tenant,'reviewer',frozenset({1})); m=Principal(tenant,M.actor,frozenset({1})); f=Principal(tenant,F.actor,frozenset({1}))
    op,d,_=prepare(s,p,r); j=RehearsalJournal(op,enabled=True); oid=d['operation_id']
    with ThreadPoolExecutor(4) as pool: values=list(pool.map(lambda _:j.begin(p,oid,d['payload_hash']),range(4)))
    assert all(v==values[0] for v in values)
    with ThreadPoolExecutor(4) as pool: claims=list(pool.map(lambda _:j.claim(m),range(4)))
    assert len([x for x in claims if x])==1
    a=next(x for x in claims if x); j.fixture_commit(f,oid)
    assert j.finish(m,*a,j.observe(m,*a))['state']=='REHEARSAL_COMMITTED'
