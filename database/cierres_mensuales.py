"""
PWOS -- carga incremental de `cierres_mensuales` desde la solapa CUENTAS de la
planilla de cierres del usuario (patron del archivo en la configuracion:
cierres_mensuales.patron_xlsx), bajada como Excel (2026-10-27).

Reemplaza la carga unica del 2026-09-03 (`cargar_cierres_mensuales_sheet_2026-09-03.py`,
numeros escritos a mano en el script) por una lectura del archivo: el usuario carga
el mes en el sheet, lo baja a `database/descargas/` y corre esto (a mano o desde
n8n). Es la fuente de la curva de Rendimiento (ADR-023).

Lectura de CUENTAS: una matriz por anio (fila "MESES" con un encabezado por
mes cada 2 columnas); de cada mes se toman, en la columna del encabezado, las
filas SUB TOTAL [USD], TRABAJO, GASTOS y MEP. Un mes sin cargar (SUB TOTAL en
0 o #DIV/0!, sin MEP) se ignora; uno a medias frena.

Convencion de fecha (verificada 2026-10-27 contra el MEP diario de `precios` y
la fecha real de la compra del auto): la columna de un mes refleja la
actividad DE ESE MES y la foto se toma a principios del mes siguiente
(sep-26: MEP 1.556 = MEP del 01/10). Lo que ADR-023 decia al reves.

Por mes:
  patrimonio_total_usd = SUB TOTAL [USD]
  aportes_netos_usd    = (TRABAJO - GASTOS) / MEP - pagos de deuda del mes
  rendimiento_usd      = patrimonio_m - aporte_m - patrimonio_{m-1}
Los pagos de deuda (`pagos_pasivo`, parte propia) son un RETIRO, no una
perdida de inversion: la plata sale de las cuentas que suma CUENTAS y paga un
pasivo que el sheet no lleva (misma regla que la compra del auto, ADR-023;
decision del usuario 2026-10-27). No hay que sumarlos tambien a GASTOS del sheet:
se restarian dos veces.

Escritura: solo inserta meses nuevos. Si un mes ya cargado da distinto (el
sheet se corrigio, o cambio un pago de deuda), FRENA y lo muestra: no pisa
nada solo. Backup + integrity_check + una transaccion.

Uso (parado en database/):
    python cierres_mensuales.py                 # dry run con el Excel mas nuevo de descargas/
    python cierres_mensuales.py --aplicar       # carga los meses nuevos
    python cierres_mensuales.py --xlsx RUTA --db RUTA
"""

import argparse
import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import openpyxl

import configuracion

DB_PATH = configuracion.ruta_db()
DESCARGAS = Path(__file__).parent / "descargas"
PATRON_XLSX = configuracion.get("cierres_mensuales", "patron_xlsx", default="*cierres*.xlsx")
HOJA = "CUENTAS"
FILAS = {"SUB TOTAL [USD]": "patrimonio_usd", "TRABAJO": "trabajo_ars", "GASTOS": "gastos_ars", "MEP": "mep"}
MESES_ES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
            "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12}
TOLERANCIA_USD = 0.015  # los montos se guardan redondeados a 2 decimales


class CierresError(Exception):
    """El sheet o la base no permiten cargar sin adivinar (lo ambiguo bloquea)."""


def xlsx_mas_nuevo(carpeta: Path = DESCARGAS) -> Path:
    archivos = sorted(carpeta.glob(PATRON_XLSX), key=lambda p: p.stat().st_mtime)
    if not archivos:
        raise CierresError(f"no hay ningun '{PATRON_XLSX}' en {carpeta}")
    return archivos[-1]


def _mes_de_encabezado(valor) -> tuple[int, int] | None:
    """'ene-26' llega como fecha (Excel lo leyo como 26/01 del anio en curso) o
    como texto ('sep - 24'). Devuelve (anio, mes) o None si no es un mes."""
    if isinstance(valor, datetime):
        if valor.day != valor.year % 100:
            raise CierresError(f"encabezado de mes inesperado: {valor:%Y-%m-%d} (dia != anio)")
        return valor.year, valor.month
    if isinstance(valor, str):
        partes = [p.strip().lower() for p in valor.replace("-", " ").split()]
        if len(partes) == 2 and partes[0][:3] in MESES_ES and partes[1].isdigit():
            return 2000 + int(partes[1]), MESES_ES[partes[0][:3]]
    return None


def _numero(valor) -> float | None:
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else None


def leer_cuentas(ruta: Path) -> list[dict]:
    """Meses cargados en CUENTAS, ordenados: [{anio, mes, patrimonio_usd,
    trabajo_ars, gastos_ars, mep}]."""
    wb = openpyxl.load_workbook(ruta, data_only=True, read_only=True)
    if HOJA not in wb.sheetnames:
        raise CierresError(f"el archivo no tiene la solapa {HOJA}")
    filas = [list(f) for f in wb[HOJA].iter_rows(values_only=True)]
    wb.close()

    def etiqueta(f):
        return str(f[1]).strip() if len(f) > 1 and f[1] is not None else ""

    inicios = [i for i, f in enumerate(filas) if etiqueta(f) == "MESES"]
    if not inicios:
        raise CierresError("no se encontro ninguna fila 'MESES' en CUENTAS")
    meses = {}
    for n, ini in enumerate(inicios):
        fin = inicios[n + 1] if n + 1 < len(inicios) else len(filas)
        bloque = {etiqueta(filas[i]): filas[i] for i in range(ini + 1, fin) if etiqueta(filas[i]) in FILAS}
        faltan = set(FILAS) - set(bloque)
        if faltan:
            raise CierresError(f"a la matriz de la fila {ini + 1} le faltan las filas {sorted(faltan)}")
        for col, valor in enumerate(filas[ini]):
            am = _mes_de_encabezado(valor)
            if am is None:
                continue
            datos = {campo: _numero(bloque[nombre][col] if col < len(bloque[nombre]) else None)
                     for nombre, campo in FILAS.items()}
            if not datos["patrimonio_usd"] and not datos["mep"]:
                continue  # mes todavia no cargado
            if any(v is None for v in datos.values()) or datos["mep"] <= 0 or datos["patrimonio_usd"] <= 0:
                raise CierresError(f"{am[1]:02d}/{am[0]}: mes a medio cargar en CUENTAS ({datos})")
            if am in meses:
                raise CierresError(f"{am[1]:02d}/{am[0]} aparece dos veces en CUENTAS")
            meses[am] = {"anio": am[0], "mes": am[1], **datos}
    orden = sorted(meses)
    for (a0, m0), (a1, m1) in zip(orden, orden[1:]):
        if (a1 * 12 + m1) - (a0 * 12 + m0) != 1:
            raise CierresError(f"hueco en CUENTAS entre {m0:02d}/{a0} y {m1:02d}/{a1}")
    return [meses[k] for k in orden]


def retiros_deuda_usd(con, anio: int, mes: int) -> float:
    desde = f"{anio:04d}-{mes:02d}-01"
    hasta = f"{anio + (mes == 12):04d}-{mes % 12 + 1:02d}-01"
    return con.execute("SELECT COALESCE(SUM(monto_usd), 0) FROM pagos_pasivo WHERE fecha >= ? AND fecha < ?;",
                       (desde, hasta)).fetchone()[0]


def calcular(con, meses: list[dict]) -> list[dict]:
    """Agrega aporte, retiros de deuda y rendimiento a cada mes leido."""
    out = []
    for i, m in enumerate(meses):
        retiros = round(retiros_deuda_usd(con, m["anio"], m["mes"]), 2)
        aporte = round((m["trabajo_ars"] - m["gastos_ars"]) / m["mep"] - retiros, 2)
        patrimonio = round(m["patrimonio_usd"], 2)
        previo = out[i - 1]["patrimonio_total_usd"] if i else None
        rendimiento = round(patrimonio - aporte - previo, 2) if previo is not None else 0.0
        out.append({**m, "patrimonio_total_usd": patrimonio, "aportes_netos_usd": aporte,
                    "retiros_deuda_usd": retiros, "rendimiento_usd": rendimiento})
    return out


def planificar(con, calculados: list[dict]) -> dict:
    """Que habria que insertar, SIN escribir. `distintos` = meses ya cargados
    que hoy dan otro numero (bloquean la carga)."""
    cargados = {(a, m): (p, ap, r) for a, m, p, ap, r in con.execute(
        "SELECT anio, mes, patrimonio_total_usd, aportes_netos_usd, rendimiento_usd FROM cierres_mensuales;")}
    nuevos, iguales, distintos = [], 0, []
    for c in calculados:
        viejo = cargados.get((c["anio"], c["mes"]))
        if viejo is None:
            nuevos.append(c)
            continue
        nuevo = (c["patrimonio_total_usd"], c["aportes_netos_usd"], c["rendimiento_usd"])
        # el primer mes del sheet no tiene previo: su rendimiento no se compara
        pares = list(zip(viejo, nuevo))[:2] if c is calculados[0] else list(zip(viejo, nuevo))
        if all(v is not None and abs(v - n) <= TOLERANCIA_USD for v, n in pares):
            iguales += 1
        else:
            distintos.append({"anio": c["anio"], "mes": c["mes"], "en_base": viejo, "en_sheet": nuevo})
    faltan_en_sheet = sorted(set(cargados) - {(c["anio"], c["mes"]) for c in calculados})
    return {"nuevos": nuevos, "iguales": iguales, "distintos": distintos, "faltan_en_sheet": faltan_en_sheet}


def _backup(db_path: Path) -> Path:
    destino = db_path.parent / "backups" / f"pwos_backup_pre-cierres-mensuales_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, destino)
    c = sqlite3.connect(destino)
    ok = c.execute("PRAGMA integrity_check;").fetchone()[0]
    c.close()
    if ok != "ok":
        raise CierresError(f"backup corrupto ({ok}), no se escribe nada")
    return destino


def aplicar(db_path: Path, plan: dict) -> dict:
    """Inserta los meses nuevos. Frena si hay meses ya cargados que cambiaron."""
    if plan["distintos"]:
        raise CierresError("hay meses ya cargados que hoy dan distinto: revisarlos antes de cargar "
                           f"({[(d['mes'], d['anio']) for d in plan['distintos']]})")
    if not plan["nuevos"]:
        return {"insertados": 0, "backup": None}
    backup = _backup(db_path)
    con = sqlite3.connect(db_path)
    try:
        con.execute("BEGIN;")
        for c in plan["nuevos"]:
            detalle = {"fuente": c.get("fuente", "sheet CUENTAS"), "trabajo_ars": c["trabajo_ars"], "gastos_ars": c["gastos_ars"],
                       "mep": c["mep"], "retiros_deuda_usd": c["retiros_deuda_usd"]}
            con.execute(
                "INSERT INTO cierres_mensuales (anio, mes, patrimonio_total_usd, aportes_netos_usd, rendimiento_usd, "
                "detalle_json) VALUES (?, ?, ?, ?, ?, ?);",
                (c["anio"], c["mes"], c["patrimonio_total_usd"], c["aportes_netos_usd"], c["rendimiento_usd"],
                 json.dumps(detalle, ensure_ascii=False)))
        ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
        if ok != "ok":
            raise CierresError(f"integrity_check={ok}")
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
    return {"insertados": len(plan["nuevos"]), "backup": backup.name}


def revisar(xlsx: Path | None = None, db_path: Path = DB_PATH, escribir: bool = False) -> dict:
    """Todo el proceso; lo usan la linea de comandos y el endpoint de n8n."""
    xlsx = xlsx or xlsx_mas_nuevo()
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        calculados = calcular(con, leer_cuentas(xlsx))
        plan = planificar(con, calculados)
    finally:
        con.close()
    res = {"archivo": xlsx.name, "meses_en_sheet": len(calculados), "iguales": plan["iguales"],
           "nuevos": [{k: c[k] for k in ("anio", "mes", "patrimonio_total_usd", "aportes_netos_usd",
                                         "retiros_deuda_usd", "rendimiento_usd")} for c in plan["nuevos"]],
           "distintos": plan["distintos"], "faltan_en_sheet": plan["faltan_en_sheet"], "aplicado": False}
    if escribir:
        res |= aplicar(db_path, plan)
        res["aplicado"] = True
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--xlsx", type=Path)
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--aplicar", action="store_true")
    a = ap.parse_args()
    try:
        r = revisar(a.xlsx, a.db, a.aplicar)
    except CierresError as e:
        raise SystemExit(f"FRENADO: {e}")
    print(f"Archivo: {r['archivo']} | meses en el sheet: {r['meses_en_sheet']} | iguales a la base: {r['iguales']}")
    for c in r["nuevos"]:
        print(f"  NUEVO {c['mes']:02d}/{c['anio']}: patrimonio {c['patrimonio_total_usd']:.2f} | aporte "
              f"{c['aportes_netos_usd']:.2f} (retiros de deuda {c['retiros_deuda_usd']:.2f}) | rendimiento "
              f"{c['rendimiento_usd']:.2f}")
    for d in r["distintos"]:
        print(f"  DISTINTO {d['mes']:02d}/{d['anio']}: base {d['en_base']} vs sheet {d['en_sheet']}")
    if r["faltan_en_sheet"]:
        print(f"  En la base pero no en el sheet: {r['faltan_en_sheet']}")
    print(f"Aplicado: {r['aplicado']}" + (f" ({r['insertados']} insertados, backup {r['backup']})" if r["aplicado"] else
                                          " (dry run; --aplicar para cargar)"))


if __name__ == "__main__":
    main()
