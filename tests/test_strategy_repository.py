"""Check the real strategy creation helper with a recording connection.

The connection records transaction boundaries and pending statements. These
checks verify how the helper uses a transaction, not PostgreSQL's own behavior.
"""

import re
import unittest
from types import SimpleNamespace

from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

from database.strategy_repository import (
    Duplicate_Name_Prob,
    create_strategy_with_initial_version,
)


SOURCE = "\nclass ReviewStrategy:\n    pass\n"


class RecordingConnection:
    """Supply rows and record work that commits or is discarded on failure."""

    def __init__(self, rows, *, version_error=None):
        self.rows = iter(rows)
        self.version_error = version_error
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)
        self.events = []
        self.statements = []
        self.pending = []
        self.committed = []
        self.row_factory = None

    def transaction(self):
        """Expose a transaction context without opening a real connection."""
        return RecordingTransaction(self)

    def cursor(self, *, row_factory=None):
        """Capture the requested row shape for the helper's returned ID lookups."""
        self.row_factory = row_factory
        return RecordingCursor(self)


class RecordingTransaction:
    """Record context completion without implementing SQL or database rules."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        if self.conn.info.transaction_status != TransactionStatus.IDLE:
            raise AssertionError("Unexpected nested transaction")
        self.conn.info.transaction_status = TransactionStatus.INTRANS
        self.conn.events.append("begin")
        return self

    def __exit__(self, error_type, error, traceback):
        if error_type is None:
            self.conn.committed.extend(self.conn.pending)
            self.conn.events.append("commit")
        else:
            self.conn.events.append("rollback")
        self.conn.pending.clear()
        self.conn.info.transaction_status = TransactionStatus.IDLE
        return False


class RecordingCursor:
    """Record parameterized inserts and provide the scripted RETURNING rows."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, error_type, error, traceback):
        return False

    def execute(self, query, params=None):
        if self.conn.info.transaction_status != TransactionStatus.INTRANS:
            raise AssertionError("Query executed outside the transaction")
        match = re.search(
            r"\bINSERT\s+INTO\s+(public\.(?:strategies|strategy_versions))\b",
            query, re.IGNORECASE,
        )
        if match is None:
            raise AssertionError("Unexpected query in initial strategy creation")
        table = match.group(1).casefold()
        statement = (query, params)
        self.conn.statements.append(statement)
        self.conn.events.append(table)
        if table == "public.strategy_versions" and self.conn.version_error is not None:
            raise self.conn.version_error
        self.conn.pending.append(statement)

    def fetchone(self):
        if self.conn.info.transaction_status != TransactionStatus.INTRANS:
            raise AssertionError("Row requested outside the transaction")
        try:
            return next(self.conn.rows)
        except StopIteration:
            raise AssertionError("Unexpected extra RETURNING row request") from None


class StrategyCreationTests(unittest.TestCase):
    """Call the saved creation helper directly rather than replacing it."""

    def test_success_commits_strategy_and_initial_version_before_returning_ids(self):
        """Check the two inserts share one transaction and retain the supplied source."""
        for description, stored_description in (
            ("  Review description  ", "Review description"),
            ("   ", None),
        ):
            with self.subTest(description=description):
                conn = RecordingConnection([
                    {"strategy_id": 701}, {"strategy_version_id": 9907},
                ])
                result = create_strategy_with_initial_version(
                    conn, name="  Review strategy  ", description=description,
                    source_code=SOURCE, entrypoint_name="ReviewStrategy",
                )
                self.assertEqual(result, {
                    "strategy_id": 701, "strategy_version_id": 9907,
                })
                self.assertEqual(conn.events, [
                    "begin", "public.strategies", "public.strategy_versions", "commit",
                ])
                self.assertEqual(len(conn.committed), 2)
                self.assertEqual(conn.pending, [])
                self.assertEqual(conn.info.transaction_status, TransactionStatus.IDLE)
                self.assertIs(conn.row_factory, dict_row)
                strategy_sql, strategy_params = conn.committed[0]
                version_sql, version_params = conn.committed[1]
                self.assertIn("ON CONFLICT", strategy_sql.upper())
                self.assertIn("DO NOTHING", strategy_sql.upper())
                self.assertEqual(strategy_params, ("Review strategy", stored_description))
                self.assertRegex(
                    version_sql,
                    r"(?i)\bVALUES\s*\(\s*%s\s*,\s*1\s*,\s*%s\s*,\s*%s\s*\)",
                )
                self.assertEqual(version_params, (701, "ReviewStrategy", SOURCE))

    def test_duplicate_name_raises_before_any_version_insert(self):
        """A skipped name insert must not reuse an existing strategy or add a version."""
        conn = RecordingConnection([None])
        with self.assertRaises(Duplicate_Name_Prob):
            create_strategy_with_initial_version(
                conn, name="Taken", source_code=SOURCE,
                entrypoint_name="ReviewStrategy",
            )
        self.assertEqual(conn.events, ["begin", "public.strategies", "rollback"])
        self.assertEqual(len(conn.statements), 1)
        self.assertEqual(conn.committed, [])
        self.assertEqual(conn.pending, [])
        self.assertEqual(conn.info.transaction_status, TransactionStatus.IDLE)

    def test_version_insert_failure_rolls_back_the_pending_strategy(self):
        """Propagate the original failure and leave neither insert committed."""
        failure = RuntimeError("Review fixture rejected the version insert")
        conn = RecordingConnection([{"strategy_id": 701}], version_error=failure)
        with self.assertRaises(RuntimeError) as raised:
            create_strategy_with_initial_version(
                conn, name="Review strategy", source_code=SOURCE,
                entrypoint_name="ReviewStrategy",
            )
        self.assertIs(raised.exception, failure)
        self.assertEqual(conn.events, [
            "begin", "public.strategies", "public.strategy_versions", "rollback",
        ])
        self.assertEqual(len(conn.statements), 2)
        self.assertEqual(conn.statements[1][1], (701, "ReviewStrategy", SOURCE))
        self.assertEqual(conn.committed, [])
        self.assertEqual(conn.pending, [])
        self.assertEqual(conn.info.transaction_status, TransactionStatus.IDLE)
