import argparse,json,os,uuid
from .contracts import GoalContract,StockWorkArgs,Principal,OutboundArgs,WesArgs
from .store import Store,RULES
from .provider import FixtureProvider,ChatCompatibleProvider
from .tools import FixtureStoneService,StoneHttpService,make_registry,authorize
from .runtime import Runtime
from .goal_proposal import propose

def build(db,live=False):
    provider=ChatCompatibleProvider(os.environ['MODEL_BASE_URL'],os.environ['MODEL_NAME'],os.getenv('MODEL_API_KEY','')) if live else FixtureProvider()
    service=StoneHttpService(os.environ['STONE_BASE_URL'],os.getenv('STONE_API_TOKEN','')) if os.getenv('STONE_LIVE_DOMAIN')=='1' else FixtureStoneService()
    dsn=os.getenv('AGENT_DATABASE_URL')
    if dsn:
        from .postgres import PostgresStore
        store=PostgresStore(dsn,execution_mode=RULES)
    else: store=Store(db,execution_mode=RULES)
    return Runtime(store,provider,make_registry(service))

def main():
    p=argparse.ArgumentParser(description='Local read-only PoC; Stone read-only development')
    p.add_argument('--db',default='demo.sqlite'); p.add_argument('--live-model',action='store_true')
    sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('run'); a.add_argument('message'); a.add_argument('--warehouse',required=True,type=int); a.add_argument('--task',required=True,type=int); a.add_argument('--key',default=None)
    for cmd in ['diagnose','analyze','deep-diagnose']:
        a=sub.add_parser(cmd); a.add_argument('message'); a.add_argument('--warehouse',required=True,type=int); a.add_argument('--task',required=True,type=int)
    a=sub.add_parser('compare'); a.add_argument('message'); a.add_argument('--warehouse',required=True,type=int); a.add_argument('--tasks',required=True,type=int,nargs='+')
    a=sub.add_parser('outbound-diagnose'); a.add_argument('message'); a.add_argument('--warehouse',required=True,type=int); a.add_argument('--order',required=True,type=int); a.add_argument('--key',default=None)
    a=sub.add_parser('wes-diagnose'); a.add_argument('message'); a.add_argument('--warehouse',required=True,type=int); a.add_argument('--device',required=True); a.add_argument('--limit',required=True,type=int); a.add_argument('--key',default=None)
    a=sub.add_parser('propose'); a.add_argument('message')
    for cmd in ['resume','inspect','cancel']:
        a=sub.add_parser(cmd); a.add_argument('run_id')
    args=p.parse_args(); runtime=build(args.db,args.live_model); actor=Principal('demo','local-dev',frozenset(int(x) for x in os.getenv('STONE_ALLOWED_WAREHOUSES','1').split(',')))
    if args.command=='propose':
        result=propose(args.message,actor,runtime.provider if args.live_model else None)
    elif args.command=='wes-diagnose':
        entity=WesArgs(warehouse_id=args.warehouse,device_code=args.device,requested_limit=args.limit); authorize(actor,entity)
        goal=GoalContract(kind='DIAGNOSE_WES_DISPATCH',entity=entity,provenance='CONFIRMED_STRUCTURED_INPUT')
        rid=runtime.store.submit(actor,'cli',args.key or str(uuid.uuid4()),args.message,goal)
        result=runtime.advance(actor,rid)
    elif args.command=='outbound-diagnose':
        entity=OutboundArgs(warehouse_id=args.warehouse,order_id=args.order); authorize(actor,entity)
        goal=GoalContract(kind='DIAGNOSE_OUTBOUND_ALLOCATION',entity=entity,provenance='CONFIRMED_STRUCTURED_INPUT')
        rid=runtime.store.submit(actor,'cli',args.key or str(uuid.uuid4()),args.message,goal)
        result=runtime.advance(actor,rid)
    elif args.command=='run':
        entity=StockWorkArgs(warehouse_id=args.warehouse,task_id=args.task); authorize(actor,entity)
        goal=GoalContract(kind='READ_STOCK_WORK',entity=entity,provenance='CONFIRMED_STRUCTURED_INPUT')
        rid=runtime.store.submit(actor,'cli',args.key or str(uuid.uuid4()),args.message,goal)
        result=runtime.advance(actor,rid)
    elif args.command in ('diagnose','analyze','compare','deep-diagnose'):
        targets=[StockWorkArgs(warehouse_id=args.warehouse,task_id=t) for t in ([args.task] if args.command!='compare' else args.tasks)]
        for target in targets: authorize(actor,target)
        goal=GoalContract(kind={'diagnose':'DIAGNOSE_STOCK_WORK','deep-diagnose':'DEEP_DIAGNOSE_STOCK_WORK','analyze':'ANALYZE_STOCK_WORK','compare':'COMPARE_STOCK_WORK'}[args.command],entity=targets[0] if args.command!='compare' else None,entities=targets if args.command=='compare' else [],provenance='CONFIRMED_STRUCTURED_INPUT')
        rid=runtime.store.submit(actor,'cli',str(uuid.uuid4()),args.message,goal)
        result=runtime.advance(actor,rid)
    elif args.command=='resume': result=runtime.advance(actor,args.run_id)
    elif args.command=='inspect': runtime.view(actor,args.run_id); result=runtime.store.inspect(actor,args.run_id)
    else: runtime.store.cancel(actor,args.run_id); result=runtime.view(actor,args.run_id)
    print(json.dumps({'mode':('LIVE_MODEL' if args.live_model else 'FIXTURE_MODEL')+' + '+('STONE_HTTP' if os.getenv('STONE_LIVE_DOMAIN')=='1' else 'SYNTHETIC_DOMAIN'),'result':result},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
