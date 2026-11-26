BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 0001_agent

CREATE TABLE run (
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL, session TEXT NOT NULL,
 request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, message TEXT NOT NULL, goal TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'QUEUED', fence BIGINT NOT NULL DEFAULT 0, lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
 cancelled BIGINT NOT NULL DEFAULT 0, result TEXT, UNIQUE(tenant,actor,session,request_key));

CREATE TABLE agent_session (
 id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
 revision BIGINT NOT NULL DEFAULT 0, confirmed_entity TEXT,
 confirmed_proposal_id TEXT, created_at DOUBLE PRECISION NOT NULL);

CREATE TABLE goal_proposal (
 id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES agent_session(id),
 request_key TEXT NOT NULL, message TEXT NOT NULL, request_hash TEXT NOT NULL,
 revision BIGINT NOT NULL, state TEXT NOT NULL, result TEXT,
 created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL, run_id TEXT REFERENCES run(id),
 UNIQUE(session_id,request_key), UNIQUE(session_id,revision));

CREATE TABLE model_attempt (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run(id), attempt_no BIGINT NOT NULL,
 fence BIGINT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL, request_hash TEXT NOT NULL,
 snapshot TEXT NOT NULL, provider_revision TEXT NOT NULL, reserved BIGINT NOT NULL, usage INTEGER,
 accepted BIGINT NOT NULL DEFAULT 0, reason TEXT, UNIQUE(run_id,attempt_no));

CREATE UNIQUE INDEX one_accepted_turn ON model_attempt(run_id) WHERE accepted=1;

CREATE TABLE invocation (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES run(id), request_id TEXT NOT NULL REFERENCES model_attempt(id),
 provider_call_id TEXT NOT NULL, alias TEXT NOT NULL, args TEXT NOT NULL, args_hash TEXT NOT NULL,
 state TEXT NOT NULL, attempts BIGINT NOT NULL DEFAULT 0, result TEXT, evidence_hash TEXT);

CREATE TABLE event (
 seq BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run(id), kind TEXT NOT NULL, detail TEXT NOT NULL);

CREATE INDEX run_queue_idx ON run(tenant,actor,state,lease_until,id);

CREATE INDEX event_run_seq_idx ON event(run_id,seq);

INSERT INTO alembic_version (version_num) VALUES ('0001_agent') RETURNING alembic_version.version_num;

-- Running upgrade 0001_agent -> 0002_steps

ALTER TABLE model_attempt ADD COLUMN step_no BIGINT NOT NULL DEFAULT 1;

ALTER TABLE invocation ADD COLUMN step_no BIGINT NOT NULL DEFAULT 1;

DROP INDEX one_accepted_turn;

CREATE UNIQUE INDEX one_accepted_turn ON model_attempt(run_id,step_no) WHERE accepted=1;

ALTER TABLE invocation DROP CONSTRAINT invocation_run_id_key;

ALTER TABLE invocation ADD CONSTRAINT invocation_run_step_key UNIQUE(run_id,step_no);

UPDATE alembic_version SET version_num='0002_steps' WHERE alembic_version.version_num = '0001_agent';

-- Running upgrade 0002_steps -> 0003_rule_calls

ALTER TABLE run ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'LEGACY_MODEL_V1';

ALTER TABLE invocation ALTER COLUMN request_id DROP NOT NULL;

ALTER TABLE invocation ALTER COLUMN provider_call_id DROP NOT NULL;

ALTER TABLE invocation ADD COLUMN origin TEXT NOT NULL DEFAULT 'MODEL';

ALTER TABLE invocation ADD COLUMN direct_snapshot TEXT;

ALTER TABLE invocation ADD CONSTRAINT invocation_origin_check CHECK (
    (origin='MODEL' AND request_id IS NOT NULL AND provider_call_id IS NOT NULL AND direct_snapshot IS NULL)
    OR (origin='RULE' AND request_id IS NULL AND provider_call_id IS NULL AND direct_snapshot IS NOT NULL));

UPDATE alembic_version SET version_num='0003_rule_calls' WHERE alembic_version.version_num = '0002_steps';

-- Running upgrade 0003_rule_calls -> 0004_cancel_drafts

CREATE TABLE IF NOT EXISTS operation_draft (
        id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
        request_key TEXT NOT NULL, request_hash TEXT NOT NULL,
        run_id TEXT NOT NULL REFERENCES run(id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('AWAITING_REVIEW','REHEARSAL_APPROVED','REJECTED','WITHDRAWN')),
        created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL,
        reviewer TEXT, review_decision TEXT,
        UNIQUE(tenant,actor,request_key));

CREATE TABLE IF NOT EXISTS operation_event (
        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operation_draft(id),
        kind TEXT NOT NULL, actor TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
        detail TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS operation_event_idx ON operation_event(operation_id,created_at,id);

UPDATE alembic_version SET version_num='0004_cancel_drafts' WHERE alembic_version.version_num = '0003_rule_calls';

-- Running upgrade 0004_cancel_drafts -> 0005_rehearsal

CREATE TABLE IF NOT EXISTS rehearsal_execution (
        operation_id TEXT PRIMARY KEY REFERENCES operation_draft(id),
        tenant TEXT NOT NULL, warehouse_id BIGINT NOT NULL,
        command TEXT NOT NULL, request_fingerprint TEXT NOT NULL,
        checkpoint TEXT NOT NULL, fixture_receipt TEXT,
        state TEXT NOT NULL CHECK(state IN ('REHEARSAL_UNKNOWN','REHEARSAL_COMMITTED')),
        fence BIGINT NOT NULL DEFAULT 0, attempts BIGINT NOT NULL DEFAULT 0,
        lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,
        next_at DOUBLE PRECISION NOT NULL, last_code TEXT NOT NULL);

CREATE INDEX IF NOT EXISTS rehearsal_due_idx ON rehearsal_execution(tenant,state,next_at,operation_id);

UPDATE alembic_version SET version_num='0005_rehearsal' WHERE alembic_version.version_num = '0004_cancel_drafts';

COMMIT;

