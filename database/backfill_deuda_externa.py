"""
Backfill de `deuda_externa_pct` en `macro_historico` (Epica 12 Parte 3,
2026-09-13). Fuente: Ministerio de Economia / Secretaria de Finanzas,
reporte trimestral de deuda publica -- hoja "A.4.5 Deuda Bruta de la
Administracion Central - Por residencia del tenedor", serie ya
COMPLETA desde 1994 en un solo archivo (no hace falta descargar cada
trimestre por separado). Detras de esto esta el INDEC (Direccion
Nacional de Cuentas Internacionales, metodologia de balanza de pagos).

IMPORTANTE -- por que este indicador NO entra al enum de umbrales de
`tesis_umbrales` (a diferencia de riesgo_pais/rem/inflacion): esta
serie es AGREGADA (toda la deuda publica), no por bono. El analisis
puntual que motivo esta epica (73-78% en Globales comparables vs 6-27%
en Bonares) sale de informes de research de bancos y brokers locales
que procesan estos mismos datos del INDEC con su propia metodologia
para estimarlo por especie -- confirmado que no existe una fuente
publica con ese nivel de detalle por ISIN (investigado 2026-09-13, ver
ESTADO_PWOS.md). Este backfill solo carga el agregado pais, como
contexto macro informativo -- ver tesis_umbrales.py para el enum real
de indicadores automatizables.

Periodicidad real: TRIMESTRAL, con ~2,5-3 meses de rezago (el reporte
de un trimestre no aparece hasta bien entrado el trimestre siguiente)
-- mas espaciado que "revision mensual". No wireado a ningun refresco
automatico (30 min/diario) por ese motivo -- correr esto a mano cada
tanto (o wirear un trigger trimestral en n8n si el usuario lo pide).

Uso:
    python backfill_deuda_externa.py
"""
import io
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import requests
import configuracion

DB_PATH = configuracion.ruta_db()
URL_BASE = "https://www.argentina.gob.ar/sites/default/files/deuda_publica_{fecha}.xlsx"
_UA = {"User-Agent": "Mozilla/5.0 (PWOS backfill_deuda_externa)"}
CLAVE = "deuda_externa_pct"


def _fechas_fin_de_trimestre_candidatas(hoy: date, cuantos: int = 8):
    """Genera fechas de cierre de trimestre (31-03/30-06/30-09/31-12)
    hacia atras desde `hoy`, mas recientes primero -- el reporte del
    trimestre en curso o el inmediato anterior puede no estar
    publicado todavia (rezago de ~2,5-3 meses)."""
    cierres = [(3, 31), (6, 30), (9, 30), (12, 31)]
    anio = hoy.year
    candidatas = []
    while len(candidatas) < cuantos:
        for mes, dia in reversed(cierres):
            f = date(anio, mes, dia)
            if f <= hoy:
                candidatas.append(f)
        anio -= 1
    candidatas.sort(reverse=True)
    return candidatas[:cuantos]


def _descargar_ultimo_reporte() -> tuple[bytes, date]:
    """Prueba las fechas de cierre de trimestre mas recientes hasta
    encontrar un archivo publicado (HEAD, sin bajar el contenido de
    los que fallan) -- el patron de URL es predecible pero el rezago
    de publicacion varia, no se puede asumir cual es "el ultimo"."""
    for fecha in _fechas_fin_de_trimestre_candidatas(date.today()):
        url = URL_BASE.format(fecha=fecha.strftime("%d-%m-%Y"))
        head = requests.head(url, headers=_UA, timeout=15, allow_redirects=True)
        if head.status_code == 200:
            r = requests.get(url, headers=_UA, timeout=60)
            r.raise_for_status()
            return r.content, fecha
    raise RuntimeError("No se encontro ningun reporte trimestral de deuda publicado en los ultimos 2 anios")


def obtener_deuda_externa_historico() -> list[tuple[str, float]]:
    """Devuelve [(fecha_iso, pct), ...] -- pct en PORCENTAJE (0-100,
    no fraccion 0-1) para consistencia con el resto de `macro_historico`
    (riesgo_pais en pb, inflacion en %, etc.)."""
    contenido, fecha_reporte = _descargar_ultimo_reporte()
    print(f"Reporte usado: cierre {fecha_reporte.isoformat()} (el mas reciente publicado)")

    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    ws = wb["A.4.5"]
    filas = []
    for row in ws.iter_rows(values_only=True):
        # La hoja trae una columna A vacia (indentacion visual) antes de
        # "Periodo" -- Periodo queda en indice 1, "% Deuda Externa" en
        # indice 5, no 0/4 (confirmado inspeccionando el archivo real,
        # no asumido).
        if not row or row[1] is None or not hasattr(row[1], "date"):
            continue
        fecha_periodo = row[1].date().isoformat()
        pct_externa = row[5] if len(row) > 5 else None
        if isinstance(pct_externa, (int, float)):
            filas.append((fecha_periodo, round(pct_externa * 100, 3)))
    wb.close()
    return filas


def backfill(db_path: Path = DB_PATH) -> None:
    filas = obtener_deuda_externa_historico()
    con = sqlite3.connect(db_path)
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN;")
        cur = con.executemany(
            "INSERT OR IGNORE INTO macro_historico (clave, fecha, valor) VALUES (?, ?, ?);",
            [(CLAVE, fecha, valor) for fecha, valor in filas],
        )
        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")
        con.execute("COMMIT;")
        print(f"{len(filas)} filas descargadas, {cur.rowcount if cur.rowcount >= 0 else '?'} nuevas insertadas.")
        n, mn, mx = con.execute(
            "SELECT COUNT(*), MIN(fecha), MAX(fecha) FROM macro_historico WHERE clave = ?;", (CLAVE,)
        ).fetchone()
        print(f"  {CLAVE}: {n} filas totales ({mn} a {mx})")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"BACKFILL ABORTADO, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    backfill()
