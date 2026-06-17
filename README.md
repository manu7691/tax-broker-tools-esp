# Spanish Broker Tax Tools / Herramientas Fiscales de Brókers en España

🇺🇸 [English README](#english-version) | 🇪🇸 [README en Español](#version-en-espanol)

---

<a name="english-version"></a>
# 🇺🇸 Spanish Broker Tax Tools

Calculates capital gains tax using the Spanish FIFO cost basis method and progressive savings tax scale for stocks acquired through RSU vesting, ESPP purchases, and options exercises.

> ⚠️ **DISCLAIMER**: This software is provided "as is", without warranty of any kind. **Use at your own risk.** The calculations are based on my understanding of Spanish tax law and may contain errors. This tool is not a substitute for professional tax advice. Always verify the results with a qualified tax advisor (*Asesor Fiscal*) before filing your tax return. The author(s) assume no liability for any financial losses, penalties, or other damages arising from the use of this software.

---

## 📖 Bilingual Documentation

To share this repository with your teammates or show it to your tax advisor, the following resources are available in both English and Spanish:

*   **Calculator Architecture & Data Flow Visual Guide:**
    *   🇺🇸 [CALCULATOR_GUIDE_EN.md](docs/CALCULATOR_GUIDE_EN.md)
    *   🇪🇸 [CALCULATOR_GUIDE_ES.md](docs/CALCULATOR_GUIDE_ES.md)
*   **ESPP Exemption (Art 42.3.f LIRPF) & FIFO Trap Guide:**
    *   🇺🇸 [ESPP_TAX_GUIDE_EN.md](docs/ESPP_TAX_GUIDE_EN.md)
    *   🇪🇸 [ESPP_TAX_GUIDE_ES.md](docs/ESPP_TAX_GUIDE_ES.md)
*   **Spanish FIFO Tax Methodology & Compliance Audit:**
    *   🇺🇸 [TAX_CALCULATION_METHOD.md](docs/TAX_CALCULATION_METHOD.md)
    *   🇪🇸 [TAX_CALCULATION_METHOD_ES.md](docs/TAX_CALCULATION_METHOD_ES.md)
*   **Interactive Charts & Tax Dashboard Guide:**
    *   🇺🇸 [DASHBOARD_GUIDE_EN.md](docs/DASHBOARD_GUIDE_EN.md)
    *   🇪🇸 [DASHBOARD_GUIDE_ES.md](docs/DASHBOARD_GUIDE_ES.md)
*   **How the PDF Report & Dashboard Connect:**
    *   🇺🇸 [REPORT_VS_DASHBOARD_EN.md](docs/REPORT_VS_DASHBOARD_EN.md)
    *   🇪🇸 [REPORT_VS_DASHBOARD_ES.md](docs/REPORT_VS_DASHBOARD_ES.md)
*   **Multiple Securities & Brokers (Portfolio Mode) Guide:**
    *   🇺🇸 [MULTI_SECURITY_GUIDE_EN.md](docs/MULTI_SECURITY_GUIDE_EN.md)
    *   🇪🇸 [MULTI_SECURITY_GUIDE_ES.md](docs/MULTI_SECURITY_GUIDE_ES.md)
*   **How the PDF Report Is Generated (slide deck for your tax advisor):**
    *   🇺🇸 [PDF_REPORT_PRESENTATION_EN.md](docs/PDF_REPORT_PRESENTATION_EN.md) · [PDF](docs/PDF_REPORT_PRESENTATION_EN.pdf)
    *   🇪🇸 [PDF_REPORT_PRESENTATION_ES.md](docs/PDF_REPORT_PRESENTATION_ES.md) · [PDF](docs/PDF_REPORT_PRESENTATION_ES.pdf)

---

## 🚀 Easy Start (Mac Users)

If you are not a developer, you can simply use the provided script:

1.  **Download the entire project** as a ZIP from GitHub (click "Code" → "Download ZIP") and extract it.
2.  Double-click the `run_tax_engine.command` file inside the extracted folder.
3.  It will automatically set up Python, install dependencies, and open a menu.
4.  Follow the menu options to Login, Download Data, and Calculate Tax.

*Note: The first time you run it, you might need to right-click and select "Open" if macOS warns about an unidentified developer, or allow it in System Settings.*

## 🚀 Easy Start (Windows Users)

If you are not a developer, you can simply use the provided script:

1.  **Download the entire project** as a ZIP from GitHub (click "Code" → "Download ZIP") and extract it.
2.  Double-click the `run_tax_engine.bat` file inside the extracted folder.
3.  It will automatically set up Python, install dependencies, and open a menu.
4.  Follow the menu options to Login, Download Data, and Calculate Tax.

## 🚀 Easy Start (Linux Users)

If you are not a developer, you can use the same script as Mac users:

1.  **Download the entire project** as a ZIP from GitHub (click "Code" → "Download ZIP") and extract it.
2.  Open a terminal in the extracted folder and run:
    ```bash
    chmod +x run_tax_engine.command
    ./run_tax_engine.command
    ```
3.  It will automatically set up Python, install dependencies, and open a menu.
4.  Follow the menu options to Login, Download Data, and Calculate Tax.

## 🐳 Easy Start (Devcontainer)

In Visual Studio Code, open this workspace using the Dev Containers extension to run it inside the provided development container.

The container includes a lightweight desktop (fluxbox) and a Chromium browser installed via Playwright.

Access the desktop:
- Open in a browser at http://localhost:6080
- Or connect with a VNC client on port 5901 (port may vary; check the VS Code "Ports" tab)

Start the download assistant (it opens the browser inside the container):
```bash
uv run tax-download
```

Use the assistant to log in to E-Trade and download the required files (ESPP history, Orders, RSU confirmations).

---

## 🛠️ Developer Quick Start

### 1. Setup environment
```bash
brew install uv
uv sync --all-extras
uv run pre-commit install
```

### 2. Run Demo
To see the tax engine in action with E*TRADE + Revolut sample data:
```bash
uv run demo.py
```

### 3. Fetch Your Data
To automate downloading transaction history from E-Trade:

1.  **Install Playwright browsers** (first time only):
    ```bash
    uv run playwright install chromium
    ```

2.  **Run the download assistant**:
    ```bash
    uv run tax-download
    ```
    This will guide you through login and automatically download all required files (ESPP history, Orders, and RSU confirmations).

### 4. Run Analysis
Once your data is in the `input/` directory:
```bash
uv run main.py
```
It will generate a PDF file `tax_report_*.pdf`.

**Crypto (optional).** If you also trade crypto, place your exchange exports under `input/crypto/` (see *Input Files* below) and run `uv run tax-crypto` for a per-coin FIFO report, or `uv run tax-combined` to merge stocks + crypto into one Spanish savings base. To preview either flow on offline sample data: `uv run tax-demo --crypto` or `uv run tax-demo --combined`.

---

## 📝 Filing Spanish Renta (Modelo 100)

The tax report output provides a **Yearly Tax Summary (Modelo 100 - Savings Base)** section.

For each tax year, it lists:
- **Total Gains / Total Losses:** Sums of realized capital gains and losses.
- **Blocked Losses:** Losses deferred due to the 2-month wash sale rule.
- **Deductible Losses:** Losses usable this year after removing blocked ones.
- **Net Taxable Savings Base:** The final net amount after applying allowed losses against gains.
- **Estimated Tax (Isolated):** Tax on *these stock gains alone* using the savings scale (19–28%). ⚠️ This is **not** your final liability — it ignores your total savings income (dividends, interest) and prior-year loss carryforward. Treat it as a guide.

The report also includes:
- **Loss Carryforward Ledger (Art. 49 LIRPF):** simulates the 4-year offset of net losses against later gains and flags losses that expire unused. Seed pre-window losses via `input/prior_losses.json` (e.g. `{"2019": 1500}`) or `--prior-losses <file>`.
- **Savings Base with dividends/interest (optional):** provide `input/savings_income.json` (or `--savings-income <file>`) to add your E\*TRADE dividends and cash interest (*rendimientos del capital mobiliario*). The report computes the **25% cross-category offset** (a stock loss offsetting dividend/interest income) and shows the combined savings base. Foreign tax withheld is shown for reference; the *deducción por doble imposición* is left to your advisor.
  - **Recommended format — USD payments (exact):** a list of payments, each with its date; the engine converts each to EUR at the **ECB rate on that date**, exactly like stock trades:
    ```json
    [ { "date": "2024-03-15", "type": "dividend", "amount_usd": 80, "foreign_tax_usd": 12 } ]
    ```
    **Auto-import from E\*TRADE (recommended):** menu **option 3 → "Auto-download"** (or `tax-download-dividends` then `tax-import-dividends`) scrapes your dividends-only cash transactions into `input/dividends/Cash_Transactions.xlsx` and merges them into `savings_income.json`. Interest lines are classified as `interest`, foreign-tax-withheld lines are captured as `foreign_tax_usd`, and re-running is idempotent (existing payments are de-duplicated by date + description + amount). The full **Download E\*TRADE Data** (option 2) also runs this step automatically.
    Or enter them by hand with the interactive helper (no JSON editing): `.venv/bin/python -m tax_engine.cli_savings_income` — or one-shot: `... cli_savings_income --date 2024-03-15 --type dividend --amount-usd 80 --foreign-tax-usd 12`. A starter template: `cp docs/savings_income.example.json input/savings_income.json`.
  - **Alternative — EUR per year (manual conversion):** if you only have an annual total (e.g. off a **Form 1042-S** / 1099 in *Accounts → Documents → Tax Center*), you can instead supply pre-converted EUR amounts: `{"2024": {"dividends_eur": 320, "interest_eur": 15, "foreign_tax_eur": 48}}`. Note this can't apply per-payment exchange rates.
- **"What to declare" summary (*¿Qué declarar en Hacienda?*):** a prominent per-year section that gives the exact figures to enter, grouped by the three IRPF buckets — employment income (only ESPP sold before 3 years), capital gains (*valor de transmisión* / *adquisición* → deductible saldo) and the savings base (+ dividends/interest) — each with its indicative casilla.

> **Complete years only:** the report covers finished tax years; the in-progress current year is excluded from every table (it isn't declarable yet), though its transactions still count in the FIFO calculation.

> **CLI options:** `uv run main.py` accepts `--input-dir`, `--output-dir`, `--prior-losses`, and `--savings-income` (all optional).

Copy the net taxable savings base into the capital gains from stock transfers section of your annual IRPF tax return.

---

## 📊 Interactive Charts & Tax Dashboard

Beyond the PDF report, you can generate an **interactive HTML dashboard** that turns your data into a plain-language picture of your stock: what you own, what it's worth today, what you'd net after tax if you sold, and when each ESPP batch becomes tax-free. It is built for a non-finance audience and has a privacy-blur mode and an EN/ES toggle.

Generate it from **menu option 6** ("Generate Charts & Tax Dashboard"), or directly:

```bash
uv run generate_charts.py            # auto-detects ticker, fetches the live price
uv run generate_charts.py --ticker DDOG            # or specify a stock ticker manually
uv run generate_charts.py --current-price 45.50   # or pin a fixed USD price
```

It writes `charts_dashboard.html` (gitignored) — open it in any browser. Full walkthrough: 🇺🇸 [DASHBOARD_GUIDE_EN.md](docs/DASHBOARD_GUIDE_EN.md) · 🇪🇸 [DASHBOARD_GUIDE_ES.md](docs/DASHBOARD_GUIDE_ES.md).

> ⚠️ The dashboard is informational only and fetches a **live market price from Yahoo Finance**. No personal data leaves your machine in the file itself.

---

## 🌐 Web Interface

For an interactive browser-based view of all your tax data, launch the local web server. It runs the existing engines on-demand and shows a five-tab dashboard where you can also enter salary and real estate income manually and compare everything against your AEAT declaration.

**First time only** — build the frontend (requires Node.js):
```bash
cd src/tax_engine/web_app/frontend
npm install
npm run build
cd -
```

Then start the server:
```bash
uv run tax-web                             # uses input/ by default, opens on port 8080
uv run tax-web --input-dir input --port 9090
```

Open `http://localhost:8080` in your browser, then click **Run Engine** to load your data.

> **Developers:** to iterate on the frontend, run two terminals — `uv run tax-web --port 8080` (backend) and `npm run dev` inside `frontend/` (Vite dev server at `http://localhost:5173`, proxies `/api` to `:8080`).

| Tab | What it shows |
|-----|---------------|
| **Stocks** | Capital gains/losses/tax by year, open positions, links to full HTML reports (EN/ES) |
| **Crypto** | Same for crypto (reads from `input/crypto/`) |
| **Work Income** | Computed ESPP early-sale discounts + manual salary entry per year |
| **Real Estate** | Manual rental income and property sale gains per year |
| **Hacienda** | Upload your AEAT Renta Web XML → preview extracted casillas → import → side-by-side comparison table (Computed vs Filed vs Difference) |

Manual entries (salary, real estate, imported AEAT casillas) are saved to `input/manual_data.json` and persist across runs.

---

## 📋 Menu Options

When you launch `run_tax_engine.command` (macOS/Linux) or `run_tax_engine.bat` (Windows), you get this menu:

| # | Option | What it does |
|---|--------|--------------|
| 1 | Login to E-Trade Plan | Opens a browser to log in (and pass MFA). **Run this first** — it saves a session. |
| 2 | Download E-Trade Data | Downloads ESPP history, Orders, RSU confirmations, option exercises and dividends into `input/` (dividends are auto-imported into `savings_income.json`). |
| 3 | Add Dividend/Interest Income | *Optional.* Auto-download dividends from E\*TRADE, or record dividend/interest payments by hand (USD + date) for the savings base. |
| 4 | Calculate Tax & PDF Reports (optional: incl. Revolut) | Runs the engine and generates the English + Spanish PDF reports (integrates Revolut CSV if present). |
| 5 | Generate Charts & Tax Dashboard (optional: incl. Revolut) | Builds the interactive `charts_dashboard.html` (auto-detects ticker; fetches a live price; includes Revolut if present). |
| 6 | Run Demo: Calculate Tax & PDF Reports | Runs on sample data so you can see the output reports without your own data. |
| 7 | Run Demo: Generate Charts & Tax Dashboard | Builds the interactive `charts_dashboard_demo.html` using simulated offline test data. |
| 8 | Exit | Quit. |

## 📂 Input Files

Everything lives under `input/`. Menu options 1–2 create most of these automatically; the last two are optional and entered by you.

| File | Required? | Where it comes from |
|------|-----------|---------------------|
| `input/espp/BenefitHistory.xlsx` | **Yes** | E-Trade → Stock Plan → Benefit History → *Download Expanded* |
| `input/orders/orders.xlsx` | For any sells | E-Trade → Stock Plan → Orders |
| `input/rsu/*.pdf` | If you have RSUs | E-Trade → Documents → RSU release confirmations |
| `input/options/*.pdf` | If you exercised options | E-Trade → Documents → option exercise confirmations |
| `input/prior_losses.json` | Optional | Pending losses from before your data window, e.g. `{"2019": 1500}` |
| `input/savings_income.json` | Optional | Dividends/interest (see *Filing Spanish Renta* above) |
| `input/ticker.json` | For Revolut | Primary/employer security, e.g. `{"ticker": "DT", "isin": "US..."}` — the ISIN drives the single-security Revolut filter |
| `input/securities.json` | Optional | Multi-security config: turns on portfolio mode and maps tickers→ISINs (see *Multiple securities & brokers* below) |
| `input/revolut/*.csv` | Optional | Revolut investment CSV — *Account statement* (preferred) or *Profit & Loss* (see below) |
| `input/crypto/pionex/trading.csv` | Optional (crypto) | Pionex → trade/order history export (see *Crypto* below) |
| `input/crypto/binance/*Spot-Trade-History*.csv` | Optional (crypto) | Binance → spot trade history export (see *Crypto* below) |

### Revolut (optional)

If you also hold **the same company stock** on Revolut, export its **account statement** — the full transaction log (`Date, Ticker, Type, Quantity, Price per share, …`) — and drop the CSV (any name) into `input/revolut/`. It includes your **buys** even for shares you never sold, so they enter the cost-basis pool. Rows are filtered by **ticker** (this export has no ISIN) — set `"ticker"` in `input/ticker.json` (or pass `--revolut-symbol`). Non-trade rows (cash top-ups/withdrawals, internal transfers) are ignored; stock splits are normalized into post-split share terms so FIFO stays consistent.

> An older **realized gains/losses** export (`Date acquired, Date sold, … ISIN, Gross proceeds`) is also accepted (filtered by ISIN; its "Other income & fees" section adds to dividend/interest totals), but it only lists *sells* — prefer the account statement above so your buys are included.

Matched rows are folded into the **same FIFO pool** as your E\*TRADE shares, so FIFO ordering and the 2-month wash-sale rule apply across both brokers, as Spain requires for homogeneous securities. Gains are computed in EUR at the **official ECB rate per date** (Revolut's own FX column is ignored). Rows in **EUR (1:1) or any ECB reference currency** (USD, GBP, CHF, …) are converted; currencies the ECB does not publish are skipped with a warning. **Requires the complete acquisition history for that security** so the global FIFO queue never goes negative. When more than one broker is present, the PDF tags each ledger row with a **Broker** column and adds a *Realized Gains/Losses by Broker* subtotal; the charts use the same combined position. Format reference: [`docs/revolut-movements.example.csv`](docs/revolut-movements.example.csv).

### Multiple securities & brokers (portfolio mode)

By default the tool tracks **one** security (your employer stock). If you also traded **other** securities (e.g. on Revolut), turn on **portfolio mode**: every security gets its own FIFO queue (grouped by **ISIN**), and the results roll up into one Spanish savings base.

- **Turn it on:** the launcher's *Calculate Tax* option now asks *"Process ALL securities across brokers?"*; or run `tax-engine --all-securities`; or simply create `input/securities.json` (its presence auto-enables it).
- **`input/securities.json`** (all fields optional): `{ "include": ["DT","TSLA"], "isin_map": {"TSLA":"US88160R1014"}, "primary": "DT" }` — `include` limits which securities are processed (empty = all), and `isin_map` supplies ISINs for the ticker-only Revolut *account statement* so the same stock merges across brokers reliably.
- **Output:** the PDF adds a **Portfolio Summary by Security** table plus a separate ledger + FIFO section per security; the savings base, 4-year carryforward and 25% cross-offset run on the portfolio total, while the 2-month wash-sale rule stays per security. The dashboard adds a per-security breakdown chart, a security selector, and **scope badges** on every card (🌐 whole portfolio vs 🏷️ selected stock) so a non-finance reader always knows which numbers they're looking at.

Full walkthrough: [MULTI_SECURITY_GUIDE_EN.md](docs/MULTI_SECURITY_GUIDE_EN.md) · [🇪🇸 ES](docs/MULTI_SECURITY_GUIDE_ES.md).

### Crypto (optional)

If you trade crypto on **Pionex** or **Binance**, drop their spot-trade exports into `input/crypto/pionex/` and `input/crypto/binance/` (Binance files match `*Spot-Trade-History*.csv`; any filename works). Each coin gets its **own FIFO queue**, and every disposal is valued in EUR at the **official ECB rate on the trade date**, exactly like stocks. Stablecoin-base trades and crypto-to-crypto swaps are skipped (out of scope), and fees are valued in the quote asset.

- **Per-coin report:** `uv run tax-crypto --input-dir input/crypto` prints a per-coin console summary and writes a per-disposal CSV plus bilingual HTML reports.
- **Combined with stocks:** `uv run tax-combined` merges the per-year stock and crypto gains/losses into one Art. 49 LIRPF savings base (bilingual HTML).
- **Try it offline:** `uv run tax-demo --crypto` (or `--combined`) runs on sample BTC/ETH/SOL data with no network call.

> The optional `--wash-sale` flag applies the 2-month homogeneous-asset rule per coin; it is **off by default** because its applicability to crypto is unsettled at AEAT.

## ❓ Troubleshooting

- **"Session expired" / redirected to login:** run **Login** (option 1) again — E-Trade sessions expire after a while.
- **Downloads fail or a page won't load:** disable ad/privacy blockers for `us.etrade.com`; they can break E-Trade's own scripts.
- **`BenefitHistory.xlsx not found`:** ensure the ESPP file is at `input/espp/BenefitHistory.xlsx` (run **Download All Data**, or place it manually).
- **Exchange-rate / network error:** the on-disk ECB cache (`.ecb_rate_cache.json`) lets repeat runs work offline, but the first run needs internet to fetch rates.
- **Browser won't launch:** reinstall the Playwright browser: `.venv/bin/playwright install chromium`.

---

<br>
<br>

<a name="version-en-espanol"></a>
# 🇪🇸 Herramientas Fiscales de Brókers en España

Calcula el impuesto sobre las ganancias patrimoniales utilizando el método de asignación FIFO obligatorio en España y la escala progresiva del gravamen del ahorro para acciones adquiridas a través de RSU (acciones gratuitas), ESPP (compra con descuento) y ejercicio de opciones.

> ⚠️ **DESCARGO DE RESPONSABILIDAD**: Este software se proporciona "tal cual", sin garantía de ningún tipo. **Su uso es bajo su propia responsabilidad.** Los cálculos se basan en mi interpretación de la normativa fiscal española y pueden contener errores. Esta herramienta no sustituye el asesoramiento fiscal profesional. Siempre verifique los resultados con un asesor fiscal colegiado antes de presentar su declaración de la renta. Los autores no asumen responsabilidad alguna por pérdidas financieras, multas u otros daños derivados del uso de este software.

---

## 📖 Documentación Bilingüe

Para compartir este repositorio con tus compañeros de equipo o mostrárselo a tu gestor fiscal, dispones de los siguientes recursos tanto en inglés como en español:

*   **Guía Visual de Arquitectura y Flujo de Datos:**
    *   🇺🇸 [CALCULATOR_GUIDE_EN.md](docs/CALCULATOR_GUIDE_EN.md)
    *   🇪🇸 [CALCULATOR_GUIDE_ES.md](docs/CALCULATOR_GUIDE_ES.md)
*   **Guía de Exención del ESPP (Art 42.3.f LIRPF) y Trampa del FIFO:**
    *   🇺🇸 [ESPP_TAX_GUIDE_EN.md](docs/ESPP_TAX_GUIDE_EN.md)
    *   🇪🇸 [ESPP_TAX_GUIDE_ES.md](docs/ESPP_TAX_GUIDE_ES.md)
*   **Metodología Fiscal FIFO y Auditoría de Cumplimiento:**
    *   🇺🇸 [TAX_CALCULATION_METHOD.md](docs/TAX_CALCULATION_METHOD.md)
    *   🇪🇸 [TAX_CALCULATION_METHOD_ES.md](docs/TAX_CALCULATION_METHOD_ES.md)
*   **Guía del Panel Interactivo de Gráficos e Impuestos:**
    *   🇺🇸 [DASHBOARD_GUIDE_EN.md](docs/DASHBOARD_GUIDE_EN.md)
    *   🇪🇸 [DASHBOARD_GUIDE_ES.md](docs/DASHBOARD_GUIDE_ES.md)
*   **Cómo se conectan el Informe PDF y el Panel:**
    *   🇺🇸 [REPORT_VS_DASHBOARD_EN.md](docs/REPORT_VS_DASHBOARD_EN.md)
    *   🇪🇸 [REPORT_VS_DASHBOARD_ES.md](docs/REPORT_VS_DASHBOARD_ES.md)
*   **Guía de Varios Valores y Brókers (Modo Cartera):**
    *   🇺🇸 [MULTI_SECURITY_GUIDE_EN.md](docs/MULTI_SECURITY_GUIDE_EN.md)
    *   🇪🇸 [MULTI_SECURITY_GUIDE_ES.md](docs/MULTI_SECURITY_GUIDE_ES.md)
*   **Cómo se genera el Informe PDF (presentación para tu gestor fiscal):**
    *   🇺🇸 [PDF_REPORT_PRESENTATION_EN.md](docs/PDF_REPORT_PRESENTATION_EN.md) · [PDF](docs/PDF_REPORT_PRESENTATION_EN.pdf)
    *   🇪🇸 [PDF_REPORT_PRESENTATION_ES.md](docs/PDF_REPORT_PRESENTATION_ES.md) · [PDF](docs/PDF_REPORT_PRESENTATION_ES.pdf)

---

## 🚀 Inicio Rápido (Usuarios de Mac)

Si no eres programador, puedes utilizar directamente el script preparado:

1.  **Descarga el proyecto completo** en un archivo ZIP desde GitHub (botón "Code" → "Download ZIP") y descomprímelo.
2.  Haz doble clic en el archivo `run_tax_engine.command` dentro de la carpeta descomprimida.
3.  El script configurará automáticamente Python, instalará las dependencias y abrirá un menú interactivo.
4.  Sigue las opciones del menú para iniciar sesión en E-Trade, descargar tus datos y calcular los impuestos.

*Nota: La primera vez que lo ejecutes, es posible que debas hacer clic derecho y seleccionar "Abrir" si macOS muestra un aviso de desarrollador no identificado, o autorizarlo en Ajustes del Sistema.*

## 🚀 Inicio Rápido (Usuarios de Windows)

Si no eres programador, puedes utilizar directamente el archivo ejecutable por lotes:

1.  **Descarga el proyecto completo** en un archivo ZIP desde GitHub (botón "Code" → "Download ZIP") y descomprímelo.
2.  Haz doble clic en el archivo `run_tax_engine.bat` dentro de la carpeta descomprimida.
3.  Configurará de forma automática Python, instalará las dependencias y abrirá un menú interactivo.
4.  Sigue las opciones del menú para conectar con E-Trade, descargar los datos y calcular los impuestos.

## 🚀 Inicio Rápido (Usuarios de Linux)

Si no eres programador, puedes usar el mismo script que en Mac:

1.  **Descarga el proyecto completo** en un archivo ZIP y descomprímelo.
2.  Abre una terminal en la carpeta descomprimida y ejecuta:
    ```bash
    chmod +x run_tax_engine.command
    ./run_tax_engine.command
    ```
3.  El programa configurará Python, instalará las dependencias y mostrará un menú.
4.  Sigue las opciones para descargar datos y procesar tus impuestos.

## 🐳 Inicio Rápido (Devcontainer)

En Visual Studio Code, abre esta carpeta utilizando la extensión Dev Containers para ejecutar todo dentro de un contenedor de desarrollo preconfigurado.

El contenedor incluye un escritorio ligero (fluxbox) y el navegador Chromium necesario para Playwright.

Acceso al escritorio virtual:
- Abre en el navegador la dirección http://localhost:6080
- O conéctate con un cliente VNC en el puerto 5901 (el puerto puede variar, compruébalo en la pestaña "Ports" de VS Code)

Inicia el asistente de descarga (abrirá el navegador de E-Trade dentro del contenedor):
```bash
uv run tax-download
```

Utiliza el asistente para iniciar sesión en E-Trade y descargar los archivos obligatorios (histórico de ESPP, órdenes de venta e informes de confirmación de RSU).

---

## 🛠️ Inicio Rápido para Desarrolladores

### 1. Preparar el entorno de desarrollo
```bash
brew install uv
uv sync --all-extras
uv run pre-commit install
```

### 2. Ejecutar la Demo
Para comprobar el funcionamiento del motor fiscal con datos de prueba (E*TRADE + Revolut):
```bash
uv run demo.py
```

### 3. Descargar tus Datos reales
Para automatizar la descarga de tu historial de transacciones desde E-Trade:

1.  **Instala los navegadores de Playwright** (solo la primera vez):
    ```bash
    uv run playwright install chromium
    ```

2.  **Inicia el asistente de descarga**:
    ```bash
    uv run tax-download
    ```
    Te guiará para hacer login y descargará automáticamente tu historial de ESPP, órdenes de venta y confirmaciones de RSU.

### 4. Ejecutar el Análisis Fiscal
Una vez que los archivos estén colocados en la carpeta `input/`:
```bash
uv run main.py
```
El motor generará el informe detallado en formato PDF (`tax_report_*.pdf`).

**Cripto (opcional).** Si además operas con cripto, coloca los exports de tu exchange bajo `input/crypto/` (ver *Archivos de Entrada* abajo) y ejecuta `uv run tax-crypto` para un informe FIFO por moneda, o `uv run tax-combined` para fusionar acciones + cripto en una única base del ahorro española. Para probar cualquiera de los dos flujos con datos de ejemplo sin conexión: `uv run tax-demo --crypto` o `uv run tax-demo --combined`.

---

## 📝 Declarar en Renta (Modelo 100 España)

El informe PDF contiene una sección **Resumen Fiscal Anual (Modelo 100 - Base Imponible del Ahorro)**.

Para cada ejercicio fiscal calcula:
- **Ganancias / Pérdidas Totales:** Sumas de las plusvalías y minusvalías realizadas.
- **Pérdidas Bloqueadas:** Pérdidas diferidas por la regla de los 2 meses.
- **Pérdidas Deducibles:** Pérdidas utilizables en el ejercicio tras descontar las bloqueadas.
- **Base Imponible del Ahorro:** Importe neto a declarar tras compensar las pérdidas correspondientes.
- **Impuesto Estimado (Aislado):** Impuesto sobre *estas ganancias bursátiles de forma aislada* según los tramos del ahorro (19%–28%). ⚠️ **No** es tu cuota definitiva: ignora el resto de tu base del ahorro (dividendos, intereses) y la compensación de pérdidas de años anteriores. Úsalo como orientación.

El informe incluye además:
- **Libro de Compensación de Pérdidas (Art. 49 LIRPF):** simula la compensación a 4 años de pérdidas netas con ganancias posteriores y avisa de las que caducan. Inicializa pérdidas previas con `input/prior_losses.json` (p. ej. `{"2019": 1500}`) o `--prior-losses <archivo>`.
- **Base del Ahorro con dividendos/intereses (opcional):** aporta `input/savings_income.json` (o `--savings-income <archivo>`) para incluir tus dividendos e intereses de cuenta de E\*TRADE (*rendimientos del capital mobiliario*). El informe calcula la **compensación cruzada del 25%** (una pérdida bursátil compensando dividendos/intereses) y muestra la base del ahorro combinada. La retención en origen se muestra a título informativo; la *deducción por doble imposición* la aplica tu asesor.
  - **Formato recomendado — pagos en USD (exacto):** una lista de pagos, cada uno con su fecha; el motor convierte cada uno a EUR al **tipo del BCE de esa fecha**, igual que las operaciones de acciones:
    ```json
    [ { "date": "2024-03-15", "type": "dividend", "amount_usd": 80, "foreign_tax_usd": 12 } ]
    ```
    **Importación automática desde E\*TRADE (recomendado):** la **opción 3 → "Auto-download"** del menú (o `tax-download-dividends` y luego `tax-import-dividends`) descarga tus transacciones de efectivo (solo dividendos) a `input/dividends/Cash_Transactions.xlsx` y las fusiona en `savings_income.json`. Las líneas de intereses se clasifican como `interest`, las retenciones en origen se capturan como `foreign_tax_usd` y reejecutar es idempotente (se eliminan duplicados por fecha + descripción + importe). La **Descarga de datos de E\*TRADE** completa (opción 2) también ejecuta este paso automáticamente.
    O introdúcelos a mano con el asistente interactivo (sin editar JSON): `.venv/bin/python -m tax_engine.cli_savings_income` — o directo: `... cli_savings_income --date 2024-03-15 --type dividend --amount-usd 80 --foreign-tax-usd 12`. Plantilla inicial: `cp docs/savings_income.example.json input/savings_income.json`.
  - **Alternativa — EUR por año (conversión manual):** si solo tienes un total anual (p. ej. de un **Formulario 1042-S** / 1099 en *Accounts → Documents → Tax Center*), puedes aportar importes ya convertidos a EUR: `{"2024": {"dividends_eur": 320, "interest_eur": 15, "foreign_tax_eur": 48}}`. Ten en cuenta que así no se aplican los tipos de cambio por pago.
- **Resumen *¿Qué declarar en Hacienda?*:** una sección destacada, por ejercicio, con las cifras exactas a introducir agrupadas por las tres categorías del IRPF — rendimientos del trabajo (solo ESPP vendido antes de 3 años), ganancias patrimoniales (*valor de transmisión* / *adquisición* → saldo deducible) y base del ahorro (+ dividendos/intereses), cada una con su casilla orientativa.

> **Solo ejercicios completos:** el informe cubre los ejercicios ya finalizados; el año en curso se excluye de todas las tablas (aún no es declarable), aunque sus operaciones sí cuentan en el cálculo FIFO.

> **Opciones de línea de comandos:** `uv run main.py` admite `--input-dir`, `--output-dir`, `--prior-losses` y `--savings-income` (todas opcionales).

Introduce el valor de la "base imponible del ahorro" en el apartado de ganancias y pérdidas derivadas de la transmisión de valores en el borrador de tu declaración de la renta (IRPF).

---

## 📊 Panel Interactivo de Gráficos e Impuestos

Además del informe PDF, puedes generar un **panel HTML interactivo** que convierte tus datos en una imagen clara y sin tecnicismos de tus acciones: qué posees, cuánto valen hoy, cuánto te quedaría neto tras impuestos si vendieras y cuándo queda exento cada lote de ESPP. Está pensado para un público no financiero e incluye un modo de privacidad (difumina importes) y un selector EN/ES.

Genéralo desde la **opción 6 del menú** ("Generate Charts & Tax Dashboard"), o directamente:

```bash
uv run generate_charts.py            # detecta el ticker y obtiene el precio en vivo
uv run generate_charts.py --ticker DDOG            # o especifica manualmente un ticker de acción
uv run generate_charts.py --current-price 45.50   # o fija un precio en USD
```

Crea `charts_dashboard.html` (en `.gitignore`) — ábrelo en cualquier navegador. Guía completa: 🇺🇸 [DASHBOARD_GUIDE_EN.md](docs/DASHBOARD_GUIDE_EN.md) · 🇪🇸 [DASHBOARD_GUIDE_ES.md](docs/DASHBOARD_GUIDE_ES.md).

> ⚠️ El panel es solo informativo y obtiene un **precio de mercado en vivo de Yahoo Finance**. Ningún dato personal sale de tu equipo en el propio archivo.

---

## 🌐 Interfaz Web

Para una vista interactiva en el navegador de todos tus datos fiscales, lanza el servidor web local. Ejecuta los motores de cálculo a demanda y muestra un panel con cinco pestañas donde también puedes introducir rendimientos del trabajo e ingresos inmobiliarios manualmente y compararlo todo con tu declaración en la AEAT.

**Solo la primera vez** — compila el frontend (requiere Node.js):
```bash
cd src/tax_engine/web_app/frontend
npm install
npm run build
cd -
```

Luego arranca el servidor:
```bash
uv run tax-web                             # usa la carpeta input/ por defecto, puerto 8080
uv run tax-web --input-dir input --port 9090
```

Abre `http://localhost:8080` en tu navegador y haz clic en **Run Engine** para cargar los datos.

> **Desarrolladores:** para iterar sobre el frontend, usa dos terminales — `uv run tax-web --port 8080` (backend) y `npm run dev` dentro de `frontend/` (servidor Vite en `http://localhost:5173`, redirige `/api` al puerto `:8080`).

| Pestaña | Qué muestra |
|---------|-------------|
| **Stocks** | Ganancias/pérdidas/impuesto anual, posiciones abiertas, enlaces a informes HTML completos (EN/ES) |
| **Crypto** | Lo mismo para criptomonedas (lee de `input/crypto/`) |
| **Work Income** | Descuentos ESPP por venta anticipada (calculados) + campos para introducir el salario anual |
| **Real Estate** | Rendimientos de alquiler y ganancias por venta de inmuebles por año |
| **Hacienda** | Sube tu XML de Renta Web → previsualiza las casillas extraídas → importa → tabla comparativa (Calculado vs Declarado vs Diferencia) |

Las entradas manuales (salario, inmuebles, casillas AEAT importadas) se guardan en `input/manual_data.json` y persisten entre ejecuciones.

---

## 📋 Opciones del Menú

Al ejecutar `run_tax_engine.command` (macOS/Linux) o `run_tax_engine.bat` (Windows), aparece este menú:

| # | Opción | Qué hace |
|---|--------|----------|
| 1 | Login to E-Trade Plan | Abre el navegador para iniciar sesión (y el MFA). **Ejecútalo primero** — guarda la sesión. |
| 2 | Download E-Trade Data | Descarga el histórico ESPP, las órdenes, las confirmaciones RSU, los ejercicios de opciones y los dividendos en `input/` (los dividendos se importan automáticamente a `savings_income.json`). |
| 3 | Add Dividend/Interest Income | *Opcional.* Descarga los dividendos de E\*TRADE automáticamente, o registra los pagos de dividendos/intereses a mano (USD + fecha) para la base del ahorro. |
| 4 | Calculate Tax & PDF Reports (optional: incl. Revolut) | Ejecuta el motor y genera los informes PDF en inglés y español (integra el CSV de Revolut si está presente). |
| 5 | Generate Charts & Tax Dashboard (optional: incl. Revolut) | Crea el `charts_dashboard.html` interactivo (detecta el ticker; obtiene el precio en vivo; incluye Revolut si está presente). |
| 6 | Run Demo: Calculate Tax & PDF Reports | Ejecuta con datos de ejemplo para generar los informes PDF de prueba. |
| 7 | Run Demo: Generate Charts & Tax Dashboard | Crea el `charts_dashboard_demo.html` interactivo utilizando datos simulados sin conexión. |
| 8 | Exit | Salir. |

## 📂 Archivos de Entrada

Todo va dentro de `input/`. Las opciones 1–2 del menú crean la mayoría automáticamente; las dos últimas son opcionales y las introduces tú.

| Archivo | ¿Obligatorio? | De dónde sale |
|---------|---------------|---------------|
| `input/espp/BenefitHistory.xlsx` | **Sí** | E-Trade → Stock Plan → Benefit History → *Download Expanded* |
| `input/orders/orders.xlsx` | Si hay ventas | E-Trade → Stock Plan → Orders |
| `input/rsu/*.pdf` | Si tienes RSU | E-Trade → Documents → confirmaciones de liberación RSU |
| `input/options/*.pdf` | Si ejerciste opciones | E-Trade → Documents → confirmaciones de ejercicio de opciones |
| `input/prior_losses.json` | Opcional | Pérdidas pendientes de antes de tu ventana de datos, p. ej. `{"2019": 1500}` |
| `input/savings_income.json` | Opcional | Dividendos/intereses (ver *Declarar en Renta* arriba) |
| `input/ticker.json` | Para Revolut | Valor principal/de empresa, p. ej. `{"ticker": "DT", "isin": "US..."}` — el ISIN filtra el CSV de Revolut en modo de un solo valor |
| `input/securities.json` | Opcional | Configuración multivalor: activa el modo cartera y asigna tickers→ISINs (ver *Varios valores y brókers* abajo) |
| `input/revolut/*.csv` | Opcional | CSV de inversión de Revolut — *Extracto de cuenta* (preferido) o *Profit & Loss* (ver abajo) |
| `input/crypto/pionex/trading.csv` | Opcional (cripto) | Pionex → export del historial de operaciones (ver *Cripto* abajo) |
| `input/crypto/binance/*Spot-Trade-History*.csv` | Opcional (cripto) | Binance → export del historial de operaciones spot (ver *Cripto* abajo) |

### Revolut (opcional)

Si además tienes **las mismas acciones de la empresa** en Revolut, exporta su **extracto de cuenta** — el registro completo de operaciones (`Date, Ticker, Type, Quantity, Price per share, …`) — y coloca el CSV (cualquier nombre) en `input/revolut/`. Incluye tus **compras** aunque nunca las hayas vendido, de modo que entran en el conjunto de coste de adquisición. Las filas se filtran por **ticker** (este export no tiene ISIN) — define `"ticker"` en `input/ticker.json` (o pasa `--revolut-symbol`). Las filas que no son operaciones (ingresos/retiradas de efectivo, transferencias internas) se ignoran; los *splits* se normalizan a términos post-split para mantener la coherencia del FIFO.

> También se acepta un export más antiguo de **ganancias/pérdidas realizadas** (`Date acquired, Date sold, … ISIN, Gross proceeds`) (filtrado por ISIN; su sección "Other income & fees" se suma a dividendos/intereses), pero solo lista *ventas* — usa preferentemente el extracto de cuenta para que se incluyan tus compras.

Las filas coincidentes se integran en el **mismo conjunto FIFO** que tus acciones de E\*TRADE, de modo que el orden FIFO y la regla de los 2 meses (*wash sale*) se aplican entre ambos brókers, como exige España para los valores homogéneos. Las ganancias se calculan en EUR al **tipo oficial del BCE de cada fecha** (se ignora la columna FX de Revolut). Se convierten las filas en **EUR (1:1) o en cualquier divisa de referencia del BCE** (USD, GBP, CHF, …); las divisas que el BCE no publica se omiten con un aviso. **Requiere el historial completo de adquisiciones** de ese valor para que la cola FIFO global nunca quede en negativo. Cuando hay más de un bróker, el PDF marca cada fila del libro con una columna **Bróker** y añade un subtotal *Ganancias/Pérdidas Realizadas por Bróker*; los gráficos usan la misma posición combinada. Formato de referencia: [`docs/revolut-movements.example.csv`](docs/revolut-movements.example.csv).

### Varios valores y brókers (modo cartera)

Por defecto la herramienta analiza **un** valor (las acciones de tu empresa). Si además operaste con **otros** valores (p. ej. en Revolut), activa el **modo cartera**: cada valor tiene su propia cola FIFO (agrupada por **ISIN**) y los resultados se consolidan en una única base del ahorro española.

- **Cómo activarlo:** la opción *Calcular Impuestos* del menú ahora pregunta *«¿Procesar TODOS los valores entre brókers?»*; o ejecuta `tax-engine --all-securities`; o simplemente crea `input/securities.json` (su presencia lo activa automáticamente).
- **`input/securities.json`** (todos los campos opcionales): `{ "include": ["DT","TSLA"], "isin_map": {"TSLA":"US88160R1014"}, "primary": "DT" }` — `include` limita qué valores se procesan (vacío = todos) e `isin_map` aporta los ISINs del *extracto de cuenta* de Revolut (que solo tiene ticker) para que el mismo valor se fusione entre brókers de forma fiable.
- **Resultado:** el PDF añade una tabla **Resumen de Cartera por Valor** y una sección de libro + FIFO por valor; la base del ahorro, la compensación a 4 años y el límite del 25% operan sobre el total de la cartera, mientras que la regla de los 2 meses se mantiene por valor. El panel añade un gráfico de desglose por valor, un selector de valores y **etiquetas de alcance** en cada tarjeta (🌐 toda la cartera vs 🏷️ valor seleccionado) para que un lector no financiero siempre sepa qué cifras está viendo.

Guía completa: [MULTI_SECURITY_GUIDE_ES.md](docs/MULTI_SECURITY_GUIDE_ES.md) · [🇺🇸 EN](docs/MULTI_SECURITY_GUIDE_EN.md).

### Cripto (opcional)

Si operas con cripto en **Pionex** o **Binance**, coloca sus exports de operaciones spot en `input/crypto/pionex/` e `input/crypto/binance/` (los archivos de Binance coinciden con `*Spot-Trade-History*.csv`; cualquier nombre vale). Cada moneda tiene su **propia cola FIFO**, y cada transmisión se valora en EUR al **tipo oficial del BCE de la fecha de la operación**, igual que las acciones. Las operaciones cuya base es una stablecoin y los intercambios cripto-a-cripto se omiten (fuera de alcance), y las comisiones se valoran en el activo de cotización.

- **Informe por moneda:** `uv run tax-crypto --input-dir input/crypto` imprime un resumen por moneda en consola y genera un CSV por transmisión más informes HTML bilingües.
- **Combinado con acciones:** `uv run tax-combined` fusiona las ganancias/pérdidas anuales de acciones y cripto en una única base del ahorro (Art. 49 LIRPF) en HTML bilingüe.
- **Pruébalo sin conexión:** `uv run tax-demo --crypto` (o `--combined`) usa datos de ejemplo BTC/ETH/SOL sin ninguna llamada de red.

> La opción `--wash-sale` aplica la regla de los 2 meses para activos homogéneos por moneda; está **desactivada por defecto** porque su aplicabilidad a la cripto no está resuelta en la AEAT.

## ❓ Solución de Problemas

- **"Sesión caducada" / redirige al login:** vuelve a ejecutar **Login** (opción 1) — las sesiones de E-Trade caducan al cabo de un rato.
- **Las descargas fallan o una página no carga:** desactiva los bloqueadores de anuncios/privacidad para `us.etrade.com`; pueden romper los propios scripts de E-Trade.
- **`BenefitHistory.xlsx not found`:** asegúrate de que el archivo ESPP está en `input/espp/BenefitHistory.xlsx` (ejecuta **Download All Data** o colócalo manualmente).
- **Error de tipo de cambio / red:** la caché del BCE en disco (`.ecb_rate_cache.json`) permite repetir sin conexión, pero la primera ejecución necesita internet para obtener los tipos.
- **El navegador no se abre:** reinstala el navegador de Playwright: `.venv/bin/playwright install chromium`.
