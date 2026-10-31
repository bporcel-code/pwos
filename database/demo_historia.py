"""PWOS -- la linea de tiempo del inversor demo (paso 7b).

Recorre, en orden de fecha, el sueldo, los gastos, el ahorro, los plazos
fijos, las operaciones de demo_cronologia.OPERACIONES, los cobros de bonos y
dividendos (reales: se pagan solo si hay tenencia), el inmueble, el alquiler
y la cuota del credito. Escribe con demo_inversor.Libro y con las funciones de
alta del motor (tesis, propiedades, alquileres, pasivos, valuaciones, gastos).
"""
import heapq
from datetime import date, timedelta

import demo_cronologia as cron
from demo_inversor import (VN100, Libro, Mercado, SinDato, DIAS_PAGO_DIVIDENDO_CEDEAR,
                           DIAS_PAGO_DIVIDENDO_LOCAL)
import demo_inversor


def _d(f: str) -> date:
    return date.fromisoformat(f)


def _s(d: date) -> str:
    return d.isoformat()


def _meses(desde: str, hasta: str):
    a, m = int(desde[:4]), int(desde[5:7])
    while f"{a}-{m:02d}" <= hasta[:7]:
        yield a, m
        a, m = (a + 1, 1) if m == 12 else (a, m + 1)


def _mes_anterior(f: str) -> str:
    d = _d(f).replace(day=1) - timedelta(days=1)
    return _s(d.replace(day=1))


def _sumar_meses(f: str, n: int) -> str:
    a, m = int(f[:4]), int(f[5:7]) + n
    a, m = a + (m - 1) // 12, (m - 1) % 12 + 1
    return f"{a}-{m:02d}-{f[8:10]}"


class Historia:
    """Al terminar deja en `hechos` lo que necesitan las fases siguientes."""

    def __init__(self, con, m: Mercado, log, corte: str):
        self.con, self.m, self.log, self.corte = con, m, log, corte
        self.L = Libro(con, m, log)
        self.cola, self._orden = [], 0
        self.pf = {"ARS": None, "USD": None}
        self.fondo_ars = 0.0
        self.cuota_usd = None
        self.inmueble = None
        self.alquiler = None
        self.hechos = {"tesis": {}, "fechas_movidas": [], "sacadas": [], "inmueble": None, "rebalanceo": None,
                       "ventas_para_inmueble": []}
        self.fechas_ops: list[str] = []

    # cola: (fecha, prioridad, orden, accion, datos). Prioridad dentro del dia:
    # 0 ingresos, 1 vencimientos y cobros, 2 fecha ex, 3 operaciones, 4 gastos.
    def agendar(self, fecha, prioridad, accion, datos=None):
        if fecha > self.corte:
            return
        self._orden += 1
        heapq.heappush(self.cola, (fecha, prioridad, self._orden, accion, datos or {}))

    def construir(self):
        self.L.preparar()
        m = self.m
        self.agendar(m.dia_habil(cron.INICIO), 0, "aporte_inicial")
        for a, mes in _meses(cron.INICIO, self.corte):
            self.agendar(m.dia_habil(f"{a}-{mes:02d}-{cron.DIA_SUELDO:02d}"), 0, "sueldo", {"anio": a, "mes": mes})
        for fecha, accion, datos in cron.OPERACIONES:
            try:
                real = m.dia_habil(fecha, datos.get("ticker"))
            except SinDato as e:
                self.hechos["sacadas"].append((fecha, accion, datos, str(e)))
                self.log(f"  SACADA {fecha} {accion} {datos}: {e}")
                continue
            if real != fecha:
                self.hechos["fechas_movidas"].append((fecha, real, accion, datos.get("ticker")))
            self.fechas_ops.append(real)
            self.agendar(real, 3, accion, datos)
        self._agendar_cobros()
        while self.cola:
            fecha, _, _, accion, datos = heapq.heappop(self.cola)
            getattr(self, f"_{accion}")(fecha, **datos)
        return self.L, self.hechos

    # --- cobros conocidos de antemano (se pagan solo si hay tenencia)
    def _agendar_cobros(self):
        import yfinance as yf
        m = self.m
        ratios = dict(self.con.execute("SELECT ticker_byma, ratio FROM ratios_cedear;").fetchall())
        for t, tipo in m.tipo.items():
            if tipo == "cedear":
                if not ratios.get(t):
                    self.log(f"  ! {t} sin ratio en ratios_cedear: sus dividendos no se cobran")
                    continue
                serie = yf.Ticker(t).dividends
                datos = {"broker": "Broker A", "cuenta": ("Broker A", "USD", "disponible"),
                         "factor": demo_inversor.FACTOR_DIVIDENDO_CEDEAR / ratios[t], "dias": DIAS_PAGO_DIVIDENDO_CEDEAR}
            elif tipo == "accion":
                serie = yf.Ticker(f"{t}.BA").dividends
                datos = {"broker": "Broker B", "cuenta": ("Broker B", "ARS", "disponible"),
                         "factor": demo_inversor.FACTOR_DIVIDENDO_LOCAL, "dias": DIAS_PAGO_DIVIDENDO_LOCAL}
            else:
                continue
            for ts, dps in serie.items():
                ex = ts.date().isoformat()
                if cron.INICIO <= ex <= self.corte:
                    self.agendar(ex, 2, "dividendo_ex", {"ticker": t, "dps": float(dps), **datos})
        for t, tipo in m.tipo.items():
            if tipo in VN100:
                for fecha, cupon, principal in m.flujos(t):
                    if fecha >= cron.INICIO:
                        self.agendar(m.dia_habil(fecha), 1, "pago_bono", {"ticker": t, "cupon": cupon, "principal": principal})

    def _dividendo_ex(self, fecha, ticker, dps, broker, cuenta, factor, dias):
        cantidad = self.L.tenencia[(broker, ticker)]
        if cantidad <= 0:
            return
        monto = round(dps * cantidad * factor, 2)
        if monto >= 0.01:
            pago = _s(_d(fecha) + timedelta(days=dias))
            if pago > self.corte:   # se acredita despues del corte: no entra en la demo
                return
            pago = self.m.dia_habil(pago)
            self.agendar(pago, 1, "cobro", {"cuenta": cuenta, "monto": monto, "desc": f"Dividendo {ticker}",
                                            "ticker": ticker, "broker": broker})

    def _cobro(self, fecha, cuenta, monto, desc, ticker=None, broker=None):
        self.L.renta(fecha, tuple(cuenta), monto, desc)
        if ticker:
            self.L.cobrado_usd[(broker, ticker)] += monto if cuenta[1] == "USD" else monto / self.m.mep(fecha)

    def _pago_bono(self, fecha, ticker, cupon, principal):
        L = self.L
        for (broker, t), vn in list(L.tenencia.items()):
            if t != ticker or vn <= 0:
                continue
            if self.m.tipo[t] == "lecap":
                total = round(vn * (cupon + principal) / 100, 2)
                L.renta(fecha, (broker, "ARS", "disponible"), total, f"Amortización {t} (vencimiento)", cierra=(broker, t, vn),
                        precio_cierre=cupon + principal)
                L.cobrado_usd[(broker, t)] += total / self.m.mep(fecha)
                self._cerrar_tesis_lecap(fecha, t, broker, total)
                continue
            for parte, desc in ((cupon, f"Renta {t}"), (principal, f"Amortización {t}")):
                monto = round(vn * parte / 100, 2)
                if monto >= 0.01:
                    L.renta(fecha, (broker, "USD", "disponible"), monto, desc)
                    L.cobrado_usd[(broker, t)] += monto

    # --- sueldo, gastos y ahorro
    def _aporte_inicial(self, fecha):
        import tesis_cartera
        self.L.ingreso(fecha, ("Banco Demo", "USD", "disponible"), cron.APORTE_INICIAL_USD, "Aporte externo: ahorros previos")
        self.L.mes[fecha[:7]]["aporte_usd"] += cron.APORTE_INICIAL_USD   # se pasa a pesos con el MEP de la foto del cierre
        texto, riesgos, conv = self._escribir("tesis cartera v1", fecha, cron.tesis_cartera_v1, self.m, fecha)
        tid = tesis_cartera.crear_tesis(self.con, fecha=fecha, tesis=texto, riesgos=riesgos, conviccion=conv)
        self.hechos["tesis"]["cartera_v1"] = tid
        self.agendar(self.m.dia_habil("2026-01-05"), 3, "tesis_cartera_v2")

    def _tesis_cartera_v2(self, fecha):
        import tesis_cartera
        tesis_cartera.actualizar_tesis(self.con, self.hechos["tesis"]["cartera_v1"], estado="cerrada",
                                       reflexion_final=self._escribir("reflexion cartera v1", fecha, cron.reflexion_cartera_v1, self.m, fecha), fecha_reflexion=fecha)
        texto, riesgos, conv = self._escribir("tesis cartera v2", fecha, cron.tesis_cartera_v2, self.m, fecha)
        self.hechos["tesis"]["cartera_v2"] = tesis_cartera.crear_tesis(self.con, fecha=fecha, tesis=texto,
                                                                       riesgos=riesgos, conviccion=conv)

    def _sueldo(self, fecha, anio, mes):
        L, m = self.L, self.m
        mep = m.mep(fecha)
        sueldo = cron.SUELDO_USD[anio] * mep
        ahorro = cron.AHORRO_USD[anio] * mep
        gastos = sueldo - ahorro
        banco = ("Banco Demo", "ARS", "disponible")
        L.ingreso(fecha, banco, sueldo, "Sueldo", "Sueldo")
        billetera = gastos * cron.PARTE_GASTOS_BILLETERA
        L.transferir(fecha, banco, ("Billetera Demo", "ARS", "disponible"), billetera,
                     "Transferencia a Billetera Demo (cuenta propia)")
        for dia, parte, desc in ((6, .30, "Supermercado"), (13, .25, "Compras del hogar"), (20, .25, "Salidas"),
                                 (27, .20, "Supermercado")):
            f = max(fecha, f"{anio}-{mes:02d}-{dia:02d}")
            self.agendar(f, 4, "gasto", {"cuenta": ("Billetera Demo", "ARS", "disponible"), "monto": billetera * parte,
                                         "desc": desc, "categoria": "Gastos del hogar"})
        self.agendar(max(fecha, f"{anio}-{mes:02d}-12"), 4, "gasto", {"cuenta": banco, "monto": gastos - billetera,
                                                                       "desc": "Servicios (luz, gas, internet, celular)",
                                                                       "categoria": "Servicios"})
        L.mes[f"{anio}-{mes:02d}"]["trabajo_ars"] += sueldo
        L.mes[f"{anio}-{mes:02d}"]["gastos_ars"] += gastos
        if fecha <= cron.AHORRO_EN_PESOS_HASTA:
            self.fondo_ars += ahorro
            if self.pf["ARS"] is None:
                self._constituir_pf(fecha, "ARS", self.fondo_ars)
                self.fondo_ars = 0.0
        else:
            L.convertir(fecha, "Banco Demo", "ARS", "USD", ahorro, "Compra de dólar MEP (ahorro del mes)")
            if self.pf["USD"] is None:
                self._quizas_pf_usd(fecha)

    def _gasto(self, fecha, cuenta, monto, desc, categoria):
        if tuple(cuenta) == ("Banco Demo", "ARS", "disponible"):  # si no alcanzan los pesos, vende dolares MEP
            self.L.asegurar_ars(fecha, "Banco Demo", monto)
        self.L.egreso(fecha, tuple(cuenta), monto, desc, categoria)
        if categoria is None:
            self.L.mes[fecha[:7]]["gastos_ars"] += monto if cuenta[1] == "ARS" else monto * self.m.mep(fecha)

    # --- plazos fijos (tasa real del BCRA del dia de constitucion)
    def _constituir_pf(self, fecha, moneda, capital):
        capital = round(capital, 2)
        if capital < (100 if moneda == "USD" else 10_000):
            return
        clave = "tasa_pf_30d" if moneda == "ARS" else "tasa_pf_usd"
        tna = self.m.macro(clave, fecha)
        if tna is None:
            self.log(f"  ! sin tasa {clave} al {fecha}: no se constituye el plazo fijo")
            return
        vence = _s(_d(fecha) + timedelta(days=30))
        if vence <= self.corte:   # despues del corte no hay calendario: queda abierto en la foto final
            vence = self.m.dia_habil(vence)
        dias = (_d(vence) - _d(fecha)).days
        self.L.transferir(fecha, ("Banco Demo", moneda, "disponible"), ("Banco Demo", moneda, "plazo_fijo"), capital,
                          f"Plazo fijo {dias} días, TNA {tna:.2f} %")
        self.pf[moneda] = {"capital": capital, "interes": round(capital * tna / 100 * dias / 365, 2)}
        self.agendar(vence, 1, "vence_pf", {"moneda": moneda})

    def _vence_pf(self, fecha, moneda):
        pf, self.pf[moneda] = self.pf[moneda], None
        L = self.L
        L.transferir(fecha, ("Banco Demo", moneda, "plazo_fijo"), ("Banco Demo", moneda, "disponible"), pf["capital"],
                     "Vencimiento plazo fijo (capital)")
        if pf["interes"] >= 0.01:
            L.renta(fecha, ("Banco Demo", moneda, "disponible"), pf["interes"], "Interés plazo fijo")
        if moneda == "USD":
            self._quizas_pf_usd(fecha)
        elif fecha <= cron.AHORRO_EN_PESOS_HASTA:
            self._constituir_pf(fecha, "ARS", pf["capital"] + pf["interes"] + self.fondo_ars)
            self.fondo_ars = 0.0
        else:
            total = pf["capital"] + pf["interes"] + self.fondo_ars
            self.fondo_ars = 0.0
            L.convertir(fecha, "Banco Demo", "ARS", "USD", total, "Compra de dólar MEP (fin del plazo fijo en pesos)")
            self._quizas_pf_usd(fecha)

    def _quizas_pf_usd(self, fecha):
        """Los dolares del banco van a plazo fijo, salvo que haya una operacion en
        los 35 dias siguientes (la plata queda a la vista para usarla)."""
        limite = _s(_d(fecha) + timedelta(days=35))
        if any(fecha < f <= limite for f in self.fechas_ops):
            return
        reserva = 3 * (self.cuota_usd or 0) + 200
        self._constituir_pf(fecha, "USD", self.L.saldo[("Banco Demo", "USD", "disponible")] - reserva)

    # --- operaciones
    def _transferir_usd(self, fecha, desde, hacia, usd):
        self.L.transferir(fecha, (desde, "USD", "disponible"), (hacia, "USD", "disponible"), usd,
                          f"Transferencia a {hacia} (cuenta propia)")

    def _comprar(self, fecha, broker, ticker, usd):
        cantidad, precio, monto = self.L.comprar(fecha, broker, ticker, usd)
        self.log(f"  {fecha} compra {ticker} {cantidad:g} @ {precio:g} (ARS {monto:,.0f}, ~USD {monto / self.m.mep(fecha):,.0f})")
        self._tesis_compra(fecha, ticker)

    def _vender(self, fecha, broker, ticker, parte):
        cantidad = self.L.tenencia[(broker, ticker)] * parte
        if self.m.tipo[ticker] not in VN100:
            cantidad = int(cantidad)
        monto = self.L.vender(fecha, broker, ticker, cantidad)
        self.log(f"  {fecha} venta {ticker} {cantidad:g} (ARS {monto:,.0f})")
        if parte >= 1.0:
            self._tesis_venta(fecha, broker, ticker, monto / self.m.mep(fecha))

    def _rebalancear_rv(self, fecha, broker, vender_parte):
        m, L = self.m, self.L
        valores = {t: L.tenencia[(broker, t)] * m.precio_ultimo(t, fecha) / m.ccl(fecha)
                   for (b, t) in list(L.tenencia) if b == broker and m.tipo[t] == "cedear" and L.tenencia[(b, t)] > 0}
        total = sum(valores.values())
        pesado = max(valores, key=valores.get)
        if m.precio(pesado, fecha) is None:
            fecha = m.dia_habil(fecha, pesado)
        self.hechos["rebalanceo"] = {"fecha": fecha, "ticker": pesado, "peso_pct": round(valores[pesado] / total * 100, 1)}
        self.log(f"  {fecha} rebalanceo: {pesado} pesa {valores[pesado] / total:.0%} de la RV, se vende el {vender_parte:.0%}")
        self._vender(fecha, broker, pesado, vender_parte)

    # --- tesis de cada operacion (texto con el dato real del dia)
    def _escribir(self, nombre, fecha, funcion, *args):
        """Escribe una tesis o reflexion registrando la fecha de cada dato que usa:
        demo_validar falla si alguno es posterior a `fecha`."""
        self.m.auditar()
        try:
            resultado = funcion(*args)
        finally:
            datos = self.m.fin_auditoria()
        self.hechos.setdefault("auditoria_tesis", []).append(
            {"texto": nombre, "fecha": fecha, "datos": sorted(set(datos), key=lambda x: x[1])})
        return resultado

    def _tesis_compra(self, fecha, ticker):
        import tesis_acciones
        import tesis_bonos
        import tesis_umbrales
        t = self.hechos["tesis"]
        if ticker in t:
            return
        m = self.m
        if ticker in ("AL30", "GD30", "GD35"):
            texto, riesgos, conv = self._escribir(f"tesis {ticker}", fecha, getattr(cron, f"tesis_{ticker.lower()}"), m, fecha)
            t[ticker] = tesis_bonos.crear_tesis(self.con, ticker=ticker, fecha=fecha, tesis=texto, riesgos=riesgos, conviccion=conv)
            if ticker == "GD35":
                tesis_umbrales.crear_umbral(self.con, tesis_tabla="bonos", tesis_id=t[ticker], indicador="riesgo_pais",
                                            operador="menor_que", valor=500, accion="Evaluar tomar ganancia en el GD35")
        elif m.tipo[ticker] == "lecap":
            texto, riesgos, conv = self._escribir(f"tesis {ticker}", fecha, cron.tesis_lecap, m, fecha, ticker)
            t[ticker] = tesis_bonos.crear_tesis(self.con, ticker=ticker, fecha=fecha, tesis=texto, riesgos=riesgos, conviccion=conv)
            t[f"{ticker}_inicio"] = fecha
            tesis_umbrales.crear_umbral(self.con, tesis_tabla="bonos", tesis_id=t[ticker], indicador="inflacion_mensual",
                                        operador="mayor_que", valor=3.0, accion=f"Revisar la tasa fija de {ticker}")
        elif m.tipo[ticker] == "cedear":
            texto, riesgos, conv = self._escribir(f"tesis {ticker}", fecha, cron.tesis_rv, m, fecha, ticker)
            t[ticker] = tesis_acciones.crear_tesis(self.con, ticker=ticker, fecha=fecha, tesis=texto, riesgos=riesgos, conviccion=conv)
        elif m.tipo[ticker] == "accion" and "acciones_ar" not in t:
            texto, riesgos, conv = self._escribir("tesis acciones argentinas", fecha, cron.tesis_acciones_ar, m, fecha)
            t["acciones_ar"] = tesis_acciones.crear_tesis(self.con, ticker=None, fecha=fecha, tesis=texto, riesgos=riesgos, conviccion=conv)

    def _tesis_venta(self, fecha, broker, ticker, ingreso_usd):
        import tesis_bonos
        tid = self.hechos["tesis"].get(ticker)
        if tid is None or ticker not in ("AL30", "GD30"):
            return
        costo = self.L.costo_usd[(broker, ticker)]
        resultado = ((ingreso_usd + self.L.cobrado_usd[(broker, ticker)]) / costo - 1) * 100
        texto = self._escribir(f"reflexion {ticker}", fecha, getattr(cron, f"reflexion_{ticker.lower()}"), self.m, fecha, resultado)
        tesis_bonos.actualizar_tesis(self.con, tid, estado="cerrada", reflexion_final=texto, fecha_reflexion=fecha)

    def _cerrar_tesis_lecap(self, fecha, ticker, broker, total_ars):
        import tesis_bonos
        t = self.hechos["tesis"]
        if ticker not in t:
            return
        inicio = t[f"{ticker}_inicio"]
        costo_ars = self.L.costo_usd[(broker, ticker)] * self.m.mep(inicio)
        rend = (total_ars / costo_ars - 1) * 100
        infl = 1.0
        for a, mes in _meses(inicio, _mes_anterior(fecha)):
            v = self.m.macro("inflacion_mensual", f"{a}-{mes:02d}-31")
            infl *= 1 + (v or 0) / 100
        texto = self._escribir(f"reflexion {ticker}", fecha, cron.reflexion_lecap, self.m, fecha, ticker, (infl - 1) * 100, rend)
        tesis_bonos.actualizar_tesis(self.con, t[ticker], estado="cerrada", reflexion_final=texto, fecha_reflexion=fecha)

    # --- inmueble
    def _comprar_inmueble(self, fecha):
        import pasivos
        import propiedades
        import tesis_inmuebles
        import tesis_umbrales
        import valuaciones
        m, L, cfg = self.m, self.L, cron.INMUEBLE
        clave = "zpindex_caba_venta_usd_m2"
        f_tesis = m.dia_habil("2025-10-15")
        texto, riesgos, conv = self._escribir("tesis inmueble", f_tesis, cron.tesis_inmueble, _MercadoPublicado(m), f_tesis)
        tid = tesis_inmuebles.crear_tesis(self.con, ticker=cfg["alias"], fecha=f_tesis, tesis=texto, riesgos=riesgos,
                                          conviccion=conv, contenedor_cobro="Banco Demo")
        tesis_umbrales.crear_umbral(self.con, tesis_tabla="inmuebles", tesis_id=tid, indicador=clave, operador="menor_que",
                                    valor=2300, accion="Revisar la valuación del departamento")
        zp = m.macro(clave, _mes_anterior(fecha))
        precio = round(zp * cfg["m2"], 2)
        propio = round(precio * (1 + cfg["gastos_escritura_pct"] / 100) - cfg["credito_usd"], 2)
        banco = ("Banco Demo", "USD", "disponible")
        try:
            L.asegurar_usd(fecha, "Banco Demo", propio)
        except ValueError:
            self._vender_para(fecha, propio)
            L.asegurar_usd(fecha, "Banco Demo", propio)
        L.egreso(fecha, banco, propio, f"Escritura {cfg['alias']}: fondos propios (precio + gastos - crédito)",
                 "Compra de inmueble")
        L.mes[fecha[:7]]["retiro_inmueble_usd"] += propio
        pid = propiedades.crear_propiedad(self.con, alias=cfg["alias"], tipo="activo_fisico", pct_propiedad=100,
                                          fecha_alta=fecha, notas="Inversor demo: 2 ambientes en CABA para alquilar.")
        propiedades.actualizar_inversion_inicial(self.con, pid, propio)
        pasivo = pasivos.crear_pasivo(self.con, alias=cfg["credito_alias"], monto_original_usd=cfg["credito_usd"],
                                      fecha_alta=fecha, propiedad_id=pid)
        valuaciones.crear_valuacion(self.con, propiedad_id=pid, fecha=fecha, valor_usd=precio,
                                    fuente=f"Precio de compra: {cfg['m2']} m² × zpindex CABA de {_mes_anterior(fecha)[:7]}")
        self.inmueble = {"id": pid, "pasivo": pasivo, "precio": precio, "propio": propio, "fecha": fecha, "zp": zp}
        self.hechos["inmueble"] = self.inmueble
        self.cuota_usd = round(cfg["credito_usd"] / cfg["credito_cuotas"], 2)
        self.log(f"  {fecha} compra del inmueble: USD {precio:,.0f} ({cfg['m2']} m² × {zp:,.0f}), "
                 f"fondos propios USD {propio:,.0f}, crédito USD {cfg['credito_usd']:,.0f}")
        # valuaciones mensuales con el zpindex publicado, alquiler, cuota e impuestos
        for a, mes in _meses(_sumar_meses(fecha, 1), self.corte):
            self.agendar(m.dia_habil(f"{a}-{mes:02d}-01"), 1, "valuar_inmueble")
        for a, mes in _meses(cfg["alquiler_desde"], self.corte):
            dia = f"{a}-{mes:02d}-{cfg['dia_cobro']:02d}"
            self.agendar(m.dia_habil(dia), 0, "cobro_alquiler", {"periodo": f"{a}-{mes:02d}"})
            self.agendar(m.dia_habil(dia), 4, "cuota_credito")
            if mes in (3, 9):
                self.agendar(m.dia_habil(f"{a}-{mes:02d}-15"), 4, "abl")
        for f, usd, concepto in cfg["mantenimiento"]:
            self.agendar(m.dia_habil(f), 4, "mantenimiento", {"usd": usd, "concepto": concepto})

    def _vender_para(self, fecha, usd_necesarios):
        """Si no alcanzan los dolares para la escritura, vende el mismo porcentaje
        de cada CEDEAR del Broker A hasta cubrir, y lo anota."""
        m, L = self.m, self.L
        disponible = sum(L.saldo[(c, "USD", "disponible")] for c in ("Banco Demo", "Broker A", "Broker B"))
        valores = {t: q * m.precio(t, fecha) / m.ccl(fecha) for (b, t), q in L.tenencia.items()
                   if b == "Broker A" and q > 0 and m.tipo[t] == "cedear" and m.precio(t, fecha)}
        total = sum(valores.values())
        falta = usd_necesarios - disponible
        if falta > total:
            raise ValueError("no alcanza para la escritura ni vendiendo la renta variable")
        parte = min(1.0, falta * 1.03 / total)
        for t in sorted(valores):
            cantidad = max(1, int(L.tenencia[("Broker A", t)] * parte + 0.999))
            L.vender(fecha, "Broker A", t, cantidad)
            self.hechos["ventas_para_inmueble"].append((fecha, t, cantidad))
        self.log(f"  {fecha} venta del {parte:.0%} de cada CEDEAR para completar la escritura")

    def _valuar_inmueble(self, fecha):
        import valuaciones
        clave = "zpindex_caba_venta_usd_m2"
        mes_dato = _mes_anterior(fecha)
        fila = self.con.execute("SELECT valor FROM macro_historico WHERE clave = ? AND fecha = ?;", (clave, mes_dato)).fetchone()
        if fila is None:
            self.log(f"  {fecha}: sin zpindex de {mes_dato[:7]}, ese mes no se revalua (no se inventa)")
            return
        valuaciones.crear_valuacion(self.con, propiedad_id=self.inmueble["id"], fecha=fecha,
                                    valor_usd=round(fila[0] * cron.INMUEBLE["m2"], 2),
                                    fuente=f"{cron.INMUEBLE['m2']} m² × zpindex CABA de {mes_dato[:7]}")

    def _cobro_alquiler(self, fecha, periodo):
        import alquileres
        cfg = cron.INMUEBLE
        if self.alquiler is None:
            self.alquiler = {"monto": round(_ALQUILER_2AMB_50M2["monto"] * cfg["m2"] / 50, -2), "desde": periodo}
        elif (int(periodo[:4]) * 12 + int(periodo[5:])) - (int(self.alquiler["desde"][:4]) * 12 + int(self.alquiler["desde"][5:])) >= cfg["ajuste_meses"]:
            factor = 1.0
            for k in range(cfg["ajuste_meses"]):   # inflacion real de los 3 meses ya publicados
                f_mes = _sumar_meses(f"{periodo}-01", -(k + 2))
                v = self.m.macro("inflacion_mensual", f_mes[:7] + "-31")
                factor *= 1 + (v or 0) / 100
            self.alquiler = {"monto": round(self.alquiler["monto"] * factor, -2), "desde": periodo}
        monto = self.alquiler["monto"]
        banco = ("Banco Demo", "ARS", "disponible")
        self.L.ingreso(fecha, banco, monto, f"Transferencia inquilino - alquiler {periodo}", "Alquileres")
        self.L.mes[fecha[:7]]["trabajo_ars"] += monto
        alquileres.crear_alquiler(self.con, propiedad_id=self.inmueble["id"], periodo=periodo, monto=monto,
                                  moneda="ARS", mep=self.m.mep(fecha), fecha_cobro=fecha)
        self.L.convertir(fecha, "Banco Demo", "ARS", "USD", monto, "Compra de dólar MEP (alquiler)")

    def _cuota_credito(self, fecha):
        import pasivos
        self.L.asegurar_usd(fecha, "Banco Demo", self.cuota_usd)
        self.L.egreso(fecha, ("Banco Demo", "USD", "disponible"), self.cuota_usd,
                      f"Cuota {cron.INMUEBLE['credito_alias']}", "Crédito hipotecario")
        pasivos.crear_pago_pasivo(self.con, pasivo_id=self.inmueble["pasivo"], fecha=fecha, monto_usd=self.cuota_usd)

    def _abl(self, fecha):
        import gastos_propiedad
        usd = cron.INMUEBLE["abl_usd_anual"] / 2
        ars = round(usd * self.m.mep(fecha), 2)
        self.L.asegurar_ars(fecha, "Banco Demo", ars)
        self.L.egreso(fecha, ("Banco Demo", "ARS", "disponible"), ars, f"ABL {cron.INMUEBLE['alias']}", "Servicios")
        self.L.mes[fecha[:7]]["gastos_ars"] += ars
        gastos_propiedad.crear_gasto_propiedad(self.con, propiedad_id=self.inmueble["id"], fecha=fecha, tipo="impuesto",
                                               monto_usd=usd, concepto="ABL (semestral)")

    def _mantenimiento(self, fecha, usd, concepto):
        import gastos_propiedad
        ars = round(usd * self.m.mep(fecha), 2)
        self.L.asegurar_ars(fecha, "Banco Demo", ars)
        self.L.egreso(fecha, ("Banco Demo", "ARS", "disponible"), ars, f"{concepto} ({cron.INMUEBLE['alias']})", "Gastos del hogar")
        self.L.mes[fecha[:7]]["gastos_ars"] += ars
        gastos_propiedad.crear_gasto_propiedad(self.con, propiedad_id=self.inmueble["id"], fecha=fecha, tipo="mantenimiento",
                                               monto_usd=usd, concepto=concepto)


class _MercadoPublicado:
    """Para la tesis del inmueble: el zpindex del mes M recien se conoce al final
    de M, asi que el dato "de hoy" es el del mes anterior."""

    def __init__(self, m):
        self._m = m

    def macro(self, clave, f):
        if clave == "zpindex_caba_venta_usd_m2":
            return self._m.macro(clave, _mes_anterior(f))
        return self._m.macro(clave, f)

    def __getattr__(self, nombre):
        return getattr(self._m, nombre)


# Alquiler de referencia de Zonaprop (2 ambientes, 50 m2), leido del informe
# real con generar_demo.alquiler_de_referencia() antes de construir la historia.
_ALQUILER_2AMB_50M2 = {"monto": None}
