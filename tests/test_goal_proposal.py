import pytest
from agent.goal_proposal import propose, ALIAS
from agent.contracts import Principal, ModelTurn, ToolCall, Forbidden, ModelAttemptError
P=Principal('demo','u',frozenset({1}))

@pytest.mark.parametrize('message', ['查询仓库1的103号库存作业状态及流水','帮我查询1号仓库中103号库存作业的状态及库存流水。','查看仓库1任务103'])
def test_fixture_single(message):
    r=propose(message,P); assert r['status']=='NEEDS_CONFIRMATION' and r['candidate']=={'warehouse_id':1,'task_id':103}; assert not r['execution_allowed']

@pytest.mark.parametrize('message',['查询103号库存作业','查询仓库1','查询'])
def test_missing(message):
    assert propose(message,P)['status']=='NEEDS_INPUT'

@pytest.mark.parametrize('message',['如果仓库1任务103失败就取消','查询仓库1任务103然后冻结库位','查询仓库1任务103和104','为什么仓库1任务103是UNKNOWN','查询仓库1的任务ASN1:1'])
def test_no_silent_downgrade(message):
    assert propose(message,P)['status']=='UNSUPPORTED'

def test_warehouse_scope():
    with pytest.raises(Forbidden): propose('查询仓库2任务103',P)

class Provider:
    model='fake'
    def __init__(self,turn): self.turn=turn; self.bodies=[]
    def generate(self,body): self.bodies.append(body); return self.turn

def turn(raw,alias=ALIAS): return ModelTurn('TOOL_CALLS',(ToolCall('id',alias,raw),),10)

def test_model_candidate_not_permission():
    provider=Provider(turn('{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":103}'))
    r=propose('查询仓库1任务103',P,provider)
    assert r['status']=='NEEDS_CONFIRMATION' and not r['execution_allowed']
    assert len(provider.bodies)==1 and provider.bodies[0]['tools'][0]['function']['name']==ALIAS

@pytest.mark.parametrize('raw,alias', [('{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":103}','cancel_stock_work'),('{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":103,"task_id":104}',ALIAS),('{"kind":"READ_STOCK_WORK","warehouse_id":"1","task_id":103}',ALIAS),('{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":103,"authorized":true}',ALIAS)])
def test_model_invalid(raw,alias):
    assert propose('查询仓库1任务103',P,Provider(turn(raw,alias)))['status']=='PROPOSAL_FAILED'

def test_model_hallucinated_id():
    r=propose('查询仓库1的作业',P,Provider(turn('{"kind":"READ_STOCK_WORK","warehouse_id":1,"task_id":103}')))
    assert r['status']=='NEEDS_INPUT' and r['candidate']['task_id'] is None

def test_model_rejects_out_of_scope():
    with pytest.raises(Forbidden): propose('查询仓库2任务103',P,Provider(turn('{"kind":"READ_STOCK_WORK","warehouse_id":2,"task_id":103}')))

def test_api_proposal_never_creates_run(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from agent.api import create_app
    import sqlite3
    monkeypatch.setenv('POC_DEV_TOKEN','test-token-1234567890'); monkeypatch.setenv('POC_DB',str(tmp_path/'api.sqlite')); monkeypatch.delenv('POC_LIVE_MODEL',raising=False)
    client=TestClient(create_app()); h={'Authorization':'Bearer test-token-1234567890'}
    assert client.post('/api/v1/goal-proposals',json={'message':'查询仓库1任务103'}).status_code==401
    r=client.post('/api/v1/goal-proposals',headers=h,json={'message':'查询仓库1任务103'}).json()
    assert r['status']=='NEEDS_CONFIRMATION' and not r['execution_allowed']
    with sqlite3.connect(tmp_path/'api.sqlite') as c: assert c.execute('SELECT COUNT(*) FROM run').fetchone()[0]==0
    data={'client_request_id':'confirmed-1','message':'仅查询仓库1任务103状态和流水','goal':{'kind':'READ_STOCK_WORK','entity':r['candidate'],'provenance':'CONFIRMED_STRUCTURED_INPUT'}}
    res=client.post('/api/v1/sessions/s/runs',headers=h,json=data); assert res.status_code==202
    assert client.post('/api/v1/runs/'+res.json()['run_id']+'/advance',headers=h).json()['state']=='COMPLETED'
