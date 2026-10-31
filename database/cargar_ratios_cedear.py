"""
PWOS -- carga de `ratios_cedear` (v2.37, "¿CEDEAR en pesos o en dolares?", Fase 2).

Fuentes (decision del usuario, 2026-09-24):
  - BYMA, PDF "CEDEARs negociables en BYMA con ratios de conversion": FUENTE DE
    VERDAD del ratio (~570 especies, ambos emisores). El link cambia en cada
    actualizacion (CDN con hash) -> se busca en la pagina de BYMA el href que
    termina en `<AAAA-MM-DD>-BYMA-CEDEARs.pdf` y se toma la fecha mas nueva. Si
    falla, `--pdf <ruta>` acepta un PDF guardado a mano (database/referencias/).
  - Banco Comafi, Excel "Lista total de CEDEARs" (URL fija): SOLO enriquecimiento
    (ticker de origen, ISIN, pais, industria, tipo). Nunca pisa el ratio de BYMA;
    su ratio se usa como control cruzado.

El PDF no trae celdas: cada fila es "Nombre CODIGO MERCADO ratio" como texto
corrido, y el mercado puede tener varias palabras ("NASDAQ GS", "NYSE Arca") y
el codigo tambien ("BAYN GR"). Se parsea por la POSICION HORIZONTAL de cada
palabra (columnas fijas del documento, verificadas contra el encabezado de cada
pagina). Una fila que no tenga exactamente codigo + mercado + ratio se reporta,
no se adivina.

Reglas de calidad (seccion 3.1 del ticket + lo encontrado en los datos reales):
  - Ratio "a:b" con espacios ("144 : 1", "1 :4") -> se quitan antes de parsear.
  - Ratio que no matchea ^\\d+:\\d+$ ("3.1", "25.1", "15.1" de Comafi) -> se
    BLOQUEA Y MARCA, nunca se adivina.
  - Formato hora de Excel: "5:01" (EEM) y "04:01" (XP) -> partes con cero a la
    izquierda se leen como enteros (5:1, 4:1) y se loguea.
  - Fraccionarios (1 CEDEAR = varias acciones, ej. ABEV 1:3): se guardan las dos
    partes y `ratio = cedear / subyacente` (REAL).
  - Codigo BYMA DUPLICADO con datos distintos (XLU aparece 2 veces el
    2026-09-16: "First Trust NASDAQ Cybersecurity" 10:1 -- ese ETF es CIBR -- y
    la XLU real 15:1): se bloquean las dos filas, no se elige una.
  - Marcas `*`/`**`/`***` de Comafi en el nombre -> `nota` con la leyenda.
  - Codigo BYMA != ticker de Comafi -> diccionario explicito BYMA_A_COMAFI
    (mismo patron que TICKER_OVERRIDE_DATA912); lo que no cruza queda "sin
    enriquecer" (no bloquea).

Ticker de origen (para el precio de NY, en el formato de data912 `usa_stocks`/
`usa_adrs`: `BRK.B`): el de Comafi si cruza (con `/` y `-` pasados a `.`), si no
el propio codigo BYMA.

Patron de siempre: DRY-RUN por defecto (imprime el reporte y no toca nada) ->
`--aplicar` hace backup + transaccion + integrity_check/foreign_key_check. Un
cambio de ratio o una baja respecto de la ultima carga NO se aplica en silencio:
sin `--confirmar-cambios` la corrida con `--aplicar` aborta.

Uso:
    python cargar_ratios_cedear.py                         # dry-run contra la base de la configuracion
    python cargar_ratios_cedear.py --aplicar               # carga (aborta si hay cambios de ratio/bajas)
    python cargar_ratios_cedear.py --aplicar --confirmar-cambios
    python cargar_ratios_cedear.py --db <copia.db> --pdf <byma.pdf> --comafi <lista.xlsx>
"""
import argparse
import io
import re
import shutil
import sqlite3
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path

import openpyxl
import pdfplumber
import requests
import configuracion

DB_PATH = configuracion.ruta_db()
BYMA_PAGINA = "https://www.byma.com.ar/productos/productos-financieros/cedears"
BYMA_PDF_RE = re.compile(r'https://[^"\'\s>]+?(\d{4}-\d{2}-\d{2})-BYMA-CEDEARs\.pdf')
COMAFI_URL = "https://www.comafi.com.ar/custodiaglobal/Multimedios/otros/14779.xlsx"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"}

# Limites de columna del PDF de BYMA (puntos, pagina de 612 de ancho), medidos
# el 2026-09-24. Codigo, mercado y ratio van CENTRADOS en su columna: un
# mercado largo ("NASDAQ GM", "LONDON STOCK") arranca bastante mas a la
# izquierda, asi que se clasifica por el CENTRO de la palabra (codigo ~293,
# mercado 341-368, ratio ~420); el nombre va alineado a la izquierda y se
# reconoce por su x0 (< X_NOMBRE_FIN; los codigos arrancan en x0 >= 280).
# `_verificar_encabezado` confirma en CADA pagina que los titulos siguen donde
# se esperan antes de usar estos cortes.
X_NOMBRE_FIN = 276
X_CODIGO, X_MERCADO, X_RATIO = 270, 320, 395

RATIO_RE = re.compile(r"^(\d+):(\d+)$")

# Codigo BYMA -> ticker de Comafi, para los que no coinciden tal cual.
# Encontrados cruzando las listas del 2026-09-16 (BYMA) y 2026-09-17 (Comafi),
# cada uno verificado por nombre.
BYMA_A_COMAFI = {
    "BA.C": "BAC",       # Bank of America (en BYMA "BA" es Boeing)
    "BRKB": "BRK/B",     # Berkshire Hathaway B
    "DISN": "DIS",       # Walt Disney
    # los 13 que el 1er dry-run cruzo por nombre, revisados uno a uno
    "ADGO": "AGRO", "BAYN": "BAYN GR", "BK": "BNY", "BSN": "BSN GR", "MBG": "MBG GR",
    "NOKA": "NOK", "NSAN": "NSANY", "ORAN": "ORANY", "PKS": "PKX", "TXR": "TX",
    "UN": "NU", "WBO": "WB", "XROX": "XRX",
    # mismo emisor con otro nombre en cada lista (no cruzaban por nombre)
    "BBV": "BBVA",       # Banco Bilbao Vizcaya Argentaria
    "BNG": "BG",         # Bunge ("Bunge Limited" / "BUNGE GLOBAL SA")
    "KOFM": "KOF",       # Coca-Cola Femsa
    "TRVV": "TRV",       # Travelers
    "TEN": "TS",         # Tenaris
    "MMC": "MRSH",       # Marsh & McLennan
    "ELP": "ELPC",       # Copel
    "YZCA": "YZCAY",     # Yanzhou Coal / Yankuang Energy
    # Eletrobras cambio de nombre a Axia Energia; su ADR paso de EBR a AXIA en
    # NYSE el 10/11/2025, mismos derechos (confirmado por el usuario). Solo se cruza
    # si el ISIN del subyacente coincide (ISIN_ESPERADO).
    "EBR": "AXIAY",
}

# Cruces que ademas exigen el ISIN del subyacente en Comafi: si no coincide,
# no se cruzan y se reporta (el programa pudo haber cambiado).
ISIN_ESPERADO = {
    "EBR": "US15234Q2075",
}

# Ticker de origen que no es ni el de Comafi ni el codigo BYMA.
TICKER_ORIGEN_OVERRIDE = {
    "EBR": "AXIA",       # Comafi lo lista como AXIAY (OTC); el ADR cotiza como AXIA en NYSE
}

LEYENDA_MARCAS = {
    "***": "Programa inhabilitado para emitir y cancelar (Comafi ***)",
    "**": "Programa inhabilitado para emitir (Comafi **)",
    "*": "Programa con situacion especial (Comafi *, sin leyenda en la lista)",
}

TIPO_COMAFI = {
    "common stock": "accion", "accion ordinaria": "accion", "acciones registradas en ny": "accion",
    "adr": "adr", "etf": "etf",
}


# ---------------------------------------------------------------- normalizacion

def normalizar_ratio(texto) -> tuple[int, int, str | None] | None:
    """(cedear, subyacente, nota) o None si no es un ratio valido.
    '144 : 1' -> (144, 1, None); '04:01' -> (4, 1, 'formato hora'); '3.1' -> None."""
    if texto is None:
        return None
    limpio = str(texto).replace(" ", "").replace("\xa0", "")
    m = RATIO_RE.match(limpio)
    if not m:
        return None
    a, b = m.groups()
    ced, sub = int(a), int(b)
    if ced <= 0 or sub <= 0:
        return None
    nota = None
    if (len(a) > 1 and a.startswith("0")) or (len(b) > 1 and b.startswith("0")):
        nota = f"ratio '{texto}' con ceros a la izquierda (formato hora de Excel) leido como {ced}:{sub}"
    return ced, sub, nota


def normalizar_nombre(nombre: str | None) -> str:
    s = unicodedata.normalize("NFKD", nombre or "").encode("ascii", "ignore").decode().upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    ruido = {"INC", "CORP", "CORPORATION", "CO", "LTD", "PLC", "SA", "S", "A", "AG", "NV", "THE", "ADR",
             "HOLDINGS", "HOLDING", "GROUP", "COMPANY", "LIMITED", "DE", "CV", "SE", "SPA", "N", "V", "B"}
    return " ".join(p for p in s.split() if p not in ruido)


def ticker_para_data912(ticker: str) -> str:
    return ticker.strip().replace("/", ".").replace("-", ".")


# --------------------------------------------------------------------- BYMA PDF

def descubrir_pdf_byma() -> tuple[str, str]:
    """(url, fecha AAAA-MM-DD) del PDF mas nuevo enlazado en la pagina de BYMA."""
    r = requests.get(BYMA_PAGINA, headers=UA, timeout=30)
    r.raise_for_status()
    encontrados = {(m.group(1), m.group(0)) for m in BYMA_PDF_RE.finditer(r.text)}
    if not encontrados:
        raise RuntimeError("No se encontro el link al PDF de ratios en la pagina de BYMA (usar --pdf)")
    fecha, url = max(encontrados)
    return url, fecha


def _verificar_encabezado(pagina, n: int) -> None:
    """Los titulos vienen con cada letra repetida ('CCCCóóóódddd...'): se
    compara el x0 de 'Codigo' y 'Ratio' contra los cortes de columna."""
    palabras = pagina.extract_words()
    cod = [w for w in palabras if w["text"].startswith("CCCCóóóó")]
    rat = [w for w in palabras if w["text"].startswith("RRRRaaaa")]
    if not cod or not rat or not (X_CODIGO < cod[0]["x0"] < X_MERCADO) or not (X_MERCADO < rat[0]["x0"] < X_RATIO + 20):
        raise RuntimeError(f"Pagina {n}: el encabezado no esta donde se esperaba -- el formato del PDF cambio, "
                           "revisar X_CODIGO/X_MERCADO/X_RATIO antes de cargar nada")


def parsear_pdf_byma(contenido: bytes) -> tuple[list[dict], str | None, list[str]]:
    filas, anomalias, fecha_lista = [], [], None
    with pdfplumber.open(io.BytesIO(contenido)) as pdf:
        for n, pagina in enumerate(pdf.pages, 1):
            _verificar_encabezado(pagina, n)
            texto = pagina.extract_text() or ""
            m = re.search(r"Actualizado (\d{2})/(\d{2})/(\d{4})", texto)
            if m:
                fecha_lista = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
            palabras = [w for w in pagina.extract_words() if w["top"] > 112]   # debajo del encabezado
            renglones: list[list[dict]] = []
            for w in sorted(palabras, key=lambda w: (w["top"], w["x0"])):
                if renglones and abs(w["top"] - renglones[-1][0]["top"]) <= 3:
                    renglones[-1].append(w)
                else:
                    renglones.append([w])
            principales, fragmentos = [], []
            for r in renglones:
                texto_r = " ".join(w["text"] for w in sorted(r, key=lambda w: w["x0"]))
                if texto_r.startswith(("Bolsas y Mercados", "Mercado registrado")):
                    continue
                cols = {"nombre": [], "codigo": [], "mercado": [], "ratio": []}
                for w in sorted(r, key=lambda w: w["x0"]):
                    centro = (w["x0"] + w["x1"]) / 2
                    col = ("nombre" if w["x0"] < X_NOMBRE_FIN else "codigo" if centro < X_MERCADO
                           else "mercado" if centro < X_RATIO else "ratio")
                    cols[col].append(w["text"])
                reng = {"top": r[0]["top"], "cols": cols, "texto": texto_r}
                (principales if cols["codigo"] and cols["ratio"] else fragmentos).append(reng)
            # Una celda en 2-3 renglones ("LONDON STOCK / EXCHANGE", un nombre
            # largo) deja renglones sueltos con solo nombre o solo mercado: se
            # pegan al renglon completo mas cercano (<= 14 pt), en orden vertical.
            for fr in fragmentos:
                cercano = min(principales, key=lambda p: abs(p["top"] - fr["top"]), default=None)
                usadas = [c for c, v in fr["cols"].items() if v]
                if cercano is None or abs(cercano["top"] - fr["top"]) > 14 or set(usadas) - {"nombre", "mercado"}:
                    anomalias.append(f"p{n}: renglon suelto sin fila a la que pegarlo -> {fr['texto']!r}")
                    continue
                cercano.setdefault("extra", []).append(fr)
            for p in principales:
                cols = {k: list(v) for k, v in p["cols"].items()}
                for fr in sorted(p.get("extra", []), key=lambda f: f["top"]):
                    for c in ("nombre", "mercado"):
                        if fr["top"] < p["top"]:
                            cols[c] = fr["cols"][c] + cols[c]
                        else:
                            cols[c] = cols[c] + fr["cols"][c]
                if not cols["mercado"] or len(cols["ratio"]) != 1:
                    anomalias.append(f"p{n}: fila sin mercado o con mas de un ratio -> {p['texto']!r}")
                    continue
                filas.append({"nombre": " ".join(cols["nombre"]), "codigo": " ".join(cols["codigo"]),
                              "mercado": " ".join(cols["mercado"]), "ratio_texto": cols["ratio"][0], "pagina": n,
                              "multirenglon": bool(p.get("extra"))})
    return filas, fecha_lista, anomalias


# ---------------------------------------------------------------- Comafi Excel

def parsear_comafi(contenido: bytes) -> tuple[dict[str, dict], str | None, list[str]]:
    wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    filas = list(ws.iter_rows(values_only=True))
    fecha = None
    encabezado = None
    for i, f in enumerate(filas[:20]):
        if f[5] and "LISTA TOTAL" in str(f[5]).upper() and f[7]:
            d, mth, y = str(f[7]).split(".")
            fecha = f"{int(y):04d}-{int(mth):02d}-{int(d):02d}"
        if f[1] and "DENOMINACION" in str(f[1]).upper():
            encabezado = i
    if encabezado is None:
        raise RuntimeError("Excel de Comafi: no se encontro la fila de encabezado (formato cambio)")
    salida, avisos = {}, []
    for f in filas[encabezado + 1:]:
        nombre, ticker = f[1], f[2]
        if not ticker or not nombre or f[7] is None:
            continue
        ticker = str(ticker).strip()
        nombre = str(nombre).strip()
        marca = re.search(r"(\*+)\s*$", nombre)
        tipo_raw = str(f[10] or "").strip()
        tipo_norm = unicodedata.normalize("NFKD", tipo_raw).encode("ascii", "ignore").decode().lower()
        if ticker in salida:
            avisos.append(f"Comafi: ticker {ticker} duplicado, se conserva la primera fila")
            continue
        salida[ticker] = {
            "ticker": ticker, "nombre": re.sub(r"\*+\s*$", "", nombre).strip(),
            "marca": marca.group(1) if marca else None,
            "ratio_texto": str(f[7]).strip(), "isin_cedear": f[4], "isin_subyacente": f[5],
            "mercado": f[9], "tipo": TIPO_COMAFI.get(tipo_norm, "otro" if tipo_raw else None), "tipo_raw": tipo_raw,
            "pais": f[12], "industria": f[13],
        }
    return salida, fecha, avisos


# ----------------------------------------------------------------------- cruce

def armar_filas(byma: list[dict], comafi: dict[str, dict], fecha_byma: str, fecha_comafi: str | None):
    rep = {"normalizados": [], "bloqueados": [], "duplicados": [], "sin_cruce": [], "cruce_por_nombre": [],
           "difiere_comafi": [], "comafi_typos": [], "comafi_sin_byma": [], "isin_distinto": []}
    conteo = Counter(f["codigo"] for f in byma)
    # Tickers de Comafi que YA cruzan directo (o por diccionario) con algun
    # codigo BYMA: no se ofrecen al cruce por nombre. Sin esto, CSNA3 (accion
    # en B3) tomaba el Comafi de SID (el ADR), que tambien cruzaba directo.
    directos = {BYMA_A_COMAFI.get(f["codigo"], f["codigo"]) for f in byma} & set(comafi)
    comafi_por_nombre: dict[str, list[str]] = {}
    for t, c in comafi.items():
        if t not in directos:
            comafi_por_nombre.setdefault(normalizar_nombre(c["nombre"]), []).append(t)
    usados_comafi = set()
    filas = []
    for f in byma:
        cod = f["codigo"]
        if conteo[cod] > 1:
            rep["duplicados"].append(f"{cod} p{f['pagina']}: {f['nombre']} {f['mercado']} {f['ratio_texto']}")
            continue
        r = normalizar_ratio(f["ratio_texto"])
        if r is None:
            rep["bloqueados"].append(f"{cod} ({f['nombre']}): ratio '{f['ratio_texto']}' no valido")
            continue
        ced, sub, nota_ratio = r
        if nota_ratio:
            rep["normalizados"].append(f"{cod}: {nota_ratio}")
        # cruce con Comafi: diccionario explicito -> ticker igual -> nombre normalizado (unico)
        tc = BYMA_A_COMAFI.get(cod, cod)
        c = comafi.get(tc)
        if c is None:
            cands = comafi_por_nombre.get(normalizar_nombre(f["nombre"]), [])
            cands = [t for t in cands if t not in usados_comafi]
            if len(cands) == 1:
                c = comafi[cands[0]]
                rep["cruce_por_nombre"].append(f"{cod} ({f['nombre']}) <-> Comafi {cands[0]} ({c['nombre']})")
        if c is not None and cod in ISIN_ESPERADO and c["isin_subyacente"] != ISIN_ESPERADO[cod]:
            rep["isin_distinto"].append(f"{cod} <-> Comafi {c['ticker']}: ISIN {c['isin_subyacente']} "
                                        f"!= esperado {ISIN_ESPERADO[cod]} -- no se cruza")
            c = None
        notas = [n for n in (nota_ratio,) if n]
        if c is not None:
            usados_comafi.add(c["ticker"])
            rc = normalizar_ratio(c["ratio_texto"])
            if rc is None:
                rep["comafi_typos"].append(f"{cod}: Comafi dice '{c['ratio_texto']}', BYMA {ced}:{sub} (vale BYMA)")
            elif (rc[0], rc[1]) != (ced, sub):
                rep["difiere_comafi"].append(f"{cod}: BYMA {ced}:{sub} vs Comafi {rc[0]}:{rc[1]} (vale BYMA)")
            if c["marca"]:
                notas.append(LEYENDA_MARCAS.get(c["marca"], f"marca {c['marca']} de Comafi"))
        else:
            rep["sin_cruce"].append(f"{cod} ({f['nombre']}, {f['mercado']})")
        filas.append({
            "ticker_byma": cod,
            "ticker_origen": TICKER_ORIGEN_OVERRIDE.get(cod) or ticker_para_data912(c["ticker"] if c else cod),
            "nombre": f["nombre"], "mercado_origen": f["mercado"],
            "ratio_cedear": float(ced), "ratio_subyacente": float(sub), "ratio": ced / sub,
            "isin_cedear": c["isin_cedear"] if c else None, "isin_subyacente": c["isin_subyacente"] if c else None,
            "tipo": c["tipo"] if c else None, "pais": c["pais"] if c else None,
            "industria": c["industria"] if c else None, "emisor": "comafi" if c else None,
            "nota": "; ".join(notas) or None, "fuente_ratio": "byma_pdf", "fecha_lista": fecha_byma,
        })
    rep["comafi_sin_byma"] = sorted(t for t in comafi if t not in usados_comafi)
    return filas, rep


# Mercados de la lista de BYMA que NO son de EE.UU.: ahi Yahoo no se consulta
# (el ticker BYMA no es un simbolo de Yahoo y el precio no sirve contra NY).
MERCADOS_NO_USA = ("B3", "BOVESPA", "FRANKFURT", "XETRA", "LONDON STOCK EXCHANGE")
TIPO_YAHOO = {"ETF": "etf", "EQUITY": "accion"}


def clasificar_tipo_con_yahoo(filas: list[dict], rep: dict) -> None:
    """Completa `tipo` de las filas SIN Comafi con el quoteType de Yahoo (una
    consulta por ticker, solo mercados de EE.UU.). Hace falta para saber que es
    un ETF: data912 no tiene panel de ETFs y para esos el precio de NY sale de
    yfinance (decision del usuario, 2026-09-24). Si Yahoo no responde, el tipo queda
    NULL y se reporta -- nunca se deduce por el nombre."""
    import yfinance as yf

    rep["tipo_yahoo"], rep["sin_tipo"] = [], []
    for f in filas:
        if f["tipo"] is not None or f["mercado_origen"].upper() in MERCADOS_NO_USA:
            continue
        simbolo = f["ticker_origen"].replace(".", "-")
        try:
            qt = (yf.Ticker(simbolo).get_info() or {}).get("quoteType")
        except Exception as e:  # red, rate limit o ticker inexistente
            qt, error = None, str(e)[:80]
        else:
            error = None
        tipo = TIPO_YAHOO.get(qt)
        if tipo:
            f["tipo"] = tipo
            f["nota"] = "; ".join(x for x in (f["nota"], f"tipo segun Yahoo ({qt})") if x)
            rep["tipo_yahoo"].append(f"{f['ticker_byma']} ({simbolo}): {qt} -> {tipo}")
        else:
            rep["sin_tipo"].append(f"{f['ticker_byma']} ({simbolo}): quoteType={qt!r}{' ' + error if error else ''}")


COLUMNAS = ["ticker_byma", "ticker_origen", "nombre", "mercado_origen", "ratio_cedear", "ratio_subyacente", "ratio",
            "isin_cedear", "isin_subyacente", "tipo", "pais", "industria", "emisor", "nota", "fuente_ratio", "fecha_lista"]


def comparar_con_base(con, filas: list[dict]) -> dict:
    try:
        actuales = {r[0]: r[1:] for r in con.execute(
            "SELECT ticker_byma, ratio_cedear, ratio_subyacente, fecha_lista FROM ratios_cedear;")}
    except sqlite3.OperationalError:
        return {"tabla_existe": False, "altas": [f["ticker_byma"] for f in filas], "cambios_ratio": [], "bajas": [],
                "sin_cambio": 0}
    nuevos = {f["ticker_byma"]: f for f in filas}
    altas = sorted(t for t in nuevos if t not in actuales)
    bajas = sorted(t for t in actuales if t not in nuevos)
    cambios = [f"{t}: {actuales[t][0]:g}:{actuales[t][1]:g} (lista {actuales[t][2]}) -> "
               f"{nuevos[t]['ratio_cedear']:g}:{nuevos[t]['ratio_subyacente']:g}"
               for t in sorted(nuevos) if t in actuales
               and (actuales[t][0], actuales[t][1]) != (nuevos[t]["ratio_cedear"], nuevos[t]["ratio_subyacente"])]
    return {"tabla_existe": True, "altas": altas, "cambios_ratio": cambios, "bajas": bajas,
            "sin_cambio": len(nuevos) - len(altas) - len(cambios)}


def imprimir_reporte(filas, rep, diff, meta, anomalias_pdf, avisos_comafi) -> None:
    print("=" * 78)
    print(f"BYMA:   {meta['url_pdf']}\n        lista 'Actualizado' {meta['fecha_byma']} -- {meta['n_byma']} renglones parseados")
    print(f"Comafi: {COMAFI_URL}\n        lista al {meta['fecha_comafi']} -- {meta['n_comafi']} programas")
    print(f"Filas a cargar: {len(filas)} | con enriquecimiento Comafi: {sum(1 for f in filas if f['emisor'])}"
          f" | fraccionarios (1 CEDEAR = varias acciones): {sum(1 for f in filas if f['ratio_subyacente'] > 1)}")
    print("=" * 78)

    def bloque(titulo, items, maximo=60):
        print(f"\n## {titulo}: {len(items)}")
        for it in items[:maximo]:
            print(f"   {it}")
        if len(items) > maximo:
            print(f"   ... y {len(items) - maximo} mas")

    print("\n### LO MAS IMPORTANTE: contra la ultima carga")
    if not diff["tabla_existe"]:
        print("   (la tabla ratios_cedear no existe todavia: TODO es alta)")
    bloque("CAMBIOS DE RATIO (posible split o cambio de programa)", diff["cambios_ratio"])
    bloque("Bajas (estaban y ya no figuran en BYMA)", diff["bajas"])
    print(f"\n## Altas: {len(diff['altas'])} | sin cambio: {diff['sin_cambio']}")
    bloque("BLOQUEADOS en BYMA (ratio no valido, no se cargan)", rep["bloqueados"])
    bloque("DUPLICADOS en BYMA (codigo repetido con datos distintos, no se carga ninguno)", rep["duplicados"])
    bloque("Ratios normalizados (formato hora de Excel)", rep["normalizados"])
    bloque("Typos de ratio en Comafi (solo control cruzado)", rep["comafi_typos"])
    bloque("Ratio BYMA != Comafi (vale BYMA)", rep["difiere_comafi"])
    bloque("Cruzados con Comafi por NOMBRE (revisar)", rep["cruce_por_nombre"])
    bloque("Cruce NO hecho por ISIN distinto al esperado", rep["isin_distinto"])
    bloque("Tipo tomado de Yahoo (quoteType) para filas sin Comafi", rep.get("tipo_yahoo", []), maximo=200)
    bloque("Sin tipo (Yahoo no respondio o no resolvio el ticker)", rep.get("sin_tipo", []), maximo=200)
    bloque("Renglones del PDF no parseables", anomalias_pdf)
    bloque("Avisos Comafi", avisos_comafi)
    bloque("Comafi sin par en BYMA", rep["comafi_sin_byma"])
    bloque("BYMA sin cruce con Comafi (quedan sin enriquecer, no bloquea)", rep["sin_cruce"], maximo=400)


# ---------------------------------------------------------------------- carga

def hacer_backup(db_path: Path) -> None:
    destino = db_path.parent / "backups" / f"{db_path.name}.backup_ratios_cedear_{datetime.now():%Y-%m-%d_%H%M%S}"
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_path, destino)
    con = sqlite3.connect(destino)
    ok = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"Backup OK: {destino.name}")


def aplicar(db_path: Path, filas: list[dict], diff: dict, confirmar_cambios: bool) -> None:
    if not diff["tabla_existe"]:
        raise RuntimeError("La tabla ratios_cedear no existe: correr antes migrar_v237_ratios_cedear.py")
    if (diff["cambios_ratio"] or diff["bajas"]) and not confirmar_cambios:
        raise RuntimeError(f"{len(diff['cambios_ratio'])} cambio(s) de ratio y {len(diff['bajas'])} baja(s): "
                           "revisarlos en el reporte y volver a correr con --confirmar-cambios")
    hacer_backup(db_path)
    con = sqlite3.connect(db_path, isolation_level=None)
    try:
        con.execute("BEGIN;")
        marcadores = ", ".join("?" for _ in COLUMNAS)
        actualizar = ", ".join(f"{c} = excluded.{c}" for c in COLUMNAS[1:])
        for f in filas:
            con.execute(
                f"INSERT INTO ratios_cedear ({', '.join(COLUMNAS)}, actualizado_en) VALUES ({marcadores}, CURRENT_TIMESTAMP) "
                f"ON CONFLICT(ticker_byma) DO UPDATE SET {actualizar}, actualizado_en = CURRENT_TIMESTAMP;",
                [f[c] for c in COLUMNAS])
        for t in diff["bajas"]:
            con.execute("DELETE FROM ratios_cedear WHERE ticker_byma = ?;", (t,))
        n = con.execute("SELECT COUNT(*) FROM ratios_cedear;").fetchone()[0]
        if n != len(filas):
            raise RuntimeError(f"ratios_cedear quedo con {n} filas, se esperaban {len(filas)}")
        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check: {rotos}")
        con.execute("COMMIT;")
    except Exception:
        con.execute("ROLLBACK;")
        raise
    finally:
        con.close()
    con = sqlite3.connect(db_path)
    ic = con.execute("PRAGMA integrity_check;").fetchone()[0]
    con.close()
    print(f"Carga OK: {len(filas)} filas en ratios_cedear, {len(diff['bajas'])} bajas. integrity_check={ic}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--pdf", type=Path, help="PDF de BYMA guardado a mano (si no, se descarga)")
    ap.add_argument("--comafi", type=Path, help="Excel de Comafi guardado a mano (si no, se descarga)")
    ap.add_argument("--aplicar", action="store_true")
    ap.add_argument("--confirmar-cambios", action="store_true")
    ap.add_argument("--sin-yahoo", action="store_true", help="no clasificar ETF/accion con Yahoo")
    args = ap.parse_args()

    if args.pdf:
        url_pdf, contenido_pdf = str(args.pdf), args.pdf.read_bytes()
    else:
        url_pdf, _ = descubrir_pdf_byma()
        r = requests.get(url_pdf, headers=UA, timeout=60)
        r.raise_for_status()
        contenido_pdf = r.content
    if args.comafi:
        contenido_xlsx = args.comafi.read_bytes()
    else:
        r = requests.get(COMAFI_URL, headers=UA, timeout=60)
        r.raise_for_status()
        contenido_xlsx = r.content

    byma, fecha_byma, anomalias = parsear_pdf_byma(contenido_pdf)
    if not fecha_byma:
        raise RuntimeError("El PDF de BYMA no trae la fecha 'Actualizado dd/mm/aaaa'")
    comafi, fecha_comafi, avisos = parsear_comafi(contenido_xlsx)
    filas, rep = armar_filas(byma, comafi, fecha_byma, fecha_comafi)
    if not args.sin_yahoo:
        clasificar_tipo_con_yahoo(filas, rep)

    con = sqlite3.connect(f"file:{args.db.as_posix()}?mode=ro", uri=True)
    diff = comparar_con_base(con, filas)
    con.close()
    imprimir_reporte(filas, rep, diff, {"url_pdf": url_pdf, "fecha_byma": fecha_byma, "n_byma": len(byma),
                                        "fecha_comafi": fecha_comafi, "n_comafi": len(comafi)}, anomalias, avisos)
    if args.aplicar:
        aplicar(args.db, filas, diff, args.confirmar_cambios)
    else:
        print("\nDRY-RUN: no se toco la base. Para cargar: --aplicar")


if __name__ == "__main__":
    main()
