import json,threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from contextlib import contextmanager
import pytest
from agent.contracts import StockWorkArgs,Principal,DomainReadError,Forbidden,GoalContract
from agent.tools import StoneHttpService,make_registry
from agent.runtime import Runtime
from agent.provider import FixtureProvider
from agent.store import Store

P=Principal('demo','u',frozenset({1})); A=StockWorkArgs(warehouse_id=1,task_id=103)
def payload(task=103,state='UNKNOWN'):
    return {'success':True,'message':'success','data':{'task':{'id':task,'taskKey':'MOVE:103','priority':10,'state':state,'deviceCode':'private-device','claimKey':'private-claim','work':{'type':'TRANSFER','receiptKey':None,'skuId':8,'lotNo':'L01','sourceId':10,'destinationId':20,'quantity':12}},'ledger':[{'locationId':10,'action':'TRANSFER_RESERVE','deltaOnHand':0,'deltaAvailable':-12,'deltaReserved':12,'deltaFrozen':0}]}}
@contextmanager
def server(status=200,body=None,redirect=None):
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.command,self.path,self.headers.get('Authorization'))); self.send_response(status)
            if redirect: self.send_header('Location',redirect)
            self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(json.dumps(body if body is not None else payload()).encode())
        def do_POST(self):
            seen.append(('POST',self.path,None)); self.send_error(405)
        def log_message(self,*args): pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler); t=threading.Thread(target=http.serve_forever,daemon=True); t.start()
    try: yield f'http://127.0.0.1:{http.server_port}',seen
    finally: http.shutdown(); http.server_close(); t.join()

def test_http_end_to_end(tmp_path):
    with server() as (url,seen):
        r=Runtime(Store(tmp_path/'live.sqlite'),FixtureProvider(),make_registry(StoneHttpService(url,'test-token')))
        g=GoalContract(kind='READ_STOCK_WORK',entity=A,provenance='CONFIRMED_STRUCTURED_INPUT'); rid=r.store.submit(P,'s','k','查询库存作业状态',g); result=r.advance(P,rid)
        assert result['state']=='COMPLETED'; f=result['outcome']['facts']; assert f['source']=='STONE_HTTP'; assert f['task']['state']=='UNKNOWN'; assert f['ledger'][0]['deltaReserved']==12
        assert 'private-claim' not in json.dumps(result); assert 'private-device' not in json.dumps(result)
        assert seen==[('GET','/api/wms/warehouses/1/stock-work/103','Bearer test-token')]
        assert r.advance(P,rid)==result and len(seen)==1

@pytest.mark.parametrize('status,body,code',[(200,{'success':False,'data':None,'message':'error'},'UPSTREAM_BUSINESS_ERROR'),(409,{'success':False},'UPSTREAM_HTTP_409'),(404,{'message':'not found'},'UPSTREAM_HTTP_404'),(200,payload(task=999),'UPSTREAM_CONTRACT_ERROR'),(200,payload(state='BOGUS'),'UPSTREAM_CONTRACT_ERROR'),(503,{},'UPSTREAM_HTTP_503')])
def test_upstream_errors(status,body,code):
    with server(status,body) as (url,seen):
        with pytest.raises(DomainReadError,match=code): StoneHttpService(url).get_detail(P,A)
        assert len(seen)==1

def test_authorized_not_found():
    with server(404,{'success':False,'message':'库存任务不存在','data':None}) as (url,seen):
        assert StoneHttpService(url).get_detail(P,A)['status']=='NOT_FOUND'

def test_warehouse_access_before_network():
    with server() as (url,seen):
        with pytest.raises(Forbidden): StoneHttpService(url).get_detail(P,StockWorkArgs(warehouse_id=2,task_id=103))
        assert not seen

def test_no_redirect():
    with server() as (dest,dest_seen):
        with server(302,{},dest+'/arbitrary') as (url,seen):
            with pytest.raises(DomainReadError,match='UPSTREAM_HTTP_302'): StoneHttpService(url).get_detail(P,A)
            assert not dest_seen

def test_http_analysis_calls_only_one_real_endpoint(tmp_path):
    data=payload(); data['data']['ledger'].append({'locationId':10,'action':'TRANSFER_RELEASE','deltaOnHand':0,'deltaAvailable':3,'deltaReserved':-3,'deltaFrozen':0})
    data['data']['ledger'].append({'locationId':20,'action':'EXAMPLE','deltaOnHand':4,'deltaAvailable':4,'deltaReserved':0,'deltaFrozen':0})
    with server(body=data) as (url,seen):
        r=Runtime(Store(tmp_path/'analysis.sqlite'),FixtureProvider(),make_registry(StoneHttpService(url,'test-token')))
        g=GoalContract(kind='ANALYZE_STOCK_WORK',entity=A,provenance='CONFIRMED_STRUCTURED_INPUT'); rid=r.store.submit(P,'s','analysis','confirmed analysis',g); out=r.advance(P,rid)['outcome']
        assert out['diagnosis']['root_cause']=='UNDETERMINED'; assert out['diagnosis']['source']=='STONE_HTTP'
        summary=out['ledger_summary']; assert summary['ledger_entries']==3
        assert summary['movements']==[{'locationId':10,'deltaOnHand':0,'deltaAvailable':-9,'deltaReserved':9,'deltaFrozen':0,'entries':2},{'locationId':20,'deltaOnHand':4,'deltaAvailable':4,'deltaReserved':0,'deltaFrozen':0,'entries':1}]
        assert seen==[('GET','/api/wms/warehouses/1/stock-work/103','Bearer test-token')]
        assert 'private-claim' not in json.dumps(out)
