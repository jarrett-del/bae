"""Profile-scoped SQLite cache for Taxonomy/Metadata quota protection (5,000 calls/day)
and Haul / Closet Death-Pile break-even tracking.

Stored in `cache.db` (or `tour_cache.db` during sample tours) via `plugins.plugin_storage.plugin_db`
so `data.db` retains its exact four constitutional ledger tables.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.plugin_storage import plugin_db

from .config import PLUGIN_NAME, get_settings
from .db import canonical_json, get_active_tour_info, parse_iso_utc
from .models import CategoryAspectSpec, utc_now_iso

CACHE_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS aspect_cache (
    cache_key       TEXT PRIMARY KEY,
    marketplace_id  TEXT NOT NULL,
    category_id     TEXT NOT NULL,
    specs_json      TEXT NOT NULL,
    cached_at       TEXT NOT NULL,
    expires_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS haul_batches (
    haul_id                 TEXT PRIMARY KEY,
    label                   TEXT NOT NULL,
    total_cost_cents        INTEGER NOT NULL CHECK (total_cost_cents >= 0),
    item_count              INTEGER NOT NULL CHECK (item_count >= 1),
    unlisted_pile_count     INTEGER NOT NULL DEFAULT 0 CHECK (unlisted_pile_count >= 0),
    daily_sprint_target     INTEGER NOT NULL DEFAULT 3 CHECK (daily_sprint_target >= 1),
    notes                   TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);
"""


def get_cache_db(plugin_name: str | None = None, *, filename: str | None = None) -> sqlite3.Connection:
    """Open the profile-scoped auxiliary SQLite cache database."""
    resolved = plugin_name or get_settings().plugin_name or PLUGIN_NAME
    target = filename or ("tour_cache.db" if get_active_tour_info(resolved) else "cache.db")
    try:
        conn = plugin_db(resolved, filename=target)
    except TypeError:
        conn = plugin_db(resolved)
    conn.row_factory = sqlite3.Row
    conn.executescript(CACHE_SCHEMA_SQL)
    conn.commit()
    return conn


def get_cached_category_aspects(
    category_id: str,
    marketplace_id: str = "EBAY_US",
    *,
    plugin_name: str | None = None,
    now: datetime | None = None,
) -> list[CategoryAspectSpec] | None:
    """Return cached CategoryAspectSpec list if fresh, protecting the 5,000/day Taxonomy quota."""
    now_dt = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    cache_key = f"{marketplace_id}:{category_id}"
    conn = get_cache_db(plugin_name)
    try:
        row = conn.execute(
            "SELECT specs_json, expires_at FROM aspect_cache WHERE cache_key = ?",
            (cache_key,),
        ).fetchone()
        if row is None:
            return None
        if now_dt > parse_iso_utc(str(row["expires_at"])):
            conn.execute("DELETE FROM aspect_cache WHERE cache_key = ?", (cache_key,))
            conn.commit()
            return None
        raw_list = json.loads(str(row["specs_json"]))
        return [CategoryAspectSpec.model_validate(entry) for entry in raw_list]
    except Exception:
        return None
    finally:
        conn.close()


def put_cached_category_aspects(
    category_id: str,
    marketplace_id: str,
    specs: list[CategoryAspectSpec],
    *,
    ttl_seconds: int = 7 * 86400,
    plugin_name: str | None = None,
    now: datetime | None = None,
) -> None:
    """Persist Taxonomy/Metadata leaf category aspect rules in SQLite cache."""
    base_dt = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    now_iso = utc_now_iso(base_dt)
    exp_iso = utc_now_iso(base_dt + timedelta(seconds=ttl_seconds))
    cache_key = f"{marketplace_id}:{category_id}"
    payload = json.dumps([s.model_dump(mode="json") for s in specs], ensure_ascii=False)

    conn = get_cache_db(plugin_name)
    try:
        conn.execute(
            """
            INSERT INTO aspect_cache (cache_key, marketplace_id, category_id, specs_json, cached_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                specs_json = excluded.specs_json,
                cached_at = excluded.cached_at,
                expires_at = excluded.expires_at
            """,
            (cache_key, marketplace_id, category_id, payload, now_iso, exp_iso),
        )
        conn.commit()
    finally:
        conn.close()


def get_aspect_cache_stats(plugin_name: str | None = None) -> dict[str, Any]:
    """Return cached category count for quota monitoring in Setup & Logs."""
    conn = get_cache_db(plugin_name)
    try:
        row = conn.execute("SELECT COUNT(*) AS n FROM aspect_cache").fetchone()
        count = int(row["n"]) if row else 0
        return {
            "cached_categories": count,
            "daily_quota_limit": 5000,
            "ttl_days": 7,
        }
    finally:
        conn.close()


def upsert_haul_batch(
    *,
    label: str,
    total_cost_cents: int,
    item_count: int,
    unlisted_pile_count: int,
    daily_sprint_target: int = 3,
    notes: str | None = None,
    haul_id: str | None = None,
    plugin_name: str | None = None,
    filename: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create or update a sourced Haul or Closet Clearout batch for break-even velocity tracking."""
    now_iso = utc_now_iso(now)
    hid = haul_id or f"haul_{secrets.token_hex(4)}"
    conn = get_cache_db(plugin_name, filename=filename)
    try:
        conn.execute(
            """
            INSERT INTO haul_batches (
                haul_id, label, total_cost_cents, item_count, unlisted_pile_count,
                daily_sprint_target, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(haul_id) DO UPDATE SET
                label = excluded.label,
                total_cost_cents = excluded.total_cost_cents,
                item_count = excluded.item_count,
                unlisted_pile_count = excluded.unlisted_pile_count,
                daily_sprint_target = excluded.daily_sprint_target,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                hid,
                label.strip(),
                max(0, int(total_cost_cents)),
                max(1, int(item_count)),
                max(0, int(unlisted_pile_count)),
                max(1, int(daily_sprint_target)),
                notes,
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM haul_batches WHERE haul_id = ?", (hid,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def list_haul_summaries(
    items: list[dict[str, Any]],
    *,
    plugin_name: str | None = None,
) -> list[dict[str, Any]]:
    """Compute live Haul / Closet Death-Pile break-even progress and 3-item micro-sprint projections."""
    conn = get_cache_db(plugin_name)
    try:
        rows = [
            dict(r)
            for r in conn.execute("SELECT * FROM haul_batches ORDER BY updated_at DESC").fetchall()
        ]
    finally:
        conn.close()

    realized_net_cents = sum(
        int(it.get("estimated_net_payout_cents") or 0)
        for it in items
        if str(it.get("status")) == "SOLD"
    )
    active_net_pool_cents = sum(
        int(it.get("estimated_net_payout_cents") or 0)
        for it in items
        if str(it.get("status")) in {"ACTIVE", "STAGED"}
    )
    avg_net_per_item = (
        int(round((realized_net_cents + active_net_pool_cents) / max(1, len(items))))
        if items
        else 3200
    )

    out: list[dict[str, Any]] = []
    for row in rows:
        cost = int(row["total_cost_cents"])
        total_cnt = int(row["item_count"])
        unlisted = min(total_cnt, int(row["unlisted_pile_count"]))
        listed_cnt = max(0, total_cnt - unlisted)
        remaining_breakeven_cents = max(0, cost - realized_net_cents)
        sprint_n = min(unlisted, int(row["daily_sprint_target"])) if unlisted > 0 else int(row["daily_sprint_target"])
        sprint_unlock_cents = sprint_n * max(1200, avg_net_per_item)
        pct_listed = round((listed_cnt / max(1, total_cnt)) * 100)
        out.append(
            {
                **row,
                "listed_count": listed_cnt,
                "pct_listed": pct_listed,
                "realized_net_cents": realized_net_cents,
                "remaining_breakeven_cents": remaining_breakeven_cents,
                "breakeven_reached": remaining_breakeven_cents == 0,
                "sprint_items": sprint_n,
                "sprint_minutes": sprint_n * 3,
                "sprint_ebay_balance_unlock_cents": sprint_unlock_cents,
                "canonical_digest": canonical_json({"haul_id": row["haul_id"], "unlisted": unlisted}),
            }
        )
    return out
