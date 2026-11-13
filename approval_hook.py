"""`pre_tool_call` hook that sends every model-issued APPROVE to Hermes's own approval prompt.

The staging tools hand their `action_token` back to the model, so holding a token is not proof
that a person agreed to the write. This hook makes Hermes ask the person before
`ebay_confirm_action(decision="APPROVE")` runs: the call is escalated to the same human-approval
gate Hermes uses for dangerous shell commands, and a denial, a timeout or a run with nobody
present blocks it.

The Approve buttons in the Seller Desk and dashboard do not pass through here. They call the
plugin's confirm route directly, and the click is the approval.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .adapter import MockEbayAdapter, get_adapter
from .config import get_settings
from .db import get_db
from .models import ActionStatus, ActionType
from .tools import format_usd

CONFIRM_TOOL_NAME = "ebay_confirm_action"
HOOK_NAME = "pre_tool_call"

LIVE_PREFIX = "Live eBay write: "
SAMPLE_PREFIX = "Sample data only, nothing is sent to eBay: "


def _one_line(raw: Any, limit: int = 80) -> str:
    """Flatten untrusted text (titles, SKUs, ids) to one printable, bounded line."""
    text = "".join(ch for ch in " ".join(str(raw or "").split()) if ch.isprintable())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _listing_summary(payload: dict[str, Any]) -> str:
    summary = (
        f'"{_one_line(payload.get("title"))}" ({_one_line(payload.get("sku"), 50)}) '
        f"at {format_usd(int(payload.get('list_price_cents') or 0))}"
    )
    local_photos = [Path(str(p)).name for p in (payload.get("media_paths") or [])]
    if local_photos:
        shown = ", ".join(_one_line(name, 40) for name in local_photos[:3])
        more = f", +{len(local_photos) - 3} more" if len(local_photos) > 3 else ""
        summary += f", uploading {len(local_photos)} local photo(s) as public listing images ({shown}{more})"
    return summary


def _summarize(action_type: str, payload: dict[str, Any]) -> str:
    if action_type == ActionType.PUBLISH_LISTING.value:
        return f"publish a new listing: {_listing_summary(payload)}"

    if action_type == ActionType.REVISE_LISTING.value:
        changes = payload.get("changes") or {}
        fields = ", ".join(_one_line(key, 30) for key in sorted(changes)) or "no fields"
        price = changes.get("list_price_cents")
        price_note = f", new price {format_usd(int(price))}" if isinstance(price, (int, float)) else ""
        return (
            f"revise listing {_one_line(payload.get('ebay_item_id'), 30)} "
            f"({_one_line(payload.get('sku'), 50)}): changes {fields}{price_note}"
        )

    if action_type == ActionType.BOOTSTRAP_POLICIES.value:
        return (
            "opt this eBay account into Business Policies "
            f"({_one_line(payload.get('marketplace_id') or 'EBAY_US', 20)})"
        )

    if action_type == ActionType.RESPOND_OFFER.value:
        action = _one_line(payload.get("action"), 20)
        counter = payload.get("counter_price_cents")
        counter_note = f" at {format_usd(int(counter))}" if isinstance(counter, (int, float)) else ""
        return (
            f"send a binding Best Offer response: {action}{counter_note} on offer "
            f"{_one_line(payload.get('best_offer_id'), 30)} for item {_one_line(payload.get('ebay_item_id'), 30)}"
        )

    if action_type == ActionType.BULK_PUBLISH.value:
        items = [item for item in (payload.get("items") or []) if isinstance(item, dict)]
        skus = ", ".join(_one_line(item.get("sku"), 50) for item in items[:3])
        more = f", +{len(items) - 3} more" if len(items) > 3 else ""
        total = sum(int(item.get("list_price_cents") or 0) for item in items)
        return (
            f"publish {len(items)} new listing(s) in one batch ({skus}{more}), "
            f"total list price {format_usd(total)}"
        )

    return f"run staged write of type {_one_line(action_type, 40)}"


def _sample_mode() -> bool:
    """True when the write would run on the sample adapter (a tour, or `adapter_mode: mock`).

    Asks the same selector the tool handler uses, so the prompt and the write cannot disagree.
    Any doubt counts as live.
    """
    try:
        return isinstance(get_adapter(get_settings()), MockEbayAdapter)
    except Exception:  # noqa: BLE001
        return False


def describe_staged_action(action_token: str) -> str:
    """One-line summary of a staged write for Hermes's approval prompt.

    Read-only. A token that is unknown or no longer pending still gets a prompt, with a message
    that says so, because the gate is unconditional; the tool then refuses it without any write.
    """
    shown_token = _one_line(action_token, 40) or "(no token)"
    try:
        conn = get_db(get_settings().plugin_name)
        try:
            row = conn.execute(
                "SELECT action_type, status, payload_json FROM pending_actions WHERE action_token = ?",
                (action_token,),
            ).fetchone()
        finally:
            conn.close()
        if row is None or str(row["status"]) != ActionStatus.PENDING.value:
            return (
                f"Approve staged eBay action {shown_token}: it is unknown or no longer pending, "
                "so bae will refuse it and change nothing on eBay"
            )
        payload = json.loads(str(row["payload_json"]))
        summary = _summarize(str(row["action_type"]), payload if isinstance(payload, dict) else {})
        return (SAMPLE_PREFIX if _sample_mode() else LIVE_PREFIX) + summary
    except Exception:  # noqa: BLE001 - a summary failure must never drop the approval prompt
        return f"Run staged eBay write {shown_token} on the live store (bae could not load its preview)"


def require_human_approval(tool_name: str = "", args: Any = None, **_kwargs: Any) -> dict[str, str] | None:
    """Escalate every `ebay_confirm_action(decision="APPROVE")` call to Hermes's approval gate.

    `rule_key` is derived from the single-use token, so answering "session" or "always" at the
    prompt can never pre-approve a different write. CANCEL passes through without a prompt.
    """
    if tool_name != CONFIRM_TOOL_NAME:
        return None
    call_args = args if isinstance(args, dict) else {}
    if str(call_args.get("decision") or "").strip().upper() != "APPROVE":
        return None
    token = str(call_args.get("action_token") or "").strip()
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return {
        "action": "approve",
        "message": describe_staged_action(token),
        "rule_key": f"{CONFIRM_TOOL_NAME}:{token_digest}",
    }
