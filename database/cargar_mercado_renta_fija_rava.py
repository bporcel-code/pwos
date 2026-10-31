"""
PWOS -- Carga `mercado_renta_fija_rava` (Epica 11, Herramienta 1) desde
`rava_client.py::obtener_referencia_bonos()`: TIR + duration de TODO el
universo de bonos que publica Rava (~885 especies), no solo los que
el usuario tiene en cartera. Insumo de la sugerencia de reemplazo al
detectar compresion de TIR en una posicion propia.

Re-ejecutable (mismo patron que cargar_bonos_referencia_flujo.py):
UPSERT por `ticker`, pensado para correr periodicamente (ronda semanal,
junto con el resto del refresco de Rava).

Cobertura (2026-09-07): de 885 especies, 884 traen `tir` numerico -- la
unica excepcion (SOC3O) viene con todos los campos de mercado vacios
(sin precio, sin tir, sin duration) y se salta sin abortar el batch,
mismo criterio de tolerancia a fallos por ticker que el resto del
pipeline de Rava. El campo `tipo` de Rava viene vacio para ~500
especies (incluidos bonos provinciales y ONs habituales)
-- NO se usa como filtro de inclusion, es un hueco de metadata de Rava,
no una senal de que el instrumento no sea un bono real.

NO se clasifica `moneda_pago` aca (ver docstring de la tabla en
create_schema.py) -- inviable a mano para ~885 tickers. Cualquier
consumidor de esta tabla trata la moneda como no verificada.

Campos de mercado (v2.14, 2026-09-08): ademas de tir/duration/paridad/
valor_tecnico/precio se guardan `dm` (modified duration), `convexity` y
`current_yield` -- todos vienen en el MISMO JSON de Rava, ninguna
llamada extra. Son metricas calculadas por Rava, agnosticas de moneda
(a diferencia de `precio`) -- se muestran directo en el panel "Mercado
de bonos" del simulador.

Precio limpio / paridad / current yield calculados (v2.15, movido aca el
2026-09-10 desde enriquecer_mercado_bonos.py): `precio_limpio_vn100`,
`paridad_calc`, `current_yield_calc` son lo que muestra la tabla "Mercado
de bonos" -- se recalculan ACA con cada refresco (cada 30 min) para que
no queden viejos al lado de la TIR. Sin red extra: `paridad*valor_tecnico`
cuando Rava publica paridad en el bulk, valor presente de los flujos a la
TIR de Rava para los bonos de cartera (`cronograma_pagos_bonos` local),
o la paridad de una especie hermana C/D. Ver `_metricas_limpias`.
enriquecer_mercado_bonos.py ahora SOLO escribe descripcion/emisor/isin/
moneda_especie (datos casi estaticos, se corre a mano en la PC).

Ruta de la base: pensado para correr DENTRO del contenedor de pwos-api
(disparado por n8n via POST /api/refrescar-mercado-bonos, Opcion B --
tabla cacheada, refresco periodico en horario de mercado). Ahi
`Path(__file__).parent` es `/app` y `/app/pwos.db` es el bind mount de la
base. Corrido a mano fuera de Docker, `configuracion.ruta_db()` usa la
base de la configuracion (el `database/pwos.db` del repo es un stub de 0
bytes -- escribir ahi seria un no-op silencioso).
"""
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from clasificar_bonos import _sin_sufijo_especie
from rava_client import obtener_referencia_bonos
from simulador_bonos import flujos_futuros, metricas, precio_desde_tir
import configuracion

DB_PATH = configuracion.ruta_db()


def _float_o_none(valor) -> float | None:
    try:
        return float(valor)
    except (ValueError, TypeError):
        return None


def _metricas_limpias(con, ticker, tir, valor_tecnico, paridad, current_yield,
                      hoy, cartera_con_crono, paridad_por_base):
    """(precio_limpio_vn100, paridad_calc, current_yield_calc) -- el precio
    limpio / paridad / current yield que muestra la tabla "Mercado de
    bonos". Se calcula ACA (con cada refresco de Rava, cada 30 min) y no
    en enriquecer_mercado_bonos.py, para que no quede viejo al lado de la
    TIR (decision 2026-09-10). Orden de preferencia, TODO sin llamadas de
    red extra:
      1. Rava ya publico `paridad` en el bulk -> precio = paridad*VT.
      2. Bono en cartera con cronograma cacheado -> valor presente de los
         flujos a la TIR de Rava (`cronograma_pagos_bonos`, local).
      3. Prestar la paridad de una especie hermana C/D/base (misma
         emision) que si la tenga en el bulk.
      4. Sin dato (NULL) -- especie obscura fuera de cartera; si el usuario la
         clickea, el Analizador la calcula en vivo.
    """
    if paridad is not None and paridad > 0 and valor_tecnico:
        return (round(paridad * valor_tecnico, 4), round(paridad, 6),
                round(current_yield, 6) if current_yield else None)

    if tir is not None and ticker in cartera_con_crono:
        ff = flujos_futuros(con, ticker, hoy)
        if ff is not None and ff.get("fuente") == "cartera" and ff["flujos"]:
            precio = precio_desde_tir(ff["flujos"], tir * 100, hoy)
            if precio and precio > 0:
                m = metricas(precio, ff["flujos"], hoy, valor_tecnico)
                return (
                    round(precio, 4),
                    round(m["paridad_pct"] / 100, 6) if m.get("paridad_pct") is not None else None,
                    round(m["current_yield_pct"] / 100, 6) if m.get("current_yield_pct") is not None else None,
                )

    hermana = paridad_por_base.get(_sin_sufijo_especie(ticker))
    if hermana is not None and hermana > 0 and valor_tecnico:
        return (round(hermana * valor_tecnico, 4), round(hermana, 6), None)

    return (None, None, None)


def cargar(db_path: Path = DB_PATH) -> None:
    con = sqlite3.connect(db_path, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")

    referencia = obtener_referencia_bonos()
    print(f"Listado de Rava: {len(referencia)} especies.")

    ahora = datetime.now(timezone.utc).isoformat()
    hoy = date.today()
    ok, sin_tir, con_metricas = 0, [], 0

    # Insumos para _metricas_limpias (una consulta cada uno, no por ticker).
    cartera_con_crono = {
        r[0] for r in con.execute(
            "SELECT DISTINCT a.ticker FROM cronograma_pagos_bonos cp "
            "JOIN activos a ON a.id = cp.activo_id;"
        )
    }
    # Paridad del bulk de Rava por "emision" (ticker sin sufijo C/D) --
    # para prestarsela a la especie base cuando Rava la publica en 0.
    paridad_por_base: dict[str, float] = {}
    for tk, dd in referencia.items():
        par = _float_o_none(dd.get("paridad"))
        if par and par > 0:
            paridad_por_base.setdefault(_sin_sufijo_especie(tk), par)

    try:
        con.execute("BEGIN;")

        for ticker, d in referencia.items():
            tir = _float_o_none(d.get("tir"))
            if tir is None:
                sin_tir.append(ticker)
                continue

            duration = _float_o_none(d.get("duration"))
            dm = _float_o_none(d.get("dm"))
            convexity = _float_o_none(d.get("convexity"))
            current_yield = _float_o_none(d.get("current_yield"))
            paridad = _float_o_none(d.get("paridad"))
            valor_tecnico = _float_o_none(d.get("valor_tecnico"))
            precio = _float_o_none(d.get("precio"))
            vencimiento = d.get("vencimiento", "")[:10] or None  # 'YYYY-MM-DDT...' -> 'YYYY-MM-DD'

            precio_limpio, paridad_calc, current_yield_calc = _metricas_limpias(
                con, ticker, tir, valor_tecnico, paridad, current_yield,
                hoy, cartera_con_crono, paridad_por_base,
            )
            if precio_limpio is not None:
                con_metricas += 1

            con.execute(
                """
                INSERT INTO mercado_renta_fija_rava
                    (ticker, nombre, tipo_instrumento, legislacion, tir, duration, dm, convexity, current_yield, paridad, valor_tecnico, precio, vencimiento,
                     precio_limpio_vn100, paridad_calc, current_yield_calc, fuente, actualizado_en)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'rava', ?)
                ON CONFLICT (ticker) DO UPDATE SET
                    nombre = excluded.nombre,
                    tipo_instrumento = excluded.tipo_instrumento,
                    legislacion = excluded.legislacion,
                    tir = excluded.tir,
                    duration = excluded.duration,
                    dm = excluded.dm,
                    convexity = excluded.convexity,
                    current_yield = excluded.current_yield,
                    paridad = excluded.paridad,
                    valor_tecnico = excluded.valor_tecnico,
                    precio = excluded.precio,
                    vencimiento = excluded.vencimiento,
                    precio_limpio_vn100 = excluded.precio_limpio_vn100,
                    paridad_calc = excluded.paridad_calc,
                    current_yield_calc = excluded.current_yield_calc,
                    fuente = 'rava',
                    actualizado_en = excluded.actualizado_en;
                """,
                (ticker, (d.get("nombre") or "").strip(), d.get("tipo") or None,
                 d.get("ley") or None, tir, duration, dm, convexity, current_yield,
                 paridad, valor_tecnico, precio, vencimiento,
                 precio_limpio, paridad_calc, current_yield_calc, ahora),
            )
            ok += 1

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")

        # Refresco frecuente (Opcion B, via n8n) -> NO se hace copia
        # completa de la base cada vez (seria decenas de backups/dia de
        # una tabla 100% derivada de Rava y re-ejecutable). El resguardo
        # es: UPSERT dentro de una transaccion unica con rollback +
        # integrity_check sobre la base en vivo despues del commit.
        integridad = con.execute("PRAGMA integrity_check;").fetchone()[0]
        if integridad != "ok":
            raise RuntimeError(f"integrity_check post-commit = {integridad!r} (deberia ser 'ok')")

        print(f"\nCarga OK. {ok}/{len(referencia)} especies con TIR cargadas "
              f"({con_metricas} con precio_limpio/paridad calculados). integrity_check=ok")
        if sin_tir:
            print(f"  Sin TIR (no cargadas): {sin_tir}")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"\nCARGA ABORTADA, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    cargar()
