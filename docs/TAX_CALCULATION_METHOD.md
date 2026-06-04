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
| **Loss Carryforward** | ❌ Out of Scope | Art. 49 LIRPF — Must be handled manually in Modelo 100 |
| **Modelo 720 (Foreign Assets)** | ❌ Out of Scope | Separate annual obligation (if assets abroad > €50,000) |

---

## 2. Core Calculation Methodology

### FIFO Lot Matching (First-In, First-Out)
Under Spanish law (**Art. 37.2 LIRPF**), shares of the same company are homogeneous. When you execute a sell order, the engine matches the sold shares against your oldest available share acquisitions in chronological order.
* Realized gain/loss is calculated per lot:
  $$\text{Realized Gain/Loss} = (\text{Selling Price in EUR} - \text{Acquisition Cost in EUR}) \times \text{Shares}$$
* If a single sell transaction spans multiple purchase lots, the transaction is split and calculated on a per-lot basis.
* Stale lots are completely cleared once their remaining shares reach `0`.

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
  $$\text{Blocked Shares} = \min(\text{Sold Shares}, \text{Replacement Shares Remaining in Portfolio})$$
* **Correct Application:** The engine only blocks losses against replacement shares that *remain in your portfolio* (shares consumed by the sell itself do not trigger a wash sale).
* **Filing Treatment:** Blocked losses are deferred and cannot offset gains in the current tax year. They are carried forward as "blocked" until the replacement shares are sold.

### Transaction & Transfer Fee Deductions (*Gastos Inherentes*)
According to **Art. 35.1 and 35.2 LIRPF**, commissions and fees directly related to the acquisition or transmission of shares are deductible.
* The engine automatically parses and deducts **Commissions**, **SEC Fees**, and **Brokerage Assist Fees** from capital gains.
* Users can manually record platform wire transfer fees (for transferring cash out of E-Trade) in the input file to have them deducted as inherent transaction costs.

### ESPP 3-Year Holding Period Exemption (Art. 42.3.f LIRPF)
Discounts on ESPP purchases (up to €12,000/year) are tax-exempt if:
1. The shares are held for at least **3 years** from the purchase date.
2. The ESPP program was offered to all employees under the same conditions (verified via company enrollment sign-off).

**Early Sale Detection:**
* The engine scans all FIFO sales. If ESPP shares are sold before the 3-year mark, the engine flags the corresponding purchase discount as **taxable salary income** (*Rendimiento del Trabajo*).
* The tax is imputed to the **Purchase Year**, requiring a **Complementary Tax Return** (*Declaración Complementaria*) for that year, which may incur delay interest but no penalties if filed voluntarily.

---

## 4. Scope Limitations & Caveats

1. **Loss Carryforward (Art. 49 LIRPF):** The engine calculates net taxable bases on a strictly yearly basis. If you have net capital losses in a year, they can be carried forward to offset gains for the next 4 years. **Your tax advisor must apply this carryforward manually on your tax return.**
2. **Single Ticker Assumption:** The engine assumes all input transactions apply to the same company stock. If you trade multiple tickers, separate files must be processed to prevent FIFO lot mixing.
3. **Modelo 720:** If your foreign bank accounts or stock portfolios (like E-Trade) exceed a value of €50,000 at any point during the year (or as of Dec 31st), you must file the Modelo 720 informative declaration. The engine does not generate this form.

---

## 5. Notes for Your Tax Advisor & Hacienda

### Summary for Your Asesor Fiscal
Provide the following information to your gestor when submitting your report:
* "This report uses a strict **FIFO cost basis matching** and applies official **ECB daily exchange rates** on transaction dates."
* "Transaction fees (Commissions, SEC, and Brokerage Assist) have been deducted as *gastos inherentes* (Art. 35 LIRPF)."
* "The **2-month wash sale rule** (Art. 33.5.f LIRPF) has been applied to defer losses where replacement shares remain in the portfolio."
* "The engine scans for **ESPP early sales** (< 3 years) and separates the discount amount to be declared as *Rendimiento del Trabajo* via a *Declaración Complementaria* for the purchase year."

### For Hacienda
The Spanish PDF report (`tax_report_ES_*.pdf`) generated by the engine is formatted to serve as proof for the Agencia Tributaria. It contains a complete ledger of transactions, individual FIFO lot-matching details, and calculations for both capital gains (*Base del Ahorro*) and salary adjustments (*Rendimiento del Trabajo*).
