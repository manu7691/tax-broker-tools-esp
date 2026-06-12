---
marp: true
title: Cómo se genera el Informe Fiscal en PDF
author: tax-broker-tools-esp
paginate: true
---

# Cómo se genera el Informe Fiscal en PDF

**Una herramienta para calcular ganancias patrimoniales de acciones de E\*TRADE / Revolut según la normativa fiscal española**

Presentación para el asesor / gestor fiscal

> ⚠️ La herramienta es de apoyo. **El cálculo final siempre lo valida usted.**

---

## La idea en una frase

> Tomamos los datos **en bruto** del bróker (compras, vestings, ejercicios y ventas),
> les aplicamos el método **FIFO** y las reglas del **IRPF español**, y volcamos el
> resultado en un **PDF** ordenado por casillas del **Modelo 100**.

Un solo origen de datos → un solo cálculo → un solo informe.
**Nada se introduce a mano.**

---

## El recorrido de los datos (4 pasos)

```
  1. DATOS BRUTOS          2. MOTOR FISCAL        3. PLANTILLA        4. PDF
  ───────────────          ──────────────         ──────────         ─────
  E*TRADE (Excel):    →    FIFO + reglas    →     Informe HTML   →   Chromium
   · Compras ESPP          IRPF:                  con todas las       imprime
   · Vestings RSU           · orden FIFO          secciones y         el HTML
   · Ejercicio opciones     · ESPP art. 42.3.f    tablas              a A4
   · Ventas                 · regla 2 meses                           (PDF final)
  Revolut (CSV):            · base del ahorro
   · Operaciones            · dividendos/intereses
   · Dividendos
```

Cada número del PDF se puede rastrear hasta una fila concreta del Excel del bróker.

---

## Paso 1 — De dónde salen los datos

Se leen **directamente de los ficheros que descarga E\*TRADE** (y, si aplica, el CSV de Revolut):

| Tipo de evento | Origen | Qué aporta |
|---|---|---|
| **Compras ESPP** | Excel E\*TRADE | Precio de compra y descuento de empresa |
| **Vestings de RSU** | Excel E\*TRADE | Acciones consolidadas y su valor |
| **Ejercicio de opciones** | Excel E\*TRADE | Coste de adquisición |
| **Ventas** | Excel E\*TRADE | Fecha, nº de acciones y precio de venta |
| **Revolut** (opcional) | CSV | Operaciones y dividendos del mismo valor |

No hay introducción manual de cifras: se parsean los ficheros oficiales del bróker.

---

## Paso 2 — El motor fiscal (el corazón del cálculo)

Todos los eventos pasan por **un único motor** que aplica, en este orden:

1. **FIFO** — las acciones que se venden son las **más antiguas** primero (criterio obligatorio en España, no se permite identificación específica de lotes).
2. **Conversión a EUR** — cada operación se convierte con el **tipo de cambio del BCE** de su fecha (no un tipo medio anual).
3. **Detección automática de *sell-to-cover*** — las ventas para cubrir retención en el vesting se identifican y clasifican solas.
4. **Reglas especiales** (siguiente diapositiva).

---

## Paso 2 — Reglas fiscales que aplica

<style scoped>table { font-size: 0.72em; }</style>

| Regla | Base legal | Qué hace |
|---|---|---|
| **Método FIFO** | Art. 37.2 LIRPF | Empareja ventas con las compras más antiguas |
| **Exención ESPP** | Art. 42.3.f LIRPF | El descuento de empresa está exento si se mantienen las acciones **3 años**; si se venden antes, pasa a **rendimiento del trabajo** |
| **Regla de los 2 meses** | Art. 33.5.f LIRPF | Bloquea pérdidas si se recompra el mismo valor en ±2 meses (anti *wash-sale*) |
| **Base del ahorro** | Art. 49 LIRPF | Integra ganancias + dividendos + intereses y compensa pérdidas |
| **Compensación 4 años** | Art. 49 LIRPF | Arrastra pérdidas pendientes a ejercicios futuros |

---

## Paso 3 y 4 — Del cálculo al PDF

- El resultado del motor rellena una **plantilla de informe** (HTML) con todas las tablas.
- Un navegador **Chromium** (automatizado) **imprime ese HTML a PDF** en formato A4.
- Se generan **dos versiones**: 🇪🇸 español y 🇺🇸 inglés, con los **mismos números**.

> El PDF es, literalmente, el cálculo del motor «impreso». No se retoca después.

---

## Qué contiene el PDF (sección por sección)

1. **Metodología** — explica FIFO y las reglas aplicadas.
2. **Resumen de cartera por valor** — acciones que quedan y coste medio.
3. **Resumen Fiscal Anual** — ganancia/pérdida por año (Base del Ahorro, Modelo 100).
4. **Ganancias/pérdidas por bróker** — desglose E\*TRADE vs Revolut.
5. **Base del Ahorro** — ganancias + dividendos/intereses (Art. 49).
6. **Libro de compensación de pérdidas** — arrastre a 4 años.

---

## Qué contiene el PDF (continuación)

7. **Detalle de transmisiones** — cada venta, lista para el Modelo 100.
8. **Guía de cumplimentación del Modelo 100** — qué casilla rellenar con qué cifra.
9. **Análisis ESPP a 3 años** — exento vs tributable (Art. 42.3.f).
10. **Libro de transacciones detallado** — cada operación con su cálculo FIFO paso a paso.

> Las secciones 7 y 8 son las pensadas **directamente para la declaración**.

---

## Ejemplo 1 — Una venta con FIFO paso a paso

**Situación.** Tienes dos lotes de RSU del mismo valor y vendes parte:

| Lote | Fecha (vesting) | Acciones | Precio | Tipo BCE | Coste en EUR |
|---|---|---|---|---|---|
| Lote 1 | 15/03/2022 | 10 | 100 $ | 0,90 | **900,00 €** |
| Lote 2 | 20/06/2023 | 10 | 120 $ | 0,92 | **1.104,00 €** |

➡️ **Vendes 15 acciones** el 10/09/2024 a 150 $, tipo BCE 0,91.

---

## Ejemplo 1 — El cálculo del motor

**Importe de la venta (EUR):** 15 × 150 $ × 0,91 = **2.047,50 €**

**FIFO → se venden las más antiguas primero:** 10 del Lote 1 + 5 del Lote 2

| Acciones | De | Coste EUR |
|---|---|---|
| 10 | Lote 1 (todo) | 900,00 € |
| 5 | Lote 2 (5 de 10 → 5×120×0,92) | 552,00 € |
| **15** | | **1.452,00 €** |

**Ganancia patrimonial** = 2.047,50 − 1.452,00 = **595,50 €** → Base del Ahorro

**Quedan en cartera:** 5 acciones del Lote 2 (coste 552,00 €), listas para la próxima venta.

---

## Ejemplo 2 — El descuento ESPP (Art. 42.3.f)

**Situación.** Compra ESPP: precio de mercado 100 $, pagas el 85 % = 85 $.
Descuento de empresa = **15 $/acción × 10 acciones = 150 $**.

| Escenario | Tratamiento fiscal |
|---|---|
| 🟢 Mantienes las acciones **≥ 3 años** | El descuento de 150 $ está **EXENTO** (no tributa) |
| 🔴 Vendes **antes de 3 años** | Los 150 $ (convertidos a EUR) pasan a **Rendimiento del Trabajo** y tributan |

> El informe coloca cada caso en su sección: exento → solo informativo; venta anticipada → rendimientos del trabajo. **La ganancia/pérdida por la venta de la acción se calcula aparte, siempre por FIFO.**

---

## Ejemplo 3 — La Base del Ahorro (Art. 49)

Se integran ganancias, dividendos e intereses, y se compensan pérdidas:

| Concepto (año 2024) | Importe |
|---|---|
| Ganancia patrimonial (Ejemplo 1) | +595,50 € |
| Dividendos / intereses (convertidos a EUR) | +200,00 € |
| Pérdidas de otras ventas | −100,00 € |
| **Base del ahorro neta** | **695,50 €** |

**Escala progresiva del ahorro:** 19 % hasta 6.000 € · 21 % de 6.000–50.000 € · 23 % de 50.000–200.000 € · 27 % de 200.000–300.000 € · 28 % por encima.

➡️ Aquí: 695,50 € × 19 % ≈ **132,15 € de cuota** estimada.

---

## Ejemplo 4 — La regla de los 2 meses (anti *wash-sale*)

**Situación.** Vendes con **pérdida** y recompras el mismo valor poco después:

| Operación | Fecha | Resultado |
|---|---|---|
| Venta con pérdida de −300 € | 10/05/2024 | Pérdida... |
| Recompra del **mismo valor** | 02/06/2024 (< 2 meses) | 🚫 Pérdida **bloqueada** |

➡️ La pérdida de −300 € **no se puede computar ahora**: queda «aparcada» y se incorporará al coste de las nuevas acciones (se aprovechará cuando las vendas definitivamente).

> El informe marca estas pérdidas como bloqueadas para que no se declaren por error.

---

## Cómo se ven los ejemplos en el PDF

| Ejemplo | Sección del informe donde aparece |
|---|---|
| **1 — Venta FIFO** | *Detalle de transmisiones* + *Libro de transacciones detallado* |
| **2 — ESPP** | *Análisis ESPP a 3 años* (exento) o *Rendimientos del trabajo* (venta anticipada) |
| **3 — Base del ahorro** | *Resumen Fiscal Anual* + *Base del Ahorro* |
| **4 — Regla 2 meses** | *Libro de compensación de pérdidas* (pérdida bloqueada) |

Todos los importes en EUR, con el **tipo del BCE de cada fecha**.

---

## Por qué se puede confiar en cada número

- **Trazabilidad total**: cada fila del informe procede de una fila del Excel del bróker.
- **Tipo de cambio oficial**: BCE, por fecha de operación (con caché auditable).
- **Sin entrada manual**: se elimina el error de transcribir cifras a mano.
- **Mismo motor, dos salidas**: el PDF (declaración) y el panel interactivo (decisión) comparten los mismos cálculos; los números de anclaje deben coincidir.
- **Cobertura de tests automatizados** sobre las reglas fiscales (FIFO, ESPP, clasificación de ventas).

---

## Lo que el informe **no** hace (límites)

- No presenta la declaración: **produce las cifras**, usted las introduce/valida.
- No sustituye su criterio profesional ni el asesoramiento fiscal.
- Refleja **nuestra interpretación** de la norma; usted tiene la última palabra.
- Los precios «en vivo» (valor de hoy) viven en el panel, **no** en el PDF; el PDF es una **instantánea fiscal fija**.

---

## Resumen para el gestor

> **Datos oficiales del bróker → método FIFO + reglas del IRPF → PDF ordenado por
> casillas del Modelo 100.**

- Un solo origen, un solo cálculo, sin retoques manuales.
- Cada cifra es **rastreable** y convertida a EUR con el **tipo del BCE** por fecha.
- El PDF separa lo que va a **ganancias patrimoniales**, **rendimientos del trabajo** (ESPP vendido antes de 3 años) y **base del ahorro** (dividendos/intereses).

**¿Qué necesita de usted?** Revisar las secciones de *Transmisiones* y la *Guía del Modelo 100* y confirmar que el enfoque le encaja.
