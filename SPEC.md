Save this specification as SPEC.md in your repository root and instruct Antigravity to generate its implementation plan and task checklist directly from it. It establishes hard architectural invariants up front, prevents the coding agent from calling eBay XML endpoints decommissioned in 2026, and breaks Phase 1 (Lite C2C plus Pro reseller hooks) into verifiable tasks that pass Hermes plugin validation.

1. Project Constitution & Non-Negotiable Invariants
Antigravity must treat these six rules as hard constraints across every module and test:

	•	Hermes Public Surface Only: Register tools, hooks, skills, and CLI commands strictly via register(ctx) and store profile-scoped SQLite data via plugins.plugin_storage.plugin_db without monkeypatching Hermes core. 
	•	Zero Raw Credential Access: Never read environment files, token stores, or raw OAuth secrets in Python; declare OAuth in plugin.yaml under requires_auth and execute requests via credentialed_provider_request. 
	•	2026 eBay Deprecation Compliance: Never use Trading XML calls decommissioned in 2026—specifically UploadSiteHostedPictures, GetCategories, or GetCategoryFeatures. 
	•	Modern Endpoint Routing: Route image uploads to REST Media API (createImageFromFile), category aspects to REST Taxonomy and Metadata APIs, and paid orders to REST Fulfillment API rather than deprecated GetOrders. 
	•	Hard Two-Step Write Gate: No live eBay write (publish, revise, policy bootstrap, or offer action) may execute without an unexpired action_token verified against SHA-256 payload hashes in SQLite. 
	•	Progressive Lite-to-Pro Enrichment: Never require SKU, COGS, or bin inputs at intake; auto-mint hidden SKUs for casual C2C sellers and unlock Pro margin/bin workflows automatically when operational fields appear. 
2. Repository Layout & Hermes Manifest
Target a standalone Python plugin installable into the user's Hermes plugins directory and validated via the Hermes CLI:

Plaintext

hermes-ebay-ops/
├── SPEC.md
├── plugin.yaml
├── pyproject.toml
├── __init__.py                 # register(ctx) entrypoint + check_fn wiring
├── config.py                   # Mode flags, fee defaults, token TTL settings
├── db.py                       # plugin_db() SQLite connection, schema init, ledger helpers
├── models.py                   # Strict Pydantic v2 models for tool I/O and adapter DTOs
├── adapter/
│   ├── base.py                 # EbayAdapter Protocol definition
│   ├── live.py                 # Live HTTP/XML client using credentialed_provider_request
│   └── mock.py                 # Deterministic offline fixtures for Lite and Pro accounts
├── tools/
│   ├── phase1_mvp.py           # stage_listing, audit_active_listing, check_comps, confirm_action
│   └── phase2_ops.py           # triage_offers, lookup_bin (gated via check_fn)
├── skills/
│   └── ebay-seller-ops/
│       └── SKILL.md            # Bundled Hermes skill teaching multimodal intake + QA etiquette
└── tests/
    ├── test_db_invariants.py
    ├── test_adapter_routing.py
    ├── test_approval_gate.py
    └── test_progressive_mode.py
Define plugin.yaml using native Hermes authentication and schema conventions, placing LLM-facing instructions inside each tool's schema["description"]:

YAML

name: hermes-ebay-ops
version: "0.1.0"
description: Multimodal listing QA, unit economics, and store ops companion for eBay sellers
python_dependencies:
  - pydantic>=2.7.0
  - defusedxml>=0.7.1
requires_auth:
  - id: ebay
    authorize_url: https://auth.ebay.com/oauth2/authorize
    token_url: https://api.ebay.com/identity/v1/oauth2/token
    client_id_env: EBAY_PLUGIN_CLIENT_ID
    client_secret_env: EBAY_PLUGIN_CLIENT_SECRET
    hosts:
      - api.ebay.com
      - apiz.ebay.com
    scopes:
      - https://api.ebay.com/oauth/api_scope/sell.inventory
      - https://api.ebay.com/oauth/api_scope/sell.account
      - https://api.ebay.com/oauth/api_scope/sell.fulfillment.readonly
      - https://api.ebay.com/oauth/api_scope/commerce.identity.readonly
provides_tools:
  - ebay_stage_listing
  - ebay_audit_active_listing
  - ebay_check_comps
  - ebay_confirm_action
  - ebay_triage_offers
  - ebay_lookup_bin
3. Forward-Compatible SQLite Data Model
Initialize SQLite in WAL mode with busy_timeout = 5000 using Hermes's profile-scoped plugin_db() helper so seller ledgers survive plugin upgrades without storing credentials. Create all four tables in Phase 1 so Phase 2 and Phase 3 require zero destructive schema migrations:

SQL

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Caches seller account capabilities and Lite vs. Pro progressive state
CREATE TABLE IF NOT EXISTS seller_profile (
    profile_key             TEXT PRIMARY KEY DEFAULT 'default',
    marketplace_id          TEXT NOT NULL DEFAULT 'EBAY_US',
    policies_opted_in       INTEGER NOT NULL DEFAULT 0,
    fulfillment_policy_id   TEXT,
    payment_policy_id       TEXT,
    return_policy_id        TEXT,
    merchant_location_key   TEXT,
    pro_ops_enabled         INTEGER NOT NULL DEFAULT 0,
    estimated_fee_bps       INTEGER NOT NULL DEFAULT 1360,
    updated_at              TEXT NOT NULL
);

-- Unified Lite + Pro inventory record (Pro columns nullable by design)
CREATE TABLE IF NOT EXISTS items (
    sku                     TEXT PRIMARY KEY,
    ebay_item_id            TEXT UNIQUE,
    offer_id                TEXT UNIQUE,
    source_mode             TEXT NOT NULL CHECK (source_mode IN ('AGENT_CAPTURE', 'MAGICAL_AUDIT')),
    status                  TEXT NOT NULL CHECK (status IN ('STAGED', 'ACTIVE', 'SOLD', 'ENDED')),
    title                   TEXT NOT NULL,
    category_id             TEXT NOT NULL,
    condition_enum          TEXT NOT NULL,
    list_price_cents        INTEGER NOT NULL CHECK (list_price_cents > 0),
    shipping_est_cents      INTEGER NOT NULL DEFAULT 0,
    package_weight_oz       REAL,
    package_dims_json       TEXT,
    aspects_json            TEXT NOT NULL DEFAULT '{}',
    flaws_disclosure        TEXT,
    media_urls_json         TEXT NOT NULL DEFAULT '[]',
    -- Progressive Pro Seller columns (NULL for casual C2C)
    cogs_cents              INTEGER CHECK (cogs_cents IS NULL OR cogs_cents >= 0),
    bin_code                TEXT,
    floor_price_cents       INTEGER CHECK (floor_price_cents IS NULL OR floor_price_cents > 0),
    listed_at               TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

-- Cryptographically bound two-step human approval queue
CREATE TABLE IF NOT EXISTS pending_actions (
    action_token            TEXT PRIMARY KEY,
    action_type             TEXT NOT NULL CHECK (
                                action_type IN ('PUBLISH_LISTING', 'REVISE_LISTING', 'BOOTSTRAP_POLICIES', 'RESPOND_OFFER')
                            ),
    sku                     TEXT REFERENCES items(sku),
    payload_json            TEXT NOT NULL,
    payload_sha256          TEXT NOT NULL,
    preview_card_md         TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'PENDING' CHECK (
                                status IN ('PENDING', 'EXECUTED', 'EXPIRED', 'CANCELLED', 'FAILED')
                            ),
    expires_at              TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    executed_at             TEXT
);

-- Append-only audit log for every draft, QA fix, price change, and offer decision
CREATE TABLE IF NOT EXISTS item_events (
    event_id                INTEGER PRIMARY KEY AUTOINCREMENT,
    sku                     TEXT NOT NULL REFERENCES items(sku),
    event_type              TEXT NOT NULL,
    actor                   TEXT NOT NULL CHECK (actor IN ('USER', 'AGENT_STAGED', 'EBAY_SYNC', 'CRON')),
    detail_json             TEXT NOT NULL,
    created_at              TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS prevent_item_events_update
BEFORE UPDATE ON item_events
BEGIN
    SELECT RAISE(ABORT, 'item_events ledger is append-only');
END;

CREATE TRIGGER IF NOT EXISTS prevent_item_events_delete
BEFORE DELETE ON item_events
BEGIN
    SELECT RAISE(ABORT, 'item_events ledger is append-only');
END;
4. Unified EbayAdapter Contract & API Routing
Isolate eBay's split REST and XML surfaces behind a single Python Protocol in adapter/base.py so tool handlers never construct raw URLs or XML envelopes.

Adapter Method
eBay API
Endpoint or Call
Seller Tier Role
upload_media
Media REST
createImageFromFile
Both (replaces XML upload)
get_category_aspects
Taxonomy REST
getItemAspectsForCategory
Both (aspect validation)
search_comps
Browse REST
item_summary/search
Both (price sanity check)
check_policy_status
Account REST
getOptedInPrograms
Routes Inventory vs. Trading
publish_or_revise
Inventory or XML
createOffer or AddFixedPriceItem
Pro REST or C2C fallback
list_best_offers
Trading XML
GetBestOffers
Pro margin offer triage
list_paid_orders
Fulfillment REST
getOrders
Pro bin pick-list lookup
Key adapter routing behaviors to enforce in adapter/live.py:

	•	Media Host Separation: Send createImageFromFile multipart uploads to [https://apiz.ebay.com/commerce/media/v1_beta/image/create_image_from_file](https://apiz.ebay.com/commerce/media/v1_beta/image/create_image_from_file) rather than api.ebay.com. 
	•	Casual C2C Policy Fallback: Query getOptedInPrograms once per profile and cache in seller_profile. If SELLING_POLICY_MANAGEMENT is enabled, publish via REST Sell Inventory API(createOrReplaceInventoryItem → createOffer → publishOffer). If disabled, either stage a one-time BOOTSTRAP_POLICIES approval action or route single-item Lite listings through Trading APIAddFixedPriceItem with inline shipping and return defaults. 
	•	2026 Best Offer Lifecycle Enum: When parsing GetBestOffers XML via defusedxml, handle eBay's 2026 SellerAccept status code (seller accepted, awaiting buyer payment) distinctly from Active (needs decision) and Accepted (paid) so the agent never double-actions an unpaid offer. 
Python

from typing import Protocol
from models import (
    AccountCapabilities,
    ActiveListingSnapshot,
    BestOfferRecord,
    CategoryAspectSpec,
    CompSummary,
    PaidOrderLine,
    PublishReceipt,
    StagedListingPayload,
)

class EbayAdapter(Protocol):
    def check_policy_status(self, marketplace_id: str = "EBAY_US") -> AccountCapabilities: ...
    def upload_media(self, file_paths: list[str]) -> list[str]: ...
    def suggest_category(self, query: str, marketplace_id: str = "EBAY_US") -> str: ...
    def get_category_aspects(self, category_id: str, marketplace_id: str = "EBAY_US") -> list[CategoryAspectSpec]: ...
    def search_comps(self, query: str, category_id: str | None, condition: str | None) -> CompSummary: ...
    def fetch_recent_active_listings(self, limit: int = 10) -> list[ActiveListingSnapshot]: ...
    def publish_listing(self, payload: StagedListingPayload) -> PublishReceipt: ...
    def revise_listing(self, sku: str, ebay_item_id: str, changes: dict) -> PublishReceipt: ...
    def list_best_offers(self, ebay_item_id: str | None = None) -> list[BestOfferRecord]: ...
    def list_paid_orders(self, limit: int = 25) -> list[PaidOrderLine]: ...
5. Hermes Tool Specifications & check_fn Gating
Register tools in init.py via ctx.register_tool(), reading session metadata via handler keyword arguments (session_id, platform) and returning structured Markdown/JSON strings rather than calling print().

Phase 1 Core Tools (Always Active When Plugin Enabled)
	1	ebay_stage_listing 
	◦	Inputs: media_paths: list[str], seller_notes: str, title_hint: str | None, price_cents: int | None, condition: str | None, cogs_cents: int | None, bin_code: str | None, floor_price_cents: int | None, package_weight_oz: float | None. 
	◦	Behavior: Auto-mints HRM-YYYYMMDD-XXXX if no SKU exists. Queries suggest_category and get_category_aspects, extracting concrete measurements and flaw disclosures from seller_notes without marketing fluff. If cogs_cents or bin_code is supplied, flips seller_profile.pro_ops_enabled = 1. Inserts items(STAGED) and pending_actions (PUBLISH_LISTING, 15-minute TTL, SHA-256 digest). 
	◦	Output: Markdown approval card showing required/missing aspects, shipping weight sanity check,Estimated Net Payout (Lite) or Net Profit & Margin % (Pro), and the action_token. 
	2	ebay_audit_active_listing 
	◦	Inputs: ebay_item_id: str | None (defaults to most recent active listing created via eBay mobile Magical Listing). 
	◦	Behavior: Pulls the live listing snapshot, checks populated specifics against get_category_aspects, flags missing measurements or generic AI boilerplate descriptions that invite INAD returns, compares price against search_comps, and optionally captures cogs_cents / bin_code into SQLite. Stages a REVISE_LISTING row in pending_actions. 
	◦	Output: QA scorecard plus diff preview and action_token to apply fixes. 
	3	ebay_check_comps 
	◦	Inputs: query: str, category_id: str | None, condition: str | None, cogs_cents: int | None. 
	◦	Behavior: Read-only call to search_comps. Computes P25, median, and P75 active comp prices, typical shipping charges, and net seller proceeds after estimated fees (and COGS if present). 
	4	ebay_confirm_action 
	◦	Inputs: action_token: str, decision: Literal["APPROVE", "CANCEL"]. 
	◦	Behavior: Atomically loads pending_actions where action_token matches. Rejects if status is not PENDING,current UTC time exceeds expires_at, or recomputed SHA-256 of payload_json mismatches payload_sha256.On APPROVE, executes the corresponding EbayAdapter write, updates pending_actions.status = 'EXECUTED', updates items.status, and appends an item_events receipt. 
Phase 2 Pro Tools (Gated via check_fn)
Pass check_fn=is_pro_ops_enabled to ctx.register_tool() so casual C2C sessions never pay token overhead for warehouse tools until seller_profile.pro_ops_enabled == 1 or plugin config sets mode: pro:

	5	ebay_triage_offers 
	◦	Behavior: Calls list_best_offers, joins against local items.cogs_cents and floor_price_cents, filters out offers already in SellerAccept awaiting buyer payment, calculates net profit after fees, and stages RESPOND_OFFER approval tokens for accept/counter actions. 
	6	ebay_lookup_bin 
	◦	Behavior: Read-only fulfillment helper calling list_paid_orders via REST Fulfillment API, joining sold SKUs or item IDs against items.bin_code to return a sorted warehouse pick-list. 
6. Atomic Antigravity Task Breakdown & Verification Plan
Execute this build in six sequential task groups, generating verification artifacts after each step before advancing:

	1	Task 1 — Scaffold, Manifest & Models: Create directory tree, pyproject.toml, plugin.yaml, and models.py.Verify strict Pydantic v2 validation on currency cents, condition enums, and SKU generation. 
	2	Task 2 — Durable SQLite Ledger & Triggers: Implement db.py using plugin_db() with WAL mode,pending_actions SHA-256 digest generation, TTL expiration checks, and append-only item_events triggers. Run pytest tests/test_db_invariants.py and confirm updates/deletes on item_events raise sqlite3.IntegrityError. 
	3	Task 3 — Mock & Live EbayAdapter: Implement adapter/mock.py first with fixtures covering a Lite C2C user (no Business Policies), a Pro Seller Hub user, an active Magical Listing missing garment measurements, and a GetBestOffers response containing both Active and SellerAccept offers. Implement adapter/live.py using credentialed_provider_request and defusedxml, verifying zero references to decommissioned 2026 calls (UploadSiteHostedPictures, GetCategories, GetCategoryFeatures) in pytest tests/test_adapter_routing.py. 
	4	Task 4 — Phase 1 Tools & Two-Step Gate: Implement tools/phase1_mvp.py and wire register(ctx) in init.py.In pytest tests/test_approval_gate.py, prove that expired tokens, cancelled tokens, replayed tokens, and mutated JSON payloads all fail closed with zero adapter write calls. 
	5	Task 5 — Progressive Enrichment & Bundled Skill: Implement tools/phase2_ops.py behind check_fn=is_pro_ops_enabled and author skills/ebay-seller-ops/SKILL.md (ctx.register_skill) instructing Hermes how to parse voice-note flaws, COGS, and bins without hallucinating item specifics. Verify automatic Lite-to-Pro promotion in pytest tests/test_progressive_mode.py. 
	6	Task 6 — Hermes Plugin Doctor Sign-Off: Run the full pytest -q suite, then run hermes plugins validate . --install-deps inside a clean temporary HERMES_HOME directory and fix every manifest, capability, or security warning until validation exits clean.

