"""Observable evaluation triage, not a proof of underlying model root cause."""
from collections import Counter
from .goal_proposal import ALIAS

def diagnose(expected,actual,raw,turn,error_code,model_requests,exact,execution_ok=True,fixture=False):
    if not execution_ok: return 'CONFIRMATION_OR_RUNTIME_FAILED'
    if exact: return 'MATCHED'
    if error_code: return 'PROVIDER_FAILURE'
    if model_requests:
        if turn is None or turn.finish!='TOOL_CALLS' or len(turn.calls)!=1 or turn.calls[0].name!=ALIAS:
            return 'TOOL_PROTOCOL_MISMATCH'
        if raw is None: return 'CANDIDATE_SCHEMA_INVALID'
        if expected.get('status')=='NEEDS_CONFIRMATION':
            matches=(raw.get('kind')==expected.get('kind') and
                     {k:v for k,v in raw.items() if k!='kind'}==expected.get('candidate'))
            return 'POST_VALIDATION_MISMATCH' if matches else 'RAW_CANDIDATE_MISMATCH'
        # For rejection/clarification cases, an authorization or lexical gate
        # can legitimately transform the raw candidate. Avoid asserting cause.
        return 'NONCONFIRMABLE_EXPECTATION_MISMATCH'
    return 'FIXTURE_OR_CONTEXT_MISMATCH' if fixture else 'CONTEXT_OR_RULE_MISMATCH'

def diagnostic_counts(rows):
    return dict(sorted(Counter(r.get('diagnosis','SKIPPED_AFTER_MISMATCH' if r.get('skipped') else 'UNCLASSIFIED') for r in rows).items()))

def provider_error_counts(rows):
    return dict(sorted(Counter(r['provider_error_code'] for r in rows if r.get('provider_error_code')).items()))
