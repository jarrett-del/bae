"""Zero-token (`--no-agent`) 7:30 PM 3-item Death-Pile Micro-Sprint & Haul break-even nudge.

Prints `[SILENT]` once all tracked Haul / Closet Clearout batches have `unlisted_pile_count == 0`.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


def _submod(rel_name: str):
    if __package__:
        from . import load_submod

        return load_submod(rel_name)
    import importlib

    root_dir = Path(__file__).resolve().parent.parent
    if "hermes_ebay_ops" not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            "hermes_ebay_ops",
            root_dir / "__init__.py",
            submodule_search_locations=[str(root_dir)],
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules["hermes_ebay_ops"] = mod
        spec.loader.exec_module(mod)
    return importlib.import_module(f"hermes_ebay_ops.{rel_name}")


def evaluate_text() -> str:
    list_haul_summaries = _submod("cache_store").list_haul_summaries
    get_settings = _submod("config").get_settings
    db_mod = _submod("db")
    get_db = db_mod.get_db
    get_or_create_seller_profile = db_mod.get_or_create_seller_profile
    tools_mod = _submod("tools")
    compute_economics = tools_mod.compute_economics
    format_usd = tools_mod.format_usd

    settings = get_settings()
    conn = get_db(settings.plugin_name)
    try:
        profile = get_or_create_seller_profile(conn)
        fee_bps = int(profile["estimated_fee_bps"])
        items = [dict(r) for r in conn.execute("SELECT * FROM items").fetchall()]
        for it in items:
            econ = compute_economics(
                list_price_cents=int(it["list_price_cents"]),
                shipping_est_cents=int(it["shipping_est_cents"] or 0),
                estimated_fee_bps=fee_bps,
                cogs_cents=it.get("cogs_cents"),
            )
            it["estimated_net_payout_cents"] = econ.estimated_net_payout_cents
        hauls = list_haul_summaries(items, plugin_name=settings.plugin_name)
    finally:
        conn.close()

    pending_hauls = [h for h in hauls if int(h["unlisted_pile_count"]) > 0]
    if not pending_hauls:
        return "[SILENT]"

    lines = [f"[EVENING 3-ITEM MICRO-SPRINT] {len(pending_hauls)} active haul(s) with unlisted inventory:"]
    for h in pending_hauls:
        lines.append(
            f"- {h['label']}: {h['unlisted_pile_count']} unlisted ({h['pct_listed']}% listed) | "
            f"Break-even remaining: {format_usd(h['remaining_breakeven_cents'])} | "
            f"List {h['sprint_items']} items (~{h['sprint_minutes']} min) -> ~{format_usd(h['sprint_ebay_balance_unlock_cents'])} eBay Balance"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-token evening 3-item Haul micro-sprint nudge")
    parser.parse_args(argv)
    print(evaluate_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
