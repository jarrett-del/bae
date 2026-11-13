"""Pytest harness loading the repository root as package `hermes_ebay_ops`
(mirroring Hermes Agent's dynamic plugin package loader) and providing isolated
temporary `plugin_db` / `credentialed_provider_request` / `agent.file_safety` test doubles when running
outside a full Hermes CLI runtime.
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
import types
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _install_hermes_stubs_if_absent() -> None:
    """Provide standalone test shims for Hermes host modules if Hermes is not installed."""
    if "plugins.plugin_storage" not in sys.modules:
        try:
            import plugins.plugin_storage  # noqa: F401
        except ImportError:
            plugins_pkg = sys.modules.setdefault("plugins", types.ModuleType("plugins"))
            storage_mod = types.ModuleType("plugins.plugin_storage")
            storage_mod._ACTIVE_DATA_ROOT = Path("/tmp/hermes-ebay-ops-default-test")  # type: ignore[attr-defined]

            def plugin_data_dir(plugin_name: str) -> Path:
                base = Path(storage_mod._ACTIVE_DATA_ROOT) / plugin_name  # type: ignore[attr-defined]
                base.mkdir(parents=True, exist_ok=True)
                return base

            def plugin_db(plugin_name: str, filename: str = "data.db") -> sqlite3.Connection:
                db_path = plugin_data_dir(plugin_name) / filename
                conn = sqlite3.connect(str(db_path))
                conn.execute("PRAGMA journal_mode = WAL;")
                return conn

            storage_mod.plugin_data_dir = plugin_data_dir  # type: ignore[attr-defined]
            storage_mod.plugin_db = plugin_db  # type: ignore[attr-defined]
            plugins_pkg.plugin_storage = storage_mod  # type: ignore[attr-defined]
            sys.modules["plugins.plugin_storage"] = storage_mod

    if "hermes_cli.plugin_provider_requests" not in sys.modules:
        try:
            import hermes_cli.plugin_provider_requests  # noqa: F401
        except ImportError:
            cli_pkg = sys.modules.setdefault("hermes_cli", types.ModuleType("hermes_cli"))
            req_mod = types.ModuleType("hermes_cli.plugin_provider_requests")

            class ProviderNotSignedIn(RuntimeError):
                """Raised when the user has not completed OAuth sign-in in Hermes."""

            class ProviderResponse:
                def __init__(
                    self,
                    status: int = 200,
                    headers: dict[str, str] | None = None,
                    body: bytes = b"",
                ) -> None:
                    self.status = status
                    self.headers = headers or {}
                    self.body = body

                @property
                def text(self) -> str:
                    return self.body.decode("utf-8", errors="replace")

                def json(self) -> Any:
                    import json as _json

                    return _json.loads(self.text)

            def credentialed_provider_request(
                provider: str,
                method: str,
                url: str,
                *,
                json: Any | None = None,
                body: bytes | str | None = None,
                headers: dict[str, str] | None = None,
                timeout: float = 30.0,
            ) -> ProviderResponse:
                raise ProviderNotSignedIn(
                    f"No live Hermes credential broker active in unit test for provider={provider!r} ({method} {url})"
                )

            req_mod.ProviderNotSignedIn = ProviderNotSignedIn  # type: ignore[attr-defined]
            req_mod.ProviderResponse = ProviderResponse  # type: ignore[attr-defined]
            req_mod.credentialed_provider_request = credentialed_provider_request  # type: ignore[attr-defined]
            cli_pkg.plugin_provider_requests = req_mod  # type: ignore[attr-defined]
            sys.modules["hermes_cli.plugin_provider_requests"] = req_mod

    if "agent.file_safety" not in sys.modules:
        try:
            import agent.file_safety  # noqa: F401
        except ImportError:
            agent_pkg = sys.modules.setdefault("agent", types.ModuleType("agent"))
            safety_mod = types.ModuleType("agent.file_safety")

            def get_read_block_error(path: str) -> str | None:
                """Stand-in for Hermes's read denylist: like the real one, it resolves symlinks
                and refuses secret-bearing `.env` files anywhere on disk."""
                if Path(path).expanduser().resolve().name.lower() == ".env":
                    return f"Access denied: {path} is a secret-bearing environment file and cannot be read."
                return None

            safety_mod.get_read_block_error = get_read_block_error  # type: ignore[attr-defined]
            agent_pkg.file_safety = safety_mod  # type: ignore[attr-defined]
            sys.modules["agent.file_safety"] = safety_mod


def _load_plugin_package() -> types.ModuleType:
    if "hermes_ebay_ops" in sys.modules:
        return sys.modules["hermes_ebay_ops"]
    spec = importlib.util.spec_from_file_location(
        "hermes_ebay_ops",
        REPO_ROOT / "__init__.py",
        submodule_search_locations=[str(REPO_ROOT)],
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["hermes_ebay_ops"] = module
    spec.loader.exec_module(module)
    return module


_install_hermes_stubs_if_absent()
_load_plugin_package()


@pytest.fixture(autouse=True)
def isolated_plugin_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate plugin_db SQLite storage and reset plugin settings per test."""
    import plugins.plugin_storage as storage_mod
    from hermes_ebay_ops.config import configure_settings

    if hasattr(storage_mod, "_ACTIVE_DATA_ROOT"):
        monkeypatch.setattr(storage_mod, "_ACTIVE_DATA_ROOT", tmp_path / "plugin-data")
    else:
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))

    configure_settings({"adapter_mode": "mock", "mock_persona": "lite", "mode": "auto"})
    yield tmp_path
    try:
        from hermes_ebay_ops.adapter import set_adapter_override

        set_adapter_override(None)
    except ImportError:
        pass
    configure_settings(None)
