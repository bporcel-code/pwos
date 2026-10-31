"""
PWOS -- Cargador de indicadores macro con historial (Epica 21, 2026-09-29).

Trae series de FRED, BCRA (API de estadisticas + Excel del ITCRM),
yfinance y argentinadatos, y las guarda en `macro_historico` con
`indicadores_macro.upsert_serie` (upsert idempotente: la ultima version
de un dato revisado pisa a la anterior, un NaN nunca pisa, cada carga
informa filas nuevas / actualizadas).

Organizado por GRUPOS: un grupo es una descarga que produce 1 o mas
indicadores (ej. EFFR + CPILFESL -> effr, core_cpi_ia, tasa_real_corto).
Un grupo que falla no frena a los demas; su error queda en el resumen.

Dos modos:
- refresco (default): lo reciente de cada fuente (ultimos ~800 dias,
  alcanza para variaciones interanuales y para recoger revisiones).
  Incluye los indicadores existentes de yfinance/argentinadatos, que
  cargar_benchmarks_macro.py (/api/refrescar-macro, 16 h) ya escribe con
  el valor de ese momento: este refresco corre despues del cierre de NY
  y deja el cierre real (el cambio queda contado como revision).
- backfill (--backfill): historia completa que la fuente permita de
  verdad. Nunca se interpola ni se inventa hacia atras.

API key de FRED: variable de entorno FRED_API_KEY (dentro del contenedor
pwos-api) y si no esta, Windows Credential Manager via keyring (servicio
"pwos", usuario "fred_api_key") para correr scripts sueltos en la PC.
Nunca se imprime: los errores se enmascaran antes de mostrarse.

Salida: una linea por indicador + al final una linea
`RESUMEN_JSON: {...}` que parsea POST /api/refrescar-indicadores-macro.

Uso:
    python cargar_indicadores_macro.py                        # refresco, todas las tandas
    python cargar_indicadores_macro.py --tanda 1              # refresco, solo la tanda 1
    python cargar_indicadores_macro.py --backfill             # backfill de todo lo que lo admite
    python cargar_indicadores_macro.py --backfill --indicador curva_10y_2y
    python cargar_indicadores_macro.py --db COPIA.db          # contra una copia
"""
import argparse
import io
import json
import re
import os
import shutil
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

import requests

import indicadores_macro as im
import configuracion

DB_PATH = configuracion.ruta_db()

DIAS_REFRESCO = 800
BCRA_API = "https://api.bcra.gob.ar/estadisticas/v4.0/monetarias"
BCRA_ID_RESERVAS = 1    # "Reservas internacionales" (MUSD)
BCRA_ID_COMPRAS = 78    # "Variacion de reservas internacionales por compra de divisas" (MUSD)
BCRA_ID_CER = 30        # "Coeficiente de estabilizacion de referencia" (base 2.2.02=1), diaria
BCRA_ID_PF_30D = 12     # "Tasa de interes de depositos a 30 dias de plazo en entidades financieras" (% TNA), diaria
BCRA_ID_PF_USD = 1193   # "Tasa de interes de depositos a plazo fijo en dolares" (% TNA), diaria
ITCRM_URL = "https://www.bcra.gob.ar/Pdfs/PublicacionesEstadisticas/ITCRMSerie.xlsx"
ITCRM_HOJA = "ITCRM y bilaterales"
MEP_HIST_URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/bolsa"
# Tanda 2. El Excel de Shiller se publica en un servicio de archivos con un
# id en la ruta que cambia al republicarlo: se busca el link en la pagina
# en cada corrida (la copia de Yale quedo congelada en 2023-09).
SHILLER_PAGINA = "https://shillerdata.com/"
EMAE_URL = "https://www.indec.gob.ar/ftp/cuadros/economia/sh_emae_mensual_base2004.xls"
# Tanda 3.
IMIG_CSV_URL = "https://infra.datos.gob.ar/catalog/sspm/dataset/452/distribution/452.3/download/imig-mensual.csv"
FACTSET_URL = ("https://advantage.factset.com/hubfs/Website/Resources%20Section/Research%20Desk/"
               "Earnings%20Insight/EarningsInsight_{:%m%d%y}.pdf")
FACTSET_SEMANAS_REFRESCO = 3
FACTSET_ANIOS_BACKFILL = 10
MESES_ES = {m: i for i, m in enumerate(
    ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
     "septiembre", "octubre", "noviembre", "diciembre"], start=1)}
_UA = {"User-Agent": "Mozilla/5.0 (PWOS indicadores macro)"}


# ------------------------------------------------------------ transformaciones
def _mes(fecha: str) -> str:
    return fecha[:7]


def promedio_mensual(serie) -> dict[str, float]:
    """Serie diaria -> {'YYYY-MM': promedio simple de las observaciones del mes}."""
    acum: dict[str, list[float]] = {}
    for f, v in serie:
        acum.setdefault(_mes(im.fecha_iso(f)), []).append(v)
    return {m: sum(vs) / len(vs) for m, vs in sorted(acum.items())}


def variacion_interanual_mensual(serie) -> dict[str, float]:
    """Serie mensual de un INDICE -> {'YYYY-MM': variacion % vs. el mismo
    mes del anio anterior}. Sin base un anio antes, el mes no se calcula."""
    por_mes = {_mes(im.fecha_iso(f)): v for f, v in serie}
    out = {}
    for m, v in sorted(por_mes.items()):
        base = por_mes.get(f"{int(m[:4]) - 1}{m[4:]}")
        if base:
            out[m] = (v / base - 1) * 100
    return out


def promedio_3m_variacion(serie) -> list[tuple[str, float]]:
    """Serie mensual de un NIVEL (ej. PAYEMS) -> promedio de las 3 ultimas
    variaciones mensuales. El promedio de (m - m-1), (m-1 - m-2), (m-2 - m-3)
    se simplifica a (nivel de m - nivel de m-3) / 3: solo hacen falta esos
    dos niveles, asi que un mes intermedio sin publicar no rompe la cuenta.
    Si falta m o m-3, el mes no se calcula (nunca se interpola). Como cada
    refresco recalcula con la ultima version de FRED, un mes revisado
    actualiza los promedios que lo usan (upsert_serie los pisa)."""
    por_mes = {_mes(im.fecha_iso(f)): v for f, v in serie}
    out = []
    for m, v in sorted(por_mes.items()):
        anio, mes = int(m[:4]), int(m[5:7])
        anio_base, mes0 = divmod(anio * 12 + mes - 1 - 3, 12)
        base = por_mes.get(f"{anio_base}-{mes0 + 1:02d}")
        if base is not None:
            out.append((f"{m}-01", (v - base) / 3))
    return out


def tasa_real_mensual(effr_mensual: dict[str, float], core_ia: dict[str, float]) -> list[tuple[str, float]]:
    """EFFR promedio del mes - inflacion core interanual del MISMO mes. Si
    el CPI de ese mes todavia no salio (~2 semanas de rezago), el mes NO se
    calcula: nunca se usa el CPI del mes anterior en silencio."""
    return [(f"{m}-01", effr_mensual[m] - core_ia[m]) for m in sorted(effr_mensual) if m in core_ia]


def interanual_diaria(serie, tolerancia_dias: int = 10) -> list[tuple[str, float]]:
    """Serie diaria de un NIVEL -> variacion % contra el dato de 365 dias
    antes (o el habil mas cercano hacia atras, hasta `tolerancia_dias`).
    Mismo criterio que cargar_benchmarks_macro._fecha_mas_cercana_a_o_antes."""
    por_fecha = {im.fecha_iso(f): v for f, v in serie}
    out = []
    for f in sorted(por_fecha):
        objetivo = date.fromisoformat(f) - timedelta(days=365)
        for delta in range(tolerancia_dias):
            base = por_fecha.get((objetivo - timedelta(days=delta)).isoformat())
            if base:
                out.append((f, (por_fecha[f] / base - 1) * 100))
                break
    return out


def _a_mensual_iso(por_mes: dict[str, float]) -> list[tuple[str, float]]:
    return [(f"{m}-01", v) for m, v in sorted(por_mes.items())]


def cociente(num, den, factor: float = 1.0) -> list[tuple[str, float]]:
    """num/den x factor en las fechas que tienen AMBAS series (sin
    interpolar la que falte). Ej.: intereses federales / PBI x 100."""
    por_fecha = {im.fecha_iso(f): v for f, v in den}
    return [(im.fecha_iso(f), v / por_fecha[im.fecha_iso(f)] * factor)
            for f, v in num if por_fecha.get(im.fecha_iso(f))]


def _norm(texto) -> str:
    return (str(texto) if texto is not None else "").strip().lower().translate(
        str.maketrans("áéíóúñÁÉÍÓÚÑ", "aeiounAEIOUN"))


def parsear_shiller(filas) -> list[tuple[str, float]]:
    """Filas de la hoja 'Data' de ie_data.xls -> [(YYYY-MM-01, CAPE)]. La
    columna se busca por su encabezado ('CAPE', sin el 'TR CAPE') SOLO en
    la fila que empieza con 'Date': el encabezado ocupa varias filas y mas
    arriba hay otra celda 'CAPE' (del titulo 'Excess CAPE Yield', col. 16)
    -- tomarla daba valores sin sentido, encontrado en el ensayo 2026-09-30.
    La fecha viene como YYYY.MM en float (2026.1 = octubre). Si no aparece
    el encabezado, aborta en vez de leer otra columna en silencio."""
    col = None
    for fila in filas[:15]:
        textos = [_norm(c) for c in fila]
        if textos and textos[0] == "date" and "cape" in textos:
            col = textos.index("cape")
            break
    if col is None:
        raise RuntimeError("no se encontro la columna 'CAPE' en la hoja Data de Shiller")
    out = []
    for fila in filas:
        f = fila[0] if fila else None
        if not isinstance(f, float) or f < 1800:
            continue
        anio, mes = int(f), round((f - int(f)) * 100)
        v = im.float_limpio(fila[col]) if col < len(fila) else None
        if 1 <= mes <= 12 and v is not None:
            out.append((f"{anio}-{mes:02d}-01", v))
    return out


def parsear_emae(filas) -> dict[str, list]:
    """Filas de sh_emae_mensual_base2004.xls -> nivel desestacionalizado e
    i.a. de la serie original. El anio aparece solo en la fila de enero
    (se arrastra); las columnas se buscan por el texto del encabezado. La
    i.a. del archivo trae 0,0 de relleno el primer anio (sin base): solo se
    toma si existe el mismo mes del anio anterior en la serie original."""
    cols, inicio = None, None
    for i, fila in enumerate(filas[:15]):
        textos = [_norm(c) for c in fila]
        if textos and textos[0].startswith("per"):
            def buscar(fragmento):
                return next((j for j, t in enumerate(textos) if fragmento in t), None)
            cols = {"original": buscar("serie original"), "ia": buscar("igual periodo del ano anterior"),
                    "desest": buscar("desestacionalizada")}
            inicio = i + 1
            break
    if not cols or None in cols.values():
        raise RuntimeError(f"no se encontraron los encabezados del EMAE (encontrado: {cols})")

    anio, original, desest, ia = None, {}, [], []
    for fila in filas[inicio:]:
        if fila and isinstance(fila[0], float) and fila[0] > 1990:
            anio = int(fila[0])
        mes = MESES_ES.get(_norm(fila[1]) if len(fila) > 1 else "")
        if anio is None or mes is None:
            continue
        fecha = f"{anio}-{mes:02d}-01"
        v_orig = im.float_limpio(fila[cols["original"]])
        if v_orig is not None:
            original[(anio, mes)] = v_orig
        v_des = im.float_limpio(fila[cols["desest"]])
        if v_des is not None:
            desest.append((fecha, v_des))
        v_ia = im.float_limpio(fila[cols["ia"]])
        if v_ia is not None and (anio - 1, mes) in original:
            ia.append((fecha, v_ia))
    return {"emae_desest": desest, "emae_ia": ia}


# ------------------------------------------------------------ FRED
class FaltaClave(RuntimeError):
    pass


def fred_api_key() -> str | None:
    clave = os.environ.get("FRED_API_KEY")
    if clave:
        return clave
    try:
        import keyring
        return keyring.get_password("pwos", "fred_api_key")
    except Exception:
        # Dentro del contenedor Linux keyring no tiene backend: no es un
        # error propio, simplemente no hay clave por esta via.
        return None


def enmascarar(texto: str) -> str:
    clave = fred_api_key()
    return texto.replace(clave, "****") if clave else texto


_FRED = None


def _fred():
    global _FRED
    if _FRED is None:
        clave = fred_api_key()
        if not clave:
            raise FaltaClave("falta FRED_API_KEY (variable de entorno o keyring pwos/fred_api_key)")
        from fredapi import Fred
        _FRED = Fred(api_key=clave)
    return _FRED


def fred_serie(serie_id: str, desde: str | None) -> list[tuple[str, float]]:
    s = _fred().get_series(serie_id, observation_start=desde)
    return [(ts.date().isoformat(), float(v)) for ts, v in s.dropna().items()]


# ------------------------------------------------------------ BCRA / ITCRM
def bcra_serie(id_variable: int, desde: str | None) -> list[tuple[str, float]]:
    """API de estadisticas v4 del BCRA, paginada (limite 3000 por pedido)."""
    params = {"limit": 3000, "offset": 0, "hasta": date.today().isoformat()}
    if desde:
        params["desde"] = desde
    out = []
    while True:
        r = requests.get(f"{BCRA_API}/{id_variable}", params=params, headers=_UA, timeout=60)
        r.raise_for_status()
        j = r.json()
        res = j.get("results") or []
        filas = res[0]["detalle"] if res and isinstance(res[0], dict) and "detalle" in res[0] else res
        out += [(f["fecha"], f["valor"]) for f in filas if f.get("valor") is not None]
        total = (j.get("metadata") or {}).get("resultset", {}).get("count", 0)
        params["offset"] += params["limit"]
        if not filas or params["offset"] >= total:
            break
    return out


def itcrm_serie(desde: str | None) -> list[tuple[str, float]]:
    """ITCRM diario del Excel del BCRA. Busca el encabezado 'Periodo' /
    'ITCRM' por texto (no por numero de fila fijo): si el BCRA reordena el
    archivo, aborta en vez de leer una columna equivocada en silencio.
    Las fechas futuras (si las hubiera) se descartan."""
    import openpyxl

    r = requests.get(ITCRM_URL, headers=_UA, timeout=90)
    r.raise_for_status()
    wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
    filas = list(wb[ITCRM_HOJA].iter_rows(values_only=True))
    wb.close()

    col = None
    for i, fila in enumerate(filas[:10]):
        textos = [str(c).strip().lower() if c is not None else "" for c in fila]
        if textos and textos[0].startswith("per") and "itcrm" in textos:
            col, inicio = textos.index("itcrm"), i + 1
            break
    if col is None:
        raise RuntimeError(f"no se encontro el encabezado 'Periodo'/'ITCRM' en la hoja {ITCRM_HOJA!r}")

    hoy = date.today()
    out = []
    for fila in filas[inicio:]:
        f, v = fila[0], fila[col] if col < len(fila) else None
        if not isinstance(f, datetime) or im.float_limpio(v) is None:
            continue
        if f.date() > hoy or (desde and f.date().isoformat() < desde):
            continue
        out.append((f.date().isoformat(), float(v)))
    return out


# ------------------------------------------------------------ yfinance / argentinadatos
def yf_cierres(ticker: str, desde: str | None) -> dict[str, float]:
    """Cierres diarios. Backfill (`desde` None): toda la historia. Refresco:
    desde `desde` -- corre despues del cierre de NY y reemplaza el valor
    intradiario que dejo el refresco de las 16 h de cargar_benchmarks_macro.py."""
    import yfinance as yf
    t = yf.Ticker(ticker)
    hist = t.history(start=desde, auto_adjust=False) if desde else t.history(period="max", auto_adjust=False)
    return {ts.date().isoformat(): float(c) for ts, c in hist["Close"].dropna().items()}


def yf_ratio(a: str, b: str, desde: str | None, factor: float = 1.0) -> list[tuple[str, float]]:
    """Mismo calculo que el refresco diario (_ratio_cierre_yfinance de
    cargar_benchmarks_macro.py), sobre todas las fechas comunes."""
    sa, sb = yf_cierres(a, desde), yf_cierres(b, desde)
    return [(f, sa[f] / sb[f] * factor) for f in sorted(set(sa) & set(sb)) if sb[f]]


def _desde_menos_un_anio(desde: str | None) -> str | None:
    """Las variaciones interanuales necesitan 1 anio de base antes de `desde`."""
    return (date.fromisoformat(desde) - timedelta(days=380)).isoformat() if desde else None


def _solo_desde(puntos, desde: str | None):
    return [p for p in puntos if not desde or p[0] >= desde]


def mep_argentinadatos() -> list[tuple[str, float]]:
    r = requests.get(MEP_HIST_URL, timeout=30)
    r.raise_for_status()
    return [(f["fecha"], f["venta"]) for f in r.json() if f.get("venta")]


# ------------------------------------------------------------ Tanda 3: IMIG, vencimientos, FactSet
def primario_sobre_ingresos_12m(filas: list[dict]) -> list[tuple[str, float]]:
    """Filas del CSV mensual del IMIG -> resultado primario acumulado 12
    meses / ingresos totales acumulados 12 meses x 100. El CSV no trae
    "ingresos totales": son los rubros de 'iva_neto_reintegros' a
    'ingresos_capital' (16 al 2026-09-30). Se valida la identidad
    ingresos - gastos primarios = resultado primario publicado en CADA mes;
    si no cierra (cambio de estructura del CSV), aborta en vez de calcular
    mal. Sin 12 meses consecutivos, el mes no se calcula."""
    cols = list(filas[0])
    ing = cols[cols.index("iva_neto_reintegros"):cols.index("ingresos_capital") + 1]
    gas = cols[cols.index("jubilaciones_pensiones_contributivas"):cols.index("fondo_federal_solidario") + 1]
    meses = []
    for f in filas:
        try:
            i = sum(float(f[c] or 0) for c in ing)
            g = sum(float(f[c] or 0) for c in gas)
            rp = float(f["resultado_primario"])
        except (ValueError, TypeError):
            continue
        if abs(i - g - rp) > 1e-6 * max(abs(i), 1):
            raise RuntimeError(f"IMIG {f['indice_tiempo']}: ingresos - gastos != resultado primario (cambio el CSV?)")
        meses.append((f["indice_tiempo"][:10], rp, i))
    out = []
    for k in range(11, len(meses)):
        ventana = meses[k - 11:k + 1]
        consecutivos = all(
            _indice_mes_iso(ventana[j + 1][0]) - _indice_mes_iso(ventana[j][0]) == 1 for j in range(11))
        ingresos = sum(x[2] for x in ventana)
        if consecutivos and ingresos:
            out.append((meses[k][0], sum(x[1] for x in ventana) / ingresos * 100))
    return out


def _indice_mes_iso(fecha: str) -> int:
    return int(fecha[:4]) * 12 + int(fecha[5:7])


def imig_primario(desde: str | None) -> list[tuple[str, float]]:
    import csv
    r = requests.get(IMIG_CSV_URL, headers=_UA, timeout=60)
    r.raise_for_status()
    return _solo_desde(primario_sobre_ingresos_12m(list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))), desde)


# Instrumento en moneda extranjera = su nombre trae la moneda entre barras
# ('BONO GLOBAL/U$S/STEP UP/09-07-2030', 'Par/EUR/T.Fija/2038'). No entran
# '$' (pesos) ni 'DLK' (dollar-linked, se paga en pesos). Las filas hijas
# ('Emision Canje 2005') no traen moneda y no se suman (su total ya esta en
# la fila del instrumento). Letras intransferibles: son con el BCRA, afuera.
_MONEDA_EXTRANJERA = re.compile(r"/(U\$S|USD|EUR|JPY|CHF)[^/]*/")


def parsear_vencimientos_me(hojas: list[list], fecha_corte: date) -> float:
    """Hojas del reporte trimestral de deuda -> capital + interes de los
    titulos publicos en moneda extranjera que vencen en los 12 meses
    siguientes a `fecha_corte`, en MUSD. Usa las hojas de perfil MENSUAL de
    vencimientos de capital y de interes desagregadas por instrumento
    (A.3.2-A.3.5 en 2026): las columnas son meses (fechas). Aborta si no
    encuentra los 12 meses en capital y en interes, o si la unidad no es
    millones de U$S. Incluye los bonos en manos del FGS/BCRA: la fuente no
    separa por tenedor (limitacion documentada)."""
    ventana = {(fecha_corte.year * 12 + fecha_corte.month - 1 + k) for k in range(1, 13)}
    total, meses_vistos = 0.0, {"capital": set(), "interes": set()}
    for filas in hojas:
        cabecera = " ".join(_norm(c) for f in filas[:10] for c in f if c)
        if "perfil mensual de vencimientos de" not in cabecera:
            continue
        tipo = "capital" if "vencimientos de capital" in cabecera else (
            "interes" if "vencimientos de interes" in cabecera else None)
        if tipo is None:
            continue
        if "millones de u$s" not in cabecera:
            raise RuntimeError(f"unidad no reconocida en el perfil mensual de {tipo}")
        enc = next((f for f in filas[:15] if sum(isinstance(x, datetime) for x in f) >= 3), None)
        if enc is None:
            continue
        cols = {j: x.year * 12 + x.month - 1 for j, x in enumerate(enc) if isinstance(x, datetime)}
        cols = {j: m for j, m in cols.items() if m in ventana}
        meses_vistos[tipo] |= set(cols.values())
        for fila in filas:
            nombre = next((x for x in fila if isinstance(x, str) and x.strip()), "")
            if not _MONEDA_EXTRANJERA.search(nombre) or "intransf" in _norm(nombre):
                continue
            total += sum(im.float_limpio(fila[j]) or 0.0 for j in cols if j < len(fila))
    faltan = {k: 12 - len(v) for k, v in meses_vistos.items() if len(v) < 12}
    if faltan:
        raise RuntimeError(f"no estan los 12 meses en el perfil mensual (faltan: {faltan})")
    return total


def _vencimientos_de_reporte(contenido: bytes, fecha_corte: date) -> float:
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    try:
        hojas = [list(ws.iter_rows(values_only=True)) for ws in wb.worksheets if ws.title.startswith("A.3")]
    finally:
        wb.close()
    return parsear_vencimientos_me(hojas, fecha_corte)


def vencimientos_12m(desde: str | None) -> list[tuple[str, float]]:
    """Refresco: el reporte trimestral mas reciente. Backfill: cada cierre
    de trimestre desde 2018 (los reportes que no esten o no se puedan leer
    se saltean y se informan, no frenan el resto)."""
    import backfill_deuda_externa as bde
    if desde is not None:
        contenido, fecha = bde._descargar_ultimo_reporte()
        return [(fecha.isoformat(), _vencimientos_de_reporte(contenido, fecha))]
    out, faltan = [], []
    for anio in range(2018, date.today().year + 1):
        for mes, dia in ((3, 31), (6, 30), (9, 30), (12, 31)):
            f = date(anio, mes, dia)
            if f > date.today():
                continue
            r = requests.get(bde.URL_BASE.format(fecha=f.strftime("%d-%m-%Y")), headers=_UA, timeout=60)
            if r.status_code != 200:
                faltan.append(f"{f} (HTTP {r.status_code})")
                continue
            try:
                out.append((f.isoformat(), _vencimientos_de_reporte(r.content, f)))
            except Exception as e:
                faltan.append(f"{f} ({e})")
    if faltan:
        print(f"  vencimientos: {len(faltan)} reportes salteados: {'; '.join(faltan)}")
    return out


def extraer_pe_factset(texto: str) -> float | None:
    import re
    t = " ".join(texto.split())
    m = re.search(r"forward 12-month P/E ratio (?:for the S&P 500 )?(?:is|of) (\d+\.\d+)", t)
    return float(m.group(1)) if m else None


def _pe_de_semana(viernes: date) -> tuple[str, float] | None:
    """PDF de esa semana: se publica el viernes (el jueves si hay feriado)."""
    import pdfplumber
    for d in (viernes, viernes - timedelta(days=1)):
        r = requests.get(FACTSET_URL.format(d), headers=_UA, timeout=60)
        if r.status_code == 200 and r.content[:4] == b"%PDF":
            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                texto = " ".join((pg.extract_text() or "") for pg in pdf.pages[:4])
            pe = extraer_pe_factset(texto)
            return (d.isoformat(), pe) if pe else None
    return None


def factset_pe(desde: str | None) -> list[tuple[str, float]]:
    from concurrent.futures import ThreadPoolExecutor
    # Modo demo: se saltea. Probar ~520 PDFs semanales tarda horas y FactSet
    # suele cortar la descarga; pe_forward queda sin dato (docs/demo.md).
    if configuracion.es_demo():
        print("  FactSet: se saltea en modo demo (pe_forward sin dato)")
        return []
    hoy = date.today()
    ultimo_viernes = hoy - timedelta(days=(hoy.weekday() - 4) % 7)
    semanas = FACTSET_ANIOS_BACKFILL * 52 + 1 if desde is None else FACTSET_SEMANAS_REFRESCO
    viernes = [ultimo_viernes - timedelta(weeks=k) for k in range(semanas)]
    with ThreadPoolExecutor(max_workers=6) as ex:
        out = sorted(x for x in ex.map(_pe_de_semana, viernes) if x)
    if desde is not None and not out:
        raise RuntimeError(f"no se encontro informe de FactSet en las ultimas {FACTSET_SEMANAS_REFRESCO} semanas "
                           f"(cambio la URL?)")
    print(f"  FactSet: {len(out)} semanas con P/E de {len(viernes)} probadas")
    return out


def _xls_filas(contenido: bytes, hoja: str | None = None) -> list[list]:
    import xlrd
    wb = xlrd.open_workbook(file_contents=contenido)
    sh = wb.sheet_by_name(hoja) if hoja else wb.sheet_by_index(0)
    return [sh.row_values(i) for i in range(sh.nrows)]


def shiller_cape(desde: str | None) -> list[tuple[str, float]]:
    import re
    html = requests.get(SHILLER_PAGINA, headers=_UA, timeout=40).text
    m = re.search(r"//img1\.wsimg\.com/blobby/go/[^\"'<>\s]+/ie_data\.xls[^\"'<>\s]*", html)
    if not m:
        raise RuntimeError("no se encontro el link a ie_data.xls en shillerdata.com")
    r = requests.get("https:" + m.group(0), headers=_UA, timeout=90)
    r.raise_for_status()
    return _solo_desde(parsear_shiller(_xls_filas(r.content, "Data")), desde)


def emae_series(desde: str | None) -> dict[str, list]:
    r = requests.get(EMAE_URL, headers=_UA, timeout=60)
    r.raise_for_status()
    return {k: _solo_desde(v, desde) for k, v in parsear_emae(_xls_filas(r.content)).items()}


# ------------------------------------------------------------ grupos
@dataclass
class Grupo:
    nombre: str
    tanda: int
    indicadores: list[str]
    fn: Callable[[str | None], dict[str, list]]   # desde -> {indicador: puntos}
    refresco: bool = True                         # False = solo backfill (lo refresca otro script)
    notas: list[str] = field(default_factory=list)


def _fred_simple(indicador_id: str, serie_id: str) -> Callable:
    return lambda desde: {indicador_id: fred_serie(serie_id, desde)}


def _grupo_tasa_real(desde: str | None) -> dict[str, list]:
    # El CPI necesita 12 meses extra para la variacion interanual.
    desde_cpi = (date.fromisoformat(desde) - timedelta(days=400)).isoformat() if desde else None
    effr = fred_serie("EFFR", desde)
    core_ia = variacion_interanual_mensual(fred_serie("CPILFESL", desde_cpi))
    return {
        "effr": effr,
        "core_cpi_ia": _a_mensual_iso(core_ia),
        "tasa_real_corto": tasa_real_mensual(promedio_mensual(effr), core_ia),
    }


def _grupo_us_cpi(desde: str | None) -> dict[str, list]:
    desde_cpi = (date.fromisoformat(desde) - timedelta(days=400)).isoformat() if desde else None
    return {"us_cpi_interanual": _a_mensual_iso(variacion_interanual_mensual(fred_serie("CPIAUCNS", desde_cpi)))}


def _fred_interanual(indicador_id: str, serie_id: str) -> Callable:
    """Indice trimestral de FRED -> variacion i.a. (trimestre vs. mismo
    trimestre del anio anterior; las fechas son el 1ro de ene/abr/jul/oct)."""
    return lambda desde: {indicador_id: _solo_desde(_a_mensual_iso(variacion_interanual_mensual(
        fred_serie(serie_id, _desde_menos_un_anio(desde)))), desde)}


def _grupo_intereses_pbi(desde: str | None) -> dict[str, list]:
    return {"intereses_pbi_eeuu": cociente(fred_serie("A091RC1Q027SBEA", desde), fred_serie("GDP", desde), 100.0)}


def _grupo_nominas(desde: str | None) -> dict[str, list]:
    # 4 meses extra de nivel: el primer mes del refresco necesita el de 3 meses antes.
    desde_nivel = (date.fromisoformat(desde) - timedelta(days=125)).isoformat() if desde else None
    return {"nominas_eeuu_prom3m": _solo_desde(promedio_3m_variacion(fred_serie("PAYEMS", desde_nivel)), desde)}


def _grupo_bcra(desde: str | None) -> dict[str, list]:
    return {"reservas_bcra": bcra_serie(BCRA_ID_RESERVAS, desde),
            "compras_bcra": bcra_serie(BCRA_ID_COMPRAS, desde)}


GRUPOS = [
    Grupo("FRED EFFR + core CPI -> tasa real", 1, ["effr", "core_cpi_ia", "tasa_real_corto"], _grupo_tasa_real),
    Grupo("FRED T10Y3M", 1, ["curva_10y_3m"], _fred_simple("curva_10y_3m", "T10Y3M")),
    Grupo("FRED T10Y2Y", 1, ["curva_10y_2y"], _fred_simple("curva_10y_2y", "T10Y2Y")),
    Grupo("FRED BAMLH0A0HYM2", 1, ["hy_spread"], _fred_simple("hy_spread", "BAMLH0A0HYM2")),
    Grupo("FRED UNRATE", 1, ["desempleo_eeuu"], _fred_simple("desempleo_eeuu", "UNRATE")),
    Grupo("FRED SAHMREALTIME", 1, ["sahm"], _fred_simple("sahm", "SAHMREALTIME")),
    # Empleo EEUU (v2.47, 2026-10-06): por que sube el desempleo.
    Grupo("FRED PAYEMS -> nominas prom. 3m", 1, ["nominas_eeuu_prom3m"], _grupo_nominas),
    Grupo("FRED IC4WSA -> subsidios (miles)", 1, ["subsidios_desempleo_4s"],
          lambda desde: {"subsidios_desempleo_4s": [(f, v / 1000) for f, v in fred_serie("IC4WSA", desde)]}),
    Grupo("FRED CIVPART", 1, ["participacion_eeuu"], _fred_simple("participacion_eeuu", "CIVPART")),
    Grupo("FRED DCOILBRENTEU", 1, ["brent"], _fred_simple("brent", "DCOILBRENTEU")),
    Grupo("BCRA reservas + compras", 1, ["reservas_bcra", "compras_bcra"], _grupo_bcra),
    Grupo("BCRA ITCRM (Excel)", 1, ["itcrm"], lambda desde: {"itcrm": itcrm_serie(desde)}),
    # CER (v2.49, 2026-10-14): tarjeta + insumo de la TIR real de los bonos CER (tir_bonos.py).
    Grupo("BCRA CER", 1, ["cer"], lambda desde: {"cer": bcra_serie(BCRA_ID_CER, desde)}),
    # Tasas de plazo fijo (paso 7b, 2026-10-29): las usa la demo para el interes de sus PF.
    Grupo("BCRA tasas de plazo fijo", 1, ["tasa_pf_30d", "tasa_pf_usd"],
          lambda desde: {"tasa_pf_30d": bcra_serie(BCRA_ID_PF_30D, desde),
                         "tasa_pf_usd": bcra_serie(BCRA_ID_PF_USD, desde)}),
    # --- existentes (Epicas 12/15): cargar_benchmarks_macro.py ya los
    # refresca a las 16 h (antes del cierre de NY) y escribe el historial
    # via upsert_benchmark; este refresco, mas tarde, deja el CIERRE real
    # de los ultimos dias (lo intradiario queda contado como revision).
    Grupo("FRED CPIAUCNS -> CPI EEUU i.a.", 1, ["us_cpi_interanual"], _grupo_us_cpi),
    Grupo("yfinance ^TNX", 1, ["ust_10y_pct"],
          lambda desde: {"ust_10y_pct": list(yf_cierres("^TNX", desde).items())}),
    Grupo("yfinance ^IRX", 1, ["tbill_3m"],
          lambda desde: {"tbill_3m": list(yf_cierres("^IRX", desde).items())}),
    Grupo("yfinance ^MERV", 1, ["merval"],
          lambda desde: {"merval": list(yf_cierres("^MERV", desde).items())}),
    Grupo("yfinance RSP/SPY", 1, ["sp500_concentracion_pct"],
          lambda desde: {"sp500_concentracion_pct": yf_ratio("RSP", "SPY", desde, 100.0)}),
    Grupo("yfinance EEM/SPY", 1, ["em_vs_sp500_ratio"],
          lambda desde: {"em_vs_sp500_ratio": yf_ratio("EEM", "SPY", desde)}),
    Grupo("yfinance ^GSPC -> S&P 500 i.a.", 1, ["sp500_interanual"],
          lambda desde: {"sp500_interanual": _solo_desde(interanual_diaria(
              list(yf_cierres("^GSPC", _desde_menos_un_anio(desde)).items())), desde)}),
    Grupo("argentinadatos MEP -> devaluacion i.a.", 1, ["devaluacion_ars_interanual"],
          lambda desde: {"devaluacion_ars_interanual": _solo_desde(interanual_diaria(mep_argentinadatos()), desde)}),
    # --- Tanda 2 (2026-09-30): resto del nucleo.
    Grupo("FRED OPHNFB -> productividad i.a.", 2, ["productividad_ia"], _fred_interanual("productividad_ia", "OPHNFB")),
    Grupo("FRED ULCNFB -> costo laboral unitario i.a.", 2, ["clu_ia"], _fred_interanual("clu_ia", "ULCNFB")),
    Grupo("FRED GFDEGDQ188S", 2, ["deuda_pbi_eeuu"], _fred_simple("deuda_pbi_eeuu", "GFDEGDQ188S")),
    Grupo("FRED A091RC1Q027SBEA / GDP", 2, ["intereses_pbi_eeuu"], _grupo_intereses_pbi),
    Grupo("FRED DFII10", 2, ["tips_10y"], _fred_simple("tips_10y", "DFII10")),
    Grupo("FRED THREEFYTP10", 2, ["prima_plazo_10y"], _fred_simple("prima_plazo_10y", "THREEFYTP10")),
    Grupo("Shiller CAPE (Excel)", 2, ["cape"], lambda desde: {"cape": shiller_cape(desde)}),
    Grupo("INDEC EMAE (Excel)", 2, ["emae_desest", "emae_ia"], emae_series),
    # --- Tanda 3 (2026-09-30): complementos.
    Grupo("FRED DGS30", 3, ["ust_30y"], _fred_simple("ust_30y", "DGS30")),
    Grupo("FRED COMPRNFB -> compensacion real i.a.", 3, ["comp_real_ia"], _fred_interanual("comp_real_ia", "COMPRNFB")),
    Grupo("FRED A679RC1Q027SBEA / GDP", 3, ["inversion_it_pbi"], lambda desde: {"inversion_it_pbi": cociente(
        fred_serie("A679RC1Q027SBEA", desde), fred_serie("GDP", desde), 100.0)}),
    Grupo("FRED FYFSGDA188S", 3, ["deficit_federal_pbi"], _fred_simple("deficit_federal_pbi", "FYFSGDA188S")),
    Grupo("FRED CP / GDP", 3, ["ganancias_pbi"], lambda desde: {"ganancias_pbi": cociente(
        fred_serie("CP", desde), fred_serie("GDP", desde), 100.0)}),
    # NCBEILQ027S viene en MILLONES y GDP en MILES DE MILLONES: x 100 / 1000.
    Grupo("FRED NCBEILQ027S / GDP (Buffett)", 3, ["buffett"], lambda desde: {"buffett": cociente(
        fred_serie("NCBEILQ027S", desde), fred_serie("GDP", desde), 0.1)}),
    Grupo("FRED BAMLC0A0CM", 3, ["ig_spread"], _fred_simple("ig_spread", "BAMLC0A0CM")),
    Grupo("FRED PSAVERT", 3, ["ahorro_personal"], _fred_simple("ahorro_personal", "PSAVERT")),
    Grupo("FRED NFCI", 3, ["nfci"], _fred_simple("nfci", "NFCI")),
    Grupo("FRED DTWEXBGS", 3, ["dolar_global"], _fred_simple("dolar_global", "DTWEXBGS")),
    Grupo("FRED WALCL", 3, ["balance_fed"], _fred_simple("balance_fed", "WALCL")),
    Grupo("yfinance GC=F", 3, ["oro"], lambda desde: {"oro": list(yf_cierres("GC=F", desde).items())}),
    Grupo("yfinance HG=F", 3, ["cobre"], lambda desde: {"cobre": list(yf_cierres("HG=F", desde).items())}),
    Grupo("FactSet P/E forward (PDF)", 3, ["pe_forward"], lambda desde: {"pe_forward": factset_pe(desde)}),
    Grupo("IMIG resultado primario 12m", 3, ["resultado_primario_ingresos"],
          lambda desde: {"resultado_primario_ingresos": imig_primario(desde)}),
    # Ajuste 2026-09-30: en vez del total de A.3.1 (toda la deuda, incluida la
    # en pesos y la intra sector publico), solo titulos en moneda extranjera.
    Grupo("Sec. Finanzas vencimientos 12m en moneda extranjera", 3, ["vencimientos_me_12m"],
          lambda desde: {"vencimientos_me_12m": vencimientos_12m(desde)}),
    Grupo("FRED BAA10Y", 3, ["baa_spread"], _fred_simple("baa_spread", "BAA10Y")),
]


# ------------------------------------------------------------ carga
def hacer_backup(db_path: Path) -> None:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-indicadores-macro_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(db_path, backup_path)
    con = sqlite3.connect(backup_path)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup_path.name} (integrity_check=ok)")


def sembrar_desde_benchmarks(con) -> list[dict]:
    """Para cada indicador con `clave_benchmark`, escribe en el historial el
    valor que ya esta en benchmarks_macro, con SU fecha de dato, SOLO si
    esa fecha falta en el historial. Es una observacion real (no se
    inventa nada) y cubre los indicadores sin historia previa posible
    (zpindex). Nunca actualiza: benchmarks_macro redondea a 3 decimales
    y puede tener un valor intradiario, peor que el de la serie."""
    resumenes = []
    for ind_id, clave in con.execute(
        "SELECT id, clave_benchmark FROM indicadores_catalogo WHERE clave_benchmark IS NOT NULL ORDER BY orden;"
    ).fetchall():
        fila = con.execute("SELECT valor_pct, fecha_dato FROM benchmarks_macro WHERE clave = ?;", (clave,)).fetchone()
        if not fila or fila[0] is None:
            continue
        ya_esta = con.execute(
            "SELECT 1 FROM macro_historico WHERE clave = ? AND fecha = ?;", (ind_id, im.fecha_iso(fila[1]))
        ).fetchone()
        if not ya_esta:
            resumenes.append(im.upsert_serie(con, ind_id, [(fila[1], fila[0])]))
    return resumenes


def ejecutar(db_path: Path, tanda: int | None, backfill: bool, indicador: str | None) -> list[dict]:
    grupos = [g for g in GRUPOS if (tanda is None or g.tanda == tanda)]
    if indicador:
        grupos = [g for g in grupos if indicador in g.indicadores]
        if not grupos:
            raise SystemExit(f"'{indicador}' no tiene carga en este script (tanda={tanda})")
    elif not backfill:
        grupos = [g for g in grupos if g.refresco]
    desde = None if backfill else (date.today() - timedelta(days=DIAS_REFRESCO)).isoformat()

    hacer_backup(db_path)
    con = sqlite3.connect(db_path, isolation_level=None)
    resumen: list[dict] = []
    en_catalogo = {r[0] for r in con.execute("SELECT id FROM indicadores_catalogo;")}
    try:
        for g in grupos:
            # Una serie nueva que esta base todavia no tiene en el catalogo se saltea,
            # en vez de hacer fallar el grupo entero (y la corrida) en upsert_serie.
            if not set(g.indicadores) & en_catalogo:
                print(f"=== {g.nombre}: se saltea, ninguno de {g.indicadores} esta en indicadores_catalogo ===")
                continue
            print(f"=== {g.nombre} ({'backfill' if backfill else f'refresco desde {desde}'}) ===")
            t0 = time.monotonic()
            try:
                series = g.fn(desde)       # red, fuera de la transaccion
                print(f"  descarga: {time.monotonic() - t0:.1f}s")
            except Exception as e:
                msg = enmascarar(f"{type(e).__name__}: {e}")[:300]
                print(f"  FALLO la fuente: {msg}")
                resumen += [{"indicador": i, "filas_nuevas": 0, "filas_actualizadas": 0, "sin_cambio": 0,
                             "descartadas": 0, "ultima_fecha": None, "cambios": [], "error": msg}
                            for i in g.indicadores if not indicador or i == indicador]
                continue
            con.execute("BEGIN;")
            try:
                for ind_id, puntos in series.items():
                    if (indicador and ind_id != indicador) or ind_id not in en_catalogo:
                        continue
                    r = im.upsert_serie(con, ind_id, puntos)
                    resumen.append(r)
                    print(f"  {ind_id}: {r['filas_nuevas']} nuevas, {r['filas_actualizadas']} actualizadas, "
                          f"{r['sin_cambio']} sin cambio, {r['descartadas']} descartadas, "
                          f"ultima fecha {r['ultima_fecha']}")
                    for c in r["cambios"]:
                        print(f"      revisado {c['fecha']}: {c['antes']} -> {c['despues']}")
                con.execute("COMMIT;")
            except Exception as e:
                con.execute("ROLLBACK;")
                msg = f"{type(e).__name__}: {e}"[:300]
                print(f"  FALLO al guardar (rollback del grupo): {msg}")
                resumen += [{"indicador": i, "error": msg} for i in g.indicadores]

        if backfill and not indicador:
            print("=== Valores actuales de benchmarks_macro -> historial ===")
            con.execute("BEGIN;")
            for r in sembrar_desde_benchmarks(con):
                if r["filas_nuevas"] or r["filas_actualizadas"]:
                    print(f"  {r['indicador']}: {r['filas_nuevas']} nuevas, {r['filas_actualizadas']} actualizadas")
            con.execute("COMMIT;")

        ok = con.execute("PRAGMA quick_check;").fetchone()[0]
        if ok != "ok":
            raise RuntimeError(f"quick_check={ok}")
    finally:
        con.close()
    return resumen


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--tanda", type=int)
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--indicador")
    a = ap.parse_args()
    resumen = ejecutar(a.db, a.tanda, a.backfill, a.indicador)
    errores = [r for r in resumen if r.get("error")]
    print(f"\n{len(resumen)} indicadores procesados, {len(errores)} con error.")
    print("RESUMEN_JSON: " + json.dumps(resumen, ensure_ascii=False))
    sys.stdout.flush()


if __name__ == "__main__":
    main()
