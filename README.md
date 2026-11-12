# Vendere (`hermes-ebay-ops`)

**Multimodal listing QA, 2026 unit economics, displaced-seller migration, and store operations desk for eBay sellers — built natively for [Hermes Agent](https://github.com/NousResearch/hermes-agent).**

Vendere pairs conversational **Snap-and-Speak** listing intake with a full **Editorial Geist Seller Desk** (`desktop/plugin.js`) and a compact **350px Hermes Side Pane** (`dashboard/dist/index.js`). Every live eBay mutation is gated behind a cryptographic **SHA-256 + 15-minute TTL human approval token**, keeping sellers safe from unapproved AI edits, Magical Listing fluff, dimensional-shipping clawbacks, and 2026 category policy blocks.

---

## Highlights

- **Snap-and-Speak Listing Intake & Magical Listing Repair**: Drop product photos + quick voice/text notes into Hermes to stage Cassini-dense 80-character titles, ruler-verified specifics, and facts-only condition copy — or audit active Magical Listings to strip generic AI filler (`"stunning rare gem"`) and close Item Not As Described (`INAD`) gaps.
- **2026 Net-Margin & USPS `139`-DIM Shipping Engine**:
  - Models **July 12, 2026 USPS `139` dimensional weight** (`ceil(L×W×H / 139)` lbs for parcels `>1,728 cu in`), `>48 in` length surcharges, and stocked-box fitment (`BOX-PARKA`, `BOX-SHOE`, `POLY-10X13`).
  - Models **January 13, 2026 Promoted Listings General (`PLG`) 30-day attribution** (`0% / 5% / 8%`), `~7%` buyer sales-tax FVF basis, `"Very High"` INAD (`+5%`) risk, and the **\$200–\$500 Ship With eBay fashion protection cap**.
- **5 Specialized Category `SKILL.md` Playbooks & Deterministic Pre-Screens**:
  - Auto-routes by leaf category across `ebay-seller-ops`, `ebay-apparel-footwear` (Aug 2026 Size Standardization + `2990/3000/3010` condition IDs), `ebay-motors-fitment` (OEM `MPN` front-load, YMM compatibility replacing shuttered `myFitment`, `>\$10` 30-day free returns), `ebay-vintage-collectibles` (**Catalog-Number Defense** moving mold/box numbers out of `MPN` into `Pattern`), and `ebay-cards-coins-media` (PSA/PCGS certs & May 2026 coin grading), plus Nov 26, 2026 duplicate active fixed-price title and VeRO/watermark checks.
- **Dual Publishing Paths & Orphaned Tool Migration**:
  - Publish live via eBay's **Sell Inventory REST API** (or hybrid Trading XML fallback for non-opted-in casual C2C accounts), **or** export strict Windows `CRLF` (`\r\n`) **Seller Hub Draft CSVs** (`Action=Draft`, 75-day Drafts, `BAF.Error.5`-safe single-line cells).
  - Migrate shuttered **`inkFrog`** HTML listings and multi-row **`myFitment`** Year/Make/Model CSVs directly into staged `<=25 SKUs/call` Bulk Inventory API batches (`BULK_PUBLISH`).
- **Progressive Lite-to-Pro Seller Studio**:
  - Casual C2C sellers never touch SKUs, COGS, or warehouse bins (hidden `HRM-YYYYMMDD-XXXX` SKUs mint automatically).
  - Entering `cogs_cents`, `bin_code`, or `floor_price_cents` automatically unlocks **Pro Ops**: Best Offer floor triage (including March 2026 `SellerAccept` unpaid-96h holds), natural-sorted warehouse bin pick-lists, **90–180d Cassini stale-listing diagnostics**, and **Haul / Death-Pile break-even velocity tracking**.
- **Zero-Token (`--no-agent`) Scheduled Cron Blueprints**:
  - Four bundled standalone scripts in `scripts/` output `[SILENT]` when clear (`\$0.00` LLM token burn) and surface concise digests only when stale listings, active offers, buyer Q&A messages, or evening 3-item death-pile sprints need attention.

---

## Seller Desk Surfaces

Vendere ships two synchronized Hermes UI surfaces backed by FastAPI routes in `dashboard/plugin_api.py`:

1. **Desktop Seller Studio (`/vendere` · `desktop/plugin.js`)** — Four focused stations:
   - **`01 · Morning Ops`**: Haul & Death-Pile break-even velocity bar, 90–180d Cassini stale-listing triage (`IN_PLACE_ASPECT_UPGRADE` vs. `END_AND_RECREATE_5PCT`), cryptographic Two-Step Approval Queue, Best Offer floor comparison bars, and natural-sorted bin pick-lists with printable parcel tear slips.
   - **`02 · Listings`**: Searchable catalog across Drafts, Active, Needs QA, and Sold items, with `ItemSheet` quality audits, category playbook & `139`-DIM badges, net-after-PLG/INAD breakdowns, and 1-click `CRLF Draft CSV` export.
   - **`03 · Listing Bench`**: Photo & condition intake, 80-character Cassini title density meter, live P25/Median/P75 comp slider with `0%/5%/8% PLG` + `~7%` buyer-tax FVF toggles, stocked box `139`-DIM calculator, and collapsible `inkFrog` / `myFitment` CSV importer.
   - **`04 · Setup & Logs`**: Store capability & policy-lock status, `5,000/day` SQLite Taxonomy cache indicator, interactive `--no-agent` cron runner (`Run Check`), 5 bundled `SKILL.md` playbooks, `@ebay/npm-public-api-mcp` OpenAPI pairing, Dec 31, 2026 Partner Program cutoff checklist, and immutable audit log.
2. **Hermes Side Pane (`dashboard/dist/index.js`)** — 350px drawer alongside chat for instant store health, countdown approval buttons, `CRLF Draft CSV` export, and quick tour switching (`Casual C2C Tour` vs. `Pro Store Tour`).

---

## Quick Start

### 1. Install & Validate in Hermes

```bash
# Validate manifest, tool schemas, security scan, and desktop SDK surface
hermes plugins validate /path/to/vendere
```

Copy or symlink the repository into `~/.hermes/plugins/hermes-ebay-ops` (or install via your Hermes plugins directory).

### 2. Connect Your eBay Account (Optional for Sandbox Tours)

Vendere never reads `.env` files or raw tokens in Python. Set `EBAY_PLUGIN_CLIENT_ID` and `EBAY_PLUGIN_CLIENT_SECRET` in your shell environment, then authenticate through Hermes's encrypted OAuth broker:

```bash
hermes auth add hermes-ebay-ops:ebay
```

> Want to explore without eBay credentials? Open the **Vendere** Seller Desk in Hermes — it includes zero-credential **Casual C2C** and **Pro Reseller** interactive tours isolated in `tour.db` so your real `data.db` ledger stays untouched.

### 3. Run Tests Locally

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]" fastapi httpx

# Syntax-check UI bundles + run full pytest suite
node --input-type=module --check < desktop/plugin.js
node --input-type=module --check < dashboard/dist/index.js
.venv/bin/pytest -q
```

---

## Registered Tools (`toolset="ebay_ops"`)

| Tool | Mode | Write Gate | Purpose |
| :--- | :--- | :--- | :--- |
| `ebay_stage_listing` | Core (Lite + Pro) | Stages token | Upload/verify photos, resolve leaf category & cached aspects, run 2026 category compliance + `139`-DIM/PLG math, and stage a `PUBLISH_LISTING` (or `BOOTSTRAP_POLICIES`) approval token. |
| `ebay_audit_active_listing` | Core (Lite + Pro) | Stages token | Audit an active eBay listing for Magical Listing AI boilerplate, missing specifics, category compliance risks, and comp drift; stages `REVISE_LISTING`. |
| `ebay_check_comps` | Core (Lite + Pro) | Read-only | Query Buy Browse active comps (`<=100`-char normalized query or `search_by_image` visual match) cross-checked against local realized `SOLD` prices. |
| `ebay_confirm_action` | Core (Lite + Pro) | **Executes token** | Verify unexpired 15-min TTL + SHA-256 payload digest and execute `PUBLISH_LISTING`, `REVISE_LISTING`, `BOOTSTRAP_POLICIES`, `RESPOND_OFFER`, or `BULK_PUBLISH`. |
| `ebay_export_draft_csv` | Core (Lite + Pro) | Local file | Export staged/draft SKUs into a strict Windows `CRLF` (`\r\n`) Seller Hub `Action=Draft` CSV with single-line sanitized cells (`BAF.Error.5`-safe). |
| `ebay_triage_offers` | Pro (`check_fn`) | Stages token | Evaluate `Active` Best Offers against floor price & 5% PLG net margin, flag `>=96h` unpaid `SellerAccept` holds, or stage `RESPOND_OFFER`. |
| `ebay_lookup_bin` | Pro (`check_fn`) | Read-only | Build a natural-sorted (`A-1` -> `A-2` -> `A-10`) warehouse pick-list joining paid Fulfillment orders with local `bin_code` locations. |
| `ebay_import_migration_csv` | Pro (`check_fn`) | Stages token | Parse orphaned `inkFrog`, `myFitment` (multi-row YMM fitment), or Seller Hub CSVs into local staged items + a single `BULK_PUBLISH` (`<=25 SKUs/call`) token. |
| `ebay_diagnose_stale` | Pro (`check_fn`) | Read-only | Run 90–180d Cassini stale-listing diagnostics (`IN_PLACE_ASPECT_UPGRADE` vs. gentle 5% daily `END_AND_RECREATE_5PCT`) and Haul break-even velocity summaries. |

---

## Repository Layout

```text
vendere/
├── README.md
├── SPEC.md                          # Original architectural constitution
├── plugin.yaml                      # Hermes v2 manifest (OAuth, config schema, 9 tools)
├── pyproject.toml
├── __init__.py                      # register(ctx) entrypoint + progressive Pro check_fn
├── config.py                        # Mode flags, fee defaults, token TTL settings
├── models.py                        # Strict Pydantic v2 DTOs & 2026 condition/DIM models
├── db.py                            # 4-table WAL SQLite ledger (seller_profile, items, pending_actions, item_events)
├── cache_store.py                   # Auxiliary SQLite store for 7-day Taxonomy aspect cache & Haul batches
├── compliance.py                    # Deterministic 2026 category & content compliance engine
├── csv_drafts.py                    # Windows CRLF Seller Hub Draft exporter & inkFrog/myFitment CSV parser
├── diagnostics.py                   # 90-180d Cassini stale diagnostics, 96h unpaid offer guard, Q&A drafter
├── demo.py                          # Non-destructive Casual C2C & Pro Vault sample tours (tour.db)
├── adapter/
│   ├── base.py                      # EbayAdapter Protocol
│   ├── live.py                      # Live REST + hybrid Trading XML adapter via credentialed_provider_request
│   └── mock.py                      # Deterministic offline fixtures for Lite and Pro personas
├── tools/
│   ├── __init__.py                  # Economics (PLG + buyer tax FVF + INAD) & July 2026 139-DIM helpers
│   ├── phase1_mvp.py                # Core listing, QA audit, comp check, CRLF CSV, and confirm_action gate
│   └── phase2_ops.py                # Pro offer triage, bin pick-list, CSV migration, and stale diagnostics
├── scripts/                         # Zero-LLM-token (--no-agent) scheduled cron scripts ([SILENT] guard)
│   ├── stale_listing_diagnostics.py
│   ├── offer_and_unpaid_triage.py
│   ├── buyer_qa_inbox.py
│   └── haul_micro_sprint.py
├── skills/                          # 5 bundled Hermes skills (core ops + 4 category playbooks)
│   ├── ebay-seller-ops/SKILL.md
│   ├── ebay-apparel-footwear/SKILL.md
│   ├── ebay-motors-fitment/SKILL.md
│   ├── ebay-vintage-collectibles/SKILL.md
│   └── ebay-cards-coins-media/SKILL.md
├── dashboard/
│   ├── plugin_api.py                # FastAPI backend routes for Desktop Studio & Side Pane
│   └── dist/index.js                # 350px Hermes Dashboard Side Pane bundle
├── desktop/
│   └── plugin.js                    # Full-screen 4-station Editorial Geist Seller Studio
├── docs/                            # Detailed architecture, 2026 policy math, & operations guides
│   ├── architecture-and-security.md
│   ├── 2026-ebay-playbook-and-economics.md
│   └── migration-cron-and-setup.md
└── tests/                           # 29-test automated verification suite
```

---

## Documentation

- [**Architecture, Security & Endpoint Routing**](docs/architecture-and-security.md) — Constitutional invariants, SHA-256 two-step write gate, 4-table SQLite ledger, and 2026 REST vs. hybrid XML routing.
- [**2026 eBay Playbooks & Unit Economics**](docs/2026-ebay-playbook-and-economics.md) — USPS `139`-DIM math, Jan 2026 PLG attribution, category condition/size mandates, and Catalog-Number Defense.
- [**CSV Migration, Seller Hub Drafts & Zero-Token Cron Setup**](docs/migration-cron-and-setup.md) — `inkFrog`/`myFitment` bulk onboarding, CRLF Draft CSV workflow, `@ebay/npm-public-api-mcp` pairing, Dec 31, 2026 Partner cutoff, and `--no-agent` cron schedules.
- [**Original Engineering Specification (`SPEC.md`)**](SPEC.md)

## License

MIT
