---
marp: true
title: How the Tax Report PDF Is Generated
author: tax-etrade
paginate: true
---

# How the Tax Report PDF Is Generated

**A tool that computes capital gains on E\*TRADE / Revolut shares under Spanish tax rules**

Presentation for the tax advisor / accountant

> ⚠️ The tool is an aid. **The final figures are always validated by you.**

---

## The idea in one sentence

> We take the **raw** data from the broker (purchases, vestings, exercises and sales),
> apply the **FIFO** method and the **Spanish IRPF** rules, and pour the result into a
> **PDF** organised by **Modelo 100** boxes.

One data source → one calculation → one report.
**Nothing is entered by hand.**

---

## The data journey (4 steps)

```
  1. RAW DATA              2. TAX ENGINE          3. TEMPLATE         4. PDF
  ───────────              ─────────────          ──────────         ─────
  E*TRADE (Excel):    →    FIFO + IRPF      →     HTML report    →   Chromium
   · ESPP purchases        rules:                 with all the        prints
   · RSU vestings           · FIFO ordering       sections and        the HTML
   · Option exercises       · ESPP art. 42.3.f    tables              to A4
   · Sales                  · 2-month rule                            (final PDF)
  Revolut (CSV):            · savings base
   · Trades                 · dividends/interest
   · Dividends
```

Every figure in the PDF can be traced back to a specific row in the broker's Excel.

---

## Step 1 — Where the data comes from

It is read **directly from the files E\*TRADE exports** (and, if present, the Revolut CSV):

| Event type | Source | What it provides |
|---|---|---|
| **ESPP purchases** | E\*TRADE Excel | Purchase price and company discount |
| **RSU vestings** | E\*TRADE Excel | Vested shares and their value |
| **Option exercises** | E\*TRADE Excel | Acquisition cost |
| **Sales** | E\*TRADE Excel | Date, number of shares and sale price |
| **Revolut** (optional) | CSV | Trades and dividends for the same security |

No figures are entered by hand: the official broker files are parsed.

---

## Step 2 — The tax engine (the heart of the calculation)

Every event passes through **a single engine** that applies, in this order:

1. **FIFO** — the shares sold are the **oldest** ones first (mandatory criterion in Spain; specific-lot identification is not allowed).
2. **EUR conversion** — each operation is converted using the **ECB exchange rate** for its date (not an annual average rate).
3. **Automatic *sell-to-cover* detection** — sales used to cover withholding at vesting are identified and classified automatically.
4. **Special rules** (next slide).

---

## Step 2 — Tax rules it applies

<style scoped>table { font-size: 0.72em; }</style>

| Rule | Legal basis | What it does |
|---|---|---|
| **FIFO method** | Art. 37.2 LIRPF | Matches sales with the oldest purchases |
| **ESPP exemption** | Art. 42.3.f LIRPF | The company discount is exempt if shares are held for **3 years**; if sold earlier, it becomes **employment income** |
| **2-month rule** | Art. 33.5.f LIRPF | Blocks losses if the same security is rebought within ±2 months (anti *wash-sale*) |
| **Savings base** | Art. 49 LIRPF | Integrates gains + dividends + interest and offsets losses |
| **4-year carryforward** | Art. 49 LIRPF | Carries pending losses to future years |

---

## Steps 3 and 4 — From calculation to PDF

- The engine's result fills an **HTML report template** with all the tables.
- An (automated) **Chromium** browser **prints that HTML to PDF** in A4 format.
- **Two versions** are produced: 🇪🇸 Spanish and 🇺🇸 English, with the **same numbers**.

> The PDF is, literally, the engine's calculation "printed". It is not edited afterwards.

---

## What the PDF contains (section by section)

1. **Methodology** — explains FIFO and the rules applied.
2. **Portfolio summary by security** — remaining shares and average cost.
3. **Yearly Tax Summary** — gain/loss per year (Savings Base, Modelo 100).
4. **Gains/losses by broker** — E\*TRADE vs Revolut breakdown.
5. **Savings Base** — gains + dividends/interest (Art. 49).
6. **Loss carryforward ledger** — 4-year carryover.

---

## What the PDF contains (continued)

7. **Disposals detail** — each sale, ready for the Modelo 100.
8. **Modelo 100 filing guide** — which box to fill with which figure.
9. **ESPP 3-year analysis** — exempt vs taxable (Art. 42.3.f).
10. **Detailed transaction ledger** — each operation with its step-by-step FIFO calculation.

> Sections 7 and 8 are the ones designed **directly for the tax return**.

---

## Example 1 — A sale with FIFO step by step

**Setup.** You hold two RSU lots of the same security and sell part of them:

| Lot | Date (vesting) | Shares | Price | ECB rate | Cost in EUR |
|---|---|---|---|---|---|
| Lot 1 | 15/03/2022 | 10 | $100 | 0.90 | **€900.00** |
| Lot 2 | 20/06/2023 | 10 | $120 | 0.92 | **€1,104.00** |

➡️ **You sell 15 shares** on 10/09/2024 at $150, ECB rate 0.91.

---

## Example 1 — The engine's calculation

**Sale proceeds (EUR):** 15 × $150 × 0.91 = **€2,047.50**

**FIFO → oldest shares are sold first:** 10 from Lot 1 + 5 from Lot 2

| Shares | From | Cost EUR |
|---|---|---|
| 10 | Lot 1 (all) | €900.00 |
| 5 | Lot 2 (5 of 10 → 5×120×0.92) | €552.00 |
| **15** | | **€1,452.00** |

**Capital gain** = 2,047.50 − 1,452.00 = **€595.50** → Savings Base

**Remaining in portfolio:** 5 shares from Lot 2 (cost €552.00), ready for the next sale.

---

## Example 2 — The ESPP discount (Art. 42.3.f)

**Setup.** ESPP purchase: market price $100, you pay 85% = $85.
Company discount = **$15/share × 10 shares = $150**.

| Scenario | Tax treatment |
|---|---|
| 🟢 You hold the shares **≥ 3 years** | The $150 discount is **EXEMPT** (not taxed) |
| 🔴 You sell **before 3 years** | The $150 (converted to EUR) becomes **employment income** and is taxed |

> The report places each case in its section: exempt → informational only; early sale → employment income. **The gain/loss on the share sale is computed separately, always via FIFO.**

---

## Example 3 — The Savings Base (Art. 49)

Gains, dividends and interest are integrated, and losses are offset:

| Item (year 2024) | Amount |
|---|---|
| Capital gain (Example 1) | +€595.50 |
| Dividends / interest (converted to EUR) | +€200.00 |
| Losses from other sales | −€100.00 |
| **Net savings base** | **€695.50** |

**Progressive savings scale:** 19% up to €6,000 · 21% from €6,000–50,000 · 23% from €50,000–200,000 · 27% from €200,000–300,000 · 28% above.

➡️ Here: €695.50 × 19% ≈ **€132.15 estimated tax**.

---

## Example 4 — The 2-month rule (anti *wash-sale*)

**Setup.** You sell at a **loss** and rebuy the same security shortly after:

| Operation | Date | Result |
|---|---|---|
| Sale at a −€300 loss | 10/05/2024 | Loss... |
| Rebuy of the **same security** | 02/06/2024 (< 2 months) | 🚫 Loss **blocked** |

➡️ The −€300 loss **cannot be claimed now**: it is "parked" and added to the cost of the new shares (you will use it when you sell those for good).

> The report flags these losses as blocked so they are not declared by mistake.

---

## How the examples appear in the PDF

| Example | Report section where it appears |
|---|---|
| **1 — FIFO sale** | *Disposals detail* + *Detailed transaction ledger* |
| **2 — ESPP** | *ESPP 3-year analysis* (exempt) or *Employment income* (early sale) |
| **3 — Savings base** | *Yearly Tax Summary* + *Savings Base* |
| **4 — 2-month rule** | *Loss carryforward ledger* (blocked loss) |

All amounts in EUR, using the **ECB rate for each date**.

---

## Why every number can be trusted

- **Full traceability**: each report row comes from a row in the broker's Excel.
- **Official exchange rate**: ECB, by operation date (with an auditable cache).
- **No manual entry**: the error of transcribing figures by hand is eliminated.
- **Same engine, two outputs**: the PDF (tax return) and the interactive dashboard (decision-making) share the same calculations; the anchor numbers must match.
- **Automated test coverage** over the tax rules (FIFO, ESPP, sale classification).

---

## What the report does **not** do (limits)

- It does not file the return: it **produces the figures**, you enter/validate them.
- It does not replace your professional judgement or tax advice.
- It reflects **our interpretation** of the rules; you have the final word.
- "Live" prices (today's value) live in the dashboard, **not** in the PDF; the PDF is a **fixed tax snapshot**.

---

## Summary for the advisor

> **Official broker data → FIFO method + IRPF rules → PDF organised by
> Modelo 100 boxes.**

- One source, one calculation, no manual edits.
- Every figure is **traceable** and converted to EUR using the **ECB rate** by date.
- The PDF separates what goes to **capital gains**, **employment income** (ESPP sold before 3 years) and **savings base** (dividends/interest).

**What it needs from you?** Review the *Disposals* and *Modelo 100 guide* sections and confirm the approach works for you.
