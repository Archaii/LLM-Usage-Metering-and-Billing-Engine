"""SQL for the alerts table."""
import psycopg


def insert(conn: psycopg.Connection, *, source: str, ref: str | None, message: str) -> None:
    conn.execute(
        "INSERT INTO alerts (source, ref, message) VALUES (%s, %s, %s)",
        (source, ref, message),
    )
