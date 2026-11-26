"""Persist cancellation drafts and rehearsal review audit; no dispatch table."""
from alembic import op
revision="0004_cancel_drafts"
down_revision="0003_rule_calls"
branch_labels=None
depends_on=None
DDL=("CREATE TABLE IF NOT EXISTS operation_draft (\n        id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,\n        request_key TEXT NOT NULL, request_hash TEXT NOT NULL,\n        run_id TEXT NOT NULL REFERENCES run(id), payload TEXT NOT NULL, payload_hash TEXT NOT NULL,\n        state TEXT NOT NULL CHECK(state IN ('AWAITING_REVIEW','REHEARSAL_APPROVED','REJECTED','WITHDRAWN')),\n        created_at DOUBLE PRECISION NOT NULL, expires_at DOUBLE PRECISION NOT NULL,\n        reviewer TEXT, review_decision TEXT,\n        UNIQUE(tenant,actor,request_key))", 'CREATE TABLE IF NOT EXISTS operation_event (\n        id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operation_draft(id),\n        kind TEXT NOT NULL, actor TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,\n        detail TEXT NOT NULL)', 'CREATE INDEX IF NOT EXISTS operation_event_idx ON operation_event(operation_id,created_at,id)')
def upgrade():
    for stmt in DDL: op.execute(stmt)
def downgrade():
    raise RuntimeError("Restore reviewed backup to preserve approval audit")
