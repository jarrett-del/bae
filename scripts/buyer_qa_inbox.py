"""Zero-token (`--no-agent`) Buyer Q&A measurement responder & 3-day INAD landmark shield.

Prints `[SILENT]` when no buyer question is queued.
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


def evaluate_text(question: str = "", sku: str | None = None) -> str:
    if not question.strip():
        return "[SILENT]"

    get_settings = _submod("config").get_settings
    db_mod = _submod("db")
    get_db = db_mod.get_db
    get_item_by_sku = db_mod.get_item_by_sku
    draft_buyer_qa_reply = _submod("diagnostics").draft_buyer_qa_reply

    conn = get_db(get_settings().plugin_name)
    try:
        item = get_item_by_sku(conn, sku) if sku else None
        if item is None:
            row = conn.execute(
                "SELECT * FROM items WHERE status IN ('ACTIVE', 'STAGED') ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
            item = dict(row) if row else None
    finally:
        conn.close()

    if item is None:
        return "[SILENT]"

    drafted = draft_buyer_qa_reply(item, question)
    lines = [
        f"[BUYER Q&A DRAFT] SKU {drafted['sku']} (<2,000 chars):",
        drafted["draft_reply"],
    ]
    if drafted["inad_defense_check"]:
        lines.append(f"[INAD 3-DAY SHIELD] {drafted['inad_defense_check']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Zero-token Buyer Q&A measurement & INAD reply builder")
    parser.add_argument("--question", type=str, default="")
    parser.add_argument("--sku", type=str, default=None)
    args = parser.parse_args(argv)
    print(evaluate_text(question=args.question, sku=args.sku))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
