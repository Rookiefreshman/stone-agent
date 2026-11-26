"""Durable synthetic fault rehearsal; no production dispatch."""
from alembic import op
revision="0005_rehearsal"
down_revision="0004_cancel_drafts"
branch_labels=None
depends_on=None
DDL=("CREATE TABLE IF NOT EXISTS rehearsal_execution (\n        operation_id TEXT PRIMARY KEY REFERENCES operation_draft(id),\n        tenant TEXT NOT NULL, warehouse_id BIGINT NOT NULL,\n        command TEXT NOT NULL, request_fingerprint TEXT NOT NULL,\n        checkpoint TEXT NOT NULL, fixture_receipt TEXT,\n        state TEXT NOT NULL CHECK(state IN ('REHEARSAL_UNKNOWN','REHEARSAL_COMMITTED')),\n        fence BIGINT NOT NULL DEFAULT 0, attempts BIGINT NOT NULL DEFAULT 0,\n        lease_until DOUBLE PRECISION NOT NULL DEFAULT 0,\n        next_at DOUBLE PRECISION NOT NULL, last_code TEXT NOT NULL)", 'CREATE INDEX IF NOT EXISTS rehearsal_due_idx ON rehearsal_execution(tenant,state,next_at,operation_id)')
def upgrade():
    for stmt in DDL: op.execute(stmt)
def downgrade():
    raise RuntimeError("Restore reviewed backup to preserve rehearsal audit")
