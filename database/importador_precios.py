"""
PWOS -- Importador real del feed de cotizaciones (Tarea 6, sesion 2026-07-14)

Reusa el matching de dry_run_precios.py (fetch + match contra activos
vigentes) y lo inserta en `precios`. Solo se insertan filas 'matched':
'no_match', 'ambiguo' y 'requiere_clasificacion_manual' se loguean pero
no se insertan a ciegas. La moneda de cada precio es la que devuelve
`_clasificar_moneda_precio` (mapa explicito `MONEDA_PRECIO_DATA912` para
bonos/ONs, ver docstring de dry_run_precios.py) -- ya no se asume ARS
para todo.

Salvaguardas (mismo patron que los importadores de brokers):
- Backup de pwos.db antes de tocar nada, verificado con integrity_check.
- Insert dentro de una unica transaccion.
- INSERT OR IGNORE sobre UNIQUE(activo_id, fecha): correr el script dos
  veces el mismo dia no duplica filas (la segunda corrida es un no-op
  para los activos ya cargados hoy, aunque la cotizacion en vivo haya
  cambiado intradia -- mismo criterio que el resto del feed, no se
  actualiza sobre una fila ya insertada en el dia).
- PRAGMA foreign_key_check antes de commitear.
- `precios` guarda solo CIERRES (ADR-033, 2026-10-27): un dia habil antes de
  las 17:00 (hora de Argentina) no se guarda nada -- si no, un refresco en
  plena rueda quedaba como cierre del dia y el de las 18:05 no entraba
  (INSERT OR IGNORE). La fecha es la de Argentina: el contenedor corre en
  UTC y despues de las 21:00 ART `date.today()` ya es "manana".
"""

import shutil
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import cargar_usd_a3500

from dry_run_precios import (
    DB_PATH,
    obtener_mep,
    obtener_ccl,
    obtener_todos_los_paneles,
    construir_reporte,
    imprimir_reporte,
)

def hacer_backup(fecha: date):
    # Corrigiendo un hueco encontrado 2026-08-21: este script quedo afuera
    # del fix de backup-a-VeraCrypt del 2026-08-19 (que solo toco los 4
    # importadores de brokers + insertar_movimientos.py) -- seguia
    # apuntando a una ruta fija en C: en texto plano. Mismo criterio que
    # los demas: sigue a DB_PATH.parent, timestamp en el nombre para no
    # pisar corridas previas.
    #
    # BACKUP_DIR se calcula ACA (no a nivel de modulo) a proposito --
    # encontrado el mismo dia: calcularlo al importar el modulo capturaba
    # el DB_PATH de ESE momento, y si algo reasigna `dry_run_precios.DB_PATH`
    # despues de importar (como hace este mismo script cuando se corre
    # fuera de Docker, ya que Path(__file__).parent/"pwos.db" no existe
    # mas tras la migracion a VeraCrypt), el backup igual caia en la ruta
    # vieja en C: -- paso real corriendo esto en este entorno de trabajo.
    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-precios_{fecha.isoformat()}.db"
    if backup_path.exists():
        # Encontrado 2026-09-23 (ver ESTADO_PWOS.md): abortar aca hacia
        # fallar (500) el job de las 18:05 de n8n cuando el usuario ya habia
        # corrido el feed a mano ese dia (09-14 y 09-15), aunque el
        # docstring de /api/refrescar-precios promete que repetir es un
        # no-op. Este importador es INSERT OR IGNORE: una 2da corrida el
        # mismo dia es segura. Se CONSERVA el backup de la 1ra corrida (el
        # estado previo a cualquier escritura del dia), nunca se pisa.
        print(f"Backup del dia ya existe ({backup_path.name}) - corrida repetida, se conserva el original.")
        return
    shutil.copy2(DB_PATH, backup_path)
    con = sqlite3.connect(backup_path)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup_path.name} (integrity_check=ok)")


ART = ZoneInfo("America/Argentina/Buenos_Aires")
HORA_CIERRE = 17  # BYMA cierra a las 17:00; antes de eso un dia habil, el precio es intradia


def es_intradia(ahora: datetime) -> bool:
    """True si `ahora` es un dia habil antes del cierre: lo que se baje no es un cierre."""
    return ahora.weekday() < 5 and ahora.hour < HORA_CIERRE


def importar(ahora: datetime | None = None):
    ahora = ahora or datetime.now(ART)
    if es_intradia(ahora):
        print(f"NO SE GUARDA: {ahora:%Y-%m-%d %H:%M} ART es un dia habil antes de las {HORA_CIERRE}:00 "
              "(rueda abierta o sin cerrar). `precios` guarda solo cierres (ADR-033); "
              "el job de las 18:05 y la Puesta al dia cargan el cierre del dia.")
        return
    try:
        mep = obtener_mep()
    except Exception as e:
        print(f"ABORTADO: no se pudo obtener USD MEP de dolarapi.com ({e}).")
        print("No tiene sentido seguir sin la cotizacion MEP del dia.")
        raise SystemExit(1)

    try:
        ccl = obtener_ccl()
    except Exception as e:
        print(f"ABORTADO: no se pudo obtener USD CCL de dolarapi.com ({e}).")
        print("No tiene sentido seguir sin la cotizacion CCL del dia (usada para CEDEARs).")
        raise SystemExit(1)

    paneles = obtener_todos_los_paneles()

    hacer_backup(ahora.date())
    con = sqlite3.connect(DB_PATH)
    con.execute("PRAGMA foreign_keys = ON;")

    reporte = construir_reporte(con, paneles)
    imprimir_reporte(reporte, mep)

    fecha_hoy = ahora.date().isoformat()

    con.execute("BEGIN;")

    activo_mep = con.execute(
        "SELECT id FROM activos WHERE ticker = 'USD_MEP';"
    ).fetchone()[0]
    cur = con.execute(
        "INSERT OR IGNORE INTO precios (activo_id, fecha, precio, moneda, fuente) "
        "VALUES (?, ?, ?, 'ARS', 'dolarapi');",
        (activo_mep, fecha_hoy, mep["venta"]),
    )
    insertados = 1 if cur.rowcount else 0

    activo_ccl = con.execute(
        "SELECT id FROM activos WHERE ticker = 'USD_CCL';"
    ).fetchone()[0]
    cur = con.execute(
        "INSERT OR IGNORE INTO precios (activo_id, fecha, precio, moneda, fuente) "
        "VALUES (?, ?, ?, 'ARS', 'dolarapi');",
        (activo_ccl, fecha_hoy, ccl["venta"]),
    )
    if cur.rowcount:
        insertados += 1
    for f in reporte:
        if f["estado"] != "matched":
            continue
        cur = con.execute(
            "INSERT OR IGNORE INTO precios (activo_id, fecha, precio, moneda, fuente) "
            "VALUES (?, ?, ?, ?, 'data912');",
            (f["activo_id"], fecha_hoy, f["precio"], f["moneda_precio"]),
        )
        if cur.rowcount:
            insertados += 1

    fk_check = con.execute("PRAGMA foreign_key_check;").fetchall()
    if fk_check:
        con.execute("ROLLBACK;")
        raise RuntimeError(f"foreign_key_check fallo, se hizo ROLLBACK: {fk_check}")

    con.commit()
    print(f"\nInsertadas {insertados} filas nuevas en 'precios' "
          f"(activos + USD_MEP + USD_CCL) para {fecha_hoy}.")
    print("(Las filas ya existentes de una corrida previa del mismo dia se ignoraron.)")

    con.close()

    # A3500 del BCRA (2026-10-14): ventana de los ultimos dias, se
    # completa sola si un dia fallo. Un error aca no tumba los precios
    # ya commiteados; queda en el log del job.
    try:
        cargar_usd_a3500.actualizar(
            DB_PATH, (date.today() - timedelta(days=cargar_usd_a3500.DIAS_VENTANA)).isoformat()
        )
    except Exception as e:
        print(f"AVISO: no se pudo actualizar USD_A3500 desde el BCRA ({type(e).__name__}: {e}).")


if __name__ == "__main__":
    importar()
