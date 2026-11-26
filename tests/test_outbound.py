import copy
import pytest
from pydantic import ValidationError
from agent.contracts import GoalContract, Principal, Forbidden, DomainReadError, digest
from agent.integrations.outbound import OutboundArgs, OutboundResult, fixture, analyze
from agent.tools import FixtureStoneService, StoneHttpService, make_registry
from agent.provider import FixtureProvider
from agent.runtime import Runtime
from agent.store import Store, RULES, LEGACY
from test_stone_http import server

P=Principal('demo','local-dev',frozenset({1}))
A=OutboundArgs(warehouse_id=1,order_id=201)
PATH='/api/wms/warehouses/1/outbound-orders/201/allocation-diagnostic'

def payload():
    return fixture(A,'2026-01-01T00:00:00Z').model_dump()

def snapshot(**updates):
    data=payload(); data['snapshot'].update(updates); return data

def diagnosis(data): return analyze(OutboundResult.model_validate(data))

def goal(args=A): return GoalContract(kind='DIAGNOSE_OUTBOUND_ALLOCATION',entity=args,provenance='CONFIRMED_STRUCTURED_INPUT')

def submit(tmp_path,service,entity=A,mode=RULES):
    s=Store(tmp_path/'outbound.sqlite',mode); m=FixtureProvider(); r=Runtime(s,m,make_registry(service)); rid=s.submit(P,'unmanaged','order','confirmed',goal(entity)); return s,m,r,rid

def test_aggregate_two_lines():
    out=diagnosis(payload()); assert out['assessment']=='CURRENT_BLOCKERS'
    assert out['groups'][0]['required_qty']==11 and out['groups'][0]['shortage_qty']==1
    assert out['historical_root_cause']=='UNDETERMINED' and not out['write_allowed']

def test_same_inventory_id_collation():
    data=payload(); data['snapshot']['lines'][1]['lotNo']='l01'
    assert diagnosis(data)['groups'][0]['required_qty']==11

def test_separate_dimensions():
    data=payload(); data['snapshot']['lines'][1].update(locationId=11,inventoryId=100)
    out=diagnosis(data); assert out['assessment']=='NO_CURRENT_BLOCKER_OBSERVED' and len(out['groups'])==2 and not out['write_allowed']

def test_receipt_precedes_state():
    out=diagnosis(snapshot(orderStatus='ALLOCATED',receiptPresent=True)); assert out['assessment']=='ALLOCATION_RECEIPT_EXISTS' and not out['write_allowed']

def test_order_not_approved():
    assert 'ORDER_STATUS_NOT_APPROVED' in [x['code'] for x in diagnosis(snapshot(orderStatus='DRAFT'))['blockers']]

def test_frozen_source():
    data=payload();
    for x in data['snapshot']['lines']: x['locationState']='FROZEN'
    assert 'SOURCE_LOCATION_FROZEN' in [x['code'] for x in diagnosis(data)['blockers']]

def test_unconfigured_not_frozen():
    data=payload();
    for x in data['snapshot']['lines']: x.update(locationState='UNCONFIGURED',orderedQty=4)
    assert diagnosis(data)['assessment']=='NO_CURRENT_BLOCKER_OBSERVED'

def test_missing_dimension_is_not_zero():
    data=payload();
    for x in data['snapshot']['lines']:
        for k in ('inventoryId','onHandQty','availableQty','reservedQty','frozenQty','inventoryVersion'): x[k]=None
    out=diagnosis(data); assert out['groups']==[] and all(x['code']=='EXACT_AVAILABLE_INVENTORY_MISSING' for x in out['blockers'])

def test_zero_balance():
    data=payload()
    for x in data['snapshot']['lines']: x.update(onHandQty=5,availableQty=0)
    assert diagnosis(data)['groups'][0]['shortage_qty']==11

def test_fully_allocated_line():
    data=payload();data['snapshot']['lines'][0]['allocatedQty']=6
    assert 'LINE_ALREADY_ALLOCATED' in [x['code'] for x in diagnosis(data)['blockers']]

@pytest.mark.parametrize('change',[lambda d:d['snapshot'].update(warehouseId=2),lambda d:d['snapshot'].update(orderId=202),lambda d:d['snapshot']['lines'].append(copy.deepcopy(d['snapshot']['lines'][0])),lambda d:d['snapshot']['lines'][0].update(allocatedQty=7),lambda d:d['snapshot']['lines'][0].update(availableQty=11),lambda d:d['snapshot']['lines'][0].update(inventoryId=100),lambda d:d['snapshot']['lines'][0].update(locationState='FROZEN'),lambda d:d['snapshot']['lines'][0].update(inventoryVersion=4),lambda d:d['snapshot']['lines'][0].update(orderedQty=True),lambda d:d['snapshot'].update(receiptPresent=1)])
def test_contract_rejects_bad_snapshot(change):
    data=payload();change(data)
    with pytest.raises(ValidationError): OutboundResult.model_validate(data)

def test_goal_typed_and_no_write():
    with pytest.raises(ValidationError): GoalContract(kind='READ_STOCK_WORK',entity=A,provenance='CONFIRMED_STRUCTURED_INPUT')
    with pytest.raises(ValidationError): GoalContract(kind='DIAGNOSE_OUTBOUND_ALLOCATION',entity={'warehouse_id':1,'task_id':201},provenance='CONFIRMED_STRUCTURED_INPUT')
    with pytest.raises(ValidationError): GoalContract(kind='ALLOCATE_OUTBOUND',entity=A,provenance='CONFIRMED_STRUCTURED_INPUT')

def test_runtime_ledger(tmp_path):
    w=FixtureStoneService();s,m,r,rid=submit(tmp_path,w); out=r.advance(P,rid)['outcome']
    assert out['diagnosis']['groups'][0]['shortage_qty']==1 and out['evidence_hash']==digest(out['completed_steps'][0]['facts'])
    assert m.calls==0 and w.calls==1 and s.inspect(P,rid)['attempts']==[] and s.invocation(rid)['origin']=='RULE'
    assert r.advance(P,rid)['outcome']==out and w.calls==1

def test_missing_order(tmp_path):
    s,m,r,rid=submit(tmp_path,FixtureStoneService(),OutboundArgs(warehouse_id=1,order_id=999)); out=r.advance(P,rid)['outcome']; assert out['goal_status']=='PARTIAL' and out['diagnosis']['assessment']=='NOT_FOUND'

def test_legacy_rejected(tmp_path):
    w=FixtureStoneService();s,m,r,rid=submit(tmp_path,w,mode=LEGACY); assert r.advance(P,rid)['outcome']['code']=='RULE_MODE_REQUIRED' and w.calls==m.calls==0

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
        assert out['diagnosis']['source']=='STONE_HTTP' and out['diagnosis']['groups'][0]['shortage_qty']==1
        assert m.calls==0 and seen==[('GET',PATH,'Bearer test-token')]

def test_http_no_token_or_warehouse(tmp_path):
    with server() as (url,seen):
        with pytest.raises(DomainReadError,match='OUTBOUND_SERVICE_TOKEN_REQUIRED'): StoneHttpService(url).get_outbound(P,A)
        with pytest.raises(Forbidden): StoneHttpService(url,'test-token').get_outbound(P,OutboundArgs(warehouse_id=2,order_id=201))
        assert seen==[]

def test_http_not_found_envelope():
    with server(404,{'success':False,'message':'ORDER_NOT_FOUND','data':None}) as (url,_): assert StoneHttpService(url,'test').get_outbound(P,A)['status']=='NOT_FOUND'
    with server(404,{'success':False,'message':'other','data':None}) as (url,_):
        with pytest.raises(DomainReadError,match='OUTBOUND_HTTP_404'): StoneHttpService(url,'test').get_outbound(P,A)

@pytest.mark.parametrize('status',[401,403,422,503])
def test_http_error(status):
    with server(status,{}) as (url,_):
        with pytest.raises(DomainReadError,match=f'OUTBOUND_HTTP_{status}'): StoneHttpService(url,'test').get_outbound(P,A)

def test_http_redirect():
    with server() as (dest,dest_seen),server(302,{},dest+'/other') as (url,seen):
        with pytest.raises(DomainReadError,match='OUTBOUND_HTTP_302'): StoneHttpService(url,'test').get_outbound(P,A)
        assert not dest_seen and len(seen)==1

def test_api_structured_goal(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    monkeypatch.setenv('POC_DEV_TOKEN','test-token-long-enough');monkeypatch.setenv('POC_DB',str(tmp_path/'api.sqlite'));monkeypatch.setenv('STONE_ALLOWED_WAREHOUSES','1')
    for key in ('AGENT_DATABASE_URL','POC_LIVE_MODEL','STONE_LIVE_DOMAIN'): monkeypatch.delenv(key,raising=False)
    c=TestClient(create_app()); headers={'Authorization':'Bearer test-token-long-enough'}
    data={'client_request_id':'outbound','message':'confirmed','goal':goal().model_dump()}
    response=c.post('/api/v1/sessions/unmanaged/runs',headers=headers,json=data); assert response.status_code==202
    rid=response.json()['run_id']; assert c.post(f'/api/v1/runs/{rid}/advance',headers=headers).json()['outcome']['diagnosis']['groups'][0]['shortage_qty']==1
