import json, urllib.request, urllib.parse
from .contracts import ModelTurn, ToolCall, ModelAttemptError, canonical

class FixtureProvider:
    """Deterministic mock used for contract and fault tests, NOT language-model evaluation."""
    revision='fixture-v1'
    def __init__(self): self.calls=0
    def generate(self, body):
        self.calls+=1
        entity=json.loads(body['messages'][0]['content'].split('\nCONFIRMED_ENTITY=')[1])
        return ModelTurn('TOOL_CALLS',(ToolCall('fixture-call',body['tools'][0]['function']['name'],canonical(entity)),),20)

class ChatCompatibleProvider:
    """Optional real provider, non-streaming, fixed configured URL, no automatic retry."""
    def __init__(self,base_url,model,api_key='',timeout=15):
        u=urllib.parse.urlsplit(base_url)
        if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('invalid administrator-configured model endpoint')
        self.url=base_url.rstrip('/')+'/chat/completions'; self.model=model; self.key=api_key; self.timeout=timeout
        self.revision='chat:'+self.url+':'+model
    def generate(self,body):
        if body.get('model')!=self.model: raise ModelAttemptError('model profile mismatch')
        payload=body
        headers={'Content-Type':'application/json'}
        if self.key: headers['Authorization']='Bearer '+self.key
        req=urllib.request.Request(self.url,json.dumps(payload).encode(),headers,method='POST')
        # No redirects: endpoint is an operator trust boundary.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs): return None
        try:
            with urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect).open(req,timeout=self.timeout) as res:
                raw=res.read(1_000_001)
            if len(raw)>1_000_000: raise ValueError('oversized response')
            obj=json.loads(raw); c=obj['choices'][0]; msg=c['message']
            calls=tuple(ToolCall(x['id'],x['function']['name'],x['function']['arguments']) for x in (msg.get('tool_calls') or []))
            finish={'tool_calls':'TOOL_CALLS','stop':'STOP','length':'INCOMPLETE','content_filter':'FILTERED'}.get(c.get('finish_reason'),'ERROR')
            return ModelTurn(finish,calls,obj.get('usage',{}).get('total_tokens'))
        except Exception as exc:
            raise ModelAttemptError('model transport or protocol error') from exc
