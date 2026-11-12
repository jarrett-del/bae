"""Strict Pydantic v2 data models for tool I/O, SQLite DTOs, and EbayAdapter contracts."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

SKU_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{1,49}$")
HRM_SKU_PATTERN = re.compile(r"^HRM-\d{8}-[0-9A-F]{4}$")

PositiveCents = Annotated[StrictInt, Field(gt=0, description="Strictly positive integer amount in minor currency cents.")]
NonNegativeCents = Annotated[StrictInt, Field(ge=0, description="Non-negative integer amount in minor currency cents.")]


def utc_now_iso(now: datetime | None = None) -> str:
    """Return an ISO-8601 UTC timestamp with second precision."""
    dt = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def mint_sku(now: datetime | None = None) -> str:
    """Auto-mint a casual C2C hidden SKU in the format HRM-YYYYMMDD-XXXX."""
    dt = now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc)
    date_part = dt.strftime("%Y%m%d")
    rand_part = secrets.token_hex(2).upper()
    return f"HRM-{date_part}-{rand_part}"


class ConditionEnum(str, Enum):
    """Standardized eBay Inventory/Trading condition enumerations."""

    NEW = "NEW"
    NEW_OTHER = "NEW_OTHER"
    NEW_WITH_DEFECTS = "NEW_WITH_DEFECTS"
    LIKE_NEW = "LIKE_NEW"
    USED_EXCELLENT = "USED_EXCELLENT"
    USED_VERY_GOOD = "USED_VERY_GOOD"
    USED_GOOD = "USED_GOOD"
    USED_ACCEPTABLE = "USED_ACCEPTABLE"
    FOR_PARTS_OR_NOT_WORKING = "FOR_PARTS_OR_NOT_WORKING"

    @property
    def trading_condition_id(self) -> str:
        """Map canonical ConditionEnum to eBay Trading XML numeric ConditionID."""
        mapping = {
            ConditionEnum.NEW: "1000",
            ConditionEnum.NEW_OTHER: "1500",
            ConditionEnum.NEW_WITH_DEFECTS: "1750",
            ConditionEnum.LIKE_NEW: "2750",
            ConditionEnum.USED_EXCELLENT: "3000",
            ConditionEnum.USED_VERY_GOOD: "4000",
            ConditionEnum.USED_GOOD: "5000",
            ConditionEnum.USED_ACCEPTABLE: "6000",
            ConditionEnum.FOR_PARTS_OR_NOT_WORKING: "7000",
        }
        return mapping[self]


_CONDITION_SYNONYMS: dict[str, ConditionEnum] = {
    "NEW": ConditionEnum.NEW,
    "BRAND_NEW": ConditionEnum.NEW,
    "NWT": ConditionEnum.NEW,
    "NEW_WITH_TAGS": ConditionEnum.NEW,
    "1000": ConditionEnum.NEW,
    "NEW_OTHER": ConditionEnum.NEW_OTHER,
    "NWOT": ConditionEnum.NEW_OTHER,
    "NEW_WITHOUT_TAGS": ConditionEnum.NEW_OTHER,
    "1500": ConditionEnum.NEW_OTHER,
    "NEW_WITH_DEFECTS": ConditionEnum.NEW_WITH_DEFECTS,
    "1750": ConditionEnum.NEW_WITH_DEFECTS,
    "LIKE_NEW": ConditionEnum.LIKE_NEW,
    "2750": ConditionEnum.LIKE_NEW,
    "USED_EXCELLENT": ConditionEnum.USED_EXCELLENT,
    "PRE_OWNED_EXCELLENT": ConditionEnum.USED_EXCELLENT,
    "PRE_OWNED": ConditionEnum.USED_EXCELLENT,
    "PREOWNED": ConditionEnum.USED_EXCELLENT,
    "EXCELLENT": ConditionEnum.USED_EXCELLENT,
    "USED": ConditionEnum.USED_GOOD,
    "3000": ConditionEnum.USED_EXCELLENT,
    "USED_VERY_GOOD": ConditionEnum.USED_VERY_GOOD,
    "VERY_GOOD": ConditionEnum.USED_VERY_GOOD,
    "4000": ConditionEnum.USED_VERY_GOOD,
    "USED_GOOD": ConditionEnum.USED_GOOD,
    "GOOD": ConditionEnum.USED_GOOD,
    "5000": ConditionEnum.USED_GOOD,
    "USED_ACCEPTABLE": ConditionEnum.USED_ACCEPTABLE,
    "ACCEPTABLE": ConditionEnum.USED_ACCEPTABLE,
    "FAIR": ConditionEnum.USED_ACCEPTABLE,
    "6000": ConditionEnum.USED_ACCEPTABLE,
    "FOR_PARTS_OR_NOT_WORKING": ConditionEnum.FOR_PARTS_OR_NOT_WORKING,
    "FOR_PARTS": ConditionEnum.FOR_PARTS_OR_NOT_WORKING,
    "PARTS": ConditionEnum.FOR_PARTS_OR_NOT_WORKING,
    "7000": ConditionEnum.FOR_PARTS_OR_NOT_WORKING,
}


def normalize_condition(raw: str | ConditionEnum | None) -> ConditionEnum:
    """Normalize friendly seller condition shorthand into strict ConditionEnum."""
    if raw is None:
        return ConditionEnum.USED_GOOD
    if isinstance(raw, ConditionEnum):
        return raw
    cleaned = re.sub(r"[\s\-]+", "_", raw.strip().upper())
    if cleaned in _CONDITION_SYNONYMS:
        return _CONDITION_SYNONYMS[cleaned]
    return ConditionEnum(cleaned)


class SourceMode(str, Enum):
    AGENT_CAPTURE = "AGENT_CAPTURE"
    MAGICAL_AUDIT = "MAGICAL_AUDIT"


class ItemStatus(str, Enum):
    STAGED = "STAGED"
    ACTIVE = "ACTIVE"
    SOLD = "SOLD"
    ENDED = "ENDED"


class ActionType(str, Enum):
    PUBLISH_LISTING = "PUBLISH_LISTING"
    REVISE_LISTING = "REVISE_LISTING"
    BOOTSTRAP_POLICIES = "BOOTSTRAP_POLICIES"
    RESPOND_OFFER = "RESPOND_OFFER"


class ActionStatus(str, Enum):
    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class EventActor(str, Enum):
    USER = "USER"
    AGENT_STAGED = "AGENT_STAGED"
    EBAY_SYNC = "EBAY_SYNC"
    CRON = "CRON"


class BestOfferLifecycleStatus(str, Enum):
    """eBay Best Offer status codes including 2026 SellerAccept unpaid state."""

    ACTIVE = "Active"
    SELLER_ACCEPT = "SellerAccept"
    ACCEPTED = "Accepted"
    COUNTERED = "Countered"
    DECLINED = "Declined"
    EXPIRED = "Expired"
    WITHDRAWN = "Withdrawn"


class OfferDecisionAction(str, Enum):
    ACCEPT = "Accept"
    COUNTER = "Counter"
    DECLINE = "Decline"


class StrictModel(BaseModel):
    """Base model forbidding unexpected fields and validating assignments."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class PackageDimensions(StrictModel):
    length_in: float = Field(gt=0, le=108)
    width_in: float = Field(gt=0, le=108)
    height_in: float = Field(gt=0, le=108)


class AccountCapabilities(StrictModel):
    marketplace_id: str = "EBAY_US"
    policies_opted_in: bool = False
    fulfillment_policy_id: str | None = None
    payment_policy_id: str | None = None
    return_policy_id: str | None = None
    merchant_location_key: str | None = None


class CategoryAspectSpec(StrictModel):
    name: str = Field(min_length=1)
    required: bool = False
    usage: Literal["REQUIRED", "RECOMMENDED", "OPTIONAL"] = "OPTIONAL"
    data_type: str = "STRING"
    allowed_values: list[str] = Field(default_factory=list)
    multi_value: bool = False


class CompSample(StrictModel):
    item_id: str
    title: str
    price_cents: NonNegativeCents
    shipping_cents: NonNegativeCents = 0
    condition: str | None = None


class CompSummary(StrictModel):
    query: str
    category_id: str | None = None
    condition: str | None = None
    sample_count: int = Field(ge=0)
    p25_cents: NonNegativeCents
    median_cents: NonNegativeCents
    p75_cents: NonNegativeCents
    typical_shipping_cents: NonNegativeCents = 0
    samples: list[CompSample] = Field(default_factory=list)


class ActiveListingSnapshot(StrictModel):
    ebay_item_id: str = Field(min_length=1)
    sku: str | None = None
    offer_id: str | None = None
    title: str = Field(min_length=1, max_length=80)
    description: str = ""
    category_id: str = Field(min_length=1)
    condition: ConditionEnum
    list_price_cents: PositiveCents
    shipping_est_cents: NonNegativeCents = 0
    aspects: dict[str, str] = Field(default_factory=dict)
    media_urls: list[str] = Field(default_factory=list)
    created_via_magical_listing: bool = False
    listed_at: str | None = None


class StagedListingPayload(StrictModel):
    sku: str = Field(min_length=3, max_length=50)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1)
    category_id: str = Field(min_length=1)
    condition: ConditionEnum
    list_price_cents: PositiveCents
    shipping_est_cents: NonNegativeCents = 0
    package_weight_oz: float | None = Field(default=None, gt=0, le=2400)
    package_dims: PackageDimensions | None = None
    aspects: dict[str, str] = Field(default_factory=dict)
    flaws_disclosure: str | None = None
    media_paths: list[str] = Field(default_factory=list)
    media_urls: list[str] = Field(default_factory=list)
    marketplace_id: str = "EBAY_US"
    route_mode: Literal["INVENTORY_REST", "TRADING_XML_FALLBACK"] = "TRADING_XML_FALLBACK"
    fulfillment_policy_id: str | None = None
    payment_policy_id: str | None = None
    return_policy_id: str | None = None
    merchant_location_key: str | None = None
    cogs_cents: NonNegativeCents | None = None
    bin_code: str | None = None
    floor_price_cents: PositiveCents | None = None

    @field_validator("sku")
    @classmethod
    def _validate_sku(cls, value: str) -> str:
        cleaned = value.strip()
        if not SKU_PATTERN.match(cleaned):
            raise ValueError(f"Invalid SKU format: {value!r}")
        return cleaned

    @model_validator(mode="after")
    def _check_floor_vs_list(self) -> StagedListingPayload:
        if self.floor_price_cents is not None and self.floor_price_cents > self.list_price_cents:
            raise ValueError("floor_price_cents cannot exceed list_price_cents")
        return self


class ReviseListingPayload(StrictModel):
    sku: str = Field(min_length=3, max_length=50)
    ebay_item_id: str = Field(min_length=1)
    offer_id: str | None = None
    changes: dict[str, Any] = Field(min_length=1)


class BootstrapPoliciesPayload(StrictModel):
    marketplace_id: str = "EBAY_US"
    default_return_days: int = Field(default=30, ge=14, le=60)
    shipping_service: str = "USPSGroundAdvantage"


class RespondOfferPayload(StrictModel):
    sku: str | None = None
    ebay_item_id: str = Field(min_length=1)
    best_offer_id: str = Field(min_length=1)
    action: OfferDecisionAction
    counter_price_cents: PositiveCents | None = None
    seller_message: str | None = Field(default=None, max_length=250)

    @model_validator(mode="after")
    def _validate_counter_price(self) -> RespondOfferPayload:
        if self.action == OfferDecisionAction.COUNTER and self.counter_price_cents is None:
            raise ValueError("counter_price_cents is required when action is Counter")
        if self.action != OfferDecisionAction.COUNTER and self.counter_price_cents is not None:
            raise ValueError("counter_price_cents is only permitted when action is Counter")
        return self


class PublishReceipt(StrictModel):
    sku: str
    ebay_item_id: str
    offer_id: str | None = None
    route_used: Literal["INVENTORY_REST", "TRADING_XML_FALLBACK"]
    timestamp_utc: str
    warnings: list[str] = Field(default_factory=list)


class OfferActionReceipt(StrictModel):
    ebay_item_id: str
    best_offer_id: str
    action: OfferDecisionAction
    resulting_status: BestOfferLifecycleStatus
    timestamp_utc: str


class BestOfferRecord(StrictModel):
    best_offer_id: str
    ebay_item_id: str
    sku: str | None = None
    title: str | None = None
    buyer_masked_id: str
    offer_price_cents: PositiveCents
    quantity: int = Field(default=1, ge=1)
    status: BestOfferLifecycleStatus
    buyer_message: str | None = None
    expires_at: str | None = None


class PaidOrderLine(StrictModel):
    order_id: str
    line_item_id: str
    ebay_item_id: str
    sku: str | None = None
    title: str
    quantity: int = Field(default=1, ge=1)
    sold_price_cents: PositiveCents
    buyer_username: str
    paid_at: str


# ---------------------------------------------------------------------------
# Tool Input Validation Models
# ---------------------------------------------------------------------------


class StageListingInput(StrictModel):
    media_paths: list[str] = Field(default_factory=list)
    seller_notes: str = Field(min_length=1)
    title_hint: str | None = None
    price_cents: PositiveCents | None = None
    condition: ConditionEnum | None = None
    cogs_cents: NonNegativeCents | None = None
    bin_code: str | None = None
    floor_price_cents: PositiveCents | None = None
    package_weight_oz: float | None = Field(default=None, gt=0, le=2400)
    sku: str | None = None

    @field_validator("condition", mode="before")
    @classmethod
    def _coerce_condition(cls, value: Any) -> ConditionEnum | None:
        if value is None or value == "":
            return None
        if isinstance(value, (str, ConditionEnum)):
            return normalize_condition(value)
        raise ValueError(f"Unsupported condition type: {type(value).__name__}")

    @field_validator("bin_code")
    @classmethod
    def _clean_bin_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned or None


class AuditActiveListingInput(StrictModel):
    ebay_item_id: str | None = None
    seller_notes: str | None = None
    new_price_cents: PositiveCents | None = None
    cogs_cents: NonNegativeCents | None = None
    bin_code: str | None = None
    floor_price_cents: PositiveCents | None = None

    @field_validator("bin_code")
    @classmethod
    def _clean_bin_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip().upper()
        return cleaned or None


class CheckCompsInput(StrictModel):
    query: str = Field(min_length=1)
    category_id: str | None = None
    condition: ConditionEnum | None = None
    cogs_cents: NonNegativeCents | None = None

    @field_validator("condition", mode="before")
    @classmethod
    def _coerce_condition(cls, value: Any) -> ConditionEnum | None:
        if value is None or value == "":
            return None
        if isinstance(value, (str, ConditionEnum)):
            return normalize_condition(value)
        raise ValueError(f"Unsupported condition type: {type(value).__name__}")


class ConfirmActionInput(StrictModel):
    action_token: str = Field(min_length=6)
    decision: Literal["APPROVE", "CANCEL"]


class TriageOffersInput(StrictModel):
    ebay_item_id: str | None = None
    min_margin_bps: int = Field(default=1500, ge=0, le=9500)


class LookupBinInput(StrictModel):
    limit: int = Field(default=25, ge=1, le=100)
