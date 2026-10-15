"""External-app-only, durable spending reservations; no RP text or API keys.

One ledger per billing policy, shared across bridge/save folders. An unresolved
request blocks further spending across restarts. Never auto-refund ambiguity.
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .pricing import SpendingBlocked, SpendingPolicy, display_usd
from .protocol import validate_request_id


class SpendLedger:
    def __init__(self, path: Path, policy: SpendingPolicy):
        self.path, self.policy = path, policy

    @contextmanager
    def _transaction(self):
        connection = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self.path.is_symlink():
                raise SpendingBlocked("Symlinked spending ledger refused")
            self.path.touch(mode=0o600, exist_ok=True)
            self.path.chmod(0o600)
            connection = sqlite3.connect(self.path, timeout=2, isolation_level=None)
            connection.execute("BEGIN IMMEDIATE")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise SpendingBlocked("Unsupported spending ledger version")
            connection.execute("CREATE TABLE IF NOT EXISTS spend (request_id TEXT PRIMARY KEY, day TEXT NOT NULL, "
                               "model TEXT NOT NULL, state TEXT NOT NULL, reserved INTEGER NOT NULL, "
                               "charged INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER, output_tokens INTEGER)")
            connection.execute("CREATE INDEX IF NOT EXISTS spend_day ON spend(day)")
            connection.execute("CREATE INDEX IF NOT EXISTS spend_state ON spend(state)")
            connection.execute("CREATE TABLE IF NOT EXISTS adjustments (seq INTEGER PRIMARY KEY, request_id TEXT NOT NULL, "
                               "previous_state TEXT NOT NULL, previous_charge INTEGER NOT NULL, confirmed_charge INTEGER NOT NULL, "
                               "recorded_at TEXT NOT NULL)")
            connection.execute("PRAGMA user_version=2")
            yield connection
            connection.commit()
        except sqlite3.Error:
            raise SpendingBlocked("Spending ledger unavailable; no paid call allowed") from None
        finally:
            if connection is not None:
                connection.close()  # Rolls back on exception, no leaked lock.

    @staticmethod
    def _day() -> str:
        return datetime.now(timezone.utc).date().isoformat()

    def reserve(self, request_id: str) -> int:
        validate_request_id(request_id)
        self.policy.check()  # Expiry is checked every call, not only at startup.
        ceiling = self.policy.ceiling()
        day = self._day()
        with self._transaction() as db:
            if db.execute("SELECT 1 FROM spend WHERE request_id=?", (request_id,)).fetchone():
                raise SpendingBlocked("Duplicate paid request refused; do not replay archived requests")
            if db.execute("SELECT 1 FROM spend WHERE state != 'settled' LIMIT 1").fetchone():
                raise SpendingBlocked("An in-flight, uncertain, or overrun charge needs review before further spending")
            total, today = db.execute("SELECT COALESCE(SUM(charged),0), "
                                     "COALESCE(SUM(CASE WHEN day=? THEN charged ELSE 0 END),0) FROM spend", (day,)).fetchone()
            if (ceiling > self.policy.per_request or total + ceiling > self.policy.total
                    or today + ceiling > self.policy.daily):
                raise SpendingBlocked("USD limit reached; no model call made")
            db.execute("INSERT INTO spend(request_id,day,model,state,reserved) VALUES(?,?,?,'reserved',?)",
                       (request_id, day, self.policy.model, ceiling))
        return ceiling

    def settle(self, request_id: str, cost: int, input_tokens: int = 0, output_tokens: int = 0) -> None:
        if any(type(v) is not int or v < 0 for v in (cost, input_tokens, output_tokens)):
            raise SpendingBlocked("Invalid settlement")
        overrun = False
        with self._transaction() as db:
            row = db.execute("SELECT state,reserved FROM spend WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row[0] != "reserved":
                raise SpendingBlocked("No unsettled reservation for this request")
            overrun = cost > row[1]
            db.execute("UPDATE spend SET state=?,charged=?,input_tokens=?,output_tokens=? WHERE request_id=?",
                       ("overrun" if overrun else "settled", cost, input_tokens, output_tokens, request_id))
        if overrun:
            raise SpendingBlocked("Provider cost exceeded reservation; further spending blocked")

    def report(self) -> dict:
        with self._transaction() as db:
            charged, held, pending, inp, out = db.execute(
                "SELECT COALESCE(SUM(charged),0), COALESCE(SUM(CASE WHEN state='reserved' THEN reserved ELSE 0 END),0), "
                "COALESCE(SUM(CASE WHEN state!='settled' THEN 1 ELSE 0 END),0), "
                "COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0) FROM spend").fetchone()
            today = db.execute("SELECT COALESCE(SUM(charged),0) FROM spend WHERE day=?", (self._day(),)).fetchone()[0]
            unresolved = db.execute("SELECT request_id,day,model,state FROM spend WHERE state!='settled' LIMIT 20").fetchall()
        return {"currency": "USD", "day_basis": "UTC", "recorded_usd": display_usd(charged),
                "today_usd": display_usd(today), "reserved_usd": display_usd(held), "unresolved_requests": pending,
                "input_tokens": inp, "output_tokens": out, "daily_limit_usd": display_usd(self.policy.daily),
                "total_limit_usd": display_usd(self.policy.total), "per_request_limit_usd": display_usd(self.policy.per_request),
                "needs_review": [dict(zip(("request_id", "day", "model", "state"), row)) for row in unresolved]}

    def reconcile(self, request_id: str, confirmed_cost: int, *, no_active_calls: bool = False) -> None:
        """Explicit operator reconciliation, after checking provider billing.

        A timed-out request may still be running remotely. The CLI requires
        confirmation that companions/calls stopped; never call this on a timer.
        Keep the prior charge and resolution in an append-only audit table.
        """
        validate_request_id(request_id)
        if not no_active_calls or type(confirmed_cost) is not int or not 0 <= confirmed_cost <= 100_000_000_000_000:
            raise SpendingBlocked("Stop companion calls and confirm the provider charge before reconciliation")
        with self._transaction() as db:
            row = db.execute("SELECT state,charged FROM spend WHERE request_id=?", (request_id,)).fetchone()
            if row is None or row[0] not in ("reserved", "overrun"):
                raise SpendingBlocked("Only unresolved charges can be reconciled")
            db.execute("INSERT INTO adjustments(request_id,previous_state,previous_charge,confirmed_charge,recorded_at) "
                       "VALUES(?,?,?,?,?)", (request_id, row[0], row[1], confirmed_cost, datetime.now(timezone.utc).isoformat()))
            db.execute("UPDATE spend SET state='settled',charged=? WHERE request_id=?", (confirmed_cost, request_id))
