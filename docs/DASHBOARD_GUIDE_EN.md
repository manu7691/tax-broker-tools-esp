# Interactive Charts & Tax Dashboard — Guide

This repository can generate an **interactive HTML dashboard** that turns your E-Trade
data into a plain-language picture of your company stock: what you own, what it's
worth, what you'd net if you sold, and your Spanish tax situation.

It is built for a **regular employee, not a finance expert**. There is no jargon you
need to know — every number has a one-line explanation, and the advanced market tools
are tucked away behind their own tab.

> ⚠️ The dashboard is **informational only** and uses a **live market price** fetched
> from Yahoo Finance. It is not tax advice. The generated file (`charts_dashboard.html`)
> is gitignored and never leaves your computer.

---

## How to generate it

**Easiest (menu):** run `run_tax_engine.command` (macOS/Linux) or `run_tax_engine.bat`
(Windows) and choose **option 6 — "Generate Charts & Tax Dashboard"**. It opens
`charts_dashboard.html`; double-click that file to view it in your browser.

**Developer (CLI):**

```bash
uv run generate_charts.py
```

Options:

| Flag | Effect |
|------|--------|
| *(none)* | Auto-detects everything and fetches the live price. |
| `--current-price 45.50` | Use a fixed USD price instead of the live one. |
| `--include-espp` / `--skip-espp` | Force-include or exclude ESPP analysis (skips the prompt). |

Notes:

- The **stock ticker is auto-detected** from your data (`BenefitHistory.xlsx`) — you no
  longer pass it as a parameter.
- If **ESPP data is present**, you're asked whether to include the ESPP analysis. If you
  have no ESPP data, that step is skipped automatically.
- The first run needs internet (to fetch the live price and historical prices); after
  that the ECB rate cache works offline.

---

## The four tabs (a simple story)

The dashboard is organized as the questions a normal person actually asks, in order.

### 📊 My Stock — *"What do I have right now?"*

- **Your situation today** — a plain snapshot: how many shares you still own, what they
  are worth today (EUR), what you originally paid, and whether you are **up or down**
  overall. One sentence sums it up in plain words.
- **Overall Scorecard** — did holding pay off? Shows your RSU holding profit/loss and a
  three-way ESPP tax breakdown (see *My Taxes*).

### 💸 Should I Sell? — *"What happens if I sell?"*

- **Sell simulator** — drag the price and exchange-rate sliders to see your EUR value,
  gain/loss, estimated Hacienda tax, and **💰 net cash in your pocket after tax**.
- **ESPP lock warning** — if some of the shares you hold are ESPP shares still inside the
  3-year tax-free window, a banner tells you exactly how many and **how much tax exemption
  you'd forfeit** by selling now.
- **Hacienda tax-bracket optimizer** — shows which savings bracket (19–28%) your gain falls
  in and how much more you could realize before jumping a bracket. It also counts any
  dividend/interest income you've recorded this year.
- **Break-even prices** — two clearly-labeled numbers:
  - **Fresh-Start Break-even** — the USD price to sell your *current* shares at zero
    gain/loss, ignoring past sales (a "from zero" number).
  - **Full Portfolio Break-even** — the same, but adjusted for your past realized
    gains/losses.
  - An **"Exclude ESPP (RSUs only)"** toggle filters both the current holdings *and* the
    past realized figures consistently.

### 🧾 My Taxes — *"What about tax?"*

- **ESPP tax-free countdown** — every ESPP batch you hold, with the exact date it clears
  the 3-year holding period and becomes tax-free, plus a "secured / at-risk / lost"
  breakdown:
  - 🟢 **Secured** — exemption already locked in (held ≥ 3 years).
  - 🟡 **At Risk** — exemption pending; you keep it only if you hold to term.
  - 🔴 **Penalty** — exemption already lost by selling before 3 years.
- **ESPP tax audit** (doughnut) — the same secured/at-risk/lost split, visually.
- **RSU hold-vs-vest** — whether holding your RSUs beat selling them on vest day.
- **Dividends & interest income** — dividends/interest are taxed in the **same Spanish
  "savings base"** as your stock gains, so they raise your bracket before you sell.
  Add them via menu **option 4**; they then feed the bracket optimizer.

### 🔬 Advanced (for the curious) — optional market context

Stock price trend with moving averages, where your sale profits came from (stock vs.
currency), the transaction timeline, the USD→EUR exchange-rate history, a competitor /
peer comparison, sell-alert setup tips, and a single-stock concentration-risk meter.
None of this is needed for the core decisions — it's there if you want it.

---

## Handy features

- **🌐 Language toggle (EN / ES)** — every label, table, chart axis, and the dynamic
  advice switch language. Dates follow the Spanish DD/MM/YYYY format and month names are
  localized.
- **👁️ Privacy mode** — one click blurs every sensitive euro figure so you can screen-share
  or show your advisor without exposing amounts.
- **Auto-everything** — ticker, share counts, cost basis, and FIFO matching of past sells
  are all derived from your existing data; no manual entry.

---

## How the key numbers are calculated

- **Worth today / break-even** use your **EUR cost basis** (the euros you actually paid,
  converted at the ECB rate on each acquisition date), because that is what matters for
  Spanish tax — not the raw USD price.
- **Past sells are FIFO-matched** (oldest lots first, as Spanish law requires), so the
  "shares you still own" and their cost basis reflect which specific lots remain. This is
  why a break-even here can differ from E-Trade's USD-only view.
- **ESPP cost basis** uses the purchase-date FMV (fair market value), consistent with the
  rest of the tax engine.

For the underlying tax methodology, see
[TAX_CALCULATION_METHOD.md](TAX_CALCULATION_METHOD.md) and
[ESPP_TAX_GUIDE_EN.md](ESPP_TAX_GUIDE_EN.md).
