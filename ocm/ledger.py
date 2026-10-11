"""Append-only OpenCompute Credits (OCC) ledger. All amounts are integer micro-OCC.

Mutating helpers never commit; callers wrap them in `with conn:` so a ledger write and the
job-state change that caused it commit atomically."""

import sqlite3

from .db import dumps, insert, now, row, rows

MICRO = 1_000_000


class InsufficientCredits(Exception):
    pass


def ceil_div(a: int, b: int) -> int:
    return -(-a // b)


def cost_micro(tariff: dict, input_tokens: int, output_tokens: int) -> int:
    """Measured cost, rounded up to the next whole micro-OCC, never below the tariff minimum."""
    raw = input_tokens * tariff["input_micro_per_1k"] + output_tokens * tariff["output_micro_per_1k"]
    return max(ceil_div(raw, 1000), tariff["min_charge_micro"])


def estimate_input_tokens(messages: list[dict]) -> int:
    # Conservative: ~2 chars/token plus per-message template overhead. Real tokenizers average ~4.
    return sum(ceil_div(len(m["content"]), 2) + 16 for m in messages) + 16


def format_occ(micro: int) -> str:
    sign = "-" if micro < 0 else ""
    whole, frac = divmod(abs(micro), MICRO)
    return f"{sign}{whole}.{frac:06d}".rstrip("0").rstrip(".") if frac else f"{sign}{whole}"


def balance(conn: sqlite3.Connection, member_id: str) -> int:
    r = conn.execute("SELECT COALESCE(SUM(amount_micro_occ), 0) FROM ledger WHERE member_id = ?", (member_id,))
    return r.fetchone()[0]


def _entry(conn, *, network_id, member_id, entry_type, amount, key, job_id=None, tariff_version=None,
           reversal_of=None, metadata=None) -> bool:
    """Insert one entry. Returns False (no-op) if the idempotency key already exists."""
    try:
        insert(conn, "ledger", {
            "network_id": network_id, "member_id": member_id, "job_id": job_id, "entry_type": entry_type,
            "amount_micro_occ": amount, "idempotency_key": key, "tariff_version": tariff_version,
            "created_at": now(), "reversal_of": reversal_of, "metadata_json": dumps(metadata or {}),
        })
        return True
    except sqlite3.IntegrityError:
        return False


def grant(conn, network_id: str, member_id: str, amount: int, reason: str) -> bool:
    return _entry(conn, network_id=network_id, member_id=member_id, entry_type="demo_grant",
                  amount=amount, key=f"grant:{reason}:{member_id}", metadata={"reason": reason})


def reserve(conn, job: dict, amount: int) -> None:
    """Hold `amount` against the requester."""
    if balance(conn, job["requester_member_id"]) < amount:
        raise InsufficientCredits(amount)
    _entry(conn, network_id=job["network_id"], member_id=job["requester_member_id"], job_id=job["id"],
           entry_type="reservation", amount=-amount, key=f"{job['id']}:reservation",
           tariff_version=job["tariff_version"])


def release(conn, job: dict) -> int:
    res = row(conn, "SELECT id, amount_micro_occ FROM ledger WHERE idempotency_key = ?", f"{job['id']}:reservation")
    if not res:
        return 0
    _entry(conn, network_id=job["network_id"], member_id=job["requester_member_id"], job_id=job["id"],
           entry_type="reservation_release", amount=-res["amount_micro_occ"], key=f"{job['id']}:release",
           reversal_of=res["id"])
    return -res["amount_micro_occ"]


def settle(conn, job: dict, provider_member_id: str, amount: int) -> bool:
    """Atomically release the hold, debit the requester and reward the provider. Idempotent."""
    release(conn, job)
    common = {"network_id": job["network_id"], "job_id": job["id"], "tariff_version": job["tariff_version"]}
    debited = _entry(conn, member_id=job["requester_member_id"], entry_type="job_debit", amount=-amount,
                     key=f"{job['id']}:debit", **common)
    rewarded = _entry(conn, member_id=provider_member_id, entry_type="provider_reward", amount=amount,
                      key=f"{job['id']}:reward", **common)
    if debited != rewarded:
        raise RuntimeError("half-settled job; caller's transaction must roll back")
    return debited


def reverse(conn, entry_id: int, reason: str) -> bool:
    """Compensating entry; history is never edited."""
    e = row(conn, "SELECT * FROM ledger WHERE id = ?", entry_id)
    return _entry(conn, network_id=e["network_id"], member_id=e["member_id"], job_id=e["job_id"],
                  entry_type="reversal", amount=-e["amount_micro_occ"], key=f"reversal:{entry_id}",
                  reversal_of=entry_id, metadata={"reason": reason})


def consistency_report(conn) -> dict:
    """Checks every job's transfer sides cancel and every hold is released once the job is terminal."""
    unbalanced = rows(conn, """
        SELECT job_id, SUM(amount_micro_occ) AS net FROM ledger
        WHERE entry_type IN ('job_debit', 'provider_reward') GROUP BY job_id HAVING net != 0""")
    open_holds = rows(conn, """
        SELECT l.job_id FROM ledger l JOIN jobs j ON j.id = l.job_id
        WHERE l.entry_type IN ('reservation', 'reservation_release')
          AND j.status IN ('COMPLETED', 'FAILED', 'CANCELLED', 'EXPIRED')
        GROUP BY l.job_id HAVING SUM(l.amount_micro_occ) != 0""")
    grants = conn.execute("SELECT COALESCE(SUM(amount_micro_occ),0) FROM ledger WHERE entry_type IN ('demo_grant','reversal')").fetchone()[0]
    total = conn.execute("SELECT COALESCE(SUM(amount_micro_occ),0) FROM ledger").fetchone()[0]
    holds = conn.execute("SELECT COALESCE(SUM(amount_micro_occ),0) FROM ledger WHERE entry_type IN ('reservation','reservation_release')").fetchone()[0]
    return {
        "unbalanced_jobs": unbalanced,
        "unreleased_terminal_holds": open_holds,
        # Transfers net to zero, so total supply = grants + reversals + currently held reservations.
        "supply_matches_grants": total == grants + holds,
        "ok": not unbalanced and not open_holds and total == grants + holds,
    }
