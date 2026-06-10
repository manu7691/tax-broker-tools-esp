# Report ↔ Dashboard — How the two outputs connect

This project produces **two outputs from the very same calculation**:

1. The **PDF tax report** (`tax_report_EN_*.pdf` / `tax_report_ES_*.pdf`) — the
   *fiscal* view, built for your Spanish declaration (Modelo 100).
2. The **interactive dashboard** (`charts_dashboard.html`) — the *decision* view,
   built to answer "what do I own, what's it worth, and what if I sell?".

If you've ever looked at both and thought *"these don't seem to talk to each
other"*, this guide is for you. **They share the same numbers** — they just show
them for different purposes.

---

## They come from one engine, not two

There is **no separate data pipeline**. Both outputs:

1. Load the **same events** — ESPP purchases, RSU vests, option exercises, E\*TRADE
   sells, and (if present) Revolut trades of the same security.
2. Run the **same sell-to-cover auto-detection**.
3. Feed everything through the **same FIFO tax engine** (`TaxEngine.process_all`).

Everything you see in either file is then read back from the same three engine
results:

| Engine result | Plain meaning |
|---|---|
| `processed_events` | Every acquisition and sell, with its realized gain/loss |
| `state.lots` | The specific share lots you still hold (FIFO order) |
| `state` totals | Your current share count and average cost in EUR |

So the report and the dashboard are **two windows onto the same ledger**, not two
independent calculations. If one of the **shared anchor numbers** below disagrees
between them, it's a bug — not a difference of method.

> 🕒 **Generate both on the same day.** The dashboard uses a **live market price**
> and "today" as its reference date, while the PDF is a fixed fiscal snapshot. So
> live-price-derived figures (current value, "net if sold today", ESPP countdowns)
> *will* differ if you generate the two files on different days — that's expected,
> not an error. The **shared anchor numbers** (realized gains, holdings, cost
> basis, ESPP exempt/taxable totals) do **not** depend on the live price and should
> match regardless.

---

## The shared anchor numbers

These are the values that appear in **both** files. Use them to reconcile the two:

| Number | In the PDF report | In the dashboard | Common source |
|---|---|---|---|
| **Realized gain/loss per sale** | Transaction ledger rows | "Where your profit came from" (gains decomposition) chart | `processed_events[*].realized_gain_loss` |
| **Total gains / losses per year** | Yearly Tax Summary table | Decomposition + broker bars | aggregated `processed_events` |
| **Shares you still own** | "Current Position: N shares" line | Sell simulator / unsold-lots list | `state.lots` (remaining shares) |
| **Average cost (EUR)** | "Current Informational Avg Cost" | Average-cost line on the price-trend chart | `state.avg_cost_eur` |
| **ESPP discount — exempt vs taxable** | *Rendimientos del Trabajo* section (exempt, or taxable if sold early) | ESPP scorecard: 🟢 secured / 🟡 at-risk / 🔴 lost | `calculate_espp_discounts` + `detect_espp_early_sales` |
| **Per-broker realized totals** | "Realized Gains/Losses by Broker" subtotal | Broker comparison bars | `processed_events[*].event.broker` |
| **Dividends / interest income** | Savings-base section | Dividends & interest panel + bracket optimizer | `load_savings_income` |

**How to check the correlation yourself:** pick any sale. Its EUR gain/loss in a
PDF ledger row is the *same* euro figure that the dashboard's gains-decomposition
chart splits into "stock move" vs "currency move" for that date. Sum a year's
sales in the PDF Yearly Tax Summary and you get the same total the dashboard's
yearly bars show.

> ⚠️ The **ESPP three-way split is the same data shown two ways.** The PDF reports
> the discount as either *exempt* (held ≥ 3 years) or *taxable salary* (sold
> early). The dashboard refines "exempt" into 🟢 **secured** (already past 3 years)
> vs 🟡 **at-risk** (still held but not yet 3 years), and 🔴 **lost** is the PDF's
> "taxable early sale". `secured + at-risk = the PDF's exempt total`.

---

## What is in only ONE of them (and why)

This is usually the real reason the two *feel* disconnected: each file has a few
numbers the other deliberately omits.

### Dashboard-only — driven by a **live market price**

| Dashboard number | Why it's not in the PDF |
|---|---|
| Current portfolio value (EUR, today) | Depends on today's Yahoo Finance price — informational, not a fiscal fact |
| "Net cash if I sold today" / sell simulator | A *hypothetical* future sale; nothing has been realized yet |
| RSU hold-vs-sell delta | A what-if comparison, not a declared event |
| ESPP tax-free **countdown clocks** | Forward-looking timers; the PDF only states the current exempt/taxable status |
| Price trend, moving averages, peer comparison | Market context for decisions, irrelevant to a tax filing |

The dashboard is explicit about this: it uses a **live price** and is
**informational only**. That's why these numbers move between runs while the PDF's
do not.

### Report-only — the **declaration math**

| PDF number | Why it's not on the dashboard's front pages |
|---|---|
| Estimated tax due per year | The report is the authoritative tax document |
| Blocked losses (2-month wash-sale rule) | Detailed fiscal mechanics |
| Loss carryforward / savings-base ledger (4-year) | Multi-year fiscal accounting for the declaration |
| Modelo 100 box guidance | Filing instructions, specific to the PDF |

---

## One-line summary

> **Same events → same FIFO engine → same realized gains, holdings, cost basis,
> ESPP status and broker totals.** The PDF then layers on the *declaration math*
> (tax due, carryforward), and the dashboard layers on a *live price* (current
> value, what-if sells, countdowns). The shared anchor numbers in the table above
> are where the two line up exactly.

For the underlying methodology see
[TAX_CALCULATION_METHOD.md](TAX_CALCULATION_METHOD.md); for the dashboard tour see
[DASHBOARD_GUIDE_EN.md](DASHBOARD_GUIDE_EN.md).
