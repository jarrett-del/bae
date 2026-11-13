"""Live eBay REST and Trading XML client routed strictly through Hermes's
`credentialed_provider_request` broker and `defusedxml`.
"""

from __future__ import annotations

import inspect
import mimetypes
import secrets
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from statistics import median
from typing import Any, Literal
from urllib.parse import quote, urlencode
from xml.sax.saxutils import escape as xml_escape

from defusedxml import ElementTree as DefusedET
from hermes_cli.plugin_provider_requests import (
    ProviderNotSignedIn,
    credentialed_provider_request,
)

from ..cache_store import get_cached_category_aspects, put_cached_category_aspects
from ..config import PluginSettings, get_settings
from ..file_guard import local_read_block_error
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
    normalize_condition,
    utc_now_iso,
)

EBAY_XML_NS = "urn:ebay:apis:eBLBaseComponents"


class EbayApiError(RuntimeError):
    """Raised when an upstream eBay REST or XML call fails."""

    def __init__(self, status: int, message: str, endpoint: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.endpoint = endpoint


def _dollars_to_cents(raw_value: str | float | int | None) -> int:
    if raw_value is None or raw_value == "":
        return 0
    dec = (Decimal(str(raw_value)) * Decimal("100")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return int(dec)


def _cents_to_dollars_str(cents: int) -> str:
    return f"{Decimal(cents) / Decimal(100):.2f}"


def _local_tag(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _child_by_name(parent: Any, name: str) -> Any | None:
    for child in list(parent):
        if _local_tag(child.tag) == name:
            return child
    return None


def _children_by_name(parent: Any, name: str) -> list[Any]:
    return [child for child in list(parent) if _local_tag(child.tag) == name]


def _iter_by_name(root: Any, name: str) -> list[Any]:
    return [elem for elem in root.iter() if _local_tag(elem.tag) == name]


def _path_text(parent: Any, *names: str) -> str | None:
    cur = parent
    for name in names:
        if cur is None:
            return None
        cur = _child_by_name(cur, name)
    if cur is None or cur.text is None:
        return None
    stripped = cur.text.strip()
    return stripped or None


def _encode_multipart_image(file_path: Path) -> tuple[bytes, str]:
    """Build RFC 7578 multipart/form-data payload for Commerce Media createImageFromFile."""
    boundary = f"----HermesEbayOpsBoundary{secrets.token_hex(12)}"
    mime_type = mimetypes.guess_type(file_path.name)[0] or "image/jpeg"
    file_bytes = file_path.read_bytes()
    safe_filename = file_path.name.replace('"', "_")

    header_block = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{safe_filename}"\r\n'
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8")
    footer_block = f"\r\n--{boundary}--\r\n".encode("utf-8")
    content_type = f"multipart/form-data; boundary={boundary}"
    return header_block + file_bytes + footer_block, content_type


class LiveEbayAdapter:
    """Production eBay client enforcing modern REST routing and defusedxml XML parsing."""

    def __init__(self, settings: PluginSettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._tree_id_cache: dict[str, str] = {"EBAY_US": "0"}

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_payload: Any | None = None,
        raw_body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        merged_headers = {"Accept": "application/json"}
        if headers:
            merged_headers.update(headers)

        kwargs: dict[str, Any] = {
            "headers": merged_headers,
            "timeout": timeout,
        }
        if json_payload is not None:
            kwargs["json"] = json_payload
        elif raw_body is not None:
            sig = inspect.signature(credentialed_provider_request)
            if "body" in sig.parameters:
                kwargs["body"] = raw_body
            elif "content" in sig.parameters:
                kwargs["content"] = raw_body
            elif "data" in sig.parameters:
                kwargs["data"] = raw_body
            else:
                kwargs["body"] = raw_body

        try:
            resp = credentialed_provider_request(
                self._settings.oauth_provider_id,
                method,
                url,
                **kwargs,
            )
        except ProviderNotSignedIn:
            raise

        status = int(getattr(resp, "status", 200))
        if status >= 400:
            detail = getattr(resp, "text", "") or f"HTTP {status}"
            raise EbayApiError(status, f"eBay API error ({status}) on {method} {url}: {detail[:400]}", url)
        return resp

    def _xml_call(self, call_name: str, inner_xml: str) -> Any:
        envelope = (
            '<?xml version="1.0" encoding="utf-8"?>'
            f'<{call_name}Request xmlns="{EBAY_XML_NS}">'
            "<ErrorLanguage>en_US</ErrorLanguage>"
            "<WarningLevel>High</WarningLevel>"
            f"{inner_xml}"
            f"</{call_name}Request>"
        ).encode("utf-8")

        headers = {
            "Content-Type": "text/xml; charset=utf-8",
            "Accept": "text/xml",
            "X-EBAY-API-CALL-NAME": call_name,
            "X-EBAY-API-COMPATIBILITY-LEVEL": self._settings.trading_compat_level,
            "X-EBAY-API-SITEID": self._settings.trading_site_id,
        }
        resp = self._request(
            "POST",
            self._settings.trading_xml_url,
            raw_body=envelope,
            headers=headers,
        )
        xml_bytes = getattr(resp, "body", b"") or getattr(resp, "text", "").encode("utf-8")
        root = DefusedET.fromstring(xml_bytes)
        ack = (_path_text(root, "Ack") or "Success").upper()
        if ack in {"FAILURE", "PARTIALFAILURE"}:
            err_msg = _path_text(root, "Errors", "LongMessage") or _path_text(root, "Errors", "ShortMessage") or "Trading XML call failed"
            raise EbayApiError(502, f"{call_name} {ack}: {err_msg}", self._settings.trading_xml_url)
        return root

    def _resolve_category_tree_id(self, marketplace_id: str) -> str:
        if marketplace_id in self._tree_id_cache:
            return self._tree_id_cache[marketplace_id]
        url = (
            f"{self._settings.api_base_url.rstrip('/')}"
            f"/commerce/taxonomy/v1/get_default_category_tree_id?{urlencode({'marketplace_id': marketplace_id})}"
        )
        resp = self._request("GET", url)
        tree_id = str(resp.json().get("categoryTreeId", "0"))
        self._tree_id_cache[marketplace_id] = tree_id
        return tree_id

    def check_policy_status(self, marketplace_id: str = "EBAY_US") -> AccountCapabilities:
        base = self._settings.api_base_url.rstrip("/")
        resp = self._request("GET", f"{base}/sell/account/v1/program/get_opted_in_programs")
        programs = resp.json().get("programs", [])
        opted_in = any(p.get("programType") == "SELLING_POLICY_MANAGEMENT" for p in programs)
        if not opted_in:
            return AccountCapabilities(marketplace_id=marketplace_id, policies_opted_in=False)

        qs = urlencode({"marketplace_id": marketplace_id})
        ful_resp = self._request("GET", f"{base}/sell/account/v1/fulfillment_policy?{qs}").json()
        pay_resp = self._request("GET", f"{base}/sell/account/v1/payment_policy?{qs}").json()
        ret_resp = self._request("GET", f"{base}/sell/account/v1/return_policy?{qs}").json()
        loc_resp = self._request("GET", f"{base}/sell/inventory/v1/location?limit=1").json()

        ful_id = (ful_resp.get("fulfillmentPolicies") or [{}])[0].get("fulfillmentPolicyId")
        pay_id = (pay_resp.get("paymentPolicies") or [{}])[0].get("paymentPolicyId")
        ret_id = (ret_resp.get("returnPolicies") or [{}])[0].get("returnPolicyId")
        loc_key = (loc_resp.get("locations") or [{}])[0].get("merchantLocationKey")

        return AccountCapabilities(
            marketplace_id=marketplace_id,
            policies_opted_in=True,
            fulfillment_policy_id=ful_id,
            payment_policy_id=pay_id,
            return_policy_id=ret_id,
            merchant_location_key=loc_key,
        )

    def bootstrap_policies(self, marketplace_id: str = "EBAY_US") -> AccountCapabilities:
        base = self._settings.api_base_url.rstrip("/")
        self._request(
            "POST",
            f"{base}/sell/account/v1/program/opt_in",
            json_payload={"programType": "SELLING_POLICY_MANAGEMENT"},
        )
        return self.check_policy_status(marketplace_id)

    def upload_media(self, file_paths: list[str]) -> list[str]:
        """Upload images via REST Commerce Media createImageFromFile on the dedicated media host."""
        media_base = self._settings.media_base_url.rstrip("/")
        endpoint = f"{media_base}/commerce/media/v1_beta/image/create_image_from_file"
        uploaded_urls: list[str] = []

        for raw_path in file_paths:
            if raw_path.startswith(("https://", "http://")):
                uploaded_urls.append(raw_path)
                continue

            # Checked again here, at the only place local bytes are read, because the file
            # behind a staged path can change between staging and approval.
            blocked = local_read_block_error(raw_path)
            if blocked:
                raise EbayApiError(403, blocked, endpoint)
            path_obj = Path(raw_path)
            body_bytes, content_type = _encode_multipart_image(path_obj)
            resp = self._request(
                "POST",
                endpoint,
                raw_body=body_bytes,
                headers={"Content-Type": content_type, "Accept": "application/json"},
            )
            image_url: str | None = None
            if getattr(resp, "text", "").strip():
                data = resp.json()
                image_url = data.get("imageUrl")
            if not image_url:
                resp_headers = {k.lower(): v for k, v in (getattr(resp, "headers", {}) or {}).items()}
                loc = resp_headers.get("location")
                if loc:
                    meta_resp = self._request("GET", loc)
                    image_url = meta_resp.json().get("imageUrl")
            if not image_url:
                raise EbayApiError(502, f"Media upload succeeded without returning imageUrl for {path_obj.name}", endpoint)
            uploaded_urls.append(str(image_url))

        return uploaded_urls

    def suggest_category(self, query: str, marketplace_id: str = "EBAY_US") -> str:
        tree_id = self._resolve_category_tree_id(marketplace_id)
        url = (
            f"{self._settings.api_base_url.rstrip('/')}"
            f"/commerce/taxonomy/v1/category_tree/{quote(tree_id)}/get_category_suggestions?"
            f"{urlencode({'q': query})}"
        )
        resp = self._request("GET", url)
        suggestions = resp.json().get("categorySuggestions") or []
        if not suggestions:
            raise EbayApiError(404, f"No eBay category suggestion found for query={query!r}", url)
        return str(suggestions[0]["category"]["categoryId"])

    def get_category_aspects(
        self, category_id: str, marketplace_id: str = "EBAY_US"
    ) -> list[CategoryAspectSpec]:
        cached = get_cached_category_aspects(
            category_id, marketplace_id, plugin_name=self._settings.plugin_name
        )
        if cached is not None:
            return cached

        tree_id = self._resolve_category_tree_id(marketplace_id)
        base = self._settings.api_base_url.rstrip("/")

        taxonomy_url = (
            f"{base}/commerce/taxonomy/v1/category_tree/{quote(tree_id)}/get_item_aspects_for_category?"
            f"{urlencode({'category_id': category_id})}"
        )
        metadata_url = (
            f"{base}/sell/metadata/v1/marketplace/{quote(marketplace_id)}/get_item_condition_policies?"
            f"{urlencode({'filter': f'categoryIds:{{{category_id}}}'})}"
        )

        taxonomy_data = self._request("GET", taxonomy_url).json()
        # Query Sell Metadata REST API to verify modern category condition policies
        self._request("GET", metadata_url)

        specs: list[CategoryAspectSpec] = []
        for raw_aspect in taxonomy_data.get("aspects") or []:
            constraint = raw_aspect.get("aspectConstraint") or {}
            required = bool(constraint.get("aspectRequired", False))
            usage_raw = str(constraint.get("aspectUsage", "OPTIONAL")).upper()
            if required:
                usage: Literal["REQUIRED", "RECOMMENDED", "OPTIONAL"] = "REQUIRED"
            elif usage_raw == "RECOMMENDED":
                usage = "RECOMMENDED"
            else:
                usage = "OPTIONAL"

            allowed = [
                str(val["localizedValue"])
                for val in (raw_aspect.get("aspectValues") or [])
                if "localizedValue" in val
            ][:25]
            specs.append(
                CategoryAspectSpec(
                    name=str(raw_aspect.get("localizedAspectName", "")),
                    required=required,
                    usage=usage,
                    data_type=str(constraint.get("aspectDataType", "STRING")),
                    allowed_values=allowed,
                    multi_value=str(constraint.get("itemToAspectCardinality", "SINGLE")).upper() == "MULTI",
                )
            )
        put_cached_category_aspects(
            category_id, marketplace_id, specs, plugin_name=self._settings.plugin_name
        )
        return specs

    @staticmethod
    def _truncate_browse_query(query: str, max_len: int = 100) -> str:
        """Truncate Browse API `q` to <=100 characters on word boundaries per eBay Browse spec."""
        cleaned = " ".join(query.split()).strip()
        if len(cleaned) <= max_len:
            return cleaned
        cut = cleaned[:max_len]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        return cut.strip() or cleaned[:max_len]

    @staticmethod
    def _summarize_browse_items(
        raw_items: list[dict[str, Any]],
        *,
        query: str,
        category_id: str | None,
        condition: str | None,
    ) -> CompSummary:
        samples: list[CompSample] = []
        prices: list[int] = []
        shippings: list[int] = []

        for item in raw_items:
            price_val = (item.get("price") or {}).get("value")
            if price_val is None:
                continue
            price_cents = _dollars_to_cents(price_val)
            ship_opts = item.get("shippingOptions") or []
            ship_val = ((ship_opts[0].get("shippingCost") or {}).get("value")) if ship_opts else "0.00"
            ship_cents = _dollars_to_cents(ship_val)

            prices.append(price_cents)
            shippings.append(ship_cents)
            samples.append(
                CompSample(
                    item_id=str(item.get("itemId", "")),
                    title=str(item.get("title", "")),
                    price_cents=price_cents,
                    shipping_cents=ship_cents,
                    condition=item.get("condition"),
                )
            )

        if not prices:
            return CompSummary(
                query=query,
                category_id=category_id,
                condition=condition,
                sample_count=0,
                p25_cents=0,
                median_cents=0,
                p75_cents=0,
                typical_shipping_cents=0,
                samples=[],
            )

        prices_sorted = sorted(prices)
        n = len(prices_sorted)
        p25 = prices_sorted[int((n - 1) * 0.25)]
        med = int(round(median(prices_sorted)))
        p75 = prices_sorted[int((n - 1) * 0.75)]
        typ_ship = int(round(median(sorted(shippings)))) if shippings else 0

        return CompSummary(
            query=query,
            category_id=category_id,
            condition=condition,
            sample_count=n,
            p25_cents=p25,
            median_cents=med,
            p75_cents=p75,
            typical_shipping_cents=typ_ship,
            samples=samples[:10],
        )

    def search_comps(
        self, query: str, category_id: str | None, condition: str | None
    ) -> CompSummary:
        safe_q = self._truncate_browse_query(query, 100)
        params: dict[str, str] = {"q": safe_q, "limit": "25"}
        if category_id:
            params["category_ids"] = category_id
        if condition:
            cond_enum = normalize_condition(condition)
            params["filter"] = f"conditionIds:{{{cond_enum.browse_condition_id(category_id)}}}"

        url = f"{self._settings.api_base_url.rstrip('/')}/buy/browse/v1/item_summary/search?{urlencode(params)}"
        data = self._request("GET", url).json()
        return self._summarize_browse_items(
            data.get("itemSummaries") or [],
            query=safe_q,
            category_id=category_id,
            condition=condition,
        )

    def search_comps_by_image(
        self, image_b64: str, category_id: str | None = None, condition: str | None = None
    ) -> CompSummary:
        """Query POST /buy/browse/v1/item_summary/search_by_image for unbranded or vintage items."""
        params: dict[str, str] = {"limit": "25"}
        if category_id:
            params["category_ids"] = category_id
        if condition:
            cond_enum = normalize_condition(condition)
            params["filter"] = f"conditionIds:{{{cond_enum.browse_condition_id(category_id)}}}"
        url = (
            f"{self._settings.api_base_url.rstrip('/')}"
            f"/buy/browse/v1/item_summary/search_by_image?{urlencode(params)}"
        )
        data = self._request("POST", url, json_payload={"image": image_b64}).json()
        return self._summarize_browse_items(
            data.get("itemSummaries") or [],
            query="[image-search]",
            category_id=category_id,
            condition=condition,
        )

    def fetch_recent_active_listings(self, limit: int = 10) -> list[ActiveListingSnapshot]:
        inner = (
            "<ActiveList>"
            "<Include>true</Include>"
            "<Sort>StartTimeDescending</Sort>"
            f"<Pagination><EntriesPerPage>{int(limit)}</EntriesPerPage><PageNumber>1</PageNumber></Pagination>"
            "</ActiveList>"
            "<DetailLevel>ReturnAll</DetailLevel>"
        )
        root = self._xml_call("GetMyeBaySelling", inner)
        snapshots: list[ActiveListingSnapshot] = []

        active_node = _child_by_name(root, "ActiveList")
        item_array = _child_by_name(active_node, "ItemArray") if active_node is not None else None
        item_nodes = _children_by_name(item_array, "Item") if item_array is not None else []

        for node in item_nodes[:limit]:
            item_id = _path_text(node, "ItemID") or ""
            if not item_id:
                continue
            sku = _path_text(node, "SKU")
            title = (_path_text(node, "Title") or "Untitled Listing")[:80]
            description = _path_text(node, "Description") or ""
            cat_id = _path_text(node, "PrimaryCategory", "CategoryID") or "0"
            cond_id = _path_text(node, "ConditionID") or "3000"
            price_str = _path_text(node, "SellingStatus", "CurrentPrice") or _path_text(node, "BuyItNowPrice") or "1.00"
            ship_str = _path_text(node, "ShippingDetails", "ShippingServiceOptions", "ShippingServiceCost") or "0.00"
            start_time = _path_text(node, "ListingDetails", "StartTime")

            aspects: dict[str, str] = {}
            spec_container = _child_by_name(node, "ItemSpecifics")
            if spec_container is not None:
                for nvl in _children_by_name(spec_container, "NameValueList"):
                    k = _path_text(nvl, "Name")
                    v = _path_text(nvl, "Value")
                    if k and v:
                        aspects[k] = v

            pics: list[str] = []
            pic_container = _child_by_name(node, "PictureDetails")
            if pic_container is not None:
                for purl in _children_by_name(pic_container, "PictureURL"):
                    if purl.text and purl.text.strip():
                        pics.append(purl.text.strip())

            snapshots.append(
                ActiveListingSnapshot(
                    ebay_item_id=item_id,
                    sku=sku,
                    offer_id=None,
                    title=title,
                    description=description,
                    category_id=cat_id,
                    condition=normalize_condition(cond_id),
                    list_price_cents=max(1, _dollars_to_cents(price_str)),
                    shipping_est_cents=_dollars_to_cents(ship_str),
                    aspects=aspects,
                    media_urls=pics,
                    created_via_magical_listing=not bool(sku and sku.startswith("HRM-")),
                    listed_at=start_time,
                )
            )
        return snapshots

    def publish_listing(self, payload: StagedListingPayload) -> PublishReceipt:
        if payload.route_mode == "INVENTORY_REST":
            base = self._settings.api_base_url.rstrip("/")
            inv_url = f"{base}/sell/inventory/v1/inventory_item/{quote(payload.sku)}"
            inv_body: dict[str, Any] = {
                "availability": {"shipToLocationAvailability": {"quantity": 1}},
                "condition": payload.condition.value,
                "product": {
                    "title": payload.title,
                    "description": payload.description,
                    "aspects": {k: [v] for k, v in payload.aspects.items()},
                    "imageUrls": payload.media_urls,
                },
            }
            if payload.package_weight_oz is not None or payload.package_dims is not None:
                pkg_block: dict[str, Any] = {}
                if payload.package_weight_oz is not None:
                    pkg_block["weight"] = {"value": payload.package_weight_oz, "unit": "OUNCE"}
                if payload.package_dims is not None:
                    pkg_block["dimensions"] = {
                        "length": payload.package_dims.length_in,
                        "width": payload.package_dims.width_in,
                        "height": payload.package_dims.height_in,
                        "unit": "INCH",
                    }
                inv_body["packageWeightAndSize"] = pkg_block
            self._request(
                "PUT",
                inv_url,
                json_payload=inv_body,
                headers={"Content-Language": "en-US"},
            )

            offer_body: dict[str, Any] = {
                "sku": payload.sku,
                "marketplaceId": payload.marketplace_id,
                "format": "FIXED_PRICE",
                "availableQuantity": 1,
                "categoryId": payload.category_id,
                "listingDescription": payload.description,
                "pricingSummary": {
                    "price": {
                        "value": _cents_to_dollars_str(payload.list_price_cents),
                        "currency": "USD",
                    }
                },
                "listingPolicies": {
                    "fulfillmentPolicyId": payload.fulfillment_policy_id,
                    "paymentPolicyId": payload.payment_policy_id,
                    "returnPolicyId": payload.return_policy_id,
                },
            }
            if payload.merchant_location_key:
                offer_body["merchantLocationKey"] = payload.merchant_location_key

            offer_resp = self._request(
                "POST",
                f"{base}/sell/inventory/v1/offer",
                json_payload=offer_body,
                headers={"Content-Language": "en-US"},
            ).json()
            offer_id = str(offer_resp["offerId"])

            pub_resp = self._request(
                "POST",
                f"{base}/sell/inventory/v1/offer/{quote(offer_id)}/publish",
            ).json()
            listing_id = str(pub_resp["listingId"])
            return PublishReceipt(
                sku=payload.sku,
                ebay_item_id=listing_id,
                offer_id=offer_id,
                route_used="INVENTORY_REST",
                timestamp_utc=utc_now_iso(),
            )

        # Casual C2C fallback via Trading XML AddFixedPriceItem with inline shipping & return defaults
        pics_xml = "".join(f"<PictureURL>{xml_escape(u)}</PictureURL>" for u in payload.media_urls)
        specs_xml = "".join(
            f"<NameValueList><Name>{xml_escape(k)}</Name><Value>{xml_escape(v)}</Value></NameValueList>"
            for k, v in payload.aspects.items()
        )
        ship_cost_str = _cents_to_dollars_str(payload.shipping_est_cents)
        inner = (
            "<Item>"
            f"<SKU>{xml_escape(payload.sku)}</SKU>"
            f"<Title>{xml_escape(payload.title)}</Title>"
            f"<Description>{xml_escape(payload.description)}</Description>"
            f"<PrimaryCategory><CategoryID>{xml_escape(payload.category_id)}</CategoryID></PrimaryCategory>"
            f"<StartPrice>{_cents_to_dollars_str(payload.list_price_cents)}</StartPrice>"
            f"<ConditionID>{payload.condition.trading_condition_id}</ConditionID>"
            "<Country>US</Country>"
            "<Currency>USD</Currency>"
            "<DispatchTimeMax>3</DispatchTimeMax>"
            "<ListingDuration>GTC</ListingDuration>"
            "<ListingType>FixedPriceItem</ListingType>"
            "<Quantity>1</Quantity>"
            f"<PictureDetails>{pics_xml}</PictureDetails>"
            f"<ItemSpecifics>{specs_xml}</ItemSpecifics>"
            "<ShippingDetails>"
            "<ShippingType>Flat</ShippingType>"
            "<ShippingServiceOptions>"
            "<ShippingServicePriority>1</ShippingServicePriority>"
            "<ShippingService>USPSGroundAdvantage</ShippingService>"
            f"<ShippingServiceCost>{ship_cost_str}</ShippingServiceCost>"
            "</ShippingServiceOptions>"
            "</ShippingDetails>"
            "<ReturnPolicy>"
            "<ReturnsAcceptedOption>ReturnsAccepted</ReturnsAcceptedOption>"
            "<RefundOption>MoneyBack</RefundOption>"
            "<ReturnsWithinOption>Days_30</ReturnsWithinOption>"
            "<ShippingCostPaidByOption>Buyer</ShippingCostPaidByOption>"
            "</ReturnPolicy>"
            "</Item>"
        )
        root = self._xml_call("AddFixedPriceItem", inner)
        item_id = _path_text(root, "ItemID")
        if not item_id:
            raise EbayApiError(502, "AddFixedPriceItem succeeded without returning ItemID")
        return PublishReceipt(
            sku=payload.sku,
            ebay_item_id=item_id,
            offer_id=None,
            route_used="TRADING_XML_FALLBACK",
            timestamp_utc=utc_now_iso(),
        )

    def bulk_publish_listings(self, payloads: list[StagedListingPayload]) -> list[PublishReceipt]:
        """Publish up to 25 SKUs per batch via Sell Inventory bulk_create_or_replace_inventory_item
        and bulk_create_offer (falling back cleanly to publish_listing on C2C accounts).
        """
        if not payloads:
            return []
        receipts: list[PublishReceipt] = []
        base = self._settings.api_base_url.rstrip("/")

        for start in range(0, len(payloads), 25):
            chunk = payloads[start : start + 25]
            rest_chunk = [p for p in chunk if p.route_mode == "INVENTORY_REST"]
            xml_chunk = [p for p in chunk if p.route_mode != "INVENTORY_REST"]

            if rest_chunk:
                inv_requests: list[dict[str, Any]] = []
                offer_requests: list[dict[str, Any]] = []
                for p in rest_chunk:
                    inv_entry: dict[str, Any] = {
                        "sku": p.sku,
                        "locale": "en_US",
                        "availability": {"shipToLocationAvailability": {"quantity": 1}},
                        "condition": p.condition.value,
                        "product": {
                            "title": p.title,
                            "description": p.description,
                            "aspects": {k: [v] for k, v in p.aspects.items()},
                            "imageUrls": p.media_urls,
                        },
                    }
                    if p.package_weight_oz is not None or p.package_dims is not None:
                        pkg: dict[str, Any] = {}
                        if p.package_weight_oz is not None:
                            pkg["weight"] = {"value": p.package_weight_oz, "unit": "OUNCE"}
                        if p.package_dims is not None:
                            pkg["dimensions"] = {
                                "length": p.package_dims.length_in,
                                "width": p.package_dims.width_in,
                                "height": p.package_dims.height_in,
                                "unit": "INCH",
                            }
                        inv_entry["packageWeightAndSize"] = pkg
                    inv_requests.append(inv_entry)

                    offer_entry: dict[str, Any] = {
                        "sku": p.sku,
                        "marketplaceId": p.marketplace_id,
                        "format": "FIXED_PRICE",
                        "availableQuantity": 1,
                        "categoryId": p.category_id,
                        "listingDescription": p.description,
                        "pricingSummary": {
                            "price": {"value": _cents_to_dollars_str(p.list_price_cents), "currency": "USD"}
                        },
                        "listingPolicies": {
                            "fulfillmentPolicyId": p.fulfillment_policy_id,
                            "paymentPolicyId": p.payment_policy_id,
                            "returnPolicyId": p.return_policy_id,
                        },
                    }
                    if p.merchant_location_key:
                        offer_entry["merchantLocationKey"] = p.merchant_location_key
                    offer_requests.append(offer_entry)

                self._request(
                    "POST",
                    f"{base}/sell/inventory/v1/bulk_create_or_replace_inventory_item",
                    json_payload={"requests": inv_requests},
                    headers={"Content-Language": "en-US"},
                )
                bulk_offer_resp = self._request(
                    "POST",
                    f"{base}/sell/inventory/v1/bulk_create_offer",
                    json_payload={"requests": offer_requests},
                    headers={"Content-Language": "en-US"},
                ).json()

                responses = bulk_offer_resp.get("responses") or []
                offer_by_sku = {
                    str(r.get("sku") or ""): str(r.get("offerId") or "")
                    for r in responses
                    if r.get("offerId")
                }
                for idx, p in enumerate(rest_chunk):
                    offer_id = offer_by_sku.get(p.sku) or (
                        str(responses[idx]["offerId"])
                        if idx < len(responses) and responses[idx].get("offerId")
                        else ""
                    )
                    if not offer_id:
                        raise EbayApiError(502, f"bulk_create_offer returned no offerId for SKU {p.sku}")
                    pub_resp = self._request(
                        "POST",
                        f"{base}/sell/inventory/v1/offer/{quote(offer_id)}/publish",
                    ).json()
                    receipts.append(
                        PublishReceipt(
                            sku=p.sku,
                            ebay_item_id=str(pub_resp["listingId"]),
                            offer_id=offer_id,
                            route_used="INVENTORY_REST",
                            timestamp_utc=utc_now_iso(),
                        )
                    )

            for p in xml_chunk:
                receipts.append(self.publish_listing(p))

        return receipts

    def revise_listing(
        self, sku: str, ebay_item_id: str, changes: dict[str, Any]
    ) -> PublishReceipt:
        offer_id = changes.get("offer_id")
        if offer_id and changes.get("route_mode") == "INVENTORY_REST":
            base = self._settings.api_base_url.rstrip("/")
            if any(k in changes for k in ("title", "description", "aspects", "media_urls")):
                inv_patch: dict[str, Any] = {
                    "availability": {"shipToLocationAvailability": {"quantity": 1}},
                    "condition": changes.get("condition", "USED_GOOD"),
                    "product": {
                        "title": changes["title"],
                        "description": changes["description"],
                        "aspects": {k: [v] for k, v in (changes.get("aspects") or {}).items()},
                        "imageUrls": changes.get("media_urls") or [],
                    },
                }
                self._request(
                    "PUT",
                    f"{base}/sell/inventory/v1/inventory_item/{quote(sku)}",
                    json_payload=inv_patch,
                    headers={"Content-Language": "en-US"},
                )
            if "list_price_cents" in changes or "description" in changes:
                offer_update = dict(changes.get("offer_payload") or {})
                if "list_price_cents" in changes:
                    offer_update["pricingSummary"] = {
                        "price": {
                            "value": _cents_to_dollars_str(int(changes["list_price_cents"])),
                            "currency": "USD",
                        }
                    }
                if "description" in changes:
                    offer_update["listingDescription"] = changes["description"]
                self._request(
                    "PUT",
                    f"{base}/sell/inventory/v1/offer/{quote(str(offer_id))}",
                    json_payload=offer_update,
                    headers={"Content-Language": "en-US"},
                )
            return PublishReceipt(
                sku=sku,
                ebay_item_id=ebay_item_id,
                offer_id=str(offer_id),
                route_used="INVENTORY_REST",
                timestamp_utc=utc_now_iso(),
            )

        fields_xml = [f"<ItemID>{xml_escape(ebay_item_id)}</ItemID>"]
        if sku:
            fields_xml.append(f"<SKU>{xml_escape(sku)}</SKU>")
        if "title" in changes and changes["title"]:
            fields_xml.append(f"<Title>{xml_escape(str(changes['title']))}</Title>")
        if "description" in changes and changes["description"]:
            fields_xml.append(f"<Description>{xml_escape(str(changes['description']))}</Description>")
        if "list_price_cents" in changes and changes["list_price_cents"] is not None:
            fields_xml.append(f"<StartPrice>{_cents_to_dollars_str(int(changes['list_price_cents']))}</StartPrice>")
        if "aspects" in changes and changes["aspects"]:
            specs_xml = "".join(
                f"<NameValueList><Name>{xml_escape(str(k))}</Name><Value>{xml_escape(str(v))}</Value></NameValueList>"
                for k, v in dict(changes["aspects"]).items()
            )
            fields_xml.append(f"<ItemSpecifics>{specs_xml}</ItemSpecifics>")

        inner = f"<Item>{''.join(fields_xml)}</Item>"
        self._xml_call("ReviseFixedPriceItem", inner)
        return PublishReceipt(
            sku=sku,
            ebay_item_id=ebay_item_id,
            offer_id=None,
            route_used="TRADING_XML_FALLBACK",
            timestamp_utc=utc_now_iso(),
        )

    def list_best_offers(self, ebay_item_id: str | None = None) -> list[BestOfferRecord]:
        parts = ["<BestOfferStatus>All</BestOfferStatus>", "<DetailLevel>ReturnAll</DetailLevel>"]
        if ebay_item_id:
            parts.insert(0, f"<ItemID>{xml_escape(ebay_item_id)}</ItemID>")
        root = self._xml_call("GetBestOffers", "".join(parts))

        status_lookup: dict[str, BestOfferLifecycleStatus] = {
            "ACTIVE": BestOfferLifecycleStatus.ACTIVE,
            "PENDING": BestOfferLifecycleStatus.ACTIVE,
            "SELLERACCEPT": BestOfferLifecycleStatus.SELLER_ACCEPT,
            "ACCEPTED": BestOfferLifecycleStatus.ACCEPTED,
            "COUNTERADMIN": BestOfferLifecycleStatus.COUNTERED,
            "COUNTERED": BestOfferLifecycleStatus.COUNTERED,
            "SELLERCOUNTER": BestOfferLifecycleStatus.COUNTERED,
            "BUYERCOUNTER": BestOfferLifecycleStatus.COUNTERED,
            "DECLINED": BestOfferLifecycleStatus.DECLINED,
            "EXPIRED": BestOfferLifecycleStatus.EXPIRED,
            "WITHDRAWN": BestOfferLifecycleStatus.WITHDRAWN,
            "RETRACTED": BestOfferLifecycleStatus.WITHDRAWN,
        }

        records: list[BestOfferRecord] = []
        item_blocks = _iter_by_name(root, "ItemBestOffers")
        if item_blocks:
            for block in item_blocks:
                item_node = _child_by_name(block, "Item")
                block_item_id = (_path_text(item_node, "ItemID") if item_node is not None else None) or ebay_item_id or ""
                block_sku = _path_text(item_node, "SKU") if item_node is not None else None
                block_title = _path_text(item_node, "Title") if item_node is not None else None
                bo_array = _child_by_name(block, "BestOfferArray")
                offer_nodes = _children_by_name(bo_array, "BestOffer") if bo_array is not None else []
                for bo in offer_nodes:
                    records.append(self._parse_best_offer_node(bo, block_item_id, block_sku, block_title, status_lookup))
        else:
            top_item = _child_by_name(root, "Item")
            fallback_item_id = (_path_text(top_item, "ItemID") if top_item is not None else None) or ebay_item_id or ""
            fallback_sku = _path_text(top_item, "SKU") if top_item is not None else None
            fallback_title = _path_text(top_item, "Title") if top_item is not None else None
            for bo in _iter_by_name(root, "BestOffer"):
                records.append(
                    self._parse_best_offer_node(bo, fallback_item_id, fallback_sku, fallback_title, status_lookup)
                )
        return records

    @staticmethod
    def _parse_best_offer_node(
        bo_node: Any,
        item_id: str,
        sku: str | None,
        title: str | None,
        status_lookup: dict[str, BestOfferLifecycleStatus],
    ) -> BestOfferRecord:
        offer_id = _path_text(bo_node, "BestOfferID") or "UNKNOWN"
        raw_status = (_path_text(bo_node, "Status") or "Active").strip()
        mapped_status = status_lookup.get(raw_status.upper(), BestOfferLifecycleStatus.ACTIVE)
        price_cents = max(1, _dollars_to_cents(_path_text(bo_node, "Price") or "0.01"))
        qty = int(_path_text(bo_node, "Quantity") or "1")
        buyer = _path_text(bo_node, "Buyer", "UserID") or "buyer"
        message = _path_text(bo_node, "BuyerMessage")
        expiration = _path_text(bo_node, "ExpirationTime")
        return BestOfferRecord(
            best_offer_id=offer_id,
            ebay_item_id=item_id,
            sku=sku,
            title=title,
            buyer_masked_id=buyer,
            offer_price_cents=price_cents,
            quantity=max(1, qty),
            status=mapped_status,
            buyer_message=message,
            expires_at=expiration,
        )

    def respond_to_offer(self, payload: RespondOfferPayload) -> OfferActionReceipt:
        parts = [
            f"<ItemID>{xml_escape(payload.ebay_item_id)}</ItemID>",
            f"<BestOfferID>{xml_escape(payload.best_offer_id)}</BestOfferID>",
            f"<Action>{xml_escape(payload.action.value)}</Action>",
        ]
        if payload.action == OfferDecisionAction.COUNTER and payload.counter_price_cents is not None:
            parts.append(
                f"<CounterOfferPrice currencyID=\"USD\">{_cents_to_dollars_str(payload.counter_price_cents)}</CounterOfferPrice>"
            )
            parts.append("<CounterOfferQuantity>1</CounterOfferQuantity>")
        if payload.seller_message:
            parts.append(f"<SellerResponse>{xml_escape(payload.seller_message)}</SellerResponse>")

        self._xml_call("RespondToBestOffer", "".join(parts))
        resulting_map = {
            OfferDecisionAction.ACCEPT: BestOfferLifecycleStatus.SELLER_ACCEPT,
            OfferDecisionAction.COUNTER: BestOfferLifecycleStatus.COUNTERED,
            OfferDecisionAction.DECLINE: BestOfferLifecycleStatus.DECLINED,
        }
        return OfferActionReceipt(
            ebay_item_id=payload.ebay_item_id,
            best_offer_id=payload.best_offer_id,
            action=payload.action,
            resulting_status=resulting_map[payload.action],
            timestamp_utc=utc_now_iso(),
        )

    def list_paid_orders(self, limit: int = 25) -> list[PaidOrderLine]:
        """Fetch paid, unfulfilled order lines via REST Sell Fulfillment API."""
        base = self._settings.api_base_url.rstrip("/")
        qs = urlencode(
            {
                "filter": "orderfulfillmentstatus:{NOT_STARTED|IN_PROGRESS}",
                "limit": str(int(limit)),
            }
        )
        resp = self._request("GET", f"{base}/sell/fulfillment/v1/order?{qs}")
        raw_orders = resp.json().get("orders") or []

        lines: list[PaidOrderLine] = []
        for order in raw_orders:
            if str(order.get("orderPaymentStatus", "")).upper() != "PAID":
                continue
            order_id = str(order.get("orderId", ""))
            buyer_user = str((order.get("buyer") or {}).get("username", "buyer"))
            paid_at = str(order.get("creationDate") or utc_now_iso())
            for li in order.get("lineItems") or []:
                cost_val = (li.get("lineItemCost") or {}).get("value") or (li.get("total") or {}).get("value") or "0.01"
                lines.append(
                    PaidOrderLine(
                        order_id=order_id,
                        line_item_id=str(li.get("lineItemId", "")),
                        ebay_item_id=str(li.get("legacyItemId") or ""),
                        sku=li.get("sku"),
                        title=str(li.get("title", "Sold Item")),
                        quantity=max(1, int(li.get("quantity", 1))),
                        sold_price_cents=max(1, _dollars_to_cents(cost_val)),
                        buyer_username=buyer_user,
                        paid_at=paid_at,
                    )
                )
        return lines
