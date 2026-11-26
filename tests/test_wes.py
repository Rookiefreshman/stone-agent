import pytest
from pydantic import ValidationError
from agent.contracts import GoalContract, Principal, Forbidden, DomainReadError, digest
from agent.integrations.wes import WesArgs, WesResult, fixture, analyze
from agent.tools import FixtureStoneService, StoneHttpService, make_registry
from agent.provider import FixtureProvider
from agent.runtime import Runtime
from agent.store import Store, RULES, LEGACY
from test_stone_http import server

P=Principal('demo','local-dev',frozenset({1}))
A=WesArgs(warehouse_id=1,device_code='station-01',requested_limit=8)
PATH='/api/wes/warehouses/1/dispatch-diagnostic?deviceCode=station-01'

def payload(**fields):
    d=fixture(A,'2026-01-01T00:00:00Z').model_dump(); d['snapshot'].update(fields); return d

def diag(**fields): return analyze(WesResult.model_validate(payload(**fields)))
def goal(a=A): return GoalContract(kind='DIAGNOSE_WES_DISPATCH',entity=a,provenance='CONFIRMED_STRUCTURED_INPUT')
def submit(tmp_path,service,entity=A,mode=RULES):
    s=Store(tmp_path/'wes.sqlite',mode); m=FixtureProvider(); r=Runtime(s,m,make_registry(service)); rid=s.submit(P,'unmanaged','wes','confirmed',goal(entity)); return s,m,r,rid

def test_device_full_and_unknown():
    d=diag(); assert d['claim_upper_bound']==0 and d['blocking_factors']==['DEVICE_ACTIVE_SLOTS']
    assert d['warehouse_remaining_raw']==3 and d['device_unknown']==2 and d['warehouse_unknown']==4
    assert not d['write_allowed'] and d['root_cause']=='UNDETERMINED'

@pytest.mark.parametrize('fields,limit,factors',[
    ({'deviceMaxActive':20},3,['WAREHOUSE_ACTIVE_SLOTS']),
    ({'deviceMaxActive':20,'maxActive':30,'maxBatch':2},2,['MAX_BATCH']),
    ({'deviceMaxActive':20,'maxActive':30,'ready':1},1,['READY_TASKS']),
    ({'deviceMaxActive':20,'maxActive':30,'maxBatch':20},8,['REQUEST_LIMIT']),
    ({'deviceMaxActive':20,'maxActive':30},8,['REQUEST_LIMIT','MAX_BATCH']),
    ({'maxActive':0,'maxBatch':0,'ready':0},0,['MAX_BATCH','WAREHOUSE_ACTIVE_SLOTS','DEVICE_ACTIVE_SLOTS','READY_TASKS']),
    ({'maxActive':10,'deviceMaxActive':2},0,['WAREHOUSE_ACTIVE_SLOTS','DEVICE_ACTIVE_SLOTS']),
    ({'maxReady':0,'deviceMaxActive':20},3,['WAREHOUSE_ACTIVE_SLOTS']),
])
def test_limiting_factors(fields,limit,factors):
    d=diag(**fields); assert d['claim_upper_bound']==limit and d['limiting_factors']==factors
    assert d['queue_admission_full']==(d['ready']>=payload(**fields)['snapshot']['maxReady'])

def test_downsize_keeps_negative_raw_slots():
    d=diag(maxActive=10,deviceMaxActive=2); assert d['warehouse_remaining_raw']==-3 and d['device_remaining_raw']==-2
    assert d['claim_upper_bound']==0

@pytest.mark.parametrize('fields',[{'warehouseId':2},{'deviceCode':'other'},{'ready':-1},{'maxBatch':True},{'deviceUnknown':5},{'deviceInFlight':10},{'maxReady':2147483648},{'inFlight':2147483647},{'surprise':1}])
def test_contract_rejects_bad_snapshot(fields):
    with pytest.raises(ValidationError): WesResult.model_validate(payload(**fields))

@pytest.mark.parametrize('fields',[{'requested_limit':0},{'requested_limit':101},{'requested_limit':True},{'device_code':'station?x=1'},{'device_code':'a b'},{'warehouse_id':0}])
def test_invalid_goal_args(fields):
    d=A.model_dump();d.update(fields)
    with pytest.raises(ValidationError): WesArgs.model_validate(d)

def test_goal_requires_typed_entity():
    for k in ['READ_STOCK_WORK','DIAGNOSE_OUTBOUND_ALLOCATION','CLAIM_TASKS']:
        with pytest.raises(ValidationError): GoalContract(kind=k,entity=A,provenance='CONFIRMED_STRUCTURED_INPUT')
    with pytest.raises(ValidationError): GoalContract(kind='DIAGNOSE_WES_DISPATCH',entity={'warehouse_id':1,'task_id':103},provenance='CONFIRMED_STRUCTURED_INPUT')

def test_runtime_and_evidence(tmp_path):
    w=FixtureStoneService();s,m,r,rid=submit(tmp_path,w); out=r.advance(P,rid)['outcome']
    assert out['diagnosis']['claim_upper_bound']==0 and out['evidence_hash']==digest(out['completed_steps'][0]['facts'])
    assert m.calls==0 and w.calls==1 and s.inspect(P,rid)['attempts']==[] and s.invocation(rid)['origin']=='RULE'
    assert r.advance(P,rid)['outcome']==out and w.calls==1

def test_missing_device(tmp_path):
    s,m,r,rid=submit(tmp_path,FixtureStoneService(),WesArgs(warehouse_id=1,device_code='absent',requested_limit=8)); out=r.advance(P,rid)['outcome']
    assert out['goal_status']=='PARTIAL' and out['diagnosis']['claim_upper_bound'] is None

def test_legacy_rejected(tmp_path):
    w=FixtureStoneService();s,m,r,rid=submit(tmp_path,w,mode=LEGACY)
    assert r.advance(P,rid)['outcome']['code']=='RULE_MODE_REQUIRED' and w.calls==m.calls==0

def test_recovery_after_result(tmp_path):
    w=FixtureStoneService();s,m,r,rid=submit(tmp_path,w)
    def crash(stage):
        if stage=='AFTER_READ_RESULT': raise RuntimeError('injected')
    with pytest.raises(RuntimeError): r.advance(P,rid,crash)
    with s.tx() as c: c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))
    assert r.advance(P,rid)['state']=='COMPLETED' and w.calls==1 and m.calls==0

def test_http_rule_integration(tmp_path):
    with server(body={'success':True,'message':'success','data':payload()['snapshot']}) as (url,seen):
        s,m,r,rid=submit(tmp_path,StoneHttpService(url,'test-token')); out=r.advance(P,rid)['outcome']
        assert out['diagnosis']['source']=='STONE_HTTP' and out['diagnosis']['claim_upper_bound']==0
        assert m.calls==0 and seen==[('GET',PATH,'Bearer test-token')]

def test_device_code_encoded_query():
    data=payload(deviceCode='a/b:1')['snapshot']
    with server(body={'success':True,'message':'success','data':data}) as (url,seen):
        args=WesArgs(warehouse_id=1,device_code='a/b:1',requested_limit=8)
        assert StoneHttpService(url,'test').get_wes_dispatch(P,args)['status']=='FOUND'
        assert seen==[('GET','/api/wes/warehouses/1/dispatch-diagnostic?deviceCode=a%2Fb%3A1','Bearer test')]

def test_http_no_token_or_warehouse():
    with server() as (url,seen):
        with pytest.raises(DomainReadError,match='WES_SERVICE_TOKEN_REQUIRED'): StoneHttpService(url).get_wes_dispatch(P,A)
        with pytest.raises(Forbidden): StoneHttpService(url,'test').get_wes_dispatch(P,WesArgs(warehouse_id=2,device_code='station-01',requested_limit=8))
        assert seen==[]

def test_http_not_found_envelope():
    with server(404,{'success':False,'message':'DISPATCH_SCOPE_NOT_FOUND','data':None}) as (url,_):
        assert StoneHttpService(url,'test').get_wes_dispatch(P,A)['status']=='NOT_FOUND'
    with server(404,{'success':False,'message':'other','data':None}) as (url,_):
        with pytest.raises(DomainReadError,match='WES_HTTP_404'): StoneHttpService(url,'test').get_wes_dispatch(P,A)

@pytest.mark.parametrize('status',[401,403,422,503])
def test_http_error(status):
    with server(status,{}) as (url,_):
        with pytest.raises(DomainReadError,match=f'WES_HTTP_{status}'): StoneHttpService(url,'test').get_wes_dispatch(P,A)

def test_http_bad_contract():
    with server(body={'success':True,'message':'success','data':payload(deviceCode='other')['snapshot']}) as (url,_):
        with pytest.raises(DomainReadError,match='WES_CONTRACT_ERROR'): StoneHttpService(url,'test').get_wes_dispatch(P,A)

def test_http_redirect():
    with server() as (dest,dest_seen),server(302,{},dest+'/other') as (url,seen):
        with pytest.raises(DomainReadError,match='WES_HTTP_302'): StoneHttpService(url,'test').get_wes_dispatch(P,A)
        assert not dest_seen and len(seen)==1

def test_api_structured_goal(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    monkeypatch.setenv('POC_DEV_TOKEN','test-token-long-enough');monkeypatch.setenv('POC_DB',str(tmp_path/'api.sqlite'));monkeypatch.setenv('STONE_ALLOWED_WAREHOUSES','1')
    for key in ('AGENT_DATABASE_URL','POC_LIVE_MODEL','STONE_LIVE_DOMAIN'): monkeypatch.delenv(key,raising=False)
    c=TestClient(create_app()); headers={'Authorization':'Bearer test-token-long-enough'}
    data={'client_request_id':'wes','message':'confirmed','goal':goal().model_dump()}
    response=c.post('/api/v1/sessions/unmanaged/runs',headers=headers,json=data); assert response.status_code==202
    rid=response.json()['run_id']; assert c.post(f'/api/v1/runs/{rid}/advance',headers=headers).json()['outcome']['diagnosis']['claim_upper_bound']==0
