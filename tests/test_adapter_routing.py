"""Task 3 verification: Zero 2026 deprecated eBay XML calls, zero raw credential reads,
media host separation (apiz.ebay.com), C2C vs Inventory routing, defusedxml XXE protection,
and 2026 Best Offer SellerAccept lifecycle parsing.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

import pytest
from defusedxml.common import DefusedXmlException

import hermes_ebay_ops.adapter.live as live_mod
from hermes_ebay_ops.adapter import EbayAdapter, LiveEbayAdapter, MockEbayAdapter
from hermes_ebay_ops.config import PluginSettings
from hermes_ebay_ops.models import (
    BestOfferLifecycleStatus,
    ConditionEnum,
    StagedListingPayload,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_2026_CALLS = (
    "UploadSiteHostedPictures",
    "GetCategories",
    "GetCategoryFeatures",
    "GetOrders",
)


class DummyProviderResponse:
    def __init__(
        self,
        status: int = 200,
        json_data: dict[str, Any] | None = None,
        xml_text: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        if json_data is not None:
            self.body = json.dumps(json_data).encode("utf-8")
        elif xml_text is not None:
            self.body = xml_text.encode("utf-8")
        else:
            self.body = b""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")

    def json(self) -> dict[str, Any]:
        return json.loads(self.text)


def _production_py_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*.py"):
        rel_parts = path.relative_to(REPO_ROOT).parts
        if rel_parts[0] in {"tests", ".venv", "build", "dist"}:
            continue
        files.append(path)
    assert files, "Expected production Python files to inspect"
    return sorted(files)


def test_zero_references_to_2026_decommissioned_xml_calls_and_raw_secrets() -> None:
    for py_path in _production_py_files():
        source = py_path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_2026_CALLS:
            assert forbidden not in source, f"Forbidden 2026 call {forbidden!r} found in {py_path.name}"

        tree = ast.parse(source, filename=str(py_path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name != "dotenv", f"dotenv forbidden in {py_path}"
                    assert not alias.name.startswith("xml.etree"), f"stdlib xml.etree forbidden in {py_path}"
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("dotenv"), f"dotenv forbidden in {py_path}"
                assert not node.module.startswith("xml.etree"), f"stdlib xml.etree forbidden in {py_path}"
            elif isinstance(node, ast.Attribute):
                assert node.attr not in {"environ", "getenv"}, f"Raw env access {node.attr!r} forbidden in {py_path}"


def test_mock_adapter_satisfies_protocol_for_lite_and_pro_personas() -> None:
    lite = MockEbayAdapter(persona="lite")
    pro = MockEbayAdapter(persona="pro")

    assert isinstance(lite, EbayAdapter)
    assert isinstance(pro, EbayAdapter)
    assert lite.check_policy_status().policies_opted_in is False
    assert pro.check_policy_status().policies_opted_in is True

    active = lite.fetch_recent_active_listings()
    assert active[0].created_via_magical_listing is True
    assert "Chest Size" not in active[0].aspects

    offers = pro.list_best_offers()
    statuses = {o.status for o in offers}
    assert BestOfferLifecycleStatus.ACTIVE in statuses
    assert BestOfferLifecycleStatus.SELLER_ACCEPT in statuses


def test_live_media_upload_routes_to_apiz_media_host_not_api_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    img_path = tmp_path / "front_photo.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0MOCKJPEGDATA\xff\xd9")

    captured: list[dict[str, Any]] = []

    def fake_broker(
        provider: str,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> DummyProviderResponse:
        captured.append(
            {
                "provider": provider,
                "method": method,
                "url": url,
                "body": body,
                "headers": headers or {},
            }
        )
        return DummyProviderResponse(
            status=201,
            json_data={"imageUrl": "https://i.ebayimg.com/images/g/live-eps-1/s-l1600.jpg"},
        )

    monkeypatch.setattr(live_mod, "credentialed_provider_request", fake_broker)
    adapter = LiveEbayAdapter(PluginSettings())
    urls = adapter.upload_media([str(img_path)])

    assert urls == ["https://i.ebayimg.com/images/g/live-eps-1/s-l1600.jpg"]
    assert len(captured) == 1
    req = captured[0]
    assert req["provider"] == "ebay"
    assert req["method"] == "POST"
    assert req["url"] == "https://apiz.ebay.com/commerce/media/v1_beta/image/create_image_from_file"
    assert "api.ebay.com" not in req["url"]
    assert req["headers"]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b"MOCKJPEGDATA" in req["body"]


def test_live_taxonomy_metadata_browse_and_fulfillment_rest_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_urls: list[tuple[str, str]] = []

    def fake_broker(
        provider: str,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> DummyProviderResponse:
        seen_urls.append((method, url))
        if "get_category_suggestions" in url:
            return DummyProviderResponse(
                json_data={"categorySuggestions": [{"category": {"categoryId": "57988"}}]}
            )
        if "get_item_aspects_for_category" in url:
            return DummyProviderResponse(
                json_data={
                    "aspects": [
                        {
                            "localizedAspectName": "Brand",
                            "aspectConstraint": {"aspectRequired": True, "aspectUsage": "REQUIRED"},
                        },
                        {
                            "localizedAspectName": "Chest Size",
                            "aspectConstraint": {"aspectRequired": False, "aspectUsage": "RECOMMENDED"},
                        },
                    ]
                }
            )
        if "get_item_condition_policies" in url:
            return DummyProviderResponse(json_data={"itemConditionPolicies": [{"categoryId": "57988"}]})
        if "/buy/browse/v1/item_summary/search" in url:
            return DummyProviderResponse(
                json_data={
                    "itemSummaries": [
                        {"itemId": "v1|1|0", "title": "A", "price": {"value": "50.00"}, "shippingOptions": [{"shippingCost": {"value": "8.00"}}]},
                        {"itemId": "v1|2|0", "title": "B", "price": {"value": "60.00"}, "shippingOptions": [{"shippingCost": {"value": "8.50"}}]},
                        {"itemId": "v1|3|0", "title": "C", "price": {"value": "70.00"}, "shippingOptions": [{"shippingCost": {"value": "9.00"}}]},
                    ]
                }
            )
        if "/sell/fulfillment/v1/order" in url:
            return DummyProviderResponse(
                json_data={
                    "orders": [
                        {
                            "orderId": "12-34567-89012",
                            "orderPaymentStatus": "PAID",
                            "creationDate": "2026-11-11T21:00:00Z",
                            "buyer": {"username": "vintage_buyer"},
                            "lineItems": [
                                {
                                    "lineItemId": "LI-1",
                                    "legacyItemId": "110599005678",
                                    "sku": "PRO-SKU-1",
                                    "title": "Vintage Jacket",
                                    "quantity": 1,
                                    "lineItemCost": {"value": "65.00"},
                                }
                            ],
                        },
                        {
                            "orderId": "12-34567-UNPAID",
                            "orderPaymentStatus": "PENDING",
                            "lineItems": [{"lineItemId": "LI-2", "legacyItemId": "999", "title": "Unpaid", "lineItemCost": {"value": "20.00"}}],
                        },
                    ]
                }
            )
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr(live_mod, "credentialed_provider_request", fake_broker)
    adapter = LiveEbayAdapter(PluginSettings())

    assert adapter.suggest_category("Patagonia Synchilla") == "57988"
    aspects = adapter.get_category_aspects("57988")
    assert [a.name for a in aspects] == ["Brand", "Chest Size"]

    comps = adapter.search_comps("Patagonia Synchilla", "57988", "USED_GOOD")
    assert comps.median_cents == 6000
    assert comps.typical_shipping_cents == 850

    orders = adapter.list_paid_orders()
    assert len(orders) == 1
    assert orders[0].sku == "PRO-SKU-1"
    assert orders[0].sold_price_cents == 6500

    endpoints_only = [u for _, u in seen_urls]
    assert any("/commerce/taxonomy/v1/" in u for u in endpoints_only)
    assert any("/sell/metadata/v1/" in u for u in endpoints_only)
    assert any("/buy/browse/v1/item_summary/search" in u for u in endpoints_only)
    assert any("/sell/fulfillment/v1/order" in u for u in endpoints_only)


def test_live_publish_routes_inventory_rest_vs_c2c_trading_xml_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, str]]] = []

    def fake_broker(
        provider: str,
        method: str,
        url: str,
        *,
        json: Any | None = None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> DummyProviderResponse:
        calls.append((method, url, headers or {}))
        if "/sell/inventory/v1/inventory_item/" in url:
            return DummyProviderResponse(status=204)
        if url.endswith("/sell/inventory/v1/offer"):
            return DummyProviderResponse(status=201, json_data={"offerId": "OFF-LIVE-777"})
        if url.endswith("/publish"):
            return DummyProviderResponse(status=200, json_data={"listingId": "110777888999"})
        if url.endswith("/ws/api.dll"):
            return DummyProviderResponse(
                status=200,
                xml_text=(
                    '<?xml version="1.0" encoding="utf-8"?>'
                    '<AddFixedPriceItemResponse xmlns="urn:ebay:apis:eBLBaseComponents">'
                    "<Ack>Success</Ack><ItemID>110111222333</ItemID>"
                    "</AddFixedPriceItemResponse>"
                ),
            )
        raise AssertionError(f"Unexpected call: {method} {url}")

    monkeypatch.setattr(live_mod, "credentialed_provider_request", fake_broker)
    adapter = LiveEbayAdapter(PluginSettings())

    # 1. Casual C2C fallback -> Trading XML AddFixedPriceItem
    c2c_payload = StagedListingPayload(
        sku="HRM-20261111-C2C1",
        title="Vintage Patagonia Fleece Mens L",
        description="Clean pre-owned fleece, pit to pit 23 in.",
        category_id="57988",
        condition=ConditionEnum.USED_GOOD,
        list_price_cents=6500,
        shipping_est_cents=850,
        aspects={"Brand": "Patagonia", "Size": "L"},
        media_urls=["https://i.ebayimg.com/images/g/1/s-l1600.jpg"],
        route_mode="TRADING_XML_FALLBACK",
    )
    c2c_receipt = adapter.publish_listing(c2c_payload)
    assert c2c_receipt.route_used == "TRADING_XML_FALLBACK"
    assert c2c_receipt.ebay_item_id == "110111222333"
    assert calls[-1][2]["X-EBAY-API-CALL-NAME"] == "AddFixedPriceItem"

    # 2. Pro Seller Hub -> REST Sell Inventory (createOrReplaceInventoryItem -> createOffer -> publishOffer)
    calls.clear()
    pro_payload = c2c_payload.model_copy(
        update={
            "sku": "PRO-PAT-001",
            "route_mode": "INVENTORY_REST",
            "fulfillment_policy_id": "FUL-1",
            "payment_policy_id": "PAY-1",
            "return_policy_id": "RET-1",
            "merchant_location_key": "WH-1",
        }
    )
    pro_receipt = adapter.publish_listing(pro_payload)
    assert pro_receipt.route_used == "INVENTORY_REST"
    assert pro_receipt.offer_id == "OFF-LIVE-777"
    assert pro_receipt.ebay_item_id == "110777888999"
    assert [m for m, _, _ in calls] == ["PUT", "POST", "POST"]


def test_live_get_best_offers_parses_2026_seller_accept_and_blocks_xxe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    best_offers_xml = """<?xml version="1.0" encoding="utf-8"?>
    <GetBestOffersResponse xmlns="urn:ebay:apis:eBLBaseComponents">
      <Ack>Success</Ack>
      <ItemBestOffersArray>
        <ItemBestOffers>
          <Item>
            <ItemID>110599005678</ItemID>
            <SKU>PRO-LEVI-501</SKU>
            <Title>Levi's 501 Selvedge</Title>
          </Item>
          <BestOfferArray>
            <BestOffer>
              <BestOfferID>BO-1</BestOfferID>
              <Status>Active</Status>
              <Price currencyID="USD">62.00</Price>
              <Quantity>1</Quantity>
              <Buyer><UserID>b***1</UserID></Buyer>
              <BuyerMessage>Ready to pay</BuyerMessage>
            </BestOffer>
            <BestOffer>
              <BestOfferID>BO-2</BestOfferID>
              <Status>SellerAccept</Status>
              <Price currencyID="USD">68.00</Price>
              <Quantity>1</Quantity>
              <Buyer><UserID>b***2</UserID></Buyer>
            </BestOffer>
            <BestOffer>
              <BestOfferID>BO-3</BestOfferID>
              <Status>Accepted</Status>
              <Price currencyID="USD">70.00</Price>
              <Quantity>1</Quantity>
              <Buyer><UserID>b***3</UserID></Buyer>
            </BestOffer>
          </BestOfferArray>
        </ItemBestOffers>
      </ItemBestOffersArray>
    </GetBestOffersResponse>
    """

    monkeypatch.setattr(
        live_mod,
        "credentialed_provider_request",
        lambda *args, **kwargs: DummyProviderResponse(status=200, xml_text=best_offers_xml),
    )
    adapter = LiveEbayAdapter(PluginSettings())
    offers = adapter.list_best_offers()

    by_id = {o.best_offer_id: o for o in offers}
    assert by_id["BO-1"].status == BestOfferLifecycleStatus.ACTIVE
    assert by_id["BO-2"].status == BestOfferLifecycleStatus.SELLER_ACCEPT
    assert by_id["BO-3"].status == BestOfferLifecycleStatus.ACCEPTED

    # Malicious XXE payload must be rejected by defusedxml
    xxe_xml = """<?xml version="1.0" encoding="utf-8"?>
    <!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
    <GetBestOffersResponse><Ack>&xxe;</Ack></GetBestOffersResponse>
    """
    monkeypatch.setattr(
        live_mod,
        "credentialed_provider_request",
        lambda *args, **kwargs: DummyProviderResponse(status=200, xml_text=xxe_xml),
    )
    with pytest.raises(DefusedXmlException):
        adapter.list_best_offers()
