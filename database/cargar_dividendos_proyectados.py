"""
PWOS -- cronograma de dividendos POR ACCION desde yfinance (ticket "Flujo de
pagos mensual: estimado vs. real", Tanda 2, 2026-10-05; schema v2.46).

Para cada posicion de renta variable (accion local o CEDEAR) arma las
fechas ex / de pago y el monto bruto por accion del subyacente:
  - 'historico': el ultimo dividendo ya pagado en el exterior cuya
    acreditacion estimada todavia cae en el mes en curso o despues.
  - 'anunciado': `Ticker.calendar` trae una fecha ex POSTERIOR a la ultima
    del historial (la proxima, ya declarada) y su fecha de pago.
  - 'proyectado': repite el patron hasta HORIZONTE_DIAS: periodo = mediana
    entre las ultimas fechas ex (anual si en el ultimo ano hubo un solo
    pago), monto = el ultimo.
Fecha de pago:
  - CEDEAR: fecha de pago en EE.UU. + DEMORA_ACREDITACION_CEDEAR. La
    distancia ex->pago sale del calendario de yfinance (PEP: ex 03/09, pago
    29/09); si no la trae, DEMORA_EX_PAGO_DEFAULT. Calibrado contra cobros
    reales (calibrar_dividendos.py).
  - Accion local: fecha ex + DEMORA_LOCAL (ej.: ex 03/11, cobro 05/11).
Si en los ultimos ULTIMO_PAGO_MAX_DIAS no hubo ningun dividendo, no se
proyecta nada (ej. una accion local que no paga -- "no se inventa un dividendo").

La multiplicacion por tenencia/ratio/factor neto la hace flujo_estimado.py
(local). La re-ejecuta puesta_al_dia.py si tiene mas de 24 h.

Uso (parado en database/):
    python cargar_dividendos_proyectados.py [--db COPIA.db] [--dry-run]
"""
import argparse
import sqlite3
import statistics
import warnings
from datetime import date, datetime, timedelta
from pathlib import Path
import configuracion

DB_PATH = configuracion.ruta_db()

HORIZONTE_DIAS = 400
ULTIMO_PAGO_MAX_DIAS = 400
DEMORA_ACREDITACION_CEDEAR = 2
DEMORA_EX_PAGO_DEFAULT = 25
DEMORA_LOCAL = 2


def posiciones_rv(con) -> list[tuple[int, str, str, str]]:
    """(activo_id, ticker, simbolo_yahoo, moneda) de cada posicion abierta."""
    filas = con.execute(
        """
        SELECT DISTINCT vp.activo_id, vp.ticker, vp.tipo_activo, rc.ticker_origen
        FROM vista_posiciones vp
        LEFT JOIN ratios_cedear rc ON rc.ticker_byma = vp.ticker
        WHERE vp.tipo_activo IN ('accion', 'cedear') AND vp.cantidad > 0;
        """
    ).fetchall()
    salida = []
    for activo_id, ticker, tipo, origen in filas:
        if tipo == "accion":
            salida.append((activo_id, ticker, f"{ticker}.BA", "ARS"))
        elif origen:
            salida.append((activo_id, ticker, origen.replace(".", "-"), "USD"))
    return salida


def armar_cronograma(divs: list[tuple[date, float]], cal: dict, moneda: str, hoy: date) -> list[dict]:
    """Funcion pura (testeable sin red). `divs` = historial (fecha_ex, monto)
    ordenado; `cal` = Ticker.calendar ({} si no hay)."""
    divs = [d for d in divs if d[1] > 0]
    if not divs or (hoy - divs[-1][0]).days > ULTIMO_PAGO_MAX_DIAS:
        return []
    es_local = moneda == "ARS"
    ex_cal, pago_cal = cal.get("Ex-Dividend Date"), cal.get("Dividend Date")
    demora_ex_pago = DEMORA_EX_PAGO_DEFAULT
    if isinstance(ex_cal, date) and isinstance(pago_cal, date) and pago_cal >= ex_cal:
        demora_ex_pago = (pago_cal - ex_cal).days

    def fecha_pago(ex: date) -> date:
        if es_local:
            return ex + timedelta(days=DEMORA_LOCAL)
        return ex + timedelta(days=demora_ex_pago + DEMORA_ACREDITACION_CEDEAR)

    ultimo_ex, ultimo_monto = divs[-1]
    ultimo_ano = [d for d, _ in divs if (ultimo_ex - d).days < 365]
    if len(ultimo_ano) >= 2:
        recientes = [d for d, _ in divs[-5:]]
        periodo = statistics.median((b - a).days for a, b in zip(recientes, recientes[1:]))
    else:
        periodo = 365

    eventos = []
    inicio_mes = hoy.replace(day=1)
    if fecha_pago(ultimo_ex) >= inicio_mes:
        eventos.append({"fecha_ex": ultimo_ex, "fecha_pago": fecha_pago(ultimo_ex),
                        "monto_por_accion": ultimo_monto, "tipo": "historico"})
    base = ultimo_ex
    # El calendario y el historial pueden diferir en 1 dia (zona horaria):
    # "posterior" = mas de 5 dias despues del ultimo ex del historial.
    if isinstance(ex_cal, date) and (ex_cal - ultimo_ex).days > 5:
        pago = (pago_cal + timedelta(days=DEMORA_ACREDITACION_CEDEAR)
                if isinstance(pago_cal, date) and not es_local else fecha_pago(ex_cal))
        eventos.append({"fecha_ex": ex_cal, "fecha_pago": pago,
                        "monto_por_accion": ultimo_monto, "tipo": "anunciado"})
        base = ex_cal
    k = 1
    while True:
        ex = base + timedelta(days=round(periodo * k))
        if (ex - hoy).days > HORIZONTE_DIAS:
            break
        eventos.append({"fecha_ex": ex, "fecha_pago": fecha_pago(ex),
                        "monto_por_accion": ultimo_monto, "tipo": "proyectado"})
        k += 1
    return eventos


def descargar(simbolo: str) -> tuple[list[tuple[date, float]], dict]:
    import yfinance as yf
    warnings.filterwarnings("ignore")
    t = yf.Ticker(simbolo)
    divs = [(ts.date(), float(v)) for ts, v in t.dividends.items()]
    try:
        cal = t.calendar or {}
    except Exception:
        cal = {}
    return divs, cal if isinstance(cal, dict) else {}


def cargar(db_path: Path = DB_PATH, dry_run: bool = False, hoy: date | None = None) -> dict:
    hoy = hoy or date.today()
    con = sqlite3.connect(db_path)
    ahora = datetime.now().isoformat(timespec="seconds")
    filas, sin_dividendos, errores = [], [], []
    for activo_id, ticker, simbolo, moneda in posiciones_rv(con):
        try:
            divs, cal = descargar(simbolo)
        except Exception as e:
            errores.append(f"{ticker} ({simbolo}): {type(e).__name__}: {e}"[:200])
            continue
        eventos = armar_cronograma(divs, cal, moneda, hoy)
        if not eventos:
            sin_dividendos.append(ticker)
        for ev in eventos:
            filas.append((activo_id, simbolo, ev["fecha_ex"].isoformat(), ev["fecha_pago"].isoformat(),
                          ev["monto_por_accion"], moneda, ev["tipo"], ahora))
            print(f"  {ticker:6s} {simbolo:9s} ex {ev['fecha_ex']}  pago {ev['fecha_pago']}  "
                  f"{moneda} {ev['monto_por_accion']:.4f}  {ev['tipo']}")
    if sin_dividendos:
        print(f"Sin dividendos en el ultimo ano (no se proyecta): {', '.join(sorted(sin_dividendos))}")
    if errores:
        print("Errores de yfinance (esos activos conservan lo anterior):\n  " + "\n  ".join(errores))
    if dry_run:
        print(f"(dry-run: {len(filas)} filas, no se escribio nada)")
        con.close()
        return {"filas": len(filas), "errores": errores}

    activos_ok = {f[0] for f in filas} | {
        a for a, t, _, _ in posiciones_rv(con) if t in sin_dividendos}
    with con:
        # Se reemplazan solo los activos consultados con exito; los vendidos se limpian.
        con.execute(
            "DELETE FROM dividendos_proyectados WHERE activo_id NOT IN "
            "(SELECT activo_id FROM vista_posiciones WHERE tipo_activo IN ('accion','cedear'));")
        con.executemany("DELETE FROM dividendos_proyectados WHERE activo_id = ?;", [(a,) for a in activos_ok])
        con.executemany("INSERT INTO dividendos_proyectados VALUES (?, ?, ?, ?, ?, ?, ?, ?);", filas)
    con.close()
    print(f"Guardadas {len(filas)} filas en dividendos_proyectados.")
    if errores and not filas:
        raise RuntimeError("yfinance fallo para todos los activos")
    return {"filas": len(filas), "errores": errores}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_PATH)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    cargar(args.db, args.dry_run)
