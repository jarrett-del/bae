(function () {
  "use strict";
  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK || !window.__HERMES_PLUGINS__) return;
  var React = SDK.React;
  var useState = SDK.hooks.useState,
    useEffect = SDK.hooks.useEffect,
    useRef = SDK.hooks.useRef,
    useCallback = SDK.hooks.useCallback;
  var h = React.createElement;
  var API = "/api/plugins/hermes-ebay-ops";
  var POLL_MS = 4000;

  function get(path) {
    return SDK.fetchJSON(API + path);
  }
  function post(path, body) {
    return SDK.fetchJSON(API + path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {})
    });
  }

  function usd(cents) {
    if (cents === null || cents === undefined || isNaN(cents)) return "—";
    var n = Number(cents) / 100;
    var sign = n < 0 ? "-" : "";
    var abs = Math.abs(n);
    if (abs >= 1000) return sign + "$" + abs.toLocaleString(undefined, { minimumFractionDigits: 0, maximumFractionDigits: 0 });
    return sign + "$" + abs.toFixed(2);
  }
  function usdCompact(cents) {
    var n = Math.abs(Number(cents || 0)) / 100;
    if (n >= 1e6) return "$" + (n / 1e6).toFixed(1) + "M";
    if (n >= 1e4) return "$" + (n / 1e3).toFixed(1) + "k";
    if (n >= 1000) return "$" + Math.round(n).toLocaleString();
    return "$" + n.toFixed(0);
  }
  function minsLeft(sec) {
    var s = Math.max(0, Number(sec || 0));
    var m = Math.floor(s / 60);
    var r = s % 60;
    return m + ":" + (r < 10 ? "0" : "") + r;
  }
  function ago(iso) {
    var t = Date.parse(iso || "");
    if (!t) return "";
    var s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 60) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  function actionLabel(t) {
    return {
      PUBLISH_LISTING: "Publish Listing",
      REVISE_LISTING: "Revise Listing (QA)",
      RESPOND_OFFER: "Respond to Offer",
      BOOTSTRAP_POLICIES: "Bootstrap Policies",
      BULK_PUBLISH: "Bulk Publish (≤25 SKUs)"
    }[t] || t;
  }

  function downloadDraftCsv(csvText, filename) {
    if (typeof window === "undefined" || typeof Blob === "undefined") return;
    var blob = new Blob([String(csvText || "")], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename || "bae-seller-hub-drafts.csv";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1500);
  }

  function Btn(props) {
    return h(
      "button",
      {
        type: "button",
        className: "vd-btn",
        "data-vd-kind": props.kind || "",
        disabled: props.disabled,
        onClick: props.onClick,
        title: props.title
      },
      props.children
    );
  }
  function Badge(props) {
    return h("span", { className: "vd-badge", "data-vd-tone": props.tone || "" }, props.children);
  }

  function Hero(props) {
    var s = props.state,
      t = s.totals,
      pro = s.mode === "pro";
    var steps = [
      { idx: "01", n: t.staged_count, label: "Staged", sub: t.pending_approvals_count + " awaiting token", view: "approvals" },
      { idx: "02", n: t.active_count, label: "Live on eBay", sub: usdCompact(t.active_value_cents) + " listed", view: "inventory" },
      { idx: "03", n: s.offers.length, label: "Best Offers", sub: t.awaiting_payment_offers_count ? t.awaiting_payment_offers_count + " awaiting pay" : "floor guarded", view: "offers" },
      { idx: "04", n: t.unpacked_pick_count, label: "To Ship", sub: t.pick_list_count + " paid orders", view: "pick" }
    ];
    return h(
      "section",
      { className: "vd-hero" },
      h(
        "div",
        null,
        h(
          "div",
          { className: "vd-eyebrow" },
          h("span", { style: { textTransform: "none", fontWeight: 700, fontSize: "13px", letterSpacing: "-0.01em", color: "var(--vd-ink)" } }, "bae"),
          h("span", null, "//"),
          pro ? "your eBay sidekick · pro" : "your eBay sidekick · c2c",
          h("span", { className: "vd-live" }, h("i", { className: "vd-live-dot" }), props.live ? "Live" : "Offline")
        ),
        h(
          "h1",
          { className: "vd-title" },
          usdCompact(t.active_value_cents || t.staged_value_cents),
          h("em", null, t.active_value_cents ? "in play" : "staged")
        ),
        h(
          "p",
          { className: "vd-sub" },
          h("span", null, h("b", null, t.active_count), " active"),
          h("span", null, h("b", null, t.pending_approvals_count), " approvals"),
          h("span", null, h("b", null, t.qa_alerts_count), " qa alerts"),
          pro && t.avg_margin_pct !== null
            ? h("span", null, h("b", null, t.avg_margin_pct + "%"), " avg margin")
            : h("span", null, h("b", null, usdCompact(t.est_net_proceeds_cents)), " est. payout")
        )
      ),
      h(
        "div",
        { className: "vd-steps" },
        steps.map(function (st) {
          return h(
            "button",
            {
              key: st.idx,
              className: "vd-step",
              "data-vd-empty": st.n ? "0" : "1",
              onClick: function () { props.setView(st.view); }
            },
            h("div", { className: "vd-step-idx" }, st.idx),
            h("div", { className: "vd-step-num" }, st.n),
            h("small", null, st.label),
            h("span", null, st.sub)
          );
        })
      )
    );
  }

  function Attention(props) {
    var s = props.state;
    var pending = s.pending_actions || [];
    var qaItems = (s.items || []).filter(function (i) { return i.qa_flags && i.qa_flags.length > 0 && i.status !== "SOLD"; });
    var opsRows = [];
    (s.offers || []).forEach(function (o) {
      opsRows.push({
        key: "off-" + o.best_offer_id,
        tone: o.is_awaiting_payment ? "warn" : (o.meets_floor ? "good" : "warn"),
        title: usd(o.price_cents) + " · " + o.buyer_username,
        sub: o.title,
        right: o.is_awaiting_payment ? "SellerAccept" : o.recommendation,
        click: function () { props.setView("offers"); }
      });
    });
    (s.pick_list || []).filter(function (p) { return !p.packed; }).slice(0, 3).forEach(function (p) {
      opsRows.push({
        key: "pk-" + p.order_id,
        tone: "good",
        title: "Bin " + p.bin_code,
        sub: p.title,
        right: "paid",
        click: function () { props.setView("pick"); }
      });
    });

    return h(
      "section",
      { className: "vd-attn" },
      h(
        "div",
        { className: "vd-panel" },
        h("div", { className: "vd-h" }, "Awaiting Approval", h("b", null, pending.length)),
        pending.length
          ? pending.slice(0, 5).map(function (p) {
              return h(
                "button",
                {
                  key: p.action_token,
                  className: "vd-row",
                  onClick: function () { props.openToken(p); }
                },
                h("i", { className: "vd-dot", "data-vd-tone": "warn" }),
                h("strong", null, actionLabel(p.action_type)),
                h("span", null, p.item_title),
                h("em", null, minsLeft(p.seconds_remaining))
              );
            })
          : h("div", { className: "vd-empty" }, "Zero pending write tokens.")
      ),
      h(
        "div",
        { className: "vd-panel" },
        h("div", { className: "vd-h" }, "QA & Anti-INAD Watch", h("b", null, qaItems.length)),
        qaItems.length
          ? qaItems.slice(0, 5).map(function (it) {
              return h(
                "button",
                {
                  key: it.sku,
                  className: "vd-row",
                  onClick: function () { props.openItem(it.sku); }
                },
                h("i", { className: "vd-dot", "data-vd-tone": it.qa_score < 75 ? "bad" : "warn" }),
                h("strong", null, it.title),
                h("span", null, it.qa_flags[0]),
                h("em", null, "QA " + it.qa_score)
              );
            })
          : h("div", { className: "vd-empty" }, "All active listings pass ruler & fluff checks.")
      ),
      h(
        "div",
        { className: "vd-panel" },
        h("div", { className: "vd-h" }, "Offers & Bin Fulfillment", h("b", null, opsRows.length)),
        opsRows.length
          ? opsRows.slice(0, 5).map(function (r) {
              return h(
                "button",
                { key: r.key, className: "vd-row", onClick: r.click },
                h("i", { className: "vd-dot", "data-vd-tone": r.tone }),
                h("strong", null, r.title),
                h("span", null, r.sub),
                h("em", null, r.right)
              );
            })
          : h("div", { className: "vd-empty" }, "No open offers or unpicked paid orders.")
      )
    );
  }

  function InventoryBoard(props) {
    var pro = props.state.mode === "pro";
    var q = (props.query || "").toLowerCase();
    var items = (props.state.items || []).filter(function (i) {
      if (!q) return true;
      return [i.sku, i.title, i.bin_code || "", i.condition_enum].join(" ").toLowerCase().indexOf(q) !== -1;
    });
    var cols = [
      { key: "STAGED", label: "Staged Drafts", rows: items.filter(function (i) { return i.status === "STAGED"; }) },
      { key: "ACTIVE", label: "Active on eBay", rows: items.filter(function (i) { return i.status === "ACTIVE"; }) },
      { key: "SOLD", label: "Sold / Paid", rows: items.filter(function (i) { return i.status === "SOLD"; }) }
    ];
    return h(
      "div",
      { className: "vd-cols" },
      cols.map(function (col) {
        var sum = col.rows.reduce(function (acc, r) { return acc + r.list_price_cents; }, 0);
        return h(
          "div",
          { key: col.key, className: "vd-col" },
          h("div", { className: "vd-colhead" }, h("h4", null, col.label), h("span", null, col.rows.length + " · " + usdCompact(sum))),
          col.rows.length
            ? col.rows.map(function (it) {
                return h(
                  "div",
                  {
                    key: it.sku,
                    className: "vd-card",
                    onClick: function () { props.openItem(it.sku); }
                  },
                  h(
                    "div",
                    { className: "vd-card-top" },
                    h("span", null, it.sku + (it.bin_code ? " · BIN " + it.bin_code : "")),
                    h("span", { className: "vd-card-price" }, usd(it.list_price_cents))
                  ),
                  h("div", { className: "vd-card-title" }, it.title),
                  h(
                    "div",
                    { className: "vd-card-meta" },
                    pro && it.margin_pct !== null
                      ? h(Badge, { tone: it.margin_pct >= 25 ? "good" : "warn" }, "+" + usd(it.net_profit_cents) + " (" + it.margin_pct + "%)")
                      : h("span", null, "Net " + usd(it.estimated_net_payout_cents)),
                    h(Badge, { tone: it.qa_score >= 85 ? "good" : "warn" }, "QA " + it.qa_score),
                    it.dim_check && it.dim_check.dim_triggered
                      ? h(Badge, { tone: "warn" }, "139-DIM " + it.dim_check.billable_lbs + "lb")
                      : null,
                    it.pending_actions && it.pending_actions.length
                      ? h(Badge, { tone: "warn" }, "Token Pending")
                      : null
                  )
                );
              })
            : h("div", { className: "vd-empty" }, "None in this column.")
        );
      })
    );
  }

  function ApprovalsTable(props) {
    var list = props.state.pending_actions || [];
    if (!list.length) {
      return h("div", { className: "vd-panel vd-empty" }, "No pending write actions. Stage a draft or run a Magical Listing QA audit.");
    }
    return h(
      "div",
      { className: "vd-table-wrap" },
      h(
        "table",
        { className: "vd-table" },
        h(
          "thead",
          null,
          h("tr", null, h("th", null, "Action"), h("th", null, "SKU / Item"), h("th", null, "SHA-256"), h("th", null, "TTL"), h("th", null, "Gate Decision"))
        ),
        h(
          "tbody",
          null,
          list.map(function (p) {
            return h(
              "tr",
              { key: p.action_token },
              h("td", null, h(Badge, { tone: "warn" }, actionLabel(p.action_type))),
              h(
                "td",
                null,
                h("strong", { style: { display: "block", cursor: "pointer" }, onClick: function () { props.openToken(p); } }, p.item_title),
                h("span", { className: "vd-mono", style: { opacity: 0.7 } }, p.sku || "—")
              ),
              h("td", { className: "vd-mono" }, p.payload_sha256 + "…"),
              h("td", { className: "vd-mono" }, minsLeft(p.seconds_remaining)),
              h(
                "td",
                null,
                h(
                  "div",
                  { style: { display: "flex", gap: 6 } },
                  h(Btn, { kind: "primary", onClick: function () { props.decide(p.action_token, "APPROVE"); } }, "Approve"),
                  h(Btn, { kind: "danger", onClick: function () { props.decide(p.action_token, "CANCEL"); } }, "Cancel")
                )
              )
            );
          })
        )
      )
    );
  }

  function OffersTable(props) {
    var offers = props.state.offers || [];
    if (!offers.length) {
      return h("div", { className: "vd-panel vd-empty" }, "No open Best Offers right now.");
    }
    return h(
      "div",
      { className: "vd-table-wrap" },
      h(
        "table",
        { className: "vd-table" },
        h(
          "thead",
          null,
          h("tr", null, h("th", null, "Item / Buyer"), h("th", null, "Offer vs Floor"), h("th", null, "Net Profit"), h("th", null, "2026 Lifecycle"), h("th", null, "Action"))
        ),
        h(
          "tbody",
          null,
          offers.map(function (o) {
            return h(
              "tr",
              { key: o.best_offer_id },
              h(
                "td",
                null,
                h("strong", { style: { display: "block" } }, o.title),
                h("span", { className: "vd-mono", style: { opacity: 0.72 } }, o.buyer_username + (o.buyer_message ? ' · "' + o.buyer_message + '"' : ""))
              ),
              h(
                "td",
                { className: "vd-mono" },
                usd(o.price_cents) + " / " + (o.floor_price_cents ? "Floor " + usd(o.floor_price_cents) : "List " + usd(o.list_price_cents))
              ),
              h(
                "td",
                { className: "vd-mono" },
                o.net_profit_cents !== null ? usd(o.net_profit_cents) + " (" + o.margin_pct + "%)" : usd(o.estimated_net_payout_cents) + " net"
              ),
              h(
                "td",
                null,
                o.is_awaiting_payment
                  ? h(Badge, { tone: "warn" }, "SellerAccept · Unpaid")
                  : h(Badge, { tone: o.meets_floor ? "good" : "warn" }, o.recommendation)
              ),
              h(
                "td",
                null,
                o.is_awaiting_payment
                  ? h("span", { className: "vd-mono", style: { opacity: 0.7 } }, "Live until buyer pays")
                  : o.staged_action_token
                  ? h(Btn, { kind: "primary", onClick: function () { props.decide(o.staged_action_token, "APPROVE"); } }, "Approve Staged")
                  : h(
                      "div",
                      { style: { display: "flex", gap: 6 } },
                      h(Btn, { onClick: function () { props.stageOffer(o, "Accept", null); } }, "Stage Accept"),
                      o.floor_price_cents
                        ? h(Btn, { onClick: function () { props.stageOffer(o, "Counter", o.floor_price_cents); } }, "Counter " + usd(o.floor_price_cents))
                        : null
                    )
              )
            );
          })
        )
      )
    );
  }

  function PickListTable(props) {
    var rows = props.state.pick_list || [];
    if (!rows.length) {
      return h("div", { className: "vd-panel vd-empty" }, "No paid orders awaiting fulfillment.");
    }
    return h(
      "div",
      { className: "vd-table-wrap" },
      h(
        "table",
        { className: "vd-table" },
        h(
          "thead",
          null,
          h("tr", null, h("th", null, "Bin Code"), h("th", null, "Item & SKU"), h("th", null, "Order / Buyer"), h("th", null, "Weight"), h("th", null, "Status"))
        ),
        h(
          "tbody",
          null,
          rows.map(function (r) {
            return h(
              "tr",
              { key: r.order_id, style: r.packed ? { opacity: 0.55 } : undefined },
              h("td", { className: "vd-mono" }, h(Badge, { tone: r.bin_code === "UNASSIGNED" ? "warn" : "good" }, r.bin_code)),
              h(
                "td",
                null,
                h("strong", { style: { display: "block", textDecoration: r.packed ? "line-through" : "none" } }, r.title),
                h("span", { className: "vd-mono", style: { opacity: 0.7 } }, r.sku + " · Qty " + r.quantity)
              ),
              h("td", { className: "vd-mono" }, r.order_id + " · " + r.buyer_username),
              h("td", { className: "vd-mono" }, r.package_weight_oz ? r.package_weight_oz + " oz" : "—"),
              h(
                "td",
                null,
                h(
                  Btn,
                  { kind: r.packed ? "" : "primary", onClick: function () { props.togglePick(r.order_id); } },
                  r.packed ? "Packed ✓" : "Mark Pulled"
                )
              )
            );
          })
        )
      )
    );
  }

  function ActivityView(props) {
    var events = props.state.activity || [];
    if (!events.length) {
      return h("div", { className: "vd-panel vd-empty" }, "Append-only item_events audit ledger is empty.");
    }
    return h(
      "div",
      { className: "vd-table-wrap" },
      h(
        "table",
        { className: "vd-table" },
        h("thead", null, h("tr", null, h("th", null, "When"), h("th", null, "Actor"), h("th", null, "Event"), h("th", null, "Item"))),
        h(
          "tbody",
          null,
          events.map(function (ev) {
            return h(
              "tr",
              { key: ev.event_id },
              h("td", { className: "vd-mono" }, ago(ev.created_at)),
              h("td", null, h(Badge, null, ev.actor)),
              h("td", { className: "vd-mono" }, ev.event_type),
              h("td", null, h("strong", null, ev.title), " ", h("span", { className: "vd-mono", style: { opacity: 0.65 } }, "(" + ev.sku + ")"))
            );
          })
        )
      )
    );
  }

  function SetupView(props) {
    var s = props.state;
    var left = s.setup.steps.filter(function (x) { return !x.done; }).length;
    return h(
      "div",
      { className: "vd-grid2" },
      h(
        "div",
        { className: "vd-panel" },
        h("div", { className: "vd-h" }, "Get Started", h("b", null, left ? left + " left" : "Ready")),
        s.setup.steps.map(function (st, i) {
          return h(
            "div",
            { key: i, className: "vd-check", "data-vd-done": st.done ? "1" : "0" },
            h("i", null, st.done ? "✓" : "○"),
            h("div", null, h("strong", null, st.title), h("small", null, st.detail))
          );
        })
      ),
      h(
        "div",
        { className: "vd-panel" },
        h("div", { className: "vd-h" }, "Connected & Routing", h("b", null, s.profile.marketplace_id)),
        h(
          "div",
          { className: "vd-check", "data-vd-done": "1" },
          h("i", null, "●"),
          h("div", null, h("strong", null, "Publish Route"), h("small", null, s.profile.route_label))
        ),
        h(
          "div",
          { className: "vd-check", "data-vd-done": s.mode === "pro" ? "1" : "0" },
          h("i", null, s.mode === "pro" ? "●" : "○"),
          h(
            "div",
            null,
            h("strong", null, "Progressive Seller Tier: " + s.mode.toUpperCase()),
            h("small", null, "Estimated fee rate: " + (s.profile.estimated_fee_bps / 100).toFixed(2) + "% + $0.40 per order")
          )
        ),
        h(
          "div",
          { className: "vd-check", "data-vd-done": "1" },
          h("i", null, "●"),
          h("div", null, h("strong", null, "Commerce Media Host"), h("small", null, "apiz.ebay.com (REST createImageFromFile)"))
        ),
        h(
          "div",
          { className: "vd-check", "data-vd-done": "1" },
          h("i", null, "●"),
          h(
            "div",
            null,
            h("strong", null, "Taxonomy Cache & @ebay/npm-public-api-mcp"),
            h(
              "small",
              null,
              ((s.aspect_cache_stats && s.aspect_cache_stats.cached_categories) || 0) +
                " leaf categories cached (5,000/d quota) · Partner Program cutoff: " +
                ((s.partner_milestones && s.partner_milestones.partner_cutoff_date) || "2026-12-31")
            )
          )
        )
      ),
      h(
        "div",
        { className: "vd-panel" },
        h("div", { className: "vd-h" }, "Rules In Force", h("b", null, "2026 Spec")),
        s.setup.rules.map(function (rl, i) {
          return h(
            "div",
            { key: i, className: "vd-check", "data-vd-done": "1" },
            h("i", null, "▪"),
            h("div", null, h("strong", null, rl.title), h("small", null, rl.detail))
          );
        })
      ),
      h(
        "div",
        { className: "vd-panel" },
        h("div", { className: "vd-h" }, "Your Data & Sample Tours", h("b", null, s.tour ? "Touring" : "Live DB")),
        h(
          "div",
          { className: "vd-check", "data-vd-done": "1" },
          h("i", null, "●"),
          h(
            "div",
            null,
            h("strong", null, s.totals.total_items + " items · " + s.activity.length + " recent ledger events"),
            h("small", null, "SQLite WAL ledger: " + s.profile.db_path)
          )
        ),
        h(
          "div",
          { style: { marginTop: 12 } },
          h("small", { style: { display: "block", marginBottom: 8, color: "var(--vd-mute)" } }, "Explore realistic sample data in an isolated tour.db (your real ledger stays untouched):"),
          h(
            "div",
            { style: { display: "flex", gap: 8, flexWrap: "wrap" } },
            (s.tours || []).map(function (tr) {
              var active = s.tour && s.tour.id === tr.id;
              return h(
                Btn,
                {
                  key: tr.id,
                  kind: active ? "primary" : "",
                  onClick: function () { props.switchTour("start", tr.id); }
                },
                tr.title
              );
            }),
            s.tour
              ? h(Btn, { kind: "danger", onClick: function () { props.switchTour("leave"); } }, "Leave Tour")
              : null
          )
        )
      )
    );
  }

  function ItemDrawer(props) {
    var item = props.item;
    var _c = useState(item.cogs_cents != null ? (item.cogs_cents / 100).toFixed(2) : ""), cogs = _c[0], setCogs = _c[1];
    var _f = useState(item.floor_price_cents != null ? (item.floor_price_cents / 100).toFixed(2) : ""), floor = _f[0], setFloor = _f[1];
    var _b = useState(item.bin_code || ""), bin = _b[0], setBin = _b[1];

    function save() {
      props.onEnrich(item.sku, {
        cogs_cents: cogs === "" ? null : Math.round( parseFloat(cogs) * 100 ),
        floor_price_cents: floor === "" ? null : Math.round( parseFloat(floor) * 100 ),
        bin_code: bin
      });
    }

    return h(
      React.Fragment,
      null,
      h("div", { className: "vd-scrim", onClick: props.close }),
      h(
        "aside",
        { className: "vd-drawer" },
        h(
          "div",
          { className: "vd-drawer-head" },
          h("div", { className: "vd-eyebrow" }, item.sku + " // " + item.status),
          h("h3", null, item.title),
          h("button", { type: "button", className: "vd-x", onClick: props.close }, "✕")
        ),
        h(
          "div",
          { className: "vd-drawer-body" },
          h(
            "div",
            { className: "vd-kv-grid" },
            h("div", { className: "vd-kv" }, h("small", null, "List Price"), h("b", null, usd(item.list_price_cents))),
            h("div", { className: "vd-kv" }, h("small", null, "Est. Net Payout"), h("b", null, usd(item.estimated_net_payout_cents))),
            h("div", { className: "vd-kv" }, h("small", null, "Net Profit"), h("b", null, item.net_profit_cents != null ? usd(item.net_profit_cents) + " (" + item.margin_pct + "%)" : "Add COGS below")),
            h("div", { className: "vd-kv" }, h("small", null, "Anti-INAD QA"), h("b", null, item.qa_score + " / 100"))
          ),
          item.flaws_disclosure
            ? h("div", { className: "vd-panel" }, h("div", { className: "vd-h" }, "Disclosed Condition / Flaws"), h("div", null, item.flaws_disclosure))
            : null,
          item.pending_actions && item.pending_actions.length
            ? item.pending_actions.map(function (pa) {
                return h(
                  "div",
                  { key: pa.action_token, className: "vd-panel" },
                  h("div", { className: "vd-h" }, actionLabel(pa.action_type), h("b", null, minsLeft(pa.seconds_remaining))),
                  h(
                    "div",
                    { className: "vd-mono", style: { whiteSpace: "pre-wrap", margin: "0 0 10px", fontSize: 11.5, lineHeight: 1.45 } },
                    String(pa.preview_card_md || "").replace(/^###\s+.*\n?/gm, "").replace(/\*\*/g, "").replace(/`/g, "").trim()
                  ),
                  h(
                    "div",
                    { style: { display: "flex", gap: 8 } },
                    h(Btn, { kind: "primary", onClick: function () { props.decide(pa.action_token, "APPROVE"); } }, "Approve & Execute"),
                    h(Btn, { kind: "danger", onClick: function () { props.decide(pa.action_token, "CANCEL"); } }, "Cancel")
                  )
                );
              })
            : null,
          h(
            "div",
            { className: "vd-panel" },
            h("div", { className: "vd-h" }, "Progressive Pro Enrichment (Unlocks Margin & Bin Tools)"),
            h(
              "div",
              { style: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 } },
              h("input", { className: "vd-input", placeholder: "COGS ($)", value: cogs, onChange: function (e) { setCogs(e.target.value); } }),
              h("input", { className: "vd-input", placeholder: "Floor ($)", value: floor, onChange: function (e) { setFloor(e.target.value); } }),
              h("input", { className: "vd-input", placeholder: "Bin (A-01-02)", value: bin, onChange: function (e) { setBin(e.target.value); } })
            ),
            h("div", { style: { marginTop: 10 } }, h(Btn, { kind: "primary", onClick: save }, "Save Pro Economics"))
          )
        )
      )
    );
  }

  function NewDraftDrawer(props) {
    var _t = useState(""), title = _t[0], setTitle = _t[1];
    var _n = useState(""), notes = _n[0], setNotes = _n[1];
    var _p = useState(""), price = _p[0], setPrice = _p[1];
    var _c = useState(""), cogs = _c[0], setCogs = _c[1];
    var _b = useState(""), bin = _b[0], setBin = _b[1];

    function submit() {
      props.onStage({
        title_hint: title,
        seller_notes: notes,
        price_cents: price ? Math.round(parseFloat(price) * 100) : null,
        cogs_cents: cogs ? Math.round(parseFloat(cogs) * 100) : null,
        bin_code: bin || null
      });
    }

    return h(
      React.Fragment,
      null,
      h("div", { className: "vd-scrim", onClick: props.close }),
      h(
        "aside",
        { className: "vd-drawer" },
        h(
          "div",
          { className: "vd-drawer-head" },
          h("div", { className: "vd-eyebrow" }, "Zero Live Writes · Stages Approval Token"),
          h("h3", null, "Stage New eBay Draft"),
          h("button", { type: "button", className: "vd-x", onClick: props.close }, "✕")
        ),
        h(
          "div",
          { className: "vd-drawer-body" },
          h("input", { className: "vd-input", placeholder: "Title hint (optional, e.g. Patagonia Better Sweater Large)", value: title, onChange: function (e) { setTitle(e.target.value); } }),
          h("textarea", { className: "vd-area", rows: 4, placeholder: "Raw measurements & honest flaws (e.g. 22.5\" pit-to-pit, 27\" length, minor cuff pilling)", value: notes, onChange: function (e) { setNotes(e.target.value); } }),
          h(
            "div",
            { style: { display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 8 } },
            h("input", { className: "vd-input", placeholder: "List $ (optional)", value: price, onChange: function (e) { setPrice(e.target.value); } }),
            h("input", { className: "vd-input", placeholder: "COGS $ (Pro)", value: cogs, onChange: function (e) { setCogs(e.target.value); } }),
            h("input", { className: "vd-input", placeholder: "Bin (Pro)", value: bin, onChange: function (e) { setBin(e.target.value); } })
          )
        ),
        h(
          "div",
          { className: "vd-drawer-foot" },
          h(Btn, { kind: "primary", onClick: submit }, "Stage Draft & Mint Approval Token"),
          h(Btn, { onClick: props.close }, "Cancel")
        )
      )
    );
  }

  function BaeDashboardPage() {
    var _s = useState(null), state = _s[0], setState = _s[1];
    var _v = useState("inventory"), view = _v[0], setView = _v[1];
    var _q = useState(""), query = _q[0], setQuery = _q[1];
    var _d = useState(null), drawer = _d[0], setDrawer = _d[1];
    var _msg = useState(""), toast = _msg[0], setToast = _msg[1];
    var _lv = useState(true), live = _lv[0], setLive = _lv[1];
    var toastTimer = useRef(null);

    var say = useCallback(function (text) {
      setToast(text);
      clearTimeout(toastTimer.current);
      toastTimer.current = setTimeout(function () { setToast(""); }, 4000);
    }, []);

    var refresh = useCallback(function () {
      return get("/state")
        .then(function (next) {
          setState(next);
          setLive(true);
        })
        .catch(function () { setLive(false); });
    }, []);

    useEffect(function () {
      refresh();
      var id = setInterval(function () { if (!document.hidden) refresh(); }, POLL_MS);
      return function () { clearInterval(id); };
    }, [refresh]);

    function decide(token, decision) {
      post("/confirm", { action_token: token, decision: decision })
        .then(function () {
          say(decision === "APPROVE" ? "Verified SHA-256 token & executed." : "Action token cancelled.");
          setDrawer(null);
          return refresh();
        })
        .catch(function (e) { say(String((e && e.message) || e)); });
    }

    function enrich(sku, body) {
      post("/item/" + encodeURIComponent(sku) + "/enrich", body)
        .then(function (res) {
          say(res.pro_ops_enabled ? "Saved & unlocked Pro Store Ops!" : "Saved item details.");
          setDrawer(null);
          return refresh();
        })
        .catch(function (e) { say(String((e && e.message) || e)); });
    }

    function stageNew(body) {
      post("/stage", body)
        .then(function (res) {
          say("Staged " + res.sku + " — review approval token.");
          setDrawer(null);
          setView("approvals");
          return refresh();
        })
        .catch(function (e) { say(String((e && e.message) || e)); });
    }

    function stageOffer(off, action, counterCents) {
      post("/offer/stage", {
        best_offer_id: off.best_offer_id,
        ebay_item_id: off.ebay_item_id,
        sku: off.sku,
        action: action,
        counter_price_cents: counterCents
      })
        .then(function () {
          say("Staged " + action + " token — confirm to execute.");
          return refresh();
        })
        .catch(function (e) { say(String((e && e.message) || e)); });
    }

    function togglePick(orderId) {
      post("/pick/" + encodeURIComponent(orderId) + "/toggle", {})
        .then(function () { return refresh(); })
        .catch(function (e) { say(String((e && e.message) || e)); });
    }

    function switchTour(action, tourId) {
      post("/tour", { action: action, tour_id: tourId })
        .then(function () {
          say(action === "leave" ? "Returned to live data.db." : "Loaded sample tour.");
          return refresh();
        })
        .catch(function (e) { say(String((e && e.message) || e)); });
    }

    function exportCsv() {
      post("/export/draft-csv", { status_filter: "ALL" })
        .then(function (res) {
          downloadDraftCsv(res.csv_text, res.filename);
          say("Exported " + res.row_count + " row(s) with Windows CRLF (Action=Draft).");
        })
        .catch(function (e) { say(String((e && e.message) || e)); });
    }

    if (!state) {
      return h("div", { className: "vd" }, h("div", { className: "vd-panel vd-empty" }, "loading bae…"));
    }

    var t = state.totals;
    var tabs = [
      ["inventory", "Inventory", t.total_items],
      ["approvals", "Approvals", t.pending_approvals_count],
      ["offers", "Offers", state.offers.length],
      ["pick", "Pick-List", t.unpacked_pick_count],
      ["activity", "Activity", null],
      ["setup", "Setup", null]
    ];

    var openSkuItem = drawer && drawer.kind === "item"
      ? (state.items || []).find(function (x) { return x.sku === drawer.sku; })
      : null;

    return h(
      "div",
      { className: "vd" },
      state.tour
        ? h(
            "div",
            { className: "vd-tour", role: "status" },
            h("span", null, h("b", null, "Sample Tour"), " · " + state.tour.title + " (" + state.tour.subtitle + "). Your real data.db is untouched."),
            h(Btn, { onClick: function () { switchTour("leave"); } }, "Leave Tour")
          )
        : null,
      h(Hero, { state: state, live: live, setView: setView }),
      h(Attention, {
        state: state,
        setView: setView,
        openItem: function (sku) { setDrawer({ kind: "item", sku: sku }); },
        openToken: function (pa) {
          if (pa.sku) setDrawer({ kind: "item", sku: pa.sku });
          else setView("approvals");
        }
      }),
      h(
        "div",
        { className: "vd-bar" },
        h(
          "div",
          { className: "vd-seg", role: "tablist" },
          tabs.map(function (tb) {
            return h(
              "button",
              {
                key: tb[0],
                role: "tab",
                "data-vd-on": view === tb[0] ? "1" : "0",
                onClick: function () { setView(tb[0]); }
              },
              tb[1],
              tb[2] !== null ? h("i", null, tb[2]) : null
            );
          })
        ),
        h("span", { className: "vd-grow" }),
        view === "inventory"
          ? h("input", {
              className: "vd-input",
              style: { width: 220 },
              placeholder: "Filter SKU, title, bin…",
              value: query,
              onChange: function (e) { setQuery(e.target.value); }
            })
          : null,
        h(Btn, { onClick: exportCsv }, "CRLF Draft CSV"),
        h(Btn, { kind: "primary", onClick: function () { setDrawer({ kind: "new" }); } }, "New Draft")
      ),
      view === "inventory"
        ? h(InventoryBoard, { state: state, query: query, openItem: function (sku) { setDrawer({ kind: "item", sku: sku }); } })
        : null,
      view === "approvals"
        ? h(ApprovalsTable, {
            state: state,
            decide: decide,
            openToken: function (pa) { if (pa.sku) setDrawer({ kind: "item", sku: pa.sku }); }
          })
        : null,
      view === "offers"
        ? h(OffersTable, { state: state, decide: decide, stageOffer: stageOffer })
        : null,
      view === "pick"
        ? h(PickListTable, { state: state, togglePick: togglePick })
        : null,
      view === "activity" ? h(ActivityView, { state: state }) : null,
      view === "setup" ? h(SetupView, { state: state, switchTour: switchTour }) : null,
      openSkuItem
        ? h(ItemDrawer, {
            item: openSkuItem,
            close: function () { setDrawer(null); },
            decide: decide,
            onEnrich: enrich
          })
        : null,
      drawer && drawer.kind === "new"
        ? h(NewDraftDrawer, { close: function () { setDrawer(null); }, onStage: stageNew })
        : null,
      toast ? h("div", { className: "vd-toast", role: "status" }, toast) : null
    );
  }

  window.__HERMES_PLUGINS__.register("hermes-ebay-ops", BaeDashboardPage);
})();
