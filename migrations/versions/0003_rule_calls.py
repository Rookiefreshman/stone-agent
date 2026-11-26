"""Record deterministic tool reservations separately from model attempts."""
from alembic import op
revision='0003_rule_calls'
down_revision='0002_steps'
branch_labels=None
depends_on=None
DDL=[
    "ALTER TABLE run ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'LEGACY_MODEL_V1'",
    'ALTER TABLE invocation ALTER COLUMN request_id DROP NOT NULL',
    'ALTER TABLE invocation ALTER COLUMN provider_call_id DROP NOT NULL',
    "ALTER TABLE invocation ADD COLUMN origin TEXT NOT NULL DEFAULT 'MODEL'",
    'ALTER TABLE invocation ADD COLUMN direct_snapshot TEXT',
    """ALTER TABLE invocation ADD CONSTRAINT invocation_origin_check CHECK (
    (origin='MODEL' AND request_id IS NOT NULL AND provider_call_id IS NOT NULL AND direct_snapshot IS NULL)
    OR (origin='RULE' AND request_id IS NULL AND provider_call_id IS NULL AND direct_snapshot IS NOT NULL))""",
]
def upgrade():
    for stmt in DDL: op.execute(stmt)
def downgrade():
    raise RuntimeError('Restore reviewed backup; direct invocations have no model request')
