"""Explicit opt-in smoke test against isolated development services. Never invokes Stone POST."""
import argparse
import json
import os
import uuid
from .contracts import Principal, StockWorkArgs
from .main import build
from .session import SessionService
from .store import RULES


def main():
    parser=argparse.ArgumentParser(description='Verify one known diagnostic task against dev PostgreSQL, real model and Stone GET')
    parser.add_argument('--confirm-isolated-dev',action='store_true',required=True)
    parser.add_argument('--warehouse',type=int,required=True)
    parser.add_argument('--task',type=int,required=True)
    parser.add_argument('--expected-state',choices=['READY','IN_FLIGHT','UNKNOWN','SUCCEEDED','FAILED','CANCELLED'],required=True)
    a=parser.parse_args()
    required=['AGENT_DATABASE_URL','MODEL_BASE_URL','MODEL_NAME','STONE_BASE_URL','STONE_ALLOWED_WAREHOUSES']
    if any(not os.getenv(k) for k in required) or os.getenv('STONE_LIVE_DOMAIN')!='1':
        parser.error('configure '+', '.join(required)+' and STONE_LIVE_DOMAIN=1; no fixture fallback')
    args=StockWorkArgs(warehouse_id=a.warehouse,task_id=a.task)
    p=Principal('integration-'+uuid.uuid4().hex,'dev-verifier',frozenset(int(x) for x in os.environ['STONE_ALLOWED_WAREHOUSES'].split(',')))
    if a.warehouse not in p.warehouses: parser.error('warehouse not in allowed dev scope')
    runtime=build('unused.sqlite',live=True)
    svc=SessionService(runtime.store,runtime.provider)
    sid=svc.create(p)['session_id']
    proposal=svc.propose(p,sid,'live-smoke',f'诊断仓库{a.warehouse}任务{a.task}的状态及待核对证据，不推断具体根因')
    if proposal['status']!='NEEDS_CONFIRMATION' or proposal.get('source')!='MODEL_PROPOSED' or proposal.get('goal_kind')!='DIAGNOSE_STOCK_WORK' or proposal.get('candidate')!=args.model_dump():
        raise SystemExit('FAIL: actual model did not propose the independently expected goal; no business query executed')
    # The operator supplied the expected target explicitly; not automatic model approval in user traffic.
    rid=svc.confirm(p,sid,proposal['proposal_id'],args,'DIAGNOSE_STOCK_WORK')['run_id']
    result=runtime.advance(p,rid)
    audit=runtime.store.inspect(p,rid)
    triage=(result['outcome'] or {}).get('triage',{})
    checks={'completed':result['state']=='COMPLETED','actual_http_source':triage.get('source')=='STONE_HTTP',
            'expected_state':triage.get('observed_state')==a.expected_state,'root_cause_not_invented':triage.get('root_cause')=='UNDETERMINED',
            'rule_mode':runtime.store.get(p,rid)['execution_mode']==RULES,'no_execution_model_attempt':not audit['attempts'],
            'two_successful_rule_invocations':len(audit['invocations'])==2 and all(i['origin']=='RULE' and i['state']=='SUCCEEDED' for i in audit['invocations'])}
    print(json.dumps({'run_id':rid,'checks':checks,'passed':all(checks.values()),'scope':'one isolated development smoke test; not a production release gate'},ensure_ascii=False,indent=2))
    if not all(checks.values()): raise SystemExit(1)

if __name__=='__main__': main()
