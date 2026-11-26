"""PostgreSQL repository; only constant internal SQL is translated."""
from contextlib import contextmanager
from .store import Store,validate_execution_mode

class Row(dict):
    def __getitem__(self,key):
        return tuple(self.values())[key] if isinstance(key,int) else super().__getitem__(key)

class Cursor:
    def __init__(self,cur): self.cur=cur
    @property
    def rowcount(self): return self.cur.rowcount
    def _row(self,values):
        return Row(zip((c.name for c in self.cur.description),values)) if values is not None else None
    def fetchone(self): return self._row(self.cur.fetchone())
    def fetchall(self): return [self._row(v) for v in self.cur.fetchall()]
    def __iter__(self):
        for v in self.cur: yield self._row(v)

class Connection:
    def __init__(self,conn): self.conn=conn
    def execute(self,sql,params=()):
        return Cursor(self.conn.execute(sql.replace('?', '%s'),params))

class PostgresStore(Store):
    def __init__(self,dsn,execution_mode="LEGACY_MODEL_V1"):
        self.execution_mode=validate_execution_mode(execution_mode)
        import psycopg
        self.driver=psycopg; self.dsn=dsn
        with self.tx() as c:
            row=c.execute('SELECT version_num FROM alembic_version').fetchone()
            if not row or row[0]!='0006_receipt_check': raise RuntimeError('run Alembic upgrade head before starting')
    @contextmanager
    def tx(self):
        with self.driver.connect(self.dsn,connect_timeout=5) as conn:
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout = '5s'")
                conn.execute("SET LOCAL statement_timeout = '10s'")
                yield Connection(conn)
    def locked(self,c,sql,params=(),skip=False):
        return c.execute(sql+' FOR UPDATE'+(' SKIP LOCKED' if skip else ''),params).fetchone()
    def now(self,c): return float(c.execute('SELECT EXTRACT(EPOCH FROM clock_timestamp())').fetchone()[0])
