"""Zero-LLM-token (`--no-agent`) scheduled cron helpers for `hermes-ebay-ops`.

Every script prints `[SILENT]` and exits 0 when no actionable event exists, so
Hermes's scheduler suppresses noisy empty alerts while keeping token burn at $0.00.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

CRON_BLUEPRINTS: tuple[dict[str, Any], ...] = (
    {
        "id": "ebay-stale-cassini-check",
        "title": "Cassini 90–180d Stale Diagnostics",
        "schedule": "30 6 * * *",
        "human_schedule": "Daily · 06:30",
        "mode": "--no-agent",
        "script": "scripts/stale_listing_diagnostics.py",
        "summary": "Splits aged listings into in-place aspect upgrades vs. 5% daily gentle pruning.",
    },
    {
        "id": "ebay-offer-96h-unpaid-triage",
        "title": "Best Offer PLG Floor & 96h Unpaid Guard",
        "schedule": "0 */2 * * *",
        "human_schedule": "Every 2 hours",
        "mode": "--no-agent",
        "script": "scripts/offer_and_unpaid_triage.py",
        "summary": "Triages Active offers after 5% PLG fee & flags 96h unpaid SellerAccept holds.",
    },
    {
        "id": "ebay-buyer-qa-inad-shield",
        "title": "Buyer Q&A & 3-Day INAD Shield",
        "schedule": "*/30 * * * *",
        "human_schedule": "Every 30 mins",
        "mode": "--no-agent",
        "script": "scripts/buyer_qa_inbox.py",
        "summary": "Drafts facts-only ruler measurement replies (<2,000 chars) & INAD landmark checks.",
    },
    {
        "id": "ebay-evening-haul-sprint",
        "title": "Evening 3-Item Death-Pile Sprint",
        "schedule": "30 19 * * *",
        "human_schedule": "Daily · 19:30",
        "mode": "--no-agent",
        "script": "scripts/haul_micro_sprint.py",
        "summary": "Prompts a 9-minute 3-item listing sprint until Haul break-even hits $0 ([SILENT] once clear).",
    },
)


def load_plugin_root() -> Any:
    """Resolve the parent plugin package whether imported by Hermes, pytest, or standalone CLI."""
    if __package__ and "." in __package__:
        parent_name = __package__.rsplit(".", 1)[0]
        if parent_name in sys.modules:
            return sys.modules[parent_name]
    for candidate in ("_plugin_pkg_hermes_ebay_ops", "hermes_plugins_hermes_ebay_ops", "hermes_ebay_ops"):
        if candidate in sys.modules:
            return sys.modules[candidate]

    root_dir = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "hermes_ebay_ops",
        root_dir / "__init__.py",
        submodule_search_locations=[str(root_dir)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load plugin package at {root_dir}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["hermes_ebay_ops"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_submod(rel_name: str) -> Any:
    """Import a sibling plugin submodule (`config`, `db`, `tools`, etc.) from the active plugin root."""
    import importlib

    root = load_plugin_root()
    return importlib.import_module(f"{root.__name__}.{rel_name}")


def run_cron_job(job_id: str, *, sample_question: str | None = None) -> dict[str, Any]:
    """Execute any bundled `--no-agent` cron check in-process and return its output."""
    from . import (
        buyer_qa_inbox,
        haul_micro_sprint,
        offer_and_unpaid_triage,
        stale_listing_diagnostics,
    )

    if job_id == "ebay-stale-cassini-check":
        out = stale_listing_diagnostics.evaluate_text()
    elif job_id == "ebay-offer-96h-unpaid-triage":
        out = offer_and_unpaid_triage.evaluate_text()
    elif job_id == "ebay-buyer-qa-inad-shield":
        q = sample_question or "What is the exact pit-to-pit measurement and any cuff wear?"
        out = buyer_qa_inbox.evaluate_text(question=q)
    elif job_id == "ebay-evening-haul-sprint":
        out = haul_micro_sprint.evaluate_text()
    else:
        raise ValueError(f"Unknown cron job_id: {job_id!r}")

    silent = out.strip() == "[SILENT]"
    return {
        "job_id": job_id,
        "mode": "--no-agent",
        "llm_tokens_used": 0,
        "silent": silent,
        "output": out,
        "preview_json": json.dumps({"job_id": job_id, "silent": silent}, ensure_ascii=False),
    }
