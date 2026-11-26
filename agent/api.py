"""Local development API. Token and identity mapping are test-only, not enterprise IdP."""
import os, hmac, time
from typing import Literal
from fastapi.responses import StreamingResponse
from .store import TERMINAL
from .contracts import canonical
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from .contracts import Principal,GoalContract,StockWorkArgs,Forbidden,Conflict,LostClaim
from .tools import authorize
from .main import build
from .goal_proposal import propose
from .session import SessionService
from .operations import OperationService, DraftRequest, ReviewRequest
from fastapi import Query

class Submit(BaseModel):
    model_config=ConfigDict(extra='forbid')
    client_request_id: str=Field(min_length=1,max_length=128)
    message: str=Field(min_length=1,max_length=4000)
    goal: GoalContract

class Propose(BaseModel):
    model_config=ConfigDict(extra='forbid', strict=True)
    message: str=Field(min_length=1,max_length=4000)

class SessionPropose(Propose):
    client_request_id: str=Field(min_length=1,max_length=128)

class Confirm(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    entity: StockWorkArgs
    kind: Literal['READ_STOCK_WORK','DIAGNOSE_STOCK_WORK','ANALYZE_STOCK_WORK']='READ_STOCK_WORK'


def create_app():
    token=os.environ.get('POC_DEV_TOKEN')
    if not token or len(token)<16: raise RuntimeError('set POC_DEV_TOKEN (at least 16 characters); local development only')
    runtime=build(os.getenv('POC_DB','demo.sqlite'),os.getenv('POC_LIVE_MODEL')=='1')
    sessions=SessionService(runtime.store,runtime.provider if os.getenv('POC_LIVE_MODEL')=='1' else None)
    app=FastAPI(title='Stone Agent v0.9 — local development')
    review_token=os.getenv('POC_REVIEW_TOKEN','')
    rehearsal=os.getenv('POC_REHEARSAL_ENABLED')=='1'
    if rehearsal and (len(review_token)<16 or hmac.compare_digest(review_token,token) or os.getenv('STONE_LIVE_DOMAIN')=='1'):
        raise RuntimeError('rehearsal requires distinct reviewer token and fixture domain')
    operations=OperationService(runtime.store,runtime.registry,rehearsal_enabled=rehearsal)
    def reviewer(auth):
        if not rehearsal or not auth or not hmac.compare_digest(auth,'Bearer '+review_token): raise HTTPException(403,'Rehearsal reviewer required')
        return Principal('demo','local-reviewer',frozenset(int(x) for x in os.getenv('POC_REVIEW_WAREHOUSES','').split(',') if x))
    def principal(auth):
        if not auth or not hmac.compare_digest(auth,'Bearer '+token): raise HTTPException(401,'Unauthorized')
        return Principal('demo','local-dev',frozenset(int(x) for x in os.getenv('STONE_ALLOWED_WAREHOUSES','1').split(',')))
    def guard(fn):
        try: return fn()
        except Forbidden: raise HTTPException(403,'Not accessible')
        except (Conflict,LostClaim): raise HTTPException(409,'Conflict or stale claim')
    @app.post('/api/v1/sessions',status_code=201)
    def create_session(authorization:str|None=Header(None)):
        return guard(lambda:sessions.create(principal(authorization)))
    @app.get('/api/v1/sessions/{session_id}')
    def session_history(session_id:str,limit:int=Query(20,ge=1,le=100),before:int|None=Query(None,ge=1),authorization:str|None=Header(None)):
        return guard(lambda:sessions.history(principal(authorization),session_id,limit,before))
    @app.post('/api/v1/sessions/{session_id}/goal-proposals')
    def session_proposal(session_id:str,req:SessionPropose,authorization:str|None=Header(None)):
        p=principal(authorization)
        if not req.message.strip(): raise HTTPException(422,'Empty message')
        return guard(lambda:sessions.propose(p,session_id,req.client_request_id,req.message))
    @app.post('/api/v1/sessions/{session_id}/goal-proposals/{proposal_id}/confirm',status_code=202)
    def confirm(session_id:str,proposal_id:str,req:Confirm,authorization:str|None=Header(None)):
        return guard(lambda:sessions.confirm(principal(authorization),session_id,proposal_id,req.entity,req.kind))
    @app.post('/api/v1/goal-proposals')
    def goal_proposal(req:Propose,authorization:str|None=Header(None)):
        p=principal(authorization)
        if not req.message.strip(): raise HTTPException(422,'Empty message')
        return guard(lambda:propose(req.message,p,runtime.provider if os.getenv('POC_LIVE_MODEL')=='1' else None))
    @app.post('/api/v1/sessions/{session_id}/runs',status_code=202)
    def submit(session_id:str,req:Submit,authorization:str|None=Header(None)):
        p=principal(authorization)
        if req.goal.provenance!='CONFIRMED_STRUCTURED_INPUT': raise HTTPException(422,'Explicit structured goal required')
        def op():
            for entity in runtime.entities(req.goal): authorize(p,entity)
            rid=runtime.store.submit(p,session_id,req.client_request_id,req.message,req.goal)
            return runtime.view(p,rid)
        return guard(op)
    @app.get('/api/v1/runs/{run_id}')
    def get(run_id:str,authorization:str|None=Header(None)):
        return guard(lambda:runtime.view(principal(authorization),run_id))
    @app.get('/api/v1/runs/{run_id}/events')
    def events(run_id:str,after:int=Query(0,ge=0),last_event_id:str|None=Header(None),authorization:str|None=Header(None)):
        if last_event_id is not None:
            try:
                after=max(after,int(last_event_id))
            except ValueError: raise HTTPException(422,'Invalid Last-Event-ID')
        if after<0 or after>9223372036854775807: raise HTTPException(422,'Invalid cursor')
        guard(lambda:runtime.view(principal(authorization),run_id))
        def stream():
            cursor=after
            # Bounded connection lifetime; reconnect with last event ID.
            end=time.monotonic()+30
            while time.monotonic()<end:
                try:
                    p=principal(authorization)
                    runtime.view(p,run_id)
                    state,rows=runtime.store.events_after(p,run_id,cursor)
                except (Forbidden,HTTPException): return
                for row in rows:
                    cursor=row['seq']
                    yield f"id: {cursor}\nevent: run_event\ndata: {canonical(row)}\n\n"
                if state in TERMINAL and len(rows)<100: return
                if not rows: yield ': heartbeat\n\n'; time.sleep(1)
        return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache','X-Accel-Buffering':'no'})
    @app.post('/api/v1/runs/{run_id}/advance')
    def advance(run_id:str,authorization:str|None=Header(None)):
        # Compatibility/debug trigger; worker mode does not require this route.
        return guard(lambda:runtime.advance(principal(authorization),run_id))
    @app.post('/api/v1/runs/{run_id}/cancel')
    def cancel(run_id:str,authorization:str|None=Header(None)):
        p=principal(authorization)
        def op(): runtime.store.cancel(p,run_id); return runtime.view(p,run_id)
        return guard(op)
    @app.get('/api/v1/operations/preflight/{run_id}')
    def preflight(run_id:str,warehouse_id:int=Query(...,ge=1,le=9223372036854775807),task_id:int=Query(...,ge=1,le=9223372036854775807),authorization:str|None=Header(None)):
        return guard(lambda:operations.preflight(principal(authorization),run_id,StockWorkArgs(warehouse_id=warehouse_id,task_id=task_id)))
    @app.post('/api/v1/operations/cancel-drafts',status_code=201)
    def draft(req:DraftRequest,authorization:str|None=Header(None)):
        return guard(lambda:operations.create(principal(authorization),req))
    @app.get('/api/v1/operations/{operation_id}')
    def operation(operation_id:str,authorization:str|None=Header(None)):
        return guard(lambda:operations.get(principal(authorization),operation_id))
    @app.get('/api/v1/rehearsal/reviews/{operation_id}')
    def review_view(operation_id:str,authorization:str|None=Header(None)):
        return guard(lambda:operations.get(reviewer(authorization),operation_id,reviewer=True))
    @app.post('/api/v1/rehearsal/reviews/{operation_id}')
    def review(operation_id:str,req:ReviewRequest,authorization:str|None=Header(None)):
        return guard(lambda:operations.review(reviewer(authorization),operation_id,req))
    @app.post('/api/v1/operations/{operation_id}/withdraw')
    def withdraw(operation_id:str,authorization:str|None=Header(None)):
        return guard(lambda:operations.withdraw(principal(authorization),operation_id))
    return app
