"""Durable SQLite ledger and two-step cryptographic approval queue.

Uses Hermes's profile-scoped `plugins.plugin_storage.plugin_db` exclusively so
seller ledgers survive plugin upgrades without storing credentials or writing
into the plugin source directory.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.plugin_storage import plugin_data_dir, plugin_db

from .config import PLUGIN_NAME, get_settings
from .models import (
    AccountCapabilities,
    ActionStatus,
    ActionType,
    ConditionEnum,
    EventActor,
    ItemStatus,
    SourceMode,
    utc_now_iso,
)

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

-- Caches seller account capabilities and Lite vs. Pro progressive state
CREATE TABLE IF NOT EXISTS seller_profile (
    profile_key             TEXT PRIMARY KEY DEFAULT 'default',
    marketplace_id          TEXT NOT NULL DEFAULT 'EBAY_US',
    policies_opted_in       INTEGER NOT NULL DEFAULT 0,
    fulfillment_policy_id   TEXT,
    payment_policy_id       TEXT,
    return_policy_id        TEXT,
    merchant_location_key   TEXT,
    pro_ops_enabled         INTEGER NOT NULL DEFAULT 0,
    estimated_fee_bps       INTEGER NOT NULL DEFAULT 1360,
    updated_at              TEXT NOT NULL
);

-- Unified Lite + Pro inventory record (Pro columns nullable by design)
CREATE TABLE IF NOT EXISTS items (
    sku                     TEXT PRIMARY KEY,
    ebay_item_id            TEXT UNIQUE,
    offer_id                TEXT UNIQUE,
    source_mode             TEXT NOT NULL CHECK (source_mode IN ('AGENT_CAPTURE', 'MAGICAL_AUDIT')),
    status                  TEXT NOT NULL CHECK (status IN ('STAGED', 'ACTIVE', 'SOLD', 'ENDED')),
    title                   TEXT NOT NULL,
    category_id             TEXT NOT NULL,
    condition_enum          TEXT NOT NULL,
    list_price_cents        INTEGER NOT NULL CHECK (list_price_cents > 0),
    shipping_est_cents      INTEGER NOT NULL DEFAULT 0,
    package_weight_oz       REAL,
    package_dims_json       TEXT,
    aspects_json            TEXT NOT NULL DEFAULT '{}',
    flaws_disclosure        TEXT,
    media_urls_json         TEXT NOT NULL DEFAULT '[]',
    -- Progressive Pro Seller columns (NULL for casual C2C)
    cogs_cents              INTEGER CHECK (cogs_cents IS NULL OR cogs_cents >= 0),
    bin_code                TEXT,
    floor_price_cents       INTEGER CHECK (floor_price_cents IS NULL OR floor_price_cents > 0),
    listed_at               TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

-- Cryptographically bound two-step human approval queue
CREATE TABLE IF NOT EXISTS pending_actions (
    action_token            TEXT PRIMARY KEY,
    action_type             TEXT NOT NULL CHECK (
                                action_type IN ('PUBLISH_LISTING', 'REVISE_LISTING', 'BOOTSTRAP_POLICIES', 'RESPOND_OFFER', 'BULK_PUBLISH')
                            ),
    sku                     TEXT REFERENCES items(sku),
    payload_json            TEXT NOT NULL,
    payload_sha256          TEXT NOT NULL,
    preview_card_md         TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'PENDING' CHECK (
                                status IN ('PENDING', 'EXECUTED', 'EXPIRED', 'CANCELLED', 'FAILED')
                            ),
    expires_at              TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    executed_at             TEXT
);

-- Append-only audit log for every draft, QA fix, price change, and offer decision
CREATE TABLE IF NOT EXISTS item_events (
    event_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sku                     TEXT NOT NULL REFERENCES items(sku),
    event_type              TEXT NOT NULL,
    actor                   TEXT NOT NULL CHECK (actor IN ('USER', 'AGENT_STAGED', 'EBAY_SYNC', 'CRON')),
    detail_json             TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS prevent_item_events_update
BEFORE UPDATE ON item_events
BEGIN
    SELECT RAISE(ABORT, 'item_events ledger is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_item_events_delete
BEFORE DELETE ON item_events
BEGIN
    SELECT RAISE(ABORT, 'item_events ledger is append-only');
END;
"""


class ActionGateError(RuntimeError):
    """Raised when an action_token fails closed at the two-step write gate."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PendingActionRecord:
    action_token: str
    action_type: ActionType
    sku: str | None
    payload: dict[str, Any]
    payload_json: str
    payload_sha256: str
    preview_card_md: str
    status: ActionStatus
    expires_at: str
    created_at: str
    executed_at: str | None = None


def canonical_json(payload: Mapping[str, Any]) -> str:
    """Serialize a JSON mapping deterministically with sorted keys and compact separators."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_payload_sha256(payload_json: str) -> str:
    """Return hex-encoded SHA-256 digest of a serialized JSON payload string."""
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def parse_iso_utc(timestamp: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware UTC datetime."""
    normalized = timestamp.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def init_schema(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Configure WAL, busy_timeout, foreign keys, and create forward-compatible tables/triggers."""
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 5000;")
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.executescript(SCHEMA_SQL)
    item_cols = {str(r["name"]) for r in conn.execute("PRAGMA table_info(items)").fetchall()}
    if "package_dims_json" not in item_cols:
        conn.execute("ALTER TABLE items ADD COLUMN package_dims_json TEXT;")
    pa_ddl_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'pending_actions'"
    ).fetchone()
    if pa_ddl_row and "BULK_PUBLISH" not in str(pa_ddl_row["sql"]):
        conn.execute("PRAGMA foreign_keys = OFF;")
        conn.executescript(
            """
            CREATE TABLE pending_actions_v2 (
                action_token    TEXT PRIMARY KEY,
                action_type     TEXT NOT NULL CHECK (
                                    action_type IN ('PUBLISH_LISTING', 'REVISE_LISTING', 'BOOTSTRAP_POLICIES', 'RESPOND_OFFER', 'BULK_PUBLISH')
                                ),
                sku             TEXT REFERENCES items(sku),
                payload_json    TEXT NOT NULL,
                payload_sha256  TEXT NOT NULL,
                preview_card_md TEXT NOT NULL,
                status          TEXT NOT NULL DEFAULT 'PENDING' CHECK (
                                    status IN ('PENDING', 'EXECUTED', 'EXPIRED', 'CANCELLED', 'FAILED')
                                ),
                expires_at      TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                executed_at     TEXT
            );
            INSERT INTO pending_actions_v2 SELECT * FROM pending_actions;
            DROP TABLE pending_actions;
            ALTER TABLE pending_actions_v2 RENAME TO pending_actions;
            """
        )
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.commit()
    return conn


def get_active_tour_info(plugin_name: str | None = None) -> dict[str, Any] | None:
    """Return active sample-data tour metadata if the desk is currently touring, else None."""
    resolved_name = plugin_name or get_settings().plugin_name or PLUGIN_NAME
    try:
        tour_file = plugin_data_dir(resolved_name) / "tour_state.json"
        if not tour_file.is_file():
            return None
        data = json.loads(tour_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("id"):
            return data
    except Exception:
        return None
    return None


def set_active_tour_info(
    info: dict[str, Any] | None,
    plugin_name: str | None = None,
) -> None:
    """Activate or clear sample-data tour state without touching the real data.db ledger."""
    resolved_name = plugin_name or get_settings().plugin_name or PLUGIN_NAME
    tour_file = plugin_data_dir(resolved_name) / "tour_state.json"
    if info is None:
        tour_file.unlink(missing_ok=True)
        return
    tour_file.write_text(json.dumps(info, indent=2, ensure_ascii=False), encoding="utf-8")


def get_packed_order_ids(plugin_name: str | None = None) -> set[str]:
    """Return order IDs marked packed on the warehouse pick-list."""
    resolved_name = plugin_name or get_settings().plugin_name or PLUGIN_NAME
    tour = get_active_tour_info(resolved_name)
    fname = f"packed_{tour['id']}.json" if tour else "packed_orders.json"
    try:
        packed_file = plugin_data_dir(resolved_name) / fname
        if not packed_file.is_file():
            return set()
        raw = json.loads(packed_file.read_text(encoding="utf-8"))
        return {str(x) for x in raw} if isinstance(raw, list) else set()
    except Exception:
        return set()


def toggle_packed_order_id(order_id: str, plugin_name: str | None = None) -> bool:
    """Toggle packed status for an order line on the pick-list; returns new packed boolean."""
    resolved_name = plugin_name or get_settings().plugin_name or PLUGIN_NAME
    tour = get_active_tour_info(resolved_name)
    fname = f"packed_{tour['id']}.json" if tour else "packed_orders.json"
    packed = get_packed_order_ids(resolved_name)
    oid = str(order_id).strip()
    if oid in packed:
        packed.remove(oid)
        now_packed = False
    else:
        packed.add(oid)
        now_packed = True
    packed_file = plugin_data_dir(resolved_name) / fname
    packed_file.write_text(json.dumps(sorted(packed), indent=2), encoding="utf-8")
    return now_packed


def get_db(
    plugin_name: str | None = None,
    *,
    filename: str | None = None,
) -> sqlite3.Connection:
    """Open and initialize the profile-scoped Hermes plugin SQLite database."""
    resolved_name = plugin_name or get_settings().plugin_name or PLUGIN_NAME
    target_filename = filename
    if target_filename is None:
        target_filename = "tour.db" if get_active_tour_info(resolved_name) else "data.db"
    try:
        conn = plugin_db(resolved_name, filename=target_filename)
    except TypeError:
        conn = plugin_db(resolved_name)
    return init_schema(conn)


def get_or_create_seller_profile(
    conn: sqlite3.Connection,
    *,
    profile_key: str = "default",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Fetch or initialize the seller_profile singleton row."""
    settings = get_settings()
    row = conn.execute(
        "SELECT * FROM seller_profile WHERE profile_key = ?",
        (profile_key,),
    ).fetchone()
    if row is not None:
        return dict(row)

    now_iso = utc_now_iso(now)
    conn.execute(
        """
        INSERT INTO seller_profile (
            profile_key, marketplace_id, policies_opted_in, pro_ops_enabled, estimated_fee_bps, updated_at
        ) VALUES (?, ?, 0, 0, ?, ?)
        """,
        (profile_key, settings.marketplace_id, settings.default_estimated_fee_bps, now_iso),
    )
    conn.commit()
    created = conn.execute(
        "SELECT * FROM seller_profile WHERE profile_key = ?",
        (profile_key,),
    ).fetchone()
    return dict(created)


def update_seller_capabilities(
    conn: sqlite3.Connection,
    capabilities: AccountCapabilities,
    *,
    profile_key: str = "default",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist Account API capability discovery into seller_profile."""
    get_or_create_seller_profile(conn, profile_key=profile_key, now=now)
    now_iso = utc_now_iso(now)
    conn.execute(
        """
        UPDATE seller_profile
        SET marketplace_id = ?,
            policies_opted_in = ?,
            fulfillment_policy_id = ?,
            payment_policy_id = ?,
            return_policy_id = ?,
            merchant_location_key = ?,
            updated_at = ?
        WHERE profile_key = ?
        """,
        (
            capabilities.marketplace_id,
            1 if capabilities.policies_opted_in else 0,
            capabilities.fulfillment_policy_id,
            capabilities.payment_policy_id,
            capabilities.return_policy_id,
            capabilities.merchant_location_key,
            now_iso,
            profile_key,
        ),
    )
    conn.commit()
    return get_or_create_seller_profile(conn, profile_key=profile_key, now=now)


def maybe_enable_pro_ops(
    conn: sqlite3.Connection,
    *,
    cogs_cents: int | None = None,
    bin_code: str | None = None,
    floor_price_cents: int | None = None,
    profile_key: str = "default",
    now: datetime | None = None,
) -> bool:
    """Promote seller_profile.pro_ops_enabled = 1 when operational reseller fields appear."""
    profile = get_or_create_seller_profile(conn, profile_key=profile_key, now=now)
    should_promote = (
        cogs_cents is not None
        or (bin_code is not None and bin_code.strip() != "")
        or floor_price_cents is not None
    )
    if should_promote and not bool(profile["pro_ops_enabled"]):
        conn.execute(
            "UPDATE seller_profile SET pro_ops_enabled = 1, updated_at = ? WHERE profile_key = ?",
            (utc_now_iso(now), profile_key),
        )
        conn.commit()
        return True
    return bool(profile["pro_ops_enabled"])


def is_pro_ops_flag_set(conn: sqlite3.Connection, *, profile_key: str = "default") -> bool:
    """Return True if seller_profile has unlocked progressive Pro operations."""
    profile = get_or_create_seller_profile(conn, profile_key=profile_key)
    return bool(profile["pro_ops_enabled"])


def upsert_item(
    conn: sqlite3.Connection,
    *,
    sku: str,
    source_mode: SourceMode | str,
    status: ItemStatus | str,
    title: str,
    category_id: str,
    condition_enum: ConditionEnum | str,
    list_price_cents: int,
    shipping_est_cents: int = 0,
    ebay_item_id: str | None = None,
    offer_id: str | None = None,
    package_weight_oz: float | None = None,
    package_dims: Mapping[str, Any] | None = None,
    aspects: Mapping[str, Any] | None = None,
    flaws_disclosure: str | None = None,
    media_urls: list[str] | None = None,
    cogs_cents: int | None = None,
    bin_code: str | None = None,
    floor_price_cents: int | None = None,
    listed_at: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Insert or update an inventory item row while preserving non-null Pro metadata."""
    now_iso = utc_now_iso(now)
    src_val = source_mode.value if isinstance(source_mode, SourceMode) else source_mode
    status_val = status.value if isinstance(status, ItemStatus) else status
    cond_val = condition_enum.value if isinstance(condition_enum, ConditionEnum) else condition_enum

    dims_json = canonical_json(package_dims) if package_dims is not None else None
    aspects_json = canonical_json(aspects or {})
    media_json = json.dumps(media_urls or [], ensure_ascii=False)

    existing = conn.execute("SELECT * FROM items WHERE sku = ?", (sku,)).fetchone()
    if existing is None:
        conn.execute(
            """
            INSERT INTO items (
                sku, ebay_item_id, offer_id, source_mode, status, title, category_id,
                condition_enum, list_price_cents, shipping_est_cents, package_weight_oz,
                package_dims_json, aspects_json, flaws_disclosure, media_urls_json,
                cogs_cents, bin_code, floor_price_cents, listed_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sku,
                ebay_item_id,
                offer_id,
                src_val,
                status_val,
                title,
                category_id,
                cond_val,
                list_price_cents,
                shipping_est_cents,
                package_weight_oz,
                dims_json,
                aspects_json,
                flaws_disclosure,
                media_json,
                cogs_cents,
                bin_code,
                floor_price_cents,
                listed_at,
                now_iso,
                now_iso,
            ),
        )
    else:
        conn.execute(
            """
            UPDATE items
            SET ebay_item_id = COALESCE(?, ebay_item_id),
                offer_id = COALESCE(?, offer_id),
                status = ?,
                title = ?,
                category_id = ?,
                condition_enum = ?,
                list_price_cents = ?,
                shipping_est_cents = ?,
                package_weight_oz = COALESCE(?, package_weight_oz),
                package_dims_json = COALESCE(?, package_dims_json),
                aspects_json = ?,
                flaws_disclosure = COALESCE(?, flaws_disclosure),
                media_urls_json = ?,
                cogs_cents = COALESCE(?, cogs_cents),
                bin_code = COALESCE(?, bin_code),
                floor_price_cents = COALESCE(?, floor_price_cents),
                listed_at = COALESCE(?, listed_at),
                updated_at = ?
            WHERE sku = ?
            """,
            (
                ebay_item_id,
                offer_id,
                status_val,
                title,
                category_id,
                cond_val,
                list_price_cents,
                shipping_est_cents,
                package_weight_oz,
                dims_json,
                aspects_json,
                flaws_disclosure,
                media_json,
                cogs_cents,
                bin_code,
                floor_price_cents,
                listed_at,
                now_iso,
                sku,
            ),
        )
    conn.commit()
    row = conn.execute("SELECT * FROM items WHERE sku = ?", (sku,)).fetchone()
    return dict(row)


def get_item_by_sku(conn: sqlite3.Connection, sku: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM items WHERE sku = ?", (sku,)).fetchone()
    return dict(row) if row is not None else None


def get_item_by_ebay_item_id(conn: sqlite3.Connection, ebay_item_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM items WHERE ebay_item_id = ?", (ebay_item_id,)).fetchone()
    return dict(row) if row is not None else None


def find_realized_sold_comps(
    conn: sqlite3.Connection,
    query: str,
    category_id: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return realized SOLD items from the seller's own local ledger matching query keywords or category."""
    tokens = [tok.lower() for tok in query.split() if len(tok) >= 3][:6]
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT sku, ebay_item_id, title, category_id, condition_enum, list_price_cents, shipping_est_cents, updated_at "
            "FROM items WHERE status = 'SOLD' ORDER BY updated_at DESC LIMIT 100"
        ).fetchall()
    ]
    matched: list[dict[str, Any]] = []
    for row in rows:
        if category_id and str(row["category_id"]) == str(category_id):
            matched.append(row)
            continue
        hay = str(row["title"]).lower()
        if tokens and any(tok in hay for tok in tokens):
            matched.append(row)
    return matched[:limit]


def append_item_event(
    conn: sqlite3.Connection,
    *,
    sku: str,
    event_type: str,
    actor: EventActor | str,
    detail: Mapping[str, Any],
    now: datetime | None = None,
) -> int:
    """Append an immutable audit event to item_events."""
    actor_val = actor.value if isinstance(actor, EventActor) else actor
    cur = conn.execute(
        """
        INSERT INTO item_events (sku, event_type, actor, detail_json, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (sku, event_type, actor_val, canonical_json(detail), utc_now_iso(now)),
    )
    conn.commit()
    return int(cur.lastrowid)


def mint_action_token() -> str:
    """Generate a high-entropy single-use approval token."""
    return f"act_{secrets.token_urlsafe(16)}"


def stage_pending_action(
    conn: sqlite3.Connection,
    *,
    action_type: ActionType | str,
    sku: str | None,
    payload: Mapping[str, Any],
    preview_card_md: str,
    ttl_seconds: int | None = None,
    now: datetime | None = None,
    action_token: str | None = None,
) -> PendingActionRecord:
    """Stage a SHA-256 bound pending write action with a strict expiration timestamp."""
    effective_ttl = ttl_seconds if ttl_seconds is not None else get_settings().action_token_ttl_seconds
    base_dt = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    created_iso = utc_now_iso(base_dt)
    expires_iso = utc_now_iso(base_dt + timedelta(seconds=effective_ttl))

    action_type_enum = ActionType(action_type)
    payload_json = canonical_json(payload)
    payload_sha = compute_payload_sha256(payload_json)
    token = action_token or mint_action_token()

    conn.execute(
        """
        INSERT INTO pending_actions (
            action_token, action_type, sku, payload_json, payload_sha256,
            preview_card_md, status, expires_at, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)
        """,
        (
            token,
            action_type_enum.value,
            sku,
            payload_json,
            payload_sha,
            preview_card_md,
            expires_iso,
            created_iso,
        ),
    )
    conn.commit()
    return PendingActionRecord(
        action_token=token,
        action_type=action_type_enum,
        sku=sku,
        payload=json.loads(payload_json),
        payload_json=payload_json,
        payload_sha256=payload_sha,
        preview_card_md=preview_card_md,
        status=ActionStatus.PENDING,
        expires_at=expires_iso,
        created_at=created_iso,
        executed_at=None,
    )


def verify_pending_action_for_execution(
    conn: sqlite3.Connection,
    action_token: str,
    *,
    now: datetime | None = None,
) -> PendingActionRecord:
    """Atomically load and validate a PENDING action_token against status, TTL, and SHA-256."""
    now_dt = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)

    conn.execute("BEGIN IMMEDIATE;")
    try:
        row = conn.execute(
            "SELECT * FROM pending_actions WHERE action_token = ?",
            (action_token,),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise ActionGateError("TOKEN_NOT_FOUND", f"Unknown action_token: {action_token}")

        status_str = str(row["status"])
        if status_str != ActionStatus.PENDING.value:
            conn.rollback()
            raise ActionGateError(
                "TOKEN_NOT_PENDING",
                f"Action {action_token} cannot be used because its status is {status_str}.",
            )

        expires_dt = parse_iso_utc(str(row["expires_at"]))
        if now_dt > expires_dt:
            conn.execute(
                "UPDATE pending_actions SET status = 'EXPIRED' WHERE action_token = ?",
                (action_token,),
            )
            conn.commit()
            raise ActionGateError(
                "TOKEN_EXPIRED",
                f"Action {action_token} expired at {row['expires_at']} (current UTC: {utc_now_iso(now_dt)}).",
            )

        raw_payload_json = str(row["payload_json"])
        try:
            parsed_payload = json.loads(raw_payload_json)
            recomputed_sha = compute_payload_sha256(canonical_json(parsed_payload))
        except (ValueError, TypeError) as exc:
            conn.execute(
                "UPDATE pending_actions SET status = 'FAILED' WHERE action_token = ?",
                (action_token,),
            )
            conn.commit()
            raise ActionGateError("PAYLOAD_CORRUPTED", f"Malformed payload_json for {action_token}: {exc}") from exc

        raw_sha = compute_payload_sha256(raw_payload_json)
        expected_sha = str(row["payload_sha256"])
        if not (
            hmac.compare_digest(raw_sha, expected_sha)
            and hmac.compare_digest(recomputed_sha, expected_sha)
        ):
            conn.execute(
                "UPDATE pending_actions SET status = 'FAILED' WHERE action_token = ?",
                (action_token,),
            )
            conn.commit()
            raise ActionGateError(
                "PAYLOAD_TAMPERED",
                f"SHA-256 payload verification failed for {action_token}; refusing execution.",
            )

        conn.commit()
        return PendingActionRecord(
            action_token=str(row["action_token"]),
            action_type=ActionType(str(row["action_type"])),
            sku=row["sku"],
            payload=parsed_payload,
            payload_json=raw_payload_json,
            payload_sha256=expected_sha,
            preview_card_md=str(row["preview_card_md"]),
            status=ActionStatus(status_str),
            expires_at=str(row["expires_at"]),
            created_at=str(row["created_at"]),
            executed_at=row["executed_at"],
        )
    except ActionGateError:
        raise
    except Exception:
        if conn.in_transaction:
            conn.rollback()
        raise


def mark_pending_action_status(
    conn: sqlite3.Connection,
    action_token: str,
    status: ActionStatus,
    *,
    now: datetime | None = None,
) -> None:
    """Transition a pending action row to EXECUTED, CANCELLED, EXPIRED, or FAILED."""
    executed_iso = utc_now_iso(now) if status == ActionStatus.EXECUTED else None
    conn.execute(
        """
        UPDATE pending_actions
        SET status = ?, executed_at = COALESCE(?, executed_at)
        WHERE action_token = ?
        """,
        (status.value, executed_iso, action_token),
    )
    conn.commit()
