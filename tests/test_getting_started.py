"""First-run behaviour: sample data by default, a sign-in hint Hermes accepts, an honest setup
checklist, and an OAuth scope list that matches the eBay APIs the live adapter calls.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

import hermes_ebay_ops.adapter.live as live_mod
from hermes_ebay_ops.adapter import MockEbayAdapter, get_adapter
from hermes_ebay_ops.config import (
    CONNECT_COMMAND,
    NOT_CONNECTED_MESSAGE,
    SAMPLE_MODE_COMMAND,
    PluginSettings,
    configure_settings,
)
from hermes_ebay_ops.demo import leave_tour, start_tour
from hermes_ebay_ops.tools.phase1_mvp import handle_stage_listing

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = yaml.safe_load((REPO_ROOT / "plugin.yaml").read_text(encoding="utf-8"))
BASE = "/api/plugins/hermes-ebay-ops"
API_SCOPE = "https://api.ebay.com/oauth/api_scope"


def _client() -> TestClient:
    spec = importlib.util.spec_from_file_location("test_bae_getting_started_api", REPO_ROOT / "dashboard" / "plugin_api.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    app = FastAPI()
    app.include_router(mod.router, prefix=BASE)
    return TestClient(app)


def _connect_step(client: TestClient) -> dict[str, Any]:
    steps = client.get(f"{BASE}/state").json()["setup"]["steps"]
    return next(step for step in steps if step["title"] == "Connect your eBay seller account")


# --- Sample data by default -------------------------------------------------------------------


def test_fresh_install_runs_on_sample_data() -> None:
    assert PluginSettings().adapter_mode == "mock"
    assert MANIFEST["config_schema"]["adapter_mode"]["default"] == "mock"

    configure_settings(None)  # nothing configured, as on a fresh install
    assert isinstance(get_adapter(), MockEbayAdapter)


# --- The sign-in hint is a command Hermes accepts -------------------------------------------


def test_connect_command_matches_the_manifest() -> None:
    oauth_id = MANIFEST["oauth"][0]["id"]
    assert CONNECT_COMMAND == f"hermes auth add {MANIFEST['name']}:{oauth_id}"
    assert SAMPLE_MODE_COMMAND.endswith(f"plugins.entries.{MANIFEST['name']}.settings.adapter_mode mock")


def test_no_source_suggests_the_rejected_sign_in_form() -> None:
    offenders = [
        str(path.relative_to(REPO_ROOT))
        for path in REPO_ROOT.rglob("*")
        if path.suffix in {".py", ".js", ".md", ".yaml"}
        and not {".venv", "tests", "__pycache__"} & set(path.relative_to(REPO_ROOT).parts)
        and f"{MANIFEST['name']} {MANIFEST['oauth'][0]['id']}" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == []


def test_live_mode_without_sign_in_explains_both_ways_forward(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hermes_cli.plugin_provider_requests import ProviderNotSignedIn

    def not_signed_in(*_args: Any, **_kwargs: Any) -> Any:
        raise ProviderNotSignedIn("hermes-ebay-ops:ebay is not signed in")

    monkeypatch.setattr(live_mod, "credentialed_provider_request", not_signed_in)
    configure_settings({"adapter_mode": "live", "mode": "auto"})
    photo = tmp_path / "front.jpg"
    photo.write_bytes(b"\xff\xd8\xff\xe0SAMPLEIMAGE\xff\xd9")

    res = json.loads(handle_stage_listing({"media_paths": [str(photo)], "seller_notes": "Vintage camera, works well."}))

    assert res["ok"] is False and res["error_code"] == "AUTH_REQUIRED"
    assert res["message"].startswith(NOT_CONNECTED_MESSAGE)
    assert f"`{CONNECT_COMMAND}`" in res["message"] and f"`{SAMPLE_MODE_COMMAND}`" in res["message"]


# --- The setup checklist does not call sample mode "connected" -------------------------------


def test_setup_checklist_marks_sample_mode_and_tours_as_not_connected() -> None:
    client = _client()

    step = _connect_step(client)
    assert step["done"] is False and step["detail"].startswith("Sample mode")

    start_tour("closet")
    try:
        step = _connect_step(client)
        assert step["done"] is False and step["detail"].startswith("Sample mode")
    finally:
        leave_tour()


# --- Scopes match the eBay APIs the live adapter calls ---------------------------------------

# API path prefix -> the OAuth scope eBay requires for it.
_SCOPE_FOR_API = {
    "/sell/inventory/": "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "/commerce/media/": "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "/sell/account/": "https://api.ebay.com/oauth/api_scope/sell.account",
    "/sell/fulfillment/": "https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly",
    "/commerce/taxonomy/": API_SCOPE,
    "/sell/metadata/": API_SCOPE,
    "/buy/browse/": API_SCOPE,
}


def test_declared_scopes_are_exactly_what_the_live_adapter_needs() -> None:
    source = (REPO_ROOT / "adapter" / "live.py").read_text(encoding="utf-8")
    called = set(re.findall(r"/(?:sell|buy|commerce)/[a-z_]+/", source))
    unknown = called - set(_SCOPE_FOR_API)
    assert not unknown, f"map the scope for {sorted(unknown)} in _SCOPE_FOR_API and plugin.yaml"

    needed = {_SCOPE_FOR_API[prefix] for prefix in called}
    declared = set(MANIFEST["oauth"][0]["scopes"])
    # Hermes refuses a sign-in unless eBay grants every declared scope, so extras only add ways to fail.
    assert declared == needed
