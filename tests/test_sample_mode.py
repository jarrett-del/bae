"""Sample mode (mock adapter and the built-in tours) stays offline and keeps working after the
first approved write.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import hermes_ebay_ops.adapter.live as live_mod
from hermes_ebay_ops.adapter import LiveEbayAdapter, MockEbayAdapter, get_adapter
from hermes_ebay_ops.approval_hook import require_human_approval
from hermes_ebay_ops.config import configure_settings
from hermes_ebay_ops.db import get_db
from hermes_ebay_ops.demo import leave_tour, start_tour
from hermes_ebay_ops.models import StagedListingPayload
from hermes_ebay_ops.tools.phase1_mvp import handle_confirm_action, handle_stage_listing

JPEG_BYTES = b"\xff\xd8\xff\xe0SAMPLEIMAGE\xff\xd9"


def _stage(tmp_path: Path, name: str, notes: str) -> dict[str, Any]:
    photo = tmp_path / name
    photo.write_bytes(JPEG_BYTES)
    res = json.loads(
        handle_stage_listing({"media_paths": [str(photo)], "seller_notes": notes, "price_cents": 4200})
    )
    assert res["ok"] is True, res
    return res


def _approve(token: str) -> dict[str, Any]:
    return json.loads(handle_confirm_action({"action_token": token, "decision": "APPROVE"}))


def _payload(sku: str) -> StagedListingPayload:
    return StagedListingPayload(
        sku=sku, title=f"Sample {sku}", description="d", category_id="57988",
        condition="USED_GOOD", list_price_cents=1000,
    )


def test_every_sample_publish_gets_its_own_item_id(tmp_path: Path) -> None:
    # No adapter override: each tool call builds a fresh MockEbayAdapter, as real sample mode does.
    first = _stage(tmp_path, "one.jpg", "Patagonia Synchilla fleece mens size Large, pit to pit 23 in.")
    second = _stage(tmp_path, "two.jpg", "Levi's 501 jeans mens 34x32, inseam 32 in, light fade.")

    published = [_approve(first["action_token"]), _approve(second["action_token"])]

    assert [p["ok"] for p in published] == [True, True], published
    assert published[0]["ebay_item_id"] != published[1]["ebay_item_id"]
    conn = get_db()
    try:
        assert conn.execute("SELECT COUNT(*) FROM items WHERE status = 'ACTIVE'").fetchone()[0] == 2
    finally:
        conn.close()


def test_sample_ids_are_stable_per_sku_and_unique_across_adapters() -> None:
    one, other = MockEbayAdapter(persona="pro"), MockEbayAdapter(persona="pro")
    a, b = one.publish_listing(_payload("HRM-A")), other.publish_listing(_payload("HRM-B"))
    assert a.ebay_item_id != b.ebay_item_id and a.offer_id != b.offer_id
    assert MockEbayAdapter(persona="pro").publish_listing(_payload("HRM-A")).ebay_item_id == a.ebay_item_id
    assert a.ebay_item_id.isdigit() and len(a.ebay_item_id) == 12

    bulk = MockEbayAdapter(persona="pro").bulk_publish_listings([_payload(f"HRM-{i}") for i in range(25)])
    assert len({r.ebay_item_id for r in bulk}) == 25 and len({r.offer_id for r in bulk}) == 25


def test_active_tour_forces_the_sample_adapter_even_when_configured_live() -> None:
    configure_settings({"adapter_mode": "live", "mode": "auto"})
    assert isinstance(get_adapter(), LiveEbayAdapter)

    start_tour("closet")
    try:
        adapter = get_adapter()
        assert isinstance(adapter, MockEbayAdapter) and adapter.persona == "lite"
    finally:
        leave_tour()
    assert isinstance(get_adapter(), LiveEbayAdapter)


def test_chat_tools_during_a_tour_never_reach_ebay(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_network(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("a sample tour must never call the eBay broker")

    monkeypatch.setattr(live_mod, "credentialed_provider_request", no_network)
    configure_settings({"adapter_mode": "live", "mode": "auto"})

    start_tour("closet")
    try:
        staged = _stage(tmp_path, "tour.jpg", "Vintage Pyrex mixing bowl, no chips, 2.5 qt.")
        directive = require_human_approval(
            tool_name="ebay_confirm_action", args={"action_token": staged["action_token"], "decision": "APPROVE"}
        )
        assert directive is not None and directive["message"].startswith("Sample data only")
        published = _approve(staged["action_token"])
        assert published["ok"] is True, published
    finally:
        leave_tour()

    conn = get_db()  # the real ledger, now that the tour is over
    try:
        assert conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    finally:
        conn.close()
