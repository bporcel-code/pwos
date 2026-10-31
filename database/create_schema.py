"""
PWOS — Personal Wealth Operating System
create_schema.py — Schema v2.1

Cambios respecto de v2 (ver bitácora Sprint 1):
- `operaciones_activos` ahora tiene `contenedor_id`: una tenencia
  pertenece a un bróker/contenedor, NO a una cuenta ficticia. Esto
  refleja cómo funcionan realmente los brokers (ej. una cuenta en
  pesos, cuenta en dólares, y tenencias como concepto aparte).
- Nueva tabla `flujos_esperados`: reemplaza el Excel de "flujo de
  pagos" + tabla dinámica. Cada fila es un cobro esperado a futuro
  (cupón, amortización, dividendo, alquiler) de cualquier activo.

Filosofía (ADR-003): el sistema modela MOVIMIENTOS, no fotos.
Saldos de cuenta y posiciones de activos NUNCA se cargan a mano:
siempre se calculan a partir de la tabla `movimientos` (y su extensión
`operaciones_activos`).

Unidad de cuenta (ADR-001): USD MEP, modelado como activo (ticker
'USD_MEP', tipo 'fx') con su historial en `precios`.

Uso:
    python create_schema.py            # crea la DB si no existe
    python create_schema.py --reset    # borra y recrea todo (DEV)
"""

import argparse
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "pwos.db"

TABLES = [
    (
        "contenedores",
        """
        CREATE TABLE contenedores (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre      TEXT NOT NULL UNIQUE,
            tipo        TEXT NOT NULL CHECK (tipo IN (
                            'banco', 'broker', 'billetera_virtual',
                            'inmueble', 'otro'
                        )),
            activo      INTEGER NOT NULL DEFAULT 1,
            notas       TEXT,
            creado_en   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            -- v2.50 (ADR-032): parte del contenedor que es del usuario. Los
            -- movimientos van al 100%; se aplica al valuar.
            pct_propiedad REAL NOT NULL DEFAULT 100
                        CHECK (pct_propiedad > 0 AND pct_propiedad <= 100)
        );
        """,
        "Reemplaza el concepto rígido de 'brokers'. Agregar uno nuevo "
        "es un INSERT, no un cambio de código.",
    ),
    (
        "cuentas",
        """
       CREATE TABLE cuentas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    contenedor_id   INTEGER NOT NULL,
    moneda          TEXT NOT NULL,
    estado          TEXT NOT NULL DEFAULT 'disponible'
                        CHECK (estado IN ('disponible', 'caucionado', 'plazo_fijo')),
    alias           TEXT,
    activa          INTEGER NOT NULL DEFAULT 1,
    creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (contenedor_id) REFERENCES contenedores (id)
        ON DELETE RESTRICT,
    UNIQUE (contenedor_id, moneda, estado)
);
        """,
        "Subdivisión de EFECTIVO por moneda dentro de un contenedor "
        "(ej: cuenta en pesos, cuenta en dólares). Ya NO se usa para "
        "modelar tenencias — eso ahora es responsabilidad directa del "
        "contenedor vía `operaciones_activos.contenedor_id`.",
    ),
    (
        "categorias",
        """
        CREATE TABLE categorias (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre              TEXT NOT NULL,
            categoria_padre_id  INTEGER,
            tipo                TEXT CHECK (tipo IN ('ingreso', 'egreso', 'neutro')),
            FOREIGN KEY (categoria_padre_id) REFERENCES categorias (id)
                ON DELETE SET NULL,
            UNIQUE (nombre, categoria_padre_id)
        );
        """,
        "Árbol autorreferencial de clasificación de gastos/ingresos.",
    ),
    (
        "activos",
        """
        CREATE TABLE activos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL UNIQUE,
            nombre          TEXT,
            tipo            TEXT NOT NULL CHECK (tipo IN (
                                'accion', 'cedear', 'bono_soberano',
                                'bono_provincial', 'on', 'lecap',
                                'bono_cer', 'cripto', 'inmueble', 'fx', 'fci'
                            )),
            moneda_nominal  TEXT NOT NULL,
            creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "Catálogo de instrumentos, incluido USD_MEP (tipo 'fx') y tu "
        "depto alquilado (tipo 'inmueble') — así el mismo mecanismo de "
        "flujos_esperados sirve para cupones Y para alquiler.",
    ),
    (
        "precios",
        """
        CREATE TABLE precios (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            activo_id   INTEGER NOT NULL,
            fecha       DATE NOT NULL,
            precio      REAL NOT NULL,
            moneda      TEXT NOT NULL,
            fuente      TEXT,
            FOREIGN KEY (activo_id) REFERENCES activos (id)
                ON DELETE RESTRICT,
            UNIQUE (activo_id, fecha)
        );
        """,
        "Historial de cotizaciones, incluida la cotización MEP diaria.",
    ),
    (
        "movimientos",
        """
        CREATE TABLE movimientos (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha               DATE NOT NULL,
            tipo                TEXT NOT NULL CHECK (tipo IN (
                                    'ingreso', 'egreso', 'transferencia',
                                    'compra_activo', 'venta_activo', 'renta'
                                )),
            cuenta_origen_id    INTEGER,
            cuenta_destino_id   INTEGER,
            monto               REAL NOT NULL CHECK (monto >= 0),
            moneda              TEXT NOT NULL,
            categoria_id        INTEGER,
            descripcion         TEXT,
            naturaleza          TEXT NOT NULL DEFAULT 'efectivo'
                                    CHECK (naturaleza IN ('efectivo', 'en_especie')),
            creado_en           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            -- v2.4 (ADR-012, migrar_v24_identificador_externo.py): ID real del
            -- broker o hash de contenido, para deduplicar en la carga incremental.
            identificador_externo TEXT,
            FOREIGN KEY (cuenta_origen_id) REFERENCES cuentas (id)
                ON DELETE RESTRICT,
            FOREIGN KEY (cuenta_destino_id) REFERENCES cuentas (id)
                ON DELETE RESTRICT,
            FOREIGN KEY (categoria_id) REFERENCES categorias (id)
                ON DELETE SET NULL,
            CHECK (
                naturaleza = 'en_especie'
                OR cuenta_origen_id IS NOT NULL
                OR cuenta_destino_id IS NOT NULL
            )
        );
        """,
        "TABLA CENTRAL. En compra_activo, cuenta_origen_id es la cuenta "
        "de efectivo que paga (cuenta_destino_id queda NULL — la "
        "tenencia no vive en una cuenta). En venta_activo, es al revés. "
        "ADR-010: movimientos con naturaleza='en_especie' (transferencia "
        "de títulos entre brokers, dividendo en especie) no mueven "
        "efectivo — monto puede ser 0 y ambas cuentas pueden ser NULL.",
    ),
    (
        "operaciones_activos",
        """
        CREATE TABLE operaciones_activos (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            movimiento_id       INTEGER NOT NULL UNIQUE,
            activo_id           INTEGER NOT NULL,
            contenedor_id       INTEGER NOT NULL,
            cantidad            REAL NOT NULL CHECK (cantidad > 0),
            precio_unitario     REAL NOT NULL CHECK (precio_unitario > 0),
            moneda              TEXT NOT NULL,
            comision            REAL NOT NULL DEFAULT 0,
            FOREIGN KEY (movimiento_id) REFERENCES movimientos (id)
                ON DELETE CASCADE,
            FOREIGN KEY (activo_id) REFERENCES activos (id)
                ON DELETE RESTRICT,
            FOREIGN KEY (contenedor_id) REFERENCES contenedores (id)
                ON DELETE RESTRICT
        );
        """,
        "Extensión 1:1 de movimientos. `contenedor_id` es la corrección "
        "clave: la tenencia pertenece al bróker directamente, igual que "
        "en la plataforma real (el broker), sin cuenta inventada.",
    ),
    (
        "flujos_esperados",
        """
        CREATE TABLE flujos_esperados (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            activo_id           INTEGER,
            propiedad_id        INTEGER,
            contenedor_id       INTEGER,
            tipo_flujo          TEXT NOT NULL CHECK (tipo_flujo IN (
                                    'cupon', 'amortizacion', 'dividendo',
                                    'alquiler', 'otro'
                                )),
            fecha_estimada      DATE NOT NULL,
            monto_estimado      REAL NOT NULL CHECK (monto_estimado > 0),
            moneda              TEXT NOT NULL,
            monto_estimado_usd  REAL,
            origen_estimado     TEXT NOT NULL DEFAULT 'importador' CHECK (origen_estimado IN (
                                    'importador', 'cronograma', 'yfinance', 'alquiler', 'manual'
                                )),
            estimado_en         TIMESTAMP,
            estado              TEXT NOT NULL DEFAULT 'pendiente' CHECK (estado IN (
                                    'pendiente', 'cobrado', 'no_cobrado'
                                )),
            movimiento_id       INTEGER,
            notas               TEXT,
            creado_en           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK ((activo_id IS NULL) <> (propiedad_id IS NULL)),
            FOREIGN KEY (activo_id) REFERENCES activos (id) ON DELETE RESTRICT,
            FOREIGN KEY (propiedad_id) REFERENCES propiedades (id) ON DELETE RESTRICT,
            FOREIGN KEY (contenedor_id) REFERENCES contenedores (id) ON DELETE RESTRICT,
            FOREIGN KEY (movimiento_id) REFERENCES movimientos (id) ON DELETE SET NULL
        );
        """,
        "Reemplaza el Excel de flujo de pagos + tabla dinámica. Cada "
        "fila es UN cobro esperado. Cuando se cobra de verdad, "
        "se linkea a un movimiento real (tipo 'renta') y pasa a "
        "estado='cobrado'. Responde la Pregunta 4: ¿qué me genera ingresos? "
        "v2.45 (2026-10-05): registro de ESTIMADOS -- flujo_estimado.py "
        "genera los cobros de bonos (por contenedor) y alquileres "
        "(`propiedad_id`, contrato en `propiedades.alquiler_*`), se "
        "recalculan mientras su fecha es futura y quedan fijos despues, "
        "para comparar estimado vs. real. `origen_estimado='importador'` = "
        "filas viejas de un importador, cargadas con el monto real.",
    ),
    (
        "cierres_mensuales",
        """
        CREATE TABLE cierres_mensuales (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            anio                    INTEGER NOT NULL,
            mes                     INTEGER NOT NULL CHECK (mes BETWEEN 1 AND 12),
            patrimonio_total_usd    REAL NOT NULL,
            aportes_netos_usd       REAL NOT NULL DEFAULT 0,
            rendimiento_usd         REAL NOT NULL DEFAULT 0,
            detalle_json            TEXT,
            generado_en             TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (anio, mes)
        );
        """,
        "Foto calculada automáticamente al cierre de cada mes.",
    ),
    (
        "bonos_referencia",
        """
        CREATE TABLE bonos_referencia (
            activo_id           INTEGER PRIMARY KEY,
            nombre              TEXT,
            tipo_instrumento    TEXT,
            legislacion         TEXT,
            es_dolar            INTEGER,
            fuente              TEXT NOT NULL DEFAULT 'rava',
            actualizado_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            -- v2.6 (migrar_v26_moneda_pago_bonos.py): moneda en que paga el bono.
            moneda_pago         TEXT,
            FOREIGN KEY (activo_id) REFERENCES activos (id)
                ON DELETE CASCADE
        );
        """,
        "Datos de referencia estáticos por bono (legislación, tipo de "
        "instrumento) para el scorecard de riesgo/diversificación de "
        "renta fija. Fuente primaria: scraping de Rava "
        "(database/rava_client.py) -- 'fuente' distingue de una futura "
        "carga manual si algún ticker no tiene cobertura en Rava.",
    ),
    (
        "cronograma_pagos_bonos",
        """
        CREATE TABLE cronograma_pagos_bonos (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            activo_id       INTEGER NOT NULL,
            fecha_pago      DATE NOT NULL,
            tipo_pago       TEXT NOT NULL CHECK (tipo_pago IN (
                                'interes', 'amortizacion'
                            )),
            monto_por_vn    REAL NOT NULL,
            fuente          TEXT NOT NULL DEFAULT 'rava',
            actualizado_en  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (activo_id, fecha_pago, tipo_pago),
            FOREIGN KEY (activo_id) REFERENCES activos (id)
                ON DELETE CASCADE
        );
        """,
        "Cronograma de pagos (pasado + futuro) por bono, por VN 100 -- "
        "una fila por evento de pago (interés/amortización), no una "
        "fila por fecha, para poder sumar/filtrar por tipo sin parsear "
        "(ADR-003: eventos, no snapshots, mismo criterio que "
        "flujos_esperados). Insumo crudo para el calculo de duration "
        "del scorecard de riesgo de renta fija (fuera de alcance de "
        "esta tabla en si).",
    ),
    (
        "factores_riesgo",
        """
        CREATE TABLE factores_riesgo (
            activo_id           INTEGER PRIMARY KEY,
            beta                REAL,
            deuda_patrimonio    REAL,
            fuente              TEXT NOT NULL DEFAULT 'yfinance',
            actualizado_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (activo_id) REFERENCES activos (id)
                ON DELETE CASCADE
        );
        """,
        "Factores de riesgo obtenidos de yfinance (beta para acciones/"
        "CEDEARs, Deuda/Patrimonio para ONs sin rating formal) -- "
        "insumo del scorecard de riesgo de cartera (database/riesgo.py, "
        "ver ESTADO_PWOS.md seccion 'Metodologia de riesgo de cartera'). "
        "Rating de soberanos/provinciales y legislacion/tipo de "
        "instrumento NO viven aca (son constantes o ya estan en "
        "bonos_referencia) -- esta tabla es solo lo que requiere una "
        "consulta de red, para no pegarle a yfinance en cada carga del "
        "dashboard.",
    ),
    (
        "benchmarks_macro",
        """
        CREATE TABLE benchmarks_macro (
            clave           TEXT PRIMARY KEY,
            valor_pct       REAL,
            fecha_dato      DATE NOT NULL,
            fuente          TEXT NOT NULL,
            detalle_json    TEXT,
            actualizado_en  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "Benchmarks macro externos (2026-08-29; ampliado 2026-09-09 con "
        "REM/CPI EEUU/Fed para el panel macro de las solapas de Analisis) "
        "para comparar contra el YoY de la cartera y contextualizar el "
        "analisis de bonos -- inflacion Argentina interanual y mensual "
        "(INDEC via argentinadatos), devaluacion del peso interanual (USD "
        "MEP, ADR-001), S&P 500 interanual, REM del BCRA (inflacion "
        "esperada / TAMAR / tipo de cambio, xlsx historico oficial), CPI "
        "de EEUU interanual (BLS, serie CUUR0000SA0) y tasa/expectativa de "
        "la Fed (EFFR de la NY Fed + probabilidades de cme-fedwatch). "
        "`valor_pct` = numero de titular; `detalle_json` = contexto extra "
        "(horizonte REM, distribucion de probabilidad de la Fed). "
        "1 sola fila por `clave` (UPSERT, no serie historica -- el "
        "dashboard solo necesita el ultimo valor, no un grafico), mismo "
        "criterio que factores_riesgo: cachear lo que requiere una "
        "consulta de red externa, para no pegarle a la fuente en cada "
        "carga del dashboard. Se puebla corriendo "
        "cargar_benchmarks_macro.py.",
    ),
    (
        "mercado_renta_fija_rava",
        """
        CREATE TABLE mercado_renta_fija_rava (
            ticker              TEXT PRIMARY KEY,
            nombre              TEXT,
            tipo_instrumento    TEXT,
            legislacion         TEXT,
            precio              REAL,
            tir                 REAL,
            duration            REAL,
            dm                  REAL,
            convexity           REAL,
            current_yield       REAL,
            paridad             REAL,
            valor_tecnico       REAL,
            vencimiento         DATE,
            fuente              TEXT NOT NULL DEFAULT 'rava',
            actualizado_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            -- Columnas de enriquecimiento (v2.15):
            --  * descripcion/emisor/isin/moneda_especie: API de un broker
            --    (MarketData/SearchInstrument), datos casi estaticos ->
            --    los escribe enriquecer_mercado_bonos.py (a mano, en la PC,
            --    usa credenciales del broker en Windows Credential Manager).
            --  * precio_limpio_vn100/paridad_calc/current_yield_calc: el
            --    precio limpio/paridad que muestra la tabla "Mercado de
            --    bonos" (Rava da el `precio`/`paridad` del bulk sin
            --    normalizar o en 0). Se recalculan con CADA refresco de
            --    Rava (cargar_mercado_renta_fija_rava.py, n8n cada 30 min)
            --    para no quedar viejos al lado de la TIR -- ver
            --    _metricas_limpias ahi.
            descripcion         TEXT,
            emisor              TEXT,
            isin                TEXT,
            moneda_especie      TEXT,
            precio_limpio_vn100 REAL,
            paridad_calc        REAL,
            current_yield_calc  REAL,
            enriquecido_en      TIMESTAMP
        );
        """,
        "Epica 11 (Herramienta 1 -- rotacion de bonos por TIR), "
        "2026-09-07: TIR + duration de TODO el universo de bonos que "
        "publica Rava (~885 especies), no solo los que el usuario tiene en "
        "cartera -- insumo de la sugerencia de reemplazo al detectar "
        "compresion de TIR. A diferencia de `bonos_referencia`/"
        "`factores_riesgo` (PK activo_id, FK a `activos`), esta tabla NO "
        "tiene FK: la enorme mayoria de estos tickers nunca van a existir "
        "en `activos` (catalogo de lo que el usuario posee o poseyo), asi que "
        "el link es imposible por diseno -- se identifica por `ticker` "
        "libre. `moneda_pago` deliberadamente NO vive aca: clasificarla a "
        "mano (como se hizo para los 22 tickers de `bonos_referencia`, "
        "ver MONEDA_PAGO_BONOS en cargar_bonos_referencia_flujo.py) no es "
        "viable para ~885 tickers -- cualquier consumidor de esta tabla "
        "debe tratar la moneda como no verificada y dejar la decision "
        "final en manos del usuario, nunca auto-ejecutar sobre esto solo. "
        "Se puebla con cargar_mercado_renta_fija_rava.py (campos de Rava + "
        "precio_limpio/paridad_calc/current_yield_calc calculados, n8n cada "
        "30 min) y enriquecer_mercado_bonos.py (descripcion/emisor/isin/"
        "moneda_especie via la API de un broker, a mano en la PC, v2.15 2026-09-09).",
    ),
    (
        "tesis_inversion",
        """
        CREATE TABLE tesis_inversion (
            activo_id           INTEGER PRIMARY KEY,
            motivo              TEXT,
            riesgo              TEXT,
            horizonte           TEXT,
            precio_objetivo     REAL,
            criterio_venta      TEXT,
            peso_objetivo_pct   REAL,
            creado_en           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actualizado_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            cubeta              TEXT CHECK (cubeta IS NULL OR cubeta IN ('valor_puro', 'calidad', 'mayor_riesgo')),
            valor_intrinseco_min     REAL,
            valor_intrinseco_max     REAL,
            margen_seguridad_req_pct REAL,
            proxima_revision    DATE,
            tendencia_secular   TEXT,
            ventaja_competitiva TEXT,
            pre_mortem          TEXT,
            alertas_sumar       TEXT,
            alertas_reducir     TEXT,
            FOREIGN KEY (activo_id) REFERENCES activos (id)
                ON DELETE CASCADE
        );
        """,
        "v2.31 (2026-09-21, rediseño solapa Acciones, Fase 1) agrego "
        "`cubeta` (CHECK: valor_puro/calidad/mayor_riesgo/NULL) y las "
        "columnas de la Ficha de posicion (valor intrinseco, margen "
        "requerido, proxima revision, tesis por secciones, alertas) -- "
        "todas NULL hasta que el usuario las carga. Mapeo con los campos "
        "originales: motivo = tesis en 2 frases, riesgo = riesgos, "
        "criterio_venta = ruptura de tesis con numeros. "
        "Epica 11 (Herramienta 3 -- rebalanceo de Renta Variable), "
        "2026-09-07: registro de tesis de inversion por posicion -- "
        "mismo patron 1:1 que bonos_referencia/factores_riesgo (PK "
        "activo_id, FK a activos), pero esta tabla la completa el usuario a "
        "mano (no viene de ningun feed externo), hacia adelante, a "
        "medida que define el objetivo de cada posicion -- no hay "
        "backfill retroactivo esperado. `peso_objetivo_pct` es SIMETRICO "
        "(decision del usuario 2026-09-07): dispara tanto 'agregar' (si el "
        "peso actual esta por debajo) como 'vender' (si esta por "
        "encima), no es solo un techo. `precio_objetivo` esta en la "
        "moneda nativa de cotizacion del activo (ver `precios.moneda`), "
        "no en USD MEP. Sin fila para un ticker = sin tesis definida "
        "todavia -- el rebalanceo (database/rebalanceo_rv.py) no "
        "adivina un objetivo, lo marca explicito.",
    ),
    (
        "mercado_renta_fija_historico",
        """
        CREATE TABLE mercado_renta_fija_historico (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker          TEXT NOT NULL,
            fecha           DATE NOT NULL,
            categoria       TEXT,
            moneda          TEXT,
            tir_pct         REAL,
            precio_vn100    REAL,
            paridad_pct     REAL,
            duration        REAL,
            carry_pct       REAL,
            bpv_usd         REAL,
            breakeven_pb    REAL,
            en_cartera      INTEGER NOT NULL DEFAULT 0,
            creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            origen          TEXT NOT NULL DEFAULT 'snapshot'
                            CHECK (origen IN ('snapshot', 'copia_dia_sin_rueda')),
            UNIQUE (ticker, fecha)
        );
        """,
        "v2.35 (2026-09-24): columna `origen` ('snapshot' = tomado por el job de las 18:00 o por la puesta al "
        "dia; 'copia_dia_sin_rueda' = copia del ultimo dia habil para un fin de semana/feriado que se perdio: "
        "con el mercado cerrado `mercado_renta_fija_rava` no cambia -- medido, el domingo 09-20 es identico al "
        "viernes 09-18 en 903/903 tickers). v2.17 (2026-09-11): snapshot DIARIO (no cada "
        "30 min) de renta fija -- `mercado_renta_fija_rava` es PK por "
        "ticker (1 fila, se pisa cada refresco) y nunca guardo historia; "
        "esta tabla arranca a acumularla de ahora en adelante, sin "
        "backfill (no hay pasado que recuperar). v2.18 (mismo dia, "
        "el usuario amplio el pedido apenas vio v2.17): pasa de 'solo "
        "cartera' (~22 tickers, PK activo_id con FK) a TODO el universo "
        "de Rava (~890 especies) para poder comparar oportunidades, no "
        "solo monitorear la cartera -- por eso la identidad de fila es "
        "`ticker` (TEXT, SIN FK, mismo patron que `mercado_renta_fija_"
        "rava`: la mayoria de estos tickers nunca existen en `activos`). "
        "`tir_pct`/`precio_vn100` (=`precio_limpio_vn100`)/`paridad_pct` "
        "(=`paridad_calc`)/`duration` salen de `mercado_renta_fija_rava` "
        "para TODO el universo -- no dependen de tener posicion, cero "
        "llamadas nuevas (dato ya cacheado cada 30 min). `carry_pct`/"
        "`bpv_usd`/`breakeven_pb` en cambio SI dependen de VN en "
        "cartera -- quedan NULL fuera de las posiciones "
        "vigentes (decision explicita del usuario: extender Carry a las "
        "~890 hubiera necesitado ~868 llamadas EN VIVO diarias al "
        "endpoint de flujo de Rava, sin precedente a ese volumen -- se "
        "descarto). `en_cartera` marca esas ~22 filas. Se puebla con "
        "cargar_mercado_renta_fija_historico.py (reusa rotacion_bonos() "
        "para el overlay de cartera, sin recalcular nada de eso), "
        "UPSERT sobre (ticker, fecha) -- re-ejecutable el mismo dia sin "
        "duplicar. Uso previsto: comparar evolucion de TIR entre bonos "
        "parecidos, dentro y fuera de cartera.",
    ),
    (
        "tesis_bonos",
        """
        CREATE TABLE tesis_bonos (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker              TEXT,
            fecha               DATE NOT NULL,
            tesis               TEXT NOT NULL,
            riesgos             TEXT,
            conviccion          TEXT CHECK (conviccion IN ('alta', 'media', 'baja')),
            estado              TEXT NOT NULL DEFAULT 'vigente' CHECK (estado IN ('vigente', 'cerrada')),
            reflexion_final     TEXT,
            fecha_reflexion     DATE,
            creado_en           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actualizado_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.19 (2026-09-12, punto 3 de la ronda de "
        "Bonos): bitacora CRONOLOGICA de tesis de inversion, acotada a "
        "la solapa de Bonos por ahora (no el 'diario general' mas amplio "
        "del que se hablo en su momento). 2 tipos en la MISMA tabla, "
        "distinguidos por `ticker`: macro (NULL, tesis de la cartera de "
        "renta fija completa) y micro (NOT NULL, tesis de un bono "
        "puntual) -- mismos campos en ambos casos. `ticker` TEXT SIN FK "
        "a `activos` (a proposito, mismo patron que "
        "`mercado_renta_fija_rava`/`mercado_renta_fija_historico`, v2.17/"
        "v2.18): `activos` solo tiene los tickers que el usuario ALGUNA VEZ "
        "tuvo, pero el espiritu de esta ronda es poder escribir una "
        "tesis sobre un bono que esta EVALUANDO comprar, todavia sin "
        "posicion -- una FK a `activos` lo hubiera bloqueado (primer "
        "diseño de este mismo dia, corregido antes de exponer la API). "
        "Deliberadamente NO se reusa `tesis_inversion` (Renta Variable, "
        "v2.13): esa es 1-fila-por-activo (PK=activo_id, UPSERT, sin "
        "fecha/estado/historial) pensada como 'tesis vigente actual' -- "
        "un concepto distinto al de una bitacora donde el usuario puede "
        "escribir VARIAS tesis a lo largo del tiempo sobre el mismo bono "
        "(investigado y descartado 2026-09-11). Al cerrar o revisar una "
        "tesis se le agrega `reflexion_final` + `fecha_reflexion` -- "
        "UPDATE de la misma fila (la fila ES la tesis, de principio a "
        "fin de su vida), nunca una fila nueva. Se puebla a mano por "
        "el usuario desde el formulario del dashboard, sin feed externo ni "
        "validacion estricta de existencia del ticker (bitacora "
        "personal, no un catalogo -- ver tesis_bonos.py).",
    ),
    (
        "tesis_cartera",
        """
        CREATE TABLE tesis_cartera (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha               DATE NOT NULL,
            tesis               TEXT NOT NULL,
            riesgos             TEXT,
            conviccion          TEXT CHECK (conviccion IN ('alta', 'media', 'baja')),
            estado              TEXT NOT NULL DEFAULT 'vigente' CHECK (estado IN ('vigente', 'cerrada')),
            reflexion_final     TEXT,
            fecha_reflexion     DATE,
            creado_en           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actualizado_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.20 (2026-09-12): bitacora de metas/tesis de "
        "la cartera COMPLETA, visible en la solapa Dashboard -- 'para "
        "plantear metas y objetivos' sin perderlos de vista. Mismos "
        "campos que `tesis_bonos`/`tesis_acciones` pero SIN columna "
        "`ticker`: no hay nocion de micro aca, esa granularidad ya la "
        "cubren las otras 2 bitacoras. Logica compartida en "
        "tesis_bitacora.py, wrapper en tesis_cartera.py.",
    ),
    (
        "tesis_acciones",
        """
        CREATE TABLE tesis_acciones (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker              TEXT,
            fecha               DATE NOT NULL,
            tesis               TEXT NOT NULL,
            riesgos             TEXT,
            conviccion          TEXT CHECK (conviccion IN ('alta', 'media', 'baja')),
            estado              TEXT NOT NULL DEFAULT 'vigente' CHECK (estado IN ('vigente', 'cerrada')),
            reflexion_final     TEXT,
            fecha_reflexion     DATE,
            creado_en           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actualizado_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.20 (2026-09-12): mismo esquema y misma "
        "logica que `tesis_bonos` (macro/micro via `ticker` NULL/NOT "
        "NULL, `ticker` TEXT sin FK a `activos`), aplicado a la solapa "
        "Analisis - Acciones en vez de Bonos. NO reusa `tesis_inversion` "
        "(v2.13, 1-fila-por-activo sin historial, sigue viva para el "
        "rebalanceo) -- mismo motivo que documenta tesis_bonos.py. "
        "Logica compartida en tesis_bitacora.py, wrapper en "
        "tesis_acciones.py.",
    ),
    (
        "tesis_inmuebles",
        """
        CREATE TABLE tesis_inmuebles (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker              TEXT,
            fecha               DATE NOT NULL,
            tesis               TEXT NOT NULL,
            riesgos             TEXT,
            conviccion          TEXT CHECK (conviccion IN ('alta', 'media', 'baja')),
            estado              TEXT NOT NULL DEFAULT 'vigente' CHECK (estado IN ('vigente', 'cerrada')),
            reflexion_final     TEXT,
            fecha_reflexion     DATE,
            contenedor_cobro    TEXT,
            creado_en           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actualizado_en      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.23 (2026-09-15, Epica 15 Parte B): mismo "
        "esquema y misma logica que `tesis_bonos`/`tesis_acciones` "
        "(macro/micro via `ticker` NULL/NOT NULL), aplicado a Real "
        "Estate. `ticker` reusado como identificador CORTO de la "
        "propiedad (ej. 'DEPTO-CENTRO'), texto libre SIN FK -- no hay "
        "catalogo de propiedades separado, cada una es su propio "
        "`contenedor` (tipo='inmueble', ver alta_inmueble.py). Logica "
        "compartida en tesis_bitacora.py, wrapper en tesis_inmuebles.py. "
        "Sin pestana propia todavia -- vive en el Dashboard, al lado de "
        "`tesis_cartera`, hasta que se construya la solapa Real Estate "
        "(Fase 2/3: flujo de renta mensual, indicadores ICC/precio m2). "
        "v2.26 (2026-09-15, Real Estate Fase 2): agrega "
        "`contenedor_cobro` -- nombre del contenedor (ej. 'Banco X') "
        "donde se cobra el alquiler de la propiedad de esta tesis, texto "
        "libre SIN FK (mismo criterio laxo que `ticker`; no toda "
        "propiedad tiene renta activa, y no hay necesidad de integridad "
        "referencial real acá). Manejado aparte en tesis_inmuebles.py, "
        "NO se sumo a tesis_bitacora.py (las otras 3 bitacoras no "
        "tienen este concepto). Ver migrar_v226_contenedor_cobro.py.",
    ),
    (
        "tesis_umbrales",
        """
        CREATE TABLE tesis_umbrales (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            tesis_tabla     TEXT NOT NULL CHECK (tesis_tabla IN ('cartera', 'bonos', 'acciones', 'inmuebles')),
            tesis_id        INTEGER NOT NULL,
            indicador       TEXT NOT NULL,
            operador        TEXT NOT NULL CHECK (operador IN ('mayor_que', 'menor_que')),
            valor           REAL NOT NULL,
            accion          TEXT NOT NULL,
            creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.21 (2026-09-12, Epica 12 Parte 1): umbrales "
        "de monitoreo estructurados sobre una tesis (cualquiera de las "
        "bitacoras) -- convierte 'si el riesgo pais perfora 400pb...' "
        "de texto libre en una fila indicador/operador/valor/accion, "
        "comparable en automatico contra los feeds ya cacheados "
        "(Epica 12 Parte 2). UNA tabla compartida por TODAS las "
        "bitacoras (`tesis_tabla` discriminador + `tesis_id`, SIN FK "
        "real -- SQLite no soporta FK condicional a distintas tablas, "
        "mismo patron que `tesis_bonos.ticker`), no una tabla separada "
        "por bitacora: la Parte 2 necesita mirar los umbrales de todas "
        "juntos. El campo `riesgos` de texto libre de cada tesis NO se "
        "toca, esto es un agregado. CRUD en tesis_umbrales.py. "
        "`tesis_tabla`/`indicador` ampliados en v2.23 (Epica 15 Parte "
        "A/B, 2026-09-15): 'inmuebles' se suma a `tesis_tabla`; "
        "'ust_10y_pct'/'sp500_concentracion_pct'/'em_vs_sp500_ratio'/"
        "'merval' (indicadores globales, yfinance) se suman a "
        "`indicador` -- `tenencia_extranjera` sigue afuera a proposito "
        "(Parte 3 de la Epica 12, sin feed todavia). `indicador` "
        "ampliado de nuevo en v2.24 (Epica 16, 2026-09-15): "
        "'patentamientos_interanual_pct'/'construccion_interanual_pct'/"
        "'empleo_privado_interanual_pct'/'mora_hogares_pct'/"
        "'industria_interanual_pct' (economia real argentina, "
        "cargar_indicadores_economia_real.py). `indicador` ampliado de "
        "nuevo en v2.25 (Epica 16, Real Estate m2, 2026-09-15): "
        "'zpindex_caba_venta_usd_m2'/'zpindex_cordoba_venta_usd_m2'/"
        "'zpindex_rosario_venta_usd_m2' (Zonaprop, cargar_zpindex.py). "
        "v2.39 (2026-09-29, Epica 21): el CHECK de `indicador` se SACO "
        "(migrar_v239_indicadores_macro.py, ultima reconstruccion) -- la "
        "validacion es en Python contra `indicadores_catalogo` "
        "(indicadores_macro.validar_indicador).",
    ),
    (
        "macro_historico",
        """
        CREATE TABLE macro_historico (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            clave       TEXT NOT NULL,
            fecha       DATE NOT NULL,
            valor       REAL NOT NULL,
            creado_en   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actualizado_en TIMESTAMP,
            UNIQUE(clave, fecha)
        );
        """,
        "v2.22 (2026-09-12, Epica 12 Parte 4): serie "
        "historica de los indicadores macro que `benchmarks_macro` solo "
        "guarda como 'ultimo valor'. Agregado, no reemplazo -- el "
        "header/panel macro sigue leyendo de `benchmarks_macro`. "
        "`clave` usa los MISMOS nombres que `tesis_umbrales.indicador` "
        "(riesgo_pais, rem_inflacion_12m, inflacion_mensual, "
        "rem_devaluacion_esperada_12m) para que la Parte 2 pueda "
        "consultar esta tabla directo con el string que ya guarda cada "
        "umbral, sin mapear 2 convenciones de nombres distintas. "
        "`inflacion_interanual` se suma tambien (fuera del enum de "
        "umbrales, solo contexto). `ccl_mep_brecha_pct` NO vive aca -- "
        "se calcula al vuelo desde `USD_MEP`/`USD_CCL` en `precios` "
        "(ver brecha_cambiaria.py). Poblada por "
        "backfill_macro_historico.py (una vez, historia completa) + "
        "append incremental en cada corrida de "
        "cargar_benchmarks_macro.py (INSERT OR IGNORE sobre "
        "(clave, fecha), idempotente). v2.39 (2026-09-29, Epica 21): "
        "reusada como la serie de TODOS los indicadores macro -- `clave` = "
        "`indicadores_catalogo.id`; + `actualizado_en` (NULL hasta que una "
        "revision de la fuente cambia el valor). Se escribe solo con "
        "indicadores_macro.upsert_serie (ON CONFLICT DO UPDATE; un NaN nunca "
        "pisa; cada carga informa filas nuevas/actualizadas).",
    ),
    (
        "propiedades",
        """
        CREATE TABLE propiedades (
            id                              INTEGER PRIMARY KEY AUTOINCREMENT,
            alias                           TEXT NOT NULL UNIQUE,
            tipo                            TEXT NOT NULL CHECK (tipo IN ('activo_fisico', 'fondo_re')),
            pct_propiedad                   REAL NOT NULL CHECK (pct_propiedad > 0 AND pct_propiedad <= 100),
            fecha_alta                      DATE NOT NULL,
            fecha_disponibilidad_capital    DATE,
            notas                           TEXT,
            creado_en                       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actualizado_en                  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            inversion_inicial_usd           REAL CHECK (inversion_inicial_usd >= 0),
            -- v2.45: contrato de alquiler vigente (porcion neta del usuario), lo usa flujo_estimado.py
            alquiler_monto                  REAL CHECK (alquiler_monto IS NULL OR alquiler_monto > 0),
            alquiler_moneda                 TEXT CHECK (alquiler_moneda IS NULL OR alquiler_moneda IN ('ARS', 'USD')),
            alquiler_dia_cobro              INTEGER CHECK (alquiler_dia_cobro IS NULL OR alquiler_dia_cobro BETWEEN 1 AND 31),
            alquiler_contenedor_id          INTEGER REFERENCES contenedores (id),
            alquiler_desde                  DATE,
            CHECK (tipo = 'fondo_re' OR fecha_disponibilidad_capital IS NULL)
        );
        """,
        "v2.27 (2026-09-17, Real Estate -- cerrar la brecha de flujo/"
        "valuacion): catalogo de propiedades/fondos. "
        "Cada propiedad NO tiene contenedor propio (decision ya tomada, "
        "ver ESTADO_PWOS.md -- el alquiler va a un contenedor ya "
        "existente, ej. 'Banco X'). `tipo` distingue un activo fisico "
        "(depto, casa) de un fondo inmobiliario; "
        "`fecha_disponibilidad_capital` (vencimiento/lock-up del "
        "capital) solo tiene sentido para un fondo, bloqueado por CHECK "
        "para 'activo_fisico'. `notas` es el lugar para datos de "
        "catastro/construccion del lote -- decision explicita del usuario "
        "de NO armar una tabla aparte para eso. CRUD en propiedades.py. "
        "v2.29 (2026-09-19) agrego "
        "`inversion_inicial_usd` -- capital propio puesto para adquirir/"
        "construir la propiedad (NULL a proposito si esta 100% "
        "financiada por un pasivo -- no se inventa un "
        "0). Reusa `fecha_alta` como fecha de ese flujo en "
        "flujo_inmueble.py, sin columna de fecha nueva. CONVENCION "
        "(fix 2026-09-19, una primera carga la violo sin "
        "querer): SIEMPRE la porcion NETA del usuario, nunca el costo "
        "total de la propiedad -- misma convencion que `alquileres."
        "monto`/`pasivos.monto_original_usd`. Ver propiedades.py::"
        "actualizar_inversion_inicial.",
    ),
    (
        "alquileres",
        """
        CREATE TABLE alquileres (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            propiedad_id    INTEGER NOT NULL REFERENCES propiedades (id) ON DELETE RESTRICT,
            periodo         TEXT NOT NULL CHECK (periodo GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
            monto           REAL NOT NULL CHECK (monto >= 0),
            moneda          TEXT NOT NULL,
            monto_usd       REAL NOT NULL,
            fecha_cobro     DATE NOT NULL,
            creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (propiedad_id, periodo)
        );
        """,
        "v2.27 (2026-09-17, Real Estate): alquiler "
        "cobrado por propiedad y periodo. Convencion IMPORTANTE: "
        "`monto`/`moneda` es SIEMPRE la porcion neta segun "
        "`pct_propiedad` de esa propiedad (ej. el 50% de una propiedad "
        "compartida, nunca el 100% del alquiler). `monto_usd` se "
        "fija al MEP del momento de la CARGA (no el historico de "
        "`fecha_cobro` como hace `calendario_flujo.py::otros_usd`) -- "
        "una vez cargado, no se recalcula. `UNIQUE(propiedad_id, "
        "periodo)` evita cargar el mismo mes 2 veces por error -- "
        "corregir un monto ya cargado es un UPDATE, no un INSERT nuevo. "
        "Sumado a /api/calendario-flujo como bucket propio "
        "`alquileres_usd`, separado de `otros_usd` (interes bancario). "
        "CRUD en alquileres.py.",
    ),
    (
        "valuaciones",
        """
        CREATE TABLE valuaciones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            propiedad_id    INTEGER NOT NULL REFERENCES propiedades (id) ON DELETE RESTRICT,
            fecha           DATE NOT NULL,
            valor_usd       REAL NOT NULL CHECK (valor_usd >= 0),
            fuente          TEXT,
            creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (propiedad_id, fecha)
        );
        """,
        "v2.28 (2026-09-18, Real Estate -- valuacion/deuda/TIR, pedido "
        "del usuario): historial de tasaciones de una propiedad, SIEMPRE al "
        "100% del activo (nunca la parte propia -- `pct_propiedad` se "
        "aplica al leer). El valor vigente es la fila mas reciente por "
        "`fecha`; nunca se pisa una anterior. `fuente` (texto libre) "
        "guarda de donde salio el numero (tasacion, acuerdo de partes, "
        "saldo de cuenta de un fondo). `UNIQUE(propiedad_id, fecha)` "
        "evita cargar 2 veces el mismo dia por error -- corregir una ya "
        "cargada es un UPDATE, no un INSERT nuevo. IMPORTANTE: solo las "
        "propiedades con `tipo='activo_fisico'` se suman a "
        "patrimonio_total (ver dashboard.py/api.py) -- un `fondo_re` "
        "como un fondo inmobiliario ya esta contado via su propia cuenta de "
        "efectivo (script de valuacion manual del fondo inmobiliario), sumar tambien su "
        "valuacion aca duplicaria esa plata. CRUD en valuaciones.py.",
    ),
    (
        "pasivos",
        """
        CREATE TABLE pasivos (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            alias               TEXT NOT NULL UNIQUE,
            monto_original_usd  REAL NOT NULL CHECK (monto_original_usd > 0),
            propiedad_id        INTEGER REFERENCES propiedades (id) ON DELETE RESTRICT,
            fecha_alta          DATE NOT NULL,
            creado_en           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.28 (2026-09-18, Real Estate): deuda puntual "
        "(ej. la que financia la compra de una propiedad) -- NO un sistema "
        "general de creditos/hipotecas/tarjetas, fuera de alcance a "
        "proposito. `monto_original_usd` es ya la parte propia (misma "
        "convencion que `alquileres.monto`). `propiedad_id` es OPCIONAL "
        "-- un pasivo no necesariamente esta atado a una propiedad. Su "
        "saldo vigente se resta de patrimonio_total en todo momento "
        "(no solo cuando termina de pagarse). CRUD en pasivos.py.",
    ),
    (
        "pagos_pasivo",
        """
        CREATE TABLE pagos_pasivo (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            pasivo_id   INTEGER NOT NULL REFERENCES pasivos (id) ON DELETE RESTRICT,
            fecha       DATE NOT NULL,
            monto_usd   REAL NOT NULL CHECK (monto_usd > 0),
            creado_en   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.28 (2026-09-18, Real Estate): cada cuota "
        "pagada de un pasivo, cargada el dia que se paga, SIN "
        "cronograma previo -- ledger de eventos como `movimientos`, sin "
        "UNIQUE (2 pagos el mismo dia son 2 filas legitimas). Saldo de "
        "un pasivo = `monto_original_usd` menos la suma de sus "
        "`pagos_pasivo` (calculado al leer, nunca guardado). CRUD en "
        "pasivos.py.",
    ),
    (
        "gastos_propiedad",
        """
        CREATE TABLE gastos_propiedad (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            propiedad_id    INTEGER NOT NULL REFERENCES propiedades (id) ON DELETE RESTRICT,
            fecha           DATE NOT NULL,
            tipo            TEXT NOT NULL CHECK (tipo IN ('mantenimiento', 'impuesto')),
            monto_usd       REAL NOT NULL CHECK (monto_usd > 0),
            concepto        TEXT,
            creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.30 (2026-09-19, Real Estate): gastos "
        "reales de una propiedad "
        "(mantenimiento, impuesto) -- flujo negativo en flujo_inmueble.py, "
        "mismo patron que `pagos_pasivo`. Sin UNIQUE (ledger de eventos, "
        "2 gastos el mismo dia son 2 filas legitimas). `monto_usd` ya "
        "neto segun `pct_propiedad`, misma convencion que `alquileres."
        "monto`/`pasivos.monto_original_usd`. `tipo` acotado a los 2 "
        "conceptos identificados hasta ahora -- ampliable con una "
        "migracion nueva si aparece un tercero.",
    ),
    (
        "parametros_rv",
        """
        CREATE TABLE parametros_rv (
            clave        TEXT PRIMARY KEY,
            valor        REAL,
            descripcion  TEXT NOT NULL
        );
        """,
        "v2.31 (2026-09-21, rediseño solapa Acciones, Fase 1): solo los "
        "umbrales que el usuario calibra seguido desde la pantalla (topes de "
        "posicion/sector, banda de rebalanceo, objetivos de cubeta). El "
        "resto de las reglas sigue como constante documentada en su "
        "modulo. `valor` admite NULL (peso_objetivo_rv_patrimonio_pct "
        "hasta que el usuario lo defina). La siembra la hace la migracion "
        "(migrar_v231_tesis_rv_cubetas.py) con INSERT OR IGNORE; "
        "parametros_rv.py cae a defaults en codigo si la tabla esta vacia. "
        "v2.36 (2026-09-24): + fila `umbral_cedear_solo_usd_pct` (1.2, "
        "caso 'solo dolares' de cedear_moneda.py), sembrada por "
        "migrar_v236_umbral_cedear.py. v2.38 (2026-09-25): + fila "
        "`desvio_cedear_alerta_pct` (2.0, badge de cedear_caro_barato.py), "
        "sembrada por migrar_v238_desvio_cedear.py.",
    ),
    (
        "tesis_indicadores",
        """
        CREATE TABLE tesis_indicadores (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            activo_id       INTEGER NOT NULL,
            nombre          TEXT NOT NULL CHECK (length(trim(nombre)) > 0),
            fuente          TEXT,
            frecuencia      TEXT,
            direccion       TEXT NOT NULL CHECK (direccion IN ('mayor_es_mejor', 'menor_es_mejor')),
            umbral_verde    REAL NOT NULL,
            umbral_rojo     REAL NOT NULL,
            activo          INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            actualizado_en  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (activo_id, nombre),
            CHECK (
                (direccion = 'mayor_es_mejor' AND umbral_verde > umbral_rojo)
                OR (direccion = 'menor_es_mejor' AND umbral_verde < umbral_rojo)
            ),
            FOREIGN KEY (activo_id) REFERENCES activos (id) ON DELETE CASCADE
        );
        """,
        "v2.32 (2026-09-21, rediseño solapa Acciones, Fase 2): 'que tiene "
        "que seguir siendo verdad' de cada posicion de RV -- 3 a 5 "
        "indicadores por posicion (guia, no se fuerza en la base), con "
        "umbral verde y rojo (el amarillo es el tramo intermedio). "
        "`direccion` y la coherencia de los umbrales con la direccion van "
        "con CHECK (umbrales invertidos darian semaforos al reves en "
        "silencio). FK a `activos` con ON DELETE CASCADE. Semaforo de la "
        "posicion = el peor estado entre sus indicadores activos (gris si "
        "no tiene). CRUD en indicadores_rv.py.",
    ),
    (
        "tesis_indicadores_historial",
        """
        CREATE TABLE tesis_indicadores_historial (
            indicador_id    INTEGER NOT NULL,
            fecha           DATE NOT NULL,
            valor           REAL NOT NULL,
            estado          TEXT NOT NULL CHECK (estado IN ('verde', 'amarillo', 'rojo')),
            creado_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (indicador_id, fecha),
            FOREIGN KEY (indicador_id) REFERENCES tesis_indicadores (id) ON DELETE CASCADE
        );
        """,
        "v2.32 (2026-09-21, rediseño solapa Acciones, Fase 2): lecturas "
        "de cada indicador, cargadas a mano por el usuario. UNA lectura por "
        "indicador y fecha (PK): cargar la misma fecha corrige la anterior. "
        "`estado` se calcula al guardar y se recalcula si se editan los "
        "umbrales del indicador (lo que queda como registro es el `valor` "
        "crudo). 'Amarillo 2 periodos seguidos' = las 2 ultimas lecturas "
        "del indicador, no un plazo de calendario.",
    ),
    (
        "fundamentales_rv",
        """
        CREATE TABLE fundamentales_rv (
            ticker                        TEXT PRIMARY KEY CHECK (length(trim(ticker)) > 0),
            simbolo_yahoo                 TEXT NOT NULL,
            nombre                        TEXT,
            tipo_instrumento              TEXT CHECK (tipo_instrumento IS NULL OR tipo_instrumento IN ('empresa', 'etf', 'otro')),
            fecha_dato                    DATE,
            fuente                        TEXT,
            sector                        TEXT,
            industria                     TEXT,
            moneda_cotizacion             TEXT,
            moneda_estados                TEXT,
            precio_subyacente_usd         REAL,
            per                           REAL,
            per_forward                   REAL,
            pb                            REAL,
            current_ratio                 REAL,
            deuda_neta_ebitda             REAL,
            cobertura_intereses           REAL,
            roic                          REAL,
            fcf_yield                     REAL,
            eps_ttm                       REAL,
            bvps                          REAL,
            anios_dividendo_sin_recorte   INTEGER CHECK (anios_dividendo_sin_recorte IS NULL OR anios_dividendo_sin_recorte >= 0),
            anios_estados                 INTEGER CHECK (anios_estados IS NULL OR anios_estados >= 0),
            series_json                   TEXT CHECK (series_json IS NULL OR json_valid(series_json)),
            campos_no_verificados         TEXT CHECK (campos_no_verificados IS NULL OR json_valid(campos_no_verificados)),
            actualizado_en                TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.33 (2026-09-21, rediseño solapa Acciones, Fase 3): UNA fila por "
        "ticker (la ultima carga), poblada por cargar_fundamentales_rv.py "
        "desde yfinance. Toda columna de dato admite NULL: NULL = 'sin "
        "verificar', nunca un valor inventado; el motivo de cada campo no "
        "calculable va en `campos_no_verificados` (JSON). `ticker` = el de "
        "`activos` para posiciones de cartera, el simbolo de Yahoo tal cual "
        "para candidatas; `simbolo_yahoo` = lo que realmente se pidio (las "
        "acciones locales llevan .BA). `tipo_instrumento`: un ETF es 'etf' "
        "= 'no aplica', distinto de 'sin verificar'. `moneda_cotizacion`/"
        "`moneda_estados`: guarda de moneda (una accion local puede cotizar "
        "en ARS y reportar en USD). `series_json`: series anuales CRUDAS "
        "(utilidad neta, FCF, margen operativo, dividendos por año) de las "
        "que el screener deriva las compuertas -- el dato crudo queda como "
        "registro y ajustar una definicion borrador no requiere migracion.",
    ),
    (
        "watchlist_rv",
        """
        CREATE TABLE watchlist_rv (
            ticker            TEXT PRIMARY KEY CHECK (length(trim(ticker)) > 0),
            nombre            TEXT,
            cubeta_sugerida   TEXT CHECK (cubeta_sugerida IS NULL OR cubeta_sugerida IN ('valor_puro', 'calidad', 'mayor_riesgo')),
            notas             TEXT,
            agregada_en       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ratio_cedear      REAL CHECK (ratio_cedear IS NULL OR ratio_cedear > 0)
        );
        """,
        "v2.33 (2026-09-21, Fase 3): candidatas a comprar que NO estan en "
        "cartera. `ticker` = simbolo de Yahoo tal cual ('BKNG', "
        "'YPFD.BA'). `ratio_cedear` NULL a proposito: hoy no existe ese "
        "dato en ninguna parte del schema (queda 'sin verificar').",
    ),
    (
        "precios_candidatas",
        """
        CREATE TABLE precios_candidatas (
            ticker          TEXT NOT NULL CHECK (length(trim(ticker)) > 0),
            fecha           DATE NOT NULL,
            cierre_usd      REAL NOT NULL CHECK (cierre_usd > 0),
            actualizado_en  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (ticker, fecha)
        );
        """,
        "v2.33 (2026-09-21, Fase 3). Cierre diario en USD, precio de cierre "
        "AJUSTADO por dividendos (no el crudo que usa el resto del "
        "proyecto para valorizar posiciones -- aca se calculan retornos, "
        "no valor de cartera, y con varios pagadores de dividendo grandes "
        "en la cartera un precio sin ajustar mostraria una caida falsa "
        "cada fecha ex-dividendo). Sin FK a `watchlist_rv`: una candidata "
        "se puede simular sin haberla agregado a la watchlist. **Uso "
        "ampliado en la Fase 4 (2026-09-22), sin cambio de schema:** "
        "tambien cachea el precio del SUBYACENTE en USD de los CEDEARs "
        "que ya estan en cartera (ticker = su simbolo de Yahoo, ej. "
        "'MELI', 'PEP' -- el mismo namespace que las candidatas, sin "
        "colision porque una candidata activa nunca es ya una posicion "
        "abierta, ver watchlist_rv.agregar). Ese dato no existe en "
        "`precios` (que guarda el precio del CEDEAR en pesos de BYMA, no "
        "el del subyacente) -- lo puebla `cargar_precios_rv.py`. Acciones "
        "locales de cartera NO entran aca: su historial en ARS ya esta en "
        "`precios` desde 2023, se convierte a USD con el MEP historico al "
        "vuelo, sin necesitar cache.",
    ),
    (
        "simulaciones_compra_rv",
        """
        CREATE TABLE simulaciones_compra_rv (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha           TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            ticker          TEXT NOT NULL CHECK (length(trim(ticker)) > 0),
            monto_usd       REAL NOT NULL CHECK (monto_usd > 0),
            fuente_fondos   TEXT NOT NULL CHECK (length(trim(fuente_fondos)) > 0),
            resultado_json  TEXT NOT NULL CHECK (json_valid(resultado_json))
        );
        """,
        "v2.34 (2026-09-22, Fase 4): guardado EXPLICITO del simulador de "
        "compra -- `POST /api/simular-compra-rv` nunca escribe aca por si "
        "solo, solo con `guardar: true` en el body. `ticker` sin FK "
        "(puede ser una candidata que nunca llego a la watchlist). "
        "`fuente_fondos` texto libre ('aporte_nuevo' o el ticker vendido), "
        "validado en simulador_rv.py, no con un CHECK (el segundo caso es "
        "un ticker variable, no un enum fijo).",
    ),
    (
        "puesta_al_dia_log",
        """
        CREATE TABLE puesta_al_dia_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ejecucion   TEXT NOT NULL,
            tarea       TEXT NOT NULL,
            accion      TEXT NOT NULL CHECK (accion IN
                            ('relleno', 'copia', 'sin_rueda', 'no_recuperable', 'reejecutado', 'alerta', 'error')),
            fecha_dato  DATE,
            filas       INTEGER NOT NULL DEFAULT 0,
            fuente      TEXT,
            detalle     TEXT,
            creado_en   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.35 (2026-09-24): bitacora de `puesta_al_dia.py` (recupera lo que los jobs de n8n no pudieron "
        "cargar por PC apagada/suspendida). Una fila por hecho: 'relleno' (filas insertadas y de que fuente), "
        "'copia' (snapshot copiado de un dia sin mercado), 'sin_rueda' (fecha que la fuente confirmo sin "
        "datos -- feriado; no se vuelve a consultar), 'no_recuperable' (dia que ya no se puede reconstruir "
        "fielmente), 'reejecutado' (job completo vuelto a correr por estar vencido), 'alerta' (aviso ya "
        "emitido, para no repetirlo mas de 1 vez por dia) y 'error'. `ejecucion` agrupa las filas de una corrida.",
    ),
    (
        "ratios_cedear",
        """
        CREATE TABLE ratios_cedear (
            ticker_byma       TEXT PRIMARY KEY CHECK (length(trim(ticker_byma)) > 0),
            ticker_origen     TEXT NOT NULL CHECK (length(trim(ticker_origen)) > 0),
            nombre            TEXT,
            mercado_origen    TEXT,
            ratio_cedear      REAL NOT NULL CHECK (ratio_cedear > 0),
            ratio_subyacente  REAL NOT NULL CHECK (ratio_subyacente > 0),
            ratio             REAL NOT NULL CHECK (ratio > 0),
            isin_cedear       TEXT,
            isin_subyacente   TEXT,
            tipo              TEXT CHECK (tipo IS NULL OR tipo IN ('accion', 'adr', 'etf', 'otro')),
            pais              TEXT,
            industria         TEXT,
            emisor            TEXT CHECK (emisor IS NULL OR emisor IN ('comafi', 'caja_valores')),
            nota              TEXT,
            fuente_ratio      TEXT NOT NULL,
            fecha_lista       DATE NOT NULL,
            actualizado_en    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CHECK (abs(ratio - ratio_cedear / ratio_subyacente) < 1e-9)
        );
        """,
        "v2.37 (2026-09-24, CEDEAR pesos/dolares Fase 2): ratio de conversion de cada CEDEAR. Fuente de "
        "verdad del ratio = PDF de BYMA (`fecha_lista` = su 'Actualizado'); ISIN/pais/industria/tipo del "
        "Excel de Comafi (solo enriquecimiento, `emisor`='comafi' cuando cruzo; NULL = sin cruce, no "
        "implica Caja de Valores). `ratio` = CEDEARs por accion (derivado, fraccionario si 1 CEDEAR = varias "
        "acciones). `ticker_origen` en formato data912 (`BRK.B`). La puebla cargar_ratios_cedear.py "
        "(dry-run primero; un cambio de ratio o una baja exige --confirmar-cambios). Reemplaza a "
        "`watchlist_rv.ratio_cedear` como lugar de lectura (esa columna queda, sin uso).",
    ),
    (
        "indicadores_catalogo",
        """
        CREATE TABLE indicadores_catalogo (
            id              TEXT PRIMARY KEY,
            nombre          TEXT NOT NULL,
            seccion         TEXT NOT NULL,
            unidad          TEXT NOT NULL,
            tipo_delta      TEXT NOT NULL CHECK (tipo_delta IN ('pp', 'pct')),
            frecuencia      TEXT NOT NULL CHECK (frecuencia IN ('diaria', 'semanal', 'mensual', 'trimestral', 'anual')),
            fuente          TEXT NOT NULL,
            serie_origen    TEXT NOT NULL,
            prioridad       INTEGER NOT NULL CHECK (prioridad IN (1, 2, 3)),
            activo          INTEGER NOT NULL DEFAULT 1 CHECK (activo IN (0, 1)),
            orden           INTEGER NOT NULL,
            clave_benchmark TEXT UNIQUE,
            decimales       INTEGER NOT NULL DEFAULT 2 CHECK (decimales BETWEEN 0 AND 4),
            semaforo        TEXT
        );
        """,
        "v2.39 (2026-09-29, Epica 21): una fila por indicador macro. Fuente UNICA de la lista de "
        "indicadores (validacion de tesis_umbrales.indicador, mapeo a benchmarks_macro via "
        "`clave_benchmark`, etiquetas del front via GET /api/indicadores-catalogo). `tipo_delta` define "
        "como se expresa el delta 3m/12m: 'pp' diferencia simple, 'pct' variacion porcentual. La semilla "
        "vive en migrar_v239_indicadores_macro.py (este script no siembra datos). v2.41 (2026-09-30, Tanda 3): "
        "`frecuencia` admite semanal y anual (migrar_v241_indicadores_tanda3.py, reconstruccion). v2.43 "
        "(Tanda 4): + `semaforo` (productividad/ciclo_corto/ciclo_largo/valuacion, validado en Python; "
        "NULL = fuera del panel Maquina economica).",
    ),
    (
        "indicadores_anotaciones",
        """
        CREATE TABLE indicadores_anotaciones (
            indicador_id        TEXT PRIMARY KEY REFERENCES indicadores_catalogo (id),
            umbral_valor        REAL,
            umbral_direccion    TEXT CHECK (umbral_direccion IN ('mayor_que', 'menor_que')),
            objetivo_valor      REAL,
            nota                TEXT,
            actualizado         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            umbral_d3m_valor     REAL,
            umbral_d3m_direccion TEXT CHECK (umbral_d3m_direccion IN ('mayor_que', 'menor_que'))
                                      CHECK ((umbral_d3m_valor IS NULL) = (umbral_d3m_direccion IS NULL)),
            CHECK ((umbral_valor IS NULL) = (umbral_direccion IS NULL))
        );
        """,
        "v2.39 (2026-09-29, Epica 21): lo que el usuario escribe sobre cada indicador desde su tarjeta -- "
        "umbral (dispara el badge de cruce), objetivo (solo referencia, no alerta) y nota. Los umbrales de "
        "las tesis siguen en tesis_umbrales. CRUD en indicadores_macro.guardar_anotacion. v2.44 (2026-10-02, "
        "migrar_v244_umbral_velocidad.py): + umbral de velocidad `umbral_d3m_valor`/`umbral_d3m_direccion`, "
        "opcional, contra el delta a 3 meses en la unidad del `tipo_delta` (no cambia el color del semaforo).",
    ),
    (
        "maquina_fases",
        """
        CREATE TABLE maquina_fases (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            semaforo    TEXT NOT NULL CHECK (semaforo IN ('productividad', 'ciclo_corto', 'ciclo_largo', 'valuacion')),
            fecha       DATE NOT NULL,
            fase        TEXT NOT NULL CHECK (length(trim(fase)) > 0),
            creado_en   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """,
        "v2.43 (2026-09-30, Epica 21 Tanda 4): la 'fase actual' que el usuario escribe por semaforo del panel "
        "Maquina economica. Una fila por revision, nunca se pisa (historial de su lectura). CRUD en "
        "indicadores_macro.guardar_fase/listar_fases.",
    ),
    (
        "dividendos_proyectados",
        """
        CREATE TABLE dividendos_proyectados (
            activo_id         INTEGER NOT NULL,
            simbolo           TEXT NOT NULL,
            fecha_ex          DATE NOT NULL,
            fecha_pago        DATE NOT NULL,
            monto_por_accion  REAL NOT NULL CHECK (monto_por_accion > 0),
            moneda            TEXT NOT NULL CHECK (moneda IN ('USD', 'ARS')),
            tipo              TEXT NOT NULL CHECK (tipo IN ('historico', 'anunciado', 'proyectado')),
            actualizado_en    TIMESTAMP NOT NULL,
            PRIMARY KEY (activo_id, fecha_ex),
            FOREIGN KEY (activo_id) REFERENCES activos (id) ON DELETE CASCADE
        );
        """,
        "v2.46 (2026-10-05, flujo estimado vs. real, Tanda 2): cronograma de dividendos POR ACCION del "
        "subyacente (yfinance), mismo rol que cronograma_pagos_bonos. Lo llena "
        "cargar_dividendos_proyectados.py (puesta_al_dia lo rehace si tiene > 24 h); flujo_estimado.py lo "
        "multiplica por tenencia/ratio/factor neto.",
    ),
    (
        "eventos_corporativos",
        """
        CREATE TABLE eventos_corporativos (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            activo_id          INTEGER NOT NULL,
            contenedor_id      INTEGER NOT NULL,
            fecha              DATE NOT NULL,
            tipo               TEXT NOT NULL CHECK (tipo IN ('split', 'contrasplit')),
            ratio_viejas       REAL NOT NULL CHECK (ratio_viejas > 0),
            ratio_nuevas       REAL NOT NULL CHECK (ratio_nuevas > 0),
            cantidad_antes     REAL NOT NULL CHECK (cantidad_antes > 0),
            cantidad_despues   REAL NOT NULL CHECK (cantidad_despues >= 0),
            descripcion        TEXT,
            creado_en          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (activo_id, contenedor_id, fecha),
            CHECK ((tipo = 'split' AND ratio_nuevas > ratio_viejas AND cantidad_despues > cantidad_antes)
                OR (tipo = 'contrasplit' AND ratio_nuevas < ratio_viejas AND cantidad_despues < cantidad_antes)),
            FOREIGN KEY (activo_id) REFERENCES activos (id) ON DELETE RESTRICT,
            FOREIGN KEY (contenedor_id) REFERENCES contenedores (id) ON DELETE RESTRICT
        );
        """,
        "v2.48 (2026-10-10, contrasplit de un ETF): split/contrasplit de una tenencia, por contenedor. "
        "ratio_viejas:ratio_nuevas es el del mercado (informativo, 3:1); cantidad_antes/_despues es lo que "
        "registro el broker (ej. 5 -> 2, redondeado). vista_posiciones suma cantidad_despues - "
        "cantidad_antes; dashboard.calcular_ganancia_por_activo cambia la cantidad en la fecha del evento "
        "sin tocar el costo (no es una venta: ganancia realizada 0, sin flujo de efectivo). Alta con "
        "cargar_evento_corporativo.py (valida la tenencia previa).",
    ),
]

INDICES = [
    "CREATE INDEX idx_movimientos_fecha ON movimientos (fecha);",
    "CREATE INDEX idx_movimientos_origen ON movimientos (cuenta_origen_id);",
    "CREATE INDEX idx_movimientos_destino ON movimientos (cuenta_destino_id);",
    "CREATE INDEX idx_movimientos_categoria ON movimientos (categoria_id);",
    "CREATE INDEX idx_precios_activo_fecha ON precios (activo_id, fecha);",
    "CREATE INDEX idx_cuentas_contenedor ON cuentas (contenedor_id);",
    "CREATE INDEX idx_operaciones_contenedor ON operaciones_activos (contenedor_id);",
    "CREATE INDEX idx_flujos_fecha ON flujos_esperados (fecha_estimada);",
    "CREATE INDEX idx_flujos_activo ON flujos_esperados (activo_id);",
    "CREATE UNIQUE INDEX idx_flujos_evento ON flujos_esperados (tipo_flujo, COALESCE(activo_id, -1), COALESCE(propiedad_id, -1), COALESCE(contenedor_id, -1), fecha_estimada) WHERE origen_estimado <> 'importador';",
    "CREATE INDEX idx_cronograma_activo ON cronograma_pagos_bonos (activo_id);",
    "CREATE INDEX idx_cronograma_fecha ON cronograma_pagos_bonos (fecha_pago);",
    "CREATE INDEX idx_historico_rf_ticker ON mercado_renta_fija_historico (ticker);",
    "CREATE INDEX idx_historico_rf_fecha ON mercado_renta_fija_historico (fecha);",
    "CREATE INDEX idx_pal_tarea_fecha ON puesta_al_dia_log (tarea, accion, fecha_dato);",
    "CREATE INDEX idx_tesis_bonos_ticker ON tesis_bonos (ticker);",
    "CREATE INDEX idx_tesis_bonos_fecha ON tesis_bonos (fecha);",
    "CREATE INDEX idx_tesis_bonos_estado ON tesis_bonos (estado);",
    "CREATE INDEX idx_tesis_cartera_fecha ON tesis_cartera (fecha);",
    "CREATE INDEX idx_tesis_cartera_estado ON tesis_cartera (estado);",
    "CREATE INDEX idx_tesis_acciones_ticker ON tesis_acciones (ticker);",
    "CREATE INDEX idx_tesis_acciones_fecha ON tesis_acciones (fecha);",
    "CREATE INDEX idx_tesis_acciones_estado ON tesis_acciones (estado);",
    "CREATE INDEX idx_tesis_umbrales_tesis ON tesis_umbrales (tesis_tabla, tesis_id);",
    "CREATE INDEX idx_macro_historico_clave_fecha ON macro_historico (clave, fecha);",
    "CREATE INDEX idx_tesis_indicadores_activo ON tesis_indicadores (activo_id);",
    "CREATE INDEX idx_maquina_fases_semaforo ON maquina_fases (semaforo, fecha);",
    # Real Estate (v2.23-v2.30): estaban en las migraciones y faltaban aca
    # (los encontro test_schema_vs_real.py, 2026-10-28).
    "CREATE INDEX idx_tesis_inmuebles_ticker ON tesis_inmuebles (ticker);",
    "CREATE INDEX idx_tesis_inmuebles_fecha ON tesis_inmuebles (fecha);",
    "CREATE INDEX idx_tesis_inmuebles_estado ON tesis_inmuebles (estado);",
    "CREATE INDEX idx_alquileres_propiedad ON alquileres (propiedad_id);",
    "CREATE INDEX idx_alquileres_fecha_cobro ON alquileres (fecha_cobro);",
    "CREATE INDEX idx_valuaciones_propiedad ON valuaciones (propiedad_id);",
    "CREATE INDEX idx_pasivos_propiedad ON pasivos (propiedad_id);",
    "CREATE INDEX idx_pagos_pasivo_pasivo ON pagos_pasivo (pasivo_id);",
    "CREATE INDEX idx_gastos_propiedad_propiedad ON gastos_propiedad (propiedad_id);",
]


def create_schema(db_path: Path, reset: bool = False) -> None:
    if reset and db_path.exists():
        db_path.unlink()
        print(f"🗑️  DB anterior eliminada: {db_path}")

    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys = ON;")

    existentes = {
        row[0]
        for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table';"
        )
    }

    for nombre, sql, motivo in TABLES:
        if nombre in existentes:
            print(f"⏭️  '{nombre}' ya existe, se omite.")
            continue
        con.executescript(sql)
        print(f"✅ Tabla creada: {nombre}")
        print(f"   → {motivo}")

    for idx_sql in INDICES:
        con.executescript(idx_sql.replace(
            "CREATE INDEX", "CREATE INDEX IF NOT EXISTS"
        ))

    con.commit()
    con.close()
    print(f"\n📦 Schema v2.1 listo en: {db_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PWOS - crear schema v2.1")
    parser.add_argument("--reset", action="store_true",
                         help="Borra la DB existente y la recrea desde cero")
    args = parser.parse_args()
    create_schema(DB_PATH, reset=args.reset)
