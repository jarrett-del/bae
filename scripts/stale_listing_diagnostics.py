"""Zero-token (`--no-agent`) daily Cassini 90–180d stale-listing check.

Prints `[SILENT]` when zero active listings exceed `min_age_days`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
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


def evaluate_text(min_age_days: int = 90, daily_prune_cap: int = 5) -> str:
    get_settings = _submod("config").get_settings
    get_db = _submod("db").get_db
    analyze_stale_listings = _submod("diagnostics").analyze_stale_listings
    format_usd = _submod("tools").format_usd

    conn = get_db(get_settings().plugin_name)
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM items WHERE status = 'ACTIVE'").fetchall()]
        for r in rows:
            try:
                r["aspects"] = json.loads(r.get("aspects_json") or "{}")
            except Exception:
                r["aspects"] = {}
        stale = analyze_stale_listings(rows, min_age_days=min_age_days, daily_prune_cap=daily_prune_cap)
    finally:
        conn.close()

    if not stale:
        return "[SILENT]"

    lines = [f"[CASSINI STALE CHECK] {len(stale)} listing(s) >= {min_age_days}d active:"]
    for item in stale:
        lines.append(
            f"- {item['sku']} ({item['age_days']}d · {item['playbook']}): {item['strategy']} "
            f"{format_usd(item['current_price_cents'])} -> {format_usd(item['suggested_price_cents'])} | {item['reason']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-token eBay 90-180d Cassini stale listing diagnostic")
    parser.add_argument("--min-age-days", type=int, default=90)
    parser.add_argument("--daily-prune-cap", type=int, default=5)
    args = parser.parse_args(argv)
    print(evaluate_text(min_age_days=args.min_age_days, daily_prune_cap=args.daily_prune_cap))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
