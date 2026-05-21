"""db_setup.py - EasyBot MySQL schema initializer.

Run this once before starting the bot, or let app.py call init_db() on boot.

Connection is read from environment variables:
  MYSQL_URL     full URL  mysql://user:pass@host:port/db   (Railway provides this)
  or individual MYSQLHOST / MYSQLPORT / MYSQLUSER / MYSQLPASSWORD / MYSQLDATABASE
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

import pymysql
import pymysql.cursors

# Individual CREATE TABLE statements — all idempotent via IF NOT EXISTS.
_TABLES = [
    """
    CREATE TABLE IF NOT EXISTS decisions (
        id                   INT          NOT NULL AUTO_INCREMENT,
        channel_id           VARCHAR(100) NOT NULL,
        message_ts           VARCHAR(100) NOT NULL,
        summary_text         TEXT         NOT NULL,
        author_id            VARCHAR(100) NOT NULL,
        reason_for_decision  TEXT,
        updated_by           VARCHAR(100),
        update_reason        TEXT,
        timestamp            INT          NOT NULL,
        status               VARCHAR(50)  NOT NULL DEFAULT 'pending',
        kind                 VARCHAR(50)  NOT NULL DEFAULT 'decision',
        answer               TEXT,
        PRIMARY KEY (id),
        UNIQUE KEY idx_decisions_source  (channel_id, message_ts),
        KEY        idx_decisions_channel (channel_id),
        KEY        idx_decisions_status  (status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS decision_history (
        id                   INT         NOT NULL AUTO_INCREMENT,
        decision_id          INT         NOT NULL,
        action               VARCHAR(50) NOT NULL,
        actor_id             VARCHAR(100) NOT NULL,
        summary_text         TEXT,
        reason_for_decision  TEXT,
        remark               TEXT,
        timestamp            INT         NOT NULL,
        PRIMARY KEY (id),
        KEY idx_history_decision (decision_id),
        FOREIGN KEY (decision_id) REFERENCES decisions(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS feedback (
        id          INT          NOT NULL AUTO_INCREMENT,
        decision_id INT          NOT NULL,
        user_id     VARCHAR(100) NOT NULL,
        value       INT          NOT NULL,
        timestamp   INT          NOT NULL,
        PRIMARY KEY (id),
        UNIQUE KEY  idx_feedback_unique  (decision_id, user_id),
        KEY         idx_feedback_decision (decision_id),
        FOREIGN KEY (decision_id) REFERENCES decisions(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS digest_state (
        channel_id   VARCHAR(100) NOT NULL,
        last_sent_ts INT          NOT NULL,
        PRIMARY KEY (channel_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def _get_connection() -> pymysql.connections.Connection:
    url = os.environ.get("MYSQL_URL") or os.environ.get("DATABASE_URL")
    if url:
        p = urlparse(url)
        return pymysql.connect(
            host=p.hostname,
            port=p.port or 3306,
            user=p.username,
            password=p.password or "",
            database=p.path.lstrip("/"),
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
        )
    return pymysql.connect(
        host=os.environ.get("MYSQLHOST", "localhost"),
        port=int(os.environ.get("MYSQLPORT", "3306")),
        user=os.environ.get("MYSQLUSER", "root"),
        password=os.environ.get("MYSQLPASSWORD", ""),
        database=os.environ.get("MYSQLDATABASE", "easybot"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _existing_columns(cur, table: str) -> set[str]:
    cur.execute(
        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
        "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
        (table,),
    )
    return {row["COLUMN_NAME"] for row in cur.fetchall()}


def _existing_tables(cur) -> set[str]:
    cur.execute(
        "SELECT TABLE_NAME FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = DATABASE()"
    )
    return {row["TABLE_NAME"] for row in cur.fetchall()}


def _migrate(cur) -> None:
    """Add columns/tables introduced after initial release. Idempotent."""
    cols = _existing_columns(cur, "decisions")
    if "kind" not in cols:
        cur.execute(
            "ALTER TABLE decisions ADD COLUMN kind VARCHAR(50) NOT NULL DEFAULT 'decision'"
        )
    if "answer" not in cols:
        cur.execute("ALTER TABLE decisions ADD COLUMN answer TEXT")

    tables = _existing_tables(cur)
    if "feedback" not in tables:
        cur.execute(
            """
            CREATE TABLE feedback (
                id          INT          NOT NULL AUTO_INCREMENT,
                decision_id INT          NOT NULL,
                user_id     VARCHAR(100) NOT NULL,
                value       INT          NOT NULL,
                timestamp   INT          NOT NULL,
                PRIMARY KEY (id),
                UNIQUE KEY  idx_feedback_unique   (decision_id, user_id),
                KEY         idx_feedback_decision (decision_id),
                FOREIGN KEY (decision_id) REFERENCES decisions(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )
    if "digest_state" not in tables:
        cur.execute(
            """
            CREATE TABLE digest_state (
                channel_id   VARCHAR(100) NOT NULL,
                last_sent_ts INT          NOT NULL,
                PRIMARY KEY (channel_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
            """
        )


def init_db() -> None:
    """Create all tables if they don't exist and run migrations. Safe to call on every boot."""
    con = _get_connection()
    try:
        with con.cursor() as cur:
            for stmt in _TABLES:
                cur.execute(stmt)
            _migrate(cur)
        con.commit()
    finally:
        con.close()


if __name__ == "__main__":
    init_db()
    print("EasyBot MySQL schema initialised.")
