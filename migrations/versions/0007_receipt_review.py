"""Audited one-shot read-only manual receipt retries."""
from alembic import op
revision="0007_receipt_review"
down_revision="0006_receipt_check"
branch_labels=None
depends_on=None
DDL="""CREATE TABLE IF NOT EXISTS receipt_check_review (
        check_id TEXT NOT NULL REFERENCES receipt_check(id),
        request_id TEXT NOT NULL, expected_fence BIGINT NOT NULL, fence BIGINT NOT NULL,
        actor TEXT NOT NULL, reason TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY(check_id,request_id), UNIQUE(check_id,fence),
        FOREIGN KEY(check_id,fence) REFERENCES receipt_check_attempt(check_id,fence))"""
def upgrade():
    op.execute(DDL)
def downgrade():
    raise RuntimeError("Restore reviewed backup to preserve receipt review audit")
