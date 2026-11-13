# bae — Technical Specification (`hermes-ebay-ops`)

Core architectural invariants, data schema, adapter contract, and tool specifications for `bae` (`hermes-ebay-ops`), an eBay seller operations companion plugin for Hermes Agent.

---

## 1. Architectural Invariants

Every module and test in `bae` enforces six core constraints:

- **Hermes Public Surface Only**: Register tools, hooks, skills, and UI routes strictly via `register(ctx)` and `@hermes/plugin-sdk`, storing profile-scoped SQLite data via `plugins.plugin_storage.plugin_db` without monkeypatching Hermes core.
- **Zero Raw Credential Access**: Never read `.env` files, token stores, or raw OAuth secrets in Python; declare OAuth in `plugin.yaml` under `oauth` and execute authenticated requests exclusively via `credentialed_provider_request`.
- **2026 eBay Deprecation Compliance**: Never call Trading XML endpoints decommissioned in 2026 — specifically `UploadSiteHostedPictures`, `GetCategories`, or `GetCategoryFeatures`.
- **Modern Endpoint Routing**: Route image uploads to REST Commerce Media (`createImageFromFile`), category aspects to REST Taxonomy and Metadata APIs, and paid orders to REST Fulfillment (`getOrders`) rather than legacy XML `GetOrders`.
- **Hard Two-Step Write Gate**: No live eBay write (publish, revise, policy bootstrap, offer response, or bulk publish) executes without an unexpired `action_token` verified against SHA-256 payload digests in SQLite.
- **Progressive Lite-to-Pro Enrichment**: Never require SKU, COGS, or storage bin inputs at intake; auto-mint hidden SKUs for casual C2C sellers and unlock Pro margin, offer-floor, and bin pick-list workflows automatically when operational fields appear.

---

## 2. Repository Layout & Hermes Manifest

Standalone Hermes v2 plugin layout:

```text
bae/
├── LICENSE
├── README.md
├── SPEC.md
├── plugin.yaml
├── pyproject.toml
├── __init__.py                 # register(ctx) entrypoint + check_fn wiring
├── config.py                   # Mode flags, fee defaults, token TTL settings
├── db.py                       # plugin_db() SQLite connection, schema init, ledger helpers
├── cache_store.py              # 7-day aspect cache + haul break-even batches
├── compliance.py               # Category sizing, condition, MPN, duplicate-title & VeRO checks
├── csv_drafts.py               # Seller Hub Draft CRLF CSV exporter & inkFrog/myFitment importer
├── diagnostics.py              # 90d+ stale-listing diagnostics, 96h unpaid offer guard, Q&A drafter
├── demo.py                     # Sample store tours isolated in tour.db
├── models.py                   # Strict Pydantic v2 models for tool I/O and adapter DTOs
├── adapter/
│   ├── base.py                 # EbayAdapter Protocol definition
│   ├── live.py                 # Live HTTP/XML client using credentialed_provider_request
│   └── mock.py                 # Deterministic offline fixtures for Lite and Pro accounts
├── tools/
│   ├── phase1_mvp.py           # stage_listing, audit_active_listing, check_comps, confirm_action, export_draft_csv
│   └── phase2_ops.py           # triage_offers, lookup_bin, import_migration_csv, diagnose_stale (gated via check_fn)
├── scripts/                    # Zero-token (--no-agent) scheduled monitoring scripts
├── skills/                     # 5 bundled SKILL.md playbooks (general ops + 4 category guides)
├── dashboard/                  # FastAPI backend API + 350px Side Pane bundle
├── desktop/                    # Full-screen 4-station Seller Desk (/bae)
└── tests/                      # Automated verification suite
```

---

## 3. Forward-Compatible SQLite Data Model

SQLite runs in `WAL` mode with `busy_timeout = 5000` inside Hermes's profile-scoped `plugin_db()` directory so seller ledgers survive plugin upgrades without storing credentials:

```sql
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
                                action_type IN (
                                    'PUBLISH_LISTING',
                                    'REVISE_LISTING',
                                    'BOOTSTRAP_POLICIES',
                                    'RESPOND_OFFER',
                                    'BULK_PUBLISH'
                                )
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
```

---

## 4. Unified `EbayAdapter` Contract & API Routing

eBay's split REST and XML surfaces sit behind a single Python `Protocol` in `adapter/base.py` so tool handlers never construct raw URLs or XML envelopes directly:

| Adapter Method | eBay API | Endpoint or Call | Seller Tier Role |
| :--- | :--- | :--- | :--- |
| `upload_media` | Media REST | `createImageFromFile` | Both (replaces decommissioned XML upload) |
| `get_category_aspects` | Taxonomy REST | `getItemAspectsForCategory` | Both (required & recommended specifics) |
| `search_comps` | Browse REST | `item_summary/search` & `search_by_image` | Both (active comp distribution) |
| `check_policy_status` | Account REST | `getOptedInPrograms` | Routes Inventory API vs. Trading XML |
| `publish_listing` / `revise_listing` | Inventory REST or Trading XML | `createOffer` / `AddFixedPriceItem` | Pro REST or C2C inline-policy fallback |
| `bulk_publish_listings` | Inventory REST | `bulkCreateOrReplaceInventoryItem` | Pro bulk migration (`<=25` SKUs/call) |
| `list_best_offers` | Trading XML | `GetBestOffers` (`defusedxml`) | Pro margin offer triage (`Active` vs `SellerAccept`) |
| `list_paid_orders` | Fulfillment REST | `getOrders` | Pro bin pick-list lookup |

Key adapter routing behaviors enforced in `adapter/live.py`:

- **Media Host Separation**: Multipart image uploads target `https://apiz.ebay.com/commerce/media/v1_beta/image/create_image_from_file` (or configured `media_base_url`) rather than `api.ebay.com`.
- **Casual C2C Policy Fallback**: Queries `getOptedInPrograms` and caches policy opt-in state in `seller_profile`. Accounts with `SELLING_POLICY_MANAGEMENT` publish through the Sell Inventory API (`createOrReplaceInventoryItem` -> `createOffer` -> `publishOffer`); casual C2C accounts can either stage a one-time `BOOTSTRAP_POLICIES` action or route single-item listings through `AddFixedPriceItem` with inline shipping and return settings.
- **2026 Best Offer Lifecycle**: Parses `GetBestOffers` XML via `defusedxml` and distinguishes `SellerAccept` (accepted by seller, item still live awaiting buyer payment) from `Active` (needs seller response) and `Accepted` (paid).

---

## 5. Hermes Tool Surface & Progressive Gating

### Core Tools (Always Active)

1. **`ebay_stage_listing`** — Auto-mints `HRM-YYYYMMDD-XXXX` when no SKU is supplied, checks category rules, computes fee/ad/dimensional-weight economics, flips `pro_ops_enabled = 1` if Pro inputs (`cogs_cents`, `bin_code`, `floor_price_cents`) are provided, and stages a `PUBLISH_LISTING` approval token.
2. **`ebay_audit_active_listing`** — Audits an active eBay listing against category specifics and comps, strips generic AI boilerplate descriptions, and stages a `REVISE_LISTING` approval token.
3. **`ebay_check_comps`** — Read-only active comp distribution (`P25`, median, `P75`) by keyword or image, joined against local sold history.
4. **`ebay_confirm_action`** — Verifies `PENDING` status, UTC expiration, and `payload_sha256` integrity before executing any staged write or cancelling it cleanly.
5. **`ebay_export_draft_csv`** — Exports staged items as a Windows `CRLF` CSV formatted for eBay Seller Hub Drafts (`75`-day draft retention).

### Pro Store Tools (`check_fn=is_pro_ops_enabled`)

6. **`ebay_triage_offers`** — Evaluates incoming Best Offers against `cogs_cents`, `floor_price_cents`, and ad-fee attribution; flags 96-hour unpaid `SellerAccept` offers; stages `RESPOND_OFFER` approval tokens.
7. **`ebay_lookup_bin`** — Builds a naturally sorted (`A-1` -> `A-2` -> `A-10`) warehouse pick-list for paid orders awaiting shipment.
8. **`ebay_import_migration_csv`** — Parses `inkFrog`, `myFitment` (Year/Make/Model compatibility), or Seller Hub CSV rows into staged items and stages batched `BULK_PUBLISH` actions (`<=25` SKUs per call).
9. **`ebay_diagnose_stale`** — Audits active listings aged 90+ days and recommends either in-place aspect updates (`ReviseFixedPriceItem`) or paced daily `End` + `Relist` batches (`<=5/day`).

---

## 6. Verification Suite

- `tests/test_db_invariants.py` — Verifies WAL mode, SHA-256 payload tamper rejection, token expiration, and append-only `item_events` SQLite triggers.
- `tests/test_adapter_routing.py` — Verifies zero references to decommissioned 2026 XML calls, Media host separation, C2C vs. Pro routing, and `SellerAccept` parsing.
- `tests/test_approval_gate.py` — Proves expired, cancelled, replayed, and mutated tokens fail closed with zero adapter write calls.
- `tests/test_progressive_mode.py` — Verifies Lite-by-default tool gating and automatic Pro promotion when COGS, floor price, or bin codes appear.
- `tests/test_ui_api.py` & `tests/test_report_alignment_2026.py` — Verifies Seller Desk FastAPI routes, sample store isolation (`tour.db`), USPS `139` dimensional weight math, category compliance guards, CSV import/export, and `--no-agent` scheduled scripts.
