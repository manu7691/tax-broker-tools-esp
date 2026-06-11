# Panel Interactivo de Gráficos e Impuestos — Guía

Este repositorio puede generar un **panel HTML interactivo** que convierte tus datos de
E-Trade en una imagen clara y sin tecnicismos de tus acciones de empresa: qué posees,
cuánto valen, cuánto te quedaría neto si vendieras y tu situación fiscal en España.

Está pensado para un **empleado normal, no para un experto en finanzas**. No necesitas
saber jerga: cada cifra tiene una explicación de una línea, y las herramientas de mercado
avanzadas están guardadas en su propia pestaña.

> ⚠️ El panel es **solo informativo** y usa un **precio de mercado en vivo** obtenido de
> Yahoo Finance. No es asesoramiento fiscal. El archivo generado
> (`charts_dashboard.html`) está en `.gitignore` y nunca sale de tu ordenador.

---

## Cómo generarlo

**Lo más fácil (menú):** ejecuta `run_tax_engine.command` (macOS/Linux) o
`run_tax_engine.bat` (Windows):
- Elige la **opción 6 — "Generate Charts & Tax Dashboard"** para tus datos reales (`charts_dashboard.html`).
- Elige la **opción 7 — "Generate Charts & Tax Dashboard - demo data"** para generar un panel de demostración (`charts_dashboard_demo.html`) con datos de prueba integrados (RSU, ESPP, Revolut) y simulaciones de cotizaciones del mercado sin realizar llamadas de red.

Haz doble clic en el archivo generado (`charts_dashboard.html` o `charts_dashboard_demo.html`) para verlo en tu navegador.

**Desarrollador (CLI):**

```bash
# Para datos reales:
uv run generate_charts.py

# Para datos de demostración:
uv run generate_charts.py --demo
```

Opciones:

| Flag | Efecto |
|------|--------|
| *(ninguno)* | Detecta todo automáticamente (revisa archivos de configuración y luego `BenefitHistory.xlsx`) y obtiene el precio en vivo. |
| `--ticker TICKER` | Especifica manualmente el ticker de la acción (p. ej. `--ticker DDOG`). Anula los archivos de configuración y la detección automática. |
| `--company-name NAME` | Especifica manualmente el nombre de la empresa (p. ej. `--company-name Datadog`). Anula los archivos de configuración y la búsqueda por API. |
| `--current-price 45.50` | Usa un precio fijo en USD en vez del precio en vivo. |
| `--include-espp` / `--skip-espp` | Fuerza incluir o excluir el análisis de ESPP (omite la pregunta). |
| `--peers DDOG MSFT NVDA` | Establece los tickers de pares para el gráfico comparativo (separados por espacio). |

Notas:

- El **ticker de la acción se detecta automáticamente** de tus datos (`BenefitHistory.xlsx`) de forma predeterminada, con fallback a `DT`.
- El **nombre de la empresa se obtiene automáticamente** a través de la API de Yahoo Finance o recurre a un mapeo local.
- Puedes configurar de forma persistente el ticker principal creando **`input/ticker.txt`** que contenga solo el símbolo (p. ej. `DDOG`), o **`input/ticker.json`** como `{"ticker": "DDOG", "company_name": "Datadog"}` (con `company_name` opcional).
- Si **hay datos de ESPP**, se te pregunta si quieres incluir el análisis de ESPP. Si no
  tienes datos de ESPP, ese paso se omite automáticamente.
- La primera ejecución necesita internet (para obtener el precio en vivo y los precios
  históricos); después, la caché de tipos del BCE funciona sin conexión.

### Configurar los tickers de pares

El gráfico "Comparativa con empresas similares" y los enlaces del Hub de Inteligencia
muestran los tickers que configures. Orden de prioridad (el mayor gana):

1. Flag `--peers` en la línea de comandos (anulación puntual).
2. **`input/peers.json`** — configuración persistente, se lee en cada ejecución.
3. Valores predeterminados: `["DDOG", "ESTC"]`.

Para establecer tus propios pares de forma permanente, crea `input/peers.json`.

* **Formato de un solo valor (lista simple):**
  ```json
  ["DDOG", "MSFT", "NVDA"]
  ```

* **Formato Multivalor / Cartera (diccionario por ticker):**
  Si trabajas en modo cartera, puedes definir competidores específicos para cada acción:
  ```json
  {
    "DT": ["DDOG", "ESTC"],
    "TSLA": ["RIVN", "LCID", "NIO"],
    "NVDA": ["AMD", "INTC"]
  }
  ```

Si el archivo no existe y usas el menú del lanzador, se te preguntará una vez antes de
generar el panel. Puedes añadir tantos tickers como quieras — cada uno aparecerá como
una línea en el gráfico y como un enlace de Yahoo Finance en el Hub de Inteligencia.

---

## Modo Cartera y Navegación Flotante (Sticky)

* **Menú de Navegación Flotante:** Los controles del panel (idioma, selector de acción, botón de privacidad y pestañas) se quedan fijos en la parte superior de la ventana del navegador con un fondo translúcido y desenfoque de cristal (`backdrop-filter`). Así puedes cambiar de pestaña o valor al instante sin tener que volver arriba.
* **Tarjeta de Resumen de Cartera:** Si analizas varios valores (modo cartera), aparecerá arriba de todo en la pestaña "Mis Acciones" un **Gráfico de Asignación de Cartera** (anillo) y una **Tabla de Posiciones** detallada (acciones, ISIN, coste medio, valor de mercado, ganancia latente y peso en cartera).
* **Selector Dinámico de Valores:** Cuando hay más de una acción en cartera, aparece un menú desplegable en la barra superior. Al seleccionar otro ticker, todos los gráficos, estimadores de ventas y listas de competidores se actualizan en vivo al instante. Una pequeña nota a su lado recuerda que el selector *"cambia solo las tarjetas de una acción"*.
* **Etiquetas de alcance (solo en modo cartera):** Como algunas tarjetas agregan **toda la cartera** y otras muestran solo el **valor seleccionado**, cada encabezado de tarjeta lleva una etiqueta de color para que siempre sepas qué estás viendo:
  * 🌐 **Toda la cartera** (azul) — agrega todo lo que posees e ignora el desplegable (p. ej. la tabla resumen, los desgloses por valor/por bróker, la exposición por divisa, la cosecha de pérdidas fiscales, el impuesto anual, el riesgo de concentración y los dividendos/intereses).
  * 🏷️ **Solo `<TICKER>`** (ámbar) — muestra solo el valor elegido en el desplegable y se reetiqueta al cambiar (p. ej. *Tu situación hoy*, el simulador de venta, el punto de equilibrio, las auditorías de ESPP/RSU y los gráficos de precio/tendencia/divisa/competidores).

  Estas etiquetas aparecen **solo cuando tienes más de un valor**. Con un único valor, la vista de toda la cartera y la de una sola acción son lo mismo, así que las etiquetas, el desplegable de valores y la tarjeta resumen quedan ocultos — el panel se mantiene como una vista limpia de una sola acción, sin nada que distinguir.

---

## Las cuatro pestañas (una historia sencilla)

El panel se organiza según las preguntas que realmente se hace una persona normal, en orden.

### 📊 Mis Acciones — *"¿Qué tengo ahora mismo?"*

- **Su situación hoy** — una foto sencilla: cuántas acciones aún posees, cuánto valen hoy
  (EUR), cuánto pagaste originalmente y si vas **ganando o perdiendo** en conjunto. Una
  frase lo resume en palabras llanas.
- **Cuadro de mando general** — ¿mereció la pena mantener? Muestra la ganancia/pérdida por
  mantener tus RSU y un desglose fiscal del ESPP en tres partes (ver *Mis Impuestos*).

### 💸 ¿Debería Vender? — *"¿Qué pasa si vendo?"*

- **Simulador de venta** — mueve los controles de precio y tipo de cambio para ver tu valor
  en EUR, la ganancia/pérdida, el impuesto estimado de Hacienda y el **💰 dinero neto en tu
  bolsillo después de impuestos**.
- **Aviso de bloqueo de ESPP** — si parte de las acciones que tienes son acciones ESPP que
  todavía están dentro de la ventana de 3 años exenta, un aviso te dice exactamente cuántas
  son y **cuánta exención fiscal perderías** al vender ahora.
- **Optimizador de tramos de Hacienda** — muestra en qué tramo del ahorro (19–28%) cae tu
  ganancia y cuánto más podrías materializar antes de saltar de tramo. También cuenta los
  dividendos/intereses que hayas registrado este año.
- **Precios de equilibrio** — dos cifras claramente etiquetadas:
  - **Equilibrio Desde Cero** — el precio en USD para vender tus acciones *actuales* sin
    ganancia ni pérdida, ignorando ventas pasadas (una cifra "desde cero").
  - **Equilibrio del Portafolio Completo** — lo mismo, pero ajustado por tus
    ganancias/pérdidas realizadas en el pasado.
  - Un interruptor **"Excluir ESPP (solo RSU)"** filtra de forma coherente tanto las
    acciones actuales como las cifras realizadas del pasado.

### 🧾 Mis Impuestos — *"¿Y los impuestos?"*

- **Cuenta atrás de exención del ESPP** — cada lote de ESPP que posees, con la fecha exacta
  en que supera el periodo de 3 años y queda exento, más un desglose
  "asegurado / en riesgo / perdido":
  - 🟢 **Asegurado** — exención ya consolidada (mantenido ≥ 3 años).
  - 🟡 **En Riesgo** — exención pendiente; solo la conservas si mantienes hasta el plazo.
  - 🔴 **Penalización** — exención ya perdida por vender antes de 3 años.
- **Auditoría fiscal del ESPP** (gráfico de anillo) — el mismo desglose
  asegurado/en riesgo/perdido, de forma visual.
- **RSU: mantener vs. vender al vest** — si mantener tus RSU superó a venderlas el día del
  vest.
- **Ingresos por dividendos e intereses** — los dividendos/intereses tributan en la **misma
  "base del ahorro"** española que tus ganancias de acciones, así que te suben de tramo
  antes de vender. Añádelos con la **opción 3** del menú (descárgalos de E\*TRADE
  automáticamente o introdúcelos a mano); luego alimentan el optimizador de tramos.

### 🔬 Avanzado (para los curiosos) — contexto de mercado opcional

Tendencia del precio con medias móviles, de dónde vinieron los beneficios de tus ventas
(acción vs. divisa), la línea temporal de transacciones, el histórico del tipo de cambio
USD→EUR, una comparativa con competidores/pares, consejos para configurar alertas de venta
y un medidor de riesgo de concentración en una sola acción. Nada de esto es necesario para
las decisiones principales — está ahí por si lo quieres.

---

## Funciones útiles

- **🌐 Selector de idioma (EN / ES)** — todas las etiquetas, tablas, ejes de los gráficos y
  los consejos dinámicos cambian de idioma. Las fechas siguen el formato español DD/MM/AAAA
  y los nombres de los meses están traducidos.
- **👁️ Modo privacidad** — un clic difumina todas las cifras sensibles en euros para que
  puedas compartir pantalla o enseñárselo a tu asesor sin exponer importes.
- **Todo automático** — el ticker, el número de acciones, el coste de adquisición y el
  emparejamiento FIFO de las ventas pasadas se derivan de tus datos existentes; sin
  introducir nada a mano.

---

## Cómo se calculan las cifras clave

- **Valor hoy / equilibrio** usan tu **base de coste en EUR** (los euros que realmente
  pagaste, convertidos al tipo del BCE en cada fecha de adquisición), porque eso es lo que
  importa para el impuesto español — no el precio en USD a secas.
- **Las ventas pasadas se emparejan por FIFO** (los lotes más antiguos primero, como exige
  la ley española), de modo que las "acciones que aún posees" y su coste reflejan qué lotes
  concretos quedan. Por eso un equilibrio aquí puede diferir de la vista solo-USD de E-Trade.
- **El coste base del ESPP** usa el FMV (valor de mercado) de la fecha de compra, coherente
  con el resto del motor fiscal.
- **Se incluyen las posiciones del mismo valor en Revolut.** Si colocaste un CSV de Revolut
  en `input/revolut/` (ver el README), esas compras/ventas de tu **ticker analizado** se
  integran en la misma posición, de modo que los gráficos muestran la imagen **combinada**
  de E\*TRADE + Revolut, igual que el informe PDF. Los gráficos cubren un único valor — los
  demás tickers que tengas en Revolut no se muestran aquí (tienen su propio FIFO independiente;
  ver [TAX_CALCULATION_METHOD_ES.md](TAX_CALCULATION_METHOD_ES.md)).

Para la metodología fiscal de fondo, consulta
[TAX_CALCULATION_METHOD_ES.md](TAX_CALCULATION_METHOD_ES.md) y
[ESPP_TAX_GUIDE_ES.md](ESPP_TAX_GUIDE_ES.md).
