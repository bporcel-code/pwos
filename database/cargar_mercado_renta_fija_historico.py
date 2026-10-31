"""
PWOS -- snapshot DIARIO de `mercado_renta_fija_historico` (Epica 11,
2026-09-11: "comparar contra todo el universo para
descubrir oportunidades", no solo monitorear la cartera).

Por que una tabla nueva y no historizar `mercado_renta_fija_rava`
directo: esa tabla es PRIMARY KEY por ticker (1 fila, se pisa en cada
refresco de Rava, n8n cada 30 min) -- historizarla shockearia el
volumen (~890 especies x cada 30 min) sin necesidad real (1 snapshot
diario alcanza para lo que el usuario quiere).

Alcance (v2.18, ampliado el mismo dia que v2.17 -- ver create_schema.py):
  - TIR / precio / paridad / duration: TODO el universo de Rava
    (~890 especies), tal cual estan cacheadas en `mercado_renta_fija_
    rava` -- no dependen de tener posicion, CERO llamadas nuevas (se
    lee la tabla que el refresco de 30 min ya mantiene al dia). Precio
    = `precio_limpio_vn100`, paridad = `paridad_calc` (los "limpios"
    que usa el resto del dashboard, no los crudos de Rava que vienen
    sin normalizar/en 0 para la mayoria).
  - Carry / BPV / Breakeven: SOLO para los ~22 bonos con posicion real
    (dependen de VN en cartera) -- reusa `rotacion_bonos()` (tir_bonos.py)
    como UNICA fuente de verdad para ese overlay, sin recalcular nada
    de nuevo. Quedan NULL para el resto del universo -- decision
    explicita del usuario: extenderlos a las ~890 especies hubiera
    necesitado ~868 llamadas EN VIVO diarias al endpoint de flujo de
    Rava (sin version bulk, a diferencia de TIR/duration/paridad), sin
    precedente a ese volumen/frecuencia -- se descarto.
  - `en_cartera` (0/1) marca esas ~22 filas para que el consumidor
    pueda filtrar "bonos en cartera" vs "resto del mercado" sin resolver la
    posicion de nuevo.

Cadencia: DIARIA, no cada 30 min como el resto del mercado -- pedido
del usuario. Necesita un trigger NUEVO en n8n (Schedule Trigger
1x/dia, en algun momento despues del cierre de rueda) apuntando a
POST /api/snapshot-mercado-bonos-historico -- separado del refresco de
30 min de mercado_renta_fija_rava.

Sin backfill: no hay forma de recuperar el pasado (mercado_renta_fija_rava
nunca guardo historia), arranca a acumular datos de HOY en adelante.

UPSERT sobre (ticker, fecha) -- re-ejecutable el mismo dia sin duplicar
(si corre 2 veces el mismo dia, la segunda pisa con datos mas frescos,
mismo criterio que cargar_benchmarks_macro.py). Tolerante a filas
parciales (ticker sin tir en Rava, bono de cartera sin BPV calculable,
etc.) -- se guarda la fila igual, con NULL en lo que no se pudo
calcular, nunca se inventa un numero.

Uso:
    python cargar_mercado_renta_fija_historico.py
"""

import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import clasificar_bonos
from tir_bonos import rotacion_bonos
from dashboard import obtener_precio_fx
from valorizacion import solo_cierre
import configuracion

# En el contenedor pwos-api, database/pwos.db es el bind mount a V: y
# tiene datos. En la PC del usuario es un stub de 0 bytes (base real en
# VeraCrypt) -> se usa V: directo. Mismo criterio que el resto de los
# cargar_*.py del proyecto.
DB_PATH = configuracion.ruta_db()


def hacer_backup(db_path: Path) -> None:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-historico-bonos_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(db_path, backup_path)
    con = sqlite3.connect(backup_path)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup_path.name} (integrity_check=ok)")


@solo_cierre()  # historia / escribe en la base: siempre con el cierre (ADR-033)
def cargar(db_path: Path = DB_PATH, fecha: str | None = None) -> None:
    """`fecha` (YYYY-MM-DD): dia al que se atribuye el snapshot. Por defecto
    HOY EN HORA ARGENTINA (no `date.today()`: dentro del contenedor es UTC y
    un snapshot tardio de noche, p.ej. 22:00 ART = 01:00 UTC, caeria en el
    dia siguiente). La puesta al dia (puesta_al_dia.py) la usa para tomar el
    snapshot de un dia cuyo job de las 18:00 se perdio."""
    hacer_backup(db_path)

    con = sqlite3.connect(db_path, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")
    hoy = fecha or datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date().isoformat()

    try:
        # Overlay de cartera (carry/BPV/breakeven) -- unica fuente de
        # verdad, no se recalcula nada de esto aca. Indexado por ticker
        # para lookup O(1) contra el universo completo de abajo.
        mep = obtener_precio_fx(con, "USD_MEP")
        ccl = obtener_precio_fx(con, "USD_CCL")
        cartera_por_ticker = {f["ticker"]: f for f in rotacion_bonos(con, mep, ccl)}

        universo = con.execute(
            """
            SELECT ticker, tipo_instrumento, legislacion, tir, duration,
                   precio_limpio_vn100, paridad_calc
            FROM mercado_renta_fija_rava;
            """
        ).fetchall()

        con.execute("BEGIN;")
        insertadas = 0
        en_cartera_snapshoteados = 0
        for ticker, tipo_instrumento, legislacion, tir, duration, precio_limpio, paridad_calc in universo:
            categoria = clasificar_bonos.clasificar(ticker, tipo_instrumento, legislacion)
            fila_cartera = cartera_por_ticker.get(ticker)
            en_cartera = fila_cartera is not None
            if en_cartera:
                en_cartera_snapshoteados += 1

            con.execute(
                """
                INSERT INTO mercado_renta_fija_historico
                    (ticker, fecha, categoria, moneda, tir_pct, precio_vn100,
                     paridad_pct, duration, carry_pct, bpv_usd, breakeven_pb, en_cartera)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (ticker, fecha) DO UPDATE SET
                    categoria    = excluded.categoria,
                    moneda       = excluded.moneda,
                    tir_pct      = excluded.tir_pct,
                    precio_vn100 = excluded.precio_vn100,
                    paridad_pct  = excluded.paridad_pct,
                    duration     = excluded.duration,
                    carry_pct    = excluded.carry_pct,
                    bpv_usd      = excluded.bpv_usd,
                    breakeven_pb = excluded.breakeven_pb,
                    en_cartera   = excluded.en_cartera,
                    origen       = 'snapshot';
                """,
                (
                    ticker, hoy, categoria,
                    fila_cartera.get("moneda") if fila_cartera else None,
                    round(tir * 100, 4) if tir is not None else None,
                    round(precio_limpio, 4) if precio_limpio is not None else None,
                    round(paridad_calc * 100, 4) if paridad_calc is not None else None,
                    round(duration, 4) if duration is not None else None,
                    fila_cartera.get("carry_pct") if fila_cartera else None,
                    fila_cartera.get("bpv_usd") if fila_cartera else None,
                    fila_cartera.get("breakeven_pb") if fila_cartera else None,
                    int(en_cartera),
                ),
            )
            insertadas += 1

        sin_overlay = [t for t in cartera_por_ticker if t not in {u[0] for u in universo}]
        if sin_overlay:
            print(f"  (en cartera pero sin fila en mercado_renta_fija_rava, no se snapshotearon): {sin_overlay}")

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print(f"Carga OK: {insertadas}/{len(universo)} especies snapshoteadas para {hoy} "
              f"({en_cartera_snapshoteados} en cartera, con carry/BPV/breakeven). "
              f"foreign_key_check: sin filas rotas")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"\nCARGA ABORTADA, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


def listar_historico(con, ticker: str | None = None, desde: str | None = None,
                      hasta: str | None = None, en_cartera: bool | None = None) -> list[dict]:
    """Lee la serie historica ya snapshoteada por `cargar()` (Epica 12
    Parte 4, 2026-09-12 -- hasta esta ronda solo existia el POST que
    escribe esta tabla, ningun GET para leerla). `ticker` es el uso
    esperado principal (comparar evolucion de TIR de un bono puntual en
    el tiempo) -- sin filtro, la tabla crece ~898 filas/dia, no tiene
    sentido devolver todo de una."""
    condiciones, params = [], []
    if ticker:
        condiciones.append("ticker = ?")
        params.append(ticker.strip().upper())
    if desde:
        condiciones.append("fecha >= ?")
        params.append(desde)
    if hasta:
        condiciones.append("fecha <= ?")
        params.append(hasta)
    if en_cartera is not None:
        condiciones.append("en_cartera = ?")
        params.append(1 if en_cartera else 0)
    where = f"WHERE {' AND '.join(condiciones)}" if condiciones else ""

    filas = con.execute(
        f"""
        SELECT ticker, fecha, categoria, moneda, tir_pct, precio_vn100, paridad_pct,
               duration, carry_pct, bpv_usd, breakeven_pb, en_cartera
        FROM mercado_renta_fija_historico
        {where}
        ORDER BY fecha, ticker;
        """,
        params,
    ).fetchall()
    columnas = ("ticker", "fecha", "categoria", "moneda", "tir_pct", "precio_vn100",
                "paridad_pct", "duration", "carry_pct", "bpv_usd", "breakeven_pb", "en_cartera")
    return [dict(zip(columnas, f)) for f in filas]


if __name__ == "__main__":
    cargar()
