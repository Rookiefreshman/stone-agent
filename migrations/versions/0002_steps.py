"""Allow one accepted call per logical step, preserving existing step 1 rows."""
from alembic import op
revision='0002_steps'
down_revision='0001_agent'
branch_labels=None
depends_on=None
DDL=[
    'ALTER TABLE model_attempt ADD COLUMN step_no BIGINT NOT NULL DEFAULT 1',
    'ALTER TABLE invocation ADD COLUMN step_no BIGINT NOT NULL DEFAULT 1',
    'DROP INDEX one_accepted_turn',
    'CREATE UNIQUE INDEX one_accepted_turn ON model_attempt(run_id,step_no) WHERE accepted=1',
    'ALTER TABLE invocation DROP CONSTRAINT invocation_run_id_key',
    'ALTER TABLE invocation ADD CONSTRAINT invocation_run_step_key UNIQUE(run_id,step_no)',
]
def upgrade():
    for stmt in DDL: op.execute(stmt)
def downgrade():
    raise RuntimeError('Downgrade is destructive after multi-step runs; restore reviewed backup')
