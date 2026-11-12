# Architecture, Security & 2026 Endpoint Routing

Vendere (`hermes-ebay-ops`) is engineered around six non-negotiable architectural invariants so that AI-assisted eBay selling never compromises account credentials, calls retired eBay APIs, or mutates live inventory without explicit human consent.

---

## 1. Six Constitutional Invariants

1. **Hermes Public Surface Only**:
   - Tools, skills, and settings register strictly through `register(ctx)` in `__init__.py`.
   - Profile-scoped state lives inside `plugins.plugin_storage.plugin_db("hermes-ebay-ops")` and `plugin_data_dir("hermes-ebay-ops")` — zero monkeypatching of Hermes core internals.
2. **Zero Raw Credential Access**:
   - Production Python code contains **zero** `os.environ`, `os.getenv`, or `dotenv` reads.
   - OAuth scopes and allowed hosts (`api.ebay.com`, `apiz.ebay.com`, `apim.ebay.com`) are declared in `plugin.yaml`, and every upstream HTTP call executes through Hermes's `credentialed_provider_request("hermes-ebay-ops:ebay", ...)`.
3. **2026 eBay XML Deprecation Compliance**:
   - Decommissioned Trading XML calls — `UploadSiteHostedPictures`, `GetCategories`, `GetCategoryFeatures`, and `GetOrders` — are strictly prohibited across the codebase and verified by automated static tests.
4. **Modern REST-First + Targeted Hybrid XML Routing**:
   - Uses modern eBay REST APIs wherever supported, falling back to `defusedxml`-guarded Trading XML only for casual C2C accounts that have not opted into eBay Business Policies (`AddFixedPriceItem` / `ReviseFixedPriceItem`) and for Best Offer negotiation (`GetBestOffers` / `RespondToBestOffer`).
5. **Cryptographic Two-Step Human Write Gate**:
   - Every live eBay write (`PUBLISH_LISTING`, `REVISE_LISTING`, `BOOTSTRAP_POLICIES`, `RESPOND_OFFER`, `BULK_PUBLISH`) stages a canonical JSON payload in `pending_actions` with a `SHA-256` digest and 15-minute `expires_at` window.
   - Only `ebay_confirm_action(action_token, user_approved=True)` (or clicking **Approve** in the Seller Desk) executes the upstream write after re-verifying both the TTL and SHA-256 digest inside an `IMMEDIATE` SQLite transaction.
6. **Progressive Lite-to-Pro Enrichment**:
   - Casual C2C sellers never enter SKUs, COGS, floor prices, or bin locations; Vendere mints hidden `HRM-YYYYMMDD-XXXX` SKUs automatically.
   - Supplying `cogs_cents`, `bin_code`, or `floor_price_cents` flips `seller_profile.pro_ops_enabled = 1`, dynamically unlocking `ebay_triage_offers`, `ebay_lookup_bin`, `ebay_import_migration_csv`, and `ebay_diagnose_stale` via `check_fn=is_pro_ops_enabled`.

---

## 2. Two-Step Write Gate Lifecycle

```text
[Agent / UI Bench]
       │
       ▼
Stage Action (stage_listing / audit_active_listing / triage_offers / import_migration_csv)
       │  • Canonical JSON serialization (sort_keys=True)
       │  • payload_sha256 = sha256(payload_json)
       │  • action_token = 128-bit hex token (15-min TTL)
       │  • Zero live eBay write calls executed
       ▼
[SQLite pending_actions (status = 'PENDING')]
       │
       ▼
Human Reviews Preview Voucher (Chat Markdown Card, Desktop Morning Ops, or 350px Side Pane)
       │
       ├── Cancel / Expired ──► status = 'CANCELLED' or 'EXPIRED' (0 writes)
       │
       └── Approve ──► ebay_confirm_action(action_token, user_approved=True)
                             │  • BEGIN IMMEDIATE lock
                             │  • Verify now < expires_at
                             │  • Verify sha256(payload_json) == payload_sha256
                             ▼
                       Execute Live EbayAdapter Write
                             │
                             ▼
                       status = 'EXECUTED' + append immutable item_events audit row
```

---

## 3. SQLite Storage Architecture (`data.db` & `cache.db`)

### Core Transactional Ledger (`data.db` — 4 Tables)

Initialized in `db.py` (`WAL` mode when supported by host SQLite, `busy_timeout = 5000`, `foreign_keys = ON`):

- **`seller_profile`**: Singleton capability & progressive-mode state (`marketplace_id`, `policies_opted_in`, locked `fulfillment_policy_id` / `payment_policy_id` / `return_policy_id`, `merchant_location_key`, `pro_ops_enabled`, `estimated_fee_bps`).
- **`items`**: Unified Lite + Pro inventory record (`sku`, `ebay_item_id`, `offer_id`, `source_mode`, `status`, `title`, `category_id`, `condition_enum`, `list_price_cents`, `shipping_est_cents`, `package_weight_oz`, `package_dims_json`, `aspects_json`, `flaws_disclosure`, `media_urls_json`, plus nullable `cogs_cents`, `bin_code`, `floor_price_cents`, `listed_at`).
- **`pending_actions`**: Cryptographic write-gate queue (`action_token`, `action_type`, `sku`, `payload_json`, `payload_sha256`, `preview_card_md`, `status`, `expires_at`, `created_at`, `executed_at`).
- **`item_events`**: Append-only audit log (`event_id`, `sku`, `event_type`, `actor`, `detail_json`, `created_at`).

### Auxiliary Quota & Velocity Store (`cache.db`)

Managed in `cache_store.py` so `data.db` remains strictly at its four core ledger tables:

- **`aspect_cache`**: Caches `get_item_aspects_for_category` Taxonomy responses for **7 days** (`604,800s`), protecting eBay's default `5,000 calls/day` Basic Access tier from repetitive category-metadata lookups.
- **`haul_batches`**: Tracks estate/thrift haul batch cost (`total_haul_cogs_cents`), target piece counts, break-even countdowns, and 3-item (`~9 min`) micro-sprint eBay Balance unlocks.

> **Non-Destructive Tours (`tour.db` & `tour_cache.db`)**: Switching into the **Casual C2C** or **Pro Store** tour in the UI writes `tour_state.json` and routes reads/writes to `tour.db` + `tour_cache.db`. Clicking **Exit** immediately restores the seller's real `data.db` ledger.

---

## 4. 2026 eBay Upstream Routing Matrix

| Capability | Upstream API | Host & Endpoint | Notes |
| :--- | :--- | :--- | :--- |
| **Photo Upload** | Commerce Media REST | `POST https://apiz.ebay.com/commerce/media/v1_beta/image/create_image_from_file` | Replaces retired `UploadSiteHostedPictures`. Validates JPEG/PNG/WEBP magic bytes before upload. |
| **Leaf Category & Item Specifics** | Commerce Taxonomy REST | `GET https://api.ebay.com/commerce/taxonomy/v1/category_tree/{id}/get_category_suggestions` & `get_item_aspects_for_category` | Replaces retired `GetCategories` / `GetCategoryFeatures`. Cached 7 days in `cache.db`. |
| **Condition Policies** | Sell Metadata REST | `GET https://api.ebay.com/sell/metadata/v1/marketplace/{id}/get_item_condition_policies` | Maps 2026 fashion condition IDs (`2990`, `3000`, `3010`) and standard condition enums. |
| **Active & Visual Comps** | Buy Browse REST | `GET /buy/browse/v1/item_summary/search` & `POST /buy/browse/v1/item_summary/search_by_image` | Truncates `q` to `<=100` chars at word boundary; blends with local realized `SOLD` prices. |
| **Single & Bulk Inventory Publish** | Sell Inventory REST | `PUT /sell/inventory/v1/inventory_item/{sku}`, `POST /offer`, `POST /offer/{id}/publish`, `POST /bulk_create_or_replace_inventory_item`, `POST /bulk_create_offer` | Primary route when `policies_opted_in == True`. Chunks bulk migrations at `<=25 SKUs/call`. |
| **Business Policies** | Sell Account REST | `GET /sell/account/v1/fulfillment_policy`, `/payment_policy`, `/return_policy` | Auto-detects opt-in status and locks explicit `fulfillmentPolicyId` so eBay never swaps shipping services. |
| **Paid Order Pick-List** | Sell Fulfillment REST | `GET https://api.ebay.com/sell/fulfillment/v1/order` | Replaces retired `GetOrders` XML; joins line-item SKUs against local `bin_code` locations. |
| **Casual C2C Fallback & Best Offers** | Trading XML (`v1421+`) | `POST https://api.ebay.com/ws/api.dll` (`AddFixedPriceItem`, `ReviseFixedPriceItem`, `GetBestOffers`, `RespondToBestOffer`) | Parses responses exclusively with `defusedxml.ElementTree`. Tracks March 2026 `SellerAccept` unpaid-offer state. |
