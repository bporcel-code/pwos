"""PWOS -- la historia del inversor demo, movimiento por movimiento (paso 7b).

`Mercado` lee el dato REAL de cada fecha (MEP/CCL y series de la base de la
demo, precios y cronogramas de las mismas funciones que despues cargan los
backfills del motor). `Libro` escribe movimientos y operaciones con la misma
forma que los importadores (no hay otra funcion que los escriba) y frena si un
saldo queda negativo. `construir()` recorre la linea de tiempo en orden.
"""
import math
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta

import demo_cronologia as cron

VN100 = {"bono_soberano", "bono_provincial", "bono_cer", "on", "lecap"}
FACTOR_DIVIDENDO_CEDEAR = 0.64   # neto de la retencion de EE.UU. y gastos (flujo_estimado.FACTOR_NETO_CEDEAR_EEUU)
FACTOR_DIVIDENDO_LOCAL = 0.93    # flujo_estimado.FACTOR_NETO_ACCION_LOCAL
DIAS_PAGO_DIVIDENDO_CEDEAR = 21  # ex-date -> acreditacion del CEDEAR (aproximado)
DIAS_PAGO_DIVIDENDO_LOCAL = 3


def _d(f: str) -> date:
    return date.fromisoformat(f)


def _s(d: date) -> str:
    return d.isoformat()


class SinDato(Exception):
    pass


class Mercado:
    def __init__(self, con, corte: str, log):
        import backfill_precios_rava
        import backfill_precios_yfinance
        import flujo_estimado
        import rava_client
        self.con, self.corte, self.log = con, corte, log
        global FACTOR_DIVIDENDO_CEDEAR, FACTOR_DIVIDENDO_LOCAL
        FACTOR_DIVIDENDO_CEDEAR = flujo_estimado.FACTOR_NETO_CEDEAR_EEUU
        FACTOR_DIVIDENDO_LOCAL = flujo_estimado.FACTOR_NETO_ACCION_LOCAL
        self.tipo = {t: tipo for t, tipo, _, _ in cron.ACTIVOS}
        self._fx = {}
        for t in ("USD_MEP", "USD_CCL"):
            filas = con.execute("SELECT p.fecha, p.precio FROM precios p JOIN activos a ON a.id = p.activo_id "
                                "WHERE a.ticker = ? AND p.fecha <= ? ORDER BY p.fecha;", (t, corte)).fetchall()
            self._fx[t] = (list(f for f, _ in filas), list(v for _, v in filas))
        self.dias = self._fx["USD_MEP"][0]
        self._precios = {}
        for t, fuente in cron.FUENTE_PRECIO.items():
            datos = (backfill_precios_rava.descargar_historico(t) if fuente == "rava"
                     else backfill_precios_yfinance.descargar_historico(t, "2023-12-01")) or []
            self._precios[t] = {d["fecha"]: d["precio"] for d in datos if d["fecha"] <= corte and d["precio"]}
        self._flujos = {}
        for t, tipo in self.tipo.items():
            if tipo in VN100:
                datos = rava_client.obtener_flujo_bono(t)
                self._flujos[t] = [(f["fecha_pago"][:10], float(f["cupon"]), float(f["principal"]))
                                   for f in (datos or {}).get("flujos", []) if f["fecha_pago"][:10] <= corte]
        self._macro_cache = {}
        self.registro = None   # mientras se escribe una tesis: fechas de los datos que se usaron

    # --- dolar y precios
    def _ultimo(self, serie, f, nombre=""):
        fechas, valores = serie
        i = bisect_right(fechas, f)
        if i and self.registro is not None:
            self.registro.append((nombre, fechas[i - 1]))
        return valores[i - 1] if i else None

    def auditar(self):
        """Empieza a registrar la fecha de cada dato entregado (para la validacion
        'ninguna tesis cita un dato posterior a su fecha')."""
        self.registro = []

    def fin_auditoria(self):
        r, self.registro = self.registro, None
        return r

    def mep(self, f):
        return self._ultimo(self._fx["USD_MEP"], f, "USD_MEP")

    def ccl(self, f):
        return self._ultimo(self._fx["USD_CCL"], f, "USD_CCL")

    def precio(self, t, f):
        """Cierre real de ese dia exacto (None si no opero)."""
        return self._precios.get(t, {}).get(f)

    def precio_ultimo(self, t, f):
        fechas = sorted(x for x in self._precios.get(t, {}) if x <= f)
        if fechas and self.registro is not None:
            self.registro.append((t, fechas[-1]))
        return self._precios[t][fechas[-1]] if fechas else None

    def precio_usd(self, t, f):
        p = self.precio_ultimo(t, f)
        if p is None:
            return None
        if self.tipo[t] in VN100:
            return p / self.mep(f)
        return p / (self.ccl(f) if self.tipo[t] == "cedear" else self.mep(f))

    def dia_habil(self, f, ticker=None):
        """Primer dia >= f con MEP (y con precio del ticker, si se pide)."""
        i = bisect_right(self.dias, f) - (1 if f in self.dias else 0)
        for d in self.dias[max(i, 0):]:
            if d >= f and (ticker is None or self.precio(ticker, d) is not None):
                return d
        raise SinDato(f"sin dia habil con datos desde {f}" + (f" para {ticker}" if ticker else ""))

    def macro(self, clave, f):
        if clave not in self._macro_cache:
            filas = self.con.execute("SELECT fecha, valor FROM macro_historico WHERE clave = ? AND fecha <= ? "
                                     "ORDER BY fecha;", (clave, self.corte)).fetchall()
            self._macro_cache[clave] = ([a for a, _ in filas], [b for _, b in filas])
        return self._ultimo(self._macro_cache[clave], f, clave)

    def flujos(self, t):
        return self._flujos.get(t, [])


class Libro:
    """Movimientos y operaciones del inversor, con saldo y tenencia en memoria."""

    def __init__(self, con, m: Mercado, log):
        self.con, self.m, self.log = con, m, log
        self.cuenta, self.saldo = {}, defaultdict(float)
        self.tenencia = defaultdict(float)
        self.costo_usd = defaultdict(float)   # para las reflexiones: USD invertidos por (broker, ticker)
        self.cobrado_usd = defaultdict(float)
        self.activo = dict(con.execute("SELECT ticker, id FROM activos;").fetchall())
        self.categoria = {}
        self.mes = defaultdict(lambda: {"trabajo_ars": 0.0, "gastos_ars": 0.0, "retiro_inmueble_usd": 0.0, "aporte_usd": 0.0})
        self._n = 0

    def preparar(self):
        for nombre, padre, tipo in cron.CATEGORIAS_EXTRA:
            self.con.execute("INSERT INTO categorias (nombre, categoria_padre_id, tipo) VALUES (?, NULL, ?);", (nombre, tipo))
        self.categoria = dict(self.con.execute("SELECT nombre, id FROM categorias;").fetchall())
        for nombre, tipo, cuentas in cron.CONTENEDORES:
            cid = self.con.execute("INSERT INTO contenedores (nombre, tipo) VALUES (?, ?);", (nombre, tipo)).lastrowid
            for moneda, estado in cuentas:
                self.cuenta[(nombre, moneda, estado)] = self.con.execute(
                    "INSERT INTO cuentas (contenedor_id, moneda, estado, alias) VALUES (?, ?, ?, ?);",
                    (cid, moneda, estado, f"{nombre} {moneda} {estado}")).lastrowid
        self.contenedor = dict(self.con.execute("SELECT nombre, id FROM contenedores;").fetchall())

    # --- primitivas
    def _mov(self, fecha, tipo, origen, destino, monto, moneda, desc, categoria=None, naturaleza="efectivo"):
        monto = round(monto, 2)
        if monto <= 0:
            raise ValueError(f"monto no positivo: {fecha} {desc} {monto}")
        if origen is not None:
            self.saldo[origen] -= monto
            if self.saldo[origen] < -0.005:
                raise ValueError(f"saldo negativo en {origen} el {fecha}: {self.saldo[origen]:.2f} ({desc})")
        if destino is not None:
            self.saldo[destino] += monto
        cat = self.categoria[categoria] if categoria else None
        self._n += 1
        return self.con.execute(
            "INSERT INTO movimientos (fecha, tipo, cuenta_origen_id, cuenta_destino_id, monto, moneda, categoria_id, "
            "descripcion, naturaleza, identificador_externo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
            (fecha, tipo, self.cuenta.get(origen), self.cuenta.get(destino), monto, moneda, cat,
             desc, naturaleza, f"DEMO:{self._n:06d}")).lastrowid

    def ingreso(self, fecha, cuenta, monto, desc, categoria=None):
        return self._mov(fecha, "ingreso", None, cuenta, monto, cuenta[1], desc, categoria)

    def egreso(self, fecha, cuenta, monto, desc, categoria=None):
        return self._mov(fecha, "egreso", cuenta, None, monto, cuenta[1], desc, categoria)

    def transferir(self, fecha, origen, destino, monto, desc):
        assert origen[1] == destino[1], (origen, destino)
        return self._mov(fecha, "transferencia", origen, destino, monto, origen[1], desc)

    def convertir(self, fecha, contenedor, de, a, monto_de, desc):
        """Compra/venta de dolar MEP en la misma institucion: 2 transferencias
        (sale una moneda, entra la otra), como los importadores."""
        mep = self.m.mep(fecha)
        monto_a = monto_de / mep if a == "USD" else monto_de * mep
        self._mov(fecha, "transferencia", (contenedor, de, "disponible"), None, monto_de, de, f"{desc} (sale {de})")
        self._mov(fecha, "transferencia", None, (contenedor, a, "disponible"), monto_a, a, f"{desc} (entra {a})")
        return round(monto_a, 2)

    # --- fondeo: que haya `ars` pesos en el broker (vende MEP; si faltan dolares, los trae del banco)
    def asegurar_ars(self, fecha, broker, ars):
        falta = ars - self.saldo[(broker, "ARS", "disponible")]
        if falta <= 0.005:
            return
        usd = math.ceil(falta / self.m.mep(fecha) * 100) / 100 + 0.01
        self.asegurar_usd(fecha, broker, usd)
        self.convertir(fecha, broker, "USD", "ARS", usd, "Venta de dólar MEP")

    def asegurar_usd(self, fecha, contenedor, usd):
        falta = usd - self.saldo[(contenedor, "USD", "disponible")]
        if falta <= 0.005:
            return
        falta = math.ceil(falta * 100) / 100
        for origen in ("Banco Demo", "Broker A", "Broker B"):
            if origen == contenedor or falta <= 0.005:
                continue
            disp = self.saldo[(origen, "USD", "disponible")]
            if disp > 0.005:
                monto = min(disp, falta)
                self.transferir(fecha, (origen, "USD", "disponible"), (contenedor, "USD", "disponible"), monto,
                                f"Transferencia a {contenedor} (cuenta propia)")
                falta -= monto
        if falta > 0.005:
            raise ValueError(f"no alcanzan los dólares para {contenedor} el {fecha}: faltan USD {falta:.2f}")

    # --- operaciones
    def _unidad(self, ticker, precio):
        return precio / 100 if self.m.tipo[ticker] in VN100 else precio

    def comprar(self, fecha, broker, ticker, usd):
        precio = self.m.precio(ticker, fecha)
        cantidad = math.floor(usd * self.m.mep(fecha) / self._unidad(ticker, precio))
        if cantidad <= 0:
            raise ValueError(f"cantidad 0 comprando {ticker}")
        monto = round(cantidad * self._unidad(ticker, precio), 2)
        self.asegurar_ars(fecha, broker, monto)
        mid = self._mov(fecha, "compra_activo", (broker, "ARS", "disponible"), None, monto, "ARS",
                        f"Compra {ticker} {cantidad:g} @ {precio:g}")
        self._operacion(mid, ticker, broker, cantidad, precio)
        self.tenencia[(broker, ticker)] += cantidad
        self.costo_usd[(broker, ticker)] += monto / self.m.mep(fecha)
        return cantidad, precio, monto

    def vender(self, fecha, broker, ticker, cantidad, a_dolares=True):
        precio = self.m.precio(ticker, fecha)
        cantidad = min(cantidad, self.tenencia[(broker, ticker)])
        monto = round(cantidad * self._unidad(ticker, precio), 2)
        mid = self._mov(fecha, "venta_activo", None, (broker, "ARS", "disponible"), monto, "ARS",
                        f"Venta {ticker} {cantidad:g} @ {precio:g}")
        self._operacion(mid, ticker, broker, cantidad, precio)
        self.tenencia[(broker, ticker)] -= cantidad
        if a_dolares:
            self.convertir(fecha, broker, "ARS", "USD", monto, "Compra de dólar MEP")
        return monto

    def _operacion(self, movimiento_id, ticker, broker, cantidad, precio, moneda="ARS"):
        self.con.execute("INSERT INTO operaciones_activos (movimiento_id, activo_id, contenedor_id, cantidad, "
                         "precio_unitario, moneda, comision) VALUES (?, ?, ?, ?, ?, ?, 0);",
                         (movimiento_id, self.activo[ticker], self.contenedor[broker], cantidad, precio, moneda))

    def renta(self, fecha, cuenta, monto, desc, cierra=None, precio_cierre=None):
        """Cobro de renta (cupon, amortizacion, dividendo, interes). `cierra` =
        (broker, ticker, cantidad): amortizacion 100 % que cierra la posicion,
        con su fila en operaciones_activos (convencion del motor)."""
        mid = self._mov(fecha, "renta", None, cuenta, monto, cuenta[1], desc)
        if cierra:
            broker, ticker, cantidad = cierra
            self._operacion(mid, ticker, broker, cantidad, precio_cierre)
            self.tenencia[(broker, ticker)] -= cantidad
        return mid
