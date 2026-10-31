"""
PWOS -- Alertas de la solapa Analisis - Acciones (Epica 19 Fase 2,
2026-09-21). Una sola lista alimenta tanto el panel "Alertas y
semaforos" como el KPI "Alertas activas", asi no pueden diferir.

Tablero de decision, no de recomendacion: cada alerta muestra el dato y
la REGLA del marco del usuario; la decision de operar o no es suya.

Reglas (cada alerta tiene `estado`: 'activa' cuenta en el KPI; 'revisada'
y 'justificada' siguen visibles pero atenuadas y NO cuentan):

  revision_obligatoria -- la posicion esta a -30 % o peor desde su costo
      en USD (`ganancia_porcentaje` de calcular_ganancia_por_activo: valor
      actual vs. lo invertido al dolar de cada compra). Corre para TODAS
      las posiciones, con o sin tesis. Un -30 % no tiene una fecha de
      disparo que el sistema conozca, asi que se da por 'revisada' cuando
      `tesis_inversion.proxima_revision` es una fecha FUTURA (= "ya la
      revise, la proxima es tal dia"). Sin ganancia verificable no se
      alerta (nunca se asume).
  indicador_rojo -- la ULTIMA lectura de un indicador activo es roja.
      Regla: reducir de forma escalonada salvo que se documente por
      escrito por que la tesis sigue viva. Se da por 'justificada' si hay
      una entrada de la bitacora de tesis de Acciones (`tesis_acciones`,
      ticker, `fecha` o `fecha_reflexion`) con fecha IGUAL O POSTERIOR a
      esa lectura roja -- se reactiva sola si entra una lectura roja mas
      nueva. El sistema NO juzga el contenido de la entrada: solo
      registra que existe una, fechada. (El punto del semaforo en la
      tabla nunca se apaga: es el estado real del indicador.)
  indicador_amarillo -- las 2 ULTIMAS lecturas de un indicador activo son
      amarillas (2 periodos seguidos = 2 lecturas cargadas, no un plazo de
      calendario). Regla: frenar aportes y revisar en 30 dias.
  tope_posicion -- la posicion supera el tope de % de la RV y/o de % del
      patrimonio bruto (parametros_rv, Fase 1).
  candidata_a_sumar -- (Fase 3) el estado de valor es 'infravalorada' (precio <=
      valor intrinseco x (1 - margen requerido)) Y el semaforo esta en verde ("con
      la tesis intacta"). Severidad 'baja': es una oportunidad de tu marco, no un
      riesgo; cuenta en el KPI como cualquier alerta activa.
  tope_sector -- un sector (de fundamentales_rv / SECTOR_MANUAL, Fase 3) supera
      el tope de % de la RV. No tiene ticker (`ticker` None, `sector` = el sector);
      los sectores sin clasificar no cuentan contra el tope.

Solo se alerta sobre posiciones de RV abiertas y valuadas.
"""
from datetime import date

from indicadores_rv import indicadores_activos
import configuracion

# Constante del modulo (no parametros_rv): regla fija del marco, no algo
# que se calibre seguido desde la pantalla.
REVISION_OBLIGATORIA_CAIDA_PCT = 30

_SEVERIDAD = {"alta": 0, "media": 1, "baja": 2}
_ORDEN_ESTADO = {"activa": 0, "revisada": 1, "justificada": 1}


def _proximas_revisiones(con) -> dict[str, str | None]:
    return {
        ticker: prox
        for ticker, prox in con.execute(
            "SELECT a.ticker, t.proxima_revision FROM tesis_inversion t JOIN activos a ON a.id = t.activo_id;"
        )
    }


def _ultima_entrada_bitacora(con, ticker: str, hoy: date) -> str | None:
    """Fecha mas reciente (<= hoy) de una entrada de la bitacora de tesis
    de Acciones para el ticker: la fecha de la entrada o la de su
    reflexion, la mayor. Una entrada con fecha futura no cuenta todavia."""
    candidatas = []
    for fecha, fecha_reflexion in con.execute(
        "SELECT fecha, fecha_reflexion FROM tesis_acciones WHERE ticker = ?;", (ticker.strip().upper(),)
    ):
        for f in (fecha, fecha_reflexion):
            if f and f <= hoy.isoformat():
                candidatas.append(f)
    return max(candidatas) if candidatas else None


def _fmt(v: float) -> str:
    return f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def calcular_alertas(con, posiciones: list[dict], topes: dict, hoy: date | None = None) -> list[dict]:
    hoy = hoy or configuracion.hoy()
    tickers = {p["ticker"] for p in posiciones}
    alertas: list[dict] = []
    proximas = _proximas_revisiones(con)

    # --- revision obligatoria (-30 %) -------------------------------------
    for p in posiciones:
        g = p.get("ganancia_pct")
        if g is None or g > -REVISION_OBLIGATORIA_CAIDA_PCT:
            continue
        prox = proximas.get(p["ticker"])
        revisada = bool(prox) and prox > hoy.isoformat()
        alertas.append({
            "tipo": "revision_obligatoria", "ticker": p["ticker"], "severidad": "alta",
            "estado": "revisada" if revisada else "activa",
            "titulo": f"{p['ticker']}: revisión obligatoria ({_fmt(g)} % desde el costo)",
            "detalle": (f"Revisada — próxima revisión {prox}." if revisada else
                        "Rehacer el análisis y decidir por escrito. No es una orden de venta. "
                        "Se da por revisada al cargar una próxima revisión futura en la ficha."),
        })

    # --- indicadores (rojo / amarillo 2 seguidos) --------------------------
    for ind in indicadores_activos(con):
        if ind["ticker"] not in tickers or ind["ultima"] is None:
            continue
        ultima = ind["ultima"]
        if ultima["estado"] == "rojo":
            entrada = _ultima_entrada_bitacora(con, ind["ticker"], hoy)
            justificada = entrada is not None and entrada >= ultima["fecha"]
            alertas.append({
                "tipo": "indicador_rojo", "ticker": ind["ticker"], "severidad": "alta",
                "estado": "justificada" if justificada else "activa",
                "indicador": ind["nombre"], "desde": ultima["fecha"],
                "titulo": f"{ind['ticker']} · {ind['nombre']}: en rojo",
                "detalle": (
                    f"Última lectura {ultima['fecha']}: {_fmt(ultima['valor'])}. "
                    + (f"Justificada en la bitácora (entrada del {entrada}); se reactiva si entra una lectura roja más nueva."
                       if justificada else
                       "Regla: reducir de forma escalonada salvo que se documente por escrito por qué la tesis sigue viva "
                       "(entrada en la bitácora de tesis con fecha igual o posterior a la lectura).")
                ),
            })
        elif ind["amarillo_dos_seguidos"]:
            alertas.append({
                "tipo": "indicador_amarillo", "ticker": ind["ticker"], "severidad": "media",
                "estado": "activa", "indicador": ind["nombre"], "desde": ultima["fecha"],
                "titulo": f"{ind['ticker']} · {ind['nombre']}: amarillo 2 lecturas seguidas",
                "detalle": f"Última lectura {ultima['fecha']}: {_fmt(ultima['valor'])}. Regla: frenar aportes y revisar en 30 días.",
            })

    # --- topes (Fase 1) -----------------------------------------------------
    for p in posiciones:
        motivos = []
        if p.get("supera_tope_rv"):
            motivos.append(f"{_fmt(p['pct_rv'])} % de la RV (tope {_fmt(topes['rv'])} %)")
        if p.get("supera_tope_patrimonio"):
            motivos.append(f"{_fmt(p['pct_patrimonio'])} % del patrimonio bruto (tope {_fmt(topes['patrimonio'])} %)")
        if motivos:
            alertas.append({
                "tipo": "tope_posicion", "ticker": p["ticker"], "severidad": "media", "estado": "activa",
                "titulo": f"{p['ticker']}: supera el tope de posición",
                "detalle": " · ".join(motivos),
            })

    # --- candidata a sumar (Fase 3): precio bajo el maximo de compra y tesis intacta ---
    for p in posiciones:
        if p.get("estado_valor") == "infravalorada" and p.get("semaforo") == "verde":
            margen = p.get("margen_valor_pct")
            alertas.append({
                "tipo": "candidata_a_sumar", "ticker": p["ticker"], "severidad": "baja", "estado": "activa",
                "titulo": f"{p['ticker']}: candidata a sumar",
                "detalle": "Infravalorada según tu valor intrínseco"
                           + (f" (margen de seguridad actual {_fmt(margen)} %)" if margen is not None else "")
                           + " y con el semáforo en verde. Regla de tu marco: no es una orden de compra.",
            })

    # --- topes de sector (Fase 3, cuando hay sectores cargados) ---------------
    if topes.get("sector") is not None:
        por_sector: dict[str, list[dict]] = {}
        for p in posiciones:
            if p.get("sector"):
                por_sector.setdefault(p["sector"], []).append(p)
        for sector, ps in por_sector.items():
            pct = sum(p["pct_rv"] for p in ps)
            if pct > topes["sector"]:
                alertas.append({
                    "tipo": "tope_sector", "ticker": None, "sector": sector, "severidad": "media", "estado": "activa",
                    "titulo": f"{sector}: supera el tope de sector",
                    "detalle": f"{_fmt(pct)} % de la RV (tope {_fmt(topes['sector'])} %) · "
                               + ", ".join(f"{p['ticker']} {_fmt(p['pct_rv'])} %" for p in sorted(ps, key=lambda x: -x["pct_rv"])),
                })

    alertas.sort(key=lambda a: (_ORDEN_ESTADO[a["estado"]], _SEVERIDAD[a["severidad"]], a["ticker"] or "", a["tipo"]))
    return alertas
