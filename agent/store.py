import sqlite3, time, uuid
from contextlib import contextmanager
from .contracts import canonical, digest, strict_json, Conflict, LostClaim, Forbidden
from .operations import OP_DDL
from .rehearsal import REHEARSAL_DDL

DDL='''
CREATE TABLE IF NOT EXISTS agent_session (
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
 revision INTEGER NOT NULL DEFAULT 0, confirmed_entity TEXT,
 confirmed_proposal_id TEXT, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS goal_proposal (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES agent_session(id),
 request_key TEXT NOT NULL, message TEXT NOT NULL, request_hash TEXT NOT NULL,
 revision INTEGER NOT NULL, state TEXT NOT NULL, result TEXT,
 created_at REAL NOT NULL, expires_at REAL NOT NULL, run_id TEXT REFERENCES run(id),
 UNIQUE(session_id,request_key), UNIQUE(session_id,revision));
CREATE TABLE IF NOT EXISTS run (
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL, session TEXT NOT NULL,
 request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, message TEXT NOT NULL, goal TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'QUEUED', fence INTEGER NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
 cancelled INTEGER NOT NULL DEFAULT 0, result TEXT, UNIQUE(tenant,actor,session,request_key));
CREATE TABLE IF NOT EXISTS model_attempt (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run(id), attempt_no INTEGER NOT NULL,
 fence INTEGER NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL, request_hash TEXT NOT NULL,
 snapshot TEXT NOT NULL, provider_revision TEXT NOT NULL, reserved INTEGER NOT NULL, usage INTEGER,
 accepted INTEGER NOT NULL DEFAULT 0, reason TEXT, UNIQUE(run_id,attempt_no));
CREATE UNIQUE INDEX IF NOT EXISTS one_accepted_turn ON model_attempt(run_id) WHERE accepted=1;
CREATE TABLE IF NOT EXISTS invocation (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES run(id), request_id TEXT NOT NULL REFERENCES model_attempt(id),
 provider_call_id TEXT NOT NULL, alias TEXT NOT NULL, args TEXT NOT NULL, args_hash TEXT NOT NULL,
 state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, result TEXT, evidence_hash TEXT);
CREATE TABLE IF NOT EXISTS event (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL REFERENCES run(id), kind TEXT NOT NULL, detail TEXT NOT NULL);
'''
QUEUE_DDL="CREATE INDEX IF NOT EXISTS run_queue_idx ON run(tenant,actor,state,lease_until,id)"
TERMINAL={'COMPLETED','NEEDS_INPUT','FAILED','CANCELLED'}
LEGACY='LEGACY_MODEL_V1'
RULES='STOCK_WORK_RULES_V1'

def validate_execution_mode(mode):
    if mode not in (LEGACY,RULES): raise ValueError('invalid execution mode')
    return mode


class Store:
    def __init__(self,path,execution_mode="LEGACY_MODEL_V1"):
        self.path=str(path); self.execution_mode=validate_execution_mode(execution_mode)
        with self.tx() as c:
            c.executescript(DDL.replace('CREATE UNIQUE INDEX IF NOT EXISTS one_accepted_turn ON model_attempt(run_id) WHERE accepted=1;',''))
            c.execute('BEGIN IMMEDIATE')
            c.execute(QUEUE_DDL)
            # v0.4 -> v0.5: legacy rows are logical step 1; atomic table rebuild.
            cols={r['name'] for r in c.execute('PRAGMA table_info(model_attempt)')}
            if 'step_no' not in cols:
                c.execute('ALTER TABLE model_attempt ADD COLUMN step_no INTEGER NOT NULL DEFAULT 1')
                c.execute('DROP INDEX IF EXISTS one_accepted_turn')
                c.execute('ALTER TABLE invocation RENAME TO invocation_v04')
                c.execute("""CREATE TABLE invocation (
                  id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run(id), request_id TEXT NOT NULL REFERENCES model_attempt(id),
                  provider_call_id TEXT NOT NULL, alias TEXT NOT NULL, args TEXT NOT NULL, args_hash TEXT NOT NULL,
                  state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, result TEXT, evidence_hash TEXT,
                  step_no INTEGER NOT NULL DEFAULT 1, UNIQUE(run_id,step_no))""")
                c.execute('INSERT INTO invocation SELECT *,1 FROM invocation_v04')
                c.execute('DROP TABLE invocation_v04')
            # Both new and migrated databases enforce uniqueness per logical step.
            c.execute('CREATE UNIQUE INDEX IF NOT EXISTS one_accepted_turn ON model_attempt(run_id,step_no) WHERE accepted=1')
            # Existing queued/running runs keep their original execution protocol.
            if 'execution_mode' not in {r['name'] for r in c.execute('PRAGMA table_info(run)')}:
                c.execute("ALTER TABLE run ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'LEGACY_MODEL_V1'")
            if 'origin' not in {r['name'] for r in c.execute('PRAGMA table_info(invocation)')}:
                c.execute('ALTER TABLE invocation RENAME TO invocation_v05')
                c.execute("""CREATE TABLE invocation (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run(id), request_id TEXT REFERENCES model_attempt(id),
                    provider_call_id TEXT, alias TEXT NOT NULL, args TEXT NOT NULL, args_hash TEXT NOT NULL,
                    state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, result TEXT, evidence_hash TEXT,
                    step_no INTEGER NOT NULL DEFAULT 1, origin TEXT NOT NULL DEFAULT 'MODEL', direct_snapshot TEXT,
                    UNIQUE(run_id,step_no), CHECK ((origin='MODEL' AND request_id IS NOT NULL AND provider_call_id IS NOT NULL AND direct_snapshot IS NULL)
                    OR (origin='RULE' AND request_id IS NULL AND provider_call_id IS NULL AND direct_snapshot IS NOT NULL)))""")
                c.execute("INSERT INTO invocation SELECT *, 'MODEL', NULL FROM invocation_v05")
                c.execute('DROP TABLE invocation_v05')
            for stmt in OP_DDL + REHEARSAL_DDL: c.execute(stmt)
    @contextmanager
    def tx(self):
        c=sqlite3.connect(self.path,timeout=5,isolation_level=None); c.row_factory=sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA busy_timeout=5000'); c.execute('BEGIN IMMEDIATE')
        try: yield c; c.commit()
        except BaseException: c.rollback(); raise
        finally: c.close()
    def locked(self,c,sql,params=(),skip=False):
        return c.execute(sql,params).fetchone()
    def now(self,c): return time.time()
    def event(self,c,run,kind,detail=None):
        c.execute('INSERT INTO event(run_id,kind,detail) VALUES(?,?,?)',(run,kind,canonical(detail or {})))
    def submit_in_tx(self,c,p,session,key,message,goal):
        goal_data=goal.model_dump(exclude={'entities'} if not goal.entities else set()); fp=digest([message,goal_data]); rid=str(uuid.uuid4())
        old=c.execute('SELECT * FROM run WHERE tenant=? AND actor=? AND session=? AND request_key=?',(p.tenant,p.actor,session,key)).fetchone()
        if old:
            if old['fingerprint']!=fp: raise Conflict('IDEMPOTENCY_CONFLICT')
            return old['id']
        inserted=c.execute('INSERT INTO run(id,tenant,actor,session,request_key,fingerprint,message,goal,execution_mode) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(tenant,actor,session,request_key) DO NOTHING',(rid,p.tenant,p.actor,session,key,fp,message,canonical(goal_data),self.execution_mode)).rowcount
        if not inserted:
            old=c.execute('SELECT * FROM run WHERE tenant=? AND actor=? AND session=? AND request_key=?',(p.tenant,p.actor,session,key)).fetchone()
            if old['fingerprint']!=fp: raise Conflict('IDEMPOTENCY_CONFLICT')
            return old['id']
        self.event(c,rid,'QUEUED'); return rid
    def submit(self,p,session,key,message,goal):
        with self.tx() as c:
            # Managed sessions require the atomic proposal confirmation route.
            managed=c.execute('SELECT tenant,actor FROM agent_session WHERE id=?',(session,)).fetchone()
            if managed:
                if (managed['tenant'],managed['actor'])!=(p.tenant,p.actor): raise Forbidden('NOT_ACCESSIBLE')
                raise Conflict('MANAGED_SESSION_REQUIRES_CONFIRMATION')
            return self.submit_in_tx(c,p,session,key,message,goal)
    def get(self,p,rid):
        with self.tx() as c:
            r=c.execute('SELECT * FROM run WHERE id=? AND tenant=? AND actor=?',(rid,p.tenant,p.actor)).fetchone()
            if not r: raise Forbidden('NOT_ACCESSIBLE')
            return dict(r)
    def _claim_locked(self,c,r,lease):
        rid=r['id']; f=r['fence']+1
        c.execute("UPDATE run SET state='RUNNING',fence=?,lease_until=? WHERE id=?",(f,self.now(c)+lease,rid))
        c.execute("UPDATE model_attempt SET state='ABANDONED_NOT_SENT',usage=0 WHERE run_id=? AND state='PREPARED'",(rid,))
        c.execute("UPDATE model_attempt SET state='UNKNOWN',usage=reserved WHERE run_id=? AND state='SEND_INTENT'",(rid,))
        self.event(c,rid,'CLAIM',{'fence':f}); return f
    def claim(self,p,rid,lease=60):
        with self.tx() as c:
            r=self.locked(c,'SELECT * FROM run WHERE id=? AND tenant=? AND actor=?',(rid,p.tenant,p.actor))
            if not r: raise Forbidden('NOT_ACCESSIBLE')
            if r['state'] in TERMINAL: return None
            if r['lease_until']>self.now(c): raise Conflict('RUN_BUSY')
            return self._claim_locked(c,r,lease)
    def claim_next(self,p,lease=60):
        # Run is the durable queue item, avoiding a second job/run dual-write.
        # Only the configured execution identity is eligible for this worker.
        with self.tx() as c:
            r=self.locked(c,"SELECT * FROM run WHERE tenant=? AND actor=? AND state IN ('QUEUED','RUNNING') AND lease_until<=? ORDER BY lease_until,id LIMIT 1",(p.tenant,p.actor,self.now(c)),skip=True)
            if not r: return None
            return r['id'],self._claim_locked(c,r,lease)
    def heartbeat(self,rid,f,lease=60):
        with self.tx() as c:
            self.guard(c,rid,f)
            c.execute('UPDATE run SET lease_until=? WHERE id=?',(self.now(c)+lease,rid))
    def events_after(self,p,rid,after=0,limit=100):
        with self.tx() as c:
            r=self.locked(c,'SELECT * FROM run WHERE id=? AND tenant=? AND actor=?',(rid,p.tenant,p.actor))
            if not r: raise Forbidden('NOT_ACCESSIBLE')
            events=[dict(x) for x in c.execute('SELECT seq,kind,detail FROM event WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?',(rid,after,limit))]
            return r['state'],events
    def guard(self,c,rid,f,allow_cancel=False):
        r=self.locked(c,'SELECT * FROM run WHERE id=?',(rid,))
        if not r or r['fence']!=f or r['lease_until']<=self.now(c) or r['state'] in TERMINAL: raise LostClaim('stale lease or fence')
        if r['cancelled'] and not allow_cancel: raise LostClaim('cancelled')
        return r
    def require_active(self,rid,f):
        with self.tx() as c: self.guard(c,rid,f)
    def cancelled(self,rid):
        with self.tx() as c: return bool(c.execute('SELECT cancelled FROM run WHERE id=?',(rid,)).fetchone()[0])
    def cancel(self,p,rid):
        with self.tx() as c:
            r=self.locked(c,'SELECT * FROM run WHERE id=? AND tenant=? AND actor=?',(rid,p.tenant,p.actor))
            if not r: raise Forbidden('NOT_ACCESSIBLE')
            if r['state'] not in TERMINAL:
                c.execute('UPDATE run SET cancelled=1 WHERE id=?',(rid,)); self.event(c,rid,'CANCEL_REQUESTED')
                if r['lease_until']<=self.now(c):
                    c.execute("UPDATE run SET state='CANCELLED',result=?,lease_until=0 WHERE id=?",(canonical({'code':'CANCELLED'}),rid))
                    self.event(c,rid,'CANCELLED',{'code':'CANCELLED'})
    def finish(self,rid,f,state,result):
        with self.tx() as c:
            r=self.guard(c,rid,f,allow_cancel=True)
            if r['cancelled']: state,result='CANCELLED',{'code':'CANCELLED'}
            c.execute('UPDATE run SET state=?,result=?,lease_until=0 WHERE id=?',(state,canonical(result),rid)); self.event(c,rid,state,{'code':result.get('code')})
    def prepare(self,rid,f,body,snapshot,revision,max_attempts=2,reserved=4096,token_budget=8192,step_no=1,total_attempts=2):
        with self.tx() as c:
            row=self.guard(c,rid,f)
            if row['execution_mode']!=LEGACY: raise Conflict('EXECUTION_MODE_MISMATCH')
            counts=c.execute('SELECT count(*) AS n,coalesce(sum(coalesce(usage,reserved)),0) AS used FROM model_attempt WHERE run_id=?',(rid,)).fetchone()
            n,used=counts['n'],counts['used']
            step_count=c.execute('SELECT count(*) AS n FROM model_attempt WHERE run_id=? AND step_no=?',(rid,step_no)).fetchone()['n']
            if step_count>=max_attempts or n>=total_attempts or used+reserved>token_budget: return None
            q=str(uuid.uuid4()); c.execute('INSERT INTO model_attempt(id,run_id,attempt_no,fence,state,body,request_hash,snapshot,provider_revision,reserved,step_no) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(q,rid,n+1,f,'PREPARED',canonical(body),digest(body),canonical(snapshot),revision,reserved,step_no))
            self.event(c,rid,'MODEL_PREPARED',{'request_id':q}); return q
    def send_intent(self,rid,f,q,body,revision):
        with self.tx() as c:
            self.guard(c,rid,f)
            n=c.execute("UPDATE model_attempt SET state='SEND_INTENT' WHERE id=? AND run_id=? AND fence=? AND state='PREPARED' AND request_hash=? AND provider_revision=?",(q,rid,f,digest(body),revision)).rowcount
            if n!=1: raise LostClaim('request not sendable')
            self.event(c,rid,'MODEL_SEND_INTENT',{'request_id':q})
    def fail_attempt(self,rid,f,q,reason,usage=None,uncertain=False):
        with self.tx() as c:
            self.guard(c,rid,f,allow_cancel=True)
            n=c.execute("UPDATE model_attempt SET state=?,usage=coalesce(?,reserved),reason=? WHERE id=? AND run_id=? AND fence=? AND state='SEND_INTENT'",('UNKNOWN' if uncertain else 'RECEIVED',usage,reason,q,rid,f)).rowcount
            if n!=1: raise LostClaim('attempt no longer current')
            self.event(c,rid,'MODEL_NOT_ACCEPTED',{'code':reason})
    def accept(self,rid,f,q,call,args,usage,step_no=1):
        with self.tx() as c:
            self.guard(c,rid,f)
            n=c.execute("UPDATE model_attempt SET state='RECEIVED',accepted=1,usage=coalesce(?,reserved) WHERE id=? AND run_id=? AND fence=? AND step_no=? AND state='SEND_INTENT'",(usage,q,rid,f,step_no)).rowcount
            if n!=1: raise LostClaim('attempt not acceptable')
            iid=str(uuid.uuid4()); c.execute("INSERT INTO invocation(id,run_id,request_id,provider_call_id,alias,args,args_hash,state,step_no) VALUES(?,?,?,?,?,?,?,'RESERVED',?)",(iid,rid,q,call.provider_call_id,call.name,canonical(args),digest(args),step_no))
            self.event(c,rid,'ACCEPTED_CALL',{'invocation_id':iid}); return iid
    def reserve_rule(self,rid,f,alias,args,snapshot,step_no):
        with self.tx() as c:
            row=self.guard(c,rid,f)
            if row['execution_mode']!=RULES: raise Conflict('EXECUTION_MODE_MISMATCH')
            if set(snapshot)!={alias}: raise Conflict('INVALID_RULE_SNAPSHOT')
            iid=str(uuid.uuid4())
            c.execute("INSERT INTO invocation(id,run_id,request_id,provider_call_id,alias,args,args_hash,state,step_no,origin,direct_snapshot) VALUES(?,?,NULL,NULL,?,?,?,'RESERVED',?,'RULE',?)",(iid,rid,alias,canonical(args),digest(args),step_no,canonical(snapshot)))
            self.event(c,rid,'RULE_RESERVED',{'invocation_id':iid,'step_no':step_no,'alias':alias}); return iid
    def invocation(self,rid,step_no=1):
        with self.tx() as c:
            row=c.execute("""SELECT i.*,coalesce(i.direct_snapshot,m.snapshot) AS snapshot,
                CASE WHEN i.origin='RULE' THEN 1 ELSE m.accepted END AS accepted,m.provider_revision
                FROM invocation i LEFT JOIN model_attempt m ON m.id=i.request_id AND m.run_id=i.run_id AND m.step_no=i.step_no
                WHERE i.run_id=? AND i.step_no=? AND (i.origin='RULE' OR m.id IS NOT NULL)""",(rid,step_no)).fetchone()
            return dict(row) if row else None
    def dispatch(self,rid,f,iid,max_attempts=2):
        with self.tx() as c:
            self.guard(c,rid,f)
            n=c.execute("UPDATE invocation SET state='DISPATCH_INTENT',attempts=attempts+1 WHERE id=? AND run_id=? AND state IN ('RESERVED','DISPATCH_INTENT') AND attempts<?",(iid,rid,max_attempts)).rowcount
            if n!=1: raise Conflict('READ_ATTEMPTS_EXHAUSTED')
            self.event(c,rid,'READ_DISPATCH_INTENT',{'invocation_id':iid})
    def complete_read(self,rid,f,iid,data):
        with self.tx() as c:
            self.guard(c,rid,f)
            n=c.execute("UPDATE invocation SET state='SUCCEEDED',result=?,evidence_hash=? WHERE id=? AND run_id=? AND state='DISPATCH_INTENT'",(canonical(data),digest(data),iid,rid)).rowcount
            if n!=1: raise LostClaim('invocation not active')
            self.event(c,rid,'READ_SUCCEEDED',{'invocation_id':iid})
    def fail_read(self,rid,f,iid,code):
        with self.tx() as c:
            self.guard(c,rid,f,allow_cancel=True)
            c.execute("UPDATE invocation SET state='FAILED',result=? WHERE id=? AND run_id=? AND state!='SUCCEEDED'",(canonical({'code':code}),iid,rid)); self.event(c,rid,'READ_FAILED',{'code':code})
    def inspect(self,p,rid):
        self.get(p,rid)
        with self.tx() as c:
            return {'attempts':[dict(r) for r in c.execute('SELECT id,step_no,state,accepted,usage,reason FROM model_attempt WHERE run_id=? ORDER BY attempt_no',(rid,))], 'invocations':[dict(r) for r in c.execute('SELECT id,step_no,alias,state,attempts,evidence_hash,origin FROM invocation WHERE run_id=? ORDER BY step_no',(rid,))], 'events':[dict(r) for r in c.execute('SELECT seq,kind,detail FROM event WHERE run_id=? ORDER BY seq',(rid,))]}
