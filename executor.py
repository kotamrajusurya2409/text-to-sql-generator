"""
SQL Executor - Multi-DB Edition
"""
import pandas as pd
from schema_loader import DBConnection


def execute_sql(db_conn, sql: str) -> pd.DataFrame:
    conn = db_conn.conn if isinstance(db_conn, DBConnection) else db_conn
    sql_strip = sql.strip()
    sql_upper = sql_strip.upper()

    is_dml = any(sql_upper.startswith(k)
                 for k in ("INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "TRUNCATE"))

    if is_dml:
        cursor = conn.cursor()
        cursor.execute(sql_strip)
        conn.commit()
        rows = getattr(cursor, "rowcount", 0)
        return pd.DataFrame([{"rows_affected": rows, "status": "OK"}])
    else:
        try:
            return pd.read_sql(sql_strip, conn)
        except Exception:
            # Fallback for drivers that don't work with pd.read_sql directly
            cursor = conn.cursor()
            cursor.execute(sql_strip)
            cols = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            return pd.DataFrame([dict(zip(cols, r)) for r in rows], columns=cols)
