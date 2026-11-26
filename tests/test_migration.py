import os, subprocess, sys

def test_postgresql_offline_migration():
    env=dict(os.environ,AGENT_DATABASE_URL='postgresql://agent:placeholder@localhost:5432/agent')
    out=subprocess.check_output([sys.executable,'-m','alembic','upgrade','head','--sql'],env=env,text=True)
    assert 'CREATE TABLE run (' in out and 'BIGSERIAL PRIMARY KEY' in out
    assert out.index('CREATE TABLE run (')<out.index('CREATE TABLE goal_proposal (')
    assert 'CREATE UNIQUE INDEX one_accepted_turn' in out and 'run_queue_idx' in out
    assert 'AUTOINCREMENT' not in out and '0001_agent' in out
    assert '0002_steps' in out and 'ON model_attempt(run_id,step_no)' in out and 'UNIQUE(run_id,step_no)' in out
    assert '0003_rule_calls' in out and 'invocation_origin_check' in out and 'ALTER COLUMN request_id DROP NOT NULL' in out
