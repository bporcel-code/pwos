"""
PWOS -- clasificador de bonos por categoria de mercado (2026-09-08,
revisado 2026-09-09).

Motivo: la tabla "Mercado de bonos" del dashboard
lista ~890 especies de Rava sin ninguna agrupacion -- "un mundo de
bonos que no sabemos a que categoria pertenecen". Rava NO ayuda: el
`nombre` que devuelve es solo el ticker repetido (o vacio),
`tipo_instrumento` viene NULL para el 57% de las especies, y
`legislacion` = '-' para el 57%. Asi que la clasificacion es casi toda
por PATRON DE TICKER (la convencion de nomenclatura del mercado
argentino), con un diccionario de excepciones a mano para los casos que
el patron no cubre -- mismo criterio que MONEDA_PRECIO_DATA912 /
ESPECIE_CANONICA en el resto del proyecto.

Reglas de negocio puras: no toca la base ni la red (mismo patron de
separacion que clasificar_yfinance.py).

Objetivo: que los buckets CHICOS y bien definidos que el usuario usa para
decidir (Letras, BONCAP, Duales, Bonares, Globales, BOPREAL, CER,
dollar-linked, provinciales) queden 100% limpios. El universo de ONs
corporativas es enorme y sin metadata confiable -> lo que no matchea un
instrumento nacional reconocido cae en 'on' (si Rava lo tageo),
'nacional' (Titulo Publico ley ARG sin serie identificable) u 'otro'.

Revision 2026-09-09 (el usuario): la categoria 'provincial' tenia adentro
bonos DUALES (familia TXM*/TXD*/TXJ*/TT*, ej. TXMJ0-TXMJ9) y otros
Titulos Publicos nacionales. Un bono provincial REAL siempre lleva el
nombre de una provincia argentina -- como Rava no da el nombre, la
unica senal fiable es el prefijo del ticker (cada provincia emite con
su propio prefijo). Se saco el fallback "Titulo Publico ARG ->
provincial" (metia nacionales), se agrego la categoria 'dual', y
'provincial' ahora es SOLO prefijo de provincia + override.
"""

import re

import configuracion

# Orden = orden de aparicion en el filtro del dashboard.
CATEGORIAS = [
    ("letra",         "Letras (LECAP)"),
    ("boncap",        "BONCAP / Bonte tasa fija"),
    ("tamar",         "Bono TAMAR (tasa flotante)"),
    ("dual",          "Duales (TAMAR/CER o TAMAR/tasa fija)"),
    ("cer",           "CER / ajustables"),
    ("dollar_linked", "Dollar-linked"),
    ("bonar",         "Bonares (ley local)"),
    ("global",        "Globales (ley NY)"),
    ("bopreal",       "BOPREAL"),
    ("provincial",    "Provinciales / sub-soberanos"),
    ("nacional",      "Bonos nacionales (otros)"),
    ("on",            "Obligaciones Negociables"),
    ("otro",          "Otros / sin clasificar"),
]
CATEGORIA_LABEL = dict(CATEGORIAS)

# Excepciones: el patron de ticker no alcanza o daria el bucket
# equivocado. Verificado a mano contra los tickers de la cartera +
# conocidos. Ampliable cuando aparezca un caso mal clasificado.
#
# Las especies "en dolares" (sufijo D corto, no reducible a la forma
# base con _sin_sufijo_especie) de LECAPs / BONCAPs / TAMAR van todas
# aca -- confirmado por el usuario 2026-09-09, ticker por ticker.
CATEGORIA_OVERRIDE = {
    # LECAPs, especie en dolares
    "SN6D": "letra", "SO6D": "letra", "SS6D": "letra",
    # BONCAPs (tasa fija), especie en dolares o forma corta
    "TA7D": "boncap", "TE7D": "boncap", "TY7D": "boncap",
    "TO26": "boncap", "TO26D": "boncap", "TY30D": "boncap", "TY30P": "boncap",
}
# Los del usuario (tickers puntuales de su cartera), de la configuracion.
CATEGORIA_OVERRIDE.update(configuracion.get("bonos", "categoria_override", default={}))

# CER "clasicos" sin patron T-X/T-ZX (nombres historicos).
_CER_EXACTOS = {
    "CUAP", "DICP", "DIP0", "PARP", "PAP0", "DICY", "PARY", "PAPY", "DIPY",
    "PR13", "PR15", "PR17",
}

# Prefijos de emisores PROVINCIALES / sub-soberanos. Cada jurisdiccion
# emite con su propio prefijo -- es la unica senal fiable (Rava no da el
# nombre, que siempre lleva la provincia). Lista de los que aparecen hoy
# en el snapshot + las provincias grandes; ampliable. Se chequean como
# prefijo de string, asi que van de mas especifico a menos importa poco
# (startswith con tupla). NO se aplica si Rava tagea la especie como ON.
_PROV_PREFIJOS = (
    # Buenos Aires (provincia) -> PB*, BA*, BAF ; CABA (Ciudad) -> BDC*
    "PB", "BA37", "BA7D", "BAF", "BADED", "BDED", "BPLD", "BPLE",
    "BDC", "CABA",
    # Cordoba -> CO*
    "CO", "CBA",
    # Entre Rios -> ER*
    "ER",
    # Mendoza -> PM*
    "PM",
    # Neuquen -> ND*, NEU*, NQN
    "ND", "NEU", "NQN",
    # Chubut -> RUD, PU*. "RUC" SACADO de aca (2026-09-16, el usuario reporto
    # RUCEO/RUCED como provinciales siendo ONs): hoy TODO ticker "RUC*"
    # vivo en el snapshot de Rava (RUCEO/RUCED/RUCDC/RUCDD/RUCDO) es de
    # MSU Energy (empresa privada, confirmado por `descripcion` real
    # via la API de un broker: "ON MSU ENERGY CL.13.."/"MSU ENERGY S.A. REGS.."), no de
    # Chubut -- `tipo_instrumento` viene NULL para estas 5 filas, asi
    # que el guard `not es_on` no las salva y caian en 'provincial' por
    # el prefijo compartido. Las ONs que ya tienen `tipo='on'` en `activos`
    # 2026-07-11) ya no estan en el snapshot vigente de Rava -- sin
    # ningun RUC* real de Chubut para verificar hoy, se saca el prefijo
    # entero en vez de intentar distinguir por coincidencia.
    "RUD", "PU",
    # Tierra del Fuego
    "TFU",
    # Chaco
    "CHSG", "CHAQ", "CHAC",
    # Santa Fe -> SF*
    "SF",
    # Rio Negro -> RN*
    "RN",
    # Tucuman
    "TUCS", "TUBB", "TUC",
    # San Juan / La Rioja / Jujuy / Corrientes / Formosa / La Pampa / Misiones
    "SJS", "SJU", "RIO", "LRD", "JUM", "JUS", "CTES", "FORM", "LPO", "MSU",
)

# TAMAR = Tasa Mayorista Argentina (referencia del BCRA para plazos
# fijos mayoristas en pesos). Distinguir 3 cosas (el usuario, 2026-09-09):
#  - Bono TAMAR (tasa flotante pura): familia TM* (TMF27, TMG28, TML7D..).
#  - Dual: rinde lo mejor entre TAMAR y otra cosa (CER o tasa fija)
#    segun que subio mas en el periodo. Familia TXM*/TXD*/TXJ* (dual
#    TAMAR/CER) y TTD26/TTS26 (dual tasa fija/TAMAR).
#  - Tasa fija (BONCAP): T30A7, TO26, TY30.. -> categoria 'boncap'.
# TTC* NO es dual (corporativas, quedan en 'on' por el sufijo O/C/D).
_RE_TAMAR = re.compile(r"^TM[A-Z]")
_RE_DUAL = re.compile(r"^(TXM|TXD|TXJ|TT[DSMJ]\d)")


def _sin_sufijo_especie(ticker: str) -> str:
    """Saca el sufijo C/D (cable/MEP) de una especie: AL30C/AL30D -> AL30.
    Solo cuando hay un digito justo antes (para no comer la D de un
    ticker legitimo terminado en D que ya es su forma canonica)."""
    return re.sub(r"(?<=\d)[CD]$", "", ticker)


def clasificar(ticker: str, tipo_instrumento: str | None = None,
               legislacion: str | None = None) -> str:
    """Devuelve la clave de categoria (ver CATEGORIAS). Nunca levanta."""
    t = (ticker or "").strip().upper()
    if not t:
        return "otro"
    if t in CATEGORIA_OVERRIDE:
        return CATEGORIA_OVERRIDE[t]

    base = _sin_sufijo_especie(t)
    tipo = tipo_instrumento or ""
    es_on = tipo.startswith("O")   # "Obligación Negociable" -- unico valor con O
    es_tp = tipo.startswith("T")   # "Título Público"

    # BOPREAL: el BCRA los emite como BP + serie [A-D] + digito (BPA7,
    # BPB8, BPD7..), o BPO + serie (BPOA7, BPOB7..), o BPY/BPJ (series
    # historicas). NO alcanza con "empieza con BP": la familia BPCT*/
    # BPCU*/BPCV*/BPCW* (BPC + LETRA + O/D, sin metadata en Rava) son ONs
    # corporativas, no BOPREAL (el usuario, 2026-09-10).
    if re.match(r"^BP(O?[A-D]\d|Y\d|J\d)", base):
        return "bopreal"

    # Globales (ley NY) y Bonares (ley local) -- por prefijo de ticker.
    if re.match(r"^GD\d{2}$", base):
        return "global"
    if re.match(r"^A[A-Z]\d{2}$", base):   # AL/AE/AO/AY/AA.. + 2 digitos
        return "bonar"

    # CER / ajustables por CER: TX+digito (TX26/TX31), o TZX.. (con o
    # sin letra de mes: TZX27, TZXA7, TZXD7), o TC+digito, o los
    # nombres historicos. La familia TZV* NO es CER -- es dollar-linked
    # (el usuario, 2026-09-10), ver mas abajo.
    #
    # LECER (letra ajustada por CER): X + dia + letra-mes + anio (mismo
    # patron de fecha que la LECAP "S..", pero prefijo X) -- ej. X30S6,
    # X30N6. Encontrado 2026-09-16 (el usuario reporto X30S6/X30N6/X29E7
    # "faltando" del bucket CER): estos 2 tickers estaban cayendo en
    # 'dollar_linked' por el patron generico `X\d{1,2}[A-Z]\d` de esa
    # regex (ver mas abajo) -- confirmado el error contra la
    # `descripcion` real (fuente: la API de un broker, ver enriquecer_mercado_bonos.py):
    # "LETRA TESORO NACIONAL AJUSTABLE CER...", no "VINC. USD" como los
    # D../TZV* genuinamente dollar-linked. Sacado de la regex de
    # dollar-linked y sumado aca. (X29E7 no aparece en el universo de
    # Rava a esta fecha -- no es un bug de clasificacion, ver ticket.)
    if re.match(r"^T(X\d|ZX|C\d)", base) or re.match(r"^X\d{1,2}[A-Z]\d$", base) or base in _CER_EXACTOS:
        return "cer"

    # Duales y Bono TAMAR -- ANTES de dollar-linked/letra/boncap para que
    # las familias TXM/TXD/TXJ/TT/TM no se escapen por otro patron.
    if _RE_DUAL.match(base):
        return "dual"
    if _RE_TAMAR.match(base):
        return "tamar"

    # Dollar-linked (letras y bonos vinculados al oficial A3500). Incluye
    # la familia TZV* (TZV27/TZV28/TZVD8..) -- confirmado por el usuario
    # (2026-09-10), no van en CER. NO incluye X.. -- esas son LECER
    # (ver bloque CER mas arriba, 2026-09-16).
    if re.match(r"^(D\d{1,2}[A-Z]\d|TV\d|TVP|TZV)", base):
        return "dollar_linked"

    # Letras capitalizables en pesos, tasa fija (LECAP): S + dia + mes + anio.
    if re.match(r"^S\d{1,2}[A-Z]\d$", base):
        return "letra"

    # BONCAP / Bonte tasa fija: T + dia + mes + anio (mismo patron que la
    # LECAP pero prefijo T). Va DESPUES de CER/dual.
    if re.match(r"^T\d{1,2}[A-Z]\d$", base):
        return "boncap"

    # Provinciales / sub-soberanos: SOLO por prefijo de emisor (cada
    # provincia tiene el suyo). No se aplica si Rava lo tagea ON.
    if not es_on and base.startswith(_PROV_PREFIJOS):
        return "provincial"

    if es_on:
        return "on"

    # Titulo Publico nacional que no cayo en ninguna serie identificable
    # (ej. TMF27/TA7D/TO26/TY30D/SN6D...). NO es provincial -- un bono
    # provincial siempre lleva el nombre de una provincia y su prefijo.
    if es_tp and legislacion in ("ARG", "NY"):
        return "nacional"

    # Sin metadata utilizable de Rava. Las ONs corporativas usan sufijo
    # de especie O/C/D (O=pesos, D=MEP, C=cable) -- por descarte (todo lo
    # nacional ya matcheo arriba), un ticker suelto terminado en O/C/D es
    # casi siempre una ON. No es certeza pero es el bucket correcto para
    # el 99% de estos casos.
    if t[-1] in ("O", "C", "D"):
        return "on"

    return "otro"


if __name__ == "__main__":
    import sqlite3
    from pathlib import Path
    import collections

    db = configuracion.ruta_db()
    con = sqlite3.connect(db)

    filas = con.execute(
        "SELECT ticker, tipo_instrumento, legislacion FROM mercado_renta_fija_rava WHERE tir IS NOT NULL"
    ).fetchall()
    cont = collections.Counter(clasificar(*f) for f in filas)
    print(f"{len(filas)} especies\n")
    for clave, label in CATEGORIAS:
        ejemplos = [tk for tk, ti, le in filas if clasificar(tk, ti, le) == clave][:14]
        print(f"  {label:32} {cont.get(clave, 0):4}   {', '.join(ejemplos)}")

    print("\n--- provinciales (control: todos deben ser de una provincia) ---")
    for tk, ti, le in sorted(filas):
        if clasificar(tk, ti, le) == "provincial":
            print(f"  {tk}", end="")
    print()

    print("\n--- cartera de renta fija ---")
    cart = [r[0] for r in con.execute(
        "SELECT DISTINCT a.ticker FROM vista_posiciones vp JOIN activos a ON a.id=vp.activo_id "
        "WHERE a.tipo IN ('bono_soberano','bono_provincial','bono_cer','on','lecap') ORDER BY a.ticker"
    ).fetchall()]
    byt = {tk: (ti, le) for tk, ti, le in filas}
    for tk in cart:
        ti, le = byt.get(tk, (None, None))
        marca = "" if tk in byt else "  (no en snapshot Rava)"
        print(f"  {tk:8} -> {CATEGORIA_LABEL[clasificar(tk, ti, le)]}{marca}")
    con.close()
