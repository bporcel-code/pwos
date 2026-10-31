"""PWOS -- el inversor demo (paso 7b): todo lo INVENTADO de la demo.

La persona, sus contenedores, su sueldo, sus decisiones y sus textos son
ficticios. Ningun precio, dolar, tasa, cronograma ni indicador sale de aca:
los pone el mercado real de cada fecha (ver generar_demo.py). Las tesis son
funciones que reciben `m` (el mercado real, ver generar_demo.Mercado) y la
fecha en que se escriben: usan solo datos de ese dia o anteriores. Las
reflexiones de cierre reciben ademas el resultado real.

Montos en USD; la cantidad de cada operacion la calcula el generador con el
cierre real del dia y el MEP real.
"""

# --- catalogo de activos: (ticker, tipo, moneda_nominal, nombre) -------------------
ACTIVOS = [
    ("USD_MEP", "fx", "USD", "Dólar MEP"),
    ("USD_CCL", "fx", "USD", "Dólar contado con liquidación"),
    ("AL30", "bono_soberano", "USD", "Bonar 2030 (ley local)"),
    ("GD30", "bono_soberano", "USD", "Global 2030 (ley NY)"),
    ("GD35", "bono_soberano", "USD", "Global 2035 (ley NY)"),
    ("PN36O", "on", "USD", "ON Pan American Energy 2036"),
    ("S31G6", "lecap", "ARS", "LECAP 31/08/2026"),
    ("T30A7", "lecap", "ARS", "BONCAP 30/04/2027"),
    ("SPY", "cedear", "ARS", "SPDR S&P 500 ETF"),
    ("KO", "cedear", "ARS", "Coca-Cola"),
    ("XOM", "cedear", "ARS", "Exxon Mobil"),
    ("PG", "cedear", "ARS", "Procter & Gamble"),
    ("PEP", "cedear", "ARS", "PepsiCo"),
    ("MSFT", "cedear", "ARS", "Microsoft"),
    ("ALUA", "accion", "ARS", "Aluar"),
    ("TXAR", "accion", "ARS", "Ternium Argentina"),
    ("PAMP", "accion", "ARS", "Pampa Energía"),
]
FUENTE_PRECIO = {  # de donde sale el precio diario de cada uno
    "AL30": "rava", "GD30": "rava", "GD35": "rava", "PN36O": "rava", "S31G6": "rava", "T30A7": "rava",
    "SPY": "yfinance", "KO": "yfinance", "XOM": "yfinance", "PG": "yfinance", "PEP": "yfinance",
    "MSFT": "yfinance", "ALUA": "yfinance", "TXAR": "yfinance", "PAMP": "yfinance",
}

# --- contenedores y cuentas: (nombre, tipo, [(moneda, estado), ...]) ------------------
CONTENEDORES = [
    ("Banco Demo", "banco", [("ARS", "disponible"), ("USD", "disponible"), ("ARS", "plazo_fijo"), ("USD", "plazo_fijo")]),
    ("Broker A", "broker", [("ARS", "disponible"), ("USD", "disponible")]),
    ("Broker B", "broker", [("ARS", "disponible"), ("USD", "disponible")]),  # sin cuenta caucionada (decision del usuario)
    ("Billetera Demo", "billetera_virtual", [("ARS", "disponible")]),        # no paga interes
]
CATEGORIAS_EXTRA = [("Compra de inmueble", None, "egreso"), ("Crédito hipotecario", None, "egreso")]

# --- sueldo y ahorro (USD por mes; el ARS sale del MEP del dia de cobro) ---------------
INICIO = "2024-01-02"
APORTE_INICIAL_USD = 30_000
DIA_SUELDO = 5
AHORRO_USD = {2024: 1_000, 2025: 1_200, 2026: 1_400}
SUELDO_USD = {2024: 2_300, 2025: 2_600, 2026: 2_900}
PARTE_GASTOS_BILLETERA = 0.70   # el resto, servicios pagados desde el banco
# Hasta abril de 2024 el ahorro se queda en pesos, en plazo fijo a 30 dias
# (con la tasa real del BCRA); desde mayo se pasa a dolares (plazo fijo en USD).
AHORRO_EN_PESOS_HASTA = "2024-04-30"

# --- operaciones (fecha pedida, accion, datos). El generador mueve la fecha al
# primer dia habil con precio y lo anota; si no hay dato, la saca y lo anota. --------
OPERACIONES = [
    ("2024-01-03", "transferir_usd", {"desde": "Banco Demo", "hacia": "Broker A", "usd": 9_000}),
    ("2024-01-03", "transferir_usd", {"desde": "Banco Demo", "hacia": "Broker B", "usd": 2_400}),
    ("2024-01-04", "comprar", {"broker": "Broker A", "ticker": "SPY", "usd": 4_000}),
    ("2024-01-04", "comprar", {"broker": "Broker A", "ticker": "KO", "usd": 3_000}),
    ("2024-01-04", "comprar", {"broker": "Broker A", "ticker": "XOM", "usd": 2_000}),
    ("2024-01-05", "comprar", {"broker": "Broker B", "ticker": "ALUA", "usd": 800}),
    ("2024-01-05", "comprar", {"broker": "Broker B", "ticker": "TXAR", "usd": 800}),
    ("2024-02-09", "comprar", {"broker": "Broker B", "ticker": "PAMP", "usd": 800}),
    # Bonos despues del pago del 09/07/2024: el cronograma publico de Rava trae
    # los pagos desde ene-2025, asi que antes no habria con que cobrar.
    ("2024-07-10", "comprar", {"broker": "Broker A", "ticker": "AL30", "usd": 7_000}),
    ("2024-07-10", "comprar", {"broker": "Broker A", "ticker": "GD30", "usd": 5_000}),
    ("2024-07-17", "comprar", {"broker": "Broker A", "ticker": "PG", "usd": 1_500}),
    ("2024-07-17", "comprar", {"broker": "Broker A", "ticker": "PEP", "usd": 1_500}),
    ("2024-12-16", "vender", {"broker": "Broker A", "ticker": "AL30", "parte": 1.0}),
    ("2025-01-15", "comprar", {"broker": "Broker B", "ticker": "PN36O", "usd": 2_000}),
    ("2025-09-15", "comprar", {"broker": "Broker A", "ticker": "GD35", "usd": 3_000}),
    ("2025-10-28", "vender", {"broker": "Broker A", "ticker": "GD30", "parte": 1.0}),
    ("2025-11-12", "comprar", {"broker": "Broker B", "ticker": "S31G6", "usd": 1_500}),
    ("2025-11-20", "comprar_inmueble", {}),
    ("2026-01-15", "comprar", {"broker": "Broker B", "ticker": "T30A7", "usd": 1_500}),
    ("2026-03-10", "comprar", {"broker": "Broker A", "ticker": "MSFT", "usd": 2_000}),
    ("2026-07-15", "rebalancear_rv", {"broker": "Broker A", "vender_parte": 0.30}),
    ("2026-09-02", "comprar", {"broker": "Broker B", "ticker": "T30A7", "usd": 1_500}),
]

# --- Real Estate ----------------------------------------------------------------
INMUEBLE = {
    "alias": "DEPTO-CABA-2AMB",
    "m2": 36,
    "gastos_escritura_pct": 4.0,      # escribano, sellos, comision: supuesto de la persona
    "credito_usd": 40_000,
    "credito_alias": "Crédito hipotecario demo",
    # El modelo de pasivos de PWOS no tiene interes: el credito se modela en USD
    # sin interes, en 240 cuotas de capital (simplificacion documentada).
    "credito_cuotas": 240,
    "alquiler_desde": "2025-12-01",
    "dia_cobro": 10,
    "ajuste_meses": 3,                # ajuste trimestral por la inflacion real (INDEC)
    "abl_usd_anual": 180,             # impuesto, supuesto de la persona
    "mantenimiento": [("2026-03-20", 250, "Pintura y arreglos"), ("2026-08-12", 120, "Cambio de calefón (parte)")],
}

# --- renta variable: ficha de cada posicion (tesis_inversion) ------------------------
FICHAS_RV = {
    "SPY": {"cubeta": "calidad", "peso_objetivo_pct": 30, "horizonte": "largo (10 años)",
            "motivo": "Índice amplio de EE.UU.: la base de la renta variable, sin elegir ganadores.",
            "riesgo": "Valuación exigente del S&P 500 y concentración en pocas tecnológicas.",
            "criterio_venta": "Solo para rebalancear o si la RV pasa del objetivo del patrimonio."},
    "KO": {"cubeta": "calidad", "peso_objetivo_pct": 15, "horizonte": "largo",
           "motivo": "Marca dominante, márgenes estables y 60 años subiendo el dividendo.",
           "riesgo": "Crecimiento lento; cambio de hábitos de consumo.",
           "criterio_venta": "Recorte del dividendo o pérdida sostenida de margen."},
    "XOM": {"cubeta": "valor_puro", "peso_objetivo_pct": 10, "horizonte": "medio (3-5 años)",
            "motivo": "Energía barata contra el mercado, con caja para dividendos y recompras.",
            "riesgo": "Precio del petróleo; transición energética.",
            "criterio_venta": "Si deja de cotizar con descuento contra su historia o el Brent se derrumba."},
    "PG": {"cubeta": "calidad", "peso_objetivo_pct": 12, "horizonte": "largo",
           "motivo": "Consumo masivo defensivo para bajar la volatilidad de la cartera.",
           "riesgo": "Competencia de segundas marcas; dólar fuerte.",
           "criterio_venta": "Caída de ventas orgánicas dos años seguidos."},
    "PEP": {"cubeta": "calidad", "peso_objetivo_pct": 10, "horizonte": "largo",
            "motivo": "Snacks y bebidas: diversificación dentro de consumo masivo.",
            "riesgo": "Regulación de alimentos ultraprocesados.",
            "criterio_venta": "Recorte del dividendo."},
    "MSFT": {"cubeta": "calidad", "peso_objetivo_pct": 10, "horizonte": "largo",
             "motivo": "Nube e IA con un negocio de software que ya da caja.",
             "riesgo": "Valuación; competencia en IA.",
             "criterio_venta": "Si el crecimiento de la nube baja de un dígito."},
    "ALUA": {"cubeta": "mayor_riesgo", "peso_objetivo_pct": 4, "horizonte": "medio",
             "motivo": "Exportadora argentina: gana con un tipo de cambio real alto.",
             "riesgo": "Precio del aluminio, energía y atraso cambiario.",
             "criterio_venta": "Si el tipo de cambio real se aprecia fuerte."},
    "TXAR": {"cubeta": "mayor_riesgo", "peso_objetivo_pct": 4, "horizonte": "medio",
             "motivo": "Acero: apuesta a la reactivación de la obra y la industria.",
             "riesgo": "Recesión y apertura de importaciones.",
             "criterio_venta": "Si la actividad no repunta en 2 años."},
    "PAMP": {"cubeta": "mayor_riesgo", "peso_objetivo_pct": 5, "horizonte": "medio",
             "motivo": "Energía argentina con Vaca Muerta y tarifas normalizándose.",
             "riesgo": "Intervención de tarifas; precio del gas.",
             "criterio_venta": "Si vuelven los congelamientos de tarifas."},
}

# Indicadores de tesis por posicion: se calculan con datos reales (precio,
# dividendos y series macro). (ticker, nombre, metrica, direccion, verde, rojo)
INDICADORES_RV = [
    ("SPY", "S&P 500 interanual (%)", "macro:sp500_interanual", "mayor_es_mejor", 5.0, -10.0),
    ("SPY", "Caída desde el máximo de 52 semanas (%)", "caida_52s", "menor_es_mejor", 10.0, 20.0),
    ("KO", "Dividend yield 12 meses (%)", "dividend_yield", "mayor_es_mejor", 2.8, 2.0),
    ("KO", "Caída desde el máximo de 52 semanas (%)", "caida_52s", "menor_es_mejor", 10.0, 20.0),
    ("XOM", "Brent (USD)", "macro:brent", "mayor_es_mejor", 70.0, 55.0),
    ("XOM", "Dividend yield 12 meses (%)", "dividend_yield", "mayor_es_mejor", 3.0, 2.0),
    ("PG", "Caída desde el máximo de 52 semanas (%)", "caida_52s", "menor_es_mejor", 10.0, 20.0),
    ("PEP", "Dividend yield 12 meses (%)", "dividend_yield", "mayor_es_mejor", 3.0, 2.2),
    ("MSFT", "Caída desde el máximo de 52 semanas (%)", "caida_52s", "menor_es_mejor", 12.0, 25.0),
    ("ALUA", "Riesgo país (pb)", "macro:riesgo_pais", "menor_es_mejor", 800.0, 1400.0),
    ("PAMP", "Riesgo país (pb)", "macro:riesgo_pais", "menor_es_mejor", 800.0, 1400.0),
]

WATCHLIST = [
    ("MELI", "MercadoLibre", "calidad", "Comercio y fintech de la región; esperar una caída para entrar."),
    ("GOOGL", "Alphabet", "calidad", "Búsqueda + nube; vigilar el impacto de la IA en el negocio de anuncios."),
]
SIMULACIONES = [("MELI", 1_500, "aporte_nuevo"), ("GOOGL", 1_000, "KO")]  # fuente: aporte nuevo o vender esa posicion

PARAMETROS_RV = {"peso_objetivo_rv_patrimonio_pct": 30.0}


# --- textos (se escriben con el dato real del dia) ---------------------------------
def _n(x, d=0):
    return f"{x:,.{d}f}".replace(",", "X").replace(".", ",").replace("X", ".") if x is not None else "s/d"


def tesis_cartera_v1(m, f):
    return ("Meta: llegar a 2028 con una cartera que viva de rentas en dólares. Mezcla objetivo: "
            "renta fija argentina en dólares ~40 %, renta variable ~30 %, el resto en un inmueble para alquilar "
            "cuando junte el capital. Ahorro fijo todos los meses, sin intentar adivinar el mercado. "
            f"Hoy el MEP está en ${_n(m.mep(f))} y la inflación de diciembre fue {_n(m.macro('inflacion_mensual', f), 1)} %: "
            "los pesos no son un lugar para el ahorro de largo plazo.",
            "Que la normalización de la economía fracase y vuelva un cepo más duro; perder el empleo; "
            "concentrarme demasiado en Argentina.", "media")


def reflexion_cartera_v1(m, f):
    return (f"Dos años después: la renta fija argentina rindió mucho más de lo esperado (riesgo país de "
            f"{_n(m.macro('riesgo_pais', '2024-01-02'))} a {_n(m.macro('riesgo_pais', f))} pb) y el inmueble se compró "
            "antes de lo planeado gracias a eso. La renta variable cumplió su papel de base. Paso a una tesis v2 "
            "con el inmueble ya comprado.")


def tesis_cartera_v2(m, f):
    return ("Con el departamento comprado y alquilado, la cartera financiera vuelve a crecer desde menos de la mitad "
            "de lo que era. Prioridad: pagar la cuota del crédito con el alquiler, seguir ahorrando "
            f"y reconstruir la renta variable hasta el {PARAMETROS_RV['peso_objetivo_rv_patrimonio_pct']:.0f} % "
            f"del patrimonio. Riesgo país hoy: {_n(m.macro('riesgo_pais', f))} pb.",
            "Vacancia del departamento; que el alquiler quede atrás de la inflación; tasas en dólares más altas.", "alta")


def tesis_al30(m, f):
    return (f"Riesgo país en {_n(m.macro('riesgo_pais', f))} pb con superávit fiscal sostenido seis meses. Si el "
            "ajuste se sostiene, el riesgo país debería bajar hacia 1.000 pb en 12-18 meses. AL30 (ley local) "
            f"cotiza a USD {_n(m.precio_usd('AL30', f), 2)} cada 100 nominales: es la apuesta más directa. "
            "Plan: tomar ganancia si el riesgo país perfora los 800 pb.",
            "Que el ajuste pierda apoyo político; que no se acumulen reservas para pagar los vencimientos de 2025; "
            "ley local: menos protección que un Global.", "media")


def reflexion_al30(m, f, resultado_pct):
    return (f"Vendido con el riesgo país en {_n(m.macro('riesgo_pais', f))} pb (era {_n(m.macro('riesgo_pais', '2024-07-10'))} "
            f"al comprar): resultado {_n(resultado_pct, 1)} % en dólares en cinco meses. La compresión llegó antes de lo "
            "que esperaba; me quedo con el GD30 por la ley NY.")


def tesis_gd30(m, f):
    return (f"Misma tesis que el AL30 pero con ley de Nueva York: cotiza a USD {_n(m.precio_usd('GD30', f), 2)}. "
            "Es la pata que me quedo si la compresión sigue, y la que vendo para comprar el departamento.",
            "Los mismos que el AL30; un evento político que devuelva el riesgo país a 2.000 pb.", "media")


def reflexion_gd30(m, f, resultado_pct):
    return (f"Vendido después de las legislativas, con el riesgo país en {_n(m.macro('riesgo_pais', f))} pb. "
            f"Resultado {_n(resultado_pct, 1)} % en dólares (precio más cupones y amortizaciones cobradas). "
            "Los fondos van a la compra del departamento.")


def tesis_gd35(m, f):
    return (f"A un mes de las legislativas el riesgo país volvió a {_n(m.macro('riesgo_pais', f))} pb. Si el oficialismo "
            "hace una buena elección, el tramo largo es el que más comprime. GD35 a "
            f"USD {_n(m.precio_usd('GD35', f), 2)}: apuesta chica y con convicción baja.",
            "Mala elección y salto del riesgo país; duration alta: el que más cae si las cosas salen mal.", "baja")


def tesis_lecap(m, f, ticker):
    return (f"Pesos que no voy a necesitar en meses: LECAP {ticker} a tasa fija. Inflación del último mes "
            f"{_n(m.macro('inflacion_mensual', f), 1)} %, plazo fijo a 30 días {_n(m.macro('tasa_pf_30d', f), 1)} % TNA. "
            "Gano si la inflación sigue bajando.",
            "Que la inflación se acelere y la tasa fija quede negativa en términos reales.", "media")


def reflexion_lecap(m, f, ticker, inflacion_periodo_pct, rendimiento_pct):
    return (f"Cobrada al vencimiento: {_n(rendimiento_pct, 1)} % en pesos contra una inflación de "
            f"{_n(inflacion_periodo_pct, 1)} % en el mismo periodo.")


def tesis_inmueble(m, f):
    zp = m.macro("zpindex_caba_venta_usd_m2", f)
    return (f"Comprar un 2 ambientes de unos {INMUEBLE['m2']} m² en CABA para alquilar. El zpindex marca "
            f"USD {_n(zp)} por m²: unos USD {_n(zp * INMUEBLE['m2'])} más gastos. Zonaprop calcula una rentabilidad "
            "bruta de alquiler del 4,8 % anual y los contratos se pactan libres desde el DNU 70/2023. Financio "
            f"USD {_n(INMUEBLE['credito_usd'])} con un crédito y pongo el resto con lo ahorrado y la venta del GD30.",
            "Vacancia; que el alquiler en pesos quede atrás de la inflación; que el precio por m² siga planchado.", "media")


def tesis_rv(m, f, ticker):
    fic = FICHAS_RV[ticker]
    return (f"{fic['motivo']} Precio del CEDEAR hoy: ${_n(m.precio(ticker, f), 0)} (CCL ${_n(m.ccl(f))}).",
            fic["riesgo"], "alta" if fic["cubeta"] == "calidad" else "media")


def tesis_acciones_ar(m, f):
    return (f"Acciones argentinas como apuesta a la normalización: el Merval en dólares está en mínimos históricos "
            f"y el riesgo país en {_n(m.macro('riesgo_pais', f))} pb. Peso chico (cubeta de mayor riesgo).",
            "Recesión profunda por el ajuste; vuelta de controles.", "baja")


# --- macro: anotaciones (umbral y nota de la persona) y fases de la "Maquina economica"
# (indicador, umbral_valor, umbral_direccion, objetivo_valor, nota)
ANOTACIONES = [
    ("riesgo_pais", 1000.0, "mayor_que", 600.0, "Por encima de 1.000 pb, la tesis de compresión se cae. Objetivo: 600."),
    ("inflacion_mensual", 4.0, "mayor_que", 2.0, "Si vuelve a más de 4 % mensual, revisar todo lo que esté en pesos."),
    ("tasa_pf_30d", None, None, None, "Referencia para el plazo fijo y las LECAP: comparar siempre con la inflación."),
    ("reservas_bcra", 25000.0, "menor_que", None, "Con menos de USD 25.000 M peligra el pago de los bonos."),
    ("ust_10y_pct", 5.0, "mayor_que", None, "Tasa larga de EE.UU. arriba de 5 % le pega a las acciones y a los emergentes."),
    ("hy_spread", 4.5, "mayor_que", None, "Spread high yield: termómetro del apetito por riesgo global."),
    ("curva_10y_3m", 0.0, "menor_que", None, "Curva invertida: aviso temprano de recesión en EE.UU."),
    ("cape", 35.0, "mayor_que", None, "S&P 500 caro: no aumentar la RV de golpe, seguir con aportes."),
    ("zpindex_caba_venta_usd_m2", None, None, None, "Precio por m² de CABA: lo que vale el departamento."),
]
FASES_MAQUINA = [
    ("ciclo_corto", "2024-01-15", "Desinflación con recesión: tasas reales negativas en pesos, dólar quieto."),
    ("ciclo_corto", "2025-04-14", "Salida del cepo con bandas cambiarias: menos riesgo de devaluación brusca."),
    ("ciclo_corto", "2025-10-27", "Después de las legislativas: compresión del riesgo país y reactivación del crédito."),
    ("ciclo_largo", "2024-01-15", "EE.UU. con tasas altas y deuda creciente: prima por plazo en alza."),
    ("valuacion", "2024-01-15", "S&P 500 caro por CAPE; emergentes y Argentina baratos."),
    ("valuacion", "2026-01-05", "El S&P 500 sigue caro; Argentina ya no está tan barata después de la suba."),
    ("productividad", "2024-06-03", "IA como posible motor de productividad: todavia sin datos que lo confirmen."),
]
