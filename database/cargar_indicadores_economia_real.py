"""
PWOS -- Epica 16 (2026-09-15): indicadores de economia
real argentina, mismo destino que Epica 15 (`benchmarks_macro`, la
tabla de cargar_benchmarks_macro.py) pero con fetcher/parser PROPIO por
fuente (Excel/CSV, no yfinance) y cadencia MENSUAL (no la cadencia de
30 min/diaria del resto del panel macro) -- por eso vive en un modulo
aparte, no mezclado en cargar_benchmarks_macro.py. Reusa `upsert_
benchmark` de ese modulo (mismo UPSERT, misma tabla) para no duplicar
esa logica.

5 indicadores, TODOS verificados descargando y abriendo el archivo real
antes de escribir el parser (2026-09-15) -- el brief original tenia 3
afirmaciones que NO coincidian con la fuente real, corregidas aca:

  - patentamientos_interanual_pct: DNRPA, inscripciones iniciales de
    automotores 0km, TODO el pais, mensual. El brief pedia Almanac.ar
    -- ES PAGO ("requiere cuenta" + "plan superior"), asi que se usa la
    fuente original gratuita: datos.jus.gob.ar (CKAN, CC-BY 4.0),
    mismo dato exacto (Almanac solo repackagea este mismo dataset).
    Historia real: 2000-01 en adelante (interanual desde 2001-01).
  - construccion_interanual_pct: INDEC, ISAC (Indicador Sintetico de
    la Actividad de la Construccion), serie original, variacion %
    interanual. El brief decia "historico desde 2001" -- el archivo
    VIGENTE de INDEC (`sh_isac_AAAA.xls`) solo tiene nivel desde 2012,
    interanual real desde **2013-01** (no hay 2012 anterior para
    comparar). Si existio una serie mas larga en algun momento, ya no
    es la que INDEC publica hoy bajo esta metodologia (base 2004=100).
  - empleo_privado_interanual_pct: OEDE/SIPA, personas con trabajo
    asalariado registrado en el sector PRIVADO (hoja A.1, total pais).
    El archivo NO trae una columna de interanual -- se calcula aca
    contra el mismo mes del nivel del anio anterior. Historia real:
    2009-01 en adelante (interanual desde 2010-01). El nombre del
    archivo (`trabajoregistrado_AAMM_...`) es el MES DE PUBLICACION,
    no del dato -- confirmado ~2-3 meses de rezago real (archivo
    publicado en 2026-06 trae datos provisorios hasta 2026-06).
  - mora_hogares_pct: BCRA, Informe sobre Bancos, anexo estadistico,
    seccion "2. Familias - Total" -> "Cartera irregular total" (ratio
    de irregularidad de cartera de creditos a familias, sistema
    financiero completo). Coincide con el brief: candidato mas fuerte
    (`InfBanc_Anexo`, nombre fijo sin mes/ano, se pisa cada mes) era
    el correcto. Historia real: 2010-01 en adelante.
  - industria_interanual_pct: INDEC, IPI manufacturero nivel general
    (mismo patron que ISAC). El brief encontro un vintage 2019 -- el
    archivo VIGENTE (`sh_ipi_manufacturero_AAAA.xls`) tiene nivel
    desde 2016, interanual real desde **2017-01**.

Cadencia MENSUAL: no tiene sentido pegarle a estas fuentes mas seguido,
los datos no cambian entre corridas del mismo mes. Pensado para un
trigger de n8n propio (1x/mes), separado del resto.

Backfill completo (una sola vez, historia real de cada fuente) via
`backfill_indicadores_economia_real.py` -- NUNCA se interpola ni se
inventa hacia atras donde no hay dato (ISAC/EMI arrancan cuando arranca
su propio interanual real, no antes).

`cargar()` tambien escribe `macro_historico` en cada corrida para que el
grafico historico de "Indicadores Macro" siga creciendo mes a mes. Desde
la Epica 21 (2026-09-29) lo hace `upsert_benchmark` por su cuenta (las 5
claves estan mapeadas en `indicadores_catalogo.clave_benchmark`), con
upsert: si INDEC/BCRA revisan un mes ya cargado, la revision pisa al
dato anterior (antes era INSERT OR IGNORE y se perdia).
"""
import csv
import io
import json
import re
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

import openpyxl
import requests
import xlrd

from cargar_benchmarks_macro import upsert_benchmark
import configuracion

DB_PATH = configuracion.ruta_db()

_UA = {"User-Agent": "Mozilla/5.0 (PWOS indicadores economia real fetch)"}

# Meses en espanol -> numero. Reusado por los 3 parsers de Excel (ISAC,
# EMI, SIPA) que traen el mes como texto en distintos formatos
# (nombre completo o abreviatura de 3 letras).
_MESES_LARGOS = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}
_MESES_ABREV = {
    "ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
    "jul": 7, "ago": 8, "sep": 9, "oct": 10, "nov": 11, "dic": 12,
}

DNRPA_RESOURCE_SHOW_URL = "https://datos.jus.gob.ar/api/3/action/resource_show?id=4358c995-bb04-4ad9-be4a-a9076dc3e42c"
SIPA_PAGINA_URL = "https://www.argentina.gob.ar/trabajo/estadisticas/situacion-y-evolucion-del-trabajo-registrado"
BCRA_ANEXO_URL = "https://www.bcra.gob.ar/archivos/Pdfs/PublicacionesEstadisticas/informes/informe-bancos-anexo.xlsx"
INDEC_ISAC_TEMPLATE = "https://www.indec.gob.ar/ftp/cuadros/economia/sh_isac_{anio}.xls"
INDEC_IPI_TEMPLATE = "https://www.indec.gob.ar/ftp/cuadros/economia/sh_ipi_manufacturero_{anio}.xls"


def _descargar(url: str, timeout: int = 45) -> bytes:
    r = requests.get(url, headers=_UA, timeout=timeout)
    r.raise_for_status()
    return r.content


def _url_indec_vigente(template: str) -> str | None:
    """INDEC nombra estos archivos con el anio de la edicion vigente
    (ej. sh_isac_2026.xls), no hay forma de saber de antemano si ya
    rotaron al anio nuevo -- se prueba el anio actual primero, despues
    el anterior. None si ninguno responde (nunca se inventa una URL
    que no se confirmo)."""
    hoy = date.today()
    for anio in (hoy.year, hoy.year - 1):
        url = template.format(anio=anio)
        try:
            r = requests.head(url, headers=_UA, timeout=15, allow_redirects=True)
            if r.status_code == 200:
                return url
        except requests.RequestException:
            continue
    return None


# ---------------------------------------------------------------- ISAC / EMI
# Mismo formato exacto en los 2 archivos de INDEC (ISAC y EMI): titulo,
# headers, despues 1 fila por mes -- el ANIO solo aparece en la fila de
# Enero de cada bloque (resto del anio queda con esa celda vacia), el
# MES es el nombre completo en espanol, y hay filas en blanco entre
# bloques. Caminar TODAS las filas y quedarse con el ultimo anio visto
# es mas robusto que asumir un offset de filas fijo (si INDEC agrega o
# saca una fila en el futuro, esto no se rompe).
def _parse_anio_indec(celda_anio) -> int | None:
    """El anio normalmente es un float (2012.0) pero INDEC marca los
    anios "provisorios" como TEXTO con asterisco (ej. '2023*' en el
    archivo de EMI/IPI manufacturero, encontrado 2026-09-15 -- ISAC no
    tiene este problema, todos sus anios son float). Sin este caso, el
    parser se quedaba pegado en el ultimo anio numerico real (2022) y
    silenciosamente re-escribia esas mismas 12 claves con los datos de
    los anios siguientes -- bug real, encontrado corriendo esto contra
    el archivo real, no en teoria."""
    if isinstance(celda_anio, (int, float)) and celda_anio > 1900:
        return int(celda_anio)
    if isinstance(celda_anio, str):
        m = re.match(r"^(\d{4})", celda_anio.strip())
        if m:
            return int(m.group(1))
    return None


def _serie_indec_mensual(contenido_xls: bytes, hoja: str, col_anio: int, col_mes: int, col_valor: int) -> dict[tuple[int, int], float]:
    wb = xlrd.open_workbook(file_contents=contenido_xls)
    sh = wb.sheet_by_name(hoja)
    serie: dict[tuple[int, int], float] = {}
    anio_actual = None
    for r in range(sh.nrows):
        anio_detectado = _parse_anio_indec(sh.cell_value(r, col_anio))
        if anio_detectado is not None:
            anio_actual = anio_detectado
        mes_num = _MESES_LARGOS.get(str(sh.cell_value(r, col_mes)).strip().lower())
        if mes_num is None or anio_actual is None:
            continue
        valor = sh.cell_value(r, col_valor)
        if isinstance(valor, (int, float)):
            serie[(anio_actual, mes_num)] = float(valor)
    return serie


def _ultimo_de_serie(serie: dict[tuple[int, int], float]) -> tuple[tuple[int, int], float] | None:
    if not serie:
        return None
    clave = max(serie.keys())
    return clave, serie[clave]


def obtener_construccion_interanual() -> dict | None:
    url = _url_indec_vigente(INDEC_ISAC_TEMPLATE)
    if url is None:
        return None
    serie = _serie_indec_mensual(_descargar(url), "Cuadro 1", col_anio=0, col_mes=1, col_valor=3)
    ultimo = _ultimo_de_serie(serie)
    if ultimo is None:
        return None
    (anio, mes), valor = ultimo
    return {"valor_pct": valor, "fecha_dato": f"{anio}-{mes:02d}-01", "fuente": f"INDEC ISAC ({url})"}


def obtener_construccion_historico() -> list[dict]:
    """Serie interanual COMPLETA de ISAC, para backfill de
    `macro_historico` -- misma fuente/parser que obtener_construccion_
    interanual, sin quedarse solo con el ultimo dato."""
    url = _url_indec_vigente(INDEC_ISAC_TEMPLATE)
    if url is None:
        return []
    serie = _serie_indec_mensual(_descargar(url), "Cuadro 1", col_anio=0, col_mes=1, col_valor=3)
    return [{"fecha": f"{a}-{m:02d}-01", "valor": v} for (a, m), v in sorted(serie.items())]


def obtener_industria_interanual() -> dict | None:
    url = _url_indec_vigente(INDEC_IPI_TEMPLATE)
    if url is None:
        return None
    serie = _serie_indec_mensual(_descargar(url), "Cuadro 1", col_anio=1, col_mes=2, col_valor=4)
    ultimo = _ultimo_de_serie(serie)
    if ultimo is None:
        return None
    (anio, mes), valor = ultimo
    return {"valor_pct": valor, "fecha_dato": f"{anio}-{mes:02d}-01", "fuente": f"INDEC IPI manufacturero/EMI ({url})"}


def obtener_industria_historico() -> list[dict]:
    url = _url_indec_vigente(INDEC_IPI_TEMPLATE)
    if url is None:
        return []
    serie = _serie_indec_mensual(_descargar(url), "Cuadro 1", col_anio=1, col_mes=2, col_valor=4)
    return [{"fecha": f"{a}-{m:02d}-01", "valor": v} for (a, m), v in sorted(serie.items())]


# ---------------------------------------------------------------- SIPA (empleo privado)
def _parse_fecha_sipa(valor) -> tuple[int, int] | None:
    """La columna Periodo de la hoja A.1 mezcla objetos datetime (filas
    viejas) y texto tipo 'jun-26' o 'jun-26*' -- el '*' marca dato
    provisorio, se ignora para el parseo (se usa igual, mismo criterio
    que el resto del proyecto: dato provisorio real, no se descarta)."""
    if isinstance(valor, datetime):
        return valor.year, valor.month
    if isinstance(valor, str):
        m = re.match(r"^([a-z]{3})-(\d{2})\*?$", valor.strip().lower())
        if m and m.group(1) in _MESES_ABREV:
            return 2000 + int(m.group(2)), _MESES_ABREV[m.group(1)]
    return None


def _url_sipa_vigente() -> str | None:
    """El nombre del archivo (`trabajoregistrado_AAMM_estadisticas.xlsx`)
    cambia cada mes (AAMM = mes de PUBLICACION) -- se scrapea la pagina
    de estadisticas en vez de adivinar el AAMM de hoy (el rezago real
    es variable, no un numero fijo de meses)."""
    try:
        html = requests.get(SIPA_PAGINA_URL, headers=_UA, timeout=20).text
    except requests.RequestException:
        return None
    m = re.search(r'https://www\.argentina\.gob\.ar/sites/default/files/trabajoregistrado_\d{4}_estadisticas\.xlsx', html)
    return m.group(0) if m else None


def _serie_sipa_privado(contenido_xlsx: bytes) -> dict[tuple[int, int], float]:
    wb = openpyxl.load_workbook(io.BytesIO(contenido_xlsx), read_only=True, data_only=True)
    sh = wb["A.1"]
    serie: dict[tuple[int, int], float] = {}
    for row in sh.iter_rows(min_row=4, values_only=True):
        clave = _parse_fecha_sipa(row[0])
        if clave is None:
            continue
        nivel = row[1]  # "Serie original", en miles
        if isinstance(nivel, (int, float)):
            serie[clave] = float(nivel)
    return serie


def _interanual_desde_niveles(serie: dict[tuple[int, int], float]) -> dict[tuple[int, int], float]:
    """De una serie de NIVELES {(anio,mes): valor} arma la serie de
    variacion % interanual -- solo para los (anio,mes) donde el mismo
    mes del anio anterior tambien existe en la serie (nunca se
    interpola ni se aproxima con otro mes)."""
    interanual = {}
    for (anio, mes), valor in serie.items():
        anterior = serie.get((anio - 1, mes))
        if anterior:
            interanual[(anio, mes)] = (valor / anterior - 1) * 100
    return interanual


def obtener_empleo_privado_interanual() -> dict | None:
    url = _url_sipa_vigente()
    if url is None:
        return None
    niveles = _serie_sipa_privado(_descargar(url))
    interanual = _interanual_desde_niveles(niveles)
    ultimo = _ultimo_de_serie(interanual)
    if ultimo is None:
        return None
    (anio, mes), valor = ultimo
    return {"valor_pct": valor, "fecha_dato": f"{anio}-{mes:02d}-01", "fuente": f"OEDE/SIPA, trabajo registrado sector privado ({url})"}


def obtener_empleo_privado_historico() -> list[dict]:
    url = _url_sipa_vigente()
    if url is None:
        return []
    niveles = _serie_sipa_privado(_descargar(url))
    interanual = _interanual_desde_niveles(niveles)
    return [{"fecha": f"{a}-{m:02d}-01", "valor": v} for (a, m), v in sorted(interanual.items())]


# ---------------------------------------------------------------- BCRA (mora hogares)
_BCRA_LABEL_SECCION = "2. Familias - Total"
_BCRA_LABEL_VALORES = "Cartera irregular total"


def _fila_calidad_cartera(contenido_xlsx: bytes) -> list[tuple]:
    wb = openpyxl.load_workbook(io.BytesIO(contenido_xlsx), read_only=True, data_only=True)
    # Nombre de la hoja tiene un caracter con encoding roto en el
    # archivo real ("l\ufffdneas") -- buscar por INDICE (la 6ta hoja,
    # confirmado 2026-09-15), no por nombre exacto, mismo criterio que
    # el resto del proyecto ante nombres de columna/hoja poco confiables.
    sh = wb.worksheets[5]
    return list(sh.iter_rows(values_only=True))


def _mora_hogares_de_filas(filas: list[tuple]) -> dict | None:
    """Busca la seccion 'Familias - Total' por TEXTO (no por numero de
    fila fijo -- si BCRA reordena secciones en el futuro, esto no
    lee un valor equivocado en silencio, devuelve None) y confirma que
    la fila de valores 2 posiciones despues efectivamente dice 'Cartera
    irregular total' antes de confiar en ella."""
    idx_seccion = next((i for i, f in enumerate(filas) if f and f[0] == _BCRA_LABEL_SECCION), None)
    if idx_seccion is None:
        return None
    fila_fechas = filas[idx_seccion + 2]
    fila_valores = filas[idx_seccion + 3]
    if not fila_valores or fila_valores[0] != _BCRA_LABEL_VALORES:
        return None
    pares = [
        (f, v) for f, v in zip(fila_fechas[1:], fila_valores[1:])
        if isinstance(f, datetime) and isinstance(v, (int, float))
    ]
    if not pares:
        return None
    return {"pares": sorted(pares, key=lambda t: t[0])}


def obtener_mora_hogares() -> dict | None:
    filas = _fila_calidad_cartera(_descargar(BCRA_ANEXO_URL))
    datos = _mora_hogares_de_filas(filas)
    if datos is None:
        return None
    fecha, valor = datos["pares"][-1]
    return {"valor_pct": valor, "fecha_dato": fecha.date().isoformat(), "fuente": f"BCRA, Informe sobre Bancos, anexo ({BCRA_ANEXO_URL})"}


def obtener_mora_hogares_historico() -> list[dict]:
    filas = _fila_calidad_cartera(_descargar(BCRA_ANEXO_URL))
    datos = _mora_hogares_de_filas(filas)
    if datos is None:
        return []
    return [{"fecha": f.date().isoformat(), "valor": v} for f, v in datos["pares"]]


# ---------------------------------------------------------------- DNRPA (patentamientos)
def _url_patentamientos_vigente() -> str | None:
    """El nombre del CSV trae el rango de fechas en el propio nombre
    (ej. ...-2000-01-2026-08.csv) -- cambia cada mes. `resource_id` en
    la URL de la API SI es estable (dataset CKAN, confirmado 2026-09-15)
    -- se resuelve la URL real vigente por API en vez de adivinar el
    nombre de archivo de este mes."""
    try:
        r = requests.get(DNRPA_RESOURCE_SHOW_URL, headers=_UA, timeout=20)
        r.raise_for_status()
        return r.json()["result"]["url"]
    except (requests.RequestException, KeyError, ValueError):
        return None


def _serie_patentamientos(contenido_csv: bytes) -> dict[tuple[int, int], float]:
    texto = contenido_csv.decode("utf-8-sig")
    serie: dict[tuple[int, int], float] = {}
    for row in csv.DictReader(io.StringIO(texto)):
        clave = (int(row["anio_inscripcion_inicial"]), int(row["mes_inscripcion_inicial"]))
        serie[clave] = serie.get(clave, 0) + int(row["cantidad_inscripciones_iniciales"])
    return serie


def obtener_patentamientos_interanual() -> dict | None:
    url = _url_patentamientos_vigente()
    if url is None:
        return None
    serie = _serie_patentamientos(_descargar(url))
    interanual = _interanual_desde_niveles(serie)
    ultimo = _ultimo_de_serie(interanual)
    if ultimo is None:
        return None
    (anio, mes), valor = ultimo
    return {"valor_pct": valor, "fecha_dato": f"{anio}-{mes:02d}-01", "fuente": f"DNRPA, inscripciones iniciales de automotores, total pais (datos.jus.gob.ar)"}


def obtener_patentamientos_historico() -> list[dict]:
    url = _url_patentamientos_vigente()
    if url is None:
        return []
    serie = _serie_patentamientos(_descargar(url))
    interanual = _interanual_desde_niveles(serie)
    return [{"fecha": f"{a}-{m:02d}-01", "valor": v} for (a, m), v in sorted(interanual.items())]


# ---------------------------------------------------------------- Carga
def hacer_backup(db_path: Path) -> None:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-indicadores-economia-real_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(db_path, backup_path)
    con = sqlite3.connect(backup_path)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {backup_path.name} (integrity_check=ok)")


def cargar(db_path: Path = DB_PATH) -> None:
    hacer_backup(db_path)

    con = sqlite3.connect(db_path, isolation_level=None)
    con.execute("PRAGMA foreign_keys = ON;")
    ahora = datetime.now().isoformat()

    try:
        con.execute("BEGIN;")

        pasos = [
            ("Patentamientos (DNRPA)", "patentamientos_interanual_pct", obtener_patentamientos_interanual),
            ("Construccion (INDEC ISAC)", "construccion_interanual_pct", obtener_construccion_interanual),
            ("Empleo privado (OEDE/SIPA)", "empleo_privado_interanual_pct", obtener_empleo_privado_interanual),
            ("Mora de hogares (BCRA)", "mora_hogares_pct", obtener_mora_hogares),
            ("Industria (INDEC EMI/IPI manufacturero)", "industria_interanual_pct", obtener_industria_interanual),
        ]
        for titulo, clave, fn in pasos:
            print(f"=== {titulo} ===")
            try:
                dato = fn()
                upsert_benchmark(con, clave, dato, ahora)  # + historial, via indicadores_catalogo
            except Exception as e:
                print(f"  FALLO: {e}")

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print("\nCarga OK.")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"\nCARGA ABORTADA, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    cargar()
