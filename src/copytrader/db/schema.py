"""
The current schema, read from schema.sql.
"""

from pathlib import Path

SQL = Path(__file__).with_name("schema.sql").read_text()


def create(conn):
    """
    Create every table and index the database does not have yet.
    """
    conn.executescript(SQL)


def table_columns(conn, table):
    """
    The names of a table's columns in this database, none when it has no
    such table, so a migration or a report can tell an older shape.
    """
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
