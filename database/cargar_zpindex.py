"""
PWOS -- Epica 16, Real Estate (m2): zpindex de Zonaprop (2026-09-15,
pedido del usuario). Informe mensual en PDF, precio de venta USD/m2 de
departamentos, 1 archivo por region. A diferencia de los 5 indicadores
de `cargar_indicadores_economia_real.py`, este NO tiene historial
publico descargable -- "arranca de cero desde que corra el fetcher, no
se puede reconstruir el pasado" -- por eso
va en un modulo aparte, sin backfill. Epica 21 (2026-09-29): desde ahora
SI acumula serie en `macro_historico` -- la escribe `upsert_benchmark`
(clave mapeada en `indicadores_catalogo`), arrancando del ultimo dato
publicado, sin inventar nada hacia atras.

Alcance de esta ronda (confirmado por el usuario, 2026-09-15): CABA,
Cordoba y Rosario -- venta USD/m2 unicamente (no alquiler ni
rentabilidad, el PDF trae las 3 pero solo se pidio venta). Descartados
como fuente (verificado en la investigacion de esta sesion): Argenprop
(solo notas periodisticas, sin PDF descargable) y deinmobiliarios.com
(red de agentes, no publica precios).

URL predecible mes a mes mismo patron para las 3 regiones:
  https://www.zonaprop.com.ar/blog/wp-content/uploads/{anio_pub}/{mes_pub}/INDEX_{codigo}_REPORTE_{anio_dato}-{mes_dato}.pdf
donde {anio_pub}/{mes_pub} es el mes en que Zonaprop subio el archivo
(carpeta) y {anio_dato}-{mes_dato} (en el NOMBRE del archivo) es el mes
al que corresponde el dato -- siempre 1 mes antes de la carpeta de
subida, confirmado en los 3 codigos de region.

Cadencia real MUY distinta entre regiones (verificado descargando,
2026-09-15, no asumido):
  - CABA (codigo "CABA"): mensual, confiable -- ultimo mes disponible
    hoy es el mes calendario anterior al actual.
  - Cordoba (codigo "CBA") y Rosario (codigo "RSA"): NO mensual --
    verificado que la edicion mas reciente de Cordoba a la fecha de
    esta sesion es datos de 2026-03 (~6 meses de atraso) y Rosario
    2026-05 (~4 meses de atraso). Por eso `_url_zpindex_vigente` busca
    hacia atras hasta 12 meses en vez de asumir "mes anterior" como en
    CABA -- si se hardcodeara un offset fijo, Cordoba/Rosario
    quedarian sin dato la mayoria de los meses.

Extraccion: cada PDF trae, en su pagina "Venta Index | Zonaprop -
Evolucion del precio medio de la ciudad", la frase fija "...se ubica en
USD {precio} por m2" -- mismo patron de oracion confirmado en los 3
PDF reales descargados. Se busca esa frase en TODAS las paginas (no una
pagina fija por numero -- el PDF puede ganar/perder paginas entre
ediciones) y se toma la PRIMERA que matchea, porque la seccion "Venta"
siempre viene antes que "Alquiler"/"Rentabilidad" en la estructura del
informe (confirmado: la 2da aparicion de la frase, mas adelante en el
PDF, es una repeticion dentro de la misma seccion Venta -- ej. precio
por zona -- nunca la seccion de Alquiler, que usa otra frase).
"""
import io
import json
import re
import shutil
import sqlite3
from datetime import date, datetime
from pathlib import Path

import pdfplumber
import requests

from cargar_benchmarks_macro import upsert_benchmark
import indicadores_macro as im
import configuracion

DB_PATH = configuracion.ruta_db()

_UA = {"User-Agent": "Mozilla/5.0 (PWOS zpindex fetch)"}

# clave benchmarks_macro -> (nombre legible, codigo de region en la URL)
ZPINDEX_REGIONES = {
    "zpindex_caba_venta_usd_m2": ("CABA", "CABA"),
    "zpindex_cordoba_venta_usd_m2": ("Córdoba", "CBA"),
    "zpindex_rosario_venta_usd_m2": ("Rosario", "RSA"),
}

_RE_PRECIO = re.compile(r"se ubica en USD\s*([\d.,]+)\s*por\s*m", re.IGNORECASE)


def _url_zpindex_vigente(codigo_region: str, meses_atras: int = 12) -> str | None:
    """Busca hacia atras mes a mes (carpeta de subida) hasta encontrar
    una edicion real -- nunca asume un offset fijo (Cordoba/Rosario no
    publican todos los meses, ver docstring del modulo)."""
    hoy = date.today()
    anio_pub, mes_pub = hoy.year, hoy.month
    for _ in range(meses_atras):
        mes_dato = mes_pub - 1
        anio_dato = anio_pub
        if mes_dato == 0:
            mes_dato = 12
            anio_dato -= 1
        url = (
            f"https://www.zonaprop.com.ar/blog/wp-content/uploads/{anio_pub:04d}/{mes_pub:02d}/"
            f"INDEX_{codigo_region}_REPORTE_{anio_dato:04d}-{mes_dato:02d}.pdf"
        )
        try:
            r = requests.head(url, headers=_UA, timeout=15, allow_redirects=True)
            if r.status_code == 200:
                return url
        except requests.RequestException:
            pass
        mes_pub -= 1
        if mes_pub == 0:
            mes_pub = 12
            anio_pub -= 1
    return None


def _precio_venta_m2_de_pdf(contenido_pdf: bytes) -> float | None:
    with pdfplumber.open(io.BytesIO(contenido_pdf)) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text() or ""
            m = _RE_PRECIO.search(texto)
            if m:
                crudo = m.group(1).replace(".", "").replace(",", ".")
                try:
                    return float(crudo)
                except ValueError:
                    return None
    return None


def obtener_zpindex(clave: str) -> dict | None:
    if clave not in ZPINDEX_REGIONES:
        raise ValueError(f"clave zpindex invalida: {clave!r} (validas: {tuple(ZPINDEX_REGIONES)})")
    nombre, codigo = ZPINDEX_REGIONES[clave]
    url = _url_zpindex_vigente(codigo)
    if url is None:
        return None
    r = requests.get(url, headers=_UA, timeout=45)
    r.raise_for_status()
    precio = _precio_venta_m2_de_pdf(r.content)
    if precio is None:
        return None
    m = re.search(r"REPORTE_(\d{4})-(\d{2})(-\d)?\.pdf$", url)
    anio, mes = int(m.group(1)), int(m.group(2))
    return {
        "valor_pct": precio,
        "fecha_dato": f"{anio}-{mes:02d}-01",
        "fuente": f"Zonaprop zpindex {nombre}, venta USD/m2 ({url})",
    }


def hacer_backup(db_path: Path) -> None:
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-zpindex_{datetime.now():%Y-%m-%d_%H%M%S}.db"
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
        for clave, (nombre, _codigo) in ZPINDEX_REGIONES.items():
            print(f"=== zpindex {nombre} (venta USD/m2) ===")
            try:
                dato = obtener_zpindex(clave)
                if dato is None:
                    print(f"  SIN DATO (no se encontro edicion en los ultimos 12 meses, o no se pudo extraer el precio)")
                upsert_benchmark(con, clave, dato, ahora)
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


# --- Backfill de meses pasados (paso 7b, 2026-10-29) -----------------------
# Zonaprop deja en su sitio los informes con el formato nuevo (texto, no
# imagen): desde el de 2025-09. Los anteriores se movieron o tienen el precio
# como imagen -- no se extraen (nunca se adivina un precio por OCR).
# La carpeta de subida es la del mes del dato o hasta 2 despues, y a veces
# el archivo lleva sufijo ("-1").
def url_zpindex_mes(codigo_region: str, anio: int, mes: int) -> str | None:
    for desfase in (1, 0, 2):
        total = anio * 12 + (mes - 1) + desfase
        anio_pub, mes_pub = total // 12, total % 12 + 1
        for sufijo in ("", "-1", "-2"):
            url = (f"https://www.zonaprop.com.ar/blog/wp-content/uploads/{anio_pub:04d}/{mes_pub:02d}/"
                   f"INDEX_{codigo_region}_REPORTE_{anio:04d}-{mes:02d}{sufijo}.pdf")
            try:
                if requests.head(url, headers=_UA, timeout=15, allow_redirects=True).status_code == 200:
                    return url
            except requests.RequestException:
                pass
    return None


def backfill(db_path: Path, clave: str, desde: str, hasta: str) -> dict:
    """Carga en `macro_historico` (upsert_serie) cada mes AAAA-MM entre `desde` y
    `hasta` que tenga informe con precio extraible. Devuelve {mes: precio o motivo}."""
    _nombre, codigo = ZPINDEX_REGIONES[clave]
    a, m = map(int, desde.split("-"))
    fin = tuple(map(int, hasta.split("-")))
    puntos, detalle = [], {}
    while (a, m) <= fin:
        url = url_zpindex_mes(codigo, a, m)
        precio = None
        if url:
            r = requests.get(url, headers=_UA, timeout=60)
            precio = _precio_venta_m2_de_pdf(r.content) if r.ok else None
        detalle[f"{a}-{m:02d}"] = precio if precio is not None else ("sin precio extraible" if url else "sin informe")
        if precio is not None:
            puntos.append((f"{a}-{m:02d}-01", precio))
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)
    if puntos:
        con = sqlite3.connect(db_path)
        with con:
            im.upsert_serie(con, clave, puntos)
        con.close()
    return detalle


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--backfill", nargs=3, metavar=("CLAVE", "DESDE", "HASTA"),
                    help="ej.: zpindex_caba_venta_usd_m2 2025-09 2026-09")
    args = ap.parse_args()
    if args.backfill:
        print(json.dumps(backfill(args.db, *args.backfill), ensure_ascii=False, indent=1))
    else:
        cargar(args.db)
