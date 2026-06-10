# Informe ↔ Panel — Cómo se conectan las dos salidas

Este proyecto genera **dos salidas a partir del mismo cálculo**:

1. El **informe fiscal en PDF** (`tax_report_ES_*.pdf` / `tax_report_EN_*.pdf`) — la
   vista *fiscal*, pensada para tu declaración española (Modelo 100).
2. El **panel interactivo** (`charts_dashboard.html`) — la vista de *decisión*,
   pensada para responder «¿qué tengo, cuánto vale y qué pasa si vendo?».

Si alguna vez has mirado ambos y has pensado *«parece que no tienen nada que
ver»*, esta guía es para ti. **Comparten los mismos números** — solo que los
muestran con propósitos distintos.

---

## Salen de un solo motor, no de dos

**No hay dos canales de datos separados.** Ambas salidas:

1. Cargan los **mismos eventos**: compras ESPP, consolidaciones (vest) de RSU,
   ejercicios de opciones, ventas de E\*TRADE y (si están presentes) operaciones de
   Revolut del mismo valor.
2. Ejecutan la **misma detección automática de sell-to-cover**.
3. Pasan todo por el **mismo motor fiscal FIFO** (`TaxEngine.process_all`).

Todo lo que ves en cualquiera de los dos ficheros se lee de los mismos tres
resultados del motor:

| Resultado del motor | Significado sencillo |
|---|---|
| `processed_events` | Cada adquisición y venta, con su ganancia/pérdida realizada |
| `state.lots` | Los lotes concretos de acciones que aún conservas (orden FIFO) |
| Totales de `state` | Tu número actual de acciones y el coste medio en EUR |

Por tanto, el informe y el panel son **dos ventanas al mismo libro contable**, no
dos cálculos independientes. Si uno de los **números de anclaje compartidos** de
abajo no coincide entre ellos, es un error — no una diferencia de método.

> 🕒 **Genera ambos el mismo día.** El panel usa un **precio de mercado en vivo** y
> «hoy» como fecha de referencia, mientras que el PDF es una instantánea fiscal
> fija. Por eso las cifras derivadas del precio en vivo (valor actual, «neto si
> vendiera hoy», cuentas atrás de ESPP) *sí* diferirán si generas los dos ficheros
> en días distintos — eso es lo esperado, no un error. Los **números de anclaje
> compartidos** (ganancias realizadas, cartera, coste medio, totales ESPP exento/
> tributable) **no** dependen del precio en vivo y deben coincidir en cualquier
> caso.

---

## Los números de anclaje compartidos

Estos son los valores que aparecen en **ambos** ficheros. Úsalos para cuadrar uno
con el otro:

| Número | En el informe PDF | En el panel | Origen común |
|---|---|---|---|
| **Ganancia/pérdida realizada por venta** | Filas del libro de transacciones | Gráfico «de dónde vino tu beneficio» (descomposición) | `processed_events[*].realized_gain_loss` |
| **Ganancias / pérdidas totales por año** | Tabla de Resumen Fiscal Anual | Descomposición + barras por bróker | `processed_events` agregados |
| **Acciones que aún posees** | Línea «Current Position: N shares» | Simulador de venta / lista de lotes no vendidos | `state.lots` (acciones restantes) |
| **Coste medio (EUR)** | «Current Informational Avg Cost» | Línea de coste medio en el gráfico de tendencia | `state.avg_cost_eur` |
| **Descuento ESPP — exento vs tributable** | Sección *Rendimientos del Trabajo* (exento, o tributable si se vendió antes) | Marcador ESPP: 🟢 asegurado / 🟡 en riesgo / 🔴 perdido | `calculate_espp_discounts` + `detect_espp_early_sales` |
| **Totales realizados por bróker** | Subtotal «Ganancias/Pérdidas Realizadas por Bróker» | Barras de comparación por bróker | `processed_events[*].event.broker` |
| **Dividendos / intereses** | Sección de base del ahorro | Panel de dividendos e intereses + optimizador de tramos | `load_savings_income` |

**Cómo comprobar tú mismo la correlación:** elige cualquier venta. Su ganancia/
pérdida en EUR en una fila del PDF es la *misma* cifra que el gráfico de
descomposición del panel divide en «movimiento de la acción» vs «movimiento de la
divisa» para esa fecha. Suma las ventas de un año en el Resumen Fiscal Anual del
PDF y obtienes el mismo total que muestran las barras anuales del panel.

> ⚠️ **El reparto ESPP en tres partes son los mismos datos mostrados de dos
> formas.** El PDF declara el descuento como *exento* (mantenido ≥ 3 años) o como
> *rendimiento del trabajo tributable* (vendido antes). El panel afina «exento» en
> 🟢 **asegurado** (ya superados los 3 años) vs 🟡 **en riesgo** (aún en cartera pero
> sin llegar a 3 años), y 🔴 **perdido** es la «venta anticipada tributable» del PDF.
> `asegurado + en riesgo = el total exento del PDF`.

---

## Lo que está en SOLO UNO de ellos (y por qué)

Esta suele ser la verdadera razón de que los dos *parezcan* desconectados: cada
fichero tiene algunos números que el otro omite a propósito.

### Solo en el panel — impulsados por un **precio de mercado en vivo**

| Número del panel | Por qué no está en el PDF |
|---|---|
| Valor actual de la cartera (EUR, hoy) | Depende del precio de hoy en Yahoo Finance — informativo, no un hecho fiscal |
| «Efectivo neto si vendiera hoy» / simulador | Una venta *hipotética* futura; aún no se ha realizado nada |
| Delta de RSU mantener-vs-vender | Una comparación condicional, no un evento declarado |
| **Cuentas atrás** de exención ESPP | Temporizadores a futuro; el PDF solo indica el estado exento/tributable actual |
| Tendencia de precio, medias móviles, comparación con pares | Contexto de mercado para decidir, irrelevante para la declaración |

El panel lo deja claro: usa un **precio en vivo** y es **solo informativo**. Por eso
estos números cambian entre ejecuciones mientras que los del PDF no.

### Solo en el informe — el **cálculo de la declaración**

| Número del PDF | Por qué no está en las páginas principales del panel |
|---|---|
| Cuota estimada por año | El informe es el documento fiscal de referencia |
| Pérdidas bloqueadas (regla de los 2 meses / wash-sale) | Mecánica fiscal detallada |
| Libro de compensación de pérdidas / base del ahorro (4 años) | Contabilidad fiscal plurianual para la declaración |
| Guía de casillas del Modelo 100 | Instrucciones de presentación, propias del PDF |

---

## Resumen en una línea

> **Mismos eventos → mismo motor FIFO → mismas ganancias realizadas, cartera, coste
> medio, estado ESPP y totales por bróker.** El PDF añade encima el *cálculo de la
> declaración* (cuota, compensación), y el panel añade un *precio en vivo* (valor
> actual, ventas hipotéticas, cuentas atrás). Los números de anclaje compartidos de
> la tabla de arriba son donde ambos coinciden exactamente.

Para la metodología subyacente, consulta
[TAX_CALCULATION_METHOD_ES.md](TAX_CALCULATION_METHOD_ES.md); para el recorrido del
panel, consulta [DASHBOARD_GUIDE_ES.md](DASHBOARD_GUIDE_ES.md).
