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
| **Compensación de Pérdidas Multianual** | ❌ Fuera de Ámbito | Art. 49 LIRPF — Debe aplicarse de forma manual en el Modelo 100 |
| **Modelo 720 (Bienes en el Extranjero)** | ❌ Fuera de Ámbito | Obligación informativa independiente (si el saldo supera los 50.000 €) |

---

## 2. Metodología de Cálculo Principal

### Asignación de Lotes por FIFO (Primero en Entrar, Primero en Salir)
Según el **Art. 37.2 de la LIRPF**, las acciones de una misma compañía se consideran homogéneas. Al realizar una venta, el motor descuenta las acciones empezando siempre por las compras o liberaciones más antiguas.
* La ganancia o pérdida patrimonial se calcula por cada lote asignado:
  $$\text{Ganancia/Pérdida} = (\text{Precio de Venta en EUR} - \text{Coste de Adquisición en EUR}) \times \text{Acciones}$$
* Si una orden de venta consume múltiples lotes de adquisición, el motor divide la transacción y computa la ganancia de forma independiente para cada lote.
* Los lotes se eliminan del inventario activo cuando su número de acciones restantes llega a `0`.

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

1. **Compensación de Pérdidas (Art. 49 LIRPF):** El motor calcula saldos anuales independientes. Si el saldo general de la base del ahorro resulta negativo en un año, el contribuyente dispone de los 4 ejercicios siguientes para compensar dichas pérdidas. **Tu gestor o asesor fiscal debe declarar esta compensación en el Modelo 100.**
2. **Un Solo Ticker:** El motor fiscal asume que todas las operaciones son sobre el mismo valor (ej. acciones de tu empleador). Si negocias diferentes acciones, debes procesar archivos independientes para no mezclar los lotes FIFO.
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
