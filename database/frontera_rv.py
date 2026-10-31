"""
PWOS -- Frontera eficiente de Renta Variable (Epica 19, Fase 5, 2026-09-23).
Automatiza el metodo que el usuario hacia a mano (Excel + Solver): cartera de
MINIMA VARIANZA y de MAXIMO SHARPE de su RV, con datos reales y actuales.
Herramienta para decidir como armar la cartera, NUNCA una recomendacion.

Modulo PURO: numpy + pandas, sin scipy, sin red, sin escribir en la base.
Todo el calculo interno es SEMANAL y sin anualizar; `anualizar_*` solo
sirve para MOSTRAR (retorno x52, sigma x sqrt(52)).

Insumos (ticket Fase 5):
  - Universo: RV en cartera (acciones + CEDEARs, los ETF ya vienen como
    CEDEAR). Retornos semanales SIMPLES en USD MEP, misma base que
    retornos_rv.py / correlacion_rv.py (subyacente USD para CEDEARs, MEP
    historico para acciones locales -- excepcion documentada a ADR-001).
  - Retorno esperado = PROMEDIO historico de cada columna, igual para todos.
    Covarianza MUESTRAL (n-1).
  - Ventana configurable (VENTANA_DEFAULT_SEM=104, minimo permitido
    VENTANA_MIN_SEM=52). Un activo con mas de TOLERANCIA_HUECOS_SEM
    retornos faltantes en la ventana se EXCLUYE y se avisa; los huecos
    menores se resuelven descartando esas semanas de la muestra comun --
    nunca se rellena ni se usa un retorno de 2 semanas como semanal.
    Por que 52 como piso: con menos de un anio el promedio de
    retornos no dice nada y la covarianza queda inestable (regla de dedo:
    T >= 2-4 veces la cantidad de activos).
  - Solo se usan semanas COMPLETAS (viernes estrictamente anterior a
    `hasta`): retornos_rv.py rotula la semana en curso con el viernes
    futuro, y ese retorno parcial contaminaria un promedio.

Optimizador (numpy puro, determinista):
  - min 1/2 w'Sigma w  s.t.  suma(w)=1 [y mu'w=R en la frontera],
    lo <= w <= hi.  Metodo de CONJUNTO ACTIVO primal (textbook): en cada
    paso resuelve el sistema KKT sobre las variables libres, avanza hasta la
    primera cota que bloquea, y libera la variable con el multiplicador mas
    violado. Sigma es semidefinida positiva -> KKT suficiente para el
    optimo global.
  - `verificar_kkt` recalcula multiplicadores desde cero con lstsq y chequea
    estacionariedad + factibilidad + signos de multiplicadores en cotas,
    INDEPENDIENTE del solver.
  - Frontera: grilla de retornos objetivo entre el de minima varianza y el
    maximo alcanzable (LP con cotas, llenado codicioso). Maximo Sharpe:
    mejor punto de la grilla, refinado con seccion aurea en su vecindad.
"""
import math

import numpy as np
import pandas as pd

from retornos_rv import RESAMPLE_SEMANAL, serie_diaria_usd, ultimo_viernes_completo
import configuracion

SEMANAS_POR_ANIO = 52
VENTANA_DEFAULT_SEM = 104
VENTANA_MIN_SEM = 52
FRACCION_MIN_MUESTRA = 0.9     # la muestra comun efectiva debe conservar >= 90% de la ventana pedida
TOLERANCIA_HUECOS_SEM = 4      # retornos faltantes tolerados por activo dentro de la ventana (~4%)
TOPE_DEFAULT = 0.25
N_PUNTOS_FRONTERA = 60
TOL_KKT = 1e-8


# ---------------------------------------------------------------- insumos

def rf_semanal(rf_anual_pct: float) -> float:
    """T-Bill anualizado en % -> tasa semanal simple: (1+rf)^(1/52) - 1."""
    return (1 + rf_anual_pct / 100.0) ** (1 / SEMANAS_POR_ANIO) - 1


def construir_retornos(con, tickers: list[tuple[str, str]], ventana_sem: int, hasta) -> dict:
    """`tickers`: [(ticker, tipo)]. Ventana = las ultimas `ventana_sem`
    SEMANAS CALENDARIO completas (viernes), sobre una grilla fija.

    Un retorno semanal se calcula SOLO entre dos viernes consecutivos con
    precio: si falta el precio de una semana, faltan ese retorno y el de la
    semana siguiente (un retorno que abarca 2 semanas NO es semanal y no se
    usa, ni se rellena). Un activo entra si le faltan a lo sumo
    TOLERANCIA_HUECOS_SEM retornos dentro de la ventana; si no, se EXCLUYE
    y se avisa. La muestra comun es la interseccion de semanas validas de
    los activos incluidos (se descartan las semanas con hueco, nunca se
    inventa un dato) y `ventana_sem_efectiva` informa cuantas quedaron."""
    if ventana_sem < VENTANA_MIN_SEM:
        raise ValueError(f"ventana_sem={ventana_sem} < minimo permitido {VENTANA_MIN_SEM}")
    corte = ultimo_viernes_completo(hasta)
    grilla = pd.date_range(end=corte, periods=ventana_sem, freq="W-FRI")
    series, excluidos = {}, {}
    for ticker, tipo in tickers:
        semanal = serie_diaria_usd(con, ticker, tipo).resample(RESAMPLE_SEMANAL).last()
        ret = semanal.pct_change(fill_method=None).reindex(grilla)
        validas = int(ret.notna().sum())
        if validas < ventana_sem - TOLERANCIA_HUECOS_SEM:
            excluidos[ticker] = validas
        else:
            series[ticker] = ret
    if not series:
        return {"retornos": pd.DataFrame(), "excluidos": excluidos, "hasta_viernes": corte, "desde": None,
                "semanas_descartadas": [], "ventana_sem_efectiva": 0}
    df = pd.DataFrame(series)
    completas = df.notna().all(axis=1)
    descartadas = [d.strftime("%Y-%m-%d") for d in df.index[~completas]]
    df = df[completas]
    minimo_efectivo = math.ceil(FRACCION_MIN_MUESTRA * ventana_sem)
    if len(df) < minimo_efectivo:
        raise ValueError(f"solo {len(df)} semanas comunes validas de {ventana_sem} pedidas "
                         f"(< {minimo_efectivo}, {FRACCION_MIN_MUESTRA:.0%}) -- demasiados huecos de datos")
    return {"retornos": df, "excluidos": excluidos, "hasta_viernes": corte, "desde": df.index[0],
            "semanas_descartadas": descartadas, "ventana_sem_efectiva": int(len(df))}


def estadisticas(retornos: pd.DataFrame, ddof: int = 1) -> tuple[np.ndarray, np.ndarray]:
    """(mu, Sigma): promedio historico y covarianza muestral (ddof=1)."""
    return retornos.mean().to_numpy(), retornos.cov(ddof=ddof).to_numpy()


# ---------------------------------------------------------------- metricas

def metricas(w: np.ndarray, mu: np.ndarray, Sigma: np.ndarray, rf: float) -> dict:
    ret = float(mu @ w)
    var = float(w @ Sigma @ w)
    sigma = math.sqrt(max(var, 0.0))
    sharpe = (ret - rf) / sigma if sigma > 0 else float("nan")
    return {"retorno": ret, "sigma": sigma, "sharpe": sharpe}


def anualizar_retorno(r: float) -> float:
    return r * SEMANAS_POR_ANIO


def anualizar_sigma(s: float) -> float:
    return s * math.sqrt(SEMANAS_POR_ANIO)


# ---------------------------------------------------------------- QP

def _cotas(n: int, lo, hi) -> tuple[np.ndarray, np.ndarray]:
    lo = np.full(n, lo, dtype=float) if np.isscalar(lo) else np.asarray(lo, dtype=float)
    hi = np.full(n, hi, dtype=float) if np.isscalar(hi) else np.asarray(hi, dtype=float)
    if lo.sum() > 1 + 1e-12 or hi.sum() < 1 - 1e-12:
        raise ValueError(f"cotas infactibles: suma(lo)={lo.sum():.4f}, suma(hi)={hi.sum():.4f}, se necesita 1")
    return lo, hi


def resolver_qp(Sigma: np.ndarray, A: np.ndarray, b: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                w0: np.ndarray, max_iter: int = 2000) -> np.ndarray:
    """min 1/2 w'Sigma w  s.t.  A w = b,  lo <= w <= hi. `w0` factible."""
    n = len(w0)
    eps = 1e-13
    escala = max(float(np.abs(Sigma).max()), 1e-300)
    w = np.clip(w0.astype(float), lo, hi)
    if np.abs(A @ w - b).max() > 1e-9:
        raise ValueError(f"punto inicial no factible: A w - b = {A @ w - b}")
    estado = np.zeros(n, dtype=int)          # 0 libre, -1 en cota inferior, +1 en superior
    estado[w <= lo + eps] = -1
    estado[w >= hi - eps] = 1
    for _ in range(max_iter):
        libres = np.where(estado == 0)[0]
        g = Sigma @ w
        p = np.zeros(n)
        if len(libres):
            AF = A[:, libres]
            k = AF.shape[0]
            K = np.block([[Sigma[np.ix_(libres, libres)], AF.T], [AF, np.zeros((k, k))]])
            rhs = np.concatenate([-g[libres], np.zeros(k)])
            try:
                sol = np.linalg.solve(K, rhs)
            except np.linalg.LinAlgError:
                sol = np.linalg.lstsq(K, rhs, rcond=None)[0]
            p[libres] = sol[:len(libres)]
        if np.abs(p).max() <= 1e-12:
            # estacionario en la cara actual: multiplicadores
            if len(libres):
                nu = np.linalg.lstsq(A[:, libres].T, g[libres], rcond=None)[0]
            else:
                nu = np.linalg.lstsq(A.T, g, rcond=None)[0]
            s = g - A.T @ nu
            viol = np.zeros(n)
            viol[estado == -1] = np.minimum(s[estado == -1], 0.0)      # en inferior: s debe ser >= 0
            viol[estado == 1] = -np.maximum(s[estado == 1], 0.0)      # en superior: s debe ser <= 0
            j = int(np.argmin(viol))
            if viol[j] >= -TOL_KKT * escala:
                return w
            estado[j] = 0
            continue
        # paso maximo antes de tocar una cota
        alfa = 1.0
        bloquea = -1
        for i in libres:
            if p[i] < -1e-15:
                a = (lo[i] - w[i]) / p[i]
            elif p[i] > 1e-15:
                a = (hi[i] - w[i]) / p[i]
            else:
                continue
            if a < alfa - 1e-15:
                alfa, bloquea = a, i
        w = w + alfa * p
        if bloquea >= 0:
            estado[bloquea] = -1 if p[bloquea] < 0 else 1
            w[bloquea] = lo[bloquea] if p[bloquea] < 0 else hi[bloquea]
    raise RuntimeError("resolver_qp: no convergio (max_iter)")


def _violacion_signos(s: np.ndarray, en_inf: np.ndarray, en_sup: np.ndarray) -> float:
    v = 0.0
    if en_inf.any():
        v = max(v, float(np.maximum(-s[en_inf], 0.0).max()))
    if en_sup.any():
        v = max(v, float(np.maximum(s[en_sup], 0.0).max()))
    return v


def verificar_kkt(Sigma: np.ndarray, A: np.ndarray, b: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                  w: np.ndarray, eps_cota: float = 1e-9) -> dict:
    """Chequeo INDEPENDIENTE del solver. Devuelve las violaciones maximas;
    `ok` si todas <= TOL_KKT (relativas a la escala de Sigma).

    Con pocas variables libres (menos que restricciones de igualdad, p.ej.
    el extremo de la frontera, sin ninguna libre) los multiplicadores NO son
    unicos: en vez de tomar el de norma minima (falso negativo), se busca
    entre TODOS los multiplicadores compatibles con la estacionariedad el
    que menos viola los signos en las cotas. Exacto para <= 2 restricciones
    (el caso de este modulo): el optimo de esa busqueda esta en el cero de
    alguna restriccion de signo, asi que se prueban esos candidatos."""
    escala = max(float(np.abs(Sigma).max()), 1e-300)
    n, k = len(w), A.shape[0]
    g = Sigma @ w
    libres = (w > lo + eps_cota) & (w < hi - eps_cota)
    en_inf, en_sup = (w <= lo + eps_cota) & ~libres, (w >= hi - eps_cota) & ~libres

    if libres.any():
        AF = A[:, libres].T                                     # |libres| x k
        nu0 = np.linalg.lstsq(AF, g[libres], rcond=None)[0]
        estacionariedad = float(np.abs(AF @ nu0 - g[libres]).max())
        _, sv, Vt = np.linalg.svd(AF)
        rango = int((sv > 1e-12 * max(sv.max(), 1e-300)).sum())
        N = Vt[rango:].T                                        # k x d, base del espacio nulo
    else:
        nu0, estacionariedad, N = np.zeros(k), 0.0, np.eye(k)
    d = N.shape[1]

    def viol(nu):
        return _violacion_signos(g - A.T @ nu, en_inf, en_sup)

    candidatos = [nu0]
    if d >= 1:
        # rectas donde s_i = 0 en el espacio (z) de multiplicadores: s_i(z) = c_i - r_i . z
        acot = np.where(en_inf | en_sup)[0]
        c = g[acot] - A[:, acot].T @ nu0                        # s_i en z=0
        R = A[:, acot].T @ N                                    # |acot| x d
        if d == 1:
            for ci, ri in zip(c, R[:, 0]):
                if abs(ri) > 1e-14:
                    candidatos.append(nu0 + N[:, 0] * (ci / ri))
        else:
            for a in range(len(acot)):
                for bb in range(a + 1, len(acot)):
                    M = np.array([R[a], R[bb]])
                    if abs(np.linalg.det(M)) > 1e-14:
                        candidatos.append(nu0 + N @ np.linalg.solve(M, np.array([c[a], c[bb]])))
    signos = min(viol(nu) for nu in candidatos)

    factib_eq = float(np.abs(A @ w - b).max())
    factib_caja = float(max(np.maximum(lo - w, 0).max(), np.maximum(w - hi, 0).max()))
    peor = max(estacionariedad, signos) / escala
    return {
        "estacionariedad": estacionariedad / escala, "signo_multiplicadores": signos / escala,
        "factib_igualdad": factib_eq, "factib_caja": factib_caja,
        "ok": bool(peor <= TOL_KKT and factib_eq <= 1e-9 and factib_caja <= 1e-9),
    }


# ---------------------------------------------------------------- carteras notables

def minima_varianza(Sigma: np.ndarray, lo, hi) -> np.ndarray:
    n = Sigma.shape[0]
    lo, hi = _cotas(n, lo, hi)
    A, b = np.ones((1, n)), np.array([1.0])
    w0 = _punto_inicial_suma1(lo, hi)
    return resolver_qp(Sigma, A, b, lo, hi, w0)


def _punto_inicial_suma1(lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Punto factible con suma 1: lo + reparto proporcional al margen."""
    margen = hi - lo
    resto = 1.0 - lo.sum()
    return lo + margen * (resto / margen.sum())


def retorno_maximo(mu: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """LP con cotas: llenar por mayor retorno esperado hasta el tope."""
    w = lo.copy()
    resto = 1.0 - w.sum()
    for i in np.argsort(-mu, kind="stable"):
        d = min(hi[i] - w[i], resto)
        w[i] += d
        resto -= d
        if resto <= 1e-15:
            break
    return w


def punto_frontera(mu, Sigma, lo, hi, R, w_mvp, w_max) -> np.ndarray:
    n = len(mu)
    R_mvp, R_max = float(mu @ w_mvp), float(mu @ w_max)
    if R_max - R_mvp < 1e-15:
        return w_mvp.copy()
    t = min(max((R - R_mvp) / (R_max - R_mvp), 0.0), 1.0)
    if t <= 0.0:
        return w_mvp.copy()
    if t >= 1.0:
        return w_max.copy()
    w0 = (1 - t) * w_mvp + t * w_max
    A = np.vstack([np.ones(n), mu])
    return resolver_qp(Sigma, A, np.array([1.0, R]), lo, hi, w0)


def frontera(mu: np.ndarray, Sigma: np.ndarray, lo, hi, n_puntos: int = N_PUNTOS_FRONTERA) -> dict:
    n = len(mu)
    lo, hi = _cotas(n, lo, hi)
    w_mvp = minima_varianza(Sigma, lo, hi)
    w_max = retorno_maximo(mu, lo, hi)
    R_mvp, R_max = float(mu @ w_mvp), float(mu @ w_max)
    retornos_obj = np.linspace(R_mvp, R_max, n_puntos) if R_max - R_mvp > 1e-15 else np.array([R_mvp])
    pesos = [punto_frontera(mu, Sigma, lo, hi, R, w_mvp, w_max) for R in retornos_obj]
    return {"lo": lo, "hi": hi, "w_mvp": w_mvp, "w_max": w_max, "retornos_obj": retornos_obj, "pesos": pesos}


def maximo_sharpe(mu: np.ndarray, Sigma: np.ndarray, rf: float, fr: dict, iter_aurea: int = 60) -> np.ndarray:
    """Mejor Sharpe de la frontera, refinado por seccion aurea en el tramo
    vecino al mejor punto de la grilla (Sharpe es unimodal a lo largo de la
    frontera eficiente)."""
    lo, hi, w_mvp, w_max = fr["lo"], fr["hi"], fr["w_mvp"], fr["w_max"]
    Rs, ws = fr["retornos_obj"], fr["pesos"]
    sh = [metricas(w, mu, Sigma, rf)["sharpe"] for w in ws]
    k = int(np.nanargmax(sh))
    if len(Rs) == 1:
        return ws[0]
    a = Rs[max(k - 1, 0)]
    c = Rs[min(k + 1, len(Rs) - 1)]

    def f(R):
        return metricas(punto_frontera(mu, Sigma, lo, hi, R, w_mvp, w_max), mu, Sigma, rf)["sharpe"]

    fi = (math.sqrt(5) - 1) / 2
    x1, x2 = c - fi * (c - a), a + fi * (c - a)
    f1, f2 = f(x1), f(x2)
    for _ in range(iter_aurea):
        if f1 > f2:
            c, x2, f2 = x2, x1, f1
            x1 = c - fi * (c - a)
            f1 = f(x1)
        else:
            a, x1, f1 = x1, x2, f2
            x2 = a + fi * (c - a)
            f2 = f(x2)
    mejor = punto_frontera(mu, Sigma, lo, hi, (a + c) / 2, w_mvp, w_max)
    # nunca peor que el mejor punto de la grilla
    if metricas(mejor, mu, Sigma, rf)["sharpe"] < sh[k]:
        return ws[k]
    return mejor


# ---------------------------------------------------------------- nube al azar

def carteras_aleatorias(n_activos: int, cantidad: int, tope: float, semilla: int = 20260923) -> np.ndarray:
    """`cantidad` carteras al azar (suma 100%, 0 <= w <= tope), determinista.
    Mitad Dirichlet(1) (repartidas), mitad Dirichlet(0.3) (mas concentradas,
    para cubrir los bordes); tope aplicado por recorte + reparto iterativo."""
    if n_activos * tope < 1 - 1e-12:
        raise ValueError("tope * n_activos < 1: no hay cartera factible")
    rng = np.random.default_rng(semilla)
    mitad = cantidad // 2
    w = np.vstack([rng.dirichlet(np.ones(n_activos), size=mitad),
                   rng.dirichlet(np.full(n_activos, 0.3), size=cantidad - mitad)])
    for _ in range(n_activos + 2):
        exceso = w > tope + 1e-15
        if not exceso.any():
            break
        sobrante = np.where(exceso, w - tope, 0.0).sum(axis=1)
        w = np.where(exceso, tope, w)
        libre = ~(w >= tope - 1e-15)
        base = np.where(libre, w, 0.0)
        suma = base.sum(axis=1)
        suma = np.where(suma <= 0, 1.0, suma)
        w = w + base / suma[:, None] * sobrante[:, None]
    return w


# ---------------------------------------------------------------- analisis completo (endpoint)

NUBE_TOTAL = 10_000          # carteras al azar de la verificacion (Control A en vivo)
NUBE_MUESTRA = 1_500         # las que se devuelven para dibujar
ROTULO_MAX_SHARPE = "referencia — sensible a retornos históricos"
ROTULO_MIN_VAR = "guía principal — no usa retornos esperados"


def _pct(x: float, dec: int = 4) -> float:
    return round(float(x) * 100, dec)


def _punto(w: np.ndarray, mu: np.ndarray, Sigma: np.ndarray, rf: float) -> dict:
    m = metricas(w, mu, Sigma, rf)
    return {
        "retorno_anual_pct": _pct(anualizar_retorno(m["retorno"])),
        "sigma_anual_pct": _pct(anualizar_sigma(m["sigma"])),
        # Sharpe anualizado (semanal x sqrt(52)) solo para MOSTRAR
        "sharpe": round(m["sharpe"] * math.sqrt(SEMANAS_POR_ANIO), 4),
    }


def analisis_frontera_rv(con, pesos_actuales: dict[str, float], tope: float = TOPE_DEFAULT,
                         ventana_sem: int = VENTANA_DEFAULT_SEM, desvio_pp: float = 5.0, hasta=None) -> dict:
    """Calculo AL VUELO (no guarda nada). `pesos_actuales`: {ticker: peso 0-1
    sobre la RV}, ya calculado por tenencias_rv.py. Levanta ValueError con un
    mensaje para el usuario si no hay datos/activos suficientes."""
    from datetime import date
    from correlacion_rv import _posiciones_vigentes

    hasta = hasta if hasta is not None else configuracion.hoy()
    posiciones = _posiciones_vigentes(con)
    tipos = dict(posiciones)
    ins = construir_retornos(con, posiciones, ventana_sem, hasta)
    df = ins["retornos"]
    tickers = list(df.columns)
    n = len(tickers)
    if n == 0:
        raise ValueError("Ningún activo de la RV tiene historia suficiente para la ventana pedida.")
    if n * tope < 1 - 1e-12:
        raise ValueError(f"Con tope {tope:.0%} hacen falta al menos {math.ceil(1 / tope - 1e-12)} activos y hay {n}: "
                         f"subí el tope o acortá la ventana.")
    fila = con.execute("SELECT valor_pct, fecha_dato, fuente FROM benchmarks_macro WHERE clave='tbill_3m_pct';").fetchone()
    if fila is None:
        raise ValueError("Falta la tasa libre de riesgo (tbill_3m_pct) en benchmarks_macro.")
    rf_anual_pct, rf_fecha, rf_fuente = fila
    rf = rf_semanal(rf_anual_pct)

    mu, Sigma = estadisticas(df)
    F = frontera(mu, Sigma, 0.0, tope)
    lo, hi = F["lo"], F["hi"]
    w_mv = F["w_mvp"]
    w_ms = maximo_sharpe(mu, Sigma, rf, F)

    w_act_raw = np.array([pesos_actuales.get(t, 0.0) for t in tickers])
    cobertura_valor_pct = round(float(w_act_raw.sum()) * 100, 1)
    w_act = w_act_raw / w_act_raw.sum() if w_act_raw.sum() > 0 else np.full(n, 1.0 / n)

    # --- verificaciones en vivo (mismos controles que validar_frontera_rv.py)
    A1, b1 = np.ones((1, n)), np.array([1.0])
    kkt_mv = verificar_kkt(Sigma, A1, b1, lo, hi, w_mv)["ok"]
    kkt_fr = all(
        verificar_kkt(Sigma, np.vstack([np.ones(n), mu]), np.array([1.0, R]), lo, hi, w)["ok"]
        for R, w in zip(F["retornos_obj"], F["pesos"])
    )
    W = carteras_aleatorias(n, NUBE_TOTAL, tope)
    rets, sigs = W @ mu, np.sqrt(np.einsum("ij,jk,ik->i", W, Sigma, W))
    shs = (rets - rf) / sigs
    m_mv, m_ms = metricas(w_mv, mu, Sigma, rf), metricas(w_ms, mu, Sigma, rf)
    ganan_mv = int((sigs < m_mv["sigma"] - 1e-10).sum())
    ganan_ms = int((shs > m_ms["sharpe"] + 1e-10).sum())
    idx = np.linspace(0, NUBE_TOTAL - 1, NUBE_MUESTRA).astype(int)

    filas = []
    for i, t in enumerate(tickers):
        d_mv, d_ms = (w_mv[i] - w_act[i]) * 100, (w_ms[i] - w_act[i]) * 100
        filas.append({
            "ticker": t, "tipo": tipos.get(t),
            "peso_actual_pct": round(float(w_act[i]) * 100, 2),
            "peso_min_var_pct": round(float(w_mv[i]) * 100, 2),
            "peso_max_sharpe_pct": round(float(w_ms[i]) * 100, 2),
            "desvio_min_var_pp": round(float(d_mv), 2), "desvio_max_sharpe_pp": round(float(d_ms), 2),
            "badge_min_var": bool(abs(d_mv) > desvio_pp), "badge_max_sharpe": bool(abs(d_ms) > desvio_pp),
        })
    filas.sort(key=lambda f: -abs(f["desvio_min_var_pp"]))

    avisos = []
    if ins["semanas_descartadas"]:
        avisos.append({"tipo": "semanas_descartadas",
                       "mensaje": f"Se descartaron {len(ins['semanas_descartadas'])} semanas por hueco de datos "
                                  f"({', '.join(ins['semanas_descartadas'])}): no se rellenan.",
                       "semanas": ins["semanas_descartadas"]})
    if ins["excluidos"]:
        avisos.append({"tipo": "activos_excluidos",
                       "mensaje": "Excluidos por historia insuficiente en la ventana: "
                                  + ", ".join(f"{t} ({s} sem)" for t, s in sorted(ins["excluidos"].items())) + ".",
                       "activos": ins["excluidos"]})
    T = ins["ventana_sem_efectiva"]
    if T < 4 * n:
        avisos.append({"tipo": "muestra_corta",
                       "mensaje": f"{T} semanas para {n} activos (menos de 4 por activo): la covarianza y los promedios son poco estables."})
    sobre_tope = [t for t, w in zip(tickers, w_act) if w > tope + 1e-9]
    if sobre_tope:
        avisos.append({"tipo": "actual_sobre_tope",
                       "mensaje": f"Tu cartera actual supera el tope de {tope:.0%} en: {', '.join(sobre_tope)} (los óptimos no pueden hacerlo)."})
    if cobertura_valor_pct < 99.5:
        avisos.append({"tipo": "cobertura",
                       "mensaje": f"Los activos incluidos cubren el {cobertura_valor_pct:.1f} % de tu RV; los pesos se renormalizan sobre ellos."})

    return {
        "parametros": {"tope_pct": round(tope * 100, 2), "ventana_sem": ventana_sem, "desvio_pp": desvio_pp},
        "base": "Retornos semanales simples en USD (subyacente para CEDEARs, MEP para acciones locales); "
                "retorno esperado = promedio histórico; covarianza muestral (n−1). Anualizado: retorno ×52, σ ×√52.",
        "rotulos": {"min_var": ROTULO_MIN_VAR, "max_sharpe": ROTULO_MAX_SHARPE},
        "rf": {"anual_pct": rf_anual_pct, "semanal_pct": _pct(rf, 5), "fecha": rf_fecha, "fuente": rf_fuente},
        "ventana": {"pedida_sem": ventana_sem, "efectiva_sem": T, "desde": ins["desde"].strftime("%Y-%m-%d"),
                    "hasta": ins["hasta_viernes"].strftime("%Y-%m-%d")},
        "semanas_descartadas": ins["semanas_descartadas"],
        "excluidos": ins["excluidos"],
        "n_activos": n,
        "cobertura_valor_pct": cobertura_valor_pct,
        "actual": _punto(w_act, mu, Sigma, rf),
        "min_var": _punto(w_mv, mu, Sigma, rf),
        "max_sharpe": _punto(w_ms, mu, Sigma, rf),
        "frontera": [_punto(w, mu, Sigma, rf) for w in F["pesos"]],
        "nube": [{"retorno_anual_pct": _pct(anualizar_retorno(rets[i])), "sigma_anual_pct": _pct(anualizar_sigma(sigs[i]))}
                 for i in idx],
        "verificacion": {"kkt_min_var_ok": bool(kkt_mv), "kkt_frontera_ok": bool(kkt_fr), "n_azar": NUBE_TOTAL,
                         "azar_superan_min_var": ganan_mv, "azar_superan_max_sharpe": ganan_ms},
        "tabla": filas,
        "avisos": avisos,
    }
