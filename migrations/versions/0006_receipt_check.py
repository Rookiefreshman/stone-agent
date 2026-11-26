"""Durable read-only receipt verification; no dispatch ledger."""
from alembic import op
revision="0006_receipt_check"
down_revision="0005_rehearsal"
branch_labels=None
depends_on=None
DDL = (
    """CREATE TABLE IF NOT EXISTS receipt_check (
        id TEXT PRIMARY KEY, tenant TEXT NOT NULL, actor TEXT NOT NULL,
        realm TEXT NOT NULL, binding_hash TEXT NOT NULL,
        warehouse_id BIGINT NOT NULL, operation_id TEXT NOT NULL,
        reference TEXT NOT NULL, reference_hash TEXT NOT NULL, checkpoint TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('UNKNOWN','RECEIPT_MATCHED')),
        attempts BIGINT NOT NULL DEFAULT 0, fence BIGINT NOT NULL DEFAULT 0,
        lease_until DOUBLE PRECISION NOT NULL DEFAULT 0, next_at DOUBLE PRECISION NOT NULL,
        held BIGINT NOT NULL DEFAULT 0, last_code TEXT NOT NULL,
        created_at DOUBLE PRECISION NOT NULL, updated_at DOUBLE PRECISION NOT NULL,
        UNIQUE(tenant,actor,realm,warehouse_id,operation_id))""",
    "CREATE INDEX IF NOT EXISTS receipt_check_due_idx ON receipt_check(tenant,actor,realm,binding_hash,state,next_at,id)",
    """CREATE TABLE IF NOT EXISTS receipt_check_attempt (
        check_id TEXT NOT NULL REFERENCES receipt_check(id), fence BIGINT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('CLAIMED','OBSERVED','ABANDONED')),
        claimed_at DOUBLE PRECISION NOT NULL, finished_at DOUBLE PRECISION,
        code TEXT, evidence_hash TEXT, PRIMARY KEY(check_id,fence))""",
)
def upgrade():
    for stmt in DDL: op.execute(stmt)
def downgrade():
    raise RuntimeError("Restore reviewed backup to preserve receipt check audit")
