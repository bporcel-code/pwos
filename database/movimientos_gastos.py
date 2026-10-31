"""
PWOS -- listado de movimientos de la esfera consumo ("Movimientos y
gastos", Epica 10, primer modulo nuevo de la app).

Filtro base: `movimientos.tipo` en la esfera 'consumo' (hoy
'ingreso'/'egreso') -- reusa el mapeo centralizado de esferas.py en vez
de hardcodear la lista de tipos aca, para que los 2 lugares nunca
diverjan si el mapeo cambia.

Dentro de esa esfera, 3 categorias raiz (sembradas 2026-09-07, Epica 10)
NO son gasto/ingreso de vida real pese a tener tipo IN ('ingreso',
'egreso') -- se excluyen aca por categoria, no por tipo (`tipo` sigue
siendo la unica fuente de verdad para esfera economica, ver esferas.py;
esto es un afinado DENTRO de consumo):
  - "Costos de operatoria financiera" (comisiones de caucion, de
    compra/venta, cargos bancarios) -- sembrar_categoria_costos_operatoria_2026-09-07.py
  - "Impuestos sobre inversiones" (retencion de Ganancias sobre renta/
    dividendos) -- distinta de una comision, es fiscal
  - "Ajustes contables" (residuales sin explicar de raiz, parches
    documentados en ESTADO_PWOS.md seccion 5, no gasto real)
  ambas -- sembrar_categorias_impuestos_ajustes_2026-09-07.py

Fondeo/desfondeo entre brokers y cuentas personales (pendiente desde
2026-09-07, resuelto 2026-09-15) -- NINGUNA categoria de arriba las
tapaba: las 437 filas confirmadas (ver abajo) tenian `categoria_id`
NULL, entraban de punta a punta como si fueran gasto/ingreso real (ej.
un deposito a un broker de ARS 1M aparecia como "ingreso" de vida real). Es
la MISMA plata moviendose dentro del ecosistema del usuario (banco/
billetera -> broker o viceversa), nunca modelada como `transferencia`
vinculada (confirmado 2026-09-15: CERO filas cross-contenedor de tipo
`transferencia` en toda la base) -- cada lado queda como su propio
`ingreso`/`egreso` independiente, exclusion por PATRON DE TEXTO, mismo
criterio que ya usa `rendimiento.py` del lado broker:
  - Lado broker (`PATRONES_FLUJO_EXTERNO`, importado de rendimiento.py,
    no duplicado).
  - Lado personal (bancos y billeteras), `PATRONES_TRANSFERENCIA_
    BROKER_PERSONAL` -- razon social del broker tal cual aparece en la
    descripcion del banco (lista en la configuracion).

Quedan afuera, A PROPOSITO, las filas del lado personal que coinciden en
fecha+monto con algun flujo del lado broker pero SIN mencionar ningun
broker en su propio texto (ej. "Transferencia enviada A"). Probado y
descartado como criterio de auto-exclusion: puede haber dos depositos
del mismo monto el mismo dia en dos brokers, uno de ellos desde un banco
que no esta cargado -- monto+fecha solos no alcanzan para decidir sin
ambiguedad.
Quedan documentados en ESTADO_PWOS.md seccion 5 para que el usuario las
revise a mano (necesitaria el resumen real de cada cuenta para
confirmar destinatario) -- no se excluyen a ciegas.
"""

import configuracion
from esferas import tipos_de_esfera
from rendimiento import PATRONES_FLUJO_EXTERNO
from valorizacion import parte_propia, pct_de, pct_propiedad_por_cuenta

TIPOS_CONSUMO = tuple(tipos_de_esfera("consumo"))

CATEGORIAS_EXCLUIDAS = (
    "Costos de operatoria financiera",
    "Impuestos sobre inversiones",
    "Ajustes contables",
    # 2026-10-10 (tipo 'neutro', la crea cargar_ronda_incremental_2026-10-10.py):
    # plata que sale y vuelve, ni gasto ni ingreso (ej. un prestamo a un
    # tercero que se devuelve). Las transferencias entre cuentas propias NO van aca
    # (decision del usuario): se verifica en cada ronda que neteen cero
    # (transferencias.verificar_transferencias_propias).
    "Movimientos neutros",
)

# Lado personal del fondeo/desfondeo con brokers -- razon social tal
# cual aparece en la descripcion del banco o la billetera (en la
# configuracion). Conviene el nombre corto del broker: algunas billeteras
# truncan la descripcion ("Transferencia enviada A <BROKER>").
PATRONES_TRANSFERENCIA_BROKER_PERSONAL = configuracion.get("movimientos", "patrones_fondeo_broker_personal", default=[])

PATRONES_EXCLUIDOS = PATRONES_FLUJO_EXTERNO + PATRONES_TRANSFERENCIA_BROKER_PERSONAL


def listar_movimientos_gastos(con, anio: int | None = None) -> list[dict]:
    """Movimientos de consumo real (ingreso/egreso), excluyendo las
    categorias de CATEGORIAS_EXCLUIDAS (y sus hijas) por no ser gasto/
    ingreso de vida real, MAS los patrones de texto de PATRONES_
    EXCLUIDOS (fondeo/desfondeo real con brokers, ver docstring del
    modulo) -- mas nuevos primero. `anio`, si se pasa, filtra por año
    calendario de `fecha`."""
    placeholders = ",".join("?" for _ in TIPOS_CONSUMO)
    params: list = list(TIPOS_CONSUMO)

    where_anio = ""
    if anio is not None:
        where_anio = " AND strftime('%Y', m.fecha) = ?"
        params.append(str(anio))

    placeholders_excluidas = ",".join("?" for _ in CATEGORIAS_EXCLUIDAS)
    where_patrones = " OR ".join(["m.descripcion LIKE ?"] * len(PATRONES_EXCLUIDOS))
    filas = con.execute(
        f"""
        WITH RECURSIVE categorias_excluidas(id) AS (
            SELECT id FROM categorias WHERE nombre IN ({placeholders_excluidas})
            UNION ALL
            SELECT c.id FROM categorias c
            JOIN categorias_excluidas ce ON c.categoria_padre_id = ce.id
        )
        SELECT m.id, m.fecha, m.tipo, m.monto, m.moneda, m.descripcion,
               cat.nombre AS categoria, COALESCE(m.cuenta_destino_id, m.cuenta_origen_id) AS cuenta_id
        FROM movimientos m
        LEFT JOIN categorias cat ON cat.id = m.categoria_id
        WHERE m.tipo IN ({placeholders}){where_anio}
          AND (m.categoria_id IS NULL OR m.categoria_id NOT IN (SELECT id FROM categorias_excluidas))
          AND NOT ({where_patrones})
        ORDER BY m.fecha DESC, m.id DESC;
        """,
        list(CATEGORIAS_EXCLUIDAS) + params + PATRONES_EXCLUIDOS,
    ).fetchall()

    # Contenedor compartido (ADR-032): el gasto/ingreso cuenta en la parte propia; `monto_total` es lo que muestra la app (100%).
    pcts = pct_propiedad_por_cuenta(con)
    return [
        {
            "id": id_,
            "fecha": fecha,
            "tipo": tipo,
            "monto": parte_propia(monto, pct_de(pcts, cuenta_id)),
            "monto_total": monto,
            "pct_propiedad": pct_de(pcts, cuenta_id),
            "moneda": moneda,
            "descripcion": descripcion,
            "categoria": categoria,
        }
        for id_, fecha, tipo, monto, moneda, descripcion, categoria, cuenta_id in filas
    ]
