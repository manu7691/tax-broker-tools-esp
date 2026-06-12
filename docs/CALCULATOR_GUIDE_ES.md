# Guía para el Equipo: Calculadora Fiscal Multibróker (FIFO España)

![Calculadora Fiscal E-Trade](./images/calculator_banner_es.png)

Bienvenido a la **Calculadora Fiscal FIFO de España** para E-Trade. Este repositorio contiene un motor por línea de comandos (CLI) personalizado y diseñado para procesar tu historial de transacciones de E-Trade (RSUs, ESPPs, Stock Options) y calcular tus obligaciones tributarias en España de acuerdo con las normas de la **Agencia Tributaria (Hacienda)**.

Esta guía ayudará a los compañeros de equipo y colegas a entender cómo funciona la herramienta, cómo preparar los archivos y cómo ejecutarla.

---

## 1. Arquitectura del Sistema y Flujo de Datos

Así es como fluyen los datos a través de la calculadora:

```mermaid
graph TD
    subgraph Inputs["Archivos de Entrada (E-Trade)"]
        A["input/espp/BenefitHistory.xlsx<br>Historial de compras ESPP"]
        A2["input/orders/orders.xlsx<br>Historial de transacciones"]
        B["input/rsu/*.pdf<br>Confirmación de liberación RSU"]
        C["input/options/*.pdf<br>Confirmación de ejercicio de Opciones"]
    end

    subgraph CoreEngine["Motor de Cálculo Fiscal"]
        D["API de Tipos de Cambio BCE<br>Conversión de USD a EUR"]
        E["Motor FIFO de Cruce de Lotes<br>Art. 37.2 LIRPF"]
        F["Escala de Gravamen de la Renta<br>Base del Ahorro 19-28%"]
        G["Filtro de Norma de los 2 Meses<br>Art. 33.5.f LIRPF"]
        H["Control de Exención de 3 Años ESPP<br>Art. 42.3.f LIRPF"]
    end

    subgraph Outputs["Informes Generados"]
        I["Desglose detallado por Terminal"]
        J["tax_report_EN_*.pdf<br>PDF en Inglés de Referencia"]
        K["tax_report_ES_*.pdf<br>PDF en Español para Hacienda"]
    end

    A --> D
    A2 --> D
    B --> D
    C --> D
    
    D --> E
    E --> F
    E --> G
    E --> H
    
    F --> I
    G --> I
    H --> I
    
    I --> J
    I --> K

    style CoreEngine fill:#f5f7fa,stroke:#cbd5e1,stroke-width:1px
    style J fill:#e0f2fe,stroke:#38bdf8,stroke-width:2px
    style K fill:#dcfce7,stroke:#4ade80,stroke-width:2px
```

---

## 2. Características Principales (Lógica Fiscal Española)

La calculadora resuelve de forma automática las reglas fiscales complejas españolas que las plataformas americanas (como E-Trade) no calculan por defecto:

1. **Cruce Estricto por FIFO:** La ley española exige que las ventas de acciones se crucen con tus compras o liberaciones más antiguas primero (**Art. 37.2 LIRPF**), ignorando cualquier lote específico que selecciones en la web de E-Trade.
2. **Conversión de Divisa Oficial (BCE):** Convierte todos los costes de adquisición y precios de venta a EUR utilizando el tipo de cambio oficial del Banco Central Europeo de la fecha exacta de cada operación.
3. **Regla de los 2 Meses (Wash Sales):** Bloquea/difiere las pérdidas patrimoniales en la declaración si compraste o recibiste acciones homogéneas en el periodo de 2 meses anteriores o posteriores a la venta con pérdidas (**Art. 33.5.f LIRPF**).
4. **Ventas Anticipadas de ESPP:** Detecta si vendiste acciones de ESPP antes de cumplir los 3 años de tenencia obligatoria. Si es así, clasifica el descuento de compra como rendimiento del trabajo tributable en el año de adquisición (**Art. 42.3.f LIRPF**).
5. **Deducción de Gastos:** Deduce las comisiones inherentes a las ventas y las SEC fees del total de tus ganancias para reducir tu cuota tributaria (**Art. 35 LIRPF**). Las comisiones bancarias por transferencia (wire fees) se **excluyen** por defecto.

---

## 3. Guía de Inicio Rápido: Cómo Ejecutar la Calculadora

Para utilizar la herramienta, sigue estos sencillos pasos:

### Paso 1: Exportar tus Datos de E-Trade
1. **Historial de Operaciones (Excel):**
   * Ve a E-Trade ➔ **Portfolios** ➔ **Transactions**.
   * Descarga el historial completo en formato Excel, guárdalo como `orders.xlsx` e introdúcelo en la carpeta `input/orders/`. El historial de compras ESPP (`BenefitHistory.xlsx`) va en `input/espp/`.
2. **Confirmaciones de RSU (PDFs):**
   * Ve a E-Trade ➔ **Documents** ➔ **Confirmations**.
   * Descarga las confirmaciones en PDF de todas tus liberaciones de RSU (vesting events) e introdúcelas en la carpeta `input/rsu/`.
3. **Confirmaciones de Ejercicio de Opciones (PDFs, si aplica):**
   * Descarga los PDFs de confirmación de ejercicio y colócalos en `input/options/`.

### Paso 2: Organizar los Archivos
Asegúrate de que la estructura de carpetas de tu proyecto se vea así:
```text
tax-broker-tools-esp/
├── input/
│   ├── espp/
│   │   └── BenefitHistory.xlsx
│   ├── orders/
│   │   └── orders.xlsx
│   ├── rsu/
│   │   ├── rsu_release_1.pdf
│   │   └── rsu_release_2.pdf
│   ├── options/
│   │   └── option_exercise_1.pdf
│   ├── prior_losses.json   # opcional: pérdidas pendientes de antes de tu ventana de datos
│   └── savings_income.json # opcional: dividendos/intereses por año (EUR)
└── ...
```

### Paso 3: Ejecutar el Programa
El proyecto incluye scripts autoejecutables que configuran automáticamente el entorno virtual y las dependencias necesarias. No es necesario instalar paquetes de python de forma manual.

* **En macOS:**
  * Haz doble clic en el archivo [run_tax_engine.command](file:///Users/manu.lopez/tax-broker-tools-esp/run_tax_engine.command) desde el Finder, o ejecuta `./run_tax_engine.command` en tu terminal.
* **En Windows:**
  * Haz doble clic en [run_tax_engine.bat](file:///Users/manu.lopez/tax-broker-tools-esp/run_tax_engine.bat).

---

## 4. Comprensión de los Informes Generados

Cuando finaliza la ejecución, la herramienta genera:

1. **Desglose en Terminal:** cada venta, el cruce FIFO por lotes, las pérdidas bloqueadas (regla de los 2 meses) y los resúmenes anuales.
2. **Informes PDF** en la raíz del proyecto: `tax_report_EN_*.pdf` (referencia) y `tax_report_ES_*.pdf` (con formato y referencias legales para **Hacienda** / tu **Asesor Fiscal**).

El PDF se organiza en estas secciones:

| Sección | Qué te dice |
|---------|-------------|
| **Resumen Fiscal Anual (Modelo 100 – Base del Ahorro)** | Por año: ganancias y pérdidas totales, pérdidas bloqueadas por la regla de los 2 meses, pérdidas deducibles, gastos, la base imponible del ahorro neta y un impuesto estimado *aislado*. Es la cifra principal para tu IRPF. |
| **Libro de Compensación de Pérdidas** | Simula la compensación a 4 años (Art. 49): resultado neto de cada año, pérdidas de años anteriores aplicadas, base tras compensación, y pérdidas pendientes y **caducadas**. |
| **Base del Ahorro – Ganancias + Dividendos/Intereses** | *Solo si aportaste `savings_income.json`.* Combina el resultado bursátil con dividendos/intereses, aplica la compensación cruzada del 25% y muestra la base combinada + la retención en origen. |
| **Guía de Cumplimentación del Modelo 100** | Asigna cada dato a su *apartado* del Modelo 100 (con casillas a verificar por ejercicio). |
| **Análisis del Período de Retención ESPP de 3 Años** | Señala acciones ESPP vendidas antes de 3 años, cuyo descuento de compra pasa a ser rendimiento del trabajo (Art. 42.3.f) y requiere declaración complementaria. |
| **Libro de Transacciones** | El detalle FIFO completo por venta (lotes cruzados, ganancia/pérdida, notas de wash-sale) — la traza justificativa para tu asesor. |

---

## 5. Recordatorios e Imprescindibles

* **Compensación de Pérdidas:** El informe incluye un **Libro de Compensación de Pérdidas** que simula la compensación a 4 años de las pérdidas netas contra ganancias posteriores entre los años analizados (Art. 49 LIRPF) y avisa de las pérdidas que caducan sin usar. Para incorporar pérdidas de *antes* de tu ventana de datos, añade `input/prior_losses.json` (p. ej. `{"2019": 1500, "2020": 300}`) o usa `--prior-losses <archivo>`. La compensación con otras rentas del ahorro (dividendos, intereses) la aplica tu asesor al presentar la declaración.
* **Guía del Modelo 100:** El informe incluye una tabla que asigna cada dato a su *apartado* del Modelo 100. Los números de casilla son orientativos — verifícalos para tu ejercicio.
* **Un Solo Valor (Ticker):** El motor asume que todas las operaciones son sobre el mismo valor (ej. acciones de tu empresa).
* **Modelo 720:** Si el saldo de tus acciones depositadas en el extranjero supera los 50.000 €, deberás presentar la declaración informativa Modelo 720. El programa no genera este modelo.
