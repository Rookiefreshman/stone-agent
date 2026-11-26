"""Actual localhost HTTP tests; no external service or actual Stone writes."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agent.contracts import ModelAttemptError, Principal
from agent.goal_proposal import ALIAS, propose
from agent.provider import ChatCompatibleProvider, ProviderError, MAX_RESPONSE_BYTES
from agent.eval_goals import evaluate, load_gold, DEFAULT_GOLD
from agent.eval_sessions import evaluate as evaluate_sessions, load_dataset, DEFAULT_GOLD as SESSION_GOLD

MODEL='synthetic-http-contract'
MESSAGE='查询仓库1任务103'
P=Principal('test','test',frozenset({1}))

def envelope(arguments=None,usage=17):
    if arguments is None:
        arguments=json.dumps({'kind':'READ_STOCK_WORK','warehouse_id':1,'task_id':103})
    return {'choices':[{'index':0,'finish_reason':'tool_calls','message':{'role':'assistant','tool_calls':[
        {'id':'call-1','type':'function','function':{'name':ALIAS,'arguments':arguments}}
    ]}}],'usage':{'total_tokens':usage} if usage is not None else None}

@pytest.fixture
def server():
    requests=[]
    responses=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length']))
            requests.append((self.path,self.headers.get('Authorization'),json.loads(body)))
            status,raw,headers,delay=responses.pop(0)
            if delay: time.sleep(delay)
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            for k,v in headers.items(): self.send_header(k,v)
            self.send_header('Content-Length',str(len(raw)))
            self.end_headers()
            try: self.wfile.write(raw)
            except (BrokenPipeError,ConnectionResetError): pass
        def do_GET(self):
            requests.append(('GET',None,None))
            self.send_error(405)
        def log_message(self,*args): pass
    httpd=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=httpd.serve_forever,daemon=True)
    thread.start()
    base=f'http://127.0.0.1:{httpd.server_port}/v1'
    def queue(payload=None,status=200,headers=None,delay=0):
        raw=payload if isinstance(payload,bytes) else json.dumps(payload).encode()
        responses.append((status,raw,headers or {},delay))
    try: yield base,queue,requests
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(2)

def test_actual_http_proposal_contract(server,monkeypatch):
    base,queue,requests=server
    monkeypatch.setenv('http_proxy','http://127.0.0.1:1')
    queue(envelope())
    p=ChatCompatibleProvider(base,MODEL,'test-only-key')
    result=propose(MESSAGE,P,p)
    assert result['status']=='NEEDS_CONFIRMATION' and result['execution_allowed'] is False
    assert result['candidate']=={'warehouse_id':1,'task_id':103}
    assert len(requests)==1
    path,auth,body=requests[0]
    assert path=='/v1/chat/completions' and auth=='Bearer test-only-key'
    assert body['model']==MODEL and body['tools'][0]['function']['name']==ALIAS
    assert len(body['tools'])==1 and body['messages'][-1]['content']==MESSAGE

@pytest.mark.parametrize('status,code',[(401,'MODEL_HTTP_AUTH'),(403,'MODEL_HTTP_AUTH'),
    (429,'MODEL_HTTP_RATE_LIMIT'),(400,'MODEL_HTTP_CLIENT'),(500,'MODEL_HTTP_SERVER'),
    (302,'MODEL_HTTP_REDIRECT'),(307,'MODEL_HTTP_REDIRECT')])
def test_http_failures_are_classified_without_retry_or_redirect(server,status,code):
    base,queue,requests=server
    queue({'error':'private upstream content'},status,{'Location':base+'/other'} if status in (302,307) else {})
    p=ChatCompatibleProvider(base,MODEL,'test-only-key')
    with pytest.raises(ProviderError) as err: p.generate({'model':MODEL})
    assert isinstance(err.value,ModelAttemptError) and err.value.code==code
    assert str(err.value)==code and len(requests)==1

@pytest.mark.parametrize('payload',[
    b'<html>bad gateway</html>',
    b'{"choices":[],"choices":[]}',
    b'{"choices":NaN}',
    {'choices':[]},
    {'choices':[{},{}]},
    {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[]}}]},
    {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'type':'function','id':'x','function':{'name':ALIAS,'arguments':{}}}]}}]},
    {'choices':[{'finish_reason':'tool_calls','message':{'tool_calls':[{'type':'function','id':'x','function':{'name':ALIAS,'arguments':'{}'}}]*2}}]},
    {'choices':[{'finish_reason':'unexpected','message':{}}]},
    {'error':{'message':'internal error'},'choices':[{'finish_reason':'stop','message':{}}]},
    envelope(usage=True),
    envelope(usage=-1),
])
def test_malformed_outer_response_fails_closed(server,payload):
    base,queue,requests=server; queue(payload)
    with pytest.raises(ProviderError,match='MODEL_RESPONSE_PROTOCOL'):
        ChatCompatibleProvider(base,MODEL).generate({'model':MODEL})
    assert len(requests)==1

def test_response_size_limit(server):
    base,queue,requests=server; queue(b' '* (MAX_RESPONSE_BYTES+1))
    with pytest.raises(ProviderError,match='MODEL_RESPONSE_TOO_LARGE'):
        ChatCompatibleProvider(base,MODEL).generate({'model':MODEL})
    assert len(requests)==1

def test_nullable_usage_and_truncated_tool_call(server):
    base,queue,requests=server
    queue(envelope(usage=None))
    payload=envelope(); payload['choices'][0]['finish_reason']='length'
    queue(payload)
    p=ChatCompatibleProvider(base,MODEL)
    t=p.generate({'model':MODEL}); assert t.usage is None and t.finish=='TOOL_CALLS'
    t=p.generate({'model':MODEL}); assert t.finish=='INCOMPLETE' and not t.calls
    assert len(requests)==2

def test_profile_mismatch_before_network(server):
    base,queue,requests=server
    with pytest.raises(ProviderError,match='MODEL_PROFILE_MISMATCH'):
        ChatCompatibleProvider(base,MODEL).generate({'model':'wrong'})
    assert not requests

def single_case():
    data=load_gold(DEFAULT_GOLD)
    data['cases']=[x for x in data['cases'] if x['id']=='S01']
    return data

@pytest.mark.parametrize('payload,status,expected_diag,expected_error',[
    ({'error':'private'},429,'PROVIDER_FAILURE','MODEL_HTTP_RATE_LIMIT'),
    (envelope('{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":103,"task_id":104}'),200,'CANDIDATE_SCHEMA_INVALID',None),
    (envelope('{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":999}'),200,'RAW_CANDIDATE_MISMATCH',None),
    ({'choices':[{'finish_reason':'stop','message':{'content':'no tool'}}]},200,'TOOL_PROTOCOL_MISMATCH',None),
])
def test_single_turn_attribution(server,payload,status,expected_diag,expected_error):
    base,queue,requests=server; queue(payload,status)
    report=evaluate(single_case(),ChatCompatibleProvider(base,MODEL))
    row=report['rows'][0]
    assert row['diagnosis']==expected_diag and row['provider_error_code']==expected_error
    assert report['metrics']['diagnostic_counts']=={expected_diag:1}
    assert report['metrics']['provider_error_counts']==({expected_error:1} if expected_error else {})
    assert report['business_calls']==report['business_writes']==0 and len(requests)==1

def test_multiturn_transport_failure_skips_dependent_turn(server):
    base,queue,requests=server; queue({'error':'private'},503)
    data=load_dataset(SESSION_GOLD)
    data['scenarios']=[x for x in data['scenarios'] if x['id']=='M01']
    report=evaluate_sessions(data,ChatCompatibleProvider(base,MODEL))
    assert report['metrics']['diagnostic_counts']=={'PROVIDER_FAILURE':1,'SKIPPED_AFTER_MISMATCH':1}
    assert report['metrics']['provider_error_counts']=={'MODEL_HTTP_SERVER':1}
    assert report['metrics']['confirmation_count']==0 and report['metrics']['model_requests']==1
    assert report['stone_http_calls']==report['stone_business_writes']==0 and len(requests)==1


def test_read_timeout_no_automatic_retry(server):
    base,queue,requests=server
    queue(envelope(),delay=0.4)
    with pytest.raises(ProviderError,match='MODEL_TIMEOUT'):
        ChatCompatibleProvider(base,MODEL,timeout=0.1).generate({'model':MODEL})
    assert len(requests)==1


def test_refused_connection_is_transport_failure():
    # Hold a bound loopback port without listen() so no other process can
    # take it and accidentally return an HTTP response.
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1',0))
        base=f'http://127.0.0.1:{sock.getsockname()[1]}/v1'
        with pytest.raises(ProviderError,match='MODEL_TRANSPORT'):
            ChatCompatibleProvider(base,MODEL,timeout=1).generate({'model':MODEL})
