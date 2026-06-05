# French Tax Adaptation & Compliance Analysis

This document outlines the technical and regulatory requirements to adapt the existing E*TRADE Spanish Tax Engine to be fully compliant with French tax legislation. It compares the two tax regimes, analyzes the algorithmic modifications needed, and outlines the impact on the codebase.

> **Baseline note (synced with `main`).** Since this proposal was first written, the Spanish engine gained several features the France adaptation can **reuse or adapt rather than build from scratch**: a 4-year **loss-carryforward ledger** (`compute_carryforward`), a **dividend/interest (RCM) savings base with the 25% cross-category offset** (`compute_savings_ledger`, fed by USD payments converted at the ECB rate per date), **stock-options exercise** support, an **on-disk ECB rate cache**, a **Modelo 100 form-mapping** section in the report, and the `tax-savings-income` CLI. The comparison and codebase-impact sections below reflect that current baseline.

---

## 1. Executive Comparison: Spain vs. France

The core differences in how stock acquisitions and sales (RSUs, ESPPs, Stock Options) are taxed in Spain and France:

| Feature / Rule | Spain (Current Engine) | France (Proposed Engine) | Regulatory Reference (France) |
| :--- | :--- | :--- | :--- |
| **Share Matching (Capital Gains)** | **Strict FIFO** (First-In, First-Out) | **PMP** (Prix Moyen Pondéré / Weighted Average Price) | Art. 150-0 D of CGI |
| **Wash Sale Rules** | **2-Month Rule** (losses blocked if homogenous repurchases occur) | **None** (Wash sales are fully permitted) | N/A |
| **Loss Carryforward** | Offset current year, carry forward **4 years** (already simulated by the engine's carryforward ledger) | Offset current year, carry forward **10 years** | Art. 156 of CGI |
| **Dividends & Interest (RCM)** | Savings base; a capital loss may offset up to **25% cross-category** of dividend/interest income (engine supports this via `savings_income.json`) | Taxed under **PFU 30%** (or progressive); capital losses offset capital gains only — **no cross-category** offset with dividends | Art. 200 A of CGI |
| **Default Tax Rate (Capital Gains)** | Progressive Savings scale (**19% to 28%**) | Flat Tax (**PFU** - *Prélèvement Forfaitaire Unique*) of **30%** (12.8% Income Tax + 17.2% Social Charges) | Art. 200 A of CGI |
| **Alternative Tax Rate** | N/A | Progressive income tax scale (based on income bracket + 17.2% social charges; 6.8% CSG deductible) | Art. 200 A of CGI |
| **RSU / ESPP Acquisition Gains** | Taxed in the year of vest/purchase (under certain limits and holding periods) | **Imposition Différée** (Deferred Taxation): Acquisition gains are only taxed in the *year of sale* | Art. 80 quaterdecies of CGI |
| **Acquisition Gain matching** | Auto-detected holding periods | Uses **FIFO** specifically to trace which acquisition lot a partial sale originates from (for salary income calculation) | BOI-RSA-ES-20-20-20 |
| **Inherent Fees Deduction** | Allowed (Commissions, SEC fees) | Allowed (Commissions, transaction fees) | Art. 150-0 D of CGI |

---

## 2. Core Algorithmic & Mathematical Adaptation

The primary mathematical challenge in adapting the engine to France is the **dual-method approach** required for RSU/ESPP sales. 

When a taxpayer sells shares acquired through employee share ownership programs, the transaction generates two distinct gains that must be reported separately:

### A. Plus-value de cession (Capital Gains)
*   **Method:** **PMP (Prix Moyen Pondéré)**.
*   **Logic:** All shares of the same security are considered homogeneous and form a single pool.
*   **Formula:**
    $$\text{PMP}_{new} = \frac{\text{Remaining Shares} \times \text{PMP}_{old} + \text{New Shares} \times \text{Acquisition FMV}}{\text{Remaining Shares} + \text{New Shares}}$$
*   When a sale occurs, the cost basis of the sold shares is:
    $$\text{Cost Basis} = \text{Sold Shares} \times \text{PMP}_{current}$$
    *The PMP itself remains unchanged by a sale transaction.*

### B. Gain d'acquisition (Acquisition Gains)
*   **Method:** **FIFO (First-In, First-Out)**.
*   **Logic:** Since RSU and ESPP benefits are taxed as salary in the year of the sale, and different vesting events have different historical FMVs (and potentially different tax regimes depending on the company plan's authorization date), the French tax authority uses FIFO to identify which specific acquisition event the sold shares belong to.
*   **Formula:** Sold shares are matched against the oldest available acquisition lots, and the salary benefit to report is:
    $$\text{Taxable Salary} = \sum (\text{Matched Shares} \times \text{Vesting Date FMV})$$

---

### Step-by-Step Calculation Example
Suppose a user has the following RSU vesting events (acquisitions) and subsequent sale:

1.  **Acquisition 1:** 50 RSU shares vest at FMV **$100** (total acquisition value = $5,000)
2.  **Acquisition 2:** 50 RSU shares vest at FMV **$120** (total acquisition value = $6,000)
    *   *Current State:* Total shares = 100. Running average cost basis (**PMP**) = **$110**
3.  **Transaction:** User sells 60 shares at **$130**
    *   **Capital Gain (PMP):**
        *   Cost basis of sold shares = $60 \times \$110 = \$6,600$.
        *   Sale value = $60 \times \$130 = \$7,800$.
        *   **Plus-value de cession** = $\$7,800 - \$6,600 =$ **$1,200** (taxed under 30% PFU or progressive scale).
    *   **Salary Benefit (FIFO Match):**
        *   50 shares matched to Acquisition 1 (FMV $100) $\to 50 \times \$100 = \$5,000$.
        *   10 shares matched to Acquisition 2 (FMV $120) $\to 10 \times \$120 = \$1,200$.
        *   **Gain d'acquisition** = $\$5,000 + \$1,200 =$ **$6,200** (taxed as salary income *Traitements et salaires* in the year of the sale).

---

## 3. Codebase Impact Analysis

Since the ingestion layer reads raw transaction files, the parser logic is unaffected. The required code adaptations are focused on the models, calculations, and reporting layers:

```
src/
├── tax_engine/
│   ├── models.py             --> Add Country mode; PMP state on TaxEngineState; French summary
│   ├── tax_engine.py         --> Implement PMP, disable Spain wash-sales, branch on country;
│   │                             adapt compute_carryforward (4yr -> 10yr) & compute_savings_ledger
│   ├── cli_main.py           --> French CLI text, output summaries, form mapping
│   ├── cli_savings_income.py --> Reuse for French dividend/interest input (PFU)
│   ├── ecb_rates.py          --> NO CHANGES (USD->EUR per-date conversion is country-agnostic)
│   └── (parsers)             --> NO CHANGES (pdf/excel parsers are country-agnostic)
```

**Reusable building blocks already on `main`** (adapt, don't rebuild):

| Existing | France reuse |
| :--- | :--- |
| `compute_carryforward` + `CarryforwardLedger` (4-year ledger, expiry tracking) | Change the window to **10 years**; France has no wash-sale blocking to feed in. |
| `compute_savings_ledger` + `SavingsLedger`/`SavingsIncomeYear` (dividends/interest, ECB per date) | Reuse the dividend/interest plumbing; **drop the 25% cross-category** step (not allowed in France) and tax under PFU. |
| `cli_savings_income` (USD-payment entry, ECB-converted) | Reuse as-is for French RCM input. |
| ECB on-disk cache (`ecb_rates.py`) | Reuse unchanged. |
| Modelo 100 form-mapping section + report structure | Mirror as the **French form mapping** (2042 / 2042 C / 2074 / 3916). |
| FIFO lot matching (`_process_sell`) | Keep for the *gain d'acquisition* salary trace; add **PMP** in parallel for *plus-value de cession*. |

### A. Data Models (`src/tax_engine/models.py`)
*   **Introduce Country Mode:** Add a `Country` configuration or configuration flags (e.g., `SPAIN` or `FRANCE`) to guide the engine.
*   **PMP State:** Update `TaxEngineState` to track the running `PMP` alongside the existing acquisition lot lists.
*   **French Tax Summary:** Update `YearlyTaxSummary` to compute:
    *   Flat Tax (30% PFU) and progressive scale estimations.
    *   Aggregated *Gain d'acquisition* (salary income) matching the sales of the year.

### B. Core Tax Engine (`src/tax_engine/tax_engine.py`)
*   **Branching Calculations:** Based on the country config:
    *   If `SPAIN`: Run FIFO lot matching, apply the 2-month wash sale filter.
    *   If `FRANCE`: Run PMP calculation for capital gains, bypass wash-sale logic, and run FIFO matching strictly to compile RSU/ESPP salary benefits for sold shares.

### C. Reporting & CLI (`src/tax_engine/cli_main.py` & PDF Generator)
*   **Translations:** Translate tables and keys to French (e.g., *Plus-value de cession*, *Gain d'acquisition*, *Prélèvements Sociaux*).
*   **French Tax Form Mapping:** Map the output summaries directly to the official French tax forms:
    *   **Form 2042 C:** Section for salary income from RSU/ESPP acquisition gains.
    *   **Form 2042:** Boxes **3VG** (Total gains) / **3VH** (Total losses).
    *   **Form 2074:** Details ledger for capital gains.
    *   **Form 3916:** Foreign account declaration (reminder to declare the E*TRADE broker account).

---

## 4. Complexity & Timeline

*   **Overall Complexity:** **Moderate**
*   **Estimated Effort:** **2 to 3 weeks** (including testing and verification)

### Key Milestones
1.  **Refactor engine core** to support PMP calculations and dual-method tracking (PMP + FIFO).
2.  **Add French tax parameters** (PFU 30%, social contributions) and **adapt the existing carryforward ledger from 4 to 10 years**.
3.  **Implement unit tests** for PMP and French RSU sale scenarios.
4.  **Create French-specific PDF report templates** and CLI prompts mapping to *impots.gouv.fr* tax forms.
