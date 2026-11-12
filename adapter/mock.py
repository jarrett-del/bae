"""Deterministic offline fixtures for Lite C2C and Pro Seller Hub accounts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from ..cache_store import get_cached_category_aspects, put_cached_category_aspects
from ..models import (
    AccountCapabilities,
    ActiveListingSnapshot,
    BestOfferLifecycleStatus,
    BestOfferRecord,
    CategoryAspectSpec,
    CompSample,
    CompSummary,
    ConditionEnum,
    OfferActionReceipt,
    OfferDecisionAction,
    PaidOrderLine,
    PublishReceipt,
    RespondOfferPayload,
    StagedListingPayload,
    utc_now_iso,
)


class MockEbayAdapter:
    """Offline EbayAdapter implementation recording mutating calls for gate tests."""

    def __init__(self, persona: Literal["lite", "pro"] = "lite") -> None:
        self.persona: Literal["lite", "pro"] = persona
        self.write_calls: list[tuple[str, dict[str, Any]]] = []
        self._policies_opted_in: bool = persona == "pro"
        self._next_item_seq: int = 2000

        self._active_listings: list[ActiveListingSnapshot] = [
            ActiveListingSnapshot(
                ebay_item_id="110599001234",
                sku=None,
                offer_id=None,
                title="Patagonia Synchilla Snap-T Fleece Pullover Mens Large Navy",
                description=(
                    "Elevate your wardrobe with this stunning, timeless must-have Patagonia fleece! "
                    "Look no further for effortless style and adventure-ready comfort."
                ),
                category_id="57988",
                condition=ConditionEnum.USED_GOOD,
                list_price_cents=8900,
                shipping_est_cents=895,
                aspects={
                    "Brand": "Patagonia",
                    "Size Type": "Regular",
                    "Size": "L",
                    "Department": "Men",
                    "Type": "Coat",
                },
                media_urls=["https://i.ebayimg.com/images/g/mock-synchilla-1/s-l1600.jpg"],
                created_via_magical_listing=True,
                listed_at="2026-11-11T18:30:00Z",
            ),
            ActiveListingSnapshot(
                ebay_item_id="110599005678",
                sku="PRO-LEVI-501-32X30",
                offer_id="OFF-9001",
                title="Levi's 501 Original Shrink-to-Fit Selvedge Jeans Mens 32x30 Indigo",
                description=(
                    "Pre-owned Levi's 501 selvedge denim. Measured flat: waist 16 in, inseam 30 in, "
                    "front rise 11.25 in, leg opening 8 in. Light heel drag on left cuff."
                ),
                category_id="11483",
                condition=ConditionEnum.USED_EXCELLENT,
                list_price_cents=7500,
                shipping_est_cents=795,
                aspects={
                    "Brand": "Levi's",
                    "Size Type": "Regular",
                    "Size": "32",
                    "Inseam": "30 in",
                    "Department": "Men",
                    "Style": "Straight",
                },
                media_urls=["https://i.ebayimg.com/images/g/mock-levis-1/s-l1600.jpg"],
                created_via_magical_listing=False,
                listed_at="2026-11-10T15:00:00Z",
            ),
        ]

        self._best_offers: list[BestOfferRecord] = [
            BestOfferRecord(
                best_offer_id="BO-ACTIVE-101",
                ebay_item_id="110599005678",
                sku="PRO-LEVI-501-32X30",
                title="Levi's 501 Original Shrink-to-Fit Selvedge Jeans Mens 32x30 Indigo",
                buyer_masked_id="d***m",
                offer_price_cents=6200,
                quantity=1,
                status=BestOfferLifecycleStatus.ACTIVE,
                buyer_message="Can pay right away if accepted!",
                expires_at="2026-11-13T18:00:00Z",
            ),
            BestOfferRecord(
                best_offer_id="BO-LOW-102",
                ebay_item_id="110599005678",
                sku="PRO-LEVI-501-32X30",
                title="Levi's 501 Original Shrink-to-Fit Selvedge Jeans Mens 32x30 Indigo",
                buyer_masked_id="v***k",
                offer_price_cents=3800,
                quantity=1,
                status=BestOfferLifecycleStatus.ACTIVE,
                buyer_message="Would you take 38?",
                expires_at="2026-11-13T19:00:00Z",
            ),
            BestOfferRecord(
                best_offer_id="BO-SELLERACCEPT-103",
                ebay_item_id="110599001234",
                sku=None,
                title="Patagonia Synchilla Snap-T Fleece Pullover Mens Large Navy",
                buyer_masked_id="p***r",
                offer_price_cents=7200,
                quantity=1,
                status=BestOfferLifecycleStatus.SELLER_ACCEPT,
                buyer_message="Thanks!",
                expires_at="2026-11-15T18:00:00Z",
            ),
        ]

        self._paid_orders: list[PaidOrderLine] = [
            PaidOrderLine(
                order_id="19-11402-88001",
                line_item_id="10044001",
                ebay_item_id="110599009901",
                sku="PRO-SHOE-NB990",
                title="New Balance 990v5 Grey Suede Running Shoes Mens 10.5 D",
                quantity=1,
                sold_price_cents=11500,
                buyer_username="runner_dan",
                paid_at="2026-11-11T19:12:00Z",
            ),
            PaidOrderLine(
                order_id="19-11402-88002",
                line_item_id="10044002",
                ebay_item_id="110599005678",
                sku="PRO-LEVI-501-32X30",
                title="Levi's 501 Original Shrink-to-Fit Selvedge Jeans Mens 32x30 Indigo",
                quantity=1,
                sold_price_cents=7500,
                buyer_username="denim_collector",
                paid_at="2026-11-11T20:05:00Z",
            ),
            PaidOrderLine(
                order_id="19-11402-88003",
                line_item_id="10044003",
                ebay_item_id="110599001234",
                sku=None,
                title="Patagonia Synchilla Snap-T Fleece Pullover Mens Large Navy",
                quantity=1,
                sold_price_cents=8900,
                buyer_username="trail_hiker",
                paid_at="2026-11-11T21:15:00Z",
            ),
        ]

    def check_policy_status(self, marketplace_id: str = "EBAY_US") -> AccountCapabilities:
        if self._policies_opted_in:
            return AccountCapabilities(
                marketplace_id=marketplace_id,
                policies_opted_in=True,
                fulfillment_policy_id="FUL-POL-100",
                payment_policy_id="PAY-POL-100",
                return_policy_id="RET-POL-100",
                merchant_location_key="MAIN_WAREHOUSE",
            )
        return AccountCapabilities(
            marketplace_id=marketplace_id,
            policies_opted_in=False,
        )

    def bootstrap_policies(self, marketplace_id: str = "EBAY_US") -> AccountCapabilities:
        self.write_calls.append(("bootstrap_policies", {"marketplace_id": marketplace_id}))
        self._policies_opted_in = True
        return self.check_policy_status(marketplace_id)

    def upload_media(self, file_paths: list[str]) -> list[str]:
        self.write_calls.append(("upload_media", {"file_paths": list(file_paths)}))
        urls: list[str] = []
        for idx, raw_path in enumerate(file_paths, start=1):
            if raw_path.startswith(("https://", "http://")):
                urls.append(raw_path)
            else:
                stem = Path(raw_path).stem or f"img-{idx}"
                urls.append(f"https://i.ebayimg.com/images/g/mock-{stem}-{idx}/s-l1600.jpg")
        return urls

    def suggest_category(self, query: str, marketplace_id: str = "EBAY_US") -> str:
        lowered = query.lower()
        if any(word in lowered for word in ("jean", "denim", "501", "trouser", "pant")):
            return "11483"
        if any(word in lowered for word in ("shoe", "sneaker", "boot", "990", "jordan")):
            return "15709"
        if any(word in lowered for word in ("camera", "lens", "sony", "nikon", "canon")):
            return "31388"
        return "57988"

    def get_category_aspects(
        self, category_id: str, marketplace_id: str = "EBAY_US"
    ) -> list[CategoryAspectSpec]:
        cached = get_cached_category_aspects(category_id, marketplace_id)
        if cached is not None:
            return cached
        if category_id == "11483":
            specs = [
                CategoryAspectSpec(name="Brand", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="Size Type", required=True, usage="REQUIRED", allowed_values=["Regular", "Big & Tall"]),
                CategoryAspectSpec(name="Size", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="Inseam", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="Department", required=True, usage="REQUIRED", allowed_values=["Men", "Women", "Unisex Adults"]),
                CategoryAspectSpec(name="Style", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="Waist Size", required=False, usage="RECOMMENDED"),
            ]
        elif category_id == "15709":
            specs = [
                CategoryAspectSpec(name="Brand", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="US Shoe Size", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="Department", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="Style Code", required=False, usage="RECOMMENDED"),
                CategoryAspectSpec(name="Shoe Width", required=False, usage="RECOMMENDED"),
            ]
        elif category_id in {"6030", "33615"}:
            specs = [
                CategoryAspectSpec(name="Brand", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="MPN", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="Placement on Vehicle", required=False, usage="RECOMMENDED"),
                CategoryAspectSpec(name="Compatible Vehicles", required=False, usage="RECOMMENDED"),
            ]
        else:
            specs = [
                CategoryAspectSpec(name="Brand", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="Size Type", required=True, usage="REQUIRED", allowed_values=["Regular", "Big & Tall"]),
                CategoryAspectSpec(name="Size", required=True, usage="REQUIRED"),
                CategoryAspectSpec(name="Department", required=True, usage="REQUIRED", allowed_values=["Men", "Women", "Unisex Adults"]),
                CategoryAspectSpec(name="Type", required=True, usage="REQUIRED", allowed_values=["Jacket", "Coat", "Vest"]),
                CategoryAspectSpec(name="Chest Size", required=False, usage="RECOMMENDED"),
                CategoryAspectSpec(name="Garment Length", required=False, usage="RECOMMENDED"),
                CategoryAspectSpec(name="Outer Shell Material", required=False, usage="RECOMMENDED"),
            ]
        put_cached_category_aspects(category_id, marketplace_id, specs)
        return specs

    def search_comps(
        self, query: str, category_id: str | None, condition: str | None
    ) -> CompSummary:
        safe_q = " ".join(query.split())[:100]
        samples = [
            CompSample(
                item_id="220001",
                title=f"{safe_q[:55]} - Comp A",
                price_cents=5200,
                shipping_cents=795,
                condition=condition or "USED_GOOD",
            ),
            CompSample(
                item_id="220002",
                title=f"{safe_q[:55]} - Comp B",
                price_cents=5900,
                shipping_cents=845,
                condition=condition or "USED_GOOD",
            ),
            CompSample(
                item_id="220003",
                title=f"{safe_q[:55]} - Comp C",
                price_cents=6500,
                shipping_cents=850,
                condition=condition or "USED_GOOD",
            ),
            CompSample(
                item_id="220004",
                title=f"{safe_q[:55]} - Comp D",
                price_cents=7200,
                shipping_cents=895,
                condition=condition or "USED_GOOD",
            ),
            CompSample(
                item_id="220005",
                title=f"{safe_q[:55]} - Comp E",
                price_cents=8100,
                shipping_cents=950,
                condition=condition or "USED_GOOD",
            ),
        ]
        return CompSummary(
            query=safe_q,
            category_id=category_id,
            condition=condition,
            sample_count=len(samples),
            p25_cents=5900,
            median_cents=6500,
            p75_cents=7200,
            typical_shipping_cents=850,
            samples=samples,
        )

    def search_comps_by_image(
        self, image_b64: str, category_id: str | None = None, condition: str | None = None
    ) -> CompSummary:
        return self.search_comps("Visual Image Match", category_id, condition)

    def fetch_recent_active_listings(self, limit: int = 10) -> list[ActiveListingSnapshot]:
        return list(self._active_listings[:limit])

    def publish_listing(self, payload: StagedListingPayload) -> PublishReceipt:
        self.write_calls.append(("publish_listing", payload.model_dump(mode="json")))
        self._next_item_seq += 1
        ebay_item_id = f"11059999{self._next_item_seq}"
        use_inventory = self._policies_opted_in or payload.route_mode == "INVENTORY_REST"
        offer_id = f"OFF-{self._next_item_seq}" if use_inventory else None
        route_used: Literal["INVENTORY_REST", "TRADING_XML_FALLBACK"] = (
            "INVENTORY_REST" if use_inventory else "TRADING_XML_FALLBACK"
        )
        return PublishReceipt(
            sku=payload.sku,
            ebay_item_id=ebay_item_id,
            offer_id=offer_id,
            route_used=route_used,
            timestamp_utc=utc_now_iso(),
        )

    def bulk_publish_listings(self, payloads: list[StagedListingPayload]) -> list[PublishReceipt]:
        self.write_calls.append(
            ("bulk_publish_listings", {"count": len(payloads), "skus": [p.sku for p in payloads]})
        )
        receipts: list[PublishReceipt] = []
        for payload in payloads[:25]:
            self._next_item_seq += 1
            ebay_item_id = f"11059999{self._next_item_seq}"
            use_inventory = self._policies_opted_in or payload.route_mode == "INVENTORY_REST"
            offer_id = f"OFF-{self._next_item_seq}" if use_inventory else None
            route_used: Literal["INVENTORY_REST", "TRADING_XML_FALLBACK"] = (
                "INVENTORY_REST" if use_inventory else "TRADING_XML_FALLBACK"
            )
            receipts.append(
                PublishReceipt(
                    sku=payload.sku,
                    ebay_item_id=ebay_item_id,
                    offer_id=offer_id,
                    route_used=route_used,
                    timestamp_utc=utc_now_iso(),
                )
            )
        return receipts

    def revise_listing(
        self, sku: str, ebay_item_id: str, changes: dict[str, Any]
    ) -> PublishReceipt:
        self.write_calls.append(
            ("revise_listing", {"sku": sku, "ebay_item_id": ebay_item_id, "changes": dict(changes)})
        )
        use_inventory = self._policies_opted_in or bool(changes.get("offer_id"))
        return PublishReceipt(
            sku=sku,
            ebay_item_id=ebay_item_id,
            offer_id=changes.get("offer_id"),
            route_used="INVENTORY_REST" if use_inventory else "TRADING_XML_FALLBACK",
            timestamp_utc=utc_now_iso(),
        )

    def list_best_offers(self, ebay_item_id: str | None = None) -> list[BestOfferRecord]:
        if ebay_item_id is None:
            return list(self._best_offers)
        return [offer for offer in self._best_offers if offer.ebay_item_id == ebay_item_id]

    def respond_to_offer(self, payload: RespondOfferPayload) -> OfferActionReceipt:
        self.write_calls.append(("respond_to_offer", payload.model_dump(mode="json")))
        status_map = {
            OfferDecisionAction.ACCEPT: BestOfferLifecycleStatus.SELLER_ACCEPT,
            OfferDecisionAction.COUNTER: BestOfferLifecycleStatus.COUNTERED,
            OfferDecisionAction.DECLINE: BestOfferLifecycleStatus.DECLINED,
        }
        return OfferActionReceipt(
            ebay_item_id=payload.ebay_item_id,
            best_offer_id=payload.best_offer_id,
            action=payload.action,
            resulting_status=status_map[payload.action],
            timestamp_utc=utc_now_iso(),
        )

    def list_paid_orders(self, limit: int = 25) -> list[PaidOrderLine]:
        return list(self._paid_orders[:limit])
