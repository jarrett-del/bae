"""Verification of the shared Dashboard + Webapp FastAPI backend (`dashboard/plugin_api.py`)
and isolated Sample Seller Tours (`demo.py`).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import hermes_ebay_ops.config as config_mod

REPO_ROOT = Path(__file__).resolve().parent.parent


def _build_client() -> TestClient:
    api_path = REPO_ROOT / "dashboard" / "plugin_api.py"
    spec = importlib.util.spec_from_file_location("test_bae_plugin_api", api_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    app = FastAPI()
    app.include_router(mod.router, prefix="/api/plugins/hermes-ebay-ops")
    return TestClient(app)


def test_tour_isolation_and_progressive_lite_vs_pro_modes(isolated_plugin_storage: Path) -> None:
    config_mod.configure_settings({"adapter_mode": "mock", "mock_persona": "lite", "mode": "auto"})
    client = _build_client()
    base = "/api/plugins/hermes-ebay-ops"

    # 1. Fresh empty ledger starts in clean Lite mode
    hb = client.post(f"{base}/hello")
    assert hb.status_code == 200
    assert hb.json()["ok"] is True

    initial = client.get(f"{base}/state").json()
    assert initial["ok"] is True
    assert initial["mode"] == "lite"
    assert initial["tour"] is None
    assert initial["totals"]["total_items"] == 0
    assert initial["desktop_live"] is True

    # 2. Switch into Casual Closet C2C Tour (isolated tour.db)
    r_closet = client.post(f"{base}/tour", json={"tour_id": "closet"})
    assert r_closet.status_code == 200
    s_closet = client.get(f"{base}/state").json()
    assert s_closet["tour"]["id"] == "closet"
    assert s_closet["mode"] == "lite"
    assert s_closet["profile"]["policies_opted_in"] is False
    assert s_closet["totals"]["total_items"] == 4
    assert s_closet["profile"]["db_path"].endswith("tour.db")

    # 3. Switch into Pro Vintage & Camera Vault Tour
    r_vault = client.post(f"{base}/tour", json={"tour_id": "vault"})
    assert r_vault.status_code == 200
    s_vault = client.get(f"{base}/state").json()
    assert s_vault["tour"]["id"] == "vault"
    assert s_vault["mode"] == "pro"
    assert s_vault["profile"]["policies_opted_in"] is True
    assert s_vault["totals"]["active_count"] >= 4
    assert s_vault["totals"]["pending_approvals_count"] >= 2
    assert s_vault["totals"]["awaiting_payment_offers_count"] == 1

    # Verify natural warehouse bin sorting (A-01-02, A-02-04, B-01-03, C-02-02)
    bins = [row["bin_code"] for row in s_vault["pick_list"]]
    assert bins == ["A-01-02", "A-02-04", "B-01-03", "C-02-02"]

    # Toggle packed state on one pick line
    first_order = s_vault["pick_list"][0]["order_id"]
    toggle_res = client.post(f"{base}/pick/{first_order}/toggle").json()
    assert toggle_res["packed"] is True
    after_toggle = client.get(f"{base}/state").json()
    assert after_toggle["totals"]["unpacked_pick_count"] == len(bins) - 1

    # 4. Leave tour -> real data.db is completely untouched (still 0 items!)
    r_leave = client.post(f"{base}/tour", json={"action": "leave"})
    assert r_leave.status_code == 200
    restored = client.get(f"{base}/state").json()
    assert restored["tour"] is None
    assert restored["totals"]["total_items"] == 0
    assert restored["profile"]["db_path"].endswith("data.db")


def test_ui_stage_enrich_offer_and_two_step_confirm_gate(isolated_plugin_storage: Path) -> None:
    config_mod.configure_settings({"adapter_mode": "mock", "mock_persona": "lite", "mode": "auto"})
    client = _build_client()
    base = "/api/plugins/hermes-ebay-ops"

    # Stage a casual draft in Lite mode without SKU/COGS/Bin
    staged = client.post(
        f"{base}/stage",
        json={
            "title_hint": "Patagonia Better Sweater Quarter Zip Mens Large Navy",
            "seller_notes": "Gently worn fleece pullover, chest 22.5 in, length 28 in, no pilling or holes.",
            "price_cents": 5800,
            "package_weight_oz": 15.0,
        },
    )
    assert staged.status_code == 200
    staged_data = staged.json()
    sku = staged_data["sku"]
    token = staged_data["action_token"]

    state_1 = client.get(f"{base}/state").json()
    assert state_1["mode"] == "lite"
    assert state_1["totals"]["staged_count"] == 1
    assert state_1["totals"]["pending_approvals_count"] == 1

    # Enrich item with COGS + Bin + Floor -> progressively unlocks Pro Ops automatically!
    enr = client.post(
        f"{base}/item/{sku}/enrich",
        json={"cogs_cents": 1400, "floor_price_cents": 4600, "bin_code": "A-09"},
    )
    assert enr.status_code == 200
    assert enr.json()["pro_ops_enabled"] is True

    state_2 = client.get(f"{base}/state").json()
    assert state_2["mode"] == "pro"

    # Approve staged listing through the cryptographic two-step gate
    approved = client.post(
        f"{base}/confirm",
        json={"action_token": token, "decision": "APPROVE"},
    )
    assert approved.status_code == 200
    assert approved.json()["ok"] is True

    # Replaying the consumed token MUST fail closed with HTTP 400
    replay = client.post(
        f"{base}/confirm",
        json={"action_token": token, "decision": "APPROVE"},
    )
    assert replay.status_code == 400

    # Stage a Best Offer counter decision and verify it links in /state, then cancel it
    offer_stage = client.post(
        f"{base}/offer/stage",
        json={
            "best_offer_id": "BO-TEST-99",
            "ebay_item_id": "110555000111",
            "sku": sku,
            "action": "Counter",
            "counter_price_cents": 5200,
            "seller_message": "Meet me at $52 shipped today!",
        },
    )
    assert offer_stage.status_code == 200
    offer_tok = offer_stage.json()["action_token"]

    cancelled = client.post(
        f"{base}/confirm",
        json={"action_token": offer_tok, "decision": "CANCEL"},
    )
    assert cancelled.status_code == 200
    assert client.get(f"{base}/state").json()["totals"]["pending_approvals_count"] == 0
