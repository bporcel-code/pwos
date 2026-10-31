import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "pwos.db"

VIEWS = [
    (
        "vista_saldos_cuenta",
        """
        CREATE VIEW vista_saldos_cuenta AS
        SELECT
            c.id AS cuenta_id,
            c.alias,
            c.moneda,
            c.estado,
            co.nombre AS contenedor,
            COALESCE(SUM(CASE WHEN m.cuenta_destino_id = c.id THEN m.monto END), 0)
            - COALESCE(SUM(CASE WHEN m.cuenta_origen_id = c.id THEN m.monto END), 0)
                AS saldo
        FROM cuentas c
        JOIN contenedores co ON co.id = c.contenedor_id
        LEFT JOIN movimientos m
            ON m.cuenta_origen_id = c.id OR m.cuenta_destino_id = c.id
        GROUP BY c.id;
        """,
        "Saldo de efectivo por cuenta (moneda nativa). Ya no incluye "
        "tenencias: las cuentas son puro efectivo, como en la plataforma real.",
    ),
    (
        "vista_posiciones",
        """
        CREATE VIEW vista_posiciones AS
        SELECT * FROM (
            SELECT
                a.id AS activo_id,
                a.ticker,
                a.tipo AS tipo_activo,
                x.contenedor_id,
                co.nombre AS contenedor,
                SUM(x.cantidad) AS cantidad
            FROM (
                SELECT oa.activo_id, oa.contenedor_id,
                       CASE WHEN m.tipo = 'compra_activo'
                            THEN oa.cantidad ELSE -oa.cantidad END AS cantidad
                FROM operaciones_activos oa
                JOIN movimientos m ON m.id = oa.movimiento_id
                UNION ALL
                SELECT activo_id, contenedor_id, cantidad_despues - cantidad_antes
                FROM eventos_corporativos
            ) x
            JOIN activos a ON a.id = x.activo_id
            JOIN contenedores co ON co.id = x.contenedor_id
            GROUP BY a.id, x.contenedor_id
        )
        WHERE ABS(cantidad) > 1e-6;
        """,
        "Cantidad neta por activo y por CONTENEDOR (bróker) directamente "
        "— ya no depende de una cuenta ficticia. El filtro de tolerancia "
        "va en un WHERE externo sobre la subconsulta ya materializada, NO "
        "en un HAVING sobre el SUM(CASE...) directo: probamos que SQLite "
        "puede reevaluar esa expresion agregada con un redondeo de punto "
        "flotante levemente distinto entre la columna de salida y la "
        "condicion de HAVING, dejando pasar como 'residual' posiciones que "
        "en la columna materializada dan 0.0 exacto (bug detectado primero "
        "en el fix de un importador, documentado tambien en los importadores "
        "— HAVING cantidad != 0 y hasta HAVING "
        "ABS(cantidad) > 1e-6 sufren lo mismo, solo el WHERE externo lo evita). "
        "v2.48 (2026-10-10): suma el ajuste de los splits/contrasplits de "
        "eventos_corporativos (cantidad_despues - cantidad_antes; ej. un contrasplit 5 -> 2).",
    ),
    (
        "vista_precio_actual",
        """
        CREATE VIEW vista_precio_actual AS
        SELECT p.activo_id, p.precio, p.moneda, p.fecha
        FROM precios p
        INNER JOIN (
            SELECT activo_id, MAX(fecha) AS fecha_max
            FROM precios
            GROUP BY activo_id
        ) ult ON ult.activo_id = p.activo_id AND ult.fecha_max = p.fecha;
        """,
        "Última cotización conocida de cada activo (incluido USD_MEP).",
    ),
    (
        "vista_proximos_flujos",
        """
        CREATE VIEW vista_proximos_flujos AS
        SELECT
            f.id,
            COALESCE(a.ticker, p.alias) AS ticker,
            COALESCE(a.tipo, 'propiedad') AS tipo_activo,
            f.tipo_flujo,
            f.fecha_estimada,
            f.monto_estimado,
            f.moneda,
            f.estado,
            CAST(julianday(f.fecha_estimada) - julianday('now') AS INTEGER)
                AS dias_restantes
        FROM flujos_esperados f
        LEFT JOIN activos a ON a.id = f.activo_id      -- v2.45: alquileres no tienen activo
        LEFT JOIN propiedades p ON p.id = f.propiedad_id
        WHERE f.estado = 'pendiente'
        ORDER BY f.fecha_estimada;
        """,
        "Reemplaza tu tabla dinámica de Excel: todos los cobros "
        "pendientes (cupón, amortización, dividendo, alquiler), "
        "ordenados por cercanía, con días restantes calculados solos.",
    ),
    (
        "vista_flujo_fondos_bonos",
        """
        CREATE VIEW vista_flujo_fondos_bonos AS
        WITH tenencia AS (
            SELECT activo_id, SUM(cantidad) AS cantidad
            FROM vista_posiciones
            GROUP BY activo_id
        ),
        mep AS (
            SELECT p.precio AS valor
            FROM vista_precio_actual p
            JOIN activos a ON a.id = p.activo_id
            WHERE a.ticker = 'USD_MEP'
        ),
        a3500 AS (
            SELECT p.precio AS valor
            FROM vista_precio_actual p
            JOIN activos a ON a.id = p.activo_id
            WHERE a.ticker = 'USD_A3500'
        )
        SELECT
            a.ticker,
            c.fecha_pago,
            c.tipo_pago,
            t.cantidad,
            (c.monto_por_vn * t.cantidad / 100.0) AS monto_moneda_pago,
            r.moneda_pago,
            CASE r.moneda_pago
                 WHEN 'USD' THEN (c.monto_por_vn * t.cantidad / 100.0)
                 WHEN 'USD_LINKED' THEN (c.monto_por_vn * t.cantidad / 100.0) / (SELECT valor FROM a3500)
                 ELSE (c.monto_por_vn * t.cantidad / 100.0) / (SELECT valor FROM mep)
            END AS monto_usd,
            CASE WHEN r.moneda_pago = 'USD' THEN 0 ELSE 1 END AS es_estimado,
            r.tipo_instrumento,
            r.legislacion
        FROM cronograma_pagos_bonos c
        JOIN activos a ON a.id = c.activo_id
        JOIN tenencia t ON t.activo_id = c.activo_id
        JOIN bonos_referencia r ON r.activo_id = c.activo_id
        WHERE c.fecha_pago >= DATE('now')
          AND r.moneda_pago IS NOT NULL;
        """,
        "Monto REAL (tenencia vigente x monto por VN 100) de cada evento "
        "de pago futuro de renta fija, convertido a USD. 3 casos, no 2: "
        "'USD' va directo (es_estimado=0); 'ARS' se convierte al MEP de "
        "HOY; 'USD_LINKED' (dollar-linked -- el monto que "
        "trae Rava YA viene en pesos) se convierte al A3500 de HOY "
        "(USD_A3500, confirmado == BCRA estadisticas cambiarias, ver "
        "cargar_usd_a3500.py), NO al MEP -- un dollar-linked se liquida "
        "con la formula de la Comunicacion A3500, mezclarlo con MEP le "
        "sumaria la brecha MEP-A3500 a un instrumento que no tiene "
        "relación con esa brecha (decisión del usuario, 2026-08-12). ARS y "
        "USD_LINKED quedan igual con es_estimado=1 -- el tipo de cambio futuro no "
        "se conoce en ningún caso, no se mezcla sin distinción con lo "
        "que sí es cierto en USD. Bonos sin moneda_pago clasificada en "
        "bonos_referencia quedan afuera (no se adivina, ver "
        "cargar_bonos_referencia_flujo.py). tipo_instrumento/legislacion "
        "agregados 2026-08-19 para la tabla de eventos del rediseño del "
        "dashboard -- ya estaban JOINeados via bonos_referencia, solo "
        "faltaba seleccionarlos.",
    ),
    (
        "vista_flujo_fondos_mensual",
        """
        CREATE VIEW vista_flujo_fondos_mensual AS
        SELECT
            strftime('%Y-%m', fecha_pago) AS mes,
            tipo_pago,
            es_estimado,
            SUM(monto_usd) AS monto_usd
        FROM vista_flujo_fondos_bonos
        GROUP BY mes, tipo_pago, es_estimado
        ORDER BY mes;
        """,
        "Flujo de fondos de renta fija agregado por mes calendario, "
        "separando interés de amortización Y separando lo que es una "
        "cifra cierta en USD de lo que es una estimación al MEP de hoy "
        "(es_estimado) -- para el indicador del dashboard (Tarea nueva, "
        "2026-08-12).",
    ),
]


def create_views(db_path: Path = DB_PATH) -> None:
    con = sqlite3.connect(db_path)
    for nombre, sql, motivo in VIEWS:
        con.execute(f"DROP VIEW IF EXISTS {nombre};")
        con.executescript(sql)
        print(f"✅ Vista creada: {nombre}")
        print(f"   → {motivo}")
    con.commit()
    con.close()
    print(f"\n📊 Vistas listas en: {db_path}")


if __name__ == "__main__":
    create_views()
