# Varios Valores y Brókers — Modo Cartera

🇪🇸 Español | 🇺🇸 [English](MULTI_SECURITY_GUIDE_EN.md)

> ⚠️ Mismo descargo que el README principal: esta herramienta es una ayuda, no asesoramiento fiscal. Verifica el resultado con un *Asesor Fiscal* cualificado antes de presentar la declaración.

## Por qué existe

Por defecto el motor es de **un solo valor**: asume que toda transacción es la misma acción de empresa (p. ej. DT). Pero el FIFO español es **por valor (por ISIN)**, y el resultado imponible es el **agregado** de la ganancia/pérdida neta de cada valor en la *base del ahorro*. Así que si además operaste con TSLA, NVDA, … (en Revolut, o en otra cuenta), esas ganancias/pérdidas van en la misma declaración.

El **modo cartera** procesa *todos* tus valores —cada uno con su propia cola FIFO correcta— y los consolida en una única base del ahorro lista para el Modelo 100.

## La única regla que hay que entender

> **Clave de agrupación FIFO = ISIN. El bróker es solo una etiqueta.**

- Cada **ISIN** tiene una cola FIFO independiente. Los tickers nunca se mezclan.
- El **mismo ISIN en distintos brókers se fusiona** en una sola cola (p. ej. DT de empresa en E\*TRADE + DT comprado en Revolut → una sola cola DT). Es lo que exige la ley española para los valores *homogéneos*.
- El bróker solo afecta a los **informes** (el subtotal por bróker), nunca a la agrupación FIFO.

## Cómo activarlo

Cualquiera de estas opciones activa el modo cartera:

1. **Menú (launcher):** ejecuta *Calcular Impuestos* — ahora pregunta *«¿Procesar TODOS los valores entre brókers?»*. Responde **y**.
2. **Línea de comandos:** `tax-engine --all-securities`
3. **Solo crea `input/securities.json`** — su mera presencia activa el modo cartera.

Sin ninguna de ellas, el comportamiento es exactamente el de antes (solo el valor principal). El modo de un solo valor no cambia.

## `input/securities.json`

Todos los campos son opcionales:

```json
{
  "include": ["DT", "TSLA", "NVDA"],
  "isin_map": { "TSLA": "US88160R1014" },
  "primary": "DT"
}
```

| Campo | Significado |
|-------|-------------|
| `include` | Lista blanca de tickers/ISINs a procesar. **Vacío o ausente = procesar todo lo detectado.** |
| `isin_map` | `ticker → ISIN`. Solo necesario para el **extracto de cuenta** de Revolut, que no tiene columna ISIN. Permite que ese valor se fusione entre brókers de forma fiable. |
| `primary` | Tu valor principal/de empresa (ticker o ISIN). Informativo. |

### Por qué importa `isin_map`

El **extracto de cuenta** de Revolut (el export preferido) es **solo de ticker** — no lleva ISIN. El motor resuelve cada ticker a un ISIN en este orden:

1. tu `isin_map`,
2. ISINs **aprendidos automáticamente** de un export de *ganancias realizadas* de Revolut en la misma carpeta (ese export *sí* lleva ISINs),
3. una caché local (`.isin_cache.json`) recordada de ejecuciones anteriores.

Si un ticker no se puede resolver, sigue funcionando — simplemente se agrupa **por ticker** en vez de por ISIN, y verás una nota de una línea. Es seguro siempre que el ticker nunca haya cambiado de ISIN; lo único que pierdes es la fusión fiable entre brókers para ese valor. Añádelo a `isin_map` para asegurarlo.

> **Consejo:** pon también el **ISIN de la acción de empresa en `input/ticker.json`** (`{"ticker":"DT","isin":"US..."}`). De lo contrario las acciones de E\*TRADE se agrupan por ticker y no pueden fusionarse con un feed del mismo ISIN bajo otro ticker.

## Qué obtienes

**Informe PDF** (`--all-securities`):

- Una tabla **Resumen de Cartera por Valor** — coste de adquisición invertido, ganancias/pérdidas realizadas, neto y posición abierta por valor, con una fila Total de la cartera.
- Una sección separada de **libro de transacciones + detalle FIFO** por valor (cada una en su propia página).
- El subtotal de G/P realizada **por bróker** (entre valores).
- La **base del ahorro combinada**: la compensación de pérdidas a 4 años y el límite del 25% entre categorías (contra dividendos/intereses) operan sobre el **total de la cartera** —porque en la ley española no son por valor—. La **regla de los 2 meses (*wash sale*) se mantiene por valor**.

**Consola:** un desglose de *Posiciones Actuales* por valor (en lugar de una media mezclada sin sentido).

**Panel** (`generate_charts.py`): en modo cartera el panel pasa a ser **por valor**. Un **selector de valor** (desplegable en la cabecera fija) cambia *todo* el panel —precio/tendencia, simulador de venta, descomposición de ganancias, historial de FX, precio en vivo y competidores— al valor elegido. Los paneles propios de la acción de empresa (**ESPP** y **RSU**) solo aparecen para los valores que tienen esos datos y se ocultan para posiciones simples de Revolut. También hay un gráfico de **desglose de cartera** (invertido + realizado por valor) y el gráfico por bróker en la pestaña **Avanzado**.

- **Competidores por valor:** los competidores de comparación se pueden definir por ticker en `input/peers.json` — una lista plana se aplica a todos, o un mapa `{ "DT": ["DDOG","ESTC"], "TSLA": ["RIVN","LCID","NIO"] }` da a cada valor su propio grupo de competidores (ver [`docs/peers.example.json`](peers.example.json)).
- **Dividendos por valor:** la tabla de cartera muestra una columna **Dividendos (EUR)** por valor (de las filas "Other income" de Revolut). Es informativa — la base imponible de RCM (dividendos) se mantiene a nivel de cartera, como exige la ley española.
- **Tarjetas a nivel de cartera:** *Ganancias Realizadas e Impuesto Estimado por Año* (barras por año), *Exposición por Divisa* (valor actual en EUR por divisa de cotización, visible si tienes más de una divisa) y *Cosecha de Pérdidas Fiscales* (posiciones abiertas con pérdida latente que podrías vender para compensar ganancias del año — con el aviso de la regla de los 2 meses).
- **El break-even se basa en el FIFO español.** La calculadora valora tus lotes *restantes* tal como los deja el FIFO (se vende primero el más antiguo → quedan los más nuevos, a menudo más caros), en **euros** mostrados como precio en USD al cambio de hoy — por eso puede diferir del break-even de E\*TRADE (identificación específica / coste medio de EE. UU.). Cuando un valor abarca varios brókers ofrece un **filtro por Bróker** (Todos / E\*TRADE / Revolut), pero es *solo informativo*: el FIFO español es un único conjunto por ISIN, así que un break-even por bróker no es una cifra fiscal.

## Divisas

La conversión a EUR usa el **tipo de referencia oficial del BCE por fecha**. EUR es 1:1; **cualquier divisa del BCE** (USD, GBP, CHF, JPY, …) se convierte; las divisas que el BCE no publica se omiten con un aviso.

## Advertencias

- **Historial completo por valor.** Cada cola necesita el historial de adquisiciones completo, o el FIFO podría intentar vender más de lo que tiene (verás un error claro por valor).
- **La precisión entre brókers depende de los ISINs.** Sin ISIN, un valor solo se fusiona con feeds del *mismo ticker*.
- **Operaciones corporativas** más allá de los *splits* directos (fusiones, *spin-offs*, cambios de ISIN) no se modelan.
- Recordatorio del **Modelo 720**: las posiciones en el extranjero por encima de 50.000 € entre todas las plataformas pueden ser declarables — fuera del alcance aquí.

## Pruébalo con datos de demo (sin datos reales)

¿Quieres ver el modo cartera antes de preparar tus propios archivos? Ambas demos
aceptan `--all-securities`, que usa un conjunto de muestra multivalor (DT +
TSLA/NVDA/ADBE en Revolut + una posición de Shell **en GBP**). Ejercita todo el
informe: FIFO por valor, multidivisa, bloqueo por *wash sale*, aplicación de la
compensación de pérdidas, la base del ahorro con dividendos/intereses y el límite
del 25%, y el análisis ESPP de venta anticipada (3 años):

```bash
tax-demo --all-securities                          # informe PDF por valor
python generate_charts.py --demo --all-securities  # panel con el gráfico por valor
```

En el menú, las dos opciones *Run Demo* ahora preguntan si quieres la demo de un
solo valor o la de cartera multivalor.

## Inicio rápido

```bash
# 1. (opcional) asigna a su ISIN los valores de Revolut que solo tengan ticker
echo '{ "isin_map": { "TSLA": "US88160R1014" } }' > input/securities.json

# 2. ejecuta el modo cartera
tax-engine --all-securities          # informe PDF
python generate_charts.py            # panel (gráfico de desglose por valor)
```
