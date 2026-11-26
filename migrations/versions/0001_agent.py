"""Initial PostgreSQL schema for read-only Stone Agent v0.4."""
from alembic import op
revision='0001_agent'
down_revision=None
branch_labels=None
depends_on=None

DDL = ["CREATE TABLE run (\n id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL, session TEXT NOT NULL,\n request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, message TEXT NOT NULL, goal TEXT NOT NULL,\n state TEXT NOT NULL DEFAULT 'QUEUED', fence BIGINT NOT NULL DEFAULT 0, lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,\n cancelled BIGINT NOT NULL DEFAULT 0, result TEXT, UNIQUE(tenant,actor,session,request_key))", 'CREATE TABLE agent_session (\n id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,\n revision BIGINT NOT NULL DEFAULT 0, confirmed_entity TEXT,\n confirmed_proposal_id TEXT, created_at DOUBLE PRECISION NOT NULL)', 'CREATE TABLE goal_proposal (\n id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES agent_session(id),\n request_key TEXT NOT NULL, message TEXT NOT NULL, request_hash TEXT NOT NULL,\n revision BIGINT NOT NULL, state TEXT NOT NULL, result TEXT,\n created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL, run_id TEXT REFERENCES run(id),\n UNIQUE(session_id,request_key), UNIQUE(session_id,revision))', 'CREATE TABLE model_attempt (\n id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run(id), attempt_no BIGINT NOT NULL,\n fence BIGINT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL, request_hash TEXT NOT NULL,\n snapshot TEXT NOT NULL, provider_revision TEXT NOT NULL, reserved BIGINT NOT NULL, usage INTEGER,\n accepted BIGINT NOT NULL DEFAULT 0, reason TEXT, UNIQUE(run_id,attempt_no))', 'CREATE UNIQUE INDEX one_accepted_turn ON model_attempt(run_id) WHERE accepted=1', 'CREATE TABLE invocation (\n id TEXT PRIMARY KEY, run_id TEXT NOT NULL UNIQUE REFERENCES run(id), request_id TEXT NOT NULL REFERENCES model_attempt(id),\n provider_call_id TEXT NOT NULL, alias TEXT NOT NULL, args TEXT NOT NULL, args_hash TEXT NOT NULL,\n state TEXT NOT NULL, attempts BIGINT NOT NULL DEFAULT 0, result TEXT, evidence_hash TEXT)', 'CREATE TABLE event (\n seq BIGSERIAL PRIMARY KEY, run_id TEXT NOT NULL REFERENCES run(id), kind TEXT NOT NULL, detail TEXT NOT NULL)', 'CREATE INDEX run_queue_idx ON run(tenant,actor,state,lease_until,id)', 'CREATE INDEX event_run_seq_idx ON event(run_id,seq)']

def upgrade():
    for stmt in DDL: op.execute(stmt)

def downgrade():
    raise RuntimeError('Destructive downgrade disabled; use reviewed backup/restore')
