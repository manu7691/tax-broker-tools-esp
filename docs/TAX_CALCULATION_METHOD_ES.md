# Método de Cálculo de Impuestos FIFO (España) y Auditoría de Cumplimiento

Este documento explica la metodología de cálculo utilizada por el motor fiscal de E-Trade para procesar operaciones con acciones (RSU, ESPP, Stock Options) y analiza el cumplimiento de dicho motor con la normativa del Impuesto sobre la Renta de las Personas Físicas (LIRPF) en España.

---

## 1. Veredicto de Cumplimiento Normativo

El motor fiscal implementa un sistema de cálculo FIFO (First-In, First-Out) totalmente adaptado a los requisitos exigidos por la **Agencia Tributaria (Hacienda)**.

| Requisito Regulatorio / Técnico | Estado | Base Legal / Referencia |
| :--- | :---: | :--- |
| **Método de Asignación FIFO** | ✅ Conforme | Art. 37.2 LIRPF — Cómputo por lotes de valores homogéneos |
| **Tipos de Cambio Oficiales (BCE)** | ✅ Conforme | Conversión diaria utilizando datos del Banco Central Europeo |
| **Escala de Gravamen del Ahorro** | ✅ Conforme | Art. 66 LIRPF — Tramo progresivo estatal y autonómico 2024–2026 (19% al 28%) |
| **Norma de los 2 Meses (Wash Sales)** | ✅ Conforme | Art. 33.5.f LIRPF — Bloqueo proporcional de pérdidas patrimoniales |
| **Deducción de Gastos de Operación** | ✅ Conforme | Art. 35.1 y 35.2 LIRPF — Gastos inherentes a la adquisición y transmisión |
| **Coste de Adquisición de RSU** | ✅ Conforme | Valor de mercado (FMV) a fecha de liberación (evita doble imposición) |
| **Coste de Adquisición de ESPP** | ✅ Conforme | Valor de mercado (FMV) a fecha de compra |
| **Control de Mantenimiento de 3 Años ESPP** | ✅ Autodetectado | Art. 42.3.f LIRPF — Identifica ventas tempranas y rendimiento del trabajo |
| **Compensación de Pérdidas (4 años)** | ⚠️ Simulada | Art. 49 LIRPF — Libro de Compensación entre los años analizados; inicializa pérdidas previas con `prior_losses.json` (ver §4.1) |
| **Compensación entre Categorías (límite 25%)** | ⚠️ Simulada | Art. 49 LIRPF — Se calcula al aportar `savings_income.json` (dividendos/intereses); el límite depende del año (ver §4.1) |
| **Modelo 720 (Bienes en el Extranjero)** | ❌ Fuera de Ámbito | Obligación informativa independiente (si el saldo supera los 50.000 €) |

---

## 2. Metodología de Cálculo Principal

### Flujo de Datos

```mermaid
flowchart TD
    A["E*TRADE ESPP<br/>BenefitHistory.xlsx"] --> P
    B["E*TRADE Órdenes<br/>orders.xlsx"] --> P
    C["E*TRADE RSU (PDF)"] --> P
    D["E*TRADE Opciones (PDF)"] --> P
    E["CSV de Revolut<br/>(movimientos / ganancias)"] -->|"filtrar al ISIN/ticker analizado<br/>etiquetar bróker = Revolut"| P
    P["Eventos combinados<br/>(un único valor)"] --> F["Motor FIFO<br/>+ regla de los 2 meses<br/>+ BCE USD→EUR por fecha"]
    F --> R["Informe PDF (EN/ES)<br/>libro · subtotal por bróker · base del ahorro"]
    F --> G["charts_dashboard.html"]
```

Cada fuente se convierte en el mismo flujo de `StockEvent`, se etiqueta con su bróker y pasa por una única cola FIFO. Las filas de Revolut se filtran al único valor analizado antes de unirse al conjunto (ver *Ámbito del FIFO* más abajo).

### Asignación de Lotes por FIFO (Primero en Entrar, Primero en Salir)
Según el **Art. 37.2 de la LIRPF**, las acciones de una misma compañía se consideran homogéneas. Al realizar una venta, el motor descuenta las acciones empezando siempre por las compras o liberaciones más antiguas.
* La ganancia o pérdida patrimonial se calcula por cada lote asignado:
  $$\text{Ganancia/Pérdida} = (\text{Precio de Venta en EUR} - \text{Coste de Adquisición en EUR}) \times \text{Acciones}$$
* Si una orden de venta consume múltiples lotes de adquisición, el motor divide la transacción y computa la ganancia de forma independiente para cada lote.
* Los lotes se eliminan del inventario activo cuando su número de acciones restantes llega a `0`.

### Ámbito del FIFO: Por Valor, No Entre Valores
El FIFO se aplica **por valor homogéneo** — es decir, por ISIN (Art. 37.2 LIRPF). Cada valor tiene su **propia cola FIFO independiente**: una venta de *DT* solo puede casarse con adquisiciones anteriores de *DT*, nunca con *TSLA* o *NVDA*. Nunca se cruza el FIFO entre tickers distintos.

* **Este motor procesa un valor cada vez.** Asume que toda operación de entrada es el mismo valor homogéneo (en la práctica, las acciones de tu empleador, p. ej. DT) y mantiene una única cola FIFO. Por eso el import opcional de Revolut se **filtra a ese único valor** (por ISIN, o por ticker en el export de movimientos); las filas de otros tickers se descartan.
* **Los demás valores también tributan y deben declararse aparte.** Si además compraste y vendiste *otros* tickers en Revolut (TSLA, NVDA, ADBE, …), cada uno es su propio cálculo FIFO que esta herramienta **no** computa. Ejecuta el motor una vez por ticker (ajustando `input/ticker.json`), o gestiónalos por separado, y combina los resultados como se indica abajo.

**Cómo se agrega para Hacienda (Modelo 100, base del ahorro):**
1. Calcula la ganancia/pérdida neta de cada valor con su **propia** cola FIFO (por ISIN).
2. **Suma** los resultados de todos los valores en el único bloque de *ganancias y pérdidas patrimoniales* de la base del ahorro — las pérdidas de un valor compensan las ganancias de otro dentro de este bloque (no están aisladas por valor).
3. Ese neto compensa luego (hasta el límite del 25%) con el bloque de dividendos/intereses (RCM), y el resto se arrastra 4 años (Art. 49 LIRPF).

Por tanto, **el FIFO es por valor, pero el resultado imponible es el agregado** de todos ellos en la base del ahorro.

### Orden de Procesamiento del Mismo Día (Same-Day Events)
Para garantizar la coherencia del inventario FIFO y evitar posiciones negativas temporales (especialmente en ventas automáticas para cubrir impuestos o "sell-to-cover"), las transacciones del mismo día natural se ordenan así:
1. **VEST / BUY / EXERCISE** (todas las adquisiciones del día)
2. **SELL** (todas las ventas del día)

### Conversión de Moneda
Todas las operaciones se convierten de USD a EUR:
1. Utiliza los tipos de cambio diarios publicados por el Banco Central Europeo (BCE).
2. Invierte el tipo de cambio oficial EUR/USD para obtener el contravalor exacto USD/EUR.
3. Si la transacción se realiza en fin de semana o festivo, el motor aplica de forma automática el tipo de cambio del último día hábil anterior.

---

## 3. Normativa Fiscal Aplicada (Reglas Avanzadas)

### La Regla de los 2 Meses (*Norma Anti-Aplicación*)
De acuerdo con el **Art. 33.5.f de la LIRPF**, no se pueden integrar las pérdidas patrimoniales derivadas de la venta de acciones si se han adquirido valores homogéneos dentro del plazo de **2 meses anteriores o posteriores** a dicha venta.
* **Bloqueo Proporcional:** El importe de la pérdida bloqueada se limita al número de acciones de sustitución adquiridas.
  $$\text{Acciones Bloqueadas} = \min(\text{Acciones Vendidas con Pérdida}, \text{Acciones de Sustitución en Cartera})$$
* **Criterio de Permanencia:** El programa solo bloquea la pérdida de aquellas acciones de sustitución que *permanecen* en cartera al final del ejercicio (las consumidas por la propia venta no activan la regla).
* **Tratamiento:** Las pérdidas bloqueadas quedan diferidas y no compensan ganancias en el año corriente, debiendo integrarse en el futuro cuando se vendan las acciones de sustitución.

### Gastos Inherentes y Comisiones Deducibles
De conformidad con los **Art. 35.1 y 35.2 de la LIRPF**, los gastos directamente relacionados con la adquisición y la transmisión de los valores minoran el valor de enajenación o incrementan el de adquisición.
* El motor calcula y deduce comisiones de corretaje, tasas SEC y comisiones por asistencia (*Brokerage Assist Fees*).
* El contribuyente puede registrar gastos financieros por transferencias internacionales (comisiones de salida de E-Trade) para deducirlos como costes inherentes a la transacción.

### Exención de ESPP por Mantenimiento de 3 Años
El descuento del ESPP (hasta 12.000 € anuales) está exento de tributación si:
1. Las acciones se mantienen en cartera al menos **3 años** desde la fecha de compra.
2. El plan de compra se ofreció a toda la plantilla bajo las mismas condiciones.

**Control de Venta Anticipada:**
* El motor escanea el histórico de ventas. Si detecta la venta de acciones de ESPP antes de cumplir los 3 años, califica el descuento original como **Rendimiento del Trabajo** ordinario.
* El ingreso se imputa al **Año de Compra**, obligando a presentar una **Declaración Complementaria** de ese ejercicio. Esto devenga intereses de demora, pero no conlleva multas si se realiza voluntariamente antes de un requerimiento de Hacienda.

---

## 4. Limitaciones de Ámbito y Advertencias

> **Por qué no se automatizan:** cada punto siguiente necesita datos que el motor nunca ve — tus resultados de *otros años* o ingresos de *otras categorías* (dividendos, intereses). Esto corresponde al Modelo 100 final, donde todo se agrega. El motor produce una hoja de cálculo anual y de un solo valor; las reglas siguientes se aplican encima de ella en el momento de presentar la declaración.

### 4.1 Tratamiento de Pérdidas (Art. 48 y 49 LIRPF)

El **Resumen Fiscal Anual** informa las ganancias y pérdidas de cada año de forma independiente. Además, el **Libro de Compensación de Pérdidas** simula el arrastre entre años; la compensación entre categorías sigue haciéndose al presentar la declaración.

**a) Compensación en 4 años (Art. 49 LIRPF).** Si tu *base del ahorro* resulta negativa en un año —las pérdidas superan a las ganancias— la pérdida no se pierde. Se arrastra para compensar ganancias durante los **4 ejercicios siguientes**. El Libro de Compensación aplica esto automáticamente entre los años analizados (primero las más antiguas) y avisa de las que caducan sin usar.

> *Ejemplo:* 2024 arroja un neto de −3.000 € (pagas 0 € de impuesto y arrastras −3.000 €). 2025 tiene +5.000 € de ganancias → compensas los −3.000 €, por lo que solo tributan 2.000 €. El libro lo muestra directamente. **Las pérdidas anteriores a tu ventana de datos** no las ve el motor: inicialízalas con `input/prior_losses.json` (p. ej. `{"2024": 3000}`) o `--prior-losses <archivo>`.

**b) Compensación entre categorías, límite del 25% (Art. 49 LIRPF).** La base del ahorro tiene dos compartimentos estancos: *ganancias/pérdidas patrimoniales* (tus ventas de acciones) y *rendimientos del capital mobiliario* (dividendos, intereses). Una pérdida neta en un compartimento puede compensar hasta el **25%** del saldo positivo del otro (el límite depende del año: 10% en 2015, 15% en 2016, 20% en 2017, 25% desde 2018).

> *Ejemplo:* una pérdida neta de −2.000 € en acciones puede reducir tu base de dividendos/intereses hasta el 25% de dicho importe en el mismo año; el remanente no utilizado se arrastra 4 años. El motor lo calcula **al aportar `input/savings_income.json`** (dividendos/intereses en EUR); si no, solo ve operaciones con acciones. La retención en origen se informa a título orientativo — la *deducción por doble imposición internacional* la aplica tu asesor.

**Qué facilitar a tu asesor:** la ganancia/pérdida neta anual de este motor, para que la encaje en las casillas de compensación (arrastre y entre categorías) del Modelo 100 junto con tus demás rentas del ahorro.

### 4.2 Otras limitaciones

2. **Un Solo Ticker:** El motor fiscal asume que todas las operaciones son sobre el mismo valor (en la práctica, las acciones de tu empleador). Si negocias diferentes acciones, debes procesar archivos independientes para no mezclar los lotes FIFO. *Excepción:* el CSV opcional de Revolut (`input/revolut/*.csv`) puede contener muchos tickers — el motor lo filtra al valor analizado (por **ISIN** en el export de ganancias realizadas, o por **ticker** en el export de movimientos de cuenta, que no tiene ISIN) según `input/ticker.json` y descarta el resto, de modo que solo entran acciones homogéneas en el conjunto FIFO. El export de movimientos aporta además las **compras** de acciones que nunca vendiste, completando el conjunto de coste de adquisición.

   **Ámbito entre brókers (Revolut):** por defecto la regla de los 2 meses y el orden FIFO solo ven esta cuenta de E\*TRADE. Si tenías el *mismo* valor (mismo ISIN) en Revolut, colocar su CSV de pérdidas y ganancias en `input/revolut/` integra esas compras/ventas en la **misma cola FIFO**, por lo que el FIFO entre brókers y la regla de los 2 meses se evalúan correctamente entre ambos — como exige España para los valores homogéneos. Esto requiere el **historial completo de adquisiciones** de ese valor; de lo contrario la cola global puede quedar en negativo y el motor lanzará un error.
3. **Modelo 720:** Si tus cuentas o valores en el extranjero superan conjuntamente los 50.000 € a 31 de diciembre (o en saldos medios del último trimestre), debes presentar la declaración informativa Modelo 720 de forma independiente.

---

## 5. Plantillas y Notas Informativas

### Resumen para tu Asesor Fiscal
Facilita la siguiente información a tu gestor para explicarle el informe:
* "Este informe utiliza una **asignación estricta por el método FIFO** y aplica los **tipos de cambio diarios del BCE** en las fechas exactas de las operaciones."
* "Los gastos de transacción (comisiones, tasas SEC y Brokerage Assist) se han deducido directamente como *gastos inherentes* (Art. 35 LIRPF)."
* "Se ha aplicado la **regla de los 2 meses** (Art. 33.5.f LIRPF) bloqueando las pérdidas en proporción a las acciones de sustitución que permanecen en cartera."
* "El programa controla de manera automática las **ventas anticipadas de ESPP** (< 3 años) para aislar el descuento de compra y poder declararlo como *Rendimiento del Trabajo* mediante declaración complementaria del año de adquisición."

### Justificante para Hacienda
El informe PDF en español (`tax_report_ES_*.pdf`) emitido por la calculadora sirve como desglose justificativo para la Agencia Tributaria. Incluye el libro diario completo convertido a EUR, el detalle de cruce FIFO de cada operación, el cálculo del tramo progresivo del ahorro y la conciliación del rendimiento del trabajo por ESPP.
