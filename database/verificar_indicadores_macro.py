"""
Verificacion de la Epica 21 contra una base (SOLO LECTURA, abre en modo ro).

1. Reporte por indicador: filas, primera y ultima fecha, valor actual,
   delta 3m/12m, percentil 10 anios.
2. Controles de cordura con datos reales (ticket de la Epica 21, seccion 8).
   Si alguno no da, sale con codigo 1.
3. Umbrales reales de las tesis: valor actual de ANTES (benchmarks_macro,
   como leia alertas_umbrales.py hasta la Epica 20) vs. el de AHORA
   (ultima observacion de la serie). Tienen que coincidir o ser mas nuevos.

Uso:
    python verificar_indicadores_macro.py                 # base real (V:)
    python verificar_indicadores_macro.py --db COPIA.db
"""
import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import indicadores_macro as im
from alertas_umbrales import chequear_umbrales
from brecha_cambiaria import brecha_ccl_mep_actual
from cargar_benchmarks_macro import rem_devaluacion_pct
import configuracion

DB_PATH = configuracion.ruta_db()


def _serie(con, ind_id, desde, hasta):
    return [v for f, v in im.leer_serie(con, ind_id, desde=desde, hasta=hasta)]


def cape_vs_multpl(con) -> tuple[str, bool, str]:
    """Verificacion cruzada del CAPE (ticket: multpl solo como control, no
    como fuente). multpl usa el precio del dia y Shiller el promedio del
    mes: se acepta hasta 5 % de diferencia."""
    import re
    import requests
    nombre = "CAPE de Shiller vs. multpl.com (diferencia < 5 %)"
    serie = im.leer_serie(con, "cape")
    if not serie:
        return nombre, False, "sin serie de CAPE"
    try:
        html = requests.get("https://www.multpl.com/shiller-pe", headers={"User-Agent": "Mozilla/5.0"}, timeout=30).text
        m = re.search(r"Current Shiller PE Ratio is ([0-9]+\.[0-9]+)", html)   # meta description
        ref = float(m.group(1))
    except Exception as e:
        return nombre, False, f"no se pudo leer multpl: {type(e).__name__}"
    dif = abs(serie[-1][1] / ref - 1) * 100
    return nombre, dif < 5, f"Shiller {serie[-1][1]:.2f} ({serie[-1][0]}) vs multpl {ref:.2f}: {dif:.1f} %"


def empleo_vs_fred(con) -> list[tuple[str, bool, str]]:
    """Criterio de aceptacion del ticket de empleo EEUU (v2.47): el ultimo
    valor guardado coincide con FRED hoy. Nominas se recalcula desde el
    NIVEL de PAYEMS como promedio de las 3 diferencias mensuales (la cuenta
    larga, no la simplificada del cargador)."""
    from cargar_indicadores_macro import fred_serie
    out = []
    try:
        payems = fred_serie("PAYEMS", "2024-01-01")
        ic4 = fred_serie("IC4WSA", "2025-01-01")
        civ = fred_serie("CIVPART", "2024-01-01")
    except Exception as e:
        return [("Empleo EEUU vs. FRED en vivo", False, f"no se pudo leer FRED: {type(e).__name__}")]
    n = payems[-4:]
    difs = [n[i + 1][1] - n[i][1] for i in range(3)]
    esperado = {"nominas_eeuu_prom3m": (n[-1][0], sum(difs) / 3),
                "subsidios_desempleo_4s": (ic4[-1][0], ic4[-1][1] / 1000),
                "participacion_eeuu": civ[-1]}
    for ind_id, (fecha, valor) in esperado.items():
        guardado = im.leer_serie(con, ind_id)
        ultimo = guardado[-1] if guardado else (None, None)
        ok = ultimo[0] == fecha and ultimo[1] is not None and abs(ultimo[1] - valor) < 1e-6
        out.append((f"{ind_id}: ultimo valor = FRED en vivo", ok,
                    f"base {ultimo[1]} ({ultimo[0]}) vs FRED {valor:.4f} ({fecha})"
                    + (f"; diferencias {difs}" if ind_id == "nominas_eeuu_prom3m" else "")))
    return out


def controles(con) -> list[tuple[str, bool, str]]:
    out = []

    v = _serie(con, "curva_10y_2y", "2022-07-01", "2024-06-30")
    neg = sum(1 for x in v if x < 0) / len(v) if v else 0
    out.append(("T10Y2Y negativo la mayor parte de mediados 2022 a mediados 2024",
                neg > 0.8, f"{neg:.0%} de {len(v)} dias negativos"))

    v = _serie(con, "sahm", "2024-05-01", "2024-09-30")
    out.append(("SAHMREALTIME >= 0,50 en algun mes de mediados de 2024",
                bool(v) and max(v) >= 0.5, f"max {max(v) if v else None} en {len(v)} meses"))

    v21 = _serie(con, "tasa_real_corto", "2021-01-01", "2021-12-31")
    v24 = _serie(con, "tasa_real_corto", "2024-01-01", "2024-12-31")
    out.append(("Tasa real de corto muy negativa en 2021",
                len(v21) == 12 and max(v21) < -1, f"2021: min {min(v21):.2f} max {max(v21):.2f} ({len(v21)} meses)"))
    out.append(("Tasa real de corto positiva en 2024",
                len(v24) == 12 and min(v24) > 0, f"2024: min {min(v24):.2f} max {max(v24):.2f} ({len(v24)} meses)"))

    if im.indicador(con, "cape"):   # Tanda 2 (v2.40)
        v = _serie(con, "emae_ia", "2020-04-01", "2020-04-01")
        out.append(("EMAE i.a. muy negativo en abril 2020 (cuarentena)",
                    bool(v) and v[0] < -20, f"abr-2020: {v[0] if v else None}"))
        v = _serie(con, "deuda_pbi_eeuu", "2020-01-01", "2020-12-31")
        out.append(("Deuda federal/PBI por encima de 120 % en 2020",
                    bool(v) and max(v) > 120, f"max 2020: {max(v) if v else None}"))
        v = _serie(con, "tips_10y", "2021-01-01", "2021-12-31")
        neg = sum(1 for x in v if x < 0) / len(v) if v else 0
        out.append(("TIPS 10 años negativo en 2021", neg > 0.95, f"{neg:.0%} de {len(v)} dias negativos"))
        v = _serie(con, "intereses_pbi_eeuu", "2024-01-01", "2024-12-31")
        out.append(("Intereses federales/PBI entre 3 % y 5 % en 2024",
                    bool(v) and 3 < min(v) and max(v) < 5, f"2024: {v}"))
        v = _serie(con, "cape", "1999-06-01", "2000-06-01")
        out.append(("CAPE por encima de 40 en la burbuja de 1999-2000",
                    bool(v) and max(v) > 40, f"max: {max(v) if v else None}"))
        out.append(cape_vs_multpl(con))

    if im.indicador(con, "pe_forward"):   # Tanda 3 (v2.41)
        v = _serie(con, "nfci", "2008-09-01", "2008-12-31")
        out.append(("NFCI por encima de 2 en la crisis de 2008", bool(v) and max(v) > 2, f"max: {max(v) if v else None}"))
        v = _serie(con, "balance_fed", "2022-01-01", "2022-12-31")
        out.append(("Balance de la Fed por encima de 8,5 billones USD en 2022",
                    bool(v) and max(v) > 8_500_000, f"max: {max(v) if v else None} MUSD"))
        v = _serie(con, "buffett", "2021-10-01", "2021-10-01")
        out.append(("Indicador Buffett por encima de 150 % a fines de 2021", bool(v) and v[0] > 150, f"2021-Q4: {v}"))
        v = _serie(con, "deficit_federal_pbi", "2020-01-01", "2020-01-01")
        out.append(("Resultado fiscal federal peor que -10 % del PBI en 2020", bool(v) and v[0] < -10, f"2020: {v}"))
        v23 = _serie(con, "resultado_primario_ingresos", "2023-12-01", "2023-12-01")
        v24 = _serie(con, "resultado_primario_ingresos", "2024-12-01", "2024-12-01")
        out.append(("Resultado primario AR negativo en 2023 y positivo en 2024 (12 meses)",
                    bool(v23 and v24) and v23[0] < 0 < v24[0], f"dic-23: {v23}, dic-24: {v24}"))
        v = _serie(con, "pe_forward", None, None)
        out.append(("P/E forward: 400+ semanas, todas entre 10 y 30",
                    len(v) >= 400 and all(10 < x < 30 for x in v),
                    f"{len(v)} semanas, rango {min(v) if v else None}-{max(v) if v else None}"))
        v = im.leer_serie(con, "prima_riesgo_acciones")
        out.append(("Prima de riesgo de acciones calculada para la ultima semana con P/E",
                    bool(v) and v[-1][0] == im.leer_serie(con, "pe_forward")[-1][0], f"ultima: {v[-1] if v else None}"))

    if im.indicador(con, "baa_spread"):   # ajustes 2026-09-30 (v2.42)
        v = _serie(con, "baa_spread", "2008-09-01", "2009-03-31")
        out.append(("Spread Baa por encima de 5 pp en la crisis de 2008", bool(v) and max(v) > 5,
                    f"max: {max(v) if v else None}"))
        v = im.leer_serie(con, "vencimientos_me_12m")
        out.append(("Vencimientos en moneda extranjera 12m entre USD 3.000 y 40.000 M (ultimo)",
                    bool(v) and 3000 < v[-1][1] < 40000, f"ultimo: {v[-1] if v else None}, {len(v)} trimestres"))
        v = im.leer_serie(con, "merval_usd_ccl")
        out.append(("Merval en USD CCL con historia desde 2013", len(v) > 2500 and v[0][0] < "2013-03-01",
                    f"{len(v)} dias, desde {v[0][0] if v else None}, ultimo {v[-1] if v else None}"))
        fuente = im.indicador(con, "prima_plazo_10y")["fuente"]
        out.append(("Prima por plazo etiquetada Kim-Wright", "Kim-Wright" in fuente, fuente))

    if im.indicador(con, "nominas_eeuu_prom3m"):   # empleo EEUU (v2.47)
        v = _serie(con, "nominas_eeuu_prom3m", "2020-03-01", "2020-06-01")
        out.append(("Nominas prom. 3m por debajo de -5.000 miles en la primavera de 2020", bool(v) and min(v) < -5000,
                    f"min: {min(v) if v else None}"))
        v = _serie(con, "subsidios_desempleo_4s", "2020-03-01", "2020-06-30")
        out.append(("Subsidios (prom. 4 semanas) por encima de 4.000 miles en 2020", bool(v) and max(v) > 4000,
                    f"max: {max(v) if v else None}"))
        v = _serie(con, "participacion_eeuu", "2020-04-01", "2020-04-01")
        out.append(("Participacion laboral por debajo de 61 % en abril 2020", bool(v) and v[0] < 61, f"abr-2020: {v}"))
        out += empleo_vs_fred(con)

    fuera, max_no_100 = [], []
    for ind in im.catalogo(con):
        serie = im.leer_serie(con, ind["id"])
        m = im.calcular_metricas(serie, ind["tipo_delta"], ind["frecuencia"])
        p = m["percentil_10a"]
        if p is not None and not (0 <= p <= 100):
            fuera.append(ind["id"])
        if serie:
            inicio = im.restar_meses(date.fromisoformat(m["fecha"]), 120).isoformat()
            ventana = [x for f, x in serie if f >= inicio]
            mx = max(ventana)
            # con 1 sola observacion no hay percentil (zpindex arranca de cero)
            if len(ventana) >= 2 and ventana.count(mx) == 1 and im.percentil(ventana, mx) != 100:
                max_no_100.append(ind["id"])
    out.append(("Percentil siempre entre 0 y 100", not fuera, f"fuera de rango: {fuera or 'ninguno'}"))
    out.append(("El maximo de cada ventana da percentil 100", not max_no_100, f"fallan: {max_no_100 or 'ninguno'}"))
    return out


def valor_antes(con, indicador: str):
    """Como resolvia alertas_umbrales.py el valor actual hasta la Epica 20."""
    mapa = {"riesgo_pais": "riesgo_pais_ar", "inflacion_mensual": "inflacion_ars_mensual", "rem_inflacion_12m": "rem"}
    if indicador == "ccl_mep_brecha_pct":
        b = brecha_ccl_mep_actual(con)
        return (b["brecha_pct"], b["fecha"]) if b else (None, None)
    if indicador == "rem_devaluacion_esperada_12m":
        fila = con.execute("SELECT detalle_json, fecha_dato FROM benchmarks_macro WHERE clave='rem'").fetchone()
        return (rem_devaluacion_pct(json.loads(fila[0])), fila[1]) if fila else (None, None)
    fila = con.execute("SELECT valor_pct, fecha_dato FROM benchmarks_macro WHERE clave=?",
                       (mapa.get(indicador, indicador),)).fetchone()
    return fila if fila else (None, None)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB_PATH)
    con = sqlite3.connect(f"file:{ap.parse_args().db.as_posix()}?mode=ro", uri=True)

    print(f"{'indicador':32s} {'filas':>6s} {'primera':>10s} {'ultima':>10s} {'valor':>12s} "
          f"{'d3m':>8s} {'d12m':>8s} {'pctil':>6s} hist")
    for ind in im.listar_indicadores(con):
        filas = con.execute("SELECT COUNT(*), MIN(fecha) FROM macro_historico WHERE clave=?", (ind["id"],)).fetchone()
        if ind["id"] in im.DERIVADOS:
            serie = im.leer_serie(con, ind["id"])
            filas = (len(serie), serie[0][0] if serie else None)
        f = lambda x, d=2: "-" if x is None else f"{x:.{d}f}"
        print(f"{ind['id']:32s} {filas[0]:6d} {filas[1] or '-':>10s} {ind['fecha'] or '-':>10s} "
              f"{f(ind['valor'], ind['decimales']):>12s} {f(ind['delta_3m']):>8s} {f(ind['delta_12m']):>8s} "
              f"{f(ind['percentil_10a'], 0):>6s} "
              f"{str(ind['anios_historia']) + ' a' + (' (CORTA)' if ind['historia_corta'] else '') if ind['anios_historia'] is not None else '-'}")

    print("\n=== Controles de cordura ===")
    fallas = 0
    for nombre, ok, detalle in controles(con):
        fallas += not ok
        print(f"  {'OK   ' if ok else 'FALLA'} {nombre} -- {detalle}")

    print("\n=== Umbrales reales de tesis: valor actual antes (benchmarks) vs. ahora (serie) ===")
    for u in chequear_umbrales(con):
        antes, fecha_antes = valor_antes(con, u["indicador"])
        igual = antes is not None and u["valor_actual"] is not None and abs(antes - u["valor_actual"]) < 1e-3
        mas_nuevo = (u["fecha_valor_actual"] or "") > (fecha_antes or "")
        ok = igual or mas_nuevo
        fallas += not ok
        print(f"  {'OK   ' if ok else 'FALLA'} umbral {u['umbral_id']:3d} {u['indicador']:30s} "
              f"antes {antes} ({fecha_antes}) / ahora {u['valor_actual']} ({u['fecha_valor_actual']}) "
              f"cruzado={u['cruzado']}")

    con.close()
    print(f"\n{'TODO OK' if not fallas else f'{fallas} CONTROL(ES) FALLARON'}")
    return 1 if fallas else 0


if __name__ == "__main__":
    sys.exit(main())
