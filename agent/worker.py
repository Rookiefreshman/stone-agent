"""Durable queue consumer for the configured development principal only."""
import argparse, logging, os, threading, time
from .main import build
from .contracts import Principal, LostClaim
log=logging.getLogger(__name__)

class Worker:
    def __init__(self,runtime,principal_factory,lease=60):
        if lease<1: raise ValueError('lease must be >=1s')
        self.runtime=runtime; self.principal_factory=principal_factory; self.lease=lease
    def once(self):
        p=self.principal_factory(); store=self.runtime.store
        claim=store.claim_next(p,self.lease)
        if claim is None: return False
        rid,f=claim; stop=threading.Event()
        deadline=time.monotonic()+120
        def renew():
            while not stop.wait(self.lease/3):
                if time.monotonic()>=deadline: return
                try: store.heartbeat(rid,f,self.lease)
                except Exception:
                    log.warning('heartbeat stopped for run %s',rid); return
        thread=threading.Thread(target=renew,daemon=True); thread.start()
        try:
            current=self.principal_factory()
            if (current.tenant,current.actor)!=(p.tenant,p.actor): raise LostClaim('execution identity changed')
            self.runtime.execute_claim(current,rid,f)
        except LostClaim: log.info('lost claim for run %s',rid)
        except Exception:
            log.exception('run %s interrupted; recover after lease expiry',rid)
        finally:
            stop.set(); thread.join()
        return True

def dev_principal():
    return Principal('demo','local-dev',frozenset(int(x) for x in os.getenv('STONE_ALLOWED_WAREHOUSES','1').split(',')))

def main():
    p=argparse.ArgumentParser(); p.add_argument('--once',action='store_true'); p.add_argument('--poll',type=float,default=1); p.add_argument('--lease',type=float,default=60); a=p.parse_args()
    if a.poll<=0: p.error('--poll must be positive')
    logging.basicConfig(level=logging.INFO)
    w=Worker(build(os.getenv('POC_DB','demo.sqlite'),os.getenv('POC_LIVE_MODEL')=='1'),dev_principal,a.lease)
    if a.once: w.once(); return
    while True:
        try:
            if not w.once(): time.sleep(a.poll)
        except Exception:
            log.exception('queue temporarily unavailable'); time.sleep(a.poll)
if __name__=='__main__': main()
