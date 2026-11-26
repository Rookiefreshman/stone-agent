from .contracts import (GoalContract, StockWorkArgs, strict_json, digest, canonical, Forbidden, ProposalError, ModelAttemptError, Conflict, LostClaim, DomainReadError)
from .tools import authorize,validate_call
from .integrations.stock_work import StockWorkPolicy, LOCAL
from .store import RULES, LEGACY

class Runtime:
    def __init__(self,store,provider,registry,policy=None): self.store=store; self.provider=provider; self.registry=registry; self.policy=policy or StockWorkPolicy()
    def advance(self,p,rid,crash_hook=lambda _:None):
        f=self.store.claim(p,rid)
        if f is None: return self.view(p,rid)
        return self.execute_claim(p,rid,f,crash_hook)
    def execute_claim(self,p,rid,f,crash_hook=lambda _:None):
        s=self.store
        with s.tx() as c: s.guard(c,rid,f,allow_cancel=True)
        row=s.get(p,rid); goal=GoalContract.model_validate(strict_json(row['goal']))
        completed=[]
        mode=row['execution_mode']; rule_mode=mode==RULES
        def done(state,code,**kw):
            s.finish(rid,f,state,{'code':code,**kw}); return self.view(p,rid)
        def failure(code):
            if goal.kind!='READ_STOCK_WORK' and completed:
                return done('FAILED',code,goal_status='PARTIAL',completed_steps=completed,
                            unresolved_items=[f'逻辑步骤 {n} 尚未完成' for n in range(len(completed)+1,len(self.policy.plan(goal,rule_mode))+1)])
            return done('FAILED',code)
        try:
            if mode not in (RULES,LEGACY): return done('FAILED','EXECUTION_MODE_INVALID')
            if goal.kind in ('DEEP_DIAGNOSE_STOCK_WORK','DIAGNOSE_OUTBOUND_ALLOCATION','DIAGNOSE_WES_DISPATCH') and not rule_mode: return done('FAILED','RULE_MODE_REQUIRED')
            if row['cancelled']: return done('CANCELLED','CANCELLED')
            if goal.kind=='CLARIFY': return done('NEEDS_INPUT','MISSING_FIELDS',missing_fields=goal.missing_fields)
            if goal.kind=='UNSUPPORTED': return done('FAILED','UNSUPPORTED_GOAL',unresolved_items=goal.unresolved_items)
            for a in self.entities(goal): authorize(p,a)
            plan=self.policy.plan(goal,rule_mode)
            # Each step has a durable invocation. Only legacy model-selected steps create model attempts.
            # New confirmed goals use a fixed, versioned policy.
            for step,(aliases,args) in enumerate(plan,1):
                s.require_active(rid,f); authorize(p,args)
                allowed=aliases-{x['tool'] for x in completed[1:]} if not rule_mode and goal.kind=='ANALYZE_STOCK_WORK' and step>1 else aliases
                prior=completed[0]['facts'] if step>1 and allowed.issubset(LOCAL) else None
                alias,raw=self.step(p,rid,f,row,allowed,args,step,len(plan),prior,crash_hook)
                completed.append({'step_no':step,'tool':alias,'facts':raw,'evidence_hash':digest(raw)})
                if self.policy.should_stop(goal,completed): break
            for a in self.entities(goal): authorize(p,a)
            return done('COMPLETED','OK',**self.policy.outcome(goal,completed,rule_mode))
        except Forbidden: return done('FAILED','FORBIDDEN')
        except ProposalError as e: return failure(str(e))
        except LostClaim:
            if s.cancelled(rid):
                try: return done('CANCELLED','CANCELLED')
                except LostClaim: pass
            raise
    def entities(self,goal): return self.policy.entities(goal)
    def plan(self,goal): return self.policy.plan(goal)
    def step(self,p,rid,f,row,allowed,args,step,total,prior,crash_hook):
        s=self.store; inv=s.invocation(rid,step)
        if inv is None and row['execution_mode']==RULES:
            if len(allowed)!=1: raise ProposalError('AMBIGUOUS_RULE_STEP')
            alias=next(iter(allowed)); snapshot=self.registry.snapshot(allowed)
            if set(snapshot)!=allowed: raise ProposalError('TOOL_NOT_AVAILABLE')
            authorize(p,args)
            s.reserve_rule(rid,f,alias,args.model_dump(),snapshot,step); crash_hook('AFTER_RULE_RESERVE'); inv=s.invocation(rid,step)
            if inv is None: raise ProposalError('RULE_RESERVATION_MISSING')
        if inv is None:
            system='Only select the offered read-only tool for this confirmed logical step. Never modify data.'
            if prior is not None: system+=' Choose one offered remaining evidence analysis tool. Both local analyses use the same verified stock work detail, never infer root cause or inventory balance.'
            body={'messages':[{'role':'system','content':system+'\nCONFIRMED_ENTITY='+canonical(args.model_dump())},{'role':'user','content':row['message']}], 'tools':self.registry.schemas(allowed),'tool_choice':'auto','max_tokens':256}
            if hasattr(self.provider,'model'): body['model']=self.provider.model
            snapshot=self.registry.snapshot(allowed)
            if set(snapshot)!=allowed: raise ProposalError('TOOL_NOT_AVAILABLE')
            # Preserve legacy limits for DIRECT. At most 2 attempts per step, 6 total.
            while inv is None:
                q=s.prepare(rid,f,body,snapshot,self.provider.revision,step_no=step,total_attempts=2*total,token_budget=8192*total)
                if q is None: raise ProposalError('MODEL_BUDGET_EXHAUSTED')
                crash_hook('AFTER_PREPARE'); authorize(p,args); s.send_intent(rid,f,q,body,self.provider.revision); crash_hook('AFTER_SEND_INTENT'); s.require_active(rid,f); authorize(p,args)
                try: turn=self.provider.generate(body)
                except ModelAttemptError:
                    s.fail_attempt(rid,f,q,'MODEL_ATTEMPT_UNKNOWN',uncertain=True); continue
                usage=turn.usage if isinstance(turn.usage,int) and turn.usage>=0 else None
                try:
                    if turn.finish!='TOOL_CALLS' or len(turn.calls)!=1: raise ProposalError('EXPECTED_ONE_READ')
                    call=turn.calls[0]; self.registry.resolve(call.name,snapshot)
                    g=GoalContract(kind='READ_STOCK_WORK',entity=args,provenance='CONFIRMED_STRUCTURED_INPUT')
                    validated=validate_call(g,call,p)
                except (ProposalError,Forbidden) as e:
                    s.fail_attempt(rid,f,q,str(e),usage); raise ProposalError(str(e)) from e
                s.accept(rid,f,q,call,validated.model_dump(),usage,step_no=step); crash_hook('AFTER_ACCEPT'); inv=s.invocation(rid,step)
        if inv['origin']!=('RULE' if row['execution_mode']==RULES else 'MODEL'): raise ProposalError('EXECUTION_ORIGIN_MISMATCH')
        if not inv['accepted'] or digest(strict_json(inv['args']))!=inv['args_hash']: raise ProposalError('ACCEPTED_RECORD_INVALID')
        alias=inv['alias']
        if alias not in allowed: raise ProposalError('STEP_TOOL_MISMATCH')
        spec=self.registry.resolve(inv['alias'],strict_json(inv['snapshot']))
        saved=spec.args_model.model_validate(strict_json(inv['args'])); authorize(p,saved)
        if saved!=args: raise ProposalError('GOAL_MISMATCH')
        def checked(raw): return self.policy.validate_result(p,args,alias,raw,prior)
        if inv['state']=='SUCCEEDED':
            raw=strict_json(inv['result'])
            if digest(raw)!=inv['evidence_hash']: raise ProposalError('EVIDENCE_INVALID')
            try: return alias,checked(raw)
            except ValueError as exc: raise ProposalError('EVIDENCE_INVALID') from exc
        if inv['state']=='FAILED': raise ProposalError('READ_FAILED')
        try:
            s.dispatch(rid,f,inv['id']); crash_hook('AFTER_READ_INTENT'); s.require_active(rid,f); authorize(p,args)
            raw=spec.handler(p,args,prior) if alias in LOCAL else spec.handler(p,args)
            crash_hook('AFTER_HANDLER_BEFORE_COMMIT')
            raw=checked(raw); s.complete_read(rid,f,inv['id'],raw); crash_hook('AFTER_READ_RESULT'); return alias,raw
        except DomainReadError as e:
            s.fail_read(rid,f,inv['id'],str(e)); raise ProposalError(str(e)) from e
        except (ValueError,Conflict,Forbidden) as e:
            s.fail_read(rid,f,inv['id'],'READ_CONTRACT_OR_POLICY_ERROR'); raise ProposalError('READ_CONTRACT_OR_POLICY_ERROR') from e
    def view(self,p,rid):
        row=self.store.get(p,rid); goal=GoalContract.model_validate(strict_json(row['goal']))
        for args in self.entities(goal): authorize(p,args)
        return {'run_id':rid,'state':row['state'],'outcome':strict_json(row['result']) if row['result'] else None}
