"""Fixed-endpoint, non-streaming OpenAI-compatible model transport."""
import http.client
import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request

from .contracts import ModelTurn, ToolCall, ModelAttemptError, canonical, strict_json

MAX_RESPONSE_BYTES=1_000_000
MAX_TOOL_CALLS=16
ERROR_CODES=frozenset({
    'MODEL_PROFILE_MISMATCH','MODEL_HTTP_AUTH','MODEL_HTTP_RATE_LIMIT',
    'MODEL_HTTP_REDIRECT','MODEL_HTTP_CLIENT','MODEL_HTTP_SERVER','MODEL_HTTP_OTHER',
    'MODEL_TIMEOUT','MODEL_TRANSPORT','MODEL_RESPONSE_TOO_LARGE',
    'MODEL_RESPONSE_PROTOCOL',
})

class ProviderError(ModelAttemptError):
    """Stable diagnostic code; no endpoint, credentials, body or upstream error text."""
    def __init__(self,code):
        if code not in ERROR_CODES:
            raise ValueError('invalid provider diagnostic code')
        self.code=code
        super().__init__(code)

def safe_error_code(exc):
    return exc.code if isinstance(exc,ProviderError) and exc.code in ERROR_CODES else 'MODEL_ATTEMPT_ERROR'

class FixtureProvider:
    """Deterministic mock used for contract and fault tests, NOT language-model evaluation."""
    revision='fixture-v1'
    def __init__(self): self.calls=0
    def generate(self, body):
        self.calls+=1
        entity=json.loads(body['messages'][0]['content'].split('\nCONFIRMED_ENTITY=')[1])
        return ModelTurn('TOOL_CALLS',(ToolCall('fixture-call',body['tools'][0]['function']['name'],canonical(entity)),),20)

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None

def http_error_code(status):
    if status in (401,403): return 'MODEL_HTTP_AUTH'
    if status==429: return 'MODEL_HTTP_RATE_LIMIT'
    if 300<=status<400: return 'MODEL_HTTP_REDIRECT'
    if 400<=status<500: return 'MODEL_HTTP_CLIENT'
    if 500<=status<600: return 'MODEL_HTTP_SERVER'
    return 'MODEL_HTTP_OTHER'

def _nonempty(value,limit):
    return isinstance(value,str) and 0<len(value)<=limit and bool(value.strip())

def decode_turn(raw):
    """Validate the outer protocol only; Candidate/tool schemas validate arguments later."""
    if len(raw)>MAX_RESPONSE_BYTES:
        raise ProviderError('MODEL_RESPONSE_TOO_LARGE')
    try:
        obj=strict_json(raw.decode('utf-8'))
        if not isinstance(obj,dict) or obj.get('error') is not None:
            raise ValueError('invalid completion envelope')
        choices=obj.get('choices')
        if not isinstance(choices,list) or len(choices)!=1 or not isinstance(choices[0],dict):
            raise ValueError('exactly one choice required')
        choice=choices[0]
        if 'index' in choice and (type(choice['index']) is not int or choice['index']!=0):
            raise ValueError('invalid choice index')
        msg=choice.get('message')
        if not isinstance(msg,dict) or msg.get('role','assistant')!='assistant':
            raise ValueError('invalid assistant message')
        reason=choice.get('finish_reason')
        finish={'tool_calls':'TOOL_CALLS','stop':'STOP','length':'INCOMPLETE',
                'content_filter':'FILTERED'}.get(reason)
        if finish is None:
            raise ValueError('unrecognized finish reason')
        usage=obj.get('usage')
        if usage is not None and not isinstance(usage,dict):
            raise ValueError('invalid usage')
        tokens=usage.get('total_tokens') if usage is not None else None
        if tokens is not None and (type(tokens) is not int or tokens<0):
            raise ValueError('invalid reported token count')
        # A truncated/content-filtered turn may contain a partial tool call.
        # Never make that partial call available to the downstream dispatcher.
        if finish!='TOOL_CALLS':
            return ModelTurn(finish,(),tokens)
        calls=msg.get('tool_calls')
        if not isinstance(calls,list) or not 1<=len(calls)<=MAX_TOOL_CALLS:
            raise ValueError('invalid tool call count')
        result=[]
        seen=set()
        for call in calls:
            if not isinstance(call,dict) or call.get('type')!='function':
                raise ValueError('invalid tool type')
            call_id=call.get('id')
            function=call.get('function')
            if not _nonempty(call_id,256) or call_id in seen or not isinstance(function,dict):
                raise ValueError('invalid or duplicate tool call id')
            name=function.get('name')
            arguments=function.get('arguments')
            if not _nonempty(name,128) or not isinstance(arguments,str):
                raise ValueError('invalid function call')
            seen.add(call_id)
            result.append(ToolCall(call_id,name,arguments))
        return ModelTurn(finish,tuple(result),tokens)
    except (ValueError,TypeError,UnicodeError,RecursionError):
        raise ProviderError('MODEL_RESPONSE_PROTOCOL') from None

class ChatCompatibleProvider:
    """Optional real provider: one POST per generate, no redirects/proxy/retry."""
    def __init__(self,base_url,model,api_key='',timeout=15):
        u=urllib.parse.urlsplit(base_url)
        if (u.scheme not in ('http','https') or not u.hostname or u.username is not None
                or u.password is not None or u.query or u.fragment):
            raise ValueError('invalid administrator-configured model endpoint')
        _=u.port
        if not _nonempty(model,256) or type(timeout) not in (int,float) or not math.isfinite(timeout) or timeout<=0:
            raise ValueError('invalid model profile')
        self.url=base_url.rstrip('/')+'/chat/completions'; self.model=model; self.key=api_key; self.timeout=timeout
        # Endpoint identity stays stable for existing persisted attempts.
        self.revision='chat:'+self.url+':'+model
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect)

    def generate(self,body):
        if body.get('model')!=self.model:
            raise ProviderError('MODEL_PROFILE_MISMATCH')
        headers={'Content-Type':'application/json','Accept':'application/json'}
        if self.key: headers['Authorization']='Bearer '+self.key
        try:
            req=urllib.request.Request(self.url,json.dumps(body,allow_nan=False).encode(),headers,method='POST')
            with self.opener.open(req,timeout=self.timeout) as res:
                raw=res.read(MAX_RESPONSE_BYTES+1)
        except urllib.error.HTTPError as exc:
            code=http_error_code(exc.code)
            exc.close()
            raise ProviderError(code) from None
        except (TimeoutError,socket.timeout):
            raise ProviderError('MODEL_TIMEOUT') from None
        except urllib.error.URLError as exc:
            code='MODEL_TIMEOUT' if isinstance(exc.reason,(TimeoutError,socket.timeout)) else 'MODEL_TRANSPORT'
            raise ProviderError(code) from None
        except (OSError,http.client.HTTPException,ValueError,TypeError,UnicodeError):
            raise ProviderError('MODEL_TRANSPORT') from None
        return decode_turn(raw)
