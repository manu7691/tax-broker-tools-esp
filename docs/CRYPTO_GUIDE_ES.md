# Ganancias Patrimoniales de Cripto — Pionex y Binance

🇪🇸 Español | 🇺🇸 [English](CRYPTO_GUIDE_EN.md)

> ⚠️ Mismo aviso que el README principal: esta herramienta es una ayuda, no asesoramiento fiscal. Verifica el resultado con un *Asesor Fiscal* cualificado antes de presentar.

## Por qué existe

En España, **toda transmisión de un criptoactivo** es una ganancia/pérdida patrimonial en la base del ahorro — ya sea vendiéndolo por una stablecoin, por euros o permutándolo por otra moneda (*permuta*). La ganancia es la diferencia entre el **valor de transmisión** y el **valor de adquisición**, ambos en EUR al **tipo oficial del BCE de la fecha de cada operación**, y los lotes se casan **FIFO por activo homogéneo** (es decir, por moneda), igual que las acciones.

Esta herramienta lee los exports de tu exchange, reconstruye una cola FIFO por moneda y genera un informe de ganancias por moneda más un informe combinado opcional que fusiona la cripto con tus ganancias bursátiles en una única base del ahorro lista para el Modelo 100.

## La única regla que debes entender

> **Clave de agrupación FIFO = la moneda. Cada moneda es su propia cola; el exchange es solo una etiqueta.**

- Cada moneda (BTC, ETH, SOL, …) tiene una cola FIFO independiente.
- La **misma moneda entre exchanges se fusiona** en una sola cola, en orden cronológico real (UTC) — de modo que un lote de BTC barato comprado en Binance se consume antes que otro más caro comprado después en Pionex.
- Las stablecoins (USDT, USDC, USD, DAI, BUSD, FDUSD, TUSD, USDP, USDD) se tratan como **efectivo en USD**: su valor en EUR es el importe de cotización al tipo BCE USD/EUR de la fecha. Nunca tienen su propia cola.

## Obtener tus exports

Coloca el export de cada exchange bajo `input/crypto/`:

```
input/crypto/
├── pionex/trading.csv
└── binance/<loquesea>Spot-Trade-History<loquesea>.csv
```

- **Pionex** → exporta tu historial de operaciones como `trading.csv`. Columnas usadas: `date(UTC+0)`, `symbol` (p. ej. `BTC_USDT`), `side`, `executed_qty`, `amount` (total en el activo de cotización), `fee`, `fee_coin`.
- **Binance** → exporta tu **Spot Trade History**; se detecta cualquier archivo cuyo nombre contenga `Spot-Trade-History` y termine en `.csv`. Columnas usadas: `Time` (hora local, por defecto **UTC+2**, se convierte a UTC), `Pair` (p. ej. `SOLUSDC`), `Side`, `Executed` (p. ej. `50SOL`), `Amount` (p. ej. `4750USDC`), `Fee`.

Cualquiera de las dos fuentes es opcional — aporta la que tengas. Plantillas listas para copiar: [`crypto-pionex.example.csv`](crypto-pionex.example.csv) · [`crypto-binance.example.csv`](crypto-binance.example.csv).

## Cómo ejecutarlo

```bash
# Informe de cripto por moneda (consola + CSV + HTML bilingüe)
uv run tax-crypto --input-dir input/crypto

# Cripto fusionada con tus acciones en una única base del ahorro (HTML bilingüe)
uv run tax-combined
```

`tax-crypto` admite `--input-dir`, `--output-dir` y `--wash-sale`. `tax-combined` admite `--input-dir` (acciones), `--crypto-dir` (por defecto `<input-dir>/crypto`), `--output-dir` y `--lang` (`es` / `en` / `both`).

## Qué obtienes

- **Resumen en consola:** ganancias/pérdidas/comisiones realizadas por moneda y por año, una tabla anual combinada de la base del ahorro (con un impuesto estimado *aislado*) y tus posiciones abiertas (no realizadas, informativas).
- **`crypto_disposals_<timestamp>.csv`:** una fila por transmisión (fecha, moneda, cantidad, valor de transmisión, valor de adquisición, comisión, ganancia/pérdida en EUR) para tu archivo.
- **Informes HTML bilingües** (`crypto_tax_report_EN/ES_<timestamp>.html`) con resumen, gráficos, posiciones, una sección del Modelo 100 y la lista completa de transmisiones.

## Qué se omite, y otras advertencias

- **Operaciones con base stablecoin** (p. ej. un convert USDC→USDT) se tratan como efectivo y no generan posición tributable.
- **Permutas cripto-a-cripto** cotizadas en una no-stablecoin (p. ej. ETH/BTC) están **fuera del alcance de este MVP** y se omiten con un aviso — aunque España *sí* trata la permuta como tributable. Por ahora gestiónalas a mano.
- **Las comisiones** se descuentan de la ganancia en las ventas, valoradas en el activo de cotización (una comisión pagada en la moneda base se valora al precio unitario de la operación; una moneda de comisión no soportada como BNB se ignora con un aviso).
- **Historial de adquisición incompleto:** si tus datos venden más de una moneda de la que muestran haber comprado, se inserta un **lote de apertura sintético** (al precio de la primera venta) para que la cola nunca quede en negativo, y se imprime un aviso. Aporta el historial completo para evitarlo.
- **Wash-sale (regla de los 2 meses):** desactivada por defecto. Según el criterio de la DGT, los criptoactivos **no son *valores homogéneos***, por lo que la regla anti-lavado de pérdidas (Art. 33.5 LIRPF) **no aplica**. `--wash-sale` existe solo como anulación expresa indicada por tu asesor — déjala desactivada salvo que tu asesor te indique expresamente lo contrario.
- **El impuesto estimado es aislado** — ignora tus ganancias bursátiles, dividendos/intereses y la compensación de pérdidas de años anteriores. Usa `tax-combined` para la base del ahorro real.

## Pruébalo con datos de ejemplo (sin datos reales)

```bash
uv run tax-demo --crypto      # demo de cripto por moneda (BTC + ETH + SOL)
uv run tax-demo --combined    # cartera de acciones + cripto fusionadas
```

Ambos usan datos de ejemplo sin conexión con tipos de cambio manuales, así que no se realiza ninguna llamada de red.

## Inicio rápido

```bash
# 1. coloca tus exports en su sitio
mkdir -p input/crypto/pionex input/crypto/binance
cp ~/Downloads/trading.csv                 input/crypto/pionex/
cp ~/Downloads/*Spot-Trade-History*.csv    input/crypto/binance/

# 2. ejecuta el informe por moneda …
uv run tax-crypto --input-dir input/crypto

# 3. … o fusiónalo con tus acciones
uv run tax-combined
```
