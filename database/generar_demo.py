"""PWOS -- generador de la demo (paso 7b).

Arma database/demo/demo.db: un inversor ficticio con datos de mercado REALES
de cada fecha. Principios:

1. No lee ninguna base ni dato de nadie: corre en modo demo, con
   config/demo.json, y `sqlite3.connect` queda encerrado en database/demo/
   (demo_entorno.py). Lo de mercado lo bajan los cargadores del motor de las
   fuentes publicas; lo personal lo inventa demo_cronologia.py.
2. "Inventado pero real": cada operacion se define en USD y la cantidad sale
   del cierre real de ese dia y del MEP real. Si falta un dato, la operacion
   se mueve o se saca y queda anotado (nunca se inventa un precio).
3. Se construye con el motor: cargadores, altas de Real Estate, tesis,
   umbrales, indicadores, flujo_estimado, conciliar_flujos y la formula de
   cierres_mensuales. SQL directo solo donde no hay funcion (movimientos y
   operaciones: la unica funcion que los escribe son los importadores).
4. Deterministico: misma fecha de corte -> misma base. Todo lo que se baja
   queda en database/demo/cache/ (ver demo_entorno.py).
5. Base nueva: create_schema + create_views + sembrar_catalogos.

Uso (parado en database/):
    python generar_demo.py                      # corte: ultimo dia habil cerrado
    python generar_demo.py --corte 2026-10-28
    python generar_demo.py --hasta mercado      # para probar por fases
Para las series de FRED hace falta FRED_API_KEY en el entorno (sin ella esas
tarjetas de la solapa Macro quedan vacias; el resto se arma igual).
"""
import argparse
import contextlib
import io
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import demo_entorno as entorno

FASES = ("base", "mercado", "inversor", "mercado_cartera", "real_estate", "analisis", "cierres", "validar")
TZ_AR = timezone(timedelta(hours=-3))


def corte_por_defecto() -> str:
    """Ultimo dia habil con el cierre ya publicado (despues de las 18 h)."""
    ahora = datetime.now(TZ_AR)
    d = ahora.date() if ahora.hour >= 18 else ahora.date() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


class Bitacora:
    """Lo que el generador decidio en el camino (fechas movidas, operaciones
    sacadas, avisos de los cargadores). Va a database/demo/generacion.md."""

    def __init__(self):
        self.lineas: list[str] = []

    def __call__(self, texto: str) -> None:
        print(texto, flush=True)
        self.lineas.append(texto)


LOG = Bitacora()


@contextlib.contextmanager
def silencio(nombre: str):
    """Corre un cargador del motor capturando su salida; deja solo el resumen."""
    t0 = time.monotonic()
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            yield buf
    except SystemExit as e:
        if e.code not in (0, None):
            LOG(f"  ! {nombre}: salio con {e.code}")
    finally:
        cola = [l for l in buf.getvalue().splitlines() if l.strip()][-3:]
        LOG(f"  {nombre} ({time.monotonic() - t0:.0f} s): " + " | ".join(c.strip()[:110] for c in cola))


def correr_main(modulo, argv: list[str]):
    viejo = sys.argv
    sys.argv = [modulo.__name__ + ".py", *argv]
    try:
        return modulo.main()
    finally:
        sys.argv = viejo


# --- fase base ------------------------------------------------------------------
def fase_base(ctx) -> None:
    import sqlite3
    from create_schema import create_schema
    from create_views import create_views
    import sembrar_catalogos
    import demo_cronologia as cron

    for p in (entorno.DB_CONSTRUCCION, entorno.DIR_DEMO / "backups"):
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            for f in p.iterdir():
                f.unlink()
    with contextlib.redirect_stdout(io.StringIO()):
        create_schema(entorno.DB_CONSTRUCCION)
        create_views(entorno.DB_CONSTRUCCION)
    con = sqlite3.connect(entorno.DB_CONSTRUCCION)
    with con:
        LOG(f"  catalogos: {sembrar_catalogos.sembrar(con)}")
        # Catalogo de activos: no hay funcion de alta generica (cargar_activos.py
        # era la carga inicial del usuario), asi que va por SQL.
        for ticker, tipo, moneda, nombre in cron.ACTIVOS:
            con.execute("INSERT INTO activos (ticker, nombre, tipo, moneda_nominal) VALUES (?, ?, ?, ?);",
                        (ticker, nombre, tipo, moneda))
    LOG(f"  activos: {len(cron.ACTIVOS)}")
    con.close()


# --- fase mercado (lo que no depende de la cartera) -----------------------------------
def fase_mercado(ctx) -> None:
    db = entorno.DB_CONSTRUCCION
    import backfill_fx_diario
    with silencio("MEP/CCL diario (argentinadatos)"):
        backfill_fx_diario.main()
    import cargar_usd_a3500
    with silencio("A3500 (BCRA)"):
        cargar_usd_a3500.actualizar(db, None)
    import cargar_indicadores_macro
    with silencio("indicadores macro, backfill (FRED/BCRA/INDEC/yfinance/argentinadatos)"):
        cargar_indicadores_macro.ejecutar(db, None, True, None)
    import backfill_macro_historico
    with silencio("riesgo pais, inflacion y REM, historia (argentinadatos/BCRA)"):
        backfill_macro_historico.backfill(db)
    import cargar_benchmarks_macro
    with silencio("benchmarks del header"):
        cargar_benchmarks_macro.cargar()
    import backfill_deuda_externa
    with silencio("deuda externa, historia (Secretaria de Finanzas)"):
        backfill_deuda_externa.backfill(db)
    import backfill_indicadores_economia_real
    with silencio("economia real, historia"):
        backfill_indicadores_economia_real.backfill(db)
    import cargar_zpindex
    with silencio("zpindex (ultimo informe de cada region)"):
        cargar_zpindex.cargar(db)
    with silencio("zpindex CABA, backfill"):
        # Desde 2025-09: los informes anteriores traen el precio como imagen (sin
        # texto extraible) y probarlos sin cache costaba ~3 minutos.
        detalle = cargar_zpindex.backfill(db, "zpindex_caba_venta_usd_m2", "2025-09", ctx.corte[:7])
    ctx.zpindex_detalle = detalle
    import cargar_ratios_cedear
    with silencio("ratios de CEDEAR (BYMA + Comafi)"):
        correr_main(cargar_ratios_cedear, ["--db", str(db), "--aplicar", "--confirmar-cambios"])
    import cargar_mercado_renta_fija_rava
    with silencio("mercado de bonos (Rava)"):
        cargar_mercado_renta_fija_rava.cargar()


def alquiler_de_referencia() -> float:
    """Alquiler medio de un 2 ambientes de 50 m2 en CABA (Zonaprop, informe de
    octubre de 2025): base del contrato de la demo, llevado a sus m2."""
    import re
    import pdfplumber
    import requests
    import cargar_zpindex
    url = cargar_zpindex.url_zpindex_mes("CABA", 2025, 10)
    r = requests.get(url, headers=cargar_zpindex._UA, timeout=60)
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        texto = "\n".join(p.extract_text() or "" for p in pdf.pages)
    m = re.search(r"2 ambientes y 50 m2 se alquila por\s*\$\s*([\d.]+)", texto)
    if not m:
        raise RuntimeError(f"no se encontro el alquiler de referencia en {url}")
    LOG(f"  alquiler de referencia (Zonaprop 2025-10, 2 amb. 50 m2): ${m.group(1)} por mes")
    return float(m.group(1).replace(".", ""))


def _con():
    import sqlite3
    return sqlite3.connect(entorno.DB_CONSTRUCCION)


# --- fase inversor: toda la historia, movimiento por movimiento ---------------------
def fase_inversor(ctx) -> None:
    import demo_historia
    import watchlist_rv
    from demo_inversor import Mercado
    import demo_cronologia as cron
    con = _con()
    ctx.mercado = Mercado(con, ctx.corte, LOG)
    demo_historia._ALQUILER_2AMB_50M2["monto"] = alquiler_de_referencia()
    historia = demo_historia.Historia(con, ctx.mercado, LOG, ctx.corte)
    ctx.libro, ctx.hechos = historia.construir()
    ctx.historia = historia
    cartera = {t for (_, t), q in ctx.libro.tenencia.items() if q > 0}
    for ticker, nombre, cubeta, notas in cron.WATCHLIST:
        watchlist_rv.agregar(con, ticker, nombre=nombre, cubeta_sugerida=cubeta, notas=notas, tickers_cartera=cartera)
    con.commit()
    # Para la validacion: fecha de cada dato que cito cada tesis, y cobertura del
    # cronograma de cada bono (Rava trae los pagos desde cierta fecha).
    import json
    cobertura = {}
    for t, tipo in ctx.mercado.tipo.items():
        flujos = [f for f, _, _ in ctx.mercado.flujos(t)]
        compra = _primera_compra(con, t)
        if tipo in ("bono_soberano", "on") and flujos and compra:
            cobertura[t] = {"primera_compra": compra, "flujos": flujos[:2]}
    (entorno.DIR_DEMO / "auditoria_tesis.json").write_text(json.dumps(
        {"tesis": ctx.hechos.get("auditoria_tesis", []), "cobertura_bonos": cobertura}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    n = con.execute("SELECT COUNT(*) FROM movimientos;").fetchone()[0]
    LOG(f"  movimientos: {n}; operaciones: {con.execute('SELECT COUNT(*) FROM operaciones_activos;').fetchone()[0]}")
    for antes, despues, accion, ticker in ctx.hechos["fechas_movidas"]:
        LOG(f"  fecha movida: {accion} {ticker or ''} {antes} -> {despues} (sin precio o feriado el dia pedido)")
    ctx.con_mercado = con   # la sigue usando ctx.mercado; se cierra en _cerrar_base


# --- fase mercado_cartera: cargadores que dependen de lo que tiene el inversor -----
def fase_mercado_cartera(ctx) -> None:
    db = entorno.DB_CONSTRUCCION
    import backfill_precios_rava, backfill_precios_yfinance, cargar_bonos_referencia_flujo
    import cargar_factores_riesgo, cargar_dividendos_proyectados, cargar_precios_rv, cargar_fundamentales_rv
    import cargar_mercado_renta_fija_historico, alta_activos_referencia, backfill_precios_rava_referencia
    with silencio("precios de bonos y LECAPs (Rava)"):
        backfill_precios_rava.main()
    with silencio("precios de CEDEARs y acciones (yfinance)"):
        backfill_precios_yfinance.main()
    with silencio("universo de referencia (Bonares, Globales, CER)"):
        alta_activos_referencia.main()
    with silencio("precios del universo de referencia (Rava)"):
        backfill_precios_rava_referencia.main()
    # Los archivos de Rava traen glitches de 1-2 ruedas (ej. AL30 a 50.000 el
    # 04-05/03/2026 entre dos ~86.000): el mismo filtro de picos que la base real.
    import limpiar_picos_rava
    with silencio("picos aislados de Rava (limpiar_picos_rava)"):
        limpiar_picos_rava.main()
    with silencio("cronogramas y referencia de los bonos en cartera (Rava)"):
        cargar_bonos_referencia_flujo.cargar()
    with silencio("factores de riesgo (yfinance)"):
        cargar_factores_riesgo.cargar()
    with silencio("dividendos proyectados (yfinance)"):
        cargar_dividendos_proyectados.cargar(db)
    with silencio("precios en USD para retornos de RV (yfinance)"):
        correr_main(cargar_precios_rv, ["--db", str(db)])
    with silencio("fundamentales de RV (yfinance)"):
        correr_main(cargar_fundamentales_rv, ["--db", str(db)])
    with silencio("snapshot del mercado de bonos"):
        cargar_mercado_renta_fija_historico.cargar()


# --- fase real_estate: el contrato vigente (lo que usa flujo_estimado) -------------
def fase_real_estate(ctx) -> None:
    import cargar_contrato_alquiler
    import demo_cronologia as cron
    alq = ctx.historia.alquiler
    if not alq:
        LOG("  sin alquiler: no hay contrato que cargar")
        return
    with silencio("contrato de alquiler vigente"):
        correr_main(cargar_contrato_alquiler, ["--alias", cron.INMUEBLE["alias"], "--monto", str(alq["monto"]),
                                               "--dia", str(cron.INMUEBLE["dia_cobro"]), "--contenedor", "Banco Demo",
                                               "--desde", f"{alq['desde']}-01", "--confirmar",
                                               "--db", str(entorno.DB_CONSTRUCCION)])


# --- fase analisis: fichas, indicadores de tesis, parametros, macro, simulaciones --
def fase_analisis(ctx) -> None:
    import indicadores_macro
    import indicadores_rv
    import parametros_rv
    import demo_cronologia as cron
    con, m, L = _con(), ctx.mercado, ctx.libro
    cartera = {t: q for (_, t), q in L.tenencia.items() if q > 0}
    for ticker, fic in cron.FICHAS_RV.items():
        if ticker not in cartera:
            continue
        # Solo el endpoint POST /api/tesis/{ticker} escribe tesis_inversion: SQL
        # con las mismas columnas (la ficha de cada posicion de RV).
        con.execute("""INSERT INTO tesis_inversion (activo_id, motivo, riesgo, horizonte, criterio_venta, peso_objetivo_pct,
                       cubeta, proxima_revision) VALUES ((SELECT id FROM activos WHERE ticker = ?), ?, ?, ?, ?, ?, ?, ?);""",
                    (ticker, fic["motivo"], fic["riesgo"], fic["horizonte"], fic["criterio_venta"],
                     fic["peso_objetivo_pct"], fic["cubeta"], "2027-01-15"))
    n_lecturas = 0
    for ticker, nombre, metrica, direccion, verde, rojo in cron.INDICADORES_RV:
        if ticker not in cartera:
            continue
        iid = indicadores_rv.crear_indicador(con, ticker=ticker, nombre=nombre, direccion=direccion, umbral_verde=verde,
                                             umbral_rojo=rojo, fuente=_fuente_metrica(metrica), frecuencia="mensual")
        desde = _primera_compra(con, ticker)
        for fecha in _fines_de_mes(m, desde, ctx.corte):
            valor = _metrica(m, ticker, metrica, fecha)
            if valor is not None:
                indicadores_rv.cargar_lectura(con, iid, fecha, round(valor, 2), hoy=date.fromisoformat(ctx.corte))
                n_lecturas += 1
    LOG(f"  indicadores de tesis: {n_lecturas} lecturas con datos reales")
    for clave, valor in cron.PARAMETROS_RV.items():
        parametros_rv.actualizar_parametro(con, clave, valor)
    for ind, valor, direccion, objetivo, nota in cron.ANOTACIONES:
        indicadores_macro.guardar_anotacion(con, ind, umbral_valor=valor, umbral_direccion=direccion,
                                            objetivo_valor=objetivo, nota=nota)
    for semaforo, fecha, fase in cron.FASES_MAQUINA:
        indicadores_macro.guardar_fase(con, semaforo, fase, fecha)
    con.commit()
    con.close()
    _simulaciones(ctx)


def _fuente_metrica(metrica):
    if metrica.startswith("macro:"):
        return f"PWOS macro_historico ({metrica[6:]})"
    return {"caida_52s": "precio del CEDEAR / CCL (yfinance)", "dividend_yield": "dividendos (yfinance) / precio implícito"}[metrica]


def _primera_compra(con, ticker):
    return con.execute("""SELECT MIN(m.fecha) FROM operaciones_activos o JOIN movimientos m ON m.id = o.movimiento_id
                          JOIN activos a ON a.id = o.activo_id WHERE a.ticker = ?;""", (ticker,)).fetchone()[0]


def _fines_de_mes(m, desde, corte):
    """Ultimo dia habil de cada mes desde `desde` hasta el corte (y el corte)."""
    salida, por_mes = [], {}
    for d in m.dias:
        if desde <= d <= corte:
            por_mes[d[:7]] = d
    return sorted(por_mes.values())


_DIVIDENDOS = {}


def _metrica(m, ticker, metrica, fecha):
    """Valor REAL de un indicador de tesis a una fecha."""
    if metrica.startswith("macro:"):
        return m.macro(metrica[6:], fecha)
    desde = (date.fromisoformat(fecha) - timedelta(days=365)).isoformat()
    if metrica == "caida_52s":
        serie = [m.precio_usd(ticker, d) for d in m.dias if desde <= d <= fecha and m.precio(ticker, d)]
        serie = [x for x in serie if x]
        return (max(serie) - serie[-1]) / max(serie) * 100 if len(serie) > 20 else None
    if metrica == "dividend_yield":
        import yfinance as yf
        import sqlite3
        if ticker not in _DIVIDENDOS:
            _DIVIDENDOS[ticker] = yf.Ticker(ticker).dividends
        divs = sum(float(v) for ts, v in _DIVIDENDOS[ticker].items() if desde < ts.date().isoformat() <= fecha)
        ratio = m.con.execute("SELECT ratio FROM ratios_cedear WHERE ticker_byma = ?;", (ticker,)).fetchone()
        precio_cedear_usd = m.precio_usd(ticker, fecha)
        if not ratio or not precio_cedear_usd or not divs:
            return None
        return divs / (precio_cedear_usd * ratio[0]) * 100
    raise ValueError(metrica)


def _simulaciones(ctx) -> None:
    import api
    import simulador_rv
    from tenencias_rv import tenencias_rv
    import demo_cronologia as cron
    api.DB_PATH = entorno.DB_CONSTRUCCION
    r = api.resumen()
    con = _con()
    t = tenencias_rv(con, r["mep"], r["ccl"], r["patrimonio_total"] + r["total_pasivos"])
    for ticker, monto, fuente in cron.SIMULACIONES:
        try:
            res = simulador_rv.simular(con, t, ticker, monto, fuente)
            nuevo_id = simulador_rv.guardar_simulacion(con, ticker, monto, fuente, res)
            # La persona simula al cierre del corte, no el dia en que se genera
            # la demo (la tabla pone la hora actual por defecto).
            con.execute("UPDATE simulaciones_compra_rv SET fecha = ? WHERE id = ?;", (f"{ctx.corte} 18:00:00", nuevo_id))
            LOG(f"  simulacion guardada: {ticker} USD {monto} ({fuente})")
        except Exception as e:
            LOG(f"  ! simulacion {ticker}: {e}")
    con.commit()
    con.close()


# --- fase cierres: cierres mensuales, flujo de cobros, recorte y cierre de la base --
def fase_cierres(ctx) -> None:
    import json
    import cierres_mensuales
    import conciliar_flujos
    import flujo_estimado
    import rendimiento
    import demo_cronologia as cron
    m, L = ctx.mercado, ctx.libro
    con = _con()
    corte_mes = ctx.corte[:7]
    meses = []
    a, mes = int(cron.INICIO[:4]), int(cron.INICIO[5:7])
    while f"{a}-{mes:02d}" < corte_mes:
        foto = rendimiento.fecha_foto_cierre(a, mes)
        f, r = foto, None
        for _ in range(10):  # el 1 del mes puede ser feriado: ultimo dia con todo valuado
            r = rendimiento.patrimonio_a_fecha(con, f)
            if r["completo"]:
                break
            f = (date.fromisoformat(f) - timedelta(days=1)).isoformat()
        if not r["completo"]:
            raise RuntimeError(f"no se pudo valuar el cierre {a}-{mes:02d}: {r['faltantes'][:3]}")
        datos = L.mes[f"{a}-{mes:02d}"]
        mep = m.mep(foto)
        meses.append({"anio": a, "mes": mes, "patrimonio_usd": r["total_usd"], "mep": mep,
                      "trabajo_ars": round(datos["trabajo_ars"] + datos["aporte_usd"] * mep, 2),
                      "gastos_ars": round(datos["gastos_ars"] + datos["retiro_inmueble_usd"] * mep, 2),
                      "fuente": f"demo: patrimonio_a_fecha({f}); aporte = sueldo + alquiler - gastos - compra del inmueble"})
        a, mes = (a + 1, 1) if mes == 12 else (a, mes + 1)
    calculados = cierres_mensuales.calcular(con, meses)
    con.close()
    plan = cierres_mensuales.planificar(_con(), calculados)
    LOG(f"  cierres mensuales: {cierres_mensuales.aplicar(entorno.DB_CONSTRUCCION, plan)}")
    con = _con()
    hoy = date.fromisoformat(ctx.corte)
    LOG(f"  flujo estimado: {flujo_estimado.sincronizar(con, hoy)}")
    r = conciliar_flujos.conciliar(con, hoy)
    LOG(f"  conciliacion: {r['vinculados']} vinculados, {r['no_cobrados']} no cobrados, {len(r['ambiguos'])} ambiguos")
    for tabla in ("precios", "macro_historico", "precios_candidatas"):
        n = con.execute(f"DELETE FROM {tabla} WHERE fecha > ?;", (ctx.corte,)).rowcount
        if n:
            LOG(f"  recorte al corte: {n} filas de {tabla} posteriores al {ctx.corte}")
    _benchmarks_al_corte(con, ctx.corte)
    con.commit()
    con.execute("VACUUM;")
    con.close()
    _foto_cierre_cedears(ctx)
    _cerrar_base(ctx)


def _benchmarks_al_corte(con, corte: str) -> None:
    """Los cargadores de benchmarks traen el "ultimo dato" del dia en que se
    genera la demo, que puede ser posterior al corte. Cada uno toma el ultimo
    valor de su serie en macro_historico (ya recortada); sin serie, se borra:
    no se inventa."""
    import re
    filas = con.execute("""SELECT b.clave, b.fuente, i.id FROM benchmarks_macro b
                           LEFT JOIN indicadores_catalogo i ON i.clave_benchmark = b.clave
                           WHERE b.fecha_dato > ?;""", (corte,)).fetchall()
    for clave, fuente, serie in filas:
        ultimo = con.execute("SELECT fecha, valor FROM macro_historico WHERE clave = ? AND fecha <= ? "
                             "ORDER BY fecha DESC LIMIT 1;", (serie, corte)).fetchone() if serie else None
        if ultimo:
            con.execute("UPDATE benchmarks_macro SET valor_pct = ?, fecha_dato = ?, fuente = ?, detalle_json = NULL "
                        "WHERE clave = ?;", (round(ultimo[1], 3), ultimo[0],
                                             re.sub(r" \(vs [^)]*\)", "", fuente) + " (historial al corte)", clave))
            LOG(f"  benchmark {clave}: al {ultimo[0]} desde el historial (el cargador trajo un dato posterior al corte)")
        else:
            con.execute("DELETE FROM benchmarks_macro WHERE clave = ?;", (clave,))
            LOG(f"  benchmark {clave}: sin dato hasta el corte, se saca")


def _foto_cierre_cedears(ctx) -> None:
    """Foto del cierre para "CEDEAR en pesos o dolares" en modo demo: paneles de
    data912 con las puntas igualadas al ultimo operado, y MEP/CCL del corte."""
    import json
    import cedear_moneda
    m = ctx.mercado
    panel, _ = cedear_moneda.obtener_panel_puntas()
    for item in panel.values():
        if item.get("c"):
            item["px_bid"] = item["px_ask"] = item["c"]
    precios_ny = cedear_moneda.obtener_precios_ny()
    con = _con()
    etfs = [r[0] for r in con.execute("SELECT ticker_origen FROM ratios_cedear WHERE tipo = 'etf';")]
    con.close()
    import yfinance as yf
    etf = {}
    for t in etfs:
        if t in ("SPY", "EEM", "QQQ", "IWM", "XLE", "XLF", "GLD", "DIA", "ARKK", "EWZ"):
            h = yf.Ticker(t).history(start=(date.fromisoformat(ctx.corte) - timedelta(days=7)).isoformat(),
                                     end=(date.fromisoformat(ctx.corte) + timedelta(days=1)).isoformat(), auto_adjust=False)
            if not h.empty:
                etf[t] = float(h["Close"].iloc[-1])
    foto = {"fecha_dato": f"{ctx.corte}T16:59:00-03:00", "panel": panel, "precios_ny": precios_ny, "etf": etf,
            "mep": {"venta": m.mep(ctx.corte), "compra": m.mep(ctx.corte), "fecha_actualizacion": ctx.corte},
            "ccl": {"venta": m.ccl(ctx.corte), "compra": m.ccl(ctx.corte), "fecha_actualizacion": ctx.corte},
            "capturada": datetime.now(TZ_AR).isoformat(timespec="seconds")}
    (entorno.DIR_DEMO / "cierre_cedears.json").write_text(json.dumps(foto, ensure_ascii=False), encoding="utf-8")
    LOG(f"  foto del cierre de CEDEARs: {len(panel)} especies, {len(etf)} ETF (capturada {foto['capturada']})")


def _cerrar_base(ctx) -> None:
    import shutil
    ctx.con_mercado.close()
    if entorno.DB_DEMO.exists():
        entorno.DB_DEMO.unlink()
    shutil.move(entorno.DB_CONSTRUCCION, entorno.DB_DEMO)
    (entorno.DIR_DEMO / "demo.env").write_text(f"PWOS_MODO=demo\nPWOS_HOY={ctx.corte}\n", encoding="utf-8")
    import os
    os.environ["PWOS_DB"] = str(entorno.DB_DEMO)
    LOG(f"  base lista: {entorno.DB_DEMO} ({entorno.DB_DEMO.stat().st_size / 1e6:.1f} MB)")


def fase_validar(ctx) -> None:
    import demo_validar
    demo_validar.validar(ctx, LOG)


def main() -> None:
    import shutil
    ap = argparse.ArgumentParser(description="Genera database/demo/demo.db")
    ap.add_argument("--corte", default=None, help="AAAA-MM-DD (default: ultimo dia habil cerrado)")
    ap.add_argument("--hasta", choices=FASES, default=FASES[-1], help="para probar: corta despues de esta fase")
    ap.add_argument("--desde", choices=("base", "inversor"), default="base",
                    help="inversor: arranca de la copia guardada al terminar la fase de mercado")
    args = ap.parse_args()
    corte = args.corte or corte_por_defecto()
    entorno.preparar(corte)

    class Ctx:
        pass

    ctx = Ctx()
    ctx.corte = corte
    tras_mercado = entorno.DIR_DEMO / "tras_mercado.db"
    LOG(f"# Demo PWOS -- corte {corte}, generada {datetime.now(TZ_AR):%Y-%m-%d %H:%M}")
    fases = FASES
    if args.desde == "inversor":
        shutil.copy2(tras_mercado, entorno.DB_CONSTRUCCION)
        fases = FASES[FASES.index("inversor"):]
    for fase in fases:
        LOG(f"\n## {fase}")
        globals()[f"fase_{fase}"](ctx)
        if fase == "mercado":
            shutil.copy2(entorno.DB_CONSTRUCCION, tras_mercado)
        if fase == args.hasta:
            break
    (entorno.DIR_DEMO / "generacion.md").write_text("\n".join(LOG.lineas) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
