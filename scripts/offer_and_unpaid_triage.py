"""Zero-token (`--no-agent`) Best Offer PLG floor & 96-hour unpaid commitment check.

Prints `[SILENT]` when zero Active offers and zero 96h unpaid commitments exist.
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


def evaluate_text(plg_ad_rate_bps: int = 500) -> str:
    get_adapter = _submod("adapter").get_adapter
    get_settings = _submod("config").get_settings
    db_mod = _submod("db")
    get_db = db_mod.get_db
    get_item_by_ebay_item_id = db_mod.get_item_by_ebay_item_id
    get_item_by_sku = db_mod.get_item_by_sku
    get_or_create_seller_profile = db_mod.get_or_create_seller_profile
    detect_unpaid_96h_offers = _submod("diagnostics").detect_unpaid_96h_offers
    BestOfferLifecycleStatus = _submod("models").BestOfferLifecycleStatus
    tools_mod = _submod("tools")
    compute_economics = tools_mod.compute_economics
    format_usd = tools_mod.format_usd

    settings = get_settings()
    conn = get_db(settings.plugin_name)
    try:
        profile = get_or_create_seller_profile(conn)
        fee_bps = int(profile["estimated_fee_bps"])
        try:
            offers = get_adapter(settings).list_best_offers()
        except Exception:
            offers = []

        active = [o for o in offers if o.status == BestOfferLifecycleStatus.ACTIVE]
        seller_accept_dicts = [
            {
                "best_offer_id": o.best_offer_id,
                "ebay_item_id": o.ebay_item_id,
                "sku": o.sku,
                "status": o.status.value,
            }
            for o in offers
            if o.status == BestOfferLifecycleStatus.SELLER_ACCEPT
        ]
        unpaid_96h = detect_unpaid_96h_offers(seller_accept_dicts)
        if not active and not unpaid_96h:
            return "[SILENT]"

        lines = [f"[OFFER & 96H UNPAID CHECK] {len(active)} active offer(s), {len(unpaid_96h)} >=96h unpaid:"]
        for off in active:
            local = (get_item_by_sku(conn, off.sku) if off.sku else None) or get_item_by_ebay_item_id(conn, off.ebay_item_id)
            cogs = local["cogs_cents"] if local else None
            floor_c = local["floor_price_cents"] if local else None
            ship_c = int(local["shipping_est_cents"]) if local else 0
            econ = compute_economics(
                list_price_cents=off.offer_price_cents,
                shipping_est_cents=ship_c,
                estimated_fee_bps=fee_bps,
                cogs_cents=cogs,
                plg_ad_rate_bps=plg_ad_rate_bps,
            )
            verdict = "ACCEPT_ELIGIBLE" if (floor_c and off.offer_price_cents >= int(floor_c)) else "COUNTER_REVIEW"
            lines.append(
                f"- {off.best_offer_id} ({off.ebay_item_id}): Offer {format_usd(off.offer_price_cents)} | "
                f"Net after {plg_ad_rate_bps / 100:.1f}% PLG: {format_usd(econ.estimated_net_payout_cents)} -> {verdict}"
            )
        for up in unpaid_96h:
            lines.append(
                f"- UNPAID >=96h: {up['best_offer_id']} on {up['ebay_item_id']} ({up['hours_unpaid']}h) -> {up['recommendation']}"
            )
        return "\n".join(lines)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-token Best Offer PLG floor & 96h unpaid check")
    parser.add_argument("--plg-bps", type=int, default=500)
    args = parser.parse_args(argv)
    print(evaluate_text(plg_ad_rate_bps=args.plg_bps))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
