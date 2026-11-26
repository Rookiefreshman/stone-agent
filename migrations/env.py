import os
from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url
url=make_url(os.environ['AGENT_DATABASE_URL']).set(drivername='postgresql+psycopg')
if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, transactional_ddl=True)
    with context.begin_transaction(): context.run_migrations()
else:
    engine=create_engine(url,poolclass=pool.NullPool)
    with engine.connect() as conn:
        conn.exec_driver_sql('SELECT pg_advisory_lock(73014204)'); conn.commit()
        context.configure(connection=conn,transactional_ddl=True)
        with context.begin_transaction(): context.run_migrations()
        conn.exec_driver_sql('SELECT pg_advisory_unlock(73014204)'); conn.commit()
