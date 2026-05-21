"""test_easybot.py — unit + integration tests for EasyBot.

DB tests (TestDB) require a live MySQL server — they are skipped automatically
if no MySQL connection vars are present.  All other test classes (Views, LLM,
Permissions, Cache) run fully offline with no external dependencies.

Run all tests:
    python -m pytest test_easybot.py -v

Run offline-only tests (no MySQL needed):
    python -m pytest test_easybot.py -v -k "not TestDB"
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import time
import types
import unittest
from unittest.mock import MagicMock, patch

os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test")
os.environ.setdefault("SLACK_APP_TOKEN", "xapp-test")
os.environ.setdefault("SLACK_SIGNING_SECRET", "test-secret")
os.environ.setdefault("GEMINI_API_KEY", "test-key")

import db_setup
import db
import views


def _mysql_available() -> bool:
    """Return True only if we can actually connect to a MySQL server."""
    if not (os.environ.get("MYSQL_URL") or os.environ.get("MYSQLHOST")):
        return False
    try:
        db_setup.init_db()
        return True
    except Exception:
        return False


_MYSQL_AVAILABLE = _mysql_available()


# ═══════════════════════════════════════════════════════════════════
# 1. DB layer  (requires MySQL — skipped otherwise)
# ═══════════════════════════════════════════════════════════════════
@unittest.skipUnless(_MYSQL_AVAILABLE, "TestDB requires MySQL — set MYSQL_URL or MYSQLHOST")
class TestDB(unittest.TestCase):

    def setUp(self):
        # Wipe all rows before each test for isolation
        import pymysql
        import pymysql.cursors
        con = db._get_connection()
        with con.cursor() as cur:
            cur.execute("DELETE FROM decision_history")
            cur.execute("DELETE FROM decisions")
            cur.execute("DELETE FROM feedback")
            cur.execute("DELETE FROM digest_state")
        con.commit()
        con.close()

    # ── create_pending_decision ──────────────────────────────────
    def test_create_pending_decision_returns_id(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="111.0",
            summary_text="We use SQLite", author_id="U001",
            reason_for_decision="simple", captured_by="U001",
        )
        self.assertIsInstance(did, int)
        self.assertGreater(did, 0)

    def test_create_pending_decision_status_is_pending(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="112.0",
            summary_text="Deploy on Fridays is banned", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        row = db.get_decision(did)
        self.assertEqual(row["status"], "pending")

    def test_create_doubt_kind(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="113.0",
            summary_text="What is the DB?", author_id="U002",
            reason_for_decision=None, captured_by="U001",
            kind="doubt", answer="SQLite",
        )
        row = db.get_decision(did)
        self.assertEqual(row["kind"], "doubt")
        self.assertEqual(row["answer"], "SQLite")

    # ── approve_decision ─────────────────────────────────────────
    def test_approve_decision_flips_status(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="114.0",
            summary_text="Use black for formatting", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        updated = db.approve_decision(did, "U001")
        self.assertEqual(updated["status"], "approved")

    def test_approve_writes_history_row(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="115.0",
            summary_text="PRs need two approvals", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        history = db.list_history(did)
        actions = [h["action"] for h in history]
        self.assertIn("created", actions)
        self.assertIn("approved", actions)

    # ── discard_decision ─────────────────────────────────────────
    def test_discard_removes_pending(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="116.0",
            summary_text="Throwaway", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.discard_decision(did)
        self.assertIsNone(db.get_decision(did))

    def test_discard_does_not_remove_approved(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="117.0",
            summary_text="Keep this one", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        db.discard_decision(did)
        self.assertIsNotNone(db.get_decision(did))

    # ── update_decision ──────────────────────────────────────────
    def test_update_decision_changes_summary(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="118.0",
            summary_text="Old summary", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        updated = db.update_decision(
            did, new_summary="New summary", new_reason=None,
            actor_id="U001", update_reason="corrected",
        )
        self.assertEqual(updated["summary_text"], "New summary")

    def test_update_decision_writes_history(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="119.0",
            summary_text="Draft", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        db.update_decision(
            did, new_summary="Final", new_reason=None,
            actor_id="U001", update_reason="polished",
        )
        history = db.list_history(did)
        self.assertTrue(any(h["action"] == "updated" for h in history))

    def test_update_doubt_answer(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="120.0",
            summary_text="Q?", author_id="U001",
            reason_for_decision=None, captured_by="U001",
            kind="doubt", answer="old answer",
        )
        db.approve_decision(did, "U001")
        updated = db.update_decision(
            did, new_summary="Q?", new_reason=None, new_answer="new answer",
            actor_id="U001", update_reason="updated answer",
        )
        self.assertEqual(updated["answer"], "new answer")

    # ── delete_decision ──────────────────────────────────────────
    def test_delete_decision_soft_deletes(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="121.0",
            summary_text="To delete", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        result = db.delete_decision(did, "U001")
        self.assertTrue(result)
        row = db.get_decision(did)
        self.assertEqual(row["status"], "deleted")

    def test_delete_writes_history(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="122.0",
            summary_text="To delete", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        db.delete_decision(did, "U001")
        history = db.list_history(did)
        self.assertTrue(any(h["action"] == "deleted" for h in history))

    def test_delete_pending_fails(self):
        did = db.create_pending_decision(
            channel_id="C001", message_ts="123.0",
            summary_text="Pending", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        result = db.delete_decision(did, "U001")
        self.assertFalse(result)

    # ── list_approved ────────────────────────────────────────────
    def test_list_approved_only_returns_approved(self):
        db.create_pending_decision(
            channel_id="C002", message_ts="131.0",
            summary_text="Pending only", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        did2 = db.create_pending_decision(
            channel_id="C002", message_ts="132.0",
            summary_text="Approved one", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did2, "U001")
        results = db.list_approved("C002")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["summary_text"], "Approved one")

    # ── search_decisions ─────────────────────────────────────────
    def test_search_finds_by_keyword(self):
        did = db.create_pending_decision(
            channel_id="C003", message_ts="141.0",
            summary_text="We deploy every Tuesday", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        results = db.search_decisions("Tuesday", channel_id="C003")
        self.assertTrue(any("Tuesday" in r["summary_text"] for r in results))

    def test_search_returns_empty_for_no_match(self):
        results = db.search_decisions("xyzzy_nonexistent", channel_id="C003")
        self.assertEqual(results, [])

    def test_search_global_finds_across_channels(self):
        did = db.create_pending_decision(
            channel_id="C004", message_ts="151.0",
            summary_text="Cross channel entry", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        results = db.search_decisions("Cross channel", channel_id=None)
        self.assertTrue(len(results) >= 1)

    # ── feedback ─────────────────────────────────────────────────
    def test_record_and_get_feedback(self):
        did = db.create_pending_decision(
            channel_id="C005", message_ts="161.0",
            summary_text="Feedback test", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        db.record_feedback(did, "U002", 1)
        db.record_feedback(did, "U003", -1)
        summary = db.get_feedback_summary(did)
        self.assertEqual(summary["helpful"], 1)
        self.assertEqual(summary["unhelpful"], 1)

    def test_feedback_upsert(self):
        did = db.create_pending_decision(
            channel_id="C005", message_ts="162.0",
            summary_text="Upsert test", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        db.record_feedback(did, "U002", 1)
        db.record_feedback(did, "U002", -1)   # same user changes vote
        summary = db.get_feedback_summary(did)
        self.assertEqual(summary["helpful"], 0)
        self.assertEqual(summary["unhelpful"], 1)

    # ── digest helpers ───────────────────────────────────────────
    def test_get_set_last_digest_ts(self):
        self.assertIsNone(db.get_last_digest_ts("C_DIGEST"))
        db.set_last_digest_ts("C_DIGEST", 12345)
        self.assertEqual(db.get_last_digest_ts("C_DIGEST"), 12345)

    def test_get_stale_decisions(self):
        did = db.create_pending_decision(
            channel_id="C006", message_ts="171.0",
            summary_text="Old entry", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        # Manually backdate the timestamp
        con = db._get_connection()
        with con.cursor() as cur:
            cur.execute("UPDATE decisions SET timestamp = 0 WHERE id = %s", (did,))
        con.commit()
        con.close()
        stale = db.get_stale_decisions("C006", days=1)
        self.assertTrue(any(d["id"] == did for d in stale))

    def test_get_channel_ids_with_decisions(self):
        did = db.create_pending_decision(
            channel_id="C_UNIQUE_999", message_ts="181.0",
            summary_text="Channel test", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        db.approve_decision(did, "U001")
        channels = db.get_channel_ids_with_decisions()
        self.assertIn("C_UNIQUE_999", channels)

    # ── get_decision_by_source ───────────────────────────────────
    def test_get_decision_by_source(self):
        db.create_pending_decision(
            channel_id="C007", message_ts="191.0",
            summary_text="Source lookup", author_id="U001",
            reason_for_decision=None, captured_by="U001",
        )
        row = db.get_decision_by_source("C007", "191.0")
        self.assertIsNotNone(row)
        self.assertEqual(row["summary_text"], "Source lookup")

    def test_get_decision_by_source_returns_none_for_unknown(self):
        row = db.get_decision_by_source("CNONE", "0.0")
        self.assertIsNone(row)


# ═══════════════════════════════════════════════════════════════════
# 2. Views layer — Block Kit structure
# ═══════════════════════════════════════════════════════════════════
class TestViews(unittest.TestCase):

    def _sample_decision(self, kind="decision", ts=None):
        return {
            "id": 1,
            "channel_id": "C001",
            "summary_text": "We use SQLite",
            "author_id": "U001",
            "reason_for_decision": "Simple and zero-dependency",
            "answer": "SQLite" if kind == "doubt" else None,
            "kind": kind,
            "timestamp": ts or int(time.time()),
            "status": "approved",
        }

    # ── approval_card ────────────────────────────────────────────
    def test_approval_card_has_approve_and_discard_buttons(self):
        blocks = views.approval_card(
            decision_id=1, channel_id="C001", summary="S",
            author_id="U001", reason=None,
        )
        action_ids = [
            el["action_id"]
            for b in blocks if b["type"] == "actions"
            for el in b["elements"]
        ]
        self.assertIn("easybot_approve_decision", action_ids)
        self.assertIn("easybot_discard_decision", action_ids)

    def test_approval_card_doubt_shows_answer(self):
        blocks = views.approval_card(
            decision_id=2, channel_id="C001", summary="Q?",
            author_id="U001", reason=None, kind="doubt", answer="42",
        )
        text_blocks = [b for b in blocks if b.get("type") == "section"]
        combined = " ".join(b["text"]["text"] for b in text_blocks)
        self.assertIn("42", combined)

    # ── match_card ───────────────────────────────────────────────
    def test_match_card_has_feedback_buttons(self):
        blocks = views.match_card(self._sample_decision())
        action_ids = [
            el["action_id"]
            for b in blocks if b["type"] == "actions"
            for el in b["elements"]
        ]
        self.assertIn("easybot_feedback_helpful", action_ids)
        self.assertIn("easybot_feedback_unhelpful", action_ids)

    def test_match_card_has_history_button(self):
        blocks = views.match_card(self._sample_decision())
        action_ids = [
            el["action_id"]
            for b in blocks if b["type"] == "actions"
            for el in b["elements"]
        ]
        self.assertIn("easybot_ask_why_updated", action_ids)

    def test_match_card_doubt_shows_answer(self):
        blocks = views.match_card(self._sample_decision(kind="doubt"))
        texts = [
            b["text"]["text"]
            for b in blocks if b.get("type") == "section"
        ]
        self.assertTrue(any("SQLite" in t for t in texts))

    def test_match_card_staleness_warning_for_old_entry(self):
        old_ts = int(time.time()) - 100 * 86400
        blocks = views.match_card(self._sample_decision(ts=old_ts))
        context_texts = [
            el["text"]
            for b in blocks if b.get("type") == "context"
            for el in b["elements"]
        ]
        self.assertTrue(any("⚠️" in t for t in context_texts))

    def test_match_card_no_staleness_for_fresh_entry(self):
        blocks = views.match_card(self._sample_decision())
        context_texts = [
            el["text"]
            for b in blocks if b.get("type") == "context"
            for el in b["elements"]
        ]
        self.assertFalse(any("⚠️" in t for t in context_texts))

    # ── uncertain_match_card ─────────────────────────────────────
    def test_uncertain_match_card_shows_confidence(self):
        blocks = views.uncertain_match_card(self._sample_decision(), 0.62)
        text = blocks[0]["text"]["text"]
        self.assertIn("62%", text)

    def test_uncertain_match_card_has_feedback_buttons(self):
        blocks = views.uncertain_match_card(self._sample_decision(), 0.6)
        action_ids = [
            el["action_id"]
            for b in blocks if b["type"] == "actions"
            for el in b["elements"]
        ]
        self.assertIn("easybot_feedback_helpful", action_ids)
        self.assertIn("easybot_feedback_unhelpful", action_ids)

    # ── fallback_card ────────────────────────────────────────────
    def test_fallback_card_has_request_button(self):
        blocks = views.fallback_card("C001", "111.0", "What is X?")
        action_ids = [
            el["action_id"]
            for b in blocks if b["type"] == "actions"
            for el in b["elements"]
        ]
        self.assertIn("easybot_request_owner_save", action_ids)

    def test_fallback_card_payload_is_valid_json(self):
        blocks = views.fallback_card("C001", "111.0", "Q?")
        for b in blocks:
            if b["type"] == "actions":
                value = b["elements"][0]["value"]
                parsed = json.loads(value)
                self.assertEqual(parsed["channel_id"], "C001")
                self.assertEqual(parsed["message_ts"], "111.0")

    # ── history_card ─────────────────────────────────────────────
    def test_history_card_has_update_and_delete_buttons(self):
        blocks = views.history_card(self._sample_decision(), [])
        action_ids = [
            el["action_id"]
            for b in blocks if b["type"] == "actions"
            for el in b["elements"]
        ]
        self.assertIn("easybot_open_update_modal", action_ids)
        self.assertIn("easybot_delete_decision", action_ids)

    def test_history_card_renders_timeline(self):
        history_rows = [
            {"action": "created", "actor_id": "U001",
             "timestamp": int(time.time()), "summary_text": "Draft",
             "reason_for_decision": None, "remark": None},
            {"action": "approved", "actor_id": "U001",
             "timestamp": int(time.time()), "summary_text": "Draft",
             "reason_for_decision": None, "remark": None},
        ]
        blocks = views.history_card(self._sample_decision(), history_rows)
        texts = " ".join(
            b["text"]["text"] for b in blocks if b.get("type") == "section"
        )
        self.assertIn("CREATED", texts)
        self.assertIn("APPROVED", texts)

    # ── update_modal ─────────────────────────────────────────────
    def test_update_modal_decision_has_reason_block(self):
        modal = views.update_modal(self._sample_decision())
        block_ids = [b["block_id"] for b in modal["blocks"]]
        self.assertIn("reason", block_ids)
        self.assertNotIn("answer", block_ids)

    def test_update_modal_doubt_has_answer_block(self):
        modal = views.update_modal(self._sample_decision(kind="doubt"))
        block_ids = [b["block_id"] for b in modal["blocks"]]
        self.assertIn("answer", block_ids)
        self.assertNotIn("reason", block_ids)

    def test_update_modal_has_remark_block(self):
        modal = views.update_modal(self._sample_decision())
        block_ids = [b["block_id"] for b in modal["blocks"]]
        self.assertIn("remark", block_ids)

    # ── search_results_card ──────────────────────────────────────
    def test_search_results_card_empty(self):
        blocks = views.search_results_card([], "nothing")
        self.assertTrue(any("No results" in str(b) for b in blocks))

    def test_search_results_card_shows_results(self):
        results = [self._sample_decision()]
        blocks = views.search_results_card(results, "SQLite")
        texts = " ".join(str(b) for b in blocks)
        self.assertIn("SQLite", texts)

    # ── list_card ────────────────────────────────────────────────
    def test_list_card_empty(self):
        blocks = views.list_card([])
        self.assertTrue(any("No saved" in str(b) for b in blocks))

    def test_list_card_shows_entries(self):
        blocks = views.list_card([self._sample_decision()])
        texts = " ".join(str(b) for b in blocks)
        self.assertIn("SQLite", texts)

    # ── digest_message ───────────────────────────────────────────
    def test_digest_message_with_new_entries(self):
        msg = views.digest_message("C001", [self._sample_decision()], [])
        self.assertIn("1 new entry", msg)
        self.assertIn("C001", msg)

    def test_digest_message_with_stale(self):
        msg = views.digest_message("C001", [], [self._sample_decision()])
        self.assertIn("⚠️", msg)

    def test_digest_message_empty(self):
        msg = views.digest_message("C001", [], [])
        self.assertIn("No new entries", msg)


# ═══════════════════════════════════════════════════════════════════
# 3. LLM helpers (no actual API calls)
# ═══════════════════════════════════════════════════════════════════
class TestLLM(unittest.TestCase):

    def test_find_match_returns_none_for_empty_candidates(self):
        import llm
        result = llm.find_match("What is X?", [])
        self.assertIsNone(result)

    def test_find_match_respects_custom_threshold(self):
        import llm
        fake_out = {"match_index": 0, "confidence": 0.7, "rationale": "ok"}
        with patch.object(llm, "_chat_json", return_value=fake_out):
            candidates = [{"id": 1, "summary_text": "X", "kind": "decision",
                           "reason_for_decision": None, "answer": None}]
            # threshold=0.8 → should miss (0.7 < 0.8)
            result = llm.find_match("X?", candidates, threshold=0.8)
            self.assertIsNone(result)
            # threshold=0.6 → should hit (0.7 >= 0.6)
            result = llm.find_match("X?", candidates, threshold=0.6)
            self.assertIsNotNone(result)
            self.assertAlmostEqual(result["_match_confidence"], 0.7)

    def test_find_duplicate_uses_high_threshold(self):
        import llm
        # confidence=0.80 → should be below find_duplicate threshold (0.85)
        fake_out = {"match_index": 0, "confidence": 0.80, "rationale": "similar"}
        with patch.object(llm, "_chat_json", return_value=fake_out):
            candidates = [{"id": 1, "summary_text": "X", "kind": "decision",
                           "reason_for_decision": None, "answer": None}]
            result = llm.find_duplicate("X", candidates)
            self.assertIsNone(result)

    def test_find_duplicate_matches_above_threshold(self):
        import llm
        fake_out = {"match_index": 0, "confidence": 0.92, "rationale": "identical"}
        with patch.object(llm, "_chat_json", return_value=fake_out):
            candidates = [{"id": 1, "summary_text": "X", "kind": "decision",
                           "reason_for_decision": None, "answer": None}]
            result = llm.find_duplicate("X", candidates)
            self.assertIsNotNone(result)

    def test_find_match_attaches_confidence(self):
        import llm
        fake_out = {"match_index": 0, "confidence": 0.88, "rationale": "clear match"}
        with patch.object(llm, "_chat_json", return_value=fake_out):
            candidates = [{"id": 5, "summary_text": "Deploy on Tuesdays",
                           "kind": "decision", "reason_for_decision": None, "answer": None}]
            result = llm.find_match("When do we deploy?", candidates)
            self.assertIsNotNone(result)
            self.assertAlmostEqual(result["_match_confidence"], 0.88)
            self.assertEqual(result["_match_rationale"], "clear match")

    def test_find_match_bad_index_returns_none(self):
        import llm
        fake_out = {"match_index": 99, "confidence": 0.9, "rationale": "out of bounds"}
        with patch.object(llm, "_chat_json", return_value=fake_out):
            candidates = [{"id": 1, "summary_text": "X", "kind": "decision",
                           "reason_for_decision": None, "answer": None}]
            result = llm.find_match("Q?", candidates)
            self.assertIsNone(result)

    def test_render_thread_skips_bot_messages(self):
        import llm
        messages = [
            {"user": "U001", "text": "hello", "subtype": None},
            {"user": "BOT", "text": "I am a bot", "subtype": "bot_message"},
            {"user": "U002", "text": "world", "subtype": None},
        ]
        rendered = llm._render_thread(messages)
        self.assertNotIn("I am a bot", rendered)
        self.assertIn("hello", rendered)
        self.assertIn("world", rendered)


# ═══════════════════════════════════════════════════════════════════
# 4. Permissions helpers
# ═══════════════════════════════════════════════════════════════════
class TestPermissions(unittest.TestCase):

    def test_is_dm_true_for_im(self):
        import permissions
        info = {"is_im": True, "is_mpim": False}
        self.assertTrue(permissions.is_dm(info))

    def test_is_dm_false_for_channel(self):
        import permissions
        info = {"is_im": False, "is_mpim": False}
        self.assertFalse(permissions.is_dm(info))

    def test_is_allowed_conversation_public_channel(self):
        import permissions
        info = {"is_channel": True, "is_im": False, "is_mpim": False, "is_group": False}
        self.assertTrue(permissions.is_allowed_conversation(info))

    def test_is_allowed_conversation_rejects_dm(self):
        import permissions
        info = {"is_channel": False, "is_im": True, "is_mpim": False, "is_group": False}
        self.assertFalse(permissions.is_allowed_conversation(info))

    def test_channel_owner_id(self):
        import permissions
        info = {"creator": "U_OWNER"}
        self.assertEqual(permissions.channel_owner_id(info), "U_OWNER")

    def test_channel_owner_id_missing(self):
        import permissions
        self.assertIsNone(permissions.channel_owner_id({}))


# ═══════════════════════════════════════════════════════════════════
# 5. Interception cache helpers
# ═══════════════════════════════════════════════════════════════════
class TestInterceptionCache(unittest.TestCase):

    def setUp(self):
        from handlers import interception
        interception._match_cache.clear()
        self.mod = interception

    def test_cache_miss_on_empty(self):
        hit, val = self.mod._get_cached("C1", "hello?")
        self.assertFalse(hit)
        self.assertIsNone(val)

    def test_cache_hit_after_set(self):
        self.mod._set_cache("C1", "hello?", {"id": 1})
        hit, val = self.mod._get_cached("C1", "hello?")
        self.assertTrue(hit)
        self.assertEqual(val["id"], 1)

    def test_cache_normalises_whitespace(self):
        self.mod._set_cache("C1", "hello   world", {"id": 2})
        hit, val = self.mod._get_cached("C1", "hello world")
        self.assertTrue(hit)

    def test_cache_expired_is_miss(self):
        self.mod._match_cache[("C1", "stale")] = ({"id": 3}, time.time() - 1)
        hit, _ = self.mod._get_cached("C1", "stale")
        self.assertFalse(hit)

    def test_looks_like_question_with_mark(self):
        self.assertTrue(self.mod._looks_like_question("Is this working?"))

    def test_looks_like_question_with_wh_word(self):
        self.assertTrue(self.mod._looks_like_question("What is the deploy process"))

    def test_looks_like_question_rejects_statement(self):
        self.assertFalse(self.mod._looks_like_question("Great job everyone"))

    def test_is_trivial_short(self):
        self.assertTrue(self.mod._is_trivial("hi"))

    def test_is_trivial_long_enough(self):
        self.assertFalse(self.mod._is_trivial("what is the deploy process?"))


# ═══════════════════════════════════════════════════════════════════
# Entry point
# ═══════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    unittest.main(verbosity=2)
