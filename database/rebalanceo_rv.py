"""
PWOS -- Herramienta 3 (Epica 11): rebalanceo de Renta Variable.

El motor que decide es el delta contra el peso_objetivo_pct que el usuario
mismo cargo en `tesis_inversion` -- NUNCA un ranking automatico ni la
frontera eficiente (esa es solo referencia matematica de fondo, ver
correlacion_rv.py). Sin tesis cargada para un ticker, no se sugiere
nada -- se marca explicito, no se asume un objetivo.

Reusa `calcular_ganancia_por_activo` (dashboard.py) para el valor
actual en USD de cada posicion -- no reimplementa la valorizacion en un
tercer lugar (mismo criterio que el resto del proyecto).
"""
from dashboard import calcular_ganancia_por_activo
from parametros_rv import leer_parametros

TIPOS_RENTA_VARIABLE = ("accion", "cedear")
# La banda (antes UMBRAL_DEFAULT_PP = 3.0) vive desde v2.31 en
# parametros_rv.banda_rebalanceo_pp -- se lee de ahi cuando el caller no
# pasa un umbral explicito.


def _tesis_por_ticker(con) -> dict[str, dict]:
    filas = con.execute(
        """
        SELECT a.ticker, t.peso_objetivo_pct, t.motivo, t.riesgo, t.horizonte,
               t.precio_objetivo, t.criterio_venta
        FROM tesis_inversion t
        JOIN activos a ON a.id = t.activo_id;
        """
    ).fetchall()
    return {
        ticker: {
            "peso_objetivo_pct": peso_objetivo_pct,
            "motivo": motivo,
            "riesgo": riesgo,
            "horizonte": horizonte,
            "precio_objetivo": precio_objetivo,
            "criterio_venta": criterio_venta,
        }
        for ticker, peso_objetivo_pct, motivo, riesgo, horizonte, precio_objetivo, criterio_venta in filas
    }


def rebalanceo_renta_variable(con, mep: float, ccl: float, umbral_pp: float | None = None) -> dict:
    """Peso actual (valor_actual_usd / total RV) vs. peso_objetivo_pct
    de la tesis, por posicion de accion/cedear vigente. `accion`:
    'vender' (peso actual por encima del objetivo + umbral), 'agregar'
    (por debajo - umbral), 'mantener' (dentro del umbral) o None (sin
    tesis cargada). `monto_sugerido_usd` es una aproximacion de primer
    orden (delta_pp% del total RV de HOY) -- no descuenta el efecto de
    segundo orden de que vender/comprar cambia el total, suficiente
    para decidir el monto aproximado a operar."""
    if umbral_pp is None:
        umbral_pp = leer_parametros(con)["banda_rebalanceo_pp"]
    ganancia = calcular_ganancia_por_activo(con, mep, ccl)
    posiciones = [
        p for p in ganancia
        if p["tipo"] in TIPOS_RENTA_VARIABLE and p["posicion_abierta"] and p["valor_actual_usd"] is not None
    ]
    total_rv_usd = sum(p["valor_actual_usd"] for p in posiciones)
    tesis = _tesis_por_ticker(con)

    resultado = []
    for p in posiciones:
        peso_actual_pct = (p["valor_actual_usd"] / total_rv_usd * 100) if total_rv_usd else 0.0
        fila = {
            "ticker": p["ticker"],
            "tipo": p["tipo"],
            "valor_actual_usd": p["valor_actual_usd"],
            "peso_actual_pct": round(peso_actual_pct, 2),
        }
        t = tesis.get(p["ticker"])
        if t is None or t["peso_objetivo_pct"] is None:
            fila["motivo"] = "sin tesis/peso objetivo definido"
            resultado.append(fila)
            continue

        delta_pp = peso_actual_pct - t["peso_objetivo_pct"]
        if delta_pp > umbral_pp:
            accion = "vender"
        elif delta_pp < -umbral_pp:
            accion = "agregar"
        else:
            accion = "mantener"

        fila.update({
            "peso_objetivo_pct": t["peso_objetivo_pct"],
            "delta_pp": round(delta_pp, 2),
            "accion": accion,
            "monto_sugerido_usd": round(abs(delta_pp) / 100 * total_rv_usd, 2) if accion != "mantener" else 0.0,
            "motivo_tesis": t["motivo"],
            "riesgo_tesis": t["riesgo"],
            "horizonte_tesis": t["horizonte"],
            "precio_objetivo": t["precio_objetivo"],
            "criterio_venta": t["criterio_venta"],
        })
        resultado.append(fila)

    resultado.sort(key=lambda f: -abs(f.get("delta_pp", 0)))
    return {"total_rv_usd": round(total_rv_usd, 2), "umbral_pp": umbral_pp, "posiciones": resultado}
