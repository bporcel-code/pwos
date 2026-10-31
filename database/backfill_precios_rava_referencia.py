"""
backfill_precios_historico_bonar_global_cer_2026-09-18.py -- PWOS

Extiende el backfill de precio historico de renta fija (ver
backfill_precios_historico_bonos_rava_2026-08-26.py) del universo
"tickers que el usuario alguna vez opero" al universo COMPLETO de Bonares +
Globales + CER de Rava (69 tickers, incluidas las 3 especies O/C/D de
cada bono), 2026-09-18: poder ver ratios de precio
entre TODOS los bonos de estas 3 categorias, no solo los que tiene en
cartera. Reusa `descargar_historico()` del script original (MISMO
endpoint, mismas salvaguardas de scraping respetuoso) -- no reimplementa
el pedido de red.

Requiere que los 54 tickers nunca operados ya esten dados de alta en
`activos` -- ver alta_activos_bonar_global_cer_2026-09-18.py, corrido
antes que este script.

Clasificacion de moneda (MONEDA_PRECIO_RAVA_NUEVOS), verificada
empiricamente hoy (no asumida): se pidio el precio real de HOY de los
54 tickers nuevos y se comparo el orden de magnitud contra los 30
tickers YA clasificados en el script original.
  - Toda especie con sufijo C/D (cable/MEP) cotiza YA en USD directo
    (rango ~50-105) -- sin excepcion en este universo puntual (a
    diferencia de algunos Provinciales/ONs con D que cotizan en pesos,
    excepcion conocida al patron de sufijo -- no aplica aca).
  - Todo ticker BASE sin sufijo C/D (AL29, AL35, AL41, AN29, GD38,
    GD46, DICP, DIP0, PAP0, PARP, TX31) cotiza en el equivalente en
    pesos, rango miles/decenas de miles -- mismo patron que AL30/
    GD29/TX26 ya clasificados.
  - CASO VERIFICADO A PROPOSITO por parecer ambiguo a primera vista: la
    familia "TZX + letra de mes + digito" (TZX27/28, TZXA7, TZXD7/D8,
    TZXM8/M9, TZXO6/O7, TZXS7/S8, TZXY7) y las 2 LECER (X30N6/X30S6)
    cotizan en un rango chico (~86-410), MUY por debajo de TX26/
    TX31/DICP/PARP (miles) pese a NO tener sufijo C/D -- se comparo
    contra TZXD6/TZXM7 (YA clasificados ARS, ya verificados en
    produccion), que HOY cotizan en el MISMO rango chico (306.20 y
    227.25) -- confirma que es un rasgo propio de esta sub-familia de
    CER (capitalizacion/VN distinto, no una especie en dolares), no una
    señal de moneda. Los 25 tickers de esta forma se clasifican ARS.

Uso (parado en database/):
    python backfill_precios_historico_bonar_global_cer_2026-09-18.py
"""
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import requests

from clasificar_bonos import clasificar
import configuracion

DB_PATH = configuracion.ruta_db()
DELAY_ENTRE_PEDIDOS_SEG = 1.5

# Mismo endpoint/headers/timeout que backfill_precios_historico_bonos_rava_2026-08-26.py
# -- duplicado en vez de importado porque ese archivo tiene guiones en el
# nombre (no es un modulo Python importable tal cual).
BASE_URL = "https://mercado.rava.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}
TIMEOUT_SEG = 20
DIAS_HISTORIA = 999999


def descargar_historico(ticker: str) -> list[dict] | None:
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

# (25 base sin especie -> ARS, 29 especie C/D -> USD, ver docstring)
MONEDA_PRECIO_RAVA_NUEVOS = {
    "AL29": "ARS", "AL35": "ARS", "AL41": "ARS", "AN29": "ARS",
    "GD38": "ARS", "GD46": "ARS", "DICP": "ARS", "DIP0": "ARS",
    "PAP0": "ARS", "PARP": "ARS", "TX31": "ARS",
    "TZX27": "ARS", "TZX28": "ARS", "TZXA7": "ARS", "TZXD7": "ARS",
    "TZXD8": "ARS", "TZXM8": "ARS", "TZXM9": "ARS", "TZXO6": "ARS",
    "TZXO7": "ARS", "TZXS7": "ARS", "TZXS8": "ARS", "TZXY7": "ARS",
    "X30N6": "ARS", "X30S6": "ARS",
    "AE38C": "USD", "AE38D": "USD", "AL29C": "USD", "AL29D": "USD",
    "AL30C": "USD", "AL35C": "USD", "AL35D": "USD", "AL41C": "USD",
    "AL41D": "USD", "AN29C": "USD", "AN29D": "USD", "AO27C": "USD",
    "AO27D": "USD", "AO28C": "USD", "AO28D": "USD", "AO29C": "USD",
    "AO29D": "USD", "GD29C": "USD", "GD29D": "USD", "GD30C": "USD",
    "GD30D": "USD", "GD35C": "USD", "GD35D": "USD", "GD38C": "USD",
    "GD38D": "USD", "GD41C": "USD", "GD41D": "USD", "GD46C": "USD",
    "GD46D": "USD",
}


def tickers_bonar_global_cer(con) -> list[tuple[int, str]]:
    filas = con.execute("SELECT ticker, tipo_instrumento, legislacion FROM mercado_renta_fija_rava;").fetchall()
    universo = sorted({tk for tk, ti, le in filas if clasificar(tk, ti, le) in ("bonar", "global", "cer")})
    resultado = []
    for ticker in universo:
        row = con.execute("SELECT id FROM activos WHERE ticker = ?;", (ticker,)).fetchone()
        if row is None:
            raise RuntimeError(f"'{ticker}' no esta en activos -- correr alta_activos_bonar_global_cer_2026-09-18.py primero.")
        resultado.append((row[0], ticker))
    return resultado


def moneda_de(ticker: str) -> str | None:
    return MONEDA_PRECIO_RAVA_NUEVOS.get(ticker)


def main():
    con = sqlite3.connect(DB_PATH)
    tickers = tickers_bonar_global_cer(con)
    print(f"Universo Bonares/Globales/CER: {len(tickers)} tickers.")

    resultados: dict[str, tuple[int, str, list[dict]]] = {}
    fallas_seguidas = 0
    abortado = False

    for i, (activo_id, ticker) in enumerate(tickers):
        try:
            datos = descargar_historico(ticker)
        except RuntimeError as e:
            print(f"\nABORTADO en {ticker} ({i + 1}/{len(tickers)}): {e}")
            print("No se toca la base. Nada de lo ya descargado se pierde -- se puede reintentar despues.")
            abortado = True
            break
        except requests.RequestException as e:
            fallas_seguidas += 1
            print(f"[{i + 1}/{len(tickers)}] {ticker}: excepcion de red ({e}), sigo -- {fallas_seguidas} falla(s) seguida(s)")
            if fallas_seguidas >= 3:
                print("\nABORTADO: 3 fallas de red seguidas, podria ser un problema de conectividad o bloqueo.")
                abortado = True
                break
            time.sleep(DELAY_ENTRE_PEDIDOS_SEG)
            continue

        fallas_seguidas = 0
        if datos is None:
            print(f"[{i + 1}/{len(tickers)}] {ticker}: sin cobertura (404), se salta")
        elif not datos:
            print(f"[{i + 1}/{len(tickers)}] {ticker}: 0 filas devueltas")
        else:
            moneda = moneda_de(ticker)
            if moneda is None:
                print(f"[{i + 1}/{len(tickers)}] {ticker}: SIN CLASIFICAR en MONEDA_PRECIO_RAVA_NUEVOS -- se salta, revisar a mano.")
            else:
                resultados[ticker] = (activo_id, moneda, datos)
                print(f"[{i + 1}/{len(tickers)}] {ticker}: {len(datos)} filas, {datos[0]['fecha']} a {datos[-1]['fecha']} ({moneda})")

        time.sleep(DELAY_ENTRE_PEDIDOS_SEG)

    total_filas = sum(len(datos) for _, _, datos in resultados.values())
    print(f"\n{'ABORTADO -- ' if abortado else ''}Tickers con datos: {len(resultados)}/{len(tickers)}. Total filas a insertar (antes de dedup): {total_filas}")

    if not resultados:
        print("Nada para insertar. Cierro sin tocar la base.")
        con.close()
        return

    backup_dir = DB_PATH.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"pwos_backup_pre-precios-bonar-global-cer_{datetime.now():%Y-%m-%d_%H%M%S}.db"
    shutil.copy2(DB_PATH, backup_path)
    con_chk = sqlite3.connect(backup_path)
    ok = con_chk.execute("PRAGMA integrity_check;").fetchone()[0]
    con_chk.close()
    if ok != "ok":
        raise RuntimeError(f"Backup corrupto (integrity_check={ok}), abortando")
    print(f"\nBackup OK: {backup_path.name} (integrity_check=ok)")

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
