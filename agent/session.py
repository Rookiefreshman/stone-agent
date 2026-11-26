"""v0.3 managed conversation. All inheritance is explicit, bounded and confirmed.
SQLite is a local development store, not a distributed authorization authority.
"""
import re
import time
import uuid
from .contracts import (StockWorkArgs, GoalContract, Forbidden, Conflict,
                        canonical, digest, strict_json, ModelAttemptError)
from .goal_proposal import propose, GRAMMAR
from .tools import authorize
from .business_language import EXTENDED, extended_followup
from .store import TERMINAL

FOLLOW_TASK = re.compile(r'(?:那|那么|再查|再查询|再看|接着查)\s*(?:任务|库存作业|库存任务)?\s*([0-9]{1,19})\s*号?\s*(?:任务|库存作业|库存任务)?\s*(?:呢|的状态|的流水|的状态及流水)?\s*[。？?]?')
TASK_ONLY = re.compile(r'(?:任务|库存作业|库存任务)\s*([0-9]{1,19})\s*号?\s*[。？?]?')
FOLLOW_WAREHOUSE = re.compile(r'(?:仓库\s*([0-9]{1,19})|([0-9]{1,19})\s*号仓库)\s*[。？?]?')

class SessionService:
    def __init__(self, store, provider=None, ttl=300, clock=time.time):
        self.store, self.provider, self.ttl, self.clock = store, provider, ttl, clock

    def _session(self, c, p, sid):
        row = self.store.locked(c,'SELECT * FROM agent_session WHERE id=? AND tenant=? AND actor=?', (sid,p.tenant,p.actor))
        if row is None: raise Forbidden('NOT_ACCESSIBLE')
        return row

    def create(self,p):
        sid=str(uuid.uuid4())
        with self.store.tx() as c:
            c.execute('INSERT INTO agent_session(id,tenant,actor,created_at) VALUES(?,?,?,?)',(sid,p.tenant,p.actor,self.clock()))
        return {'session_id':sid,'revision':0}

    def _safe_entity(self,p,entity):
        if not entity: return None
        if entity.get('warehouse_id') not in p.warehouses: return None
        return entity

    def _view(self,p,row,revision):
        result=strict_json(row['result']) if row['result'] else {}
        candidate=result.get('candidate')
        # A partial candidate with no warehouse is user input, not business data.
        if candidate and candidate.get('warehouse_id') is not None and not self._safe_entity(p,candidate):
            raise Forbidden('NOT_ACCESSIBLE')
        state=row['state']
        if state not in ('CONFIRMED','SUPERSEDED'):
            if row['revision']!=revision: state='SUPERSEDED'
            elif row['expires_at']<=self.clock(): state='EXPIRED'
        return {**result,'proposal_id':row['id'],'session_id':row['session_id'],
                'revision':row['revision'],'status':state,'run_id':row['run_id'],
                'expires_at':row['expires_at'],'execution_allowed':False}

    def _active(self,c,p,sid):
        rows=c.execute('SELECT state FROM run WHERE tenant=? AND actor=? AND session=?',(p.tenant,p.actor,sid))
        return any(r['state'] not in TERMINAL for r in rows)

    def _derive(self,message,p,confirmed,pending,extended_confirmed=None,extended_pending=None):
        extended=extended_followup(message,p,extended_confirmed,extended_pending)
        if extended is not None: return extended
        # Only tightly matched follow-up patterns can inherit fields. Never let
        # arbitrary LLM output or a previous tool result change entity memory.
        m=FOLLOW_TASK.fullmatch(message.strip())
        if not m and pending and pending.get('warehouse_id') and pending.get('task_id') is None:
            m=TASK_ONLY.fullmatch(message.strip())
        if m:
            t=int(m[1]); w=confirmed.get('warehouse_id') if confirmed else None
            source='CONFIRMED_CONTEXT'
            if pending and pending.get('warehouse_id') and pending.get('task_id') is None:
                w=pending['warehouse_id']; source='PENDING_CLARIFICATION'
            if not 1<=t<=9223372036854775807:
                return self._missing(None,None)
            if w is None: return self._missing(None,t)
            if w not in p.warehouses: raise Forbidden('NOT_ACCESSIBLE')
            return self._candidate(w,t,source)
        m=FOLLOW_WAREHOUSE.fullmatch(message.strip())
        if m and pending and pending.get('task_id') and pending.get('warehouse_id') is None:
            w=int(m[1] or m[2]); t=pending['task_id']
            if not 1<=w<=9223372036854775807: return self._missing(None,t)
            if w not in p.warehouses: raise Forbidden('NOT_ACCESSIBLE')
            return self._candidate(w,t,'PENDING_CLARIFICATION')
        # A full read request with an explicitly stated task may inherit the last
        # confirmed warehouse only when the restricted grammar matches exactly.
        m=GRAMMAR.fullmatch(message.strip())
        if m and not (m['w1'] or m['w2']) and (m['t1'] or m['t2']) and confirmed:
            t=int(m['t1'] or m['t2']); w=confirmed['warehouse_id']
            if not 1<=t<=9223372036854775807: return self._missing(None,None)
            if w not in p.warehouses: raise Forbidden('NOT_ACCESSIBLE')
            return self._candidate(w,t,'CONFIRMED_CONTEXT')
        return propose(message,p,self.provider)

    @staticmethod
    def _missing(w,t):
        return {'status':'NEEDS_INPUT','source':'FOLLOWUP_GRAMMAR','candidate':{'warehouse_id':w,'task_id':t},'missing_fields':[k for k,v in [('warehouse_id',w),('task_id',t)] if v is None], 'message':'请补充仓库或库存作业 ID。','execution_allowed':False}

    @staticmethod
    def _candidate(w,t,source):
        args=StockWorkArgs(warehouse_id=w,task_id=t)
        return {'status':'NEEDS_CONFIRMATION','source':source,'candidate':args.model_dump(),'goal_kind':'READ_STOCK_WORK','message':'请确认仅查询此仓库及库存作业的状态和流水。','execution_allowed':False}

    def propose(self,p,sid,key,message):
        if not isinstance(message,str) or not message.strip() or len(message)>4000:
            raise ValueError('invalid message')
        if not isinstance(key,str) or not 1<=len(key)<=128: raise ValueError('invalid request key')
        fp=digest(message); pid=str(uuid.uuid4()); now=self.clock()
        with self.store.tx() as c:
            s=self._session(c,p,sid)
            old=c.execute('SELECT * FROM goal_proposal WHERE session_id=? AND request_key=?',(sid,key)).fetchone()
            if old:
                if old['request_hash']!=fp: raise Conflict('IDEMPOTENCY_CONFLICT')
                return self._view(p,old,s['revision'])
            if self._active(c,p,sid): raise Conflict('ACTIVE_RUN')
            prev=c.execute('SELECT * FROM goal_proposal WHERE session_id=? AND revision=?',(sid,s['revision'])).fetchone()
            pending=None
            extended_pending=None
            extended_confirmed=None
            if prev and prev['state']=='NEEDS_INPUT' and prev['expires_at']>now:
                prior=strict_json(prev['result'])
                if prior.get('goal_kind') in EXTENDED: extended_pending=prior
                else: pending=prior.get('candidate')
            # A newer unconfirmed/unsupported turn must not silently fall back
            # to a potentially different older warehouse.
            confirmed=strict_json(s['confirmed_entity']) if s['confirmed_entity'] and prev is None else None
            if confirmed and s['confirmed_proposal_id']:
                cp=c.execute('SELECT result FROM goal_proposal WHERE id=? AND session_id=?',(s['confirmed_proposal_id'],sid)).fetchone()
                if cp:
                    prior=strict_json(cp['result'])
                    if prior.get('goal_kind') in EXTENDED and prior.get('candidate')==confirmed:
                        extended_confirmed=prior
                    if prior.get('goal_kind','READ_STOCK_WORK')!='READ_STOCK_WORK':
                        confirmed=None
            rev=s['revision']+1
            c.execute('UPDATE agent_session SET revision=? WHERE id=?',(rev,sid))
            c.execute("UPDATE goal_proposal SET state='SUPERSEDED' WHERE session_id=? AND state IN ('PENDING','NEEDS_INPUT','NEEDS_CONFIRMATION')",(sid,))
            c.execute("INSERT INTO goal_proposal(id,session_id,request_key,message,request_hash,revision,state,created_at,expires_at) VALUES(?,?,?,?,?,?,'PENDING',?,?)",(pid,sid,key,message,fp,rev,now,now+self.ttl))
        # Model call outside SQLite transaction. Request is durably reserved;
        # crashed/late attempts are not replayed under the same request key.
        try:
            result=self._derive(message,p,confirmed,pending,extended_confirmed,extended_pending)
        except Forbidden:
            result={'status':'FORBIDDEN','source':'POLICY','candidate':None,'message':'无权访问。'}
        except (ValueError,ModelAttemptError):
            result={'status':'PROPOSAL_FAILED','source':'VALIDATION','candidate':None,'message':'目标提取失败。'}
        with self.store.tx() as c:
            s=self._session(c,p,sid)
            c.execute("UPDATE goal_proposal SET state=?,result=? WHERE id=? AND state='PENDING' AND revision=? AND expires_at>? AND EXISTS (SELECT 1 FROM agent_session WHERE id=? AND revision=?)",(result['status'],canonical(result),pid,rev,self.clock(),sid,rev))
            row=c.execute('SELECT * FROM goal_proposal WHERE id=?',(pid,)).fetchone()
            return self._view(p,row,s['revision'])

    def confirm(self,p,sid,pid,entity,kind='READ_STOCK_WORK'):
        # Exact candidate equality plus actor/session/current revision/expiry.
        # Same proposal can create at most one run, including concurrent retries.
        with self.store.tx() as c:
            s=self._session(c,p,sid)
            row=c.execute('SELECT * FROM goal_proposal WHERE id=? AND session_id=?',(pid,sid)).fetchone()
            if row is None: raise Forbidden('NOT_ACCESSIBLE')
            result=strict_json(row['result']) if row['result'] else {}
            if result.get('candidate')!=entity.model_dump() or result.get('goal_kind','READ_STOCK_WORK')!=kind: raise Conflict('CONFIRMATION_MISMATCH')
            authorize(p,entity)
            if row['state']=='CONFIRMED':
                return {'session_id':sid,'proposal_id':pid,'run_id':row['run_id'],'status':'CONFIRMED'}
            if row['state']!='NEEDS_CONFIRMATION' or row['revision']!=s['revision'] or row['expires_at']<=self.clock():
                raise Conflict('PROPOSAL_NOT_CONFIRMABLE')
            if self._active(c,p,sid): raise Conflict('ACTIVE_RUN')
            goal=GoalContract(kind=kind,entity=entity,provenance='CONFIRMED_STRUCTURED_INPUT')
            # The execution prompt contains only the user-confirmed, single goal.
            # Original user message remains in the proposal audit record.
            message=f'已确认 {kind} {canonical(entity.model_dump())}（只读）' if kind in EXTENDED else f'已确认 {kind} 仓库{entity.warehouse_id}任务{entity.task_id}（只读）'
            rid=self.store.submit_in_tx(c,p,sid,'proposal:'+pid,message,goal)
            c.execute("UPDATE goal_proposal SET state='CONFIRMED',run_id=? WHERE id=?",(rid,pid))
            c.execute('UPDATE agent_session SET revision=revision+1,confirmed_entity=?,confirmed_proposal_id=? WHERE id=?',(canonical(entity.model_dump()),pid,sid))
            return {'session_id':sid,'proposal_id':pid,'run_id':rid,'status':'CONFIRMED'}

    def history(self,p,sid,limit=20,before=None):
        with self.store.tx() as c:
            s=self._session(c,p,sid)
            rows=c.execute('SELECT * FROM goal_proposal WHERE session_id=? AND revision<? ORDER BY revision DESC LIMIT ?', (sid,before if before is not None else s['revision']+1,limit)).fetchall()
            entries=[]
            for row in rows:
                # Expose bounded, authorized metadata and candidate; original
                # raw text and model payload are not replayed into LLM context.
                try: entries.append(self._view(p,row,s['revision']))
                except Forbidden: entries.append({'proposal_id':row['id'],'revision':row['revision'],'status':'REDACTED'})
            confirmed=self._safe_entity(p,strict_json(s['confirmed_entity']) if s['confirmed_entity'] else None)
            return {'session_id':sid,'revision':s['revision'],'confirmed_entity':confirmed,'entries':entries,'next_before':rows[-1]['revision'] if len(rows)==limit else None}
