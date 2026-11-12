"""Unified EbayAdapter Protocol isolating split eBay REST and XML surfaces."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..models import (
    AccountCapabilities,
    ActiveListingSnapshot,
    BestOfferRecord,
    CategoryAspectSpec,
    CompSummary,
    OfferActionReceipt,
    PaidOrderLine,
    PublishReceipt,
    RespondOfferPayload,
    StagedListingPayload,
)


@runtime_checkable
class EbayAdapter(Protocol):
    def check_policy_status(self, marketplace_id: str = "EBAY_US") -> AccountCapabilities: ...

    def bootstrap_policies(self, marketplace_id: str = "EBAY_US") -> AccountCapabilities: ...

    def upload_media(self, file_paths: list[str]) -> list[str]: ...

    def suggest_category(self, query: str, marketplace_id: str = "EBAY_US") -> str: ...

    def get_category_aspects(
        self, category_id: str, marketplace_id: str = "EBAY_US"
    ) -> list[CategoryAspectSpec]: ...

    def search_comps(
        self, query: str, category_id: str | None, condition: str | None
    ) -> CompSummary: ...

    def fetch_recent_active_listings(self, limit: int = 10) -> list[ActiveListingSnapshot]: ...

    def publish_listing(self, payload: StagedListingPayload) -> PublishReceipt: ...

    def revise_listing(
        self, sku: str, ebay_item_id: str, changes: dict[str, Any]
    ) -> PublishReceipt: ...

    def list_best_offers(self, ebay_item_id: str | None = None) -> list[BestOfferRecord]: ...

    def respond_to_offer(self, payload: RespondOfferPayload) -> OfferActionReceipt: ...

    def list_paid_orders(self, limit: int = 25) -> list[PaidOrderLine]: ...
