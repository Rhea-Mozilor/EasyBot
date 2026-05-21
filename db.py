"""db.py - MySQL query layer for EasyBot."""
from __future__ import annotations

import os
import time
from contextlib import contextmanager
from typing import Optional
from urllib.parse import urlparse

import pymysql
import pymysql.cursors


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


@contextmanager
def _conn():
    con = _get_connection()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #
def create_pending_decision(
    *,
    channel_id: str,
    message_ts: str,
    summary_text: str,
    author_id: str,
    reason_for_decision: Optional[str],
    captured_by: str,
    kind: str = "decision",
    answer: Optional[str] = None,
) -> int:
    now = int(time.time())
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO decisions
                  (channel_id, message_ts, summary_text, author_id,
                   reason_for_decision, timestamp, status, kind, answer)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending', %s, %s)
                """,
                (channel_id, message_ts, summary_text, author_id,
                 reason_for_decision, now, kind, answer),
            )
            decision_id = cur.lastrowid
            cur.execute(
                """
                INSERT INTO decision_history
                  (decision_id, action, actor_id, summary_text,
                   reason_for_decision, remark, timestamp)
                VALUES (%s, 'created', %s, %s, %s, %s, %s)
                """,
                (decision_id, captured_by, summary_text, reason_for_decision,
                 answer, now),
            )
            return decision_id


def approve_decision(decision_id: int, owner_id: str) -> Optional[dict]:
    now = int(time.time())
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM decisions WHERE id = %s", (decision_id,))
            row = cur.fetchone()
            if not row or row["status"] == "approved":
                return row
            cur.execute(
                "UPDATE decisions SET status = 'approved', timestamp = %s WHERE id = %s",
                (now, decision_id),
            )
            cur.execute(
                """
                INSERT INTO decision_history
                  (decision_id, action, actor_id, summary_text,
                   reason_for_decision, remark, timestamp)
                VALUES (%s, 'approved', %s, %s, %s, NULL, %s)
                """,
                (decision_id, owner_id, row["summary_text"],
                 row["reason_for_decision"], now),
            )
            cur.execute("SELECT * FROM decisions WHERE id = %s", (decision_id,))
            return cur.fetchone()


def discard_decision(decision_id: int) -> None:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "DELETE FROM decisions WHERE id = %s AND status = 'pending'",
                (decision_id,),
            )


def update_decision(
    decision_id: int,
    *,
    new_summary: Optional[str],
    new_reason: Optional[str],
    new_answer: Optional[str] = None,
    actor_id: str,
    update_reason: str,
) -> Optional[dict]:
    now = int(time.time())
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM decisions WHERE id = %s", (decision_id,))
            row = cur.fetchone()
            if not row:
                return None
            summary = new_summary if new_summary else row["summary_text"]
            reason  = new_reason  if new_reason  is not None else row["reason_for_decision"]
            answer  = new_answer  if new_answer  is not None else row["answer"]
            cur.execute(
                """
                UPDATE decisions
                   SET summary_text = %s, reason_for_decision = %s, answer = %s,
                       updated_by = %s, update_reason = %s, timestamp = %s
                 WHERE id = %s
                """,
                (summary, reason, answer, actor_id, update_reason, now, decision_id),
            )
            cur.execute(
                """
                INSERT INTO decision_history
                  (decision_id, action, actor_id, summary_text,
                   reason_for_decision, remark, timestamp)
                VALUES (%s, 'updated', %s, %s, %s, %s, %s)
                """,
                (decision_id, actor_id, summary, reason, update_reason, now),
            )
            cur.execute("SELECT * FROM decisions WHERE id = %s", (decision_id,))
            return cur.fetchone()


def get_decision(decision_id: int) -> Optional[dict]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute("SELECT * FROM decisions WHERE id = %s", (decision_id,))
            return cur.fetchone()


def get_decision_by_source(channel_id: str, message_ts: str) -> Optional[dict]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM decisions WHERE channel_id = %s AND message_ts = %s",
                (channel_id, message_ts),
            )
            return cur.fetchone()


def list_approved(channel_id: str) -> list[dict]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM decisions WHERE channel_id = %s AND status = 'approved' "
                "ORDER BY timestamp DESC",
                (channel_id,),
            )
            return cur.fetchall()


def list_history(decision_id: int) -> list[dict]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM decision_history WHERE decision_id = %s "
                "ORDER BY timestamp ASC, id ASC",
                (decision_id,),
            )
            return cur.fetchall()


def delete_decision(decision_id: int, actor_id: str) -> bool:
    now = int(time.time())
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM decisions WHERE id = %s AND status = 'approved'",
                (decision_id,),
            )
            row = cur.fetchone()
            if not row:
                return False
            cur.execute(
                "UPDATE decisions SET status = 'deleted', timestamp = %s WHERE id = %s",
                (now, decision_id),
            )
            cur.execute(
                """
                INSERT INTO decision_history
                  (decision_id, action, actor_id, summary_text,
                   reason_for_decision, remark, timestamp)
                VALUES (%s, 'deleted', %s, %s, %s, 'Entry deleted by owner', %s)
                """,
                (decision_id, actor_id, row["summary_text"],
                 row["reason_for_decision"], now),
            )
            return True


def search_decisions(query: str, channel_id: Optional[str] = None) -> list[dict]:
    q = f"%{query}%"
    with _conn() as con:
        with con.cursor() as cur:
            if channel_id:
                cur.execute(
                    """
                    SELECT * FROM decisions
                    WHERE status = 'approved' AND channel_id = %s
                      AND (summary_text LIKE %s OR reason_for_decision LIKE %s OR answer LIKE %s)
                    ORDER BY timestamp DESC LIMIT 20
                    """,
                    (channel_id, q, q, q),
                )
            else:
                cur.execute(
                    """
                    SELECT * FROM decisions
                    WHERE status = 'approved'
                      AND (summary_text LIKE %s OR reason_for_decision LIKE %s OR answer LIKE %s)
                    ORDER BY timestamp DESC LIMIT 20
                    """,
                    (q, q, q),
                )
            return cur.fetchall()


# --------------------------------------------------------------------------- #
# Feedback
# --------------------------------------------------------------------------- #
def record_feedback(decision_id: int, user_id: str, value: int) -> None:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO feedback (decision_id, user_id, value, timestamp)
                VALUES (%s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE value = VALUES(value), timestamp = VALUES(timestamp)
                """,
                (decision_id, user_id, value, int(time.time())),
            )


def get_feedback_summary(decision_id: int) -> dict:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM feedback WHERE decision_id = %s AND value = 1",
                (decision_id,),
            )
            helpful = cur.fetchone()["cnt"]
            cur.execute(
                "SELECT COUNT(*) AS cnt FROM feedback WHERE decision_id = %s AND value = -1",
                (decision_id,),
            )
            unhelpful = cur.fetchone()["cnt"]
            return {"helpful": helpful, "unhelpful": unhelpful}


# --------------------------------------------------------------------------- #
# Digest
# --------------------------------------------------------------------------- #
def get_channel_ids_with_decisions() -> list[str]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT DISTINCT channel_id FROM decisions WHERE status = 'approved'"
            )
            return [r["channel_id"] for r in cur.fetchall()]


def get_recent_decisions(channel_id: str, since_ts: int) -> list[dict]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM decisions WHERE channel_id = %s AND status = 'approved' "
                "AND timestamp >= %s ORDER BY timestamp DESC",
                (channel_id, since_ts),
            )
            return cur.fetchall()


def get_stale_decisions(channel_id: str, days: int = 90) -> list[dict]:
    cutoff = int(time.time()) - days * 86400
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT * FROM decisions WHERE channel_id = %s AND status = 'approved' "
                "AND timestamp < %s ORDER BY timestamp ASC",
                (channel_id, cutoff),
            )
            return cur.fetchall()


def get_last_digest_ts(channel_id: str) -> Optional[int]:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                "SELECT last_sent_ts FROM digest_state WHERE channel_id = %s",
                (channel_id,),
            )
            row = cur.fetchone()
            return row["last_sent_ts"] if row else None


def set_last_digest_ts(channel_id: str, ts: int) -> None:
    with _conn() as con:
        with con.cursor() as cur:
            cur.execute(
                """
                INSERT INTO digest_state (channel_id, last_sent_ts) VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE last_sent_ts = VALUES(last_sent_ts)
                """,
                (channel_id, ts),
            )
