import json, threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import pytest
from agent.contracts import Principal,StockWorkArgs,GoalContract,DomainReadError,Forbidden,digest
from agent.runtime import Runtime
from agent.store import Store,RULES
from agent.provider import FixtureProvider
from agent.tools import FixtureStoneService,StoneHttpService,make_registry
from test_stone_http import payload
P=Principal('demo','u',frozenset({1})); A=StockWorkArgs(warehouse_id=1,task_id=103)
DETAIL='/api/wms/warehouses/1/stock-work/103'; EVENTS='/api/wes/warehouses/1/tasks/103/events'
def response(state='UNKNOWN', events=None):
    return {'success':True,'message':'success','data':{'warehouseId':1,'taskId':103,'taskState':state,'truncated':False,'events':events if events is not None else [{'eventKey':'evt1','targetState':'UNKNOWN','reconciled':False,'reason':'设备上报：等待回执超时','completedQty':4,'createdAt':'2026-01-01T00:00:00.000000'}]}}
@contextmanager
def server(history=None,status=200,detail=None,redirect=None):
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.command,self.path,self.headers.get('Authorization')))
            d=self.path==DETAIL; self.send_response(200 if d else status)
            if not d and redirect: self.send_header('Location',redirect)
            self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(json.dumps((detail or payload()) if d else (history if history is not None else response())).encode())
        def log_message(self,*a): pass
    srv=ThreadingHTTPServer(('127.0.0.1',0),Handler); t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
    try: yield f'http://127.0.0.1:{srv.server_port}',seen
    finally: srv.shutdown();srv.server_close();t.join()
def setup(tmp_path,service,task=103,mode=RULES):
    s=Store(tmp_path/'a.db',mode); m=FixtureProvider();r=Runtime(s,m,make_registry(service));g=GoalContract(kind='DEEP_DIAGNOSE_STOCK_WORK',entity=StockWorkArgs(warehouse_id=1,task_id=task),provenance='CONFIRMED_STRUCTURED_INPUT');rid=s.submit(P,'s','k','confirmed',g);return s,m,r,rid

def test_two_gets_evidence(tmp_path):
    with server() as (url,seen):
        s,m,r,rid=setup(tmp_path,StoneHttpService(url,'dev-token'));out=r.advance(P,rid)['outcome'];h=out['history_analysis']
        assert out['goal_status']=='SATISFIED' and h['same_snapshot'] is False and h['root_cause']=='UNDETERMINED' and h['state_agrees']
        assert h['reported_events'][0]['completedQty']==4 and h['source_evidence_hashes']==[digest(x['facts']) for x in out['completed_steps']]
        assert seen==[('GET',DETAIL,'Bearer dev-token'),('GET',EVENTS,'Bearer dev-token')]
        assert m.calls==0 and len(s.inspect(P,rid)['invocations'])==2 and not s.inspect(P,rid)['attempts']
        assert '现场' in ''.join(out['missing_evidence'])
        assert out['triage']['missing_evidence']==out['missing_evidence']
        r.advance(P,rid);assert len(seen)==2

@pytest.mark.parametrize('status',[401,403,404,503])
def test_failed_history_retains_detail(tmp_path,status):
    with server(status=status) as (url,seen):
        s,m,r,rid=setup(tmp_path,StoneHttpService(url));out=r.advance(P,rid)['outcome'];assert out['goal_status']=='PARTIAL' and out['code']==f'EVENT_HISTORY_HTTP_{status}' and len(out['completed_steps'])==1 and len(seen)==2

@pytest.mark.parametrize('mutation',[lambda d:d.update(taskId=104),lambda d:d.update(warehouseId=2),lambda d:d['events'][0].update(reconciled=1),lambda d:d['events'].append(dict(d['events'][0])),lambda d:d['events'][0].update(createdAt='bad'),lambda d:d.update(truncated=True)])
def test_bad_contract(mutation):
    body=response();mutation(body['data'])
    with server(history=body) as (url,seen):
        with pytest.raises(DomainReadError,match='EVENT_HISTORY_CONTRACT_ERROR'):StoneHttpService(url).get_events(P,A)

@pytest.mark.parametrize('body',[response(state='SUCCEEDED'),response(events=[]),response(events=[{'eventKey':'e','targetState':'FAILED','reconciled':False,'reason':'reported','completedQty':0,'createdAt':'2026-01-01T00:00:00.000000'}])])
def test_missing_or_conflicting_evidence(tmp_path,body):
    with server(history=body) as (url,seen):
        s,m,r,rid=setup(tmp_path,StoneHttpService(url));out=r.advance(P,rid)['outcome'];assert out['goal_status']=='PARTIAL' and out['history_analysis']['root_cause']=='UNDETERMINED'

def test_no_request_after_missing_detail(tmp_path):
    s,m,r,rid=setup(tmp_path,FixtureStoneService(),999);out=r.advance(P,rid)['outcome'];assert out['goal_status']=='PARTIAL' and len(s.inspect(P,rid)['invocations'])==1

def test_legacy_mode_no_new_goal_execution(tmp_path):
    s,m,r,rid=setup(tmp_path,FixtureStoneService(),mode='LEGACY_MODEL_V1');assert r.advance(P,rid)['outcome']['code']=='RULE_MODE_REQUIRED' and m.calls==0

def test_permission_pre_network():
    with server() as (url,seen):
        with pytest.raises(Forbidden): StoneHttpService(url).get_events(P,StockWorkArgs(warehouse_id=2,task_id=103))
        assert not seen

def test_redirect_not_followed():
    with server() as (dest,seen_dest),server(status=302,redirect=dest+'/x') as (url,seen):
        with pytest.raises(DomainReadError,match='EVENT_HISTORY_HTTP_302'):StoneHttpService(url).get_events(P,A)
        assert not seen_dest

def test_second_step_recovery(tmp_path):
    with server() as (url,seen):
        s,m,r,rid=setup(tmp_path,StoneHttpService(url));count=0
        def crash(point):
            nonlocal count
            if point=='AFTER_RULE_RESERVE':
                count+=1
                if count==2:raise RuntimeError('injected')
        with pytest.raises(RuntimeError):r.advance(P,rid,crash)
        with s.tx() as c:c.execute('UPDATE run SET lease_until=0 WHERE id=?',(rid,))
        assert r.advance(P,rid)['state']=='COMPLETED';assert [x[1] for x in seen]==[DETAIL,EVENTS];assert m.calls==0
