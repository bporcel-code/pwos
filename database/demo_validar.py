"""PWOS -- validaciones automaticas de la demo (paso 7b), al final de cada corrida.

1. Endpoints: todos los GET de la API en modo demo, sobre demo.db, SIN red
   (la demo tiene que andar sola). Lista los que fallan y los paneles vacios.
2. Coherencia: saldos no negativos, posiciones = operaciones, cada conversion
   y transferencia con su pareja, cobros pasados conciliados contra los
   estimados, y la ecuacion de cada cierre mensual.
3. Privacidad: vuelca todos los textos de demo.db y les pasa los patrones
   prohibidos de la publicacion (privado/herramientas/publicacion/patrones_prohibidos.txt,
   si existe; fuera del repo privado no hay patrones y se saltea).
4. Ortografia: los textos que escribe el inversor (tesis, reflexiones,
   anotaciones, fases, notas) sin palabras que pierden la tilde o la enie.
5. Nada en vivo: ningun endpoint sale a la red (ni por la cache), y ningun
   dato guardado tiene fecha posterior al corte (salvo las fotos del dia que
   no tienen historico publico, que se avisan).

Escribe el resultado en database/demo/validacion.md. Uso suelto (parado en
database/): python demo_validar.py
"""
import inspect
import json
import re
import socket
import sqlite3
from pathlib import Path

import demo_entorno as entorno

PATRONES = Path(__file__).resolve().parents[1] / "privado" / "herramientas" / "publicacion" / "patrones_prohibidos.txt"
# Montos con formato "USD 1.234": la etiqueta es para montos REALES del usuario en
# el codigo; en la demo todo monto es inventado o de mercado, asi que no aplica.
ETIQUETAS_NO_APLICAN = {"monto"}
# (tabla.columna, etiqueta) que son datos publicos de mercado: los estados contables
# de Yahoo traen montos de 11 cifras que el patron de CUIT confunde.
EXCEPCIONES_BASE = {("fundamentales_rv.series_json", "cuit"): "montos de estados contables (yfinance)"}
# Endpoints con parametro de ruta: un valor de la demo.
CON_RUTA = {"/api/flujo-inmueble/{alias}": {"alias": "DEPTO-CABA-2AMB"}, "/api/tesis/{ticker}": {"ticker": "KO"},
            "/api/simular-bono/{ticker}": {"ticker": "GD35"}, "/api/valuacion-rv/{ticker}": {"ticker": "KO"},
            "/api/cedear-moneda/{ticker}": {"ticker": "KO"},
            "/api/maquina-economica/{semaforo}/fases": {"semaforo": "ciclo_corto"},
            "/api/indicadores-macro/{indicador_id}/serie": {"indicador_id": "riesgo_pais"}}
PARAMS_REQUERIDOS = {"/api/tesis-umbrales": {"tesis_tabla": "bonos", "tesis_id": 3},  # GD35, con umbral
                     "/api/tesis-indicadores": {"ticker": "KO"},
                     "/api/bonos-ratio-historico": {"ticker_a": "AL30", "ticker_b": "GD30"},
                     "/api/macro-historico": {"clave": "riesgo_pais"}}


def _vacio(x) -> bool:
    if x is None or x == [] or x == {}:
        return True
    if isinstance(x, dict):
        listas = [v for v in x.values() if isinstance(v, (list, dict))]
        return bool(listas) and all(_vacio(v) for v in listas) and not any(
            isinstance(v, (int, float)) and v for v in x.values())
    return False


def _bloquear_red(en_vivo: list, actual: dict) -> None:
    """Toda salida a la red falla y queda anotada con el endpoint que la pidio.
    Se intercepta en cada puerta (socket, requests, curl_cffi, yfinance)
    porque la cache de demo_entorno responde sin tocar el socket: un panel
    que consultara en vivo una URL ya cacheada pasaria inadvertido."""
    def _bloqueo(nombre):
        def bloqueada(*a, **k):
            en_vivo.append((actual.get("ruta"), nombre))
            raise OSError("la demo no sale a la red")
        return bloqueada
    socket.socket.connect, socket.create_connection = _bloqueo("socket"), _bloqueo("socket")
    import requests
    requests.sessions.Session.request = _bloqueo("requests")
    try:
        import curl_cffi.requests as cc
        cc.Session.request = _bloqueo("curl_cffi")
    except ImportError:
        pass
    import yfinance as yf
    yf.Ticker, yf.download = _bloqueo("yfinance"), _bloqueo("yfinance")


def endpoints() -> tuple[list, list, int, list]:
    en_vivo, actual = [], {}
    _bloquear_red(en_vivo, actual)
    import api
    import valorizacion
    from fastapi.params import Param
    api.DB_PATH = entorno.DB_DEMO
    valorizacion.VIVO_HABILITADO = False
    errores, vacios, n = [], [], 0
    for ruta in api.app.routes:
        if "GET" not in getattr(ruta, "methods", set()) or not ruta.path.startswith("/api/"):
            continue
        if "{" in ruta.path and ruta.path not in CON_RUTA:
            continue
        kwargs = dict(CON_RUTA.get(ruta.path, {}))
        kwargs.update(PARAMS_REQUERIDOS.get(ruta.path, {}))
        for p in inspect.signature(ruta.endpoint).parameters.values():
            if p.name not in kwargs and isinstance(p.default, Param):
                kwargs[p.name] = p.default.default
        n += 1
        actual["ruta"] = ruta.path
        try:
            r = ruta.endpoint(**kwargs)
            if hasattr(r, "body"):
                r = json.loads(r.body)
            if _vacio(r):
                vacios.append(ruta.path)
        except Exception as e:
            errores.append((ruta.path, f"{type(e).__name__}: {str(e)[:160]}"))
    return errores, vacios, n, sorted(set(en_vivo))


# Fotos que los cargadores solo pueden tomar "hoy" (no hay historico publico):
# si la demo se genera despues de la fecha de corte, quedan con la fecha de
# generacion. Se informan como aviso, no como error (docs/demo.md).
FOTOS_DEL_DIA = {
    "mercado_renta_fija_historico.fecha": "foto del mercado de bonos de Rava",
    "fundamentales_rv.fecha_dato": "fundamentales de Yahoo",
}


def posteriores_al_corte(con, corte: str) -> tuple[list[str], list[str]]:
    """Datos fechados despues del corte en cualquier columna `fecha` o
    `fecha_dato` (las de cronogramas y estimaciones a futuro se llaman
    distinto). Devuelve (problemas, avisos)."""
    problemas, avisos = [], []
    for (tabla,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';").fetchall():
        for col in [r[1] for r in con.execute(f'PRAGMA table_info("{tabla}")') if r[1] in ("fecha", "fecha_dato")]:
            n, maximo = con.execute(f'SELECT COUNT(*), MAX("{col}") FROM "{tabla}" WHERE substr("{col}", 1, 10) > ?;',
                                    (corte,)).fetchone()
            if not n:
                continue
            texto = f"{tabla}.{col}: {n} filas posteriores al {corte} (hasta {maximo})"
            if f"{tabla}.{col}" in FOTOS_DEL_DIA:
                avisos.append(f"{texto}: {FOTOS_DEL_DIA[f'{tabla}.{col}']} del dia de generacion")
            else:
                problemas.append(texto)
    return problemas, avisos


def coherencia(con, libro=None) -> list[str]:
    problemas = []
    for alias, saldo in con.execute("SELECT alias, saldo FROM vista_saldos_cuenta WHERE saldo < -0.005;"):
        problemas.append(f"saldo negativo: {alias} {saldo:.2f}")
    for ticker, contenedor, cantidad in con.execute(
            "SELECT ticker, contenedor, cantidad FROM vista_posiciones WHERE cantidad < 0;"):
        problemas.append(f"posicion negativa: {ticker} en {contenedor}: {cantidad}")
    if libro is not None:
        vista = {(c, t): q for t, c, q in con.execute("SELECT ticker, contenedor, cantidad FROM vista_posiciones;")}
        for (c, t), q in libro.tenencia.items():
            if abs(q - vista.get((c, t), 0)) > 1e-6:
                problemas.append(f"posicion {t} en {c}: libro {q} vs vista_posiciones {vista.get((c, t), 0)}")
    # conversiones: cada pata que sale tiene su pata que entra (mismo dia y contenedor)
    patas = con.execute("""SELECT m.fecha, co.nombre, SUM(m.cuenta_origen_id IS NOT NULL), SUM(m.cuenta_destino_id IS NOT NULL)
                           FROM movimientos m JOIN cuentas c ON c.id = COALESCE(m.cuenta_origen_id, m.cuenta_destino_id)
                           JOIN contenedores co ON co.id = c.contenedor_id
                           WHERE m.tipo = 'transferencia' AND (m.cuenta_origen_id IS NULL OR m.cuenta_destino_id IS NULL)
                           GROUP BY m.fecha, co.nombre HAVING SUM(m.cuenta_origen_id IS NOT NULL) != SUM(m.cuenta_destino_id IS NOT NULL);""").fetchall()
    for fecha, cont, sale, entra in patas:
        problemas.append(f"conversion sin pareja: {fecha} {cont} ({sale} salen, {entra} entran)")
    for desc, in con.execute("""SELECT descripcion FROM movimientos m JOIN cuentas o ON o.id = m.cuenta_origen_id
                                 JOIN cuentas d ON d.id = m.cuenta_destino_id
                                 WHERE m.tipo = 'transferencia' AND o.moneda != d.moneda;"""):
        problemas.append(f"transferencia entre monedas distintas: {desc}")
    # cobros que ya pasaron (con margen de la ventana) y no se conciliaron
    for concepto, fecha, estado in con.execute("""
            SELECT COALESCE(a.ticker, p.alias), f.fecha_estimada, f.estado FROM flujos_esperados f
            LEFT JOIN activos a ON a.id = f.activo_id LEFT JOIN propiedades p ON p.id = f.propiedad_id
            WHERE f.origen_estimado <> 'importador' AND f.estado IN ('pendiente', 'no_cobrado')
              AND f.fecha_estimada <= date((SELECT MAX(fecha) FROM movimientos), '-21 day');"""):
        problemas.append(f"cobro no conciliado: {concepto} {fecha} ({estado})")
    # ecuacion del cierre mensual: patrimonio = anterior + aporte + rendimiento
    filas = con.execute("SELECT anio, mes, patrimonio_total_usd, aportes_netos_usd, rendimiento_usd FROM cierres_mensuales "
                        "ORDER BY anio, mes;").fetchall()
    for prev, act in zip(filas, filas[1:]):
        dif = act[2] - (prev[2] + act[3] + act[4])
        if abs(dif) > 0.02:
            problemas.append(f"cierre {act[0]}-{act[1]:02d} no cierra por USD {dif:.2f}")
    return problemas


def auditoria() -> list[str]:
    """Ninguna tesis ni reflexion cita un dato posterior a su fecha, y ningun bono
    se compro antes de un pago que el cronograma publico no trae."""
    archivo = entorno.DIR_DEMO / "auditoria_tesis.json"
    if not archivo.exists():
        return ["falta auditoria_tesis.json (correr el generador completo)"]
    a = json.loads(archivo.read_text(encoding="utf-8"))
    problemas = []
    for t in a["tesis"]:
        posteriores = [(n, f) for n, f in t["datos"] if f > t["fecha"]]
        if posteriores:
            problemas.append(f"{t['texto']} ({t['fecha']}) cita datos posteriores: {posteriores}")
    from datetime import date
    for ticker, c in a["cobertura_bonos"].items():
        f0 = date.fromisoformat(c["flujos"][0])
        pago_previo = None
        if len(c["flujos"]) > 1:   # mismo dia, un periodo (en meses) antes del primer pago conocido
            f1 = date.fromisoformat(c["flujos"][1])
            meses = (f1.year - f0.year) * 12 + f1.month - f0.month
            total = f0.year * 12 + f0.month - 1 - meses
            pago_previo = date(total // 12, total % 12 + 1, min(f0.day, 28)).isoformat()
        if pago_previo and c["primera_compra"] <= pago_previo:
            problemas.append(f"{ticker}: comprado el {c['primera_compra']}, antes del pago del {pago_previo} "
                             f"que el cronograma de Rava no trae (empieza el {c['flujos'][0]})")
    return problemas


# Textos que escribe el inversor demo (tabla: columnas). Los de los cargadores
# y del motor (catalogos, notas de flujo_estimado) no entran: no son de la demo.
TEXTOS_INVERSOR = {
    "tesis_cartera": ["tesis", "riesgos", "reflexion_final"],
    "tesis_bonos": ["tesis", "riesgos", "reflexion_final"],
    "tesis_acciones": ["tesis", "riesgos", "reflexion_final"],
    "tesis_inmuebles": ["tesis", "riesgos", "reflexion_final"],
    "tesis_inversion": ["motivo", "riesgo", "horizonte", "criterio_venta"],
    "tesis_umbrales": ["accion"],
    "tesis_indicadores": ["nombre", "fuente"],
    "indicadores_anotaciones": ["nota"],
    "maquina_fases": ["fase"],
    "watchlist_rv": ["notas"],
    "movimientos": ["descripcion"],
    "categorias": ["nombre"],
    "contenedores": ["notas"],
    "propiedades": ["notas"],
    "pasivos": ["alias"],
    "valuaciones": ["fuente"],
    "gastos_propiedad": ["concepto"],
}
# Formas sin tilde (o sin enie) que en estos textos siempre son un error.
SIN_TILDE = set("""
    anio anios dolar dolares mas credito creditos atras pais paises despues ultimo ultima ultimos ultimas
    economia energia politico politica politicos historico historicos historica minimo minimos maximo maximos
    dia dias tambien segun ademas proximo proxima rapido unico publico numero deberia podria seria
    caida terminos indice indices superavit deficit metodo analisis region calefon termometro digito
    petroleo margenes habitos busqueda organicas tecnologicas implicito cumplio rindio volvio llego
""".split())
_PALABRA = re.compile(r"\b[^\W\d_]+\b")
_CION = re.compile(r"\b\w+[cs]ion\b", re.IGNORECASE)        # -cion/-sion en singular
_ESTA = re.compile(r"\besta (en|tan|bien|mal)\b", re.IGNORECASE)  # "esta en" -> "está en"


def ortografia(con) -> tuple[list[str], int]:
    """Tildes y enies en los textos del inversor demo (tesis, reflexiones,
    anotaciones, fases, notas, descripciones)."""
    import demo_cronologia as cron
    textos = []
    for tabla, columnas in TEXTOS_INVERSOR.items():
        for col in columnas:
            textos += [(f"{tabla}.{col}", v) for (v,) in con.execute(
                f'SELECT DISTINCT "{col}" FROM "{tabla}" WHERE typeof("{col}") = \'text\';')]
    demo = {t for t, *_ in cron.ACTIVOS}
    textos += [("activos.nombre", n) for t, n in con.execute("SELECT ticker, nombre FROM activos;") if t in demo and n]
    hallazgos = []
    for donde, valor in textos:
        malas = [w for w in _PALABRA.findall(valor) if w.lower() in SIN_TILDE]
        malas += _CION.findall(valor) + [m.group(0) for m in _ESTA.finditer(valor)]
        if malas:
            hallazgos.append(f"{donde}: {', '.join(dict.fromkeys(malas))} -- {valor[:100]}")
    return hallazgos, len(textos)


def privacidad(con) -> tuple[list[str] | None, int]:
    if not PATRONES.exists():
        return None, 0   # sin patrones: fuera del repo privado no se corre
    patrones = []
    for linea in PATRONES.read_text(encoding="utf-8").splitlines():
        if linea.strip() and not linea.lstrip().startswith("#"):
            etiqueta, rx = [x.strip() for x in linea.split("|", 1)]
            if etiqueta not in ETIQUETAS_NO_APLICAN:
                patrones.append((etiqueta, re.compile(rx)))
    hallazgos, textos = [], 0
    for (tabla,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%';").fetchall():
        columnas = [r[1] for r in con.execute(f'PRAGMA table_info("{tabla}")') if (r[2] or "").upper() in ("TEXT", "")]
        for col in columnas:
            for (valor,) in con.execute(f'SELECT DISTINCT "{col}" FROM "{tabla}" WHERE typeof("{col}") = \'text\';'):
                textos += 1
                for etiqueta, rx in patrones:
                    if rx.search(valor):
                        if (f"{tabla}.{col}", etiqueta) in EXCEPCIONES_BASE:
                            continue
                        hallazgos.append(f"{tabla}.{col} [{etiqueta}]: {valor[:120]}")
    return hallazgos, textos


def validar(ctx=None, log=print) -> dict:
    import configuracion
    con = sqlite3.connect(entorno.DB_DEMO)
    errores, vacios, n, en_vivo = endpoints()
    corte = getattr(ctx, "corte", None) or configuracion.hoy().isoformat()
    futuros, avisos_fecha = posteriores_al_corte(con, corte)
    problemas = coherencia(con, getattr(ctx, "libro", None))
    fechas = auditoria()
    hallazgos, textos = privacidad(con)
    mal_escritos, revisados = ortografia(con)
    con.close()
    lineas = [f"# Validacion de la demo", "", f"## Endpoints GET en modo demo, sin red: {n - len(errores)}/{n} responden"]
    lineas += [f"- ERROR {r}: {e}" for r, e in errores] or ["- todos responden"]
    lineas += ["", f"## Paneles vacios: {len(vacios)}"] + [f"- {v}" for v in vacios]
    lineas += ["", f"## Consultas en vivo desde la API: {len(en_vivo)}"]
    lineas += [f"- {r} intento salir a la red ({via})" for r, via in en_vivo] or ["- ningun endpoint sale a la red"]
    lineas += ["", f"## Datos posteriores al corte ({corte}): {len(futuros)} problemas"] + [f"- {p}" for p in futuros]
    lineas += [f"- aviso: {a}" for a in avisos_fecha]
    lineas += ["", f"## Coherencia: {len(problemas)} problemas"] + [f"- {p}" for p in problemas]
    lineas += ["", f"## Fechas de los datos de las tesis y cobertura de los cronogramas: {len(fechas)} problemas"]
    lineas += [f"- {p}" for p in fechas] or ["- ninguna tesis cita un dato posterior a su fecha; ningun bono espera un cobro fuera del cronograma"]
    if hallazgos is None:
        lineas += ["", "## Privacidad: no se corre (los patrones de la publicacion estan solo en el repo privado)"]
        hallazgos = []
    else:
        lineas += ["", f"## Privacidad: {len(hallazgos)} hallazgos en {textos} textos distintos de la base"]
        lineas += [f"- {h}" for h in hallazgos]
    lineas += ["", f"## Ortografía de los textos del inversor: {len(mal_escritos)} con palabras sin tilde en {revisados} textos"]
    lineas += [f"- {h}" for h in mal_escritos]
    (entorno.DIR_DEMO / "validacion.md").write_text("\n".join(lineas) + "\n", encoding="utf-8")
    for l in lineas:
        if l:
            log(f"  {l}")
    return {"errores": errores, "vacios": vacios, "en_vivo": en_vivo, "posteriores_al_corte": futuros,
            "problemas": problemas, "fechas": fechas, "privacidad": hallazgos, "ortografia": mal_escritos}


if __name__ == "__main__":
    import os
    os.environ.setdefault("PWOS_MODO", "demo")
    os.environ.setdefault("PWOS_CONFIG", str(entorno.DIR_DATABASE / "config" / "demo.json"))
    os.environ.setdefault("PWOS_DB", str(entorno.DB_DEMO))
    entorno._encerrar_sqlite()
    validar()
