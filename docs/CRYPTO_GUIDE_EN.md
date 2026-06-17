# Crypto Capital Gains — Pionex & Binance

🇺🇸 English | 🇪🇸 [Español](CRYPTO_GUIDE_ES.md)

> ⚠️ Same disclaimer as the main README: this tool is an aid, not tax advice. Verify the result with a qualified *Asesor Fiscal* before filing.

## Why this exists

In Spain, **every disposal of a crypto-asset** is a capital gain/loss in the savings base (*base del ahorro*) — whether you sell it for a stablecoin, for euros, or swap it for another coin (a *permuta*). The gain is the difference between the **transfer value** and the **acquisition value**, both in EUR at the **official ECB rate on each operation's date**, and lots are matched **FIFO per homogeneous asset** (i.e. per coin), exactly like stocks.

This tool reads your exchange exports, rebuilds one FIFO queue per coin, and produces a per-coin capital-gains report plus an optional combined report that merges crypto with your stock gains into a single Modelo-100 savings base.

## The one rule to understand

> **FIFO grouping key = the coin. Each coin is its own queue; the exchange is just a label.**

- Each coin (BTC, ETH, SOL, …) gets one independent FIFO queue.
- The **same coin across exchanges merges** into one queue, in true chronological (UTC) order — so a cheap BTC lot bought on Binance is consumed before a dearer one bought later on Pionex.
- Stablecoins (USDT, USDC, USD, DAI, BUSD, FDUSD, TUSD, USDP, USDD) are treated as **USD cash**: their EUR value is the quote amount at the ECB USD/EUR rate of the trade date. They never get their own queue.

## Getting your exports

Place each exchange's export under `input/crypto/`:

```
input/crypto/
├── pionex/trading.csv
└── binance/<anything>Spot-Trade-History<anything>.csv
```

- **Pionex** → export your trade/order history as `trading.csv`. Columns used: `date(UTC+0)`, `symbol` (e.g. `BTC_USDT`), `side`, `executed_qty`, `amount` (total in the quote asset), `fee`, `fee_coin`.
- **Binance** → export your **Spot Trade History**; any filename containing `Spot-Trade-History` and ending in `.csv` is picked up. Columns used: `Time` (local time, default **UTC+2**, shifted to UTC), `Pair` (e.g. `SOLUSDC`), `Side`, `Executed` (e.g. `50SOL`), `Amount` (e.g. `4750USDC`), `Fee`.

Either source is optional — provide whichever you have. Copy-paste-ready templates: [`crypto-pionex.example.csv`](crypto-pionex.example.csv) · [`crypto-binance.example.csv`](crypto-binance.example.csv).

## Running it

```bash
# Per-coin crypto report (console + CSV + bilingual HTML)
uv run tax-crypto --input-dir input/crypto

# Crypto merged with your stocks into one savings base (bilingual HTML)
uv run tax-combined
```

`tax-crypto` accepts `--input-dir`, `--output-dir`, and `--wash-sale`. `tax-combined` accepts `--input-dir` (stocks), `--crypto-dir` (defaults to `<input-dir>/crypto`), `--output-dir`, and `--lang` (`es` / `en` / `both`).

## What you get

- **Console summary:** realised gains/losses/fees per coin and per year, a combined yearly savings-base table (with an *isolated* tax estimate), and your open positions (unrealised, informational).
- **`crypto_disposals_<timestamp>.csv`:** one row per disposal (date, coin, quantity, proceeds, cost basis, fee, gain/loss in EUR) for your records.
- **Bilingual HTML reports** (`crypto_tax_report_EN/ES_<timestamp>.html`) with summary, charts, positions, a Modelo-100 section, and the full disposal list.

## What is skipped, and other caveats

- **Stablecoin-base trades** (e.g. a USDC→USDT convert) are treated as cash and produce no taxable position.
- **Crypto-to-crypto swaps** quoted in a non-stablecoin (e.g. ETH/BTC) are **out of scope for this MVP** and are skipped with a warning — even though Spain *does* treat a *permuta* as taxable. Handle those manually for now.
- **Fees** are deducted from the gain on SELL events, valued in the quote asset (a fee paid in the base coin is valued at the trade's unit price; an unsupported fee coin such as BNB is ignored with a warning).
- **Missing acquisition history:** if your data sells more of a coin than it ever shows you buying, a **synthetic opening lot** is inserted (priced at the first sell) so the queue never goes negative, and a warning is printed. Provide the complete history to avoid this.
- **Wash-sale (2-month rule):** off by default. Per DGT criteria, crypto-assets are **not *valores homogéneos***, so the anti-loss-washing rule (Art. 33.5 LIRPF) **does not apply**. `--wash-sale` exists only as an explicit advisor-directed override — leave it off unless your advisor specifically tells you otherwise.
- **The tax estimate is isolated** — it ignores your stock gains, dividends/interest, and prior-year loss carryforward. Use `tax-combined` for the real savings base.

## Try it with demo data (no real data needed)

```bash
uv run tax-demo --crypto      # per-coin crypto demo (BTC + ETH + SOL)
uv run tax-demo --combined    # stock portfolio + crypto merged
```

Both run on offline sample data with manual FX rates, so no network call is made.

## Quick start

```bash
# 1. drop your exports in place
mkdir -p input/crypto/pionex input/crypto/binance
cp ~/Downloads/trading.csv                 input/crypto/pionex/
cp ~/Downloads/*Spot-Trade-History*.csv    input/crypto/binance/

# 2. run the per-coin report …
uv run tax-crypto --input-dir input/crypto

# 3. … or merge it with your stocks
uv run tax-combined
```
