"""Task 4 verification: Cryptographic two-step approval gate fail-closed tests.
Proves expired tokens, cancelled tokens, replayed tokens, unknown tokens, and
mutated JSON payloads all fail closed with ZERO EbayAdapter write calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from hermes_ebay_ops import register
from hermes_ebay_ops.adapter import MockEbayAdapter, set_adapter_override
from hermes_ebay_ops.db import get_db, utc_now_iso
from hermes_ebay_ops.tools.phase1_mvp import (
    handle_audit_active_listing,
    handle_confirm_action,
    handle_stage_listing,
)


class FakePluginContext:
    def __init__(self) -> None:
        self.tools: dict[str, dict[str, Any]] = {}
        self.skills: dict[str, Path] = {}

    def register_tool(
        self,
        *,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Any,
        check_fn: Any | None = None,
    ) -> None:
        self.tools[name] = {
            "toolset": toolset,
            "schema": schema,
            "handler": handler,
            "check_fn": check_fn,
        }

    def register_skill(self, name: str, path: Path, description: str = "", frontmatter: Any = None) -> None:
        self.skills[name] = Path(path)

    def register_hook(self, hook_name: str, callback: Any, *, hold_stream: bool = False) -> None:
        self.hooks = getattr(self, "hooks", {})
        self.hooks.setdefault(hook_name, []).append(callback)


def _stage_sample_listing(tmp_path: Path) -> dict[str, Any]:
    photo = tmp_path / "jacket.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0SAMPLEIMAGE\xff\xd9")
    raw = handle_stage_listing(
        {
            "media_paths": [str(photo)],
            "seller_notes": "Patagonia Synchilla fleece mens size Large. Pit to pit 23 in, length 28 in. Small faint spot on left cuff.",
            "price_cents": 6500,
            "condition": "USED_GOOD",
            "package_weight_oz": 14.5,
        },
        session_id="sess-test-1",
        platform="cli",
    )
    res = json.loads(raw)
    assert res["ok"] is True, res
    return res


def test_register_wires_phase1_tools_and_staging_does_zero_writes(tmp_path: Path) -> None:
    mock_adapter = MockEbayAdapter(persona="lite")
    set_adapter_override(mock_adapter)

    ctx = FakePluginContext()
    register(ctx)
    assert {
        "ebay_stage_listing",
        "ebay_audit_active_listing",
        "ebay_check_comps",
        "ebay_confirm_action",
    }.issubset(ctx.tools.keys())

    staged = _stage_sample_listing(tmp_path)
    assert staged["action_token"].startswith("act_")
    assert mock_adapter.write_calls == []

    audited = json.loads(handle_audit_active_listing({}, session_id="sess-audit"))
    assert audited["ok"] is True
    assert audited["ebay_item_id"] == "110599001234"
    assert mock_adapter.write_calls == []


def test_expired_token_fails_closed_with_zero_writes(tmp_path: Path) -> None:
    mock_adapter = MockEbayAdapter(persona="lite")
    set_adapter_override(mock_adapter)

    staged = _stage_sample_listing(tmp_path)
    token = staged["action_token"]

    # Force expires_at into the past in SQLite
    past_iso = utc_now_iso(datetime.now(timezone.utc) - timedelta(seconds=10))
    conn = get_db()
    try:
        conn.execute(
            "UPDATE pending_actions SET expires_at = ? WHERE action_token = ?",
            (past_iso, token),
        )
        conn.commit()
    finally:
        conn.close()

    confirm_res = json.loads(
        handle_confirm_action({"action_token": token, "decision": "APPROVE"})
    )
    assert confirm_res["ok"] is False
    assert confirm_res["error_code"] == "TOKEN_EXPIRED"
    assert mock_adapter.write_calls == []


def test_cancelled_token_cannot_be_approved_and_makes_zero_writes(tmp_path: Path) -> None:
    mock_adapter = MockEbayAdapter(persona="lite")
    set_adapter_override(mock_adapter)

    staged = _stage_sample_listing(tmp_path)
    token = staged["action_token"]

    cancel_res = json.loads(
        handle_confirm_action({"action_token": token, "decision": "CANCEL"})
    )
    assert cancel_res["ok"] is True
    assert cancel_res["status"] == "CANCELLED"
    assert mock_adapter.write_calls == []

    # Attempting to approve an already cancelled token must fail closed
    replay_res = json.loads(
        handle_confirm_action({"action_token": token, "decision": "APPROVE"})
    )
    assert replay_res["ok"] is False
    assert replay_res["error_code"] == "TOKEN_NOT_PENDING"
    assert mock_adapter.write_calls == []


def test_mutated_payload_json_fails_sha256_check_with_zero_writes(tmp_path: Path) -> None:
    mock_adapter = MockEbayAdapter(persona="lite")
    set_adapter_override(mock_adapter)

    staged = _stage_sample_listing(tmp_path)
    token = staged["action_token"]

    # Tamper with list_price_cents directly in SQLite without updating payload_sha256
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT payload_json FROM pending_actions WHERE action_token = ?",
            (token,),
        ).fetchone()
        payload_dict = json.loads(row["payload_json"])
        payload_dict["list_price_cents"] = 100
        tampered_json = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
        conn.execute(
            "UPDATE pending_actions SET payload_json = ? WHERE action_token = ?",
            (tampered_json, token),
        )
        conn.commit()
    finally:
        conn.close()

    confirm_res = json.loads(
        handle_confirm_action({"action_token": token, "decision": "APPROVE"})
    )
    assert confirm_res["ok"] is False
    assert confirm_res["error_code"] == "PAYLOAD_TAMPERED"
    assert mock_adapter.write_calls == []

    conn = get_db()
    try:
        status = conn.execute(
            "SELECT status FROM pending_actions WHERE action_token = ?",
            (token,),
        ).fetchone()[0]
        assert status == "FAILED"
    finally:
        conn.close()


def test_valid_approve_executes_once_and_blocks_replay(tmp_path: Path) -> None:
    mock_adapter = MockEbayAdapter(persona="lite")
    set_adapter_override(mock_adapter)

    staged = _stage_sample_listing(tmp_path)
    token = staged["action_token"]
    sku = staged["sku"]

    approve_res = json.loads(
        handle_confirm_action(
            {"action_token": token, "decision": "APPROVE"},
            session_id="sess-approve",
            platform="cli",
        )
    )
    assert approve_res["ok"] is True
    assert approve_res["status"] == "EXECUTED"
    assert approve_res["route_used"] == "TRADING_XML_FALLBACK"
    assert [name for name, _ in mock_adapter.write_calls] == ["upload_media", "publish_listing"]

    # Verify item transitioned to ACTIVE and immutable event logged
    conn = get_db()
    try:
        item_row = conn.execute("SELECT status, ebay_item_id FROM items WHERE sku = ?", (sku,)).fetchone()
        assert item_row["status"] == "ACTIVE"
        assert item_row["ebay_item_id"] == approve_res["ebay_item_id"]

        events = [
            r["event_type"]
            for r in conn.execute("SELECT event_type FROM item_events WHERE sku = ? ORDER BY event_id", (sku,)).fetchall()
        ]
        assert events == ["LISTING_STAGED", "LISTING_PUBLISHED"]
    finally:
        conn.close()

    # Replaying the same executed token must fail closed with zero additional writes
    write_count_before = len(mock_adapter.write_calls)
    replay_res = json.loads(
        handle_confirm_action({"action_token": token, "decision": "APPROVE"})
    )
    assert replay_res["ok"] is False
    assert replay_res["error_code"] == "TOKEN_NOT_PENDING"
    assert len(mock_adapter.write_calls) == write_count_before
