"""
PWOS -- Backfill de precio HISTORICO diario de bonos/ONs/LECAPs via
Rava (2026-08-26), para extender el panel de Rendimiento (Modified
Dietz) hacia atras del 2026-07-14 -- hasta ahora el techo real era que
`precios` solo tenia foto de renta fija en 2 fechas (ver ESTADO_PWOS.md,
relevamiento 2026-08-21).

Fuente: `GET mercado.rava.com/api/prices/historico/{mercado}/{ticker}
?dias=N` -- el MISMO endpoint no documentado que ya usa `rava_client.py`
para el resto de los datos de Rava (dominio, headers, sin autenticacion),
encontrado inspeccionando con Playwright el trafico real de
`mercado.rava.com/perfil/{ticker}` (investigacion 2026-08-26, ver
ESTADO_PWOS.md). NO es una API publica documentada -- por eso este
script pide SECUENCIAL con un delay entre cada ticker (nunca en
paralelo) y se frena de inmediato ante cualquier 429/error raro en vez
de insistir, mismo criterio de respeto que cualquier scraping no
oficial.

Convencion de precio CONFIRMADA, no asumida: se cruzo el precio de HOY
que devuelve este endpoint contra el ya cargado en `precios` desde
data912 para varios bonos y LECAPs (fuente distinta, misma fecha) --
coincide exacto. Mismo VN100, misma escala, se puede insertar en la
columna `precio` de `precios` sin ninguna conversion.

BUG REAL encontrado 2026-08-27 (el usuario detecto "valor actual" de renta
fija roto en el dashboard, un dia despues de correr este script por
primera vez) -- la version original de este script insertaba TODO con
`moneda='ARS'` a ciegas. Rava en realidad devuelve ALGUNOS bonos
cotizados YA en USD directo (las variantes "D" que liquidan en dolares,
precio cercano a la par ~90-110 -- varias ONs con sufijo D, mismo
fenomeno YA documentado para data912 en `dry_run_precios.py::
MONEDA_PRECIO_DATA912`, con la MISMA advertencia de que el sufijo O/D
del ticker NO alcanza por si solo -- hay bonos provinciales con D que cotizan en pesos).
`vista_precio_actual` toma la fila de fecha MAS RECIENTE sin mirar
`fuente` -- con el precio ya en USD guardado como si fuera ARS, se
dividia dos veces por el tipo de cambio (una vez porque el codigo
asume ARS, mas el hecho de que el numero YA era el equivalente en
USD), dejando el "valor actual" en ~1% de lo real para las ONs que
cotizan en USD directo (ganancia de -99,9%) y arrastrando el header
entero a "perdida". Fix: `MONEDA_PRECIO_RAVA` (mismo patron que
`MONEDA_PRECIO_DATA912` -- mapa a mano, verificado empiricamente
comparando el orden de magnitud, NO una heuristica de sufijo de
ticker). Un ticker de tipo con especie ambigua (bono_soberano/
provincial/cer/on -- lecap SIEMPRE es ARS, no tiene variante dolar)
que no este en el mapa NO se inserta, se frena para clasificacion
manual -- mismo criterio de seguridad que `dry_run_precios.py`.
Las 6.787 filas ya insertadas con moneda incorrecta se corrigieron a
mano con un UPDATE puntual (`database/limpiar_picos_aislados_rava_
2026-08-26.py` NO cubre esto -- fue un fix aparte, sin script dedicado
versionado, ver ESTADO_PWOS.md para el detalle completo).

Universo de tickers: TODO ticker de renta fija (bono_soberano,
bono_provincial, bono_cer, on, lecap) que aparecio ALGUNA VEZ en
`operaciones_activos` -- no solo los vigentes hoy (`vista_posiciones`)
ni solo los de `bonos_referencia` (22, solo renta fija vigente). El
Rendimiento necesita poder valuar una tenencia HISTORICA aunque ya no
este en cartera, o las anclas viejas van a seguir fallando por falta
de precio de un activo que en su momento si estaba vigente.

Cobertura real (sondeada antes de escribir este script, ver sesion
2026-08-26): de 72 tickers de renta fija con historial en la base,
**45 tienen cobertura real en Rava, 27 no (ni siquiera con una ventana
de dias=3000)** -- todos instrumentos vencidos/deslistados hace tiempo
(LECAPs viejos, ONs chicas/ilíquidas, tickers anteriores a un canje).
No se inventa nada para esos 27 -- quedan sin precio historico, mismo
criterio de siempre.

Salvaguardas de siempre: backup + integrity_check antes de escribir,
INSERT OR IGNORE sobre UNIQUE(activo_id, fecha) (nunca pisa un precio
ya cargado de otra fuente, ej. data912), todo en una unica transaccion
al final (los pedidos de red se hacen ANTES de tocar la base, para que
un corte de red a mitad de camino no deje una escritura parcial).

Uso:
    python backfill_precios_historico_bonos_rava_2026-08-26.py
"""
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests
import configuracion

DB_PATH = configuracion.ruta_db()

BASE_URL = "https://mercado.rava.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT_SEG = 20
DELAY_ENTRE_PEDIDOS_SEG = 1.5
DIAS_HISTORIA = 999999  # "todo lo que haya" -- Rava lo acota solo a la vida real del instrumento

# Confirmado por sondeo previo (2026-08-26, no incluido en el repo --
# resultado de correr el mismo tipo de pedido que este script contra
# los 72 tickers de renta fija con historial en la base). Instrumentos
# ya vencidos/deslistados de Rava hace tiempo -- quedan documentados
# aca, no se reintentan en cada corrida.
SIN_COBERTURA_CONOCIDA = set(configuracion.get("precios_rava", "sin_cobertura", default=[]))

# lecap SIEMPRE cotiza en pesos, sin variante dolar posible -- mismo
# criterio que TIPOS_CON_ESPECIE_MONEDA en dry_run_precios.py (lecap
# queda afuera de ese set a proposito). No hace falta clasificarlos acá
# uno por uno.
TIPOS_SIN_AMBIGUEDAD_MONEDA = {"lecap"}

# Clasificacion empirica por ticker (2026-08-27, corrigiendo el bug de
# "moneda='ARS' a ciegas" documentado en el docstring del modulo) --
# mismo metodo y mismo espiritu que `dry_run_precios.py::
# MONEDA_PRECIO_DATA912`: mapa a mano, verificado comparando el precio
# de Rava contra el orden de magnitud esperado (~20-170 = ya esta en
# USD; miles/decenas de miles = esta en el equivalente en pesos) Y
# cruzado contra `MONEDA_PRECIO_DATA912` donde se solapan (mismo
# resultado en las 2 fuentes). El sufijo O/D del ticker NO alcanza por
# si solo -- hay bonos provinciales con D que cotizan en pesos (ya
# documentado en `dry_run_precios.py`).
MONEDA_PRECIO_RAVA = configuracion.get("precios_rava", "moneda_precio", default={})


def moneda_precio_rava(ticker: str, tipo_activo: str) -> str | None:
    """None si el ticker es de un tipo con especie ambigua (bono, ON)
    y no esta clasificado a mano en MONEDA_PRECIO_RAVA -- el llamador
    debe saltearlo, NUNCA asumir ARS por defecto (ese fue el bug real
    de la version anterior de este script)."""
    if tipo_activo in TIPOS_SIN_AMBIGUEDAD_MONEDA:
        return "ARS"
    return MONEDA_PRECIO_RAVA.get(ticker)


def tickers_a_backfillear(con) -> list[tuple[int, str, str]]:
    """(activo_id, ticker, tipo) de TODO ticker de renta fija que
    aparecio alguna vez en operaciones_activos -- no solo vigentes
    hoy."""
    filas = con.execute("""
        SELECT DISTINCT a.id, a.ticker, a.tipo
        FROM operaciones_activos oa
        JOIN activos a ON a.id = oa.activo_id
        WHERE a.tipo IN ('bono_soberano','bono_provincial','bono_cer','on','lecap')
        ORDER BY a.ticker;
    """).fetchall()
    return [(activo_id, ticker, tipo) for activo_id, ticker, tipo in filas if ticker not in SIN_COBERTURA_CONOCIDA]


def descargar_historico(ticker: str) -> list[dict] | None:
    """[{fecha, precio}, ...] o None si Rava no tiene cobertura para
    este ticker (200 con `datos` vacio) -- distinto de un error real
    (levanta RuntimeError para que el llamador decida abortar)."""
    r = requests.get(
        f"{BASE_URL}/api/prices/historico/arg/{ticker}",
        params={"dias": DIAS_HISTORIA},
        headers=HEADERS,
        timeout=TIMEOUT_SEG,
    )
    if r.status_code == 429:
        raise RuntimeError(f"HTTP 429 (rate limit) pidiendo {ticker} -- Rava esta limitando, frenar acá.")
    if r.status_code == 404:
        return None
    r.raise_for_status()
    datos = r.json().get("datos", [])
    return [{"fecha": d["fecha"][:10], "precio": d["precio"]} for d in datos]


def main():
    con = sqlite3.connect(DB_PATH)
    tickers = tickers_a_backfillear(con)
    print(f"Tickers a pedir a Rava: {len(tickers)}")

    # Frenar ANTES de pedir nada a la red si aparece un ticker de tipo
    # ambiguo sin clasificar -- mismo criterio que dry_run_precios.py,
    # nunca asumir ARS por defecto (ese fue el bug real del 2026-08-27).
    sin_clasificar = [
        (ticker, tipo) for _, ticker, tipo in tickers
        if moneda_precio_rava(ticker, tipo) is None
    ]
    if sin_clasificar:
        print("\nABORTADO -- tickers sin clasificar en MONEDA_PRECIO_RAVA, agregar a mano antes de correr:")
        for ticker, tipo in sin_clasificar:
            print(f"  {ticker} ({tipo})")
        con.close()
        return

    resultados: dict[str, tuple[int, str, list[dict]]] = {}
    fallas_seguidas = 0
    abortado = False

    for i, (activo_id, ticker, tipo) in enumerate(tickers):
        try:
            datos = descargar_historico(ticker)
        except RuntimeError as e:
            print(f"\nABORTADO en {ticker} ({i+1}/{len(tickers)}): {e}")
            print("No se toca la base. Nada de lo ya descargado se pierde -- se puede reintentar despues.")
            abortado = True
            break
        except requests.RequestException as e:
            fallas_seguidas += 1
            print(f"[{i+1}/{len(tickers)}] {ticker}: excepcion de red ({e}), sigo -- {fallas_seguidas} falla(s) seguida(s)")
            if fallas_seguidas >= 3:
                print("\nABORTADO: 3 fallas de red seguidas, podria ser un problema de conectividad o bloqueo.")
                abortado = True
                break
            time.sleep(DELAY_ENTRE_PEDIDOS_SEG)
            continue

        fallas_seguidas = 0
        if datos is None:
            print(f"[{i+1}/{len(tickers)}] {ticker}: sin cobertura (404), se salta")
        elif not datos:
            print(f"[{i+1}/{len(tickers)}] {ticker}: 0 filas devueltas")
        else:
            resultados[ticker] = (activo_id, moneda_precio_rava(ticker, tipo), datos)
            print(f"[{i+1}/{len(tickers)}] {ticker}: {len(datos)} filas, {datos[0]['fecha']} a {datos[-1]['fecha']}")

        time.sleep(DELAY_ENTRE_PEDIDOS_SEG)

    total_filas = sum(len(datos) for _, _, datos in resultados.values())
    print(f"\n{'ABORTADO -- ' if abortado else ''}Tickers con datos: {len(resultados)}/{len(tickers)}. Total filas a insertar (antes de dedup): {total_filas}")

    if not resultados:
        print("Nada para insertar. Cierro sin tocar la base.")
        con.close()
        return

    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-precios-historico-bonos-rava_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    if backup_path.exists():
        raise RuntimeError(f"{backup_path} ya existe - no lo piso, revisar a mano")
    shutil.copy2(DB_PATH, backup_path)
    con_chk = sqlite3.connect(backup_path)
    ok = con_chk.execute("PRAGMA integrity_check;").fetchone()[0]
    con_chk.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"\nBackup OK: {backup_path} (integrity_check=ok)")

    con.execute("PRAGMA foreign_keys = ON;")
    con.execute("BEGIN;")
    insertados = 0
    try:
        for ticker, (activo_id, moneda, datos) in resultados.items():
            for fila in datos:
                cur = con.execute(
                    "INSERT OR IGNORE INTO precios (activo_id, fecha, precio, moneda, fuente) "
                    "VALUES (?, ?, ?, ?, 'rava_historico');",
                    (activo_id, fila["fecha"], fila["precio"], moneda),
                )
                insertados += cur.rowcount

        rotos = con.execute("PRAGMA foreign_key_check;").fetchall()
        if rotos:
            raise RuntimeError(f"foreign_key_check encontro filas rotas: {rotos}")

        con.execute("COMMIT;")
        print(f"\nCOMMIT OK. Filas insertadas: {insertados} (de {total_filas} descargadas -- la diferencia son fechas que ya tenian precio de otra fuente, no pisadas).")
    except Exception as e:
        con.execute("ROLLBACK;")
        print(f"ABORTADO, rollback aplicado. Motivo: {e}")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
