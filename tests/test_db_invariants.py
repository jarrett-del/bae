"""Task 1 & Task 2 verification: Pydantic v2 models, WAL SQLite ledger, SHA-256 digests,
TTL expiration checks, and append-only item_events triggers.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from hermes_ebay_ops.db import (
    ActionGateError,
    append_item_event,
    canonical_json,
    compute_payload_sha256,
    get_db,
    get_or_create_seller_profile,
    stage_pending_action,
    upsert_item,
    verify_pending_action_for_execution,
)
from hermes_ebay_ops.models import (
    ActionType,
    ConditionEnum,
    EventActor,
    HRM_SKU_PATTERN,
    ItemStatus,
    SourceMode,
    StageListingInput,
    mint_sku,
)


def test_pydantic_models_strict_cents_conditions_and_sku() -> None:
    sku = mint_sku(datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc))
    assert HRM_SKU_PATTERN.match(sku)

    for bad_cents in (24.99, True, False, 0, -100):
        with pytest.raises(ValidationError):
            StageListingInput(seller_notes="Vintage jacket", price_cents=bad_cents)  # type: ignore[arg-type]

    with pytest.raises(ValidationError):
        StageListingInput(seller_notes="Vintage jacket", cogs_cents=-1)

    with pytest.raises(ValidationError):
        StageListingInput(seller_notes="Vintage jacket", condition="MINTY_FRESH_INVALID")  # type: ignore[arg-type]

    parsed = StageListingInput(
        seller_notes="Patagonia fleece M",
        price_cents=5500,
        condition="nwt",  # type: ignore[arg-type]
        cogs_cents=0,
        bin_code=" b-04-02 ",
    )
    assert parsed.condition == ConditionEnum.NEW
    assert parsed.bin_code == "B-04-02"


def test_wal_mode_pragmas_and_schema_tables() -> None:
    conn = get_db()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
        foreign_keys = conn.execute("PRAGMA foreign_keys;").fetchone()[0]

        assert str(journal_mode).lower() == "wal"
        assert int(busy_timeout) == 5000
        assert int(foreign_keys) == 1

        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        assert tables == {"seller_profile", "items", "pending_actions", "item_events"}

        profile = get_or_create_seller_profile(conn)
        assert profile["marketplace_id"] == "EBAY_US"
        assert profile["policies_opted_in"] == 0
        assert profile["pro_ops_enabled"] == 0
        assert profile["estimated_fee_bps"] == 1360
    finally:
        conn.close()


def test_sqlite_check_and_foreign_key_constraints() -> None:
    conn = get_db()
    try:
        with pytest.raises(sqlite3.IntegrityError):
            upsert_item(
                conn,
                sku="HRM-20261111-AAAA",
                source_mode=SourceMode.AGENT_CAPTURE,
                status=ItemStatus.STAGED,
                title="Invalid Zero Price",
                category_id="57988",
                condition_enum=ConditionEnum.USED_GOOD,
                list_price_cents=0,
            )

        with pytest.raises(sqlite3.IntegrityError):
            append_item_event(
                conn,
                sku="NON_EXISTENT_SKU",
                event_type="DRAFT_CREATED",
                actor=EventActor.AGENT_STAGED,
                detail={"note": "orphan fk"},
            )
    finally:
        conn.close()


def test_canonical_sha256_and_ttl_expiration() -> None:
    digest_a = compute_payload_sha256(canonical_json({"b": 2, "a": {"z": 9, "y": 8}}))
    digest_b = compute_payload_sha256(canonical_json({"a": {"y": 8, "z": 9}, "b": 2}))
    assert digest_a == digest_b

    conn = get_db()
    try:
        base_time = datetime(2026, 11, 11, 20, 0, 0, tzinfo=timezone.utc)
        upsert_item(
            conn,
            sku="HRM-20261111-BBBB",
            source_mode=SourceMode.AGENT_CAPTURE,
            status=ItemStatus.STAGED,
            title="Patagonia Synchilla Fleece Mens L",
            category_id="57988",
            condition_enum=ConditionEnum.USED_GOOD,
            list_price_cents=6500,
            now=base_time,
        )

        staged = stage_pending_action(
            conn,
            action_type=ActionType.PUBLISH_LISTING,
            sku="HRM-20261111-BBBB",
            payload={"sku": "HRM-20261111-BBBB", "list_price_cents": 6500},
            preview_card_md="### Preview",
            ttl_seconds=900,
            now=base_time,
        )

        # Valid within 15-minute window
        verified = verify_pending_action_for_execution(
            conn,
            staged.action_token,
            now=base_time + timedelta(minutes=14, seconds=59),
        )
        assert verified.payload_sha256 == staged.payload_sha256

        # Expired past 15-minute TTL -> transitions status to EXPIRED and raises ActionGateError
        with pytest.raises(ActionGateError) as exc_info:
            verify_pending_action_for_execution(
                conn,
                staged.action_token,
                now=base_time + timedelta(minutes=15, seconds=1),
            )
        assert exc_info.value.code == "TOKEN_EXPIRED"

        status_after = conn.execute(
            "SELECT status FROM pending_actions WHERE action_token = ?",
            (staged.action_token,),
        ).fetchone()[0]
        assert status_after == "EXPIRED"
    finally:
        conn.close()


def test_item_events_append_only_triggers_block_update_and_delete() -> None:
    conn = get_db()
    try:
        upsert_item(
            conn,
            sku="HRM-20261111-CCCC",
            source_mode=SourceMode.AGENT_CAPTURE,
            status=ItemStatus.STAGED,
            title="Levis 501 Selvedge Jeans 32x30",
            category_id="11483",
            condition_enum=ConditionEnum.USED_EXCELLENT,
            list_price_cents=7900,
        )
        event_id = append_item_event(
            conn,
            sku="HRM-20261111-CCCC",
            event_type="LISTING_STAGED",
            actor=EventActor.AGENT_STAGED,
            detail={"list_price_cents": 7900},
        )
        assert event_id >= 1

        with pytest.raises(sqlite3.IntegrityError, match="item_events ledger is append-only"):
            conn.execute(
                "UPDATE item_events SET event_type = 'TAMPERED' WHERE event_id = ?",
                (event_id,),
            )

        with pytest.raises(sqlite3.IntegrityError, match="item_events ledger is append-only"):
            conn.execute("DELETE FROM item_events WHERE event_id = ?", (event_id,))
    finally:
        conn.close()
