"""
Database Schema Loader - Multi-DB Edition
Supports: SQL Server, MySQL, PostgreSQL, SQLite
"""

class DBConnection:
    """Wrapper that tracks connection type alongside the DBAPI conn."""
    def __init__(self, conn, db_type: str, database: str = ""):
        self.conn     = conn
        self.db_type  = db_type   # sqlserver | mysql | postgresql | sqlite
        self.database = database

    # Delegate common DBAPI methods so executor.py can call conn.cursor() etc.
    def cursor(self):  return self.conn.cursor()
    def commit(self):  return self.conn.commit()
    def close(self):   return self.conn.close()
    # pandas.read_sql needs a connection-like object
    def __enter__(self): return self.conn.__enter__()
    def __exit__(self, *a): return self.conn.__exit__(*a)


def load_schema(db_conn: DBConnection) -> dict:
    """Load schema from any supported DB. Returns {table: [{column, type}]}"""
    t = db_conn.db_type
    if t == "sqlserver":   return _schema_sqlserver(db_conn.conn)
    if t == "mysql":       return _schema_mysql(db_conn.conn)
    if t == "postgresql":  return _schema_postgresql(db_conn.conn)
    if t == "sqlite":      return _schema_sqlite(db_conn.conn)
    raise ValueError(f"Unknown db_type: {t}")


# ── SQL Server ────────────────────────────────────────────────────────────────
def _schema_sqlserver(conn) -> dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.name, c.name, ty.name
        FROM sys.tables t
        JOIN sys.columns c  ON t.object_id = c.object_id
        JOIN sys.types   ty ON c.user_type_id = ty.user_type_id
        ORDER BY t.name, c.column_id
    """)
    schema = {}
    for table, col, dtype in cursor.fetchall():
        schema.setdefault(table, []).append({"column": col, "type": dtype})
    return schema


# ── MySQL ─────────────────────────────────────────────────────────────────────
def _schema_mysql(conn) -> dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE()
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """)
    schema = {}
    for table, col, dtype in cursor.fetchall():
        schema.setdefault(table, []).append({"column": col, "type": dtype})
    return schema


# ── PostgreSQL ────────────────────────────────────────────────────────────────
def _schema_postgresql(conn) -> dict:
    cursor = conn.cursor()
    cursor.execute("""
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_name, ordinal_position
    """)
    schema = {}
    for table, col, dtype in cursor.fetchall():
        schema.setdefault(table, []).append({"column": col, "type": dtype})
    return schema


# ── SQLite ────────────────────────────────────────────────────────────────────
def _schema_sqlite(conn) -> dict:
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [row[0] for row in cursor.fetchall()]
    schema = {}
    for table in tables:
        cursor.execute(f"PRAGMA table_info(\"{table}\")")
        schema[table] = [{"column": row[1], "type": row[2]} for row in cursor.fetchall()]
    return schema
