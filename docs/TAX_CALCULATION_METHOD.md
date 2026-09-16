# Spanish FIFO Tax Calculation Method & Compliance Audit

This document explains the calculation methodology used by the Spanish Tax Engine to process E-Trade stock data (RSUs, ESPPs, Stock Options) and analyzes the engine's compliance with Spanish Tax Law (LIRPF).

---

## 1. Executive Verdict & Compliance Checklist

The engine implements a fully compliant First-In, First-Out (FIFO) calculation system specifically tailored to the requirements of the Spanish Tax Agency (*Agencia Tributaria - Hacienda*).

| Regulatory / Technical Requirement | Status | Legal Basis / Reference |
| :--- | :---: | :--- |
| **FIFO Lot Matching** | ✅ Compliant | Art. 37.2 LIRPF — Strict homogeneous matching |
| **ECB Exchange Rates (USD → EUR)** | ✅ Compliant | Official daily European Central Bank lookup rates |
| **Progressive Savings Tax Scales** | ✅ Compliant | Art. 66 LIRPF — Up-to-date 2024–2026 tax bands (19% to 28%) |
| **2-Month Wash Sale Rule** | ✅ Compliant | Art. 33.5.f LIRPF — Anti-avoidance (proportional blocking) |
| **Fee Deductions** | ✅ Compliant | Art. 35.1 & 35.2 LIRPF — Deductible *gastos inherentes* |
| **RSU Vesting Cost Basis** | ✅ Compliant | FMV at release date (prevents double taxation) |
| **ESPP Purchase Cost Basis** | ✅ Compliant | FMV at purchase date |
| **ESPP 3-Year Holding Period** | ✅ Auto-Detected | Art. 42.3.f LIRPF — Identifies early sales and salary tax |
| **4-Year Loss Carryforward** | ⚠️ Simulated | Art. 49 LIRPF — Carryforward Ledger across tracked years; seed pre-window losses via `prior_losses.json` (see §4.1) |
| **Cross-Category Offset (25% cap)** | ⚠️ Simulated | Art. 49 LIRPF — Computed when you supply `savings_income.json` (dividends/interest); cap is year-aware (see §4.1) |
| **Modelo 720 (Foreign Assets)** | ❌ Out of Scope | Separate annual obligation (if assets abroad > €50,000) |

---

## 2. Core Calculation Methodology

### Data Flow

```mermaid
flowchart TD
    A["E*TRADE ESPP<br/>BenefitHistory.xlsx"] --> P
    B["E*TRADE Orders<br/>orders.xlsx"] --> P
    C["E*TRADE RSU PDFs"] --> P
    D["E*TRADE Options PDFs"] --> P
    E["Revolut CSV<br/>(movements / realized gains)"] -->|"filter to tracked ISIN/ticker<br/>tag broker = Revolut"| P
    P["Combined events<br/>(one security)"] --> F["FIFO engine<br/>+ 2-month wash sale<br/>+ ECB USD→EUR per date"]
    F --> R["PDF report (EN/ES)<br/>ledger · per-broker subtotal · savings base"]
    F --> G["charts_dashboard.html"]
```

Every source is converted into the same `StockEvent` stream, tagged with its broker, and fed through one FIFO queue. Revolut rows are filtered to the single tracked security before they join the pool (see *FIFO Scope* below).

### FIFO Lot Matching (First-In, First-Out)
Under Spanish law (**Art. 37.2 LIRPF**), shares of the same company are homogeneous. When you execute a sell order, the engine matches the sold shares against your oldest available share acquisitions in chronological order.
* Realized gain/loss is calculated per lot:
  $$\text{Realized Gain/Loss} = (\text{Selling Price in EUR} - \text{Acquisition Cost in EUR}) \times \text{Shares}$$
* If a single sell transaction spans multiple purchase lots, the transaction is split and calculated on a per-lot basis.
* Stale lots are completely cleared once their remaining shares reach `0`.

### FIFO Scope: Per Security, Not Across Securities
FIFO is applied **per homogeneous security** — i.e. per ISIN (Art. 37.2 LIRPF). Each security has its **own independent FIFO queue**: a sale of *DT* can only be matched against earlier acquisitions of *DT*, never against *TSLA* or *NVDA*. You never FIFO-match across different tickers.

* **This engine processes one security at a time.** It assumes every input transaction is the same homogeneous security (in practice, your employer's stock, e.g. DT) and keeps a single FIFO queue. The optional Revolut import is therefore **filtered down to that one security** (by ISIN, or by ticker for the movements export); rows for other tickers are discarded.
* **Other securities are still taxable and must be declared separately.** If you also bought and sold *other* tickers on Revolut (TSLA, NVDA, ADBE, …), each of those is its own FIFO calculation that this tool does **not** compute. Run the engine once per ticker (set `input/ticker.json` accordingly), or have those handled separately, and combine the results as below.

**How it rolls up for Hacienda (Modelo 100, base del ahorro):**
1. Compute each security's net gain/loss with its **own** FIFO queue (per ISIN).
2. **Sum** all securities' results into the single *ganancias y pérdidas patrimoniales* bucket of the savings base — losses on one security offset gains on another within this bucket (they are not ring-fenced per security).
3. That net then cross-offsets (up to the 25% cap) against the dividends/interest (RCM) bucket, and any remainder carries forward 4 years (Art. 49 LIRPF).

So **FIFO is per security, but the taxable result is the aggregate** of all of them in the savings base.

### Transaction Processing Order (Same-Day Events)
To prevent negative share inventory errors and ensure correct FIFO matching for same-day sell-to-cover actions, events occurring on the same calendar day are sorted as follows:
1. **VEST / BUY / EXERCISE** (all acquisitions)
2. **SELL** (all sales, including sell-to-cover)

### Currency Conversion
All values are converted from USD to EUR:
1. Uses the official European Central Bank (ECB) daily exchange rate.
2. Inverts the ECB's official EUR/USD rate to obtain the correct USD/EUR rate.
3. Automatically falls back to the closest preceding business day's rate for weekends and market holidays.

---

## 3. Advanced Spanish Tax Compliance Rules

### The 2-Month Wash Sale Rule (*Norma de los Dos Meses*)
Under **Art. 33.5.f LIRPF**, you cannot declare capital losses from a sale if you acquired homogeneous shares within **2 months before or after** that sale.
* **Proportional Blocking:** The blocked loss is limited to the number of replacement shares.
  $$\text{Blocked Shares} = \min(\text{Sold Shares}, \text{Replacement Shares Held When the Loss Sale Happens})$$
* **Correct Application:** The engine blocks a loss only against replacement shares the taxpayer actually held at the moment of the loss sale (shares consumed by the sell itself do not trigger a wash sale). Availability is measured then — not against whatever is left in the portfolio today — so a figure already reported cannot be altered by a later sale.
* **Lot-level tracking:** The deferred loss is parked **on the replacement lot** that caused it, not on a calendar year. Each `ShareLot` carries a `deferred_wash_sale_loss` balance and the schedule of how FIFO consumed it.
* **Filing Treatment — when the loss unlocks:** Art. 33.5 (final paragraph) says the deferred losses *"se integrarán a medida que se transmitan los valores o participaciones que permanezcan en el patrimonio del contribuyente"* — progressively, as the remaining securities are sold, **not** only on a full liquidation. DGT doctrine (V3282-18, V0046-20, V1119-21) adds that each of those later transmissions must itself be **definitive**: no homogeneous securities repurchased in the two months that follow it. Three readings are selectable with `--wash-sale-release`:
  * **`definitive` (default — the statutory rule).** A slice of the deferral is integrated when the replacement shares are transmitted **and** not replaced again within two months. The test is **proportional**: selling 50 shares with only 10 repurchased integrates 40 shares' worth and rolls 10. What is not integrated is **not lost** — it rolls onto the new replacement shares and waits for a clean transmission, however many round trips that takes.
  * **`position_zero` (conservative).** Additionally requires the whole position to reach 0.00 shares plus a clean 2-month quarantine. Stricter than the statute; defers the deduction longer than the law requires.
  * **`per_lot` (aggressive).** Frees the loss on any disposal of the replacement lot, with **no definitiveness test**. Matches the bare wording of the statute but ignores the DGT's condition, so it integrates losses earlier than the doctrine allows.
  Under all three the year of origin is **never amended** (DGT V1547-16, V1035-18): a loss deferred in 2025 and released later stays blocked in the 2025 return and is claimed in the year it unlocks.
* **Closed years are never rewritten in silence.** Declare what you already filed in `input/closed_years.json` (`{"2022": {"net_gain_loss": "-5000.00"}}`). If recomputing gives a different result — which Art. 33.5.f can legitimately cause, since a January repurchase blocks a loss sold the previous December — the engine reports the divergence and leaves the choice between a *complementaria* and a *rectificativa* to you. It never edits a past year by itself.
* **What "Blocked Losses" reports:** the balance **still pending at 31 December**, not the gross amount ever deferred. A loss deferred and released within the same year leaves nothing pending and is simply deductible that year. Liquidate a position in full and the blocked figure for that year is necessarily €0.00.
* **Year-end is not the cut-off:** because the rule also counts repurchases in the two months *after* the sale, a December loss is still exposed to a January or February purchase. That year's figure is final only once the window closes.

### Transaction & Transfer Fee Deductions (*Gastos Inherentes*)
According to **Art. 35.1 and 35.2 LIRPF**, commissions and fees directly related to the acquisition or transmission of shares are deductible.
* **Which side the fee lands on matters.** Disposal costs (commissions, SEC fees, brokerage assist fees on a sale) reduce the *valor de transmisión* of that sale. Acquisition costs are **capitalised into the lot's cost basis** instead, so they reduce the gain of whichever future sale consumes those shares, in proportion to the fraction consumed. The yearly summary reports the two buckets separately (`acquisition_fees_eur` / `disposal_fees_eur`).
* Users can manually record platform wire transfer fees (for transferring cash out of E-Trade) in the input file to have them deducted as inherent transaction costs.

### ESPP 36-Month Holding Period Exemption (Art. 42.3.f LIRPF)
Discounts on ESPP purchases (up to €12,000/year) are tax-exempt if:
1. The shares are held for at least **36 months** from the purchase date, counted **date to date** (a lot bought on 29-Feb-2020 is clear from 28-Feb-2023).
2. The ESPP program was offered to all employees under the same conditions (verified via company enrollment sign-off).

**Early Sale Detection:**
* Every lot carries a **typed origin** (`ESPP`, `RSU`, `EXERCISE`, `MARKET`) and its own FMV / price-paid, both of which survive the FIFO match. Classification never depends on free-text notes, and two ESPP purchases on the same day are valued separately.
* The engine scans all FIFO sales. If ESPP shares are sold before the 36-month mark, it flags the corresponding purchase discount as **taxable salary income** (*Rendimiento del Trabajo*), reported **separately** from the savings base — the two are never netted.
* An ESPP disposal whose discount cannot be valued (missing FMV or price paid in the input) raises a **warning**; it is never silently skipped.
* The tax is imputed to the **Purchase Year**, requiring a **Complementary Tax Return** (*Declaración Complementaria*) for that year, which may incur delay interest but no penalties if filed voluntarily.

---

## 4. Scope Limitations & Caveats

> **Why these are not automated:** each item below needs data the engine never sees — your results from *other years*, or income from *other categories* (dividends, interest). These belong in the final Modelo 100, where everything is aggregated. The engine produces a per-year, single-instrument worksheet; the rules below are applied on top of it at filing time.

### 4.1 Loss Handling (Art. 48 & 49 LIRPF)

The engine's **Yearly Tax Summary** reports each year's gains and losses independently. On top of that, the **Loss Carryforward Ledger** simulates the year-to-year offset; cross-category offset still happens at filing time.

**a) 4-year carryforward (Art. 49 LIRPF).** If your *savings base* (base del ahorro) is net negative in a year — total losses exceed total gains — the loss is not lost. It carries forward to offset gains over the **next 4 years**. The Carryforward Ledger applies this automatically across the tracked years (oldest losses first) and flags any that expire unused.

> *Example:* 2024 nets −€3,000 (pay €0 tax, carry −€3,000 forward). 2025 has +€5,000 in gains → offset the carried −€3,000, so only €2,000 is taxed. The ledger now shows this directly. **Losses from before your imported data window** aren't visible to the engine — seed them with `input/prior_losses.json` (e.g. `{"2024": 3000}`) or `--prior-losses <file>`.

**b) Cross-category offset, 25% cap (Art. 49 LIRPF).** The savings base has two ringfenced buckets: *capital gains/losses* (your stock sales) and *returns on movable capital* (dividends, interest). A net loss in one bucket may offset up to **25%** of the positive balance in the other (the cap is year-dependent: 10% in 2015, 15% in 2016, 20% in 2017, 25% from 2018).

> *Example:* a −€2,000 net stock loss can reduce your taxable dividend/interest income by up to 25% of that income in the same year; any unused remainder carries forward for 4 years. The engine computes this **when you supply `input/savings_income.json`** (dividends/interest in EUR); otherwise it only sees stock transactions. Foreign tax withheld is reported for reference — the *deducción por doble imposición internacional* is applied by your advisor.

**What to give your advisor:** this engine's net gain/loss per year, so they can slot it into the carryforward and cross-category boxes of Modelo 100 alongside your other savings income.

### 4.2 Other limitations

2. **Single Ticker Assumption:** The engine assumes all input transactions apply to the same company stock (in practice, your employer's shares). If you trade multiple tickers, separate files must be processed to prevent FIFO lot mixing. *Exception:* the optional Revolut export (`input/revolut/*.csv`) may contain many tickers — the engine filters it down to the tracked security (by **ISIN** for the realized-gains export, or by **ticker** for the account-movements export, which has no ISIN) per `input/ticker.json`, and discards the rest, so only homogeneous shares enter the FIFO pool. The movements export also contributes **buys** for shares you never sold, completing the cost-basis pool.

   **Cross-broker scope (Revolut):** by default the wash-sale rule and FIFO ordering only see this E\*TRADE account. If you held the *same* security (same ISIN) on Revolut, dropping its P&L CSV into `input/revolut/` merges those buys/sells into the **same FIFO queue**, so cross-broker FIFO and the 2-month rule are evaluated correctly across both — as Spain requires for valores homogéneos. This requires the **complete acquisition history** for that security; otherwise the global queue can go negative and the engine raises an error.
3. **Modelo 720:** If your foreign bank accounts or stock portfolios (like E-Trade) exceed a value of €50,000 at any point during the year (or as of Dec 31st), you must file the Modelo 720 informative declaration. The engine does not generate this form.

---

## 5. Notes for Your Tax Advisor & Hacienda

### Summary for Your Asesor Fiscal
Provide the following information to your gestor when submitting your report:
* "This report uses a strict **FIFO cost basis matching** and applies official **ECB daily exchange rates** on transaction dates."
* "Transaction fees (Commissions, SEC, and Brokerage Assist) have been deducted as *gastos inherentes* (Art. 35 LIRPF)."
* "The **2-month wash sale rule** (Art. 33.5.f LIRPF) has been applied to defer losses against the replacement shares held at the time of each sale. Deferred losses are integrated in the year those replacement shares are transferred, without amending the year of origin (DGT V1547-16, V1035-18)."
* "The engine scans for **ESPP early sales** (< 3 years) and separates the discount amount to be declared as *Rendimiento del Trabajo* via a *Declaración Complementaria* for the purchase year."

### For Hacienda
The Spanish PDF report (`tax_report_ES_*.pdf`) generated by the engine is formatted to serve as proof for the Agencia Tributaria. It contains a complete ledger of transactions, individual FIFO lot-matching details, and calculations for both capital gains (*Base del Ahorro*) and salary adjustments (*Rendimiento del Trabajo*).
