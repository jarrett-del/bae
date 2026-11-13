"""Applies Hermes's read denylist to local files this plugin reads on the seller's behalf.

A local photo path handed to `ebay_stage_listing` ends up uploaded to eBay as a public listing
image, so it is checked against the same denylist Hermes's own file tools use (credential stores,
`.env` files, internal caches) before the plugin touches it.
"""

from __future__ import annotations


def local_read_block_error(path: str) -> str | None:
    """Return Hermes's refusal message when ``path`` is on its read denylist, else ``None``.

    Fails closed: if Hermes's guard cannot be imported or raises, the path is refused.
    """
    try:
        from agent.file_safety import get_read_block_error
    except ImportError:
        return (
            f"Access denied: {path} cannot be checked against Hermes's read denylist "
            "(agent.file_safety is unavailable), so local files are refused."
        )
    try:
        return get_read_block_error(path)
    except Exception as exc:  # noqa: BLE001 - any guard failure must refuse the read
        return f"Access denied: {path} could not be checked against Hermes's read denylist ({exc})."
