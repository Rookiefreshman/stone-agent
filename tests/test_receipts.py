import copy, json
import pytest
from pydantic import ValidationError
from agent.contracts import Principal, Forbidden, Conflict, digest
from agent.receipts import CancelCommand, ReceiptKey, Receipt, ReceiptObservation, ReceiptReader, ReconcileCheckpoint, unknown
from test_stone_http import server

P=Principal('demo','maintenance',frozenset({1}))
CMD=CancelCommand(operation_id='00000000-0000-0000-0000-000000000001',warehouse_id=1,task_id=101,tenant='demo',actor='requester',task_key='task-101',work_type='TRANSFER',sku_id=8,lot_no='L01',source_id=10,destination_id=20,quantity=12,reason='重复申请',approval_hash='a'*64)
KEY=ReceiptKey(operation_id=CMD.operation_id,warehouse_id=1,task_id=101,request_fingerprint=CMD.fingerprint())
DATA=dict(operationId=KEY.operation_id,warehouseId=1,taskId=101,requestFingerprint=KEY.request_fingerprint,businessState='CANCELLED',recordedAt='2026-01-01T00:00:00.000001Z')
PATH='/api/wms/warehouses/1/cancel-operation-receipts/'+KEY.operation_id

def envelope(data=DATA):return {'success':True,'message':'success','data':data}
def committed(data=DATA):return ReceiptObservation(status='COMMITTED',code='MATCHING_RECEIPT',receipt=Receipt.model_validate(data))

def test_fingerprint_golden():
    assert CMD.fingerprint()=='f384e17fa1a042bfb31974d2e40c8ade060258660a4bd2102da9d70b20fd68fe'
    assert CMD.model_copy(update={'reason':'other'}).fingerprint()!=CMD.fingerprint()
    assert CMD.model_copy(update={'task_key':'task-102'}).fingerprint()!=CMD.fingerprint()

def test_get_only_matching_receipt():
    with server(body=envelope()) as (url,seen):
        o=ReceiptReader(url,'test-read-token').query(P,KEY)
        assert o.status=='COMMITTED' and seen==[('GET',PATH,'Bearer test-read-token')]

@pytest.mark.parametrize('change',[{'operationId':'00000000-0000-0000-0000-000000000002'},{'warehouseId':2},{'taskId':102},{'requestFingerprint':'b'*64}])
def test_wrong_receipt_is_unknown(change):
    with server(body=envelope(dict(DATA,**change))) as (url,_):
        assert ReceiptReader(url,'test').query(P,KEY).code=='RECEIPT_IDENTITY_MISMATCH'

@pytest.mark.parametrize('change',[{'businessState':'READY'},{'recordedAt':'2026-01-01T00:00:00'},{'taskId':True},{'other':'extra'}])
def test_malformed_receipt_is_unknown(change):
    with server(body=envelope(dict(DATA,**change))) as (url,_):assert ReceiptReader(url,'test').query(P,KEY).status=='UNKNOWN'

@pytest.mark.parametrize('status,body,code',[(404,{'success':False,'message':'OPERATION_RECEIPT_NOT_FOUND','data':None},'RECEIPT_NOT_OBSERVED'),(404,{},'RECEIPT_HTTP_404'),(403,{},'RECEIPT_HTTP_403'),(503,{},'RECEIPT_HTTP_503')])
def test_absence_and_transport_not_proof(status,body,code):
    with server(status,body) as (url,_):
        o=ReceiptReader(url,'test').query(P,KEY);assert o.status=='UNKNOWN' and o.code==code

def test_no_redirect_or_cross_warehouse():
    with server() as (dest,ds),server(302,{},dest+'/evil') as (url,seen):
        r=ReceiptReader(url,'test');assert r.query(P,KEY).status=='UNKNOWN' and not ds
        with pytest.raises(Forbidden):r.query(Principal('demo','maintenance',frozenset()),KEY)
        assert len(seen)==1

def test_unknown_checkpoint_roundtrip_and_late_receipt(tmp_path):
    # Reconstruct from a serialized, synthetic checkpoint after response loss.
    cp=ReconcileCheckpoint(key=KEY);f=tmp_path/'checkpoint.json';f.write_text(cp.model_dump_json())
    loaded=ReconcileCheckpoint.model_validate_json(f.read_text());assert loaded.apply(unknown('RECEIPT_NOT_OBSERVED'))==cp
    done=loaded.apply(committed());assert done.state=='COMMITTED' and done.evidence_hash==digest(DATA)
    f.write_text(done.model_dump_json());restored=ReconcileCheckpoint.model_validate_json(f.read_text())
    assert restored.apply(unknown('RECEIPT_HTTP_503'))==restored and restored.apply(committed())==restored
    with pytest.raises(Conflict,match='COMMITTED_RECEIPT_CHANGED'):restored.apply(committed(dict(DATA,recordedAt='2026-01-02T00:00:00Z')))
    with pytest.raises(Conflict,match='RECEIPT_IDENTITY_MISMATCH'):loaded.apply(committed(dict(DATA,taskId=102)))

def test_tampered_checkpoint_and_rehearsal_not_convertible():
    cp=ReconcileCheckpoint(key=KEY).apply(committed()).model_dump();cp['evidence_hash']='0'*64
    with pytest.raises(ValidationError):ReconcileCheckpoint.model_validate(cp)
    with pytest.raises(ValidationError):ReconcileCheckpoint.model_validate({'state':'REHEARSAL_APPROVED','operation_id':KEY.operation_id})

def test_no_writer_or_model_registry():
    from agent.tools import make_registry,FixtureStoneService
    assert not any('receipt' in x or 'cancel' in x for x in make_registry(FixtureStoneService()).snapshot())
    assert not hasattr(ReceiptReader,'execute') and not hasattr(ReceiptReader,'submit')
