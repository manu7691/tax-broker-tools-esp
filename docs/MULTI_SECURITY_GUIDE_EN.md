# Multiple Securities & Brokers — Portfolio Mode

🇺🇸 English | 🇪🇸 [Español](MULTI_SECURITY_GUIDE_ES.md)

> ⚠️ Same disclaimer as the main README: this tool is an aid, not tax advice. Verify the result with a qualified *Asesor Fiscal* before filing.

## Why this exists

By default the engine is **single-security**: it assumes every transaction is the same employer stock (e.g. DT). But Spanish FIFO is **per security (per ISIN)**, and the taxable result is the **aggregate** of every security's net gain/loss in the *base del ahorro*. So if you also traded TSLA, NVDA, … (on Revolut, or another account), those gains/losses belong in the same return.

**Portfolio mode** processes *all* your securities — each with its own correct FIFO queue — and rolls them up into one Modelo-100-ready savings base.

## The one rule to understand

> **FIFO grouping key = ISIN. The broker is just a label.**

- Each **ISIN** gets one independent FIFO queue. Tickers are never mixed.
- The **same ISIN across brokers merges** into one queue (e.g. employer DT at E\*TRADE + DT bought on Revolut → one DT queue). This is what Spanish law requires for *homogeneous* securities.
- The broker only affects **reporting** (the per-broker subtotal), never the FIFO grouping.

## Turning it on

Any one of these enables portfolio mode:

1. **Launcher:** run *Calculate Tax* — it now asks *“Process ALL securities across brokers?”*. Answer **y**.
2. **CLI:** `tax-engine --all-securities`
3. **Just create `input/securities.json`** — its mere presence auto-enables portfolio mode.

With none of these, behaviour is exactly as before (primary security only). Single-security mode is unchanged.

## `input/securities.json`

All fields are optional:

```json
{
  "include": ["DT", "TSLA", "NVDA"],
  "isin_map": { "TSLA": "US88160R1014" },
  "primary": "DT"
}
```

| Field | Meaning |
|-------|---------|
| `include` | Whitelist of tickers/ISINs to process. **Empty or absent = process everything detected.** |
| `isin_map` | `ticker → ISIN`. Needed only for the Revolut **account-statement** export, which has no ISIN column. Lets that security merge across brokers reliably. |
| `primary` | Your main/employer security (ticker or ISIN). Informational. |

### Why `isin_map` matters

The Revolut **account statement** (the preferred export) is **ticker-only** — it carries no ISIN. The engine resolves each ticker to an ISIN in this order:

1. your `isin_map`,
2. ISINs **learned automatically** from a Revolut *realized-gains* export in the same folder (that export *does* carry ISINs),
3. a local cache (`.isin_cache.json`) remembered from previous runs.

If a ticker can't be resolved, it still works — it just groups **by ticker** instead of ISIN, and you'll see a one-line note. That's safe as long as the ticker never changed ISIN; the only thing you lose is reliable cross-broker merging for that security. Add it to `isin_map` to be certain.

> **Tip:** put the **employer stock's ISIN in `input/ticker.json`** too (`{"ticker":"DT","isin":"US..."}`). Otherwise the E\*TRADE shares group by ticker and can't merge with a same-ISIN feed under a different ticker.

## What you get

**PDF report** (`--all-securities`):

- A **Portfolio Summary by Security** table — invested cost basis, realized gains/losses, net, and open position per security, with a portfolio Total row.
- A separate **transaction ledger + FIFO detail** section per security (each on its own page).
- The **per-broker** realized G/L subtotal (across securities).
- The **combined savings base**: the 4-year loss carryforward and the 25% cross-category offset (against dividends/interest) run on the **portfolio total** — because those are not per-security in Spanish law. The **2-month wash-sale rule stays per security**.

**Console:** a per-security *Current Positions* breakdown (instead of one meaningless mixed-securities average).

**Dashboard** (`generate_charts.py`): in portfolio mode the dashboard becomes **per-security**. A **security selector** (dropdown in the sticky header) switches the *entire* dashboard — price/trend, sell simulator, gains decomposition, FX history, live price and peers — to the chosen security. The employer-stock-only panels (**ESPP** countdown and **RSU** hold-vs-vest) appear only for securities that have that data and are hidden for plain Revolut holdings. There's also a **Portfolio breakdown** chart (invested + realized per security) and the per-broker chart in the **Advanced** tab.

- **Per-security peers:** the comparison peers can be set per ticker in `input/peers.json` — a flat list applies to all, or a map `{ "DT": ["DDOG","ESTC"], "TSLA": ["RIVN","LCID","NIO"] }` gives each security its own peer group (see [`docs/peers.example.json`](peers.example.json)).
- **Per-security dividends:** the portfolio holdings table shows a **Dividends (EUR)** column per security (from the Revolut "Other income" rows). This is informational — the taxable RCM (dividend) base stays portfolio-level, as Spanish law requires.
- **Portfolio-level cards:** *Realized Gains & Estimated Tax by Year* (per-year bar), *Currency Exposure* (current EUR value by trading currency, shown when you hold more than one currency), and *Tax-Loss Harvesting* (open positions at an unrealized loss you could sell to offset this year's gains — with the 2-month wash-sale caveat).
- **Break-even is Spanish-FIFO based.** The break-even calculator values your *remaining* lots as FIFO leaves them (oldest sold first → the newest, often priciest, lots remain), in **euros** shown as a USD price at today's rate — so it can differ from E\*TRADE's US (specific-ID / average-cost) break-even. When a security spans brokers it offers a **Broker filter** (All / E\*TRADE / Revolut), but that's *informational only*: Spanish FIFO is one pool per ISIN, so a per-broker break-even isn't a tax figure.

## Currencies

Conversion to EUR uses the official **ECB reference rate per date**. EUR is 1:1; **any ECB currency** (USD, GBP, CHF, JPY, …) is converted; currencies the ECB does not publish are skipped with a warning.

## Caveats

- **Complete history per security.** Each queue needs the full acquisition history, or FIFO could try to sell more than it holds (you'll get a clear per-security error).
- **Cross-broker accuracy depends on ISINs.** Without an ISIN, a security only merges with *same-ticker* feeds.
- **Corporate actions** beyond forward stock splits (mergers, spin-offs, ISIN changes) are not modelled.
- **Modelo 720** reminder: foreign holdings above €50k across all platforms may be reportable — out of scope here.

## Try it with demo data (no real data needed)

Want to see portfolio mode before wiring up your own files? Both demos take
`--all-securities`, which uses a built-in multi-security sample (DT + TSLA/NVDA/ADBE
on Revolut + a **GBP**-priced Shell position). It exercises the whole report:
per-security FIFO, multi-currency, wash-sale blocking, loss-carryforward
application, the dividend/interest savings base with the 25% cross-offset, and the
ESPP 3-year early-sale analysis:

```bash
tax-demo --all-securities                       # per-security PDF report
python generate_charts.py --demo --all-securities  # dashboard with the per-security chart
```

In the launcher, the two *Run Demo* options now ask whether you want the
single-symbol or multi-symbol portfolio demo.

## Quick start

```bash
# 1. (optional) map any ticker-only Revolut securities to their ISIN
echo '{ "isin_map": { "TSLA": "US88160R1014" } }' > input/securities.json

# 2. run portfolio mode
tax-engine --all-securities          # PDF report
python generate_charts.py            # dashboard (per-security breakdown chart)
```
