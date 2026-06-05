# E-Trade Spanish Tax Engine / Motor Fiscal de España para E-Trade

🇺🇸 [English README](#english-version) | 🇪🇸 [README en Español](#version-en-espanol)

---

<a name="english-version"></a>
# 🇺🇸 E-Trade Spanish Tax Engine

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
To see the tax engine in action with sample data:
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
- **Savings Base with dividends/interest (optional):** provide `input/savings_income.json` (e.g. `{"2024": {"dividends_eur": 320, "interest_eur": 15, "foreign_tax_eur": 48}}`) or `--savings-income <file>` to add your E\*TRADE dividends and cash interest (*rendimientos del capital mobiliario*). The report then computes the **25% cross-category offset** (a stock loss offsetting dividend/interest income) and shows the combined savings base. Foreign tax withheld is shown for reference; the *deducción por doble imposición* is left to your advisor.
  - These figures are entered **manually** — read the year-end totals off your E\*TRADE tax document (a **Form 1042-S** for non-US residents, or 1099) under *Accounts → Documents → Tax Center*. Either copy the template (`cp docs/savings_income.example.json input/savings_income.json`) and edit it, or use the interactive helper which prompts you and merges into the file: `.venv/bin/python -m tax_engine.cli_savings_income` (or one-shot: `... cli_savings_income --year 2024 --dividends 320 --interest 15 --foreign-tax 48`).
- **Modelo 100 Filing Guide:** a crosswalk mapping each figure to its Modelo 100 *apartado* (casilla numbers are indicative — verify for your year).

> **CLI options:** `uv run main.py` accepts `--input-dir`, `--output-dir`, `--prior-losses`, and `--savings-income` (all optional).

Copy the net taxable savings base into the capital gains from stock transfers section of your annual IRPF tax return.

---

<br>
<br>

<a name="version-en-espanol"></a>
# 🇪🇸 Motor Fiscal de España para E-Trade

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
Para comprobar el funcionamiento del motor fiscal con datos de prueba:
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
- **Base del Ahorro con dividendos/intereses (opcional):** aporta `input/savings_income.json` (p. ej. `{"2024": {"dividends_eur": 320, "interest_eur": 15, "foreign_tax_eur": 48}}`) o `--savings-income <archivo>` para incluir tus dividendos e intereses de cuenta de E\*TRADE (*rendimientos del capital mobiliario*). El informe calcula entonces la **compensación cruzada del 25%** (una pérdida bursátil compensando dividendos/intereses) y muestra la base del ahorro combinada. La retención en origen se muestra a título informativo; la *deducción por doble imposición* la aplica tu asesor.
  - Estos importes se introducen **manualmente** — toma los totales anuales de tu documento fiscal de E\*TRADE (un **Formulario 1042-S** para no residentes en EE. UU., o 1099) en *Accounts → Documents → Tax Center*. Copia la plantilla (`cp docs/savings_income.example.json input/savings_income.json`) y edítala, o usa el asistente interactivo que te pregunta y fusiona en el archivo: `.venv/bin/python -m tax_engine.cli_savings_income` (o directo: `... cli_savings_income --year 2024 --dividends 320 --interest 15 --foreign-tax 48`).
- **Guía de Cumplimentación del Modelo 100:** asigna cada dato a su *apartado* (las casillas son orientativas — verifícalas para tu ejercicio).

> **Opciones de línea de comandos:** `uv run main.py` admite `--input-dir`, `--output-dir`, `--prior-losses` y `--savings-income` (todas opcionales).

Introduce el valor de la "base imponible del ahorro" en el apartado de ganancias y pérdidas derivadas de la transmisión de valores en el borrador de tu declaración de la renta (IRPF).
