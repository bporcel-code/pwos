// PWOS -- Frontend del dashboard, rediseño "cascada macro→micro"
// (2026-08-19). Layout de una sola pantalla (sin scroll de pagina):
// panel izquierdo = torta/dona con drill-down (Tipo de activo / Broker),
// panel derecho = flujo de pagos (barras apiladas por mes + tabla plana
// de todos los eventos, filtrable en el cliente al hacer clic en una
// barra). Sin Chart.js / sin canvas / sin CDN, como el dashboard viejo
// -- la dona se arma con SVG nativo (arcos calculados a mano), sin
// libreria de graficos.

// ---- Etiquetas legibles por clave (el backend manda codigos estables,
// no texto ya traducido -- una sola fuente de verdad para las
// etiquetas, mismo criterio que ya usaba el dashboard viejo). ----
const LABELS = {
  total: "Activos totales",
  efectivo: "Efectivo",
  renta_fija: "Renta fija",
  renta_variable: "Renta variable",
  otro: "Otro (sin clasificar)",
  inmuebles: "Inmuebles",
  bono_soberano: "Soberano",
  bono_provincial: "Provincial",
  bono_cer: "CER",
  on: "ON",
  lecap: "LECAP",
  fci: "FCI",
  accion: "Acciones",
  cedear: "CEDEARs",
  interes: "Interés",
  amortizacion: "Amortización",
  otros: "Otros",
  Conservador: "Conservador",
  Moderado: "Moderado",
  Arriesgado: "Arriesgado",
  "No verificable": "No verificable",
};

// Slot de color FIJO por clave conocida (1-8, ver --series-N en
// style.css) -- mismo criterio que el dashboard viejo: el color de una
// entidad no depende de su posicion/ranking. Claves que no estan aca
// (tickers, brokers nuevos) caen en un slot estable calculado por hash
// del propio nombre, nunca por posicion en la lista actual.
const CLAVE_COLOR = {
  efectivo: 1, renta_fija: 2, renta_variable: 3, otro: 8, inmuebles: 4,
  bono_soberano: 1, bono_provincial: 2, bono_cer: 3, on: 4, lecap: 5, fci: 6,
  accion: 7, cedear: 3,
  interes: 1, amortizacion: 2, otros: 3, cupon: 1, dividendo: 5, alquiler: 4,
  // Riesgo: verde/dorado/coral por severidad creciente -- "No verificable"
  // sin slot fijo a proposito, cae al hash generico (bucket chico y raro).
  Conservador: 6, Moderado: 7, Arriesgado: 4,
};

const MESES_CORTOS = [
  "ene", "feb", "mar", "abr", "may", "jun",
  "jul", "ago", "sep", "oct", "nov", "dic",
];
const MESES_VISIBLES_DEFAULT = 12;

function fmtUSD(valor) {
  if (valor === null || valor === undefined) return "—";
  return valor.toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function labelFor(clave) {
  return LABELS[clave] || clave;
}

// Cantidad de nominales/unidades, formato es-AR, sin decimales forzados
// (LECAPs suelen ser millones enteros, CEDEARs/acciones unidades chicas
// -- se dejan hasta 4 decimales solo si el valor los tiene de verdad).
function fmtCantidad(valor) {
  if (valor === null || valor === undefined) return "—";
  return valor.toLocaleString("es-AR", { maximumFractionDigits: 4 });
}

// Monto en moneda nativa (ARS o USD), formato es-AR (punto de miles,
// coma decimal) -- como lo lee el usuario en la pantalla de cada broker.
// null = la cuenta no existe para ese contenedor -> "—".
function fmtNativo(valor) {
  if (valor === null || valor === undefined) return "—";
  return valor.toLocaleString("es-AR", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function colorForClave(clave) {
  let slot = CLAVE_COLOR[clave];
  if (slot === undefined) {
    let hash = 0;
    for (let i = 0; i < clave.length; i++) hash = (hash * 31 + clave.charCodeAt(i)) % 8;
    slot = hash + 1;
  }
  const root = getComputedStyle(document.documentElement);
  return root.getPropertyValue(`--series-${slot}`).trim();
}

function mesEtiqueta(mesISO) {
  const [anio, mesNum] = mesISO.split("-");
  return `${MESES_CORTOS[parseInt(mesNum, 10) - 1]} '${anio.slice(2)}`;
}

// ============================================================
// Panel izquierdo: composicion de cartera (torta con drill-down)
// ============================================================

let composicion = null; // { tipo_activo: nodo, broker: nodo }
let dimActual = "tipo_activo";
let path = []; // claves desde la raiz hasta el nivel actual

function nodoActual() {
  let n = composicion[dimActual];
  for (const clave of path) {
    const siguiente = (n.children || []).find((c) => c.clave === clave);
    if (!siguiente) {
      path = []; // no deberia pasar (datos cambiaron entre cargas), volver a la raiz
      return composicion[dimActual];
    }
    n = siguiente;
  }
  return n;
}

function renderComposicion() {
  const nodo = nodoActual();
  renderBreadcrumb();
  renderDonut(nodo);
  renderLegend(nodo);
}

function renderBreadcrumb() {
  const cont = document.getElementById("breadcrumb");
  const crumbs = [{ idx: 0, label: RAIZ_DONA[dimActual] }, ...path.map((clave, i) => ({ idx: i + 1, label: labelFor(clave) }))];

  cont.innerHTML = crumbs.map((c, i) => {
    const sep = i > 0 ? `<span class="sep">/</span>` : "";
    const esUltimo = i === crumbs.length - 1;
    if (esUltimo) return `${sep}<span class="crumb-actual">${c.label}</span>`;
    return `${sep}<button type="button" data-idx="${c.idx}">${c.label}</button>`;
  }).join(" ");

  cont.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      path = path.slice(0, parseInt(btn.dataset.idx, 10));
      renderComposicion();
    });
  });
}

function polarPoint(cx, cy, r, angle) {
  return [cx + r * Math.sin(angle), cy - r * Math.cos(angle)];
}

// Arco de dona (SVG path) entre dos angulos (radianes, 0 = arriba,
// sentido horario) -- sin libreria, geometria estandar de donut chart.
function arcPath(cx, cy, rOuter, rInner, startAngle, endAngle) {
  const TWO_PI = Math.PI * 2;
  if (endAngle - startAngle >= TWO_PI - 1e-6) endAngle = startAngle + TWO_PI - 0.0001; // caso 100% de 1 sola porcion
  const [x0, y0] = polarPoint(cx, cy, rOuter, startAngle);
  const [x1, y1] = polarPoint(cx, cy, rOuter, endAngle);
  const [x2, y2] = polarPoint(cx, cy, rInner, endAngle);
  const [x3, y3] = polarPoint(cx, cy, rInner, startAngle);
  const large = endAngle - startAngle > Math.PI ? 1 : 0;
  return `M ${x0} ${y0} A ${rOuter} ${rOuter} 0 ${large} 1 ${x1} ${y1} L ${x2} ${y2} A ${rInner} ${rInner} 0 ${large} 0 ${x3} ${y3} Z`;
}

function irANivel(hijo) {
  if (hijo && hijo.children && hijo.children.length) {
    path = [...path, hijo.clave];
    renderComposicion();
  }
}

function renderDonut(nodo) {
  const svg = document.getElementById("donut-svg");
  const hijos = (nodo.children || []).filter((c) => c.valor_usd > 1e-6);

  document.getElementById("donut-center-label").textContent =
    path.length ? labelFor(path[path.length - 1]) : RAIZ_DONA[dimActual];
  const enRaizDeActivos = !path.length && dimActual === "tipo_activo" && resumenActivos;
  document.getElementById("donut-center-valor").textContent =
    fmtUSD(enRaizDeActivos ? resumenActivos.total : nodo.valor_usd);
  pintarNotaDona();

  if (!hijos.length) {
    svg.innerHTML = "";
    return;
  }

  const cx = 120, cy = 120, rOuter = 108, rInner = 60;
  let angulo = 0;
  svg.innerHTML = hijos.map((h) => {
    const frac = nodo.valor_usd ? h.valor_usd / nodo.valor_usd : 0;
    const start = angulo;
    const end = angulo + frac * Math.PI * 2;
    angulo = end;
    const color = colorForClave(h.clave);
    return `<path class="donut-slice" d="${arcPath(cx, cy, rOuter, rInner, start, end)}" fill="${color}" data-clave="${h.clave}"><title>${labelFor(h.clave)}: ${fmtUSD(h.valor_usd)}</title></path>`;
  }).join("");

  svg.querySelectorAll(".donut-slice").forEach((el) => {
    el.addEventListener("click", () => {
      irANivel(hijos.find((h) => h.clave === el.dataset.clave));
    });
  });
}

function renderLegend(nodo) {
  const cont = document.getElementById("donut-legend");
  const hijos = (nodo.children || []).filter((c) => c.valor_usd > 1e-6);

  if (!hijos.length) {
    cont.innerHTML = `<p class="eventos-vacio">Sin datos para este nivel.</p>`;
    return;
  }

  cont.innerHTML = hijos.map((h) => {
    const pct = nodo.valor_usd ? Math.round((h.valor_usd / nodo.valor_usd) * 1000) / 10 : 0;
    const color = colorForClave(h.clave);
    return `
      <button type="button" class="legend-item" data-clave="${h.clave}">
        <span class="legend-nombre"><span class="swatch" style="background:${color}"></span>${labelFor(h.clave)}</span>
        <span class="legend-pct">${pct}%</span>
      </button>`;
  }).join("");

  cont.querySelectorAll(".legend-item").forEach((el) => {
    el.addEventListener("click", () => {
      irANivel(hijos.find((h) => h.clave === el.dataset.clave));
    });
  });
}

document.getElementById("dim-selector").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-dim]");
  if (!btn || btn.classList.contains("activo")) return;
  dimActual = btn.dataset.dim;
  path = [];
  document.querySelectorAll("#dim-selector button").forEach((b) => b.classList.toggle("activo", b === btn));
  renderComposicion();
});

// ============================================================
// Panel derecho: flujo de pagos (barras por mes + tabla de eventos)
// ============================================================
// 2026-10-05 (ticket "estimado vs. real", schema v2.45): una sola fuente,
// /api/flujo-cobros -- cada cobro con fecha exacta, donde entra, monto en
// su moneda y en USD, y estado (estimado / a confirmar / cobrado). La barra
// separa la RENTA PURA (interes, dividendos, alquileres, interes bancario)
// de la amortizacion (capital que vuelve, mas tenue arriba); la linea
// punteada es el objetivo de renta mensual, que se mide solo contra la renta.

let flujoCobros = { meses: [], eventos: [], objetivo_renta_usd: null };
let flujoExpandido = false; // horizonte del GRAFICO: 12 meses (default) o todos
let mesSeleccionado = null; // filtro de la TABLA (independiente del horizonte del grafico)
let rentaDividendosCobrados = null; // /api/renta-dividendos-cobrados (2026-08-26)
let filtroRiesgo = "todos"; // filtro de la TABLA por riesgo_bucket (2026-08-22)
// Celular (2026-10-29): la tabla muestra las primeras filas y "Ver todo"
// (en escritorio la acota el scroll interno de .eventos-wrap).
const FILAS_FLUJO_CELULAR = 8;
const ES_CELULAR = window.matchMedia("(max-width: 600px)");
let tablaFlujoCompleta = false;

// Orden de apilado (de abajo hacia arriba): renta primero, amortizacion arriba.
const FLUJO_SEGMENTOS = [
  { clave: "cupon", label: "Interés" },
  { clave: "dividendo", label: "Dividendos" },
  { clave: "alquiler", label: "Alquileres" },
  { clave: "otros", label: "Interés bancario" },
  { clave: "amortizacion", label: "Amortización" },
];
const TIPO_PAGO_LABEL = Object.fromEntries(FLUJO_SEGMENTOS.map((s) => [s.clave, s.label]));
const ESTADO_FLUJO_LABEL = {
  estimado: "Estimado", a_confirmar: "A confirmar", cobrado: "Cobrado", no_cobrado: "No cobrado",
};

function fmtFechaCorta(iso) {
  const [anio, mes, dia] = iso.split("-");
  return `${dia}/${mes}/${anio.slice(2)}`;
}

function renderFlujoChart() {
  const cont = document.getElementById("flujo-chart");
  const meses = flujoCobros.meses;
  if (!meses.length) {
    cont.innerHTML = `<p class="eventos-vacio">Sin cobros futuros estimados.</p>`;
    return;
  }

  const visibles = flujoExpandido ? meses : meses.slice(0, MESES_VISIBLES_DEFAULT);
  const objetivo = flujoCobros.objetivo_renta_usd;
  const max = Math.max(...visibles.map((f) => f.total_usd), objetivo || 0, 1);
  const alturaObjetivo = objetivo ? (objetivo / max) * 100 : null;

  cont.innerHTML = visibles.map((f) => {
    const etiqueta = mesEtiqueta(f.mes);
    const seleccionada = f.mes === mesSeleccionado ? " seleccionada" : "";
    const cumple = objetivo && f.renta_usd >= objetivo ? " cumple-objetivo" : "";
    const pasado = f.mes < flujoCobros.mes_actual ? " pasado" : "";
    const segmentos = FLUJO_SEGMENTOS.map((s) => {
      const v = f.por_tipo[s.clave] || 0;
      if (!v) return "";
      const clase = s.clave === "amortizacion" ? " amortizacion" : "";
      return `<div class="flujo-seg${clase}" style="height:${(v / max) * 100}%;background:${colorForClave(s.clave)}"></div>`;
    }).join("");
    const titulo = `${etiqueta}: renta ${fmtUSD(f.renta_usd)}`
      + (objetivo ? ` (objetivo ${fmtUSD(objetivo)})` : "")
      + ` + amortización ${fmtUSD(f.amortizacion_usd)}`
      + ` — cobrado ${fmtUSD(f.cobrado_usd)}, por cobrar ${fmtUSD(f.pendiente_usd)}`
      + (f.estimado_cobrado_usd ? ` — estimado ${fmtUSD(f.estimado_cobrado_usd)} vs. real ${fmtUSD(f.real_cobrado_usd)}` : "")
      + (f.no_cobrado_usd ? ` — no cobrado ${fmtUSD(f.no_cobrado_usd)}` : "");
    return `
      <div class="flujo-col${seleccionada}${cumple}${pasado}" data-mes="${f.mes}" title="${titulo}">
        <div class="flujo-bar-area">
          <div class="flujo-bar">${segmentos}</div>
          ${alturaObjetivo !== null ? `<div class="flujo-objetivo" style="bottom:${alturaObjetivo}%"></div>` : ""}
        </div>
        <p class="flujo-col-label">${etiqueta}</p>
      </div>`;
  }).join("");

  cont.querySelectorAll(".flujo-col").forEach((el) => {
    el.addEventListener("click", () => {
      const mes = el.dataset.mes;
      mesSeleccionado = mesSeleccionado === mes ? null : mes; // clic de nuevo = deseleccionar
      renderFlujoChart();
      renderEventosTabla();
      renderFiltroInfo();
    });
  });
}

function renderFlujoLeyenda() {
  const objetivo = flujoCobros.objetivo_renta_usd;
  document.getElementById("flujo-leyenda").innerHTML = FLUJO_SEGMENTOS.map((s) => {
    const clase = s.clave === "amortizacion" ? " amortizacion" : "";
    return `<span><span class="swatch${clase}" style="background:${colorForClave(s.clave)}"></span>${s.label}</span>`;
  }).join("")
    + (objetivo ? `<span><span class="swatch-objetivo"></span>Objetivo de renta ${fmtUSD(objetivo)}/mes</span>` : "")
    + `<div id="tabla-filtro-info"></div>`;
  renderFiltroInfo();
}

// Resumen del mes seleccionado: renta vs. objetivo, amortizacion, cuanto
// entra en cada moneda y cuanto ya esta cobrado.
function renderFiltroInfo() {
  const cont = document.getElementById("tabla-filtro-info");
  if (!cont) return;
  const f = flujoCobros.meses.find((m) => m.mes === mesSeleccionado);
  if (!f) {
    cont.innerHTML = "";
    return;
  }
  const objetivo = flujoCobros.objetivo_renta_usd;
  const pctObjetivo = objetivo ? ` (${Math.round((f.renta_usd / objetivo) * 100)}% del objetivo)` : "";
  cont.className = "flujo-resumen-mes";
  cont.innerHTML = `
    <strong>${mesEtiqueta(f.mes)}</strong>
    <span>Renta <strong>${fmtUSD(f.renta_usd)}</strong>${pctObjetivo}</span>
    <span>Amortización <strong>${fmtUSD(f.amortizacion_usd)}</strong></span>
    <span>Entra: <strong>ARS ${fmtNativo(f.por_moneda.ARS)}</strong> + <strong>USD ${fmtNativo(f.por_moneda.USD)}</strong></span>
    <span>Cobrado ${fmtUSD(f.cobrado_usd)} · por cobrar ${fmtUSD(f.pendiente_usd)}</span>
    ${f.estimado_cobrado_usd ? `<span>Estimado vs. real (lo ya cobrado): ${fmtUSD(f.estimado_cobrado_usd)} → <strong>${fmtUSD(f.real_cobrado_usd)}</strong> (${f.diferencia_usd >= 0 ? "+" : ""}${fmtUSD(f.diferencia_usd)})</span>` : ""}
    ${f.no_cobrado_usd ? `<span class="flujo-estado-no_cobrado">No cobrado ${fmtUSD(f.no_cobrado_usd)}</span>` : ""}
    <button type="button" class="flujo-toggle" id="tabla-ver-todo">Ver todo</button>`;
  document.getElementById("tabla-ver-todo").addEventListener("click", () => {
    mesSeleccionado = null;
    renderFlujoChart();
    renderEventosTabla();
    renderFiltroInfo();
  });
}

// Tanda 3: "A confirmar" con movimientos posibles = la conciliacion encontro
// un empate y no adivino (se confirma con conciliar_flujos.py --vincular).
function celdaEstadoFlujo(e) {
  let texto = ESTADO_FLUJO_LABEL[e.estado] || e.estado;
  let titulo = "";
  let sub = "";
  if (e.diferencia_usd !== null && e.diferencia_usd !== undefined) {
    const signo = e.diferencia_usd >= 0 ? "+" : "";
    sub = `<span class="flujo-sub">${signo}${fmtUSD(e.diferencia_usd)}</span>`;
    titulo = `Estimado ${fmtUSD(e.monto_usd)} · real ${fmtUSD(e.monto_real_usd)}`;
  }
  if (e.candidatos && e.candidatos.length) {
    texto += ` · ${e.candidatos.length} posibles`;
    titulo = `Flujo ${e.id}: elegir con conciliar_flujos.py --vincular ${e.id} <movimiento>\n`
      + e.candidatos.map((c) => `mov ${c.id} — ${fmtFechaCorta(c.fecha)} ${c.moneda} ${fmtNativo(c.monto)} — ${c.descripcion}`).join("\n");
  }
  return `<span class="flujo-estado flujo-estado-${e.estado}" title="${escapeAttr(titulo)}">${texto}</span>${sub}`;
}

function renderEventosTabla() {
  const body = document.getElementById("eventos-body");
  let filas = mesSeleccionado
    ? flujoCobros.eventos.filter((e) => e.fecha.slice(0, 7) === mesSeleccionado)
    : flujoCobros.eventos;
  if (filtroRiesgo !== "todos") {
    filas = filas.filter((e) => e.riesgo_bucket === filtroRiesgo);
  }

  if (!filas.length) {
    body.innerHTML = `<tr><td colspan="5" class="eventos-vacio">Sin cobros para mostrar.</td></tr>`;
    document.getElementById("eventos-ver-todo").classList.add("hidden");
    return;
  }

  const boton = document.getElementById("eventos-ver-todo");
  const recortar = ES_CELULAR.matches && filas.length > FILAS_FLUJO_CELULAR;
  boton.classList.toggle("hidden", !recortar);
  boton.textContent = tablaFlujoCompleta ? "Ver menos" : `Ver todo (${filas.length})`;
  if (recortar && !tablaFlujoCompleta) filas = filas.slice(0, FILAS_FLUJO_CELULAR);

  body.innerHTML = filas.map((e) => {
    const monto = e.monto_real ?? e.monto;
    const montoUsd = e.monto_real_usd ?? e.monto_usd;
    // Monto en la moneda en que entra; si es en pesos, el USD va abajo.
    const montoCelda = e.moneda === "ARS" && monto !== null
      ? `ARS ${fmtNativo(monto)}<span class="flujo-sub">${fmtUSD(montoUsd)}</span>`
      : fmtUSD(montoUsd);
    return `
      <tr>
        <td>${fmtFechaCorta(e.fecha)}</td>
        <td>${e.concepto}<span class="flujo-sub">${TIPO_PAGO_LABEL[e.tipo] || e.tipo}</span></td>
        <td>${e.contenedor ? labelFor(e.contenedor) : "—"}</td>
        <td class="eventos-monto">${montoCelda}</td>
        <td>${celdaEstadoFlujo(e)}</td>
      </tr>`;
  }).join("");
}

// Barras horizontales genericas para un desglose "por broker" (monto_usd
// + contenedor) -- mismo color por broker que ya define CLAVE_COLOR
// (app.js), reemplaza la lista de texto plano que tenia esto antes
// (2026-08-29: mismo dato, mejor visualizacion).
function renderBrokerBarChart(filas) {
  const max = Math.max(...filas.map((f) => f.monto_usd), 1e-9);
  return `
    <div class="broker-bar-chart">
      ${filas.map((f) => {
        const color = colorForClave(f.contenedor);
        const pct = Math.max((f.monto_usd / max) * 100, 2);
        return `
          <div class="broker-bar-fila" title="${labelFor(f.contenedor)}: ${fmtUSD(f.monto_usd)}">
            <span class="broker-bar-nombre"><span class="swatch" style="background:${color}"></span>${labelFor(f.contenedor)}</span>
            <span class="broker-bar-pista"><span class="broker-bar-relleno" style="width:${pct}%;background:${color}"></span></span>
            <span class="broker-bar-valor">${fmtUSD(f.monto_usd)}</span>
          </div>`;
      }).join("")}
    </div>`;
}

// Renta de bonos YA COBRADA en el año en curso (2026-08-26) -- complemento
// de la tabla de Flujo de pagos, que solo muestra lo PROYECTADO (esa tabla
// no se toca). Interés de caución y amortización (capital, no rendimiento)
// ya vienen excluidos por renta_dividendos.py, ver ESTADO_PWOS.md para el
// criterio completo. Vive dentro del panel de Ganancia/pérdida
// (2026-08-29, antes estaba en Flujo de pagos) -- visible salvo cuando el
// filtro L1 esta en "Renta variable" (ahí lo que interesa es
// dividendos-cobrados, mismo criterio simetrico que usa esa función).
function renderRentaCobrada() {
  const cont = document.getElementById("renta-cobrada");
  if (filtroGananciaL1 === "renta_variable" || !rentaDividendosCobrados) {
    cont.classList.add("hidden");
    cont.innerHTML = "";
    return;
  }
  cont.classList.remove("hidden");
  const { anio, renta_bonos } = rentaDividendosCobrados;
  if (!renta_bonos.por_broker.length) {
    cont.innerHTML = `<span>Sin renta de bonos cobrada en ${anio} todavía.</span>`;
    return;
  }
  cont.innerHTML = `
    <span>Renta de bonos cobrada en ${anio} <strong>${fmtUSD(renta_bonos.total_usd)}</strong></span>
    ${renderBrokerBarChart(renta_bonos.por_broker)}
  `;
}

document.getElementById("flujo-toggle").addEventListener("click", () => {
  flujoExpandido = !flujoExpandido;
  document.getElementById("flujo-toggle").textContent = flujoExpandido ? "Ver menos" : "Ver todo";
  renderFlujoChart();
});

document.getElementById("eventos-ver-todo").addEventListener("click", () => {
  tablaFlujoCompleta = !tablaFlujoCompleta;
  renderEventosTabla();
});
ES_CELULAR.addEventListener("change", () => renderEventosTabla());

document.getElementById("riesgo-filtro").addEventListener("change", (e) => {
  filtroRiesgo = e.target.value;
  renderEventosTabla();
});

// ============================================================
// Metric card + panel central: ganancia/perdida por activo
// ============================================================

// Mismo agrupamiento que valorizacion.py (RENTA_FIJA/RENTA_VARIABLE) --
// no se reinventa la clasificacion aca, solo se espeja para el filtro.
const RENTA_FIJA_TIPOS = ["bono_soberano", "bono_provincial", "bono_cer", "on", "lecap"];
const RENTA_VARIABLE_TIPOS = ["accion", "cedear"];
const L1_TIPOS = { renta_fija: RENTA_FIJA_TIPOS, renta_variable: RENTA_VARIABLE_TIPOS };

let gananciaCompleta = [];
let filtroGananciaL1 = "todos";
let filtroGananciaL2 = null; // tipo puntual (ej. "bono_soberano") o null = todo el L1
let vistaGanancia = "no_realizada"; // toggle "No realizada / Realizada" (2026-08-26)

function fmtPct(valor) {
  const signo = valor > 0 ? "+" : "";
  return `${signo}${valor.toFixed(2)}%`;
}

// Rebasa el acumulado compuesto de /api/rendimiento-serie (encadenado
// desde la primera ancla real, hoy 08-nov-2021) a un sub-tramo que
// arranca en una fecha intermedia -- sin llamar de nuevo a
// serie_rendimiento con otras anclas (eso si seria tocar la logica de
// calculo). Matematicamente equivalente: chain(A->C) = chain(A->B) *
// chain(B->C), asi que chain(B->C) = chain(A->C) / chain(A->B). Devuelve
// null si el tramo pedido no existe o la cadena esta cortada por un
// hueco (nunca se inventa un numero).
function rebasarAcumulado(tramos, desdeFecha) {
  if (!tramos.length) return null;
  const idx = tramos.findIndex((t) => t.fecha_inicio >= desdeFecha);
  if (idx === -1) return null;
  const ultimo = tramos[tramos.length - 1];
  if (ultimo.acumulado_pct === null) return null;
  const base = idx === 0 ? 0 : tramos[idx - 1].acumulado_pct;
  if (base === null) return null;
  const pct = ((1 + ultimo.acumulado_pct / 100) / (1 + base / 100) - 1) * 100;
  return { pct, desde: tramos[idx].fecha_inicio };
}

// Header (2026-08-29): antes un solo "+X%" sin aclarar de que periodo
// era. Ahora 2 lineas apiladas con su propia fecha ancla explicita --
// YTD (desde la primera ancla del anio en curso) y el acumulado total
// (desde la primera ancla de toda la serie), mismo Modified Dietz que ya
// alimenta el panel de Rendimiento, sin duplicar ni recalcular nada.
// Tarjetas del header (2026-10-29): todas con la misma estructura --
// etiqueta / valor / UNA linea de detalle (`<id>-detalle`) -- para que
// tengan el mismo alto. El detalle completo, si no entra, queda en el
// title. estado: "positivo" | "negativo" | null (color del valor).
function pintarMetrica(id, valor, detalleHtml, estado, titulo) {
  const el = document.getElementById(id);
  el.classList.toggle("positivo", estado === "positivo");
  el.classList.toggle("negativo", estado === "negativo");
  el.textContent = valor;
  const det = document.getElementById(`${id}-detalle`);
  det.innerHTML = detalleHtml || "";
  det.title = titulo || det.textContent;
}

// Patrimonio (2026-10-29): la cifra protagonista. El detalle aclara que ya
// resta las deudas -- la dona de composicion muestra los activos brutos.
function renderPatrimonioTotal(resumen) {
  const detalle = resumen.total_pasivos > 0
    ? `activos ${fmtUSD(resumen.total_activos_usd)} − deudas ${fmtUSD(resumen.total_pasivos)}`
    : "sin deudas cargadas";
  pintarMetrica("patrimonio-total", fmtUSD(resumen.patrimonio_total), detalle, null);
}

// Total de activos y deudas de /api/resumen (2026-10-30): la UNICA fuente del
// total de activos, para el header y para el centro de la dona (la raiz del
// arbol de composicion suma hojas redondeadas y difiere en centavos).
let resumenActivos = null;

// Raiz de cada dimension de la dona: no todas cubren lo mismo (Broker no
// tiene inmuebles; Riesgo, solo inversiones).
const RAIZ_DONA = { tipo_activo: "Activos totales", broker: "Activos financieros", riesgo: "Inversiones" };
const NOTA_RAIZ_DONA = {
  broker: "Efectivo e inversiones por contenedor (sin inmuebles).",
  riesgo: "Solo inversiones, por nivel de riesgo (sin efectivo ni inmuebles).",
};

function renderNotaComposicion(resumen) {
  resumenActivos = { total: resumen.total_activos_usd, deudas: resumen.total_pasivos };
  pintarNotaDona();
}

// Pie de la dona: en "Tipo de activo" el centro son los activos ANTES de
// restar deudas; el patrimonio del header es el neto (a proposito).
function pintarNotaDona() {
  const el = document.getElementById("donut-nota");
  if (dimActual !== "tipo_activo") {
    el.textContent = NOTA_RAIZ_DONA[dimActual] || "";
  } else if (!resumenActivos) {
    el.textContent = "";
  } else {
    el.textContent = resumenActivos.deudas > 0
      ? `Activos antes de restar deudas. El patrimonio total del encabezado ya resta el saldo de las deudas (${fmtUSD(resumenActivos.deudas)}).`
      : "Activos totales. Sin deudas cargadas: coincide con el patrimonio total.";
  }
}

function renderGananciaTotal(resumen, tramos) {
  const anioActual = `${new Date().getFullYear()}-01-01`;
  const ytd = rebasarAcumulado(tramos, anioActual);
  const total = tramos.length ? rebasarAcumulado(tramos, tramos[0].fecha_inicio) : null;

  const partes = [];
  const titulos = [];
  if (ytd) {
    const clase = ytd.pct >= 0 ? "ganancia-positivo" : "ganancia-negativo";
    partes.push(`YTD <strong class="${clase}">${fmtPct(ytd.pct)}</strong>`);
    titulos.push(`YTD ${fmtPct(ytd.pct)} (desde ${fechaCorta(ytd.desde)})`);
  }
  if (total) {
    const clase = total.pct >= 0 ? "ganancia-positivo" : "ganancia-negativo";
    partes.push(`total <strong class="${clase}">${fmtPct(total.pct)}</strong>`);
    titulos.push(`total ${fmtPct(total.pct)} (desde ${fechaCorta(total.desde)})`);
  }
  pintarMetrica("ganancia-total", fmtUSD(resumen.ganancia_usd), partes.join(" · "),
    resumen.ganancia_usd >= 0 ? "positivo" : "negativo", titulos.join(" · "));
}

const BENCHMARK_LABEL = {
  inflacion_ars_interanual: "Inflación interanual",
  devaluacion_ars_interanual: "Devaluación interanual",
  sp500_interanual: "S&P 500 interanual",
};

// Subtitulo del header (2026-08-29): cotizaciones del dia + 3
// referencias de benchmark para comparar contra el YoY de la cartera
// (punto 2) -- cacheadas en `benchmarks_macro`, ver
// cargar_benchmarks_macro.py. Una clave sin dato todavia (script nunca
// corrido, o esa fuente fallo en la ultima corrida) se omite en vez de
// mostrar un "—" que se confunda con un valor real.
function subtituloConBenchmarks(resumen) {
  const base = `MEP ${resumen.mep.toLocaleString("es-AR")} · CCL ${resumen.ccl.toLocaleString("es-AR")} (CEDEARs vía CCL)`;
  const benchmarks = resumen.benchmarks_macro || {};
  const partes = Object.entries(BENCHMARK_LABEL)
    .filter(([clave]) => benchmarks[clave])
    .map(([clave, label]) => `${label} ${fmtPct(benchmarks[clave].valor_pct)}`);
  return partes.length ? `${base} · ${partes.join(" · ")}` : base;
}

// De cuando es el dato con el que se valua (ADR-033, 2026-10-27): en la
// rueda, "cotizaciones de las HH:MM" (en vivo, cache de 5 min); fuera de la
// rueda o si fallo el dolar, "cierre del DD/MM". Si algun activo cayo al
// cierre (panel caido, sin operaciones hoy), se avisa con la lista.
function renderEstadoCotizaciones(resumen) {
  const el = document.getElementById("cotizaciones-estado");
  const c = resumen.cotizaciones;
  if (!c) { el.textContent = ""; return; }
  const cierre = c.fecha_cierre ? c.fecha_cierre.slice(8, 10) + "/" + c.fecha_cierre.slice(5, 7) : "—";
  let texto = c.modo === "vivo" ? `Cotizaciones de las ${c.hora}` : `Cotizaciones del cierre del ${cierre}`;
  if (c.modo === "cierre" && c.falla_fuente) texto += ` — ${c.motivo}`;
  if (c.en_cierre && c.en_cierre.length) {
    const lista = c.en_cierre.map((x) => `${x.ticker} (${x.motivo})`).join(", ");
    texto += ` · al cierre del ${cierre}: ${lista}`;
  }
  el.textContent = texto;
  el.classList.toggle("cotizaciones-aviso", Boolean(c.falla_fuente));
}

// Score ponderado por posicion (database/riesgo.py, ver ESTADO_PWOS.md) --
// "Conservador" reusa el verde de --positivo y "Arriesgado" el rojo de
// --negativo (mismo criterio que ganancia: es un indicador de estado, no
// una entidad de la paleta categorica de la torta). "Moderado" y "No
// verificable" quedan sin colorear (texto neutro).
function renderRiesgoTotal(resumen) {
  const estado = resumen.riesgo_bucket === "Conservador" ? "positivo"
    : resumen.riesgo_bucket === "Arriesgado" ? "negativo" : null;
  if (resumen.riesgo_score === null) {
    pintarMetrica("riesgo-total", resumen.riesgo_bucket, "sin datos suficientes", estado);
    return;
  }
  const partes = [`score ${resumen.riesgo_score.toFixed(2)}`];
  if (resumen.riesgo_cobertura_pct < 100) partes.push(`${resumen.riesgo_cobertura_pct}% cubierto`);
  pintarMetrica("riesgo-total", resumen.riesgo_bucket, partes.join(" · "), estado);
}

// Sensibilidad a tasas (BPV/DV01, metric card 2026-09-11, pedido del
// usuario). El backend guarda bpv_total_usd como magnitud POSITIVA
// (convencion estandar de DV01, ver tir_bonos.py::bpv_cartera) -- se
// muestra en negativo aca porque una suba de tasas resta valor a renta
// fija, que es la lectura que el usuario quiere ("-$X por cada +1pb"). El
// x100 (1 punto de tasa = 100pb) es el mismo numero, mas intuitivo.
function renderSensibilidadTasa(resumen) {
  const bpv = resumen.bpv_total_usd;
  if (bpv == null) {
    pintarMetrica("bpv-total", "—", "sin datos suficientes", null);
    return;
  }
  pintarMetrica("bpv-total", fmtUSD(-bpv), "por +1 pb de suba de tasas", "negativo",
    `${fmtUSD(-bpv)} por +1 pb de suba de tasas · ${fmtUSD(-bpv * 100)} por +1 punto (100 pb)`);
}

// Carry de cartera (running yield, 2026-09-11) --
// Σ(cupon anual $) / Σ(valor de mercado $) de toda la renta fija, ya
// agregado en el backend (tir_bonos.py::sensibilidad_tasa_cartera).
// A diferencia de BPV, mas carry es mejor -- se muestra en positivo.
// Breakeven de cartera (2026-09-11) -- Carry anual total en $ / BPV
// total en $, mismo cociente que "Breakeven" por bono pero a nivel
// cartera completa. Mas breakeven es mejor. El ×1/100 (puntos de tasa,
// no solo pb) es el mismo criterio "mas intuitivo" que la tarjeta de BPV.
// Una sola tarjeta "Renta fija" (2026-10-30): carry · breakeven, para que
// el header quede en patrimonio + 4 (2x2 en celular).
function renderRentaFijaTotal(resumen) {
  const carry = resumen.carry_pct_total;
  const be = resumen.breakeven_total_pb;
  if (carry == null && be == null) {
    pintarMetrica("renta-fija-total", "—", "sin datos suficientes", null);
    return;
  }
  const valor = [carry != null ? `${carry.toFixed(2)}%` : null,
    be != null ? `${be.toLocaleString("es-AR", { maximumFractionDigits: 0 })} pb` : null].filter(Boolean).join(" · ");
  const detalle = [carry != null ? "carry anual" : null,
    be != null ? `breakeven ≈ ${(be / 100).toFixed(2)} puntos de tasa` : null].filter(Boolean).join(" · ");
  pintarMetrica("renta-fija-total", valor, detalle, "positivo",
    "Carry: cupón anual / valor de mercado de los bonos. Breakeven: cuánto pueden subir las tasas antes de que la "
    + "baja de precio se coma un año de carry.");
}

function filasFiltradas() {
  // "No realizada" solo tiene sentido para posiciones que siguen
  // abiertas (valor actual/ganancia % dependen de tenencia viva);
  // "Realizada" solo para tickers que tuvieron alguna venta de mercado
  // alguna vez, este la posicion abierta o cerrada hoy -- ver
  // dashboard.py::calcular_ganancia_por_activo (2026-08-26).
  const base = gananciaCompleta.filter((f) =>
    vistaGanancia === "realizada" ? f.tuvo_ventas : f.posicion_abierta
  );
  if (filtroGananciaL1 === "todos") return base;
  const tipos = filtroGananciaL2 ? [filtroGananciaL2] : L1_TIPOS[filtroGananciaL1];
  return base.filter((f) => tipos.includes(f.tipo));
}

function renderGananciaFiltroL2() {
  const cont = document.getElementById("ganancia-filtro-l2");
  const tipos = L1_TIPOS[filtroGananciaL1];
  if (!tipos) {
    cont.classList.add("hidden");
    cont.innerHTML = "";
    return;
  }
  // Solo se ofrecen subtipos que realmente tienen algo en la vista activa
  // (no realizada = posicion abierta, realizada = tuvo alguna venta) --
  // no tiene sentido un boton "LECAP" vacio en esta vista puntual.
  const relevantes = gananciaCompleta.filter((f) => vistaGanancia === "realizada" ? f.tuvo_ventas : f.posicion_abierta);
  const presentes = tipos.filter((t) => relevantes.some((f) => f.tipo === t));
  if (presentes.length <= 1) {
    cont.classList.add("hidden");
    cont.innerHTML = "";
    return;
  }
  cont.classList.remove("hidden");
  const botones = [{ tipo: null, label: "Todas" }, ...presentes.map((t) => ({ tipo: t, label: labelFor(t) }))];
  cont.innerHTML = botones.map((b) => {
    const activo = filtroGananciaL2 === b.tipo ? " activo" : "";
    return `<button type="button" data-tipo="${b.tipo || ""}" class="${activo.trim()}">${b.label}</button>`;
  }).join("");
  cont.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      filtroGananciaL2 = btn.dataset.tipo || null;
      renderGananciaPanel();
    });
  });
}

function renderGananciaHead() {
  const fila = document.getElementById("ganancia-head");
  fila.innerHTML = vistaGanancia === "realizada"
    ? `
        <th>Activo</th>
        <th style="text-align:right">Costo vendido (USD)</th>
        <th style="text-align:right">Producido (USD)</th>
        <th style="text-align:right">Ganancia realizada (USD)</th>
        <th style="text-align:right">Ganancia (%)</th>`
    : `
        <th>Activo</th>
        <th style="text-align:right">Cantidad</th>
        <th style="text-align:right">Invertido (USD)</th>
        <th style="text-align:right">Valor actual (USD)</th>
        <th style="text-align:right">Ganancia (%)</th>`;
}

function renderGananciaSubtotal() {
  const cont = document.getElementById("ganancia-subtotal");
  const todasFiltradas = filasFiltradas();
  const campoVerificable = vistaGanancia === "realizada" ? "ganancia_realizada_verificable" : "verificable";
  const filas = todasFiltradas.filter((f) => f[campoVerificable]);
  const sinVerificar = todasFiltradas.length - filas.length;
  if (!filas.length) {
    cont.innerHTML = sinVerificar
      ? `<span>Sin posiciones verificables en esta categoría (${sinVerificar} sin verificar).</span>`
      : `<span>Sin posiciones en esta categoría.</span>`;
    return;
  }

  if (vistaGanancia === "realizada") {
    const costoVendido = filas.reduce((s, f) => s + f.costo_vendido_usd, 0);
    const producido = filas.reduce((s, f) => s + f.producido_usd, 0);
    const ganancia = producido - costoVendido;
    const pct = costoVendido ? (ganancia / costoVendido) * 100 : 0;
    const clasePct = pct >= 0 ? "subtotal-positivo" : "subtotal-negativo";
    cont.innerHTML = `
      <span>Costo vendido <strong>${fmtUSD(costoVendido)}</strong></span>
      <span>Producido <strong>${fmtUSD(producido)}</strong></span>
      <span>Ganancia realizada <strong class="${clasePct}">${fmtUSD(ganancia)} (${fmtPct(pct)})</strong></span>
      ${sinVerificar ? `<span>(${sinVerificar} venta${sinVerificar > 1 ? "s" : ""} sin verificar, no incluida${sinVerificar > 1 ? "s" : ""})</span>` : ""}
    `;
    return;
  }

  const invertido = filas.reduce((s, f) => s + f.invertido_usd, 0);
  const amortizacion = filas.reduce((s, f) => s + f.amortizacion_cobrada_usd, 0);
  const actual = filas.reduce((s, f) => s + f.valor_actual_usd, 0);
  // Retorno total = valor actual + amortizacion ya cobrada (fix 2026-08-21,
  // ver dashboard.py::calcular_ganancia_por_activo) -- el % del subtotal
  // tiene que salir de la misma formula que cada fila, si no queda
  // inconsistente contra el ranking de la tabla. La linea de texto de
  // "Amortización cobrada" se saco de la UI (2026-08-29, no interesa en
  // este panel) pero el numero se sigue sumando acá adentro del % -- no
  // es logica de calculo nueva, solo dejo de mostrarse aparte.
  const retornoTotal = actual + amortizacion;
  const pct = invertido ? ((retornoTotal / invertido) - 1) * 100 : 0;
  const clasePct = pct >= 0 ? "subtotal-positivo" : "subtotal-negativo";
  cont.innerHTML = `
    <span>Invertido <strong>${fmtUSD(invertido)}</strong></span>
    <span>Valor actual <strong>${fmtUSD(actual)}</strong></span>
    <span>Ganancia/pérdida <strong class="${clasePct}">${fmtPct(pct)}</strong></span>
    ${sinVerificar ? `<span>(${sinVerificar} posición${sinVerificar > 1 ? "es" : ""} sin verificar, no incluida${sinVerificar > 1 ? "s" : ""})</span>` : ""}
  `;
}

function renderGananciaTabla() {
  const body = document.getElementById("ganancia-body");
  const filas = filasFiltradas();
  const colspanVacio = 5;
  if (!filas.length) {
    body.innerHTML = `<tr><td colspan="${colspanVacio}" class="eventos-vacio">Sin posiciones en esta categoría.</td></tr>`;
    return;
  }

  if (vistaGanancia === "realizada") {
    body.innerHTML = filas.map((f) => {
      if (!f.ganancia_realizada_verificable) {
        return `
          <tr title="${f.motivo_no_verificable_realizada || ""}">
            <td>${f.ticker}</td>
            <td class="eventos-monto">no verificable</td>
            <td class="eventos-monto">no verificable</td>
            <td class="eventos-monto">—</td>
            <td class="eventos-monto">—</td>
          </tr>`;
      }
      const claseColor = f.ganancia_realizada_usd >= 0 ? "ganancia-positivo" : "ganancia-negativo";
      return `
        <tr>
          <td>${f.ticker}</td>
          <td class="eventos-monto">${fmtUSD(f.costo_vendido_usd)}</td>
          <td class="eventos-monto">${fmtUSD(f.producido_usd)}</td>
          <td class="eventos-monto ${claseColor}">${fmtUSD(f.ganancia_realizada_usd)}</td>
          <td class="eventos-monto ${claseColor}">${f.ganancia_realizada_porcentaje === null ? "—" : fmtPct(f.ganancia_realizada_porcentaje)}</td>
        </tr>`;
    }).join("");
    return;
  }

  body.innerHTML = filas.map((f) => {
    // "verificable=false" (compra en especie sin costo real reconstruible,
    // o amortizacion cobrada en ARS sin tipo de cambio historico): no hay
    // invertido/porcentaje que mostrar, y no es un dato faltante por error
    // -- se marca explicito en vez de dejar un NaN.
    if (!f.verificable) {
      return `
        <tr title="${f.motivo_no_verificable || ""}">
          <td>${f.ticker}</td>
          <td class="eventos-monto">${fmtCantidad(f.cantidad)}</td>
          <td class="eventos-monto">no verificable</td>
          <td class="eventos-monto">${fmtUSD(f.valor_actual_usd)}</td>
          <td class="eventos-monto">—</td>
        </tr>`;
    }
    const claseColor = f.ganancia_porcentaje >= 0 ? "ganancia-positivo" : "ganancia-negativo";
    return `
      <tr>
        <td>${f.ticker}</td>
        <td class="eventos-monto">${fmtCantidad(f.cantidad)}</td>
        <td class="eventos-monto">${fmtUSD(f.invertido_usd)}</td>
        <td class="eventos-monto">${fmtUSD(f.valor_actual_usd)}</td>
        <td class="eventos-monto ${claseColor}">${fmtPct(f.ganancia_porcentaje)}</td>
      </tr>`;
  }).join("");
}

// Dividendos de acciones/CEDEARs YA COBRADOS en el año en curso
// (2026-08-26) -- resumen dentro de este mismo panel, visible solo
// filtrado en "Renta variable" (mismo agrupamiento RENTA_VARIABLE de
// valorizacion.py que ya usa este filtro L1). El bucket "sin_atribuir"
// (un broker cuyo export no trae el ticker de cada dividendo)
// se muestra aparte, nunca escondido dentro del total.
function renderDividendosCobrados() {
  const cont = document.getElementById("dividendos-cobrados");
  if (filtroGananciaL1 !== "renta_variable" || !rentaDividendosCobrados) {
    cont.classList.add("hidden");
    cont.innerHTML = "";
    return;
  }
  cont.classList.remove("hidden");
  const { anio, dividendos } = rentaDividendosCobrados;
  const porTicker = dividendos.por_ticker
    .map((f) => `<span>${f.ticker} <strong>${fmtUSD(f.monto_usd)}</strong></span>`)
    .join("");
  const sinAtribuir = dividendos.sin_atribuir
    .map((f) => `<span>Sin atribuir por ticker (${f.contenedor}) <strong>${fmtUSD(f.monto_usd)}</strong></span>`)
    .join("");
  cont.innerHTML = `
    <span>Dividendos cobrados en ${anio} <strong>${fmtUSD(dividendos.total_usd)}</strong></span>
    ${porTicker}
    ${sinAtribuir}
  `;
}

function renderGananciaPanel() {
  renderGananciaHead();
  renderGananciaFiltroL2();
  renderGananciaSubtotal();
  renderGananciaTabla();
  renderRentaCobrada();
  renderDividendosCobrados();
}

// ============================================================
// Panel de rendimiento en el tiempo (Modified Dietz, 2026-08-21)
// ============================================================
// La serie arranca en la primera fecha con foto de cartera COMPLETA
// (hoy 2026-07-14, ver ESTADO_PWOS.md) -- nunca "todo el historial".
// El subtitulo lo deja explicito a proposito, no en un pie de pagina.

function fechaCorta(iso) {
  const [y, m, d] = iso.split("-");
  const meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  return `${d}-${meses[parseInt(m, 10) - 1]}-${y.slice(2)}`;
}

// ---- Panel de Rendimiento (rediseño 2026-10-27) -------------------------
// Fuente: cierres mensuales del sheet (/api/rendimiento-serie, ADR-023) + el
// mes en curso provisorio, calculado con PWOS (/api/rendimiento-provisorio).
// Tres piezas que se leen juntas: fila de indicadores, curva del acumulado y
// barras del rendimiento de cada mes, alineadas mes a mes (un solo eje Y por
// grafico, nunca dos escalas en el mismo). Se dibujan al ancho REAL del
// contenedor (antes: viewBox fijo + preserveAspectRatio="none", que estiraba
// los circulos en elipses y deformaba el texto) y se redibujan al cambiar el
// tamaño. Colores: la curva en el ambar de la casa; las barras con un par
// diverging validado para daltonismo contra la superficie oscura (verde
// azulado / rojo anaranjado, scripts/validate_palette.js de la skill dataviz:
// CVD deltaE 14,8) -- la direccion de la barra (arriba/abajo) repite el signo.
const MESES_LARGOS = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
  "agosto", "septiembre", "octubre", "noviembre", "diciembre"];
// MESES_CORTOS ya esta definido arriba (mismo orden).
const REND_COLOR_POS = "#0ea5a4";
const REND_COLOR_NEG = "#e5603d";
const SVG_NS = "http://www.w3.org/2000/svg";

let rendimientoEstado = { filas: [], hover: null };

function fmtPctEs(v, dec = 2) {
  if (v === null || v === undefined) return "—";
  const signo = v > 0 ? "+" : (v < 0 ? "−" : "");
  return `${signo}${Math.abs(v).toLocaleString("es-AR", { minimumFractionDigits: dec, maximumFractionDigits: dec })} %`;
}

function fmtUsdEs(v) {
  if (v === null || v === undefined) return "—";
  const signo = v < 0 ? "−" : "";
  return `${signo}USD ${Math.abs(v).toLocaleString("es-AR", { maximumFractionDigits: 0 })}`;
}

function nombreMes(f, largo = false) {
  const n = largo ? MESES_LARGOS[f.mes - 1] : MESES_CORTOS[f.mes - 1];
  return largo ? `${n.charAt(0).toUpperCase()}${n.slice(1)} ${f.anio}` : `${n}-${String(f.anio).slice(2)}`;
}

// Una fila por mes de actividad (el mes de `fecha_inicio`: la foto del 1/10
// cierra septiembre). La cadena firme se corta en el primer tramo incompleto.
function filasRendimiento(tramos, prov) {
  const filas = [];
  for (const t of tramos) {
    if (!t.completo || t.acumulado_pct === null || t.rendimiento_pct === null) break;
    const [y, m] = t.fecha_inicio.split("-").map(Number);
    filas.push({
      anio: y, mes: m, desde: t.fecha_inicio, hasta: t.fecha_fin, pct: t.rendimiento_pct,
      acumulado: t.acumulado_pct, patrimonio: t.emv_usd, aporte: t.flujo_neto_usd,
      usd: t.emv_usd - t.bmv_usd - t.flujo_neto_usd, provisorio: false,
    });
  }
  const ult = filas[filas.length - 1];
  if (prov && prov.rendimiento_pct !== null && ult && prov.fecha_inicio === ult.hasta) {
    const [y, m] = prov.fecha_inicio.split("-").map(Number);
    filas.push({
      anio: y, mes: m, desde: prov.fecha_inicio, hasta: prov.fecha_fin, pct: prov.rendimiento_pct,
      acumulado: prov.acumulado_pct, patrimonio: prov.emv_usd, aporte: prov.flujo_neto_usd,
      usd: prov.rendimiento_usd, provisorio: true, ultimoMovimiento: prov.ultimo_movimiento,
    });
  }
  return filas;
}

function encadenar(filas) {
  return (filas.reduce((a, f) => a * (1 + f.pct / 100), 1) - 1) * 100;
}

function ticksLindos(min, max, n = 4) {
  const span = max - min || 1;
  const paso0 = span / n;
  const mag = 10 ** Math.floor(Math.log10(paso0));
  const paso = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((p) => p >= paso0);
  const out = [];
  for (let v = Math.floor(min / paso) * paso; v <= Math.ceil(max / paso) * paso + 1e-9; v += paso) {
    out.push(Math.round(v * 1e6) / 1e6);
  }
  return out;
}

function svgEl(tag, attrs = {}, texto = null) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  if (texto !== null) el.textContent = texto;
  return el;
}

// Columna con el extremo de dato redondeado (4px) y la base recta.
function barraRedondeada(x, yBase, yDato, w, r = 4) {
  const h = Math.abs(yDato - yBase);
  const rr = Math.min(r, h, w / 2);
  const s = yDato < yBase ? 1 : -1; // 1 = hacia arriba
  return `M${x},${yBase} V${yDato + s * rr} Q${x},${yDato} ${x + rr},${yDato} H${x + w - rr} ` +
    `Q${x + w},${yDato} ${x + w},${yDato + s * rr} V${yBase} Z`;
}

function renderRendimientoSubtitulo(filas) {
  const el = document.getElementById("rendimiento-subtitulo");
  const firmes = filas.filter((f) => !f.provisorio);
  if (!firmes.length) {
    el.textContent = "Sin datos suficientes todavía.";
    return;
  }
  const prov = filas.find((f) => f.provisorio);
  el.textContent = `Cierre mensual de todos los contenedores, ` +
    `de ${nombreMes(firmes[0])} a ${nombreMes(firmes[firmes.length - 1])}` +
    (prov ? `; ${MESES_LARGOS[prov.mes - 1]} provisorio con PWOS al ${fechaCorta(prov.hasta)} ` +
      `(último movimiento cargado: ${fechaCorta(prov.ultimoMovimiento)}).` : ".");
  document.getElementById("rendimiento-titulo").textContent = `Rendimiento desde ${nombreMes(firmes[0])}`;
}

function renderRendimientoKpis(filas) {
  const cont = document.getElementById("rendimiento-kpis");
  cont.replaceChildren();
  const firmes = filas.filter((f) => !f.provisorio);
  if (!firmes.length) return;
  const ult = firmes[firmes.length - 1];
  const anualizado = (Math.pow(1 + ult.acumulado / 100, 12 / firmes.length) - 1) * 100;
  const delAnio = firmes.filter((f) => f.anio === ult.anio);
  const mejor = firmes.reduce((a, f) => (f.pct > a.pct ? f : a));
  const peor = firmes.reduce((a, f) => (f.pct < a.pct ? f : a));
  const positivos = firmes.filter((f) => f.pct > 0).length;
  const prov = filas.find((f) => f.provisorio);

  const tiles = [
    ["Acumulado", ult.acumulado, `${firmes.length} meses`],
    ["Anualizado", anualizado, "compuesto"],
    [`${ult.anio} en el año`, encadenar(delAnio), `${nombreMes(delAnio[0])} a ${nombreMes(ult)}`],
    ["Último cierre", ult.pct, `${nombreMes(ult)} · ${fmtUsdEs(ult.usd)}`],
    ["Mejor mes", mejor.pct, nombreMes(mejor)],
    ["Peor mes", peor.pct, nombreMes(peor)],
    ["Meses positivos", null, `${Math.round((positivos / firmes.length) * 100)} % del total`, `${positivos} de ${firmes.length}`],
  ];
  if (prov) tiles.push([`${nombreMes(prov, true).split(" ")[0]} (provisorio)`, prov.pct, `al ${fechaCorta(prov.hasta)} · ${fmtUsdEs(prov.usd)}`]);

  for (const [label, valor, sub, valorTexto] of tiles) {
    const tile = document.createElement("div");
    tile.className = "rend-kpi" + (label.includes("provisorio") ? " provisorio" : "");
    const l = document.createElement("div");
    l.className = "rend-kpi-label";
    l.textContent = label;
    const v = document.createElement("div");
    v.className = "rend-kpi-valor";
    if (valor !== null) v.classList.add(valor >= 0 ? "ganancia-positivo" : "ganancia-negativo");
    v.textContent = valorTexto ?? fmtPctEs(valor);
    const s = document.createElement("div");
    s.className = "rend-kpi-sub";
    s.textContent = sub;
    tile.append(l, v, s);
    cont.append(tile);
  }
}

// Geometria compartida por los 2 graficos: un "carril" por mes de actividad.
// La curva pone cada punto en el borde derecho del carril (la foto que cierra
// el mes); la barra va en el centro del carril.
function geometriaRendimiento(ancho, n) {
  const padL = 48, padR = ancho < 640 ? 70 : 104;
  const paso = (ancho - padL - padR) / n;
  return { padL, padR, paso, ancho, xBorde: (i) => padL + i * paso, xCentro: (i) => padL + (i + 0.5) * paso };
}

function renderRendimientoAcumulado(svg, filas, g) {
  const H = 250, padT = 30, padB = 14;
  const plotH = H - padT - padB;
  svg.setAttribute("viewBox", `0 0 ${g.ancho} ${H}`);
  svg.setAttribute("height", H);
  svg.replaceChildren();

  const valores = [0, ...filas.map((f) => f.acumulado)];
  const ticks = ticksLindos(Math.min(...valores), Math.max(...valores), 4);
  const yMin = ticks[0], yMax = ticks[ticks.length - 1];
  const y = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  // Franjas por año (alternadas) con el rendimiento encadenado de ese año.
  const anios = [...new Set(filas.map((f) => f.anio))];
  anios.forEach((anio, k) => {
    const idx = filas.map((f, i) => (f.anio === anio ? i : -1)).filter((i) => i >= 0);
    const x0 = g.xBorde(idx[0]), x1 = g.xBorde(idx[idx.length - 1] + 1);
    if (k % 2 === 0) svg.append(svgEl("rect", { class: "rend-franja-anio", x: x0, y: padT - 22, width: x1 - x0, height: plotH + 22 }));
    const firmesAnio = idx.map((i) => filas[i]).filter((f) => !f.provisorio);
    // Lo que entra en la franja: año + rendimiento (~110px), solo el año, o nada.
    if (x1 - x0 < 36) return;
    const t = svgEl("text", { class: "rend-anio-label", x: x0 + 6, y: padT - 8 });
    t.append(svgEl("tspan", { class: "rend-anio-nombre" }, `${anio} `));
    if (firmesAnio.length && x1 - x0 >= 120) {
      const parcial = firmesAnio.length < 12 ? ` (${firmesAnio.length} m)` : "";
      t.append(svgEl("tspan", {}, `${fmtPctEs(encadenar(firmesAnio), 1)}${parcial}`));
    }
    svg.append(t);
  });

  for (const v of ticks) {
    svg.append(svgEl("line", { class: v === 0 ? "rend-cero" : "rend-grilla", x1: g.padL, x2: g.ancho - g.padR, y1: y(v), y2: y(v) }));
    svg.append(svgEl("text", { class: "rend-eje-label", x: g.padL - 8, y: y(v) + 3.5, "text-anchor": "end" }, `${v} %`));
  }

  const firmes = filas.filter((f) => !f.provisorio);
  const puntos = [{ x: g.xBorde(0), y: y(0) }, ...firmes.map((f, i) => ({ x: g.xBorde(i + 1), y: y(f.acumulado) }))];
  const linea = puntos.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  const ultimo = puntos[puntos.length - 1];
  svg.append(svgEl("path", { class: "rend-area", d: `${linea} L${ultimo.x.toFixed(1)},${y(0)} L${puntos[0].x},${y(0)} Z` }));
  svg.append(svgEl("path", { class: "rend-linea", d: linea }));

  const prov = filas.find((f) => f.provisorio);
  if (prov) {
    const xp = g.xBorde(filas.length), yp = y(prov.acumulado);
    svg.append(svgEl("path", { class: "rend-linea-provisoria", d: `M${ultimo.x},${ultimo.y} L${xp},${yp}` }));
    svg.append(svgEl("circle", { class: "rend-punto-provisorio", cx: xp, cy: yp, r: 4.5 }));
    svg.append(svgEl("text", { class: "rend-valor-label", x: xp + 10, y: yp + 4 }, fmtPctEs(prov.acumulado)));
    svg.append(svgEl("text", { class: "rend-valor-sub", x: xp + 10, y: yp + 19 }, "provisorio"));
  }
  svg.append(svgEl("circle", { class: "rend-punto-final", cx: ultimo.x, cy: ultimo.y, r: 4.5 }));
  const ultFirme = firmes[firmes.length - 1];
  const yEtiqueta = prov ? ultimo.y - 12 : ultimo.y + 4;
  svg.append(svgEl("text", { class: "rend-valor-label", x: prov ? ultimo.x : ultimo.x + 10, y: yEtiqueta,
    "text-anchor": prov ? "end" : "start" }, fmtPctEs(ultFirme.acumulado)));

  // Capa de hover: linea vertical + punto resaltado (los dibuja moverHover).
  svg.append(svgEl("line", { class: "rend-crosshair", id: "rend-cross-acum", y1: padT - 22, y2: H - padB, visibility: "hidden" }));
  svg.append(svgEl("circle", { class: "rend-punto-hover", id: "rend-punto-hover", r: 5, visibility: "hidden" }));
  svg.dataset.yfn = JSON.stringify({ padT, plotH, yMin, yMax });
  svg.setAttribute("aria-label", `Rendimiento acumulado: ${fmtPctEs(ultFirme.acumulado)} al cierre de ${nombreMes(ultFirme, true)}.`);
}

function renderRendimientoMensual(svg, filas, g) {
  const H = 180, padT = 18, padB = 46;
  const plotH = H - padT - padB;
  svg.setAttribute("viewBox", `0 0 ${g.ancho} ${H}`);
  svg.setAttribute("height", H);
  svg.replaceChildren();

  const pcts = filas.map((f) => f.pct);
  const ticks = ticksLindos(Math.min(-1, ...pcts), Math.max(1, ...pcts), 4);
  const yMin = ticks[0], yMax = ticks[ticks.length - 1];
  const y = (v) => padT + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

  for (const v of ticks) {
    svg.append(svgEl("line", { class: v === 0 ? "rend-cero" : "rend-grilla", x1: g.padL, x2: g.ancho - g.padR, y1: y(v), y2: y(v) }));
    svg.append(svgEl("text", { class: "rend-eje-label", x: g.padL - 8, y: y(v) + 3.5, "text-anchor": "end" }, `${v} %`));
  }

  const firmes = filas.filter((f) => !f.provisorio);
  const mejor = firmes.reduce((a, f) => (f.pct > a.pct ? f : a), firmes[0]);
  const peor = firmes.reduce((a, f) => (f.pct < a.pct ? f : a), firmes[0]);
  const w = Math.max(3, Math.min(24, g.paso - 4));
  filas.forEach((f, i) => {
    const x = g.xCentro(i) - w / 2;
    svg.append(svgEl("path", {
      class: `rend-barra${f.provisorio ? " provisoria" : ""}`, "data-idx": i,
      d: barraRedondeada(x, y(0), y(f.pct), w),
      fill: f.pct >= 0 ? REND_COLOR_POS : REND_COLOR_NEG,
    }));
    // Etiqueta selectiva: solo el mejor y el peor mes (el resto, en el tooltip y la tabla).
    if (f === mejor || f === peor) {
      const arriba = f.pct >= 0;
      svg.append(svgEl("text", { class: "rend-valor-chico", x: g.xCentro(i), y: arriba ? y(f.pct) - 5 : y(f.pct) + 12,
        "text-anchor": "middle" }, fmtPctEs(f.pct, 1)));
    }
  });

  // Eje X: el mes de actividad; con lugar, los trimestres con su abreviatura
  // (o solo julio si el carril es angosto) y enero con el año.
  const mesesConRotulo = g.paso >= 22 ? [1, 4, 7, 10] : (g.paso >= 12 ? [7] : []);
  filas.forEach((f, i) => {
    if (f.mes === 1 || (i === 0 && f.mes <= 9)) {
      svg.append(svgEl("text", { class: "rend-eje-anio", x: g.xCentro(i), y: H - 5, "text-anchor": "middle" }, String(f.anio)));
    }
    if (mesesConRotulo.includes(f.mes) || (i === filas.length - 1 && g.paso >= 22)) {
      svg.append(svgEl("text", { class: "rend-eje-label", x: g.xCentro(i), y: H - 20, "text-anchor": "middle" },
        MESES_CORTOS[f.mes - 1]));
    }
  });
  svg.append(svgEl("line", { class: "rend-crosshair", id: "rend-cross-mes", y1: padT, y2: H - padB, visibility: "hidden" }));
  svg.setAttribute("aria-label", `Rendimiento de cada mes: mejor ${nombreMes(mejor)} ${fmtPctEs(mejor.pct)}, peor ${nombreMes(peor)} ${fmtPctEs(peor.pct)}.`);
}

function moverHover(idx) {
  const { filas } = rendimientoEstado;
  const tip = document.getElementById("rendimiento-tooltip");
  const svgA = document.getElementById("rendimiento-svg");
  const svgM = document.getElementById("rendimiento-svg-mensual");
  const crossA = document.getElementById("rend-cross-acum");
  const crossM = document.getElementById("rend-cross-mes");
  const punto = document.getElementById("rend-punto-hover");
  if (!crossA || !crossM) return;
  svgM.querySelectorAll(".rend-barra").forEach((b) => b.classList.toggle("atenuada", idx !== null && Number(b.dataset.idx) !== idx));
  if (idx === null) {
    [crossA, crossM, punto].forEach((e) => e.setAttribute("visibility", "hidden"));
    tip.hidden = true;
    return;
  }
  const f = filas[idx];
  const g = rendimientoEstado.geo;
  const yf = JSON.parse(svgA.dataset.yfn);
  const xa = g.xBorde(idx + 1), ya = yf.padT + yf.plotH - ((f.acumulado - yf.yMin) / (yf.yMax - yf.yMin)) * yf.plotH;
  crossA.setAttribute("x1", xa); crossA.setAttribute("x2", xa); crossA.setAttribute("visibility", "visible");
  crossM.setAttribute("x1", g.xCentro(idx)); crossM.setAttribute("x2", g.xCentro(idx)); crossM.setAttribute("visibility", "visible");
  punto.setAttribute("cx", xa); punto.setAttribute("cy", ya); punto.setAttribute("visibility", "visible");

  tip.replaceChildren();
  const titulo = document.createElement("div");
  titulo.className = "rend-tip-titulo";
  titulo.textContent = nombreMes(f, true) + (f.provisorio ? ` · provisorio al ${fechaCorta(f.hasta)}` : "");
  tip.append(titulo);
  const filasTip = [
    [fmtPctEs(f.pct), `en el mes (${fmtUsdEs(f.usd)})`, f.pct >= 0 ? REND_COLOR_POS : REND_COLOR_NEG],
    [fmtPctEs(f.acumulado), "acumulado", "var(--accent)"],
    [fmtUsdEs(f.patrimonio), f.provisorio ? `patrimonio PWOS al ${fechaCorta(f.hasta)}` : `patrimonio al ${fechaCorta(f.hasta)}`, null],
    [fmtUsdEs(f.aporte), "aporte neto del mes (sueldo − gastos)", null],
  ];
  for (const [valor, label, color] of filasTip) {
    const fila = document.createElement("div");
    fila.className = "rend-tip-fila";
    const key = document.createElement("span");
    key.className = "rend-tip-key";
    if (color) key.style.background = color;
    const v = document.createElement("strong");
    v.textContent = valor;
    const l = document.createElement("span");
    l.className = "rend-tip-label";
    l.textContent = label;
    fila.append(key, v, l);
    tip.append(fila);
  }
  tip.hidden = false;
  const cont = document.getElementById("rendimiento-graficos");
  const anchoTip = tip.offsetWidth;
  const izq = g.xCentro(idx) + 14 + anchoTip > cont.clientWidth ? g.xCentro(idx) - 14 - anchoTip : g.xCentro(idx) + 14;
  tip.style.left = `${Math.max(0, izq)}px`;
}

function engancharHoverRendimiento() {
  const cont = document.getElementById("rendimiento-graficos");
  if (cont.dataset.hover) return;
  cont.dataset.hover = "1";
  const indice = (ev) => {
    const g = rendimientoEstado.geo;
    if (!g) return null;
    const svg = ev.target.closest("svg");
    if (!svg) return null;
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * g.ancho;
    const i = Math.floor((px - g.padL) / g.paso);
    return i >= 0 && i < rendimientoEstado.filas.length ? i : null;
  };
  cont.addEventListener("pointermove", (ev) => moverHover(indice(ev)));
  cont.addEventListener("pointerleave", () => moverHover(null));
}

function renderRendimientoTabla(filas) {
  const cont = document.getElementById("rendimiento-tabla");
  const tabla = document.createElement("table");
  tabla.className = "eventos-tabla rend-tabla-detalle";
  const cab = document.createElement("tr");
  for (const h of ["Mes", "Rend. del mes", "USD", "Aporte neto", "Patrimonio", "Acumulado"]) {
    const th = document.createElement("th");
    th.textContent = h;
    cab.append(th);
  }
  const thead = document.createElement("thead");
  thead.append(cab);
  const tbody = document.createElement("tbody");
  for (const f of [...filas].reverse()) {
    const tr = document.createElement("tr");
    if (f.provisorio) tr.className = "provisoria";
    const celdas = [nombreMes(f, true) + (f.provisorio ? " (prov.)" : ""), fmtPctEs(f.pct), fmtUsdEs(f.usd),
      fmtUsdEs(f.aporte), fmtUsdEs(f.patrimonio), fmtPctEs(f.acumulado)];
    celdas.forEach((c, k) => {
      const td = document.createElement("td");
      td.textContent = c;
      if (k === 1) td.className = f.pct >= 0 ? "ganancia-positivo" : "ganancia-negativo";
      tr.append(td);
    });
    tbody.append(tr);
  }
  tabla.append(thead, tbody);
  cont.replaceChildren(tabla);
}

function dibujarGraficosRendimiento() {
  const { filas } = rendimientoEstado;
  const svgA = document.getElementById("rendimiento-svg");
  const svgM = document.getElementById("rendimiento-svg-mensual");
  if (!filas.length) {
    svgA.replaceChildren();
    svgM.replaceChildren();
    return;
  }
  const ancho = Math.max(320, Math.round(document.getElementById("rendimiento-graficos").clientWidth));
  const g = geometriaRendimiento(ancho, filas.length);
  rendimientoEstado.geo = g;
  renderRendimientoAcumulado(svgA, filas, g);
  renderRendimientoMensual(svgM, filas, g);
}

function renderRendimientoTodo(tramos, prov) {
  rendimientoEstado.filas = filasRendimiento(tramos, prov);
  renderRendimientoSubtitulo(rendimientoEstado.filas);
  renderRendimientoKpis(rendimientoEstado.filas);
  dibujarGraficosRendimiento();
  renderRendimientoTabla(rendimientoEstado.filas);
}

function renderRendimientoPanel(tramos) {
  renderRendimientoTodo(tramos, null);
  engancharHoverRendimiento();
  const cont = document.getElementById("rendimiento-graficos");
  if (!cont.dataset.resize) {
    cont.dataset.resize = "1";
    let pendiente = false;
    new ResizeObserver(() => {
      if (pendiente) return;
      pendiente = true;
      requestAnimationFrame(() => { pendiente = false; dibujarGraficosRendimiento(); });
    }).observe(cont);
  }
  // El provisorio llega aparte y despues: si falla o no hay, lo firme ya esta dibujado.
  fetch("/api/rendimiento-provisorio")
    .then((r) => (r.ok ? r.json() : null))
    .then((prov) => { if (prov && prov.rendimiento_pct !== null) renderRendimientoTodo(tramos, prov); })
    .catch(() => {});
  const nota = document.getElementById("rendimiento-nota");
  nota.textContent =
    "* Rendimiento time-weighted, compuesto mes a mes. El aporte de cada mes (sueldo neto de gastos, " +
    "incluidas compras grandes y pagos de deudas) no cuenta como rendimiento ese mes, pero " +
    "sí como base del siguiente. El mes en curso es provisorio: se calcula con PWOS y se reemplaza al cargar el cierre.";
}

document.getElementById("ganancia-filtro-l1").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-filtro]");
  if (!btn || btn.classList.contains("activo")) return;
  filtroGananciaL1 = btn.dataset.filtro;
  filtroGananciaL2 = null;
  document.querySelectorAll("#ganancia-filtro-l1 button").forEach((b) => b.classList.toggle("activo", b === btn));
  renderGananciaPanel();
});

document.getElementById("ganancia-vista-toggle").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-vista]");
  if (!btn || btn.classList.contains("activo")) return;
  vistaGanancia = btn.dataset.vista;
  filtroGananciaL2 = null; // el subtipo activo puede no existir en la otra vista
  document.querySelectorAll("#ganancia-vista-toggle button").forEach((b) => b.classList.toggle("activo", b === btn));
  renderGananciaPanel();
});

// ============================================================
// Panel: efectivo por contenedor (moneda nativa, para cruce manual)
// ============================================================

// El backend manda los slots SIEMPRE en el mismo orden que las columnas
// de la tabla: [disp ARS, disp USD, cauc ARS, cauc USD, PF ARS, PF USD, disp USD_CCL].
function renderEfectivoPorContenedor(filas) {
  const body = document.getElementById("efectivo-body");
  body.innerHTML = filas
    .map((f) => {
      const celdas = f.slots
        .map((s) => `<td class="eventos-monto">${fmtNativo(s.saldo)}</td>`)
        .join("");
      // Contenedor compartido (ADR-032): la tabla muestra el saldo real de la
      // app (100%); el patrimonio cuenta solo la parte propia.
      const compartido = f.pct_propiedad < 100
        ? `<span class="flujo-sub">Saldo real (100%) — en el patrimonio cuenta el ${f.pct_propiedad}%</span>`
        : "";
      return `<tr><td>${labelFor(f.contenedor)}${compartido}</td>${celdas}</tr>`;
    })
    .join("");
}

// ============================================================
// Panel: "Bonos en tenencia" (2026-09-10) -- PPC / precio / paridad /
// invertido / valorizacion + TIR entrada vs hoy (compresion) por bono
// vigente. Antes era "Rotacion de bonos" con candidatos de reemplazo
// (los candidatos siguen en /api/rotacion-bonos para el reporte CLI,
// solo dejaron de mostrarse aca).
//
// Filtro por categoria/moneda + columnas ordenables (2026-09-11, pedido
// del usuario) -- mismo patron chips/data-sort que "Mercado de bonos", pero
// sin buscador de texto ni checkbox de variantes: acá son ~22 filas (la
// cartera vigente), no ~890 especies, así que ordenar cada columna ya
// alcanza para encontrar el más/menos de cualquier metrica sin necesitar
// un filtro numerico por rango.
// ============================================================

let rotacionBonosData = [];
let rotacionCategoria = "todas"; // "todas" | clave de categoria (ver clasificar_bonos.CATEGORIAS)
let rotacionSort = { campo: "compresion_pp", desc: true };

function renderRotacionChips() {
  const cont = document.getElementById("rotacion-chips");
  const conteo = {};
  for (const f of rotacionBonosData) conteo[f.categoria] = (conteo[f.categoria] || 0) + 1;
  const chips = [
    { clave: "todas", label: "Todas", total: rotacionBonosData.length },
    ...Object.entries(conteo).map(([clave, total]) => ({
      clave, total, label: rotacionBonosData.find((f) => f.categoria === clave).categoria_label,
    })),
  ];
  cont.innerHTML = chips.map((c) => `
    <button type="button" class="mercado-chip${c.clave === rotacionCategoria ? " activo" : ""}"
            data-cat="${c.clave}">${c.label} <span class="mercado-chip-n">${c.total}</span></button>
  `).join("");
}

function renderRotacionBonos() {
  const moneda = document.getElementById("rotacion-moneda").value;
  const { campo, desc } = rotacionSort;

  let filas = rotacionBonosData.filter((f) => {
    if (rotacionCategoria !== "todas" && f.categoria !== rotacionCategoria) return false;
    if (moneda && f.moneda !== moneda) return false;
    return true;
  });

  const cmp = (a, b) => {
    let x = a[campo], y = b[campo];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "string") return desc ? y.localeCompare(x) : x.localeCompare(y);
    return desc ? y - x : x - y;
  };
  filas = filas.sort(cmp);

  const body = document.getElementById("rotacion-body");
  body.innerHTML = filas
    .map((f) => {
      const emisor = f.emisor || "—";
      const duration = f.duration == null ? "—" : fmtNum(f.duration);
      if (!("compresion_pp" in f)) {
        return `<tr class="rotacion-sin-calcular"><td>${f.ticker}</td><td>${emisor}</td>` +
               `<td class="mercado-cat-celda">${f.categoria_label}</td>` +
               `<td class="eventos-monto">${duration}</td>` +
               `<td colspan="10">sin TIR calculable — ${f.motivo}</td></tr>`;
      }
      const clasePp = f.candidato_a_rotar ? "rotacion-candidato" : "";
      // Bonos CER: TIR real (CER + x%), ver tir_bonos.py.
      const real = f.tir_real ? ` <span title="TIR real: por encima del CER">R</span>` : "";
      return `
        <tr>
          <td>${f.ticker}</td>
          <td class="mercado-emisor-celda" title="${escapeAttr(f.emisor || "")}">${emisor}</td>
          <td class="mercado-cat-celda">${f.categoria_label}</td>
          <td class="eventos-monto">${duration}</td>
          <td class="eventos-monto">${fmtCantidad(f.cantidad)}</td>
          <td>${f.moneda}</td>
          <td class="eventos-monto">${fmtNum(f.precio_entrada_vn100)}</td>
          <td class="eventos-monto">${fmtNum(f.precio_hoy_vn100)}</td>
          <td class="eventos-monto">${f.paridad_pct == null ? "—" : fmtNum(f.paridad_pct)}</td>
          <td class="eventos-monto">${fmtUSD(f.invertido_usd)}</td>
          <td class="eventos-monto">${fmtUSD(f.valorizacion_usd)}</td>
          <td class="eventos-monto">${fmtPct(f.tir_entrada_pct)}${real}</td>
          <td class="eventos-monto">${fmtPct(f.tir_hoy_pct)}${real}</td>
          <td class="eventos-monto ${clasePp}">${fmtPct(f.compresion_pp)}</td>
        </tr>
      `;
    })
    .join("") || `<tr><td colspan="14" class="eventos-vacio">Sin bonos para este filtro.</td></tr>`;
}

function wireRotacionBonos() {
  document.getElementById("rotacion-moneda").addEventListener("change", renderRotacionBonos);
  document.getElementById("rotacion-chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".mercado-chip");
    if (!chip) return;
    rotacionCategoria = chip.dataset.cat;
    renderRotacionChips();
    renderRotacionBonos();
  });
  document.querySelectorAll("#rotacion-tabla thead th[data-sort]").forEach((th) => {
    th.style.cursor = "pointer";
    th.addEventListener("click", () => {
      const campo = th.dataset.sort;
      if (rotacionSort.campo === campo) rotacionSort.desc = !rotacionSort.desc;
      else rotacionSort = { campo, desc: true };
      renderRotacionBonos();
    });
  });
}

// ============================================================
// Panel: "Breakeven de tasa" (Carry vs. BPV por bono, 2026-09-11).
// Reusa rotacionBonosData (misma fuente que "Bonos en tenencia", ya
// trae bpv_usd/carry_usd/breakeven_pb) -- sin fetch propio. Tabla chica
// y enfocada a proposito (el usuario pidio no saturar "Bonos en tenencia"
// de columnas): solo lo necesario para juzgar el breakeven por bono,
// no duplica el resto de la info que ya esta en la tabla principal.
// ============================================================

let breakevenSort = { campo: "breakeven_pb", desc: false }; // ascendente: lo mas ajustado primero

function renderBreakevenTasa() {
  const { campo, desc } = breakevenSort;
  const filas = rotacionBonosData.filter((f) => f.breakeven_pb != null);
  const sinCalcular = rotacionBonosData.length - filas.length;

  const cmp = (a, b) => {
    let x = a[campo], y = b[campo];
    if (typeof x === "string") return desc ? y.localeCompare(x) : x.localeCompare(y);
    return desc ? y - x : x - y;
  };
  filas.sort(cmp);

  document.getElementById("breakeven-subtitulo").textContent =
    `${filas.length} bonos con breakeven calculable` +
    (sinCalcular ? ` (${sinCalcular} sin BPV/TIR calculable, no se muestran)` : "") + ".";

  document.getElementById("breakeven-body").innerHTML = filas.map((f) => `
    <tr>
      <td>${f.ticker}</td>
      <td class="mercado-cat-celda">${f.categoria_label}</td>
      <td class="eventos-monto">${fmtUSD(f.bpv_usd)}</td>
      <td class="eventos-monto">${fmtUSD(f.carry_usd)}</td>
      <td class="eventos-monto">${f.breakeven_pb.toLocaleString("es-AR", { maximumFractionDigits: 0 })}</td>
    </tr>
  `).join("") || `<tr><td colspan="5" class="eventos-vacio">Sin datos.</td></tr>`;
}

function wireBreakevenTasa() {
  document.querySelectorAll("#breakeven-tabla thead th[data-sort]").forEach((th) => {
    th.style.cursor = "pointer";
    th.addEventListener("click", () => {
      const campo = th.dataset.sort;
      if (breakevenSort.campo === campo) breakevenSort.desc = !breakevenSort.desc;
      else breakevenSort = { campo, desc: true };
      renderBreakevenTasa();
    });
  });
}

// ============================================================
// Panel: calendario de flujo mensual (Epica 11, H2 paso 1)
// ============================================================

// Detalle por mes con fecha exacta (2026-09-17): antes
// la tabla solo mostraba el mes agregado. Mismo patron ▾/▸ que "Umbrales
// de monitoreo" (tesis) -- estado de expandido/colapsado por mes, se
// pierde al recargar (no hace falta persistirlo, a diferencia de los
// paneles colapsables enteros).
const calendarioAbiertos = new Set();
const CALENDARIO_TIPO_LABEL = { interes: "Interés", amortizacion: "Amortización", otros: "Interés bancario", alquileres: "Alquiler", dividendos: "Dividendo" };

function renderCalendarioFlujo(datos) {
  document.getElementById("calendario-subtitulo").textContent =
    `Próximos 12 meses — promedio USD ${datos.promedio_usd.toFixed(2)}/mes, mes flojo por debajo de USD ${datos.umbral_usd.toFixed(2)} (50% del promedio). Misma fuente que el flujo del Dashboard: bonos, dividendos y alquileres son estimados hasta que la ronda incremental los confirma (* = todavía sin confirmar). "Otros" = interés bancario ya cobrado.`;

  const body = document.getElementById("calendario-body");
  body.innerHTML = datos.serie
    .map((f) => {
      const estado = f.mes_flojo
        ? `<span class="calendario-flojo">mes flojo</span>`
        : "cubierto";
      const estimadoNota = f.tiene_estimado ? " *" : "";
      const abierto = calendarioAbiertos.has(f.mes);
      const detalleFilas = f.detalle.length
        ? f.detalle.map((d) => {
            const est = d.es_estimado ? " *" : "";
            const desc = d.descripcion
              ? ` <span class="calendario-detalle-info" title="${escapeAttr(d.descripcion)}">ⓘ</span>` : "";
            return `
              <tr class="calendario-detalle-fila">
                <td>${fechaCorta(d.fecha)}</td>
                <td>${CALENDARIO_TIPO_LABEL[d.tipo] || d.tipo}</td>
                <td>${d.concepto}${desc}</td>
                <td class="eventos-monto">${fmtUSD(d.monto_usd)}${est}</td>
              </tr>`;
          }).join("")
        : `<tr class="calendario-detalle-fila"><td colspan="4" class="eventos-vacio">Sin movimientos este mes.</td></tr>`;
      return `
        <tr class="calendario-mes-fila">
          <td><button type="button" class="calendario-btn-detalle" data-mes="${f.mes}">${abierto ? "▾" : "▸"}</button> ${f.mes}${estimadoNota}</td>
          <td class="eventos-monto">${fmtUSD(f.interes_usd)}</td>
          <td class="eventos-monto">${fmtUSD(f.amortizacion_usd)}</td>
          <td class="eventos-monto">${fmtUSD(f.dividendos_usd)}</td>
          <td class="eventos-monto">${fmtUSD(f.alquileres_usd)}</td>
          <td class="eventos-monto">${fmtUSD(f.otros_usd)}</td>
          <td class="eventos-monto">${fmtUSD(f.total_usd)}</td>
          <td>${estado}</td>
        </tr>
        <tr class="calendario-detalle" ${abierto ? "" : "hidden"}>
          <td colspan="8">
            <table class="calendario-detalle-tabla">
              <thead><tr><th>Fecha</th><th>Tipo</th><th>Concepto</th><th>Monto&nbsp;(USD)</th></tr></thead>
              <tbody>${detalleFilas}</tbody>
            </table>
          </td>
        </tr>
      `;
    })
    .join("");
}

function wireCalendarioFlujo() {
  document.getElementById("calendario-body").addEventListener("click", (e) => {
    const btn = e.target.closest(".calendario-btn-detalle");
    if (!btn) return;
    const mes = btn.dataset.mes;
    const filaDetalle = btn.closest(".calendario-mes-fila").nextElementSibling;
    if (calendarioAbiertos.has(mes)) {
      calendarioAbiertos.delete(mes);
      filaDetalle.hidden = true;
      btn.textContent = "▸";
    } else {
      calendarioAbiertos.add(mes);
      filaDetalle.hidden = false;
      btn.textContent = "▾";
    }
  });
}

// ============================================================
// Panel: rebalanceo de Renta Variable (Epica 11, H3)
// ============================================================

let rebalanceoData = null;
let rebalanceoTickerDetalleAbierto = null;

function escapeAttr(valor) {
  return String(valor ?? "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
}

function accionLabel(accion) {
  if (accion === "vender") return `<span class="rebalanceo-vender">vender</span>`;
  if (accion === "agregar") return `<span class="rebalanceo-agregar">agregar</span>`;
  if (accion === "mantener") return "mantener";
  return "—";
}

function renderRebalanceo() {
  const datos = rebalanceoData;
  document.getElementById("rebalanceo-subtitulo").textContent =
    `Total Renta Variable: ${fmtUSD(datos.total_rv_usd)} — acción sugerida si el desvío supera ±${datos.umbral_pp}pp`;

  const body = document.getElementById("rebalanceo-body");
  body.innerHTML = datos.posiciones
    .map((p) => {
      const abierto = rebalanceoTickerDetalleAbierto === p.ticker;
      const filaPrincipal = `
        <tr>
          <td><button type="button" class="rebalanceo-ticker-btn" data-ticker="${p.ticker}">${p.ticker}</button></td>
          <td class="eventos-monto">${fmtUSD(p.valor_actual_usd)}</td>
          <td class="eventos-monto">${p.peso_actual_pct.toFixed(2)}%</td>
          <td class="eventos-monto">
            <input type="number" step="0.1" class="rebalanceo-input-pct"
                   data-ticker="${escapeAttr(p.ticker)}" data-campo="peso_objetivo_pct"
                   value="${p.peso_objetivo_pct ?? ""}" placeholder="—">
          </td>
          <td class="eventos-monto">${p.delta_pp !== undefined ? fmtPct(p.delta_pp) : "—"}</td>
          <td>${accionLabel(p.accion)}</td>
          <td class="eventos-monto">${p.monto_sugerido_usd ? fmtUSD(p.monto_sugerido_usd) : "—"}</td>
          <td><button type="button" class="rebalanceo-guardar" data-ticker="${escapeAttr(p.ticker)}">Guardar</button></td>
        </tr>
      `;
      const filaDetalle = `
        <tr class="rebalanceo-detalle" data-detalle-de="${p.ticker}" ${abierto ? "" : "hidden"}>
          <td colspan="8">
            <div class="rebalanceo-detalle-grid">
              <label>Precio objetivo (moneda nativa)
                <input type="number" step="0.01" data-ticker="${escapeAttr(p.ticker)}" data-campo="precio_objetivo"
                       value="${p.precio_objetivo ?? ""}">
              </label>
              <label>Horizonte
                <input type="text" data-ticker="${escapeAttr(p.ticker)}" data-campo="horizonte"
                       value="${escapeAttr(p.horizonte_tesis)}">
              </label>
              <label>Riesgo
                <input type="text" data-ticker="${escapeAttr(p.ticker)}" data-campo="riesgo"
                       value="${escapeAttr(p.riesgo_tesis)}">
              </label>
              <label style="grid-column: 1 / -1">Motivo
                <textarea rows="2" data-ticker="${escapeAttr(p.ticker)}" data-campo="motivo">${escapeAttr(p.motivo_tesis)}</textarea>
              </label>
              <label style="grid-column: 1 / -1">Criterio de venta
                <textarea rows="2" data-ticker="${escapeAttr(p.ticker)}" data-campo="criterio_venta">${escapeAttr(p.criterio_venta)}</textarea>
              </label>
            </div>
          </td>
        </tr>
      `;
      return filaPrincipal + filaDetalle;
    })
    .join("");
}

async function guardarTesis(ticker) {
  const campos = {};
  document.querySelectorAll(`[data-ticker="${CSS.escape(ticker)}"][data-campo]`).forEach((el) => {
    const val = el.value.trim();
    campos[el.dataset.campo] = val === "" ? null : (el.type === "number" ? parseFloat(val) : val);
  });
  const resp = await fetch(`/api/tesis/${encodeURIComponent(ticker)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(campos),
  });
  if (!resp.ok) {
    alert(`No se pudo guardar la tesis de ${ticker}.`);
    return;
  }
  const r = await fetch("/api/rebalanceo-rv");
  rebalanceoData = await r.json();
  renderRebalanceo();
  // El peso objetivo tambien se muestra en la tabla de Tenencias.
  cargarAccionesRv().catch(() => { });
}

document.getElementById("rebalanceo-body").addEventListener("click", (e) => {
  const btnTicker = e.target.closest(".rebalanceo-ticker-btn");
  if (btnTicker) {
    const ticker = btnTicker.dataset.ticker;
    rebalanceoTickerDetalleAbierto = rebalanceoTickerDetalleAbierto === ticker ? null : ticker;
    renderRebalanceo();
    return;
  }
  const btnGuardar = e.target.closest(".rebalanceo-guardar");
  if (btnGuardar) {
    guardarTesis(btnGuardar.dataset.ticker);
  }
});

// ============================================================
// Solapa "Analisis - Acciones", rediseño (2026-09-21, Fase 1): KPIs,
// tenencias, composicion por cubeta y concentracion. Tablero de decision,
// nunca de recomendacion: muestra pesos contra topes/bandas que salen de
// parametros_rv (via /api/tenencias-rv y /api/composicion-rv). Formato
// es-AR (fmtNum). Sin dato = "—"/"Sin asignar", nunca un valor inventado.
// ============================================================

let tenenciasRvData = null;
let composicionRvData = null;
let tenenciasEditorAbierto = null;

const CUBETAS_UI = { valor_puro: "Valor puro", calidad: "Calidad", mayor_riesgo: "Mayor riesgo" };

function fmtPctAr(v, dec = 1) {
  return v === null || v === undefined ? "—" : `${fmtNum(v, dec)} %`;
}

function fmtUsdAr(v) {
  return v === null || v === undefined ? "—" : `USD ${fmtNum(v, 2)}`;
}

function chipCubeta(cubeta) {
  if (!cubeta) return `<span class="acc-chip acc-chip-sin">Sin asignar</span>`;
  return `<span class="acc-chip acc-chip-${cubeta}">${CUBETAS_UI[cubeta] ?? escapeAttr(cubeta)}</span>`;
}

// Mini barra con marca vertical en el tope; ambar si lo supera.
function barraConTope(pct, tope, escala, supera, extraClase = "") {
  const ancho = Math.min((pct / escala) * 100, 100);
  const marca = Math.min((tope / escala) * 100, 100);
  return `<span class="acc-barra ${extraClase}"><span class="acc-barra-fill${supera ? " acc-ambar" : ""}" style="width:${ancho}%"></span><span class="acc-barra-tope" style="left:${marca}%"></span></span>`;
}

function renderTenenciasRv() {
  const datos = tenenciasRvData;
  const k = datos.kpis;
  const tope = datos.parametros.tope_posicion_rv_pct;

  document.getElementById("acc-fuente").textContent =
    `Precios en USD · Datos: snapshot ${datos.fecha_datos ?? "sin fecha"}`;

  document.getElementById("acc-kpi-rv-valor").textContent = fmtUsdAr(k.valor_total_usd);
  document.getElementById("acc-kpi-rv-sub").textContent =
    `${k.n_posiciones} posiciones · ${k.n_cedears} CEDEARs · ${k.n_acciones} acciones locales`;
  document.getElementById("acc-kpi-peso-valor").textContent = fmtPctAr(k.peso_patrimonio_pct, 2);
  document.getElementById("acc-kpi-peso-sub").textContent =
    k.objetivo_rv_patrimonio_pct === null
      ? "Objetivo de renta variable: [a definir]"
      : `Objetivo de renta variable: ${fmtPctAr(k.objetivo_rv_patrimonio_pct)}`;
  document.getElementById("acc-kpi-top5-valor").textContent = fmtPctAr(k.concentracion_top5_pct);
  document.getElementById("acc-kpi-tesis-valor").textContent = `${k.con_tesis} de ${k.n_posiciones}`;
  const alertasEl = document.getElementById("acc-kpi-alertas-valor");
  alertasEl.textContent = String(k.alertas_activas);
  alertasEl.classList.toggle("acc-ambar-txt", k.alertas_activas > 0);
  document.getElementById("acc-kpi-alertas-sub").textContent = k.alertas_atenuadas
    ? `${k.alertas_atenuadas} atenuada${k.alertas_atenuadas === 1 ? "" : "s"} (no cuentan)`
    : "revisión, indicadores y topes";

  document.getElementById("tenencias-rv-subtitulo").textContent =
    `% de la RV y % del patrimonio bruto (${fmtUsdAr(datos.patrimonio_bruto_usd)})`;

  const escala = Math.max(tope * 2, ...datos.posiciones.map((p) => p.pct_rv));
  const opcionesCubeta = (sel) =>
    `<option value="">Sin asignar</option>` +
    Object.entries(CUBETAS_UI)
      .map(([v, txt]) => `<option value="${v}"${sel === v ? " selected" : ""}>${txt}</option>`)
      .join("");

  document.getElementById("tenencias-body").innerHTML = datos.posiciones
    .map((p) => {
      const t = escapeAttr(p.ticker);
      const superaAlgo = p.supera_tope_rv || p.supera_tope_patrimonio;
      const textoTope = p.supera_tope_rv ? "tope" : "tope patr.";
      const titleTope = p.supera_tope_rv
        ? `Supera el tope de ${fmtNum(tope, 0)} % de la RV`
        : `Supera el tope de ${fmtNum(datos.parametros.tope_posicion_patrimonio_pct, 0)} % del patrimonio bruto`;
      const abierto = tenenciasEditorAbierto === p.ticker;
      const tesisCelda = celdaSemaforo(p);
      return `
        <tr data-ticker="${t}" class="${fichaTicker === p.ticker ? "acc-fila-sel" : ""}">
          <td><strong>${t}</strong><span class="acc-chip acc-chip-tipo">${p.tipo === "cedear" ? "CEDEAR" : "Acción"}</span></td>
          <td>${chipCubeta(p.cubeta)}</td>
          <td class="eventos-monto">${fmtCantidad(p.cantidad)}</td>
          <td class="eventos-monto">${fmtNum(p.valor_usd, 2)}</td>
          <td>
            <span class="acc-pct-num">${fmtNum(p.pct_rv, 1)} %</span>${barraConTope(p.pct_rv, tope, escala, superaAlgo)}
            ${superaAlgo ? `<span class="acc-ambar-txt acc-tope-txt" title="${escapeAttr(titleTope)}">${textoTope}</span>` : ""}
          </td>
          <td class="eventos-monto">${p.peso_objetivo_pct !== null ? fmtNum(p.peso_objetivo_pct, 1) + " %" : "—"}</td>
          <td>${tesisCelda}</td>
          <td>${chipEstadoValor(p.estado_valor)}</td>
          <td><button type="button" class="rebalanceo-guardar tenencias-cargar-btn" data-ticker="${t}">Cargar</button></td>
        </tr>
        <tr class="tenencias-editor" ${abierto ? "" : "hidden"}>
          <td colspan="9">
            <div class="acc-editor">
              <label>Cubeta
                <select data-campo="cubeta">${opcionesCubeta(p.cubeta)}</select>
              </label>
              <label>Peso objetivo (% de la RV)
                <input type="number" step="0.1" min="0" max="100" data-campo="peso_objetivo_pct" value="${p.peso_objetivo_pct ?? ""}" placeholder="—">
              </label>
              <button type="button" class="rebalanceo-guardar tenencias-guardar-btn" data-ticker="${t}">Guardar</button>
              <span class="acc-tenue">Solo se tocan estos 2 campos — la tesis y lo demás no se modifica.</span>
            </div>
          </td>
        </tr>
      `;
    })
    .join("");

  document.getElementById("tenencias-pie-texto").textContent =
    `Total renta variable · ${k.n_posiciones} posiciones` +
    (datos.sin_valuar.length ? ` · sin precio (no incluidas): ${datos.sin_valuar.join(", ")}` : "");
  document.getElementById("tenencias-pie-monto").textContent = fmtUsdAr(k.valor_total_usd);
}

function renderCubetasRv() {
  const c = composicionRvData;
  document.getElementById("cubetas-rv-cuerpo").innerHTML = c.cubetas
    .map((b) => {
      const min = b.objetivo_min_pct ?? 0;
      const max = b.objetivo_max_pct;
      const objetivo = b.objetivo_min_pct === null
        ? `máx. ${fmtNum(max, 0)} %`
        : `${fmtNum(min, 0)}–${fmtNum(max, 0)} %`;
      const sobre = b.cargada && b.actual_pct > max;
      const actual = b.cargada
        ? `${fmtPctAr(b.actual_pct)} · ${b.n_posiciones} ${b.n_posiciones === 1 ? "posición" : "posiciones"}`
        : "sin asignar";
      const pos = Math.min(b.actual_pct, 100);
      return `
        <div class="acc-cubeta acc-c-${b.cubeta}">
          <div class="acc-cubeta-cab">
            <span class="acc-cubeta-nombre">${escapeAttr(b.nombre)}</span>
            <span class="acc-tenue">objetivo ${objetivo}</span>
          </div>
          <div class="acc-cubeta-track">
            <span class="acc-cubeta-banda" style="left:${min}%;width:${max - min}%"></span>
            ${b.cargada ? `<span class="acc-cubeta-fill${sobre ? " acc-ambar" : ""}" style="width:${pos}%"></span><span class="acc-cubeta-marca" style="left:${pos}%"></span>` : ""}
          </div>
          <div class="acc-cubeta-actual${sobre ? " acc-ambar-txt" : ""}">actual: ${actual}</div>
        </div>
      `;
    })
    .join("");

  document.getElementById("cubetas-rv-nota").textContent = c.sin_asignar_n
    ? `Sin asignar: ${fmtPctAr(c.sin_asignar_pct)} de la RV (${c.sin_asignar_n} posiciones). Cada cubeta se mide sobre el total de la RV, incluyendo lo que todavía no tiene cubeta.`
    : "Todas las posiciones tienen cubeta asignada.";
}

function renderConcentracionRv() {
  const c = composicionRvData;
  const topeP = c.tope_posicion_rv_pct;
  const escalaP = Math.max(topeP * 2, ...c.top5.map((x) => x.pct_rv));
  const filasTop = c.top5
    .map((x) => `
      <div class="acc-conc-fila">
        <span class="acc-conc-nombre">${escapeAttr(x.ticker)}</span>
        ${barraConTope(x.pct_rv, topeP, escalaP, x.supera_tope, "acc-barra-ancha")}
        <span class="acc-conc-val${x.supera_tope ? " acc-ambar-txt" : ""}">${fmtPctAr(x.pct_rv)}</span>
      </div>`)
    .join("");

  const topeS = c.tope_sector_rv_pct;
  const clasificados = c.sectores.filter((s) => s.sector !== "Sin clasificar");
  const escalaS = Math.max(topeS * 2, ...clasificados.map((s) => s.pct_rv));
  const filasSector = clasificados
    .map((s) => `
      <div class="acc-conc-fila">
        <span class="acc-conc-nombre acc-conc-sector">${escapeAttr(s.sector)}</span>
        ${barraConTope(s.pct_rv, topeS, escalaS, s.supera_tope, "acc-barra-ancha")}
        <span class="acc-conc-val${s.supera_tope ? " acc-ambar-txt" : ""}">${fmtPctAr(s.pct_rv)}</span>
      </div>`)
    .join("");

  document.getElementById("concentracion-rv-cuerpo").innerHTML = `
    <div class="acc-conc-titulo">Top 5 posiciones <span class="acc-tenue">(tope ${fmtNum(topeP, 0)} % de la RV)</span></div>
    ${filasTop || `<div class="acc-tenue">Sin posiciones.</div>`}
    <div class="acc-conc-titulo acc-conc-titulo-sep">Por sector <span class="acc-tenue">(tope ${fmtNum(topeS, 0)} % de la RV)</span></div>
    ${filasSector}
    <div class="acc-tenue">Sin clasificar: ${fmtPctAr(c.sin_clasificar_pct)} de la RV</div>
  `;
  document.getElementById("concentracion-rv-nota").textContent =
    "Sector de Yahoo (traducido) o el que fijes en SECTOR_MANUAL. Un ETF no tiene sector; lo sin clasificar no cuenta contra el tope.";
}

async function cargarAccionesRv() {
  const [t, c, sc, rr] = await Promise.all([
    fetch("/api/tenencias-rv"), fetch("/api/composicion-rv"), fetch("/api/screener-rv"), fetch("/api/riesgo-rv"),
  ]);
  if (!t.ok || !c.ok || !sc.ok || !rr.ok) throw new Error("La API de tenencias respondió con error");
  tenenciasRvData = await t.json();
  composicionRvData = await c.json();
  screenerRvData = await sc.json();
  riesgoRvData = await rr.json();
  renderTenenciasRv();
  renderCubetasRv();
  renderConcentracionRv();
  renderAlertasRv();
  renderScreenerRv();
  renderRiesgoRv();
  cargarFronteraRv();   // sin await: calculo propio (~1 s), no retrasa el resto de la solapa
  cargarCedearMoneda(); // sin await: red externa (data912 + dolarapi)
  cargarCaroBarato();   // sin await: red externa, todo el universo de ratios_cedear
  actualizarSimuladorFuentes();
  // Con la ficha abierta solo se refrescan las partes SIN campos editables
  // (cabecera y alertas de la posicion): re-renderizar los formularios
  // pisaria lo que el usuario tiene escrito y todavia no guardo en otra seccion.
  if (fichaTicker) {
    renderFichaCab();
    if (!fichaEsCandidata) renderFichaAlertasPos();
    cargarValuacionFicha();
  }
}

async function guardarTenenciaRv(ticker, filaEditor) {
  const cubeta = filaEditor.querySelector('[data-campo="cubeta"]').value || null;
  const pesoRaw = filaEditor.querySelector('[data-campo="peso_objetivo_pct"]').value.trim();
  const peso = pesoRaw === "" ? null : parseFloat(pesoRaw);
  if (peso !== null && (Number.isNaN(peso) || peso < 0 || peso > 100)) {
    alert("El peso objetivo tiene que estar entre 0 y 100.");
    return;
  }
  const resp = await fetch(`/api/tesis/${encodeURIComponent(ticker)}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cubeta, peso_objetivo_pct: peso }),
  });
  if (!resp.ok) {
    alert(`No se pudo guardar ${ticker}.`);
    return;
  }
  tenenciasEditorAbierto = null;
  await cargarAccionesRv();
  // El panel de rebalanceo (mas abajo) lee el mismo peso objetivo.
  const rb = await fetch("/api/rebalanceo-rv");
  if (rb.ok) {
    rebalanceoData = await rb.json();
    renderRebalanceo();
  }
}

document.getElementById("tenencias-body").addEventListener("click", (e) => {
  const btnCargar = e.target.closest(".tenencias-cargar-btn");
  if (btnCargar) {
    const ticker = btnCargar.dataset.ticker;
    tenenciasEditorAbierto = tenenciasEditorAbierto === ticker ? null : ticker;
    renderTenenciasRv();
    return;
  }
  const btnGuardar = e.target.closest(".tenencias-guardar-btn");
  if (btnGuardar) {
    guardarTenenciaRv(btnGuardar.dataset.ticker, btnGuardar.closest("tr"));
    return;
  }
  // Clic en cualquier otra parte de una fila de posicion = abrir su ficha.
  const fila = e.target.closest("tr[data-ticker]");
  if (fila) abrirFicha(fila.dataset.ticker);
});

// ============================================================
// Fase 2 (2026-09-21): semaforo por posicion, panel de alertas y Ficha de
// posicion. El semaforo y las alertas los calcula el servidor
// (indicadores_rv.py / alertas_rv.py); aca solo se muestran. Cada seccion de
// la ficha guarda SOLO sus campos (POST /api/tesis/{ticker} es UPDATE
// parcial) y se renderiza por separado: refrescar una no pisa lo que hay
// escrito, sin guardar, en las otras.
// ============================================================

const ESTADO_UI = { verde: "Verde", amarillo: "Amarillo", rojo: "Rojo" };

function celdaSemaforo(p) {
  if (p.semaforo) {
    const cob = p.indicadores_con_dato < p.indicadores_total
      ? ` <span class="acc-tenue">· ${p.indicadores_con_dato} de ${p.indicadores_total}</span>`
      : "";
    return `<span class="acc-dot acc-dot-${p.semaforo}"></span>${ESTADO_UI[p.semaforo]}${cob}`;
  }
  if (p.indicadores_total > 0) {
    return `<span class="acc-dot acc-dot-gris"></span><span class="acc-tenue">Sin lecturas</span>`;
  }
  return p.tiene_tesis
    ? `<span class="acc-dot acc-dot-gris"></span>Tesis cargada`
    : `<span class="acc-dot acc-dot-gris"></span><span class="acc-tenue">Sin tesis</span>`;
}

function chipEstado(estado) {
  return estado
    ? `<span class="acc-est acc-est-${estado}">${ESTADO_UI[estado]}</span>`
    : `<span class="acc-est acc-est-sin">Sin dato</span>`;
}

const ETIQUETA_ALERTA = { revisada: "Revisada", justificada: "Justificada" };

function alertaHtml(a) {
  const etiqueta = ETIQUETA_ALERTA[a.estado]
    ? `<span class="acc-alerta-etiqueta">${ETIQUETA_ALERTA[a.estado]}</span>`
    : "";
  return `
    <div class="acc-alerta acc-alerta-${a.severidad}${a.estado !== "activa" ? " acc-alerta-atenuada" : ""}">
      ${a.ticker
        ? `<button type="button" class="acc-alerta-titulo" data-ticker="${escapeAttr(a.ticker)}">${escapeAttr(a.titulo)}</button>`
        : `<span class="acc-alerta-titulo acc-alerta-titulo-fijo">${escapeAttr(a.titulo)}</span>`}${etiqueta}
      <div class="acc-alerta-detalle">${escapeAttr(a.detalle)}</div>
    </div>`;
}

function renderAlertasRv() {
  const d = tenenciasRvData;
  const activas = d.alertas.filter((a) => a.estado === "activa");
  const atenuadas = d.alertas.filter((a) => a.estado !== "activa");
  const plural = (n, s) => `${n} ${s}${n === 1 ? "" : "s"}`;
  document.getElementById("alertas-rv-subtitulo").textContent =
    plural(activas.length, "activa") + (atenuadas.length ? ` · ${plural(atenuadas.length, "atenuada")}` : "");

  const pct = d.parametros.revision_obligatoria_caida_pct;
  let html = activas.length
    ? `<div class="acc-alertas-lista">${activas.map(alertaHtml).join("")}</div>`
    : `<div class="acc-alerta-vacia">Sin alertas activas</div>
       <ul class="acc-ficha-reglas">
         <li>Revisión obligatoria si una posición cae ${pct} % o más desde su costo.</li>
         <li>Indicador de una posición en amarillo 2 lecturas seguidas → frenar aportes.</li>
         <li>Indicador en rojo → reducir de forma escalonada, salvo que se documente por escrito por qué la tesis sigue viva.</li>
         <li>Precio ≤ valor intrínseco × (1 − margen requerido) con el semáforo en verde → candidata a sumar.</li>
       </ul>`;
  if (atenuadas.length) {
    html += `<div class="acc-conc-titulo acc-conc-titulo-sep">Atenuadas <span class="acc-tenue">(no cuentan en el KPI)</span></div>
             <div class="acc-alertas-lista">${atenuadas.map(alertaHtml).join("")}</div>`;
  }
  document.getElementById("alertas-rv-cuerpo").innerHTML = html;
}

document.getElementById("alertas-rv-cuerpo").addEventListener("click", (e) => {
  const btn = e.target.closest(".acc-alerta-titulo");
  if (btn) abrirFicha(btn.dataset.ticker);
});

// ---- Ficha de posicion ----------------------------------------------------

let fichaTicker = null;
let fichaEsCandidata = false;
let fichaValuacion = null;   // respuesta de /api/valuacion-rv/{ticker}
let screenerRvData = null;
let fichaTesis = null;
let fichaIndicadores = [];
let fichaIndAbierto = null;    // id del indicador con el historial desplegado
let fichaIndEditando = null;   // id del indicador en edicion
let fichaFormNuevo = false;

const hoyLocalISO = () => new Date().toLocaleDateString("sv-SE"); // AAAA-MM-DD en hora local

async function apiJson(url, metodo = "GET", cuerpo = null) {
  const resp = await fetch(url, {
    method: metodo,
    headers: { "Content-Type": "application/json" },
    body: cuerpo === null ? undefined : JSON.stringify(cuerpo),
  });
  if (!resp.ok) {
    let detalle = "";
    try { detalle = (await resp.json()).detail; } catch { /* sin cuerpo JSON */ }
    // Los refrescos que corren un script devuelven {stdout, stderr}: se muestra su ultima linea util.
    if (detalle && typeof detalle === "object" && !Array.isArray(detalle)) {
      const texto = String(detalle.stderr || detalle.stdout || "").split("\n").map((l) => l.trim()).filter(Boolean).pop();
      detalle = texto || "";
    }
    throw new Error(typeof detalle === "string" && detalle ? detalle : `Error ${resp.status} (datos inválidos)`);
  }
  return resp.json();
}

function posicionFicha() {
  return tenenciasRvData.posiciones.find((p) => p.ticker === fichaTicker) ?? null;
}

async function abrirFicha(ticker, esCandidata = false) {
  fichaTicker = ticker;
  fichaEsCandidata = esCandidata;
  fichaValuacion = null;
  fichaIndAbierto = null;
  fichaIndEditando = null;
  fichaFormNuevo = false;
  fichaTesis = null;
  fichaIndicadores = [];
  document.getElementById("ficha-rv").classList.remove("hidden");
  renderTenenciasRv(); // resalta la fila seleccionada
  renderScreenerRv();
  document.getElementById("ficha-cab").innerHTML = `<span class="acc-tenue">Cargando ${escapeAttr(ticker)}...</span>`;
  ["ficha-tesis", "ficha-indicadores", "ficha-alertas-pos", "ficha-valuacion-auto", "ficha-valuacion", "ficha-alertas"]
    .forEach((id) => { document.getElementById(id).innerHTML = ""; });
  if (esCandidata) {
    // Una candidata no esta en `activos`: no hay tesis ni indicadores que guardar, solo su valuacion.
    fichaTesis = { proxima_revision: null };
    renderFichaCab();
    await cargarValuacionFicha();
    document.getElementById("ficha-rv").scrollIntoView({ behavior: "smooth", block: "start" });
    return;
  }
  try {
    const [tesis, indicadores] = await Promise.all([
      apiJson(`/api/tesis/${encodeURIComponent(ticker)}`),
      apiJson(`/api/tesis-indicadores?ticker=${encodeURIComponent(ticker)}`),
    ]);
    if (fichaTicker !== ticker) return; // se abrio otra ficha mientras cargaba
    fichaTesis = tesis;
    fichaIndicadores = indicadores;
  } catch (err) {
    document.getElementById("ficha-cab").innerHTML =
      `<span class="acc-ficha-estado-error">No se pudo cargar la ficha: ${escapeAttr(err.message)}</span>`;
    return;
  }
  renderFichaCab();
  renderFichaTesis();
  renderFichaIndicadores();
  renderFichaAlertasPos();
  renderFichaValuacion();
  renderFichaAlertas();
  cargarValuacionFicha();
  document.getElementById("ficha-rv").scrollIntoView({ behavior: "smooth", block: "start" });
}

function cerrarFicha() {
  fichaTicker = null;
  fichaEsCandidata = false;
  document.getElementById("ficha-rv").classList.add("hidden");
  renderTenenciasRv();
  renderScreenerRv();
}

function renderFichaCab() {
  if (!fichaTesis) return;
  if (fichaEsCandidata) {
    const fila = (screenerRvData?.filas ?? []).find((x) => x.ticker === fichaTicker);
    document.getElementById("ficha-cab").innerHTML = `
      <div class="acc-ficha-cab-fila">
        <button type="button" class="rebalanceo-guardar" data-accion="volver">← Volver</button>
        <span class="acc-ficha-ticker">${escapeAttr(fichaTicker)}</span>
        <span class="acc-chip acc-chip-tipo">Candidata</span>${chipCubeta(fila?.cubeta ?? null)}
      </div>
      <div class="acc-tenue">Candidata de la watchlist (no está en tu cartera): solo valuación. La cubeta sugerida se elige al agregarla.</div>`;
    return;
  }
  const p = posicionFicha();
  const sem = p && p.semaforo ? chipEstado(p.semaforo) : `<span class="acc-est acc-est-sin">Sin semáforo</span>`;
  const cedear = p && p.tipo === "cedear"
    ? `<span class="acc-tenue">Ratio del CEDEAR: sin verificar · Prima vs. subyacente: sin verificar</span>`
    : "";
  document.getElementById("ficha-cab").innerHTML = `
    <div class="acc-ficha-cab-fila">
      <button type="button" class="rebalanceo-guardar" data-accion="volver">← Volver</button>
      <span class="acc-ficha-ticker">${escapeAttr(fichaTicker)}</span>
      ${p ? `<span class="acc-chip acc-chip-tipo">${p.tipo === "cedear" ? "CEDEAR" : "Acción"}</span>${chipCubeta(p.cubeta)}` : ""}
      ${sem}
      ${cedear}
    </div>
    ${p
      ? `<div class="acc-ficha-datos">Valor <strong>${fmtUsdAr(p.valor_usd)}</strong> · <strong>${fmtPctAr(p.pct_patrimonio, 2)}</strong> del patrimonio · <strong>${fmtPctAr(p.pct_rv)}</strong> de la RV · Próxima revisión: <strong>${escapeAttr(fichaTesis.proxima_revision ?? "sin definir")}</strong></div>`
      : `<div class="acc-tenue">Sin posición abierta en este ticker.</div>`}`;
}

function campoTexto(campo, etiqueta, valor, filas = 2) {
  return `<label class="acc-ficha-campo">${etiqueta}
    <textarea rows="${filas}" data-campo="${campo}">${escapeAttr(valor)}</textarea></label>`;
}
function campoNumero(campo, etiqueta, valor, paso = "any", extra = "") {
  return `<label class="acc-ficha-campo">${etiqueta}
    <input type="number" step="${paso}" ${extra} data-campo="${campo}" value="${valor ?? ""}"></label>`;
}
function campoFecha(campo, etiqueta, valor) {
  return `<label class="acc-ficha-campo">${etiqueta}
    <input type="date" data-campo="${campo}" value="${escapeAttr(valor)}"></label>`;
}
function botonGuardarSeccion(seccionId, texto) {
  return `<div class="acc-ficha-acciones">
    <button type="button" class="rebalanceo-guardar" data-accion="guardar-seccion" data-seccion="${seccionId}">${texto}</button>
    <span class="acc-ficha-estado" id="estado-${seccionId}"></span></div>`;
}

function renderFichaTesis() {
  const t = fichaTesis;
  document.getElementById("ficha-tesis").innerHTML = `
    <h3 class="acc-ficha-titulo">Tesis</h3>
    ${campoTexto("tendencia_secular", "Tendencia macro / secular", t.tendencia_secular)}
    ${campoTexto("motivo", "Tesis en 2 frases", t.motivo)}
    ${campoTexto("ventaja_competitiva", "Ventaja competitiva", t.ventaja_competitiva)}
    ${campoTexto("riesgo", "Riesgos principales", t.riesgo)}
    ${campoTexto("pre_mortem", "Pre-mortem: si dentro de 3 años perdí 40 %, ¿por qué?", t.pre_mortem)}
    ${botonGuardarSeccion("ficha-tesis", "Guardar tesis")}`;
}

function renderFichaValuacion() {
  const t = fichaTesis;
  document.getElementById("ficha-valuacion").innerHTML = `
    <h3 class="acc-ficha-titulo">Valuación</h3>
    <div class="acc-ficha-grid2">
      ${campoNumero("valor_intrinseco_min", "Valor intrínseco mínimo (USD)", t.valor_intrinseco_min, "any", 'min="0"')}
      ${campoNumero("valor_intrinseco_max", "Valor intrínseco máximo (USD)", t.valor_intrinseco_max, "any", 'min="0"')}
      ${campoNumero("margen_seguridad_req_pct", "Margen de seguridad requerido (%)", t.margen_seguridad_req_pct, "1", 'min="0" max="100"')}
      ${campoFecha("proxima_revision", "Próxima revisión", t.proxima_revision)}
    </div>
    <p class="eventos-nota">Rango en USD por acción del subyacente, cargado a mano. La comparación contra el precio y las tablas Graham estricto vs. Graham-Plus llegan con los fundamentales (aún sin verificar). Cargar una próxima revisión futura da por revisada la alerta de revisión obligatoria.</p>
    ${botonGuardarSeccion("ficha-valuacion", "Guardar valuación")}`;
}

function renderFichaAlertas() {
  const t = fichaTesis;
  document.getElementById("ficha-alertas").innerHTML = `
    <h3 class="acc-ficha-titulo">Alertas tempranas y ruptura de tesis</h3>
    ${campoTexto("alertas_sumar", "Señales para sumar", t.alertas_sumar)}
    ${campoTexto("alertas_reducir", "Señales para reducir o descartar", t.alertas_reducir)}
    ${campoTexto("criterio_venta", "Ruptura de tesis, con números", t.criterio_venta)}
    <p class="eventos-nota">Revisión obligatoria si la posición cae ~${tenenciasRvData.parametros.revision_obligatoria_caida_pct} % desde la entrada: rehacer el análisis y decidir por escrito. No es una orden de venta.</p>
    ${botonGuardarSeccion("ficha-alertas", "Guardar alertas")}`;
}

function renderFichaAlertasPos() {
  const mias = tenenciasRvData.alertas.filter((a) => a.ticker === fichaTicker);
  document.getElementById("ficha-alertas-pos").innerHTML = mias.length
    ? `<h3 class="acc-ficha-titulo">Alertas de esta posición</h3>${mias.map(alertaHtml).join("")}`
    : `<h3 class="acc-ficha-titulo">Alertas de esta posición</h3><div class="acc-tenue">Sin alertas para esta posición.</div>`;
}

function recolectarCampos(contenedor) {
  const campos = {};
  contenedor.querySelectorAll("[data-campo]").forEach((el) => {
    const v = el.value.trim();
    campos[el.dataset.campo] = v === "" ? null : (el.type === "number" ? parseFloat(v) : v);
  });
  return campos;
}

async function guardarSeccionFicha(seccionId) {
  const ticker = fichaTicker;
  const estado = document.getElementById(`estado-${seccionId}`);
  estado.className = "acc-ficha-estado";
  estado.textContent = "Guardando...";
  try {
    await apiJson(`/api/tesis/${encodeURIComponent(ticker)}`, "POST", recolectarCampos(document.getElementById(seccionId)));
    const tesis = await apiJson(`/api/tesis/${encodeURIComponent(ticker)}`);
    if (fichaTicker !== ticker) return;
    fichaTesis = tesis;
    estado.textContent = "Guardado ✓";
    await cargarAccionesRv(); // KPIs, tabla, alertas (y cabecera de la ficha)
  } catch (err) {
    estado.className = "acc-ficha-estado acc-ficha-estado-error";
    estado.textContent = `No se pudo guardar: ${err.message}`;
  }
}

// ---- Indicadores ("que tiene que seguir siendo verdad") ----------------------

function textoUmbrales(i) {
  const v = fmtCantidad(i.umbral_verde);
  const r = fmtCantidad(i.umbral_rojo);
  return i.direccion === "mayor_es_mejor"
    ? { verde: `≥ ${v}`, amarillo: `${r} – ${v}`, rojo: `≤ ${r}` }
    : { verde: `≤ ${v}`, amarillo: `${v} – ${r}`, rojo: `≥ ${r}` };
}

function formIndicador(prefijo, i = null) {
  const dir = i?.direccion ?? "mayor_es_mejor";
  return `
    <div class="acc-editor" data-form="${prefijo}" data-id="${i?.id ?? ""}">
      <label>Nombre <input type="text" data-campo="nombre" value="${escapeAttr(i?.nombre)}" placeholder="Ej. ROIC"></label>
      <label>Fuente <input type="text" data-campo="fuente" value="${escapeAttr(i?.fuente)}" placeholder="Ej. balance"></label>
      <label>Frecuencia <input type="text" data-campo="frecuencia" value="${escapeAttr(i?.frecuencia)}" placeholder="Ej. trimestral"></label>
      <label>Dirección
        <select data-campo="direccion">
          <option value="mayor_es_mejor"${dir === "mayor_es_mejor" ? " selected" : ""}>Mayor es mejor</option>
          <option value="menor_es_mejor"${dir === "menor_es_mejor" ? " selected" : ""}>Menor es mejor</option>
        </select>
      </label>
      <label>Umbral verde <input type="number" step="any" data-campo="umbral_verde" value="${i?.umbral_verde ?? ""}"></label>
      <label>Umbral rojo <input type="number" step="any" data-campo="umbral_rojo" value="${i?.umbral_rojo ?? ""}"></label>
    </div>`;
}

function filaIndicador(i) {
  const u = textoUmbrales(i);
  const hoy = i.ultima
    ? `${chipEstado(i.estado_actual)} <strong>${fmtCantidad(i.ultima.valor)}</strong> <span class="acc-tenue">${i.ultima.fecha}</span>`
    : chipEstado(null);
  const arch = i.activo ? "" : ` <span class="acc-alerta-etiqueta">archivado</span>`;
  const btn = (accion, txt) => `<button type="button" class="acc-mini-btn" data-accion="${accion}" data-id="${i.id}">${txt}</button>`;
  let html = `
    <tr class="${i.activo ? "" : "acc-ind-archivado"}">
      <td><strong>${escapeAttr(i.nombre)}</strong>${arch}</td>
      <td class="acc-tenue">${escapeAttr(i.fuente) || "—"}</td>
      <td class="acc-tenue">${escapeAttr(i.frecuencia) || "—"}</td>
      <td>${u.verde}</td><td>${u.amarillo}</td><td>${u.rojo}</td>
      <td>${hoy}</td>
      <td class="acc-ind-acciones">${btn("toggle-lecturas", "Lecturas")}${btn("editar", "Editar")}${i.activo ? btn("archivar", "Archivar") : btn("restaurar", "Restaurar")}${btn("borrar", "Borrar")}</td>
    </tr>`;
  if (fichaIndAbierto === i.id) {
    const hist = i.historial.length
      ? `<table class="acc-ind-hist-tabla"><thead><tr><th>Fecha</th><th>Valor</th><th>Estado</th><th></th></tr></thead><tbody>
          ${i.historial.map((h) => `<tr><td>${h.fecha}</td><td>${fmtCantidad(h.valor)}</td><td>${chipEstado(h.estado)}</td>
            <td><button type="button" class="acc-mini-btn" data-accion="borrar-lectura" data-id="${i.id}" data-fecha="${h.fecha}" title="Borrar esta lectura">✕</button></td></tr>`).join("")}
        </tbody></table>`
      : `<div class="acc-tenue">Sin lecturas todavía.</div>`;
    html += `
      <tr class="acc-ind-detalle"><td colspan="8">
        ${hist}
        <div class="acc-editor" data-form="lectura" data-id="${i.id}">
          <label>Fecha <input type="date" data-campo="fecha" value="${hoyLocalISO()}" max="${hoyLocalISO()}"></label>
          <label>Valor <input type="number" step="any" data-campo="valor"></label>
          <button type="button" class="rebalanceo-guardar" data-accion="guardar-lectura" data-id="${i.id}">Guardar lectura</button>
          <span class="acc-tenue">Una lectura por fecha: cargar la misma fecha la corrige.</span>
        </div>
      </td></tr>`;
  }
  if (fichaIndEditando === i.id) {
    html += `
      <tr class="acc-ind-detalle"><td colspan="8">
        ${formIndicador("edicion", i)}
        <div class="acc-editor">
          <button type="button" class="rebalanceo-guardar" data-accion="guardar-edicion" data-id="${i.id}">Guardar cambios</button>
          <button type="button" class="acc-mini-btn" data-accion="cancelar-edicion">Cancelar</button>
          <span class="acc-tenue">Si cambiás la dirección o los umbrales, se recalcula el estado de todas las lecturas.</span>
        </div>
      </td></tr>`;
  }
  return html;
}

function renderFichaIndicadores() {
  const filas = fichaIndicadores.map(filaIndicador).join("");
  document.getElementById("ficha-indicadores").innerHTML = `
    <h3 class="acc-ficha-titulo">Qué tiene que seguir siendo verdad</h3>
    <div class="acc-ficha-error" id="ficha-ind-error"></div>
    ${fichaIndicadores.length
      ? `<div class="acc-tabla-scroll"><table class="eventos-tabla acc-ind-tabla">
          <thead><tr><th>Indicador</th><th>Fuente</th><th>Frecuencia</th><th>Verde</th><th>Amarillo</th><th>Rojo</th><th>Hoy</th><th></th></tr></thead>
          <tbody>${filas}</tbody></table></div>`
      : `<div class="acc-tenue">Sin indicadores. Cargá 3 a 5 que, si dejan de cumplirse, rompen la tesis.</div>`}
    ${fichaFormNuevo
      ? `<div class="acc-ind-nuevo">${formIndicador("nuevo")}
          <div class="acc-editor">
            <button type="button" class="rebalanceo-guardar" data-accion="guardar-nuevo">Agregar</button>
            <button type="button" class="acc-mini-btn" data-accion="cancelar-nuevo">Cancelar</button>
          </div></div>`
      : `<button type="button" class="rebalanceo-guardar acc-ind-agregar" data-accion="nuevo">+ Agregar indicador</button>`}
    <p class="eventos-nota">Dos lecturas seguidas en amarillo → frenar aportes. Una lectura roja → reducir de forma escalonada, salvo que documentes por escrito por qué la tesis sigue viva: escribí una entrada de esta posición en la bitácora de tesis (abajo) con fecha igual o posterior a la lectura.</p>`;
}

async function recargarIndicadoresFicha() {
  const ticker = fichaTicker;
  const lista = await apiJson(`/api/tesis-indicadores?ticker=${encodeURIComponent(ticker)}`);
  if (fichaTicker !== ticker) return;
  fichaIndicadores = lista;
  renderFichaIndicadores();
}

async function operarIndicador(operacion) {
  try {
    await operacion();
    await recargarIndicadoresFicha();
    await cargarAccionesRv(); // semaforo de la tabla, KPIs y alertas
  } catch (err) {
    const el = document.getElementById("ficha-ind-error");
    if (el) el.textContent = err.message;
  }
}

function datosFormIndicador(prefijo, id = "") {
  const cont = document.querySelector(`[data-form="${prefijo}"]${id ? `[data-id="${id}"]` : ""}`);
  const c = recolectarCampos(cont);
  if (!c.nombre) throw new Error("Poné un nombre para el indicador.");
  if (c.umbral_verde === null || c.umbral_rojo === null || Number.isNaN(c.umbral_verde) || Number.isNaN(c.umbral_rojo)) {
    throw new Error("Completá el umbral verde y el rojo.");
  }
  return c;
}

function manejarClickFicha(e) {
  const btn = e.target.closest("[data-accion]");
  if (!btn) return;
  const id = btn.dataset.id ? Number(btn.dataset.id) : null;
  switch (btn.dataset.accion) {
    case "volver": cerrarFicha(); break;
    case "refrescar-uno": refrescarFundamentales(fichaTicker, document.getElementById("ficha-valuacion-estado")); break;
    case "guardar-seccion": guardarSeccionFicha(btn.dataset.seccion); break;
    case "nuevo": fichaFormNuevo = true; renderFichaIndicadores(); break;
    case "cancelar-nuevo": fichaFormNuevo = false; renderFichaIndicadores(); break;
    case "guardar-nuevo":
      operarIndicador(async () => {
        const c = datosFormIndicador("nuevo");
        await apiJson("/api/tesis-indicadores", "POST", { ticker: fichaTicker, ...c });
        fichaFormNuevo = false;
      });
      break;
    case "toggle-lecturas":
      fichaIndAbierto = fichaIndAbierto === id ? null : id;
      fichaIndEditando = null;
      renderFichaIndicadores();
      break;
    case "editar": fichaIndEditando = id; fichaIndAbierto = null; renderFichaIndicadores(); break;
    case "cancelar-edicion": fichaIndEditando = null; renderFichaIndicadores(); break;
    case "guardar-edicion":
      operarIndicador(async () => {
        await apiJson(`/api/tesis-indicadores/${id}`, "PUT", datosFormIndicador("edicion", id));
        fichaIndEditando = null;
      });
      break;
    case "archivar":
    case "restaurar":
      operarIndicador(() => apiJson(`/api/tesis-indicadores/${id}`, "PUT", { activo: btn.dataset.accion === "restaurar" }));
      break;
    case "borrar": {
      const ind = fichaIndicadores.find((x) => x.id === id);
      if (!confirm(`¿Borrar el indicador "${ind?.nombre}" y todas sus lecturas? Esto no se puede deshacer (si solo querés sacarlo de la cuenta, archivalo).`)) break;
      operarIndicador(async () => {
        await apiJson(`/api/tesis-indicadores/${id}`, "DELETE");
        if (fichaIndAbierto === id) fichaIndAbierto = null;
        if (fichaIndEditando === id) fichaIndEditando = null;
      });
      break;
    }
    case "guardar-lectura":
      operarIndicador(async () => {
        const c = recolectarCampos(document.querySelector(`[data-form="lectura"][data-id="${id}"]`));
        if (!c.fecha || c.valor === null || Number.isNaN(c.valor)) throw new Error("Completá la fecha y el valor de la lectura.");
        await apiJson(`/api/tesis-indicadores/${id}/historial`, "POST", { fecha: c.fecha, valor: c.valor });
      });
      break;
    case "borrar-lectura":
      operarIndicador(() => apiJson(`/api/tesis-indicadores/${id}/historial/${encodeURIComponent(btn.dataset.fecha)}`, "DELETE"));
      break;
  }
}

document.getElementById("ficha-rv").addEventListener("click", manejarClickFicha);

// ============================================================
// Fase 3 (2026-09-21): estado de valor, valuacion Graham en la ficha, screener
// y watchlist. TODO lo calcula el servidor desde la base (graham_rv.py /
// screener_rv.py) -- el navegador nunca le pega a Yahoo: solo el boton
// "Actualizar fundamentales" dispara la descarga (POST), a demanda. Un dato que
// falta se muestra "sin verificar", nunca como un "pasa".
// ============================================================

const ETIQUETA_VALOR = {
  infravalorada: "Infravalorada", valor_justo: "Valor justo", sobrevalorada: "Sobrevalorada",
  sin_verificar: "sin verificar", no_aplica: "no aplica",
};
const SIMBOLO_GATE = { pasa: "✓", no_pasa: "✕", sin_verificar: "?", no_aplica: "–" };
const TEXTO_GATE = { pasa: "pasa", no_pasa: "no pasa", sin_verificar: "sin verificar", no_aplica: "no aplica" };

function chipEstadoValor(estado) {
  if (!estado) return `<span class="acc-tenue">—</span>`;
  return `<span class="acc-vs acc-vs-${estado}">${ETIQUETA_VALOR[estado] ?? escapeAttr(estado)}</span>`;
}

function fmtDato(clave, v) {
  if (v === null || v === undefined) return "—";
  if (clave === "roic" || clave === "fcf_yield") return `${fmtNum(v, 1)} %`;
  if (clave === "dividendos") return `${v} años`;
  return fmtNum(v, 2);
}

// Tooltip de una compuerta: solo lo que NO pasa (lo que hay que mirar).
function tooltipGate(gate, nombre) {
  const lineas = [`${nombre}: ${TEXTO_GATE[gate.estado]}`];
  for (const c of gate.criterios ?? []) {
    if (c.estado === "pasa" || c.estado === "no_aplica") continue;
    lineas.push(`• ${c.nombre} (${c.umbral}): ${TEXTO_GATE[c.estado]}`
      + (c.valor !== null && c.valor !== undefined ? ` · dato ${fmtDato(c.clave, c.valor)}` : "")
      + (c.detalle ? ` — ${c.detalle}` : ""));
  }
  if (gate.nota) lineas.push(gate.nota);
  if (gate.recordatorio) lineas.push(gate.recordatorio);
  return lineas.join("\n");
}

function chipGate(gate, nombre) {
  return `<span class="acc-gate acc-gate-${gate.estado}" title="${escapeAttr(tooltipGate(gate, nombre))}">${SIMBOLO_GATE[gate.estado]}</span>`;
}

// ---- Screener ---------------------------------------------------------------

function renderScreenerRv() {
  const d = screenerRvData;
  if (!d) return;
  const nCartera = d.filas.filter((f) => f.origen === "cartera").length;
  const nCand = d.filas.length - nCartera;
  document.getElementById("screener-rv-subtitulo").textContent =
    `${nCartera} posiciones · ${nCand} candidata${nCand === 1 ? "" : "s"} · ${d.con_margen} con margen de seguridad calculado`
    + (d.sin_fundamentales.length ? ` · sin fundamentales: ${d.sin_fundamentales.join(", ")}` : "");

  document.getElementById("screener-body").innerHTML = d.filas
    .map((x) => {
      const t = escapeAttr(x.ticker);
      const esCand = x.origen === "watchlist";
      const g = x.gates;
      return `
        <tr data-ticker="${t}" data-origen="${x.origen}" class="${fichaTicker === x.ticker ? "acc-fila-sel" : ""}">
          <td><strong>${t}</strong><span class="acc-chip acc-chip-tipo">${esCand ? "Candidata" : (x.tipo === "cedear" ? "CEDEAR" : "Acción")}</span></td>
          <td>${chipCubeta(x.cubeta)}</td>
          <td>${chipGate(g.c0, "C0 Descartes")}</td>
          <td>${chipGate(g.c1, "C1 Calidad")}</td>
          <td>${chipGate(g.c2, "C2 Precio")}</td>
          <td>${chipGate(g.c3, "C3 Encaje")}</td>
          <td title="${escapeAttr(x.motivo_valor ?? "")}">${chipEstadoValor(x.estado_valor)}</td>
          <td class="eventos-monto">${x.margen_pct !== null && x.margen_pct !== undefined ? fmtNum(x.margen_pct, 1) + " %" : "—"}</td>
          <td>${esCand ? `<button type="button" class="acc-mini-btn" data-quitar="${t}">Quitar</button>` : ""}</td>
        </tr>`;
    })
    .join("");

  document.getElementById("screener-top5").innerHTML = d.top5.length
    ? `<ol class="acc-top5">${d.top5.map((x) => `
        <li data-ticker="${escapeAttr(x.ticker)}" data-origen="${x.origen}">
          <strong>${escapeAttr(x.ticker)}</strong> ${chipEstadoValor(x.estado_valor)}
          <span class="acc-top5-margen">${fmtNum(x.margen_pct, 1)} %</span>
        </li>`).join("")}</ol>
       <p class="eventos-nota">Margen = descuento del precio contra el valor intrínseco conservador (negativo = precio por encima).</p>`
    : `<div class="acc-tenue">Ningún ticker tiene margen calculado todavía. Cargá un rango de valor intrínseco en la ficha, o asigná la cubeta Valor puro (usa el Número de Graham).</div>`;
}

async function quitarCandidata(ticker) {
  if (!confirm(`¿Sacar ${ticker} de la watchlist? Se borran también sus fundamentales cargados.`)) return;
  try {
    await apiJson(`/api/watchlist-rv/${encodeURIComponent(ticker)}`, "DELETE");
    if (fichaTicker === ticker) cerrarFicha();
    await cargarAccionesRv();
  } catch (err) {
    document.getElementById("watchlist-error").textContent = err.message;
  }
}

// Un solo refresco a la vez (el servidor tambien lo garantiza con un 409).
let fundamentalesActualizando = false;

async function refrescarFundamentales(ticker, estadoEl) {
  if (fundamentalesActualizando) return;
  fundamentalesActualizando = true;
  const botones = document.querySelectorAll("#fundamentales-actualizar-btn, #watchlist-agregar-btn, [data-accion=\"refrescar-uno\"]");
  botones.forEach((b) => { b.disabled = true; });
  const globalEl = document.getElementById("fundamentales-estado");
  const destinos = [...new Set([estadoEl, globalEl].filter(Boolean))];
  const escribir = (txt, error = false) => destinos.forEach((el) => {
    el.textContent = txt;
    el.classList.toggle("acc-ficha-estado-error", error);
  });
  escribir(ticker ? `Descargando ${ticker} de Yahoo...` : "Descargando de Yahoo, unos 2 minutos (podés seguir usando la app)...");
  try {
    const r = await apiJson(`/api/refrescar-fundamentales-rv${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ""}`, "POST");
    const ultima = (r.stdout ?? "").split("\n").filter((l) => l.startsWith("OK:")).pop();
    escribir(ultima ?? "Listo.");
    fundamentalesActualizando = false;
    await cargarAccionesRv();
  } catch (err) {
    escribir(`No se pudo actualizar: ${err.message}`, true);
  } finally {
    fundamentalesActualizando = false;
    document.querySelectorAll("#fundamentales-actualizar-btn, #watchlist-agregar-btn, [data-accion=\"refrescar-uno\"]")
      .forEach((b) => { b.disabled = false; });
  }
}

// `tickerOverride`/`cubetaOverride`: reusado por el Simulador de compra
// ("Agregar a watchlist" / "Correr screener sobre esta candidata") sin leer
// del formulario del panel del screener. `silencioso`: no escribe en
// #watchlist-error ni limpia el input -- si el candidato YA esta en la
// watchlist (caso esperable al abrir el screener sobre el mismo ticker
// simulado 2 veces) no es un error, se ignora; cualquier otro fallo se
// relanza para que el caller (el simulador) decida como mostrarlo.
async function agregarCandidata(tickerOverride, cubetaOverride, silencioso = false) {
  const errEl = document.getElementById("watchlist-error");
  const entrada = document.getElementById("watchlist-ticker");
  const ticker = (tickerOverride ?? entrada.value).trim();
  if (!silencioso) errEl.textContent = "";
  if (!ticker) {
    if (!silencioso) errEl.textContent = "Escribí el ticker de Yahoo (ej. BKNG, o YPFD.BA para una acción local).";
    return;
  }
  try {
    const r = await apiJson("/api/watchlist-rv", "POST", {
      ticker, cubeta_sugerida: cubetaOverride ?? (document.getElementById("watchlist-cubeta").value || null),
    });
    if (tickerOverride === undefined) entrada.value = "";
    await cargarAccionesRv();
    await refrescarFundamentales(r.ticker, null); // trae sus fundamentales apenas se agrega
  } catch (err) {
    if (silencioso && err.message.includes("ya está en la watchlist")) return;
    if (silencioso) throw err;
    errEl.textContent = err.message;
  }
}

document.getElementById("watchlist-agregar-btn").addEventListener("click", agregarCandidata);
document.getElementById("watchlist-ticker").addEventListener("keydown", (e) => { if (e.key === "Enter") agregarCandidata(); });
document.getElementById("fundamentales-actualizar-btn").addEventListener("click", () => refrescarFundamentales(null, null));
document.getElementById("screener-body").addEventListener("click", (e) => {
  const quitar = e.target.closest("[data-quitar]");
  if (quitar) { quitarCandidata(quitar.dataset.quitar); return; }
  const fila = e.target.closest("tr[data-ticker]");
  if (fila) abrirFicha(fila.dataset.ticker, fila.dataset.origen === "watchlist");
});
document.getElementById("screener-top5").addEventListener("click", (e) => {
  const li = e.target.closest("li[data-ticker]");
  if (li) abrirFicha(li.dataset.ticker, li.dataset.origen === "watchlist");
});

// ---- Valuacion Graham en la ficha ---------------------------------------------

async function cargarValuacionFicha() {
  const ticker = fichaTicker;
  if (!ticker) return;
  try {
    const v = await apiJson(`/api/valuacion-rv/${encodeURIComponent(ticker)}`);
    if (fichaTicker !== ticker) return;
    fichaValuacion = v;
    renderFichaValuacionAuto();
  } catch (err) {
    document.getElementById("ficha-valuacion-auto").innerHTML =
      `<h3 class="acc-ficha-titulo">Valuación</h3><div class="acc-ficha-estado-error">${escapeAttr(err.message)}</div>`;
  }
}

const ORDEN_CRITERIOS = ["per", "per_hist", "per_forward", "pb", "per_pb", "current_ratio", "deuda_lp",
  "nd_ebitda", "cobertura", "ganancias", "dividendos", "roic", "fcf_yield"];

function chipVeredicto(v) {
  return `<span class="acc-gate acc-gate-${v} acc-gate-texto">${TEXTO_GATE[v]}</span>`;
}

function celdaCriterio(c) {
  if (!c) return `<span class="acc-tenue">—</span>`;
  const tit = c.detalle ? ` title="${escapeAttr(c.detalle)}"` : "";
  return `<span class="acc-crit"${tit}><span class="acc-crit-umbral">${escapeAttr(c.umbral)}</span>
    <span class="acc-gate acc-gate-${c.estado}">${SIMBOLO_GATE[c.estado]}</span>${!c.bloquea && c.estado !== "no_aplica" ? `<span class="acc-tenue"> (informativo)</span>` : ""}</span>`;
}

function barraValorIntrinseco(va) {
  const puntos = [va.precio_usd, va.vi_conservador, va.precio_maximo_compra, va.manual_max, va.graham].filter((x) => x !== null && x !== undefined);
  if (va.precio_usd === null || va.precio_usd === undefined || va.vi_conservador === null || va.vi_conservador === undefined) return "";
  const escala = Math.max(...puntos) * 1.12;
  const pos = (x) => Math.min(100, Math.max(0, (x / escala) * 100));
  const rango = va.manual_min !== null && va.manual_max !== null
    ? `<span class="acc-vi-rango" style="left:${pos(va.manual_min)}%;width:${Math.max(0.8, pos(va.manual_max) - pos(va.manual_min))}%" title="Rango manual: ${fmtNum(va.manual_min, 2)} – ${fmtNum(va.manual_max, 2)}"></span>`
    : "";
  return `<div class="acc-vi-barra">
      ${rango}
      <span class="acc-vi-marca acc-vi-max" style="left:${pos(va.precio_maximo_compra)}%" title="Máximo para comprar"></span>
      <span class="acc-vi-marca acc-vi-vi" style="left:${pos(va.vi_conservador)}%" title="Valor intrínseco conservador"></span>
      <span class="acc-vi-marca acc-vi-precio" style="left:${pos(va.precio_usd)}%" title="Precio actual"></span>
    </div>
    <div class="acc-vi-leyenda"><span class="acc-vi-l acc-vi-l-precio">Precio</span><span class="acc-vi-l acc-vi-l-max">Máx. para comprar</span><span class="acc-vi-l acc-vi-l-vi">Valor intrínseco</span><span class="acc-vi-l acc-vi-l-rango">Rango manual</span></div>`;
}

function renderFichaValuacionAuto() {
  const v = fichaValuacion;
  const cont = document.getElementById("ficha-valuacion-auto");
  if (!v) return;
  const titulo = `<h3 class="acc-ficha-titulo">Valuación: Graham estricto vs. Graham-Plus</h3>`;
  const boton = `<div class="acc-ficha-acciones"><button type="button" class="acc-mini-btn" data-accion="refrescar-uno">Actualizar fundamentales de ${escapeAttr(v.ticker)}</button><span class="acc-ficha-estado" id="ficha-valuacion-estado"></span></div>`;
  if (!v.tiene_fundamentales) {
    cont.innerHTML = `${titulo}<div class="acc-tenue">${escapeAttr(v.motivo ?? "Sin fundamentales cargados.")}</div>${boton}`;
    return;
  }
  if (!v.estricto) {
    cont.innerHTML = `${titulo}<div class="acc-tenue">${escapeAttr(v.motivo ?? "Sin datos de empresa.")}</div>`;
    return;
  }
  const est = Object.fromEntries(v.estricto.criterios.map((c) => [c.clave, c]));
  const plus = Object.fromEntries(v.plus.criterios.map((c) => [c.clave, c]));
  const filas = ORDEN_CRITERIOS.filter((k) => est[k] || plus[k]).map((k) => {
    const nombre = (est[k] ?? plus[k]).nombre;
    const valor = (plus[k]?.valor ?? est[k]?.valor ?? null);
    return `<tr><td>${escapeAttr(nombre)}</td><td class="eventos-monto">${fmtDato(k, valor)}</td><td>${celdaCriterio(est[k])}</td><td>${celdaCriterio(plus[k])}</td></tr>`;
  }).join("");
  const cubetaTxt = v.cubeta ? ` (${CUBETAS_UI[v.cubeta] ?? v.cubeta})` : "";
  const nSinVerificar = Object.keys(v.campos_no_verificados ?? {}).filter((k) => !k.startsWith("_")).length;

  const va = v.valuacion;
  const usd = (x) => (x === null || x === undefined ? "—" : `USD ${fmtNum(x, 2)}`);
  const origenVi = va.vi_origen === "graham" ? "Número de Graham" : va.vi_origen === "manual" ? "rango manual (mínimo)" : "";
  const estados = [
    ["infravalorada", "Infravalorada: precio ≤ valor intrínseco × (1 − margen requerido)"],
    ["valor_justo", `Valor justo: hasta el 110 % del valor intrínseco`],
    ["sobrevalorada", "Sobrevalorada: más del 110 % del valor intrínseco"],
  ];
  cont.innerHTML = `${titulo}
    <div class="acc-val-veredictos">
      <span>Graham estricto: ${chipVeredicto(v.estricto.veredicto)}</span>
      <span>Graham-Plus${escapeAttr(cubetaTxt)}: ${chipVeredicto(v.plus.veredicto)}</span>
    </div>
    <div class="acc-tabla-scroll"><table class="eventos-tabla acc-val-tabla">
      <thead><tr><th>Métrica</th><th style="text-align:right">Dato</th><th>Estricto</th><th>Graham-Plus</th></tr></thead>
      <tbody>${filas}</tbody></table></div>
    ${v.plus.nota ? `<p class="eventos-nota">${escapeAttr(v.plus.nota)}</p>` : ""}
    <p class="eventos-nota">Dato: ${escapeAttr(v.fuente ?? "—")} · ${escapeAttr(v.fecha_dato ?? "sin fecha")}${v.moneda_cotizacion ? ` · cotiza en ${escapeAttr(v.moneda_cotizacion)}, estados en ${escapeAttr(v.moneda_estados ?? "?")}` : ""}${nSinVerificar ? ` · ${nSinVerificar} campo${nSinVerificar === 1 ? "" : "s"} sin verificar (pasá el mouse por cada criterio)` : ""}.
      "Graham estricto" no puede dar "pasa": Yahoo no entrega 10 años de estados ni la deuda de largo plazo.</p>
    ${(v.notas ?? []).map((n) => `<p class="eventos-nota acc-nota-aviso">${escapeAttr(n)}</p>`).join("")}

    <h3 class="acc-ficha-titulo acc-screener-sep">Valor intrínseco y margen de seguridad</h3>
    <div class="acc-val-estado">${chipEstadoValor(va.estado)}${va.motivo ? `<span class="acc-tenue"> ${escapeAttr(va.motivo)}</span>` : ""}</div>
    ${barraValorIntrinseco(va)}
    <ul class="acc-val-lista">
      <li>Precio del subyacente: <strong>${usd(va.precio_usd)}</strong></li>
      <li>Valor intrínseco conservador: <strong>${usd(va.vi_conservador)}</strong>${origenVi ? ` <span class="acc-tenue">(${origenVi})</span>` : ""}</li>
      <li>Margen requerido: <strong>${va.margen_req_pct !== null && va.margen_req_pct !== undefined ? fmtNum(va.margen_req_pct, 0) + " %" : "—"}</strong>${va.margen_req_origen ? ` <span class="acc-tenue">(de la ${va.margen_req_origen})</span>` : ""}</li>
      <li>Máximo para comprar: <strong>${usd(va.precio_maximo_compra)}</strong></li>
      <li>Margen de seguridad actual: <strong>${va.margen_actual_pct !== null && va.margen_actual_pct !== undefined ? fmtNum(va.margen_actual_pct, 1) + " %" : "—"}</strong></li>
    </ul>
    <p class="eventos-nota"><strong>Métodos.</strong> Número de Graham √(22,5 × EPS × BVPS): ${va.graham !== null && va.graham !== undefined ? usd(va.graham) : "no calculable (EPS o BVPS no verificados)"} — solo se usa como valor intrínseco en la cubeta Valor puro (en un negocio liviano daría siempre "sobrevalorada"). Rango manual: ${va.manual_min !== null && va.manual_min !== undefined ? `${usd(va.manual_min)} – ${usd(va.manual_max)}` : "sin cargar"}. Crecimiento de Graham, DCF y múltiplo de salida no se automatizan: cargá su resultado en el rango manual de abajo. Se trabaja con el más conservador.</p>
    <ul class="acc-ficha-reglas acc-val-estados">${estados.map(([k, t]) => `<li class="${va.estado === k ? "acc-val-actual" : ""}">${chipEstadoValor(k)} ${t}</li>`).join("")}</ul>
    ${boton}`;
}

// ============================================================
// Fase 4 (2026-09-22): correlacion/riesgo de RV (retornos SEMANALES,
// subyacente USD para CEDEARs, MEP para acciones locales -- reemplaza el
// viejo panel diario en USD MEP de la Epica 11) + Simulador de compra.
// Ambos son herramientas de DIAGNOSTICO: nunca dicen comprar/vender.
// ============================================================

let riesgoRvData = null;

// Escala de color del mapa de calor: ambar (positivo) o azul (negativo),
// intensidad proporcional a |correlacion| -- NO es la paleta de
// ganancia/perdida (--positivo/--negativo son solo para signo de resultado,
// nunca para esto).
function colorCorrelacion(v) {
  if (v === null || v === undefined) return "transparent";
  const intensidad = Math.round(Math.min(Math.abs(v), 1) * 65);
  const color = v >= 0 ? "var(--accent)" : "var(--series-3)";
  return `color-mix(in srgb, ${color} ${intensidad}%, var(--surface))`;
}

function renderRiesgoRv() {
  const d = riesgoRvData;
  if (!d) return;
  const vol = d.volatilidad_anual_pct !== null ? `${fmtNum(d.volatilidad_anual_pct, 1)} %` : "—";
  document.getElementById("riesgo-rv-subtitulo").textContent =
    `Volatilidad anual: ${vol} · Diversificación efectiva: ${d.diversificacion_efectiva ?? "—"} · Cobertura del cálculo: ${fmtNum(d.cobertura_valor_pct, 1)} % de la RV`;

  const hm = d.mapa_calor;
  document.getElementById("riesgo-heatmap").innerHTML = (!hm || !hm.tickers.length)
    ? `<div class="acc-tenue">Sin datos suficientes todavía.</div>`
    : `<div class="acc-tabla-scroll"><table class="acc-heatmap"><thead><tr><th></th>${hm.tickers.map((t) => `<th>${escapeAttr(t)}</th>`).join("")}</tr></thead><tbody>${
        hm.tickers.map((fila, i) => `
          <tr><th>${escapeAttr(fila)}</th>${hm.valores[i].map((v, j) => {
            const alta = i !== j && v !== null && v >= d.umbral_redundancia;
            const titulo = v === null ? `${hm.tickers[i]} / ${hm.tickers[j]}: sin datos suficientes en común` : `${hm.tickers[i]} / ${hm.tickers[j]}: ${fmtNum(v, 2)}`;
            return `<td class="acc-heatmap-celda${alta ? " acc-heatmap-alta" : ""}" style="background:${colorCorrelacion(v)}" title="${escapeAttr(titulo)}">${v === null ? "—" : fmtNum(v, 2)}</td>`;
          }).join("")}</tr>`).join("")
      }</tbody></table></div>`;

  document.getElementById("riesgo-pares").innerHTML = d.pares_redundantes.length
    ? d.pares_redundantes.map((p) => `<div class="acc-conc-fila"><span class="acc-conc-nombre">${escapeAttr(p.ticker_a)} / ${escapeAttr(p.ticker_b)}</span><span class="acc-conc-val acc-ambar-txt">${fmtNum(p.correlacion, 2)}</span></div>`).join("")
    : `<div class="acc-tenue">Sin pares por encima del umbral — sin señal de diversificación redundante hoy.</div>`;

  const contrib = Object.entries(d.contribucion_riesgo_pct).sort((a, b) => b[1] - a[1]);
  const maxContrib = Math.max(1, ...contrib.map(([, v]) => Math.abs(v)));
  document.getElementById("riesgo-contribucion").innerHTML = contrib.length
    ? contrib.map(([t, v]) => `
        <div class="acc-conc-fila">
          <span class="acc-conc-nombre">${escapeAttr(t)}</span>
          <span class="acc-barra acc-barra-ancha"><span class="acc-barra-fill" style="width:${Math.min(Math.max(v, 0) / maxContrib * 100, 100)}%"></span></span>
          <span class="acc-conc-val">${fmtNum(v, 1)} %</span>
        </div>`).join("")
    : `<div class="acc-tenue">Sin datos suficientes.</div>`;

  const chips = [];
  if (d.confianza_baja.length) chips.push(`<span class="acc-chip acc-chip-sin" title="Menos de ${d.min_meses_alta_confianza} meses de historial en común">confianza baja: ${d.confianza_baja.join(", ")}</span>`);
  if (d.sin_datos_suficientes.length) chips.push(`<span class="acc-chip acc-chip-sin" title="Menos de ${d.min_semanas_minimo} semanas de historial">sin datos suficientes: ${d.sin_datos_suficientes.join(", ")}</span>`);
  document.getElementById("riesgo-chips").innerHTML = chips.join("") || `<span class="acc-tenue">${escapeAttr(d.base)}</span>`;
}

// ---- Frontera eficiente de RV (Epica 19, Fase 5, 2026-09-23) --------------
// Calculo al vuelo en el backend (GET /api/frontera-rv, no guarda nada). La
// minima varianza es la guia principal; el maximo Sharpe se rotula siempre
// como referencia sensible a retornos historicos.

let fronteraRvData = null;
let fronteraRvTimer = null;
let fronteraRvSeq = 0;

function fronteraEscalaNice(min, max, objetivo = 5) {
  const span = Math.max(max - min, 1e-9);
  const crudo = span / objetivo;
  const mag = Math.pow(10, Math.floor(Math.log10(crudo)));
  const paso = [1, 2, 5, 10].map((m) => m * mag).find((p) => p >= crudo) ?? mag * 10;
  const ini = Math.floor(min / paso) * paso, fin = Math.ceil(max / paso) * paso;
  const ticks = [];
  for (let v = ini; v <= fin + paso * 1e-6; v += paso) ticks.push(Math.round(v / paso) * paso);
  return { ini, fin, ticks };
}

function fmtPpFrontera(v) {
  if (Math.abs(v) < 0.05) return "0,0 pp";   // evita "-0,0 pp"
  return `${v > 0 ? "+" : ""}${fmtNum(v, 1)} pp`;
}

function renderFronteraChart(d) {
  const svg = document.getElementById("frontera-chart");
  const W = 800, H = 380, padL = 54, padR = 18, padT = 14, padB = 46;
  const puntos = [...d.nube, ...d.frontera, d.actual, d.min_var, d.max_sharpe];
  const ex = fronteraEscalaNice(Math.min(...puntos.map((p) => p.sigma_anual_pct)), Math.max(...puntos.map((p) => p.sigma_anual_pct)));
  const ey = fronteraEscalaNice(Math.min(...puntos.map((p) => p.retorno_anual_pct)), Math.max(...puntos.map((p) => p.retorno_anual_pct)));
  const x = (v) => padL + ((v - ex.ini) / (ex.fin - ex.ini)) * (W - padL - padR);
  const y = (v) => padT + (H - padT - padB) - ((v - ey.ini) / (ey.fin - ey.ini)) * (H - padT - padB);
  const f1 = (v) => v.toFixed(1);

  const grillaX = ex.ticks.map((t) => `<line class="frontera-grid" x1="${f1(x(t))}" y1="${padT}" x2="${f1(x(t))}" y2="${H - padB}"></line>`
    + `<text class="frontera-eje-label" x="${f1(x(t))}" y="${H - padB + 14}" text-anchor="middle">${fmtNum(t, 0)}</text>`).join("");
  const grillaY = ey.ticks.map((t) => `<line class="frontera-grid" x1="${padL}" y1="${f1(y(t))}" x2="${W - padR}" y2="${f1(y(t))}"></line>`
    + `<text class="frontera-eje-label" x="${padL - 6}" y="${f1(y(t) + 3)}" text-anchor="end">${fmtNum(t, 0)}</text>`).join("");
  const nube = d.nube.map((p) => `<circle class="frontera-nube" cx="${f1(x(p.sigma_anual_pct))}" cy="${f1(y(p.retorno_anual_pct))}" r="1.7"></circle>`).join("");
  const linea = d.frontera.map((p, i) => `${i === 0 ? "M" : "L"} ${f1(x(p.sigma_anual_pct))} ${f1(y(p.retorno_anual_pct))}`).join(" ");

  const marca = (p, forma, clase, texto, dx, dy, anchor) => {
    const cx = x(p.sigma_anual_pct), cy = y(p.retorno_anual_pct);
    const tip = `${texto}: retorno ${fmtNum(p.retorno_anual_pct, 1)} % · riesgo ${fmtNum(p.sigma_anual_pct, 1)} % · Sharpe ${fmtNum(p.sharpe, 2)}`;
    let shape;
    if (forma === "circulo") shape = `<circle class="${clase}" cx="${f1(cx)}" cy="${f1(cy)}" r="6.5"><title>${escapeAttr(tip)}</title></circle>`;
    else if (forma === "rombo") shape = `<path class="${clase}" d="M ${f1(cx)} ${f1(cy - 8)} L ${f1(cx + 8)} ${f1(cy)} L ${f1(cx)} ${f1(cy + 8)} L ${f1(cx - 8)} ${f1(cy)} Z"><title>${escapeAttr(tip)}</title></path>`;
    else shape = `<path class="${clase}" d="M ${f1(cx)} ${f1(cy - 8)} L ${f1(cx + 8)} ${f1(cy + 6)} L ${f1(cx - 8)} ${f1(cy + 6)} Z"><title>${escapeAttr(tip)}</title></path>`;
    return shape + `<text class="frontera-pt-texto" x="${f1(cx + dx)}" y="${f1(cy + dy)}" text-anchor="${anchor}">${escapeAttr(texto)}</text>`;
  };

  svg.innerHTML = `
    ${grillaX}${grillaY}
    ${nube}
    <path class="frontera-linea" d="${linea}"></path>
    ${marca(d.min_var, "rombo", "frontera-pt-mv", "Mín. varianza", 12, 4, "start")}
    ${marca(d.max_sharpe, "triangulo", "frontera-pt-ms", "Máx. Sharpe (ref.)", 12, 16, "start")}
    ${marca(d.actual, "circulo", "frontera-pt-actual", "Actual", 11, 4, "start")}
    <text class="frontera-eje-titulo" x="${(padL + (W - padL - padR) / 2).toFixed(0)}" y="${H - 6}" text-anchor="middle">Riesgo: volatilidad anual (%)</text>
    <text class="frontera-eje-titulo" transform="rotate(-90)" x="${-(padT + (H - padT - padB) / 2).toFixed(0)}" y="13" text-anchor="middle">Retorno esperado anual (%)</text>
  `;
}

function renderFronteraRv() {
  const d = fronteraRvData;
  if (!d) return;
  document.getElementById("frontera-cuerpo").hidden = false;
  const s = (p) => `σ ${fmtNum(p.sigma_anual_pct, 1)} % · retorno ${fmtNum(p.retorno_anual_pct, 1)} %`;
  document.getElementById("frontera-rv-subtitulo").textContent =
    `Actual: ${s(d.actual)} · Mín. varianza: ${s(d.min_var)} · ${d.n_activos} activos · ventana ${d.ventana.efectiva_sem} sem `
    + `(${fechaCorta(d.ventana.desde)} a ${fechaCorta(d.ventana.hasta)}) · rf ${fmtNum(d.rf.anual_pct, 2)} % (T-Bill 3m)`;

  document.getElementById("frontera-avisos").innerHTML = d.avisos
    .map((a) => `<div class="frontera-aviso">${escapeAttr(a.mensaje)}</div>`).join("");

  renderFronteraChart(d);
  const sw = (forma, clase) => {
    const f = forma === "circulo" ? `<circle class="${clase}" cx="8" cy="8" r="5.5"></circle>`
      : forma === "rombo" ? `<path class="${clase}" d="M 8 1 L 15 8 L 8 15 L 1 8 Z"></path>`
      : forma === "triangulo" ? `<path class="${clase}" d="M 8 1 L 15 14 L 1 14 Z"></path>`
      : forma === "linea" ? `<path class="frontera-linea" d="M 0 8 L 16 8"></path>`
      : `<circle class="frontera-nube" cx="8" cy="8" r="3"></circle>`;
    return `<svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true">${f}</svg>`;
  };
  document.getElementById("frontera-leyenda").innerHTML = [
    `<span>${sw("nube")} ${fmtNum(d.verificacion.n_azar, 0)} carteras al azar (se dibuja una muestra)</span>`,
    `<span>${sw("linea")} Frontera eficiente</span>`,
    `<span>${sw("circulo", "frontera-pt-actual")} Cartera actual</span>`,
    `<span>${sw("rombo", "frontera-pt-mv")} Mín. varianza — ${escapeAttr(d.rotulos.min_var)}</span>`,
    `<span>${sw("triangulo", "frontera-pt-ms")} Máx. Sharpe — ${escapeAttr(d.rotulos.max_sharpe)}</span>`,
  ].join("");

  const celdaDesvio = (v, badge) => badge
    ? `<span class="frontera-badge" title="Desvío mayor a ${fmtNum(d.parametros.desvio_pp, 1)} pp">${fmtPpFrontera(v)}</span>`
    : `<span class="acc-tenue">${fmtPpFrontera(v)}</span>`;
  document.getElementById("frontera-body").innerHTML = d.tabla.map((f) => `
    <tr>
      <td>${escapeAttr(f.ticker)}<span class="acc-chip acc-chip-tipo">${escapeAttr(f.tipo ?? "")}</span></td>
      <td class="frontera-cifra" style="text-align:right">${fmtNum(f.peso_actual_pct, 1)} %</td>
      <td class="frontera-cifra" style="text-align:right">${fmtNum(f.peso_min_var_pct, 1)} %</td>
      <td style="text-align:right">${celdaDesvio(f.desvio_min_var_pp, f.badge_min_var)}</td>
      <td class="frontera-cifra" style="text-align:right">${fmtNum(f.peso_max_sharpe_pct, 1)} %</td>
      <td style="text-align:right">${celdaDesvio(f.desvio_max_sharpe_pp, f.badge_max_sharpe)}</td>
    </tr>`).join("");

  const v = d.verificacion;
  document.getElementById("frontera-nota").textContent =
    `Mínima varianza = guía principal (no usa retornos esperados). Máximo Sharpe = ${d.rotulos.max_sharpe}: `
    + `el retorno esperado es el promedio de los últimos ${d.ventana.efectiva_sem} semanas y un par de activos que subieron mucho lo domina. `
    + `Desvío = peso óptimo − peso actual; se marca si supera ${fmtNum(d.parametros.desvio_pp, 1)} pp. `
    + `Verificado en vivo: optimalidad (KKT) ${v.kkt_min_var_ok && v.kkt_frontera_ok ? "cumplida" : "NO cumplida"}, `
    + `${v.azar_superan_min_var} de ${fmtNum(v.n_azar, 0)} carteras al azar superan a la mínima varianza y ${v.azar_superan_max_sharpe} al máximo Sharpe. `
    + `Herramienta de diagnóstico, no una recomendación.`;
}

async function cargarFronteraRv() {
  const tope = parseFloat(document.getElementById("frontera-tope").value);
  const ventana = parseInt(document.getElementById("frontera-ventana").value, 10);
  const desvio = parseFloat(document.getElementById("frontera-desvio").value);
  const sub = document.getElementById("frontera-rv-subtitulo");
  const avisos = document.getElementById("frontera-avisos");
  const mostrarError = (msg) => {
    document.getElementById("frontera-cuerpo").hidden = true;
    document.getElementById("frontera-nota").textContent = "";
    sub.textContent = "No se pudo calcular.";
    avisos.innerHTML = `<div class="frontera-aviso frontera-aviso-error">${escapeAttr(msg)}</div>`;
  };
  if ([tope, ventana, desvio].some((n) => Number.isNaN(n))) { mostrarError("Completá los tres controles con un número."); return; }
  const seq = ++fronteraRvSeq;
  sub.textContent = "Calculando...";
  let resp, cuerpo = null;
  try {
    resp = await fetch(`/api/frontera-rv?tope_pct=${tope}&ventana_sem=${ventana}&desvio_pp=${desvio}`);
    cuerpo = await resp.json();
  } catch (e) {
    if (seq === fronteraRvSeq) mostrarError("No se pudo consultar la API de la frontera.");
    return;
  }
  if (seq !== fronteraRvSeq) return;   // llego una respuesta vieja, hay una consulta mas nueva en curso
  if (!resp.ok) {
    mostrarError(typeof cuerpo?.detail === "string" ? cuerpo.detail : "Revisá los valores: tope 5–100 %, ventana 52–520 semanas, desvío 0,5–50 pp.");
    return;
  }
  fronteraRvData = cuerpo;
  renderFronteraRv();
}

["frontera-tope", "frontera-ventana", "frontera-desvio"].forEach((id) => {
  document.getElementById(id).addEventListener("input", () => {
    clearTimeout(fronteraRvTimer);
    fronteraRvTimer = setTimeout(cargarFronteraRv, 600);
  });
});

// ---- Simulador de compra (seccion 7 del ticket) ---------------------------

let simuladorResultado = null;

function actualizarSimuladorFuentes() {
  const sel = document.getElementById("simulador-rv-fuente");
  const actual = sel.value;
  sel.innerHTML = [`<option value="aporte_nuevo">Aporte nuevo</option>`]
    .concat((tenenciasRvData?.posiciones ?? []).map((p) =>
      `<option value="${escapeAttr(p.ticker)}">Vender de ${escapeAttr(p.ticker)} (${fmtUsdAr(p.valor_usd)})</option>`))
    .join("");
  if ([...sel.options].some((o) => o.value === actual)) sel.value = actual;
}

function celdaTopes(p) {
  if (p.topes_nuevos.length) return `<span class="acc-ambar-txt">${p.topes_nuevos.map(escapeAttr).join(", ")}</span>`;
  if (p.topes_violados_despues.length) return `<span class="acc-tenue">${p.topes_violados_despues.map(escapeAttr).join(", ")}</span>`;
  return "—";
}

function bloqueCorrelacionSimulador(r) {
  const c = r.correlacion;
  const filas = c.candidato_vs_posiciones;
  return `
    <h3 class="acc-ficha-titulo">Correlación con tu cartera</h3>
    <div class="acc-tenue">Media ponderada contra la RV actual: <strong>${c.media_ponderada_vs_rv_actual !== null ? fmtNum(c.media_ponderada_vs_rv_actual, 2) : "—"}</strong> (umbral de redundancia ${fmtNum(c.umbral_redundancia, 2)})</div>
    ${filas.length ? `<div class="acc-tabla-scroll">${filas.map((x) => `
        <div class="acc-conc-fila">
          <span class="acc-conc-nombre">${escapeAttr(x.ticker)}</span>
          <span class="acc-conc-val${x.correlacion >= c.umbral_redundancia ? " acc-ambar-txt" : ""}">${fmtNum(x.correlacion, 2)}</span>
        </div>`).join("")}</div>` : `<div class="acc-tenue">Sin correlaciones calculables todavía.</div>`}`;
}

function bloquePesosSimulador(r) {
  const filasTabla = r.posiciones.map((p) => `
    <tr class="${p.es_candidato ? "acc-fila-sel" : ""}">
      <td><strong>${escapeAttr(p.ticker)}</strong>${p.es_candidato ? ` <span class="acc-chip acc-chip-tipo">Candidato</span>` : ""}</td>
      <td class="eventos-monto">${fmtNum(p.pct_rv_antes, 1)} %</td>
      <td class="eventos-monto">${fmtNum(p.pct_rv_despues, 1)} %</td>
      <td class="eventos-monto">${p.pct_patrimonio_antes !== null && p.pct_patrimonio_antes !== undefined ? fmtNum(p.pct_patrimonio_antes, 2) + " %" : "—"}</td>
      <td class="eventos-monto">${p.pct_patrimonio_despues !== null && p.pct_patrimonio_despues !== undefined ? fmtNum(p.pct_patrimonio_despues, 2) + " %" : "—"}</td>
      <td>${celdaTopes(p)}</td>
    </tr>`).join("");
  const cubetas = Object.entries(r.cubetas)
    .map(([c, v]) => `<div class="acc-conc-fila"><span class="acc-conc-nombre">${CUBETAS_UI[c] ?? escapeAttr(c)}</span><span class="acc-tenue">${fmtNum(v.pct_antes, 1)} % →</span><span class="acc-conc-val">${fmtNum(v.pct_despues, 1)} %</span></div>`)
    .join("");
  const sectores = Object.entries(r.sectores)
    .map(([s, v]) => `<div class="acc-conc-fila"><span class="acc-conc-nombre acc-conc-sector">${escapeAttr(s)}</span><span class="acc-tenue">${fmtNum(v.pct_antes, 1)} % →</span><span class="acc-conc-val${v.supera_tope_despues ? " acc-ambar-txt" : ""}">${fmtNum(v.pct_despues, 1)} %</span></div>`)
    .join("");
  return `
    <h3 class="acc-ficha-titulo acc-screener-sep">Pesos antes / después</h3>
    <div class="acc-tenue">RV total: ${fmtUsdAr(r.totales.total_rv_antes_usd)} → ${fmtUsdAr(r.totales.total_rv_despues_usd)} · Patrimonio bruto: ${fmtUsdAr(r.totales.patrimonio_antes_usd)} → ${fmtUsdAr(r.totales.patrimonio_despues_usd)}</div>
    <div class="acc-tabla-scroll"><table class="eventos-tabla">
      <thead><tr><th>Ticker</th><th style="text-align:right">% RV antes</th><th style="text-align:right">% RV después</th><th style="text-align:right">% patrim. antes</th><th style="text-align:right">% patrim. después</th><th>Topes</th></tr></thead>
      <tbody>${filasTabla}</tbody></table></div>
    <div class="acc-conc-titulo acc-conc-titulo-sep">Por cubeta</div>${cubetas}
    <div class="acc-conc-titulo acc-conc-titulo-sep">Por sector</div>${sectores || `<div class="acc-tenue">Sin sectores clasificados.</div>`}`;
}

function bloqueRiesgoSimulador(r) {
  const riesgo = r.riesgo;
  const chipConfianza = { baja: `<span class="acc-chip acc-chip-sin">confianza baja</span>`, insuficiente: `<span class="acc-chip acc-chip-sin">historial insuficiente</span>` }[r.confianza] ?? "";
  const v = (x, dec = 1) => (x === null || x === undefined ? "—" : fmtNum(x, dec) + " %");
  return `
    <h3 class="acc-ficha-titulo acc-screener-sep">Riesgo ${chipConfianza}</h3>
    <ul class="acc-val-lista">
      <li>Volatilidad anual: ${v(riesgo.antes.vol_anual_pct)} → ${v(riesgo.despues.vol_anual_pct)}</li>
      <li>Diversificación efectiva: ${riesgo.antes.diversificacion_efectiva ?? "—"} → ${riesgo.despues.diversificacion_efectiva ?? "—"}</li>
      <li>Contribución al riesgo del candidato: ${v(riesgo.contribucion_candidato_pct)}</li>
    </ul>`;
}

function bloqueFronteraSimulador(r) {
  return `<h3 class="acc-ficha-titulo acc-screener-sep">Frontera eficiente</h3><div class="acc-tenue">${escapeAttr(r.frontera.motivo)}</div>`;
}

function bloqueLecturaSimulador(r) {
  return `
    <h3 class="acc-ficha-titulo acc-screener-sep">Lectura</h3>
    ${r.lectura.length ? `<ul class="acc-ficha-reglas">${r.lectura.map((l) => `<li>${escapeAttr(l)}</li>`).join("")}</ul>` : `<div class="acc-tenue">Sin observaciones de las reglas del marco.</div>`}
    <div class="acc-ficha-acciones">
      <button type="button" class="rebalanceo-guardar" id="simulador-rv-agregar-watchlist-btn">Agregar a watchlist</button>
      <button type="button" class="acc-mini-btn" id="simulador-rv-ver-screener-btn">Correr screener sobre esta candidata</button>
      <button type="button" class="acc-mini-btn" id="simulador-rv-guardar-btn">Guardar esta simulación</button>
      <span class="acc-ficha-estado" id="simulador-rv-guardar-estado"></span>
    </div>`;
}

function renderSimuladorResultado() {
  const r = simuladorResultado;
  const cont = document.getElementById("simulador-rv-resultado");
  if (!r) {
    cont.innerHTML = "";
    return;
  }
  cont.innerHTML = bloqueCorrelacionSimulador(r) + bloquePesosSimulador(r) + bloqueRiesgoSimulador(r) + bloqueFronteraSimulador(r) + bloqueLecturaSimulador(r);

  document.getElementById("simulador-rv-agregar-watchlist-btn").addEventListener("click", async (e) => {
    e.target.disabled = true;
    try { await agregarCandidata(r.ticker, r.cubeta); } finally { e.target.disabled = false; }
  });
  document.getElementById("simulador-rv-ver-screener-btn").addEventListener("click", async (e) => {
    const estadoEl = document.getElementById("simulador-rv-estado");
    e.target.disabled = true;
    try {
      await agregarCandidata(r.ticker, r.cubeta, true);
      document.getElementById("panel-screener-rv").classList.remove("panel-colapsado");
      document.getElementById("panel-screener-rv").scrollIntoView({ behavior: "smooth" });
      await abrirFicha(r.ticker, true);
    } catch (err) {
      estadoEl.textContent = `No se pudo abrir en el screener: ${err.message}`;
      estadoEl.classList.add("acc-ficha-estado-error");
    } finally {
      e.target.disabled = false;
    }
  });
  document.getElementById("simulador-rv-guardar-btn").addEventListener("click", async (e) => {
    const estadoEl = document.getElementById("simulador-rv-guardar-estado");
    e.target.disabled = true;
    estadoEl.textContent = "Guardando...";
    try {
      // Persiste EXACTO lo que esta en pantalla (r), sin volver a simular ni
      // tocar la red: endpoint liviano, sin lock, no compite con "Agregar a
      // watchlist" ni con ninguna otra actualizacion en curso.
      await apiJson("/api/simulaciones-compra-rv", "POST", {
        ticker: r.ticker, monto_usd: r.monto_usd, fuente_fondos: r.fuente_fondos, resultado: r,
      });
      estadoEl.textContent = "Guardado ✓";
    } catch (err) {
      estadoEl.textContent = `No se pudo guardar: ${err.message}`;
    } finally {
      e.target.disabled = false;
    }
  });
}

async function simularCompra() {
  const estadoEl = document.getElementById("simulador-rv-estado");
  const ticker = document.getElementById("simulador-rv-ticker").value.trim();
  const monto = parseFloat(document.getElementById("simulador-rv-monto").value);
  const fuente = document.getElementById("simulador-rv-fuente").value;
  const cubeta = document.getElementById("simulador-rv-cubeta").value || null;
  estadoEl.className = "acc-ficha-estado";
  if (!ticker) {
    estadoEl.textContent = "Escribí el ticker de Yahoo del candidato.";
    estadoEl.classList.add("acc-ficha-estado-error");
    return;
  }
  if (!monto || monto <= 0) {
    estadoEl.textContent = "El monto tiene que ser mayor a 0.";
    estadoEl.classList.add("acc-ficha-estado-error");
    return;
  }
  const boton = document.getElementById("simulador-rv-simular-btn");
  boton.disabled = true;
  estadoEl.textContent = "Simulando... (puede tardar unos segundos si el candidato es nuevo)";
  try {
    const r = await apiJson("/api/simular-compra-rv", "POST", { ticker, monto_usd: monto, fuente_fondos: fuente, cubeta });
    simuladorResultado = r;
    estadoEl.textContent = "";
    renderSimuladorResultado();
  } catch (err) {
    estadoEl.textContent = err.message;
    estadoEl.classList.add("acc-ficha-estado-error");
    simuladorResultado = null;
    document.getElementById("simulador-rv-resultado").innerHTML = "";
  } finally {
    boton.disabled = false;
  }
}
document.getElementById("simulador-rv-simular-btn").addEventListener("click", simularCompra);

// ============================================================
// Panel: ¿CEDEAR en pesos o en dolares? (2026-09-24) -- ver
// cedear_moneda.py. Calculo en vivo contra las puntas de data912, no
// guarda nada. Un par sin punta o sospechoso se muestra SIN recomendacion.
// ============================================================

let cedearMonedaData = null;

const CM_REC_A = { pesos: "Comprar en pesos", dolares: "Comprar en dólares", indiferente: "Indiferente" };

function cmSpread(v) {
  if (v === null || v === undefined) return "—";
  if (Math.abs(v) < 0.005) return "≈ 0,00 %";
  return `${v > 0 ? "+" : "−"}${fmtNum(Math.abs(v), 2)} %`;
}

// Color por MONEDA recomendada (entidad, paleta categorica), no verde/rojo:
// ninguna de las dos puntas es "ganancia" o "perdida".
function cmClaseMoneda(rec) {
  if (rec === "pesos" || rec === "pesos_via_mep") return "cm-pesos";
  if (rec === "dolares") return "cm-dolares";
  return "acc-tenue";
}

function cmRecB(e) {
  if (e.recomendacion_b === "pesos_via_mep") return "Vender USD vía MEP → comprar en pesos";
  if (e.recomendacion_b === "dolares") return `Comprar directo en dólares (${escapeAttr(e.especie_usd)})`;
  return "—";
}

function cmMotivoBloqueo(e) {
  if (e.estado === "no_cotiza") return "No cotiza en el panel de CEDEARs de data912.";
  if (e.estado === "sin_especie_d") return "No hay especie en dólares MEP (D) para este CEDEAR.";
  if (e.estado === "sin_punta") {
    const falta = [!(e.ars && e.ars.ask) && "pesos", !(e.usd && e.usd.ask) && "dólares"].filter(Boolean).join(" y ");
    return `Sin punta vendedora en ${falta}: no se evalúa (no se usa el último operado como sustituto).`;
  }
  if (e.estado === "par_sospechoso") {
    return `El dólar implícito (${fmtNum(e.dolar_implicito, 2)}) está ${fmtNum(Math.abs(e.desvio_vs_mep_pct), 1)} % lejos del MEP: `
      + "libro vacío o desactualizado, no se recomienda nada.";
  }
  return "";
}

function cmHora(iso) {
  if (!iso) return "sin hora";
  return `${iso.slice(11, 19)} del ${iso.slice(8, 10)}/${iso.slice(5, 7)}`;
}

function renderCedearMonedaUno(d) {
  const e = d.evaluaciones[0];
  const cont = document.getElementById("cedear-moneda-resultado");
  const precios = `
    <div class="cm-cifras">
      <div><span class="acc-tenue">Punta vendedora ARS (${escapeAttr(e.especie_ars ?? e.ticker)})</span><strong>${e.ars && e.ars.ask ? "$ " + fmtNum(e.ars.ask, 2) : "—"}</strong>${cmCantidad(e.ars)}</div>
      <div><span class="acc-tenue">Punta vendedora USD (${escapeAttr(e.especie_usd ?? "—")})</span><strong>${e.usd && e.usd.ask ? "US$ " + fmtNum(e.usd.ask, 3) : "—"}</strong>${cmCantidad(e.usd)}</div>
      <div><span class="acc-tenue">MEP venta</span><strong>${fmtNum(d.mep.venta, 2)}</strong></div>
      <div><span class="acc-tenue">Dólar implícito del CEDEAR</span><strong>${fmtNum(e.dolar_implicito, 2)}</strong></div>
      <div><span class="acc-tenue">Spread (MEP / implícito − 1)</span><strong class="${cmClaseMoneda(e.recomendacion_a)}">${cmSpread(e.spread_pct)}</strong></div>
    </div>`;
  let recs;
  if (e.estado !== "ok") {
    recs = `<p class="frontera-aviso frontera-aviso-error">${cmMotivoBloqueo(e)}</p>`;
  } else {
    recs = `
      <ul class="cm-recs">
        <li><span class="acc-tenue">A · Tengo pesos y dólares:</span> <strong class="${cmClaseMoneda(e.recomendacion_a)}">${CM_REC_A[e.recomendacion_a]}</strong></li>
        <li><span class="acc-tenue">B · Tengo solo dólares (umbral ${fmtNum(d.umbral_solo_usd_pct, 2)} %):</span> <strong class="${cmClaseMoneda(e.recomendacion_b)}">${cmRecB(e)}</strong></li>
        <li><span class="acc-tenue">C · Tengo solo pesos:</span> <strong class="cm-pesos">Comprar en pesos</strong></li>
      </ul>`;
  }
  cont.innerHTML = cmAvisoHorario(d) + precios + recs + cmBloqueNy(d, e)
    + `<p class="eventos-nota">Dato de data912 a las ${cmHora(d.fecha_dato)} (hora de la respuesta: data912 no informa la hora de cada cotización). MEP de dolarapi.</p>`;
}

function cmAvisoHorario(d) {
  if (!d.fuera_de_horario) return "";
  return `<p class="frontera-aviso frontera-aviso-error">Fuera del horario de BYMA (11 a 17 h, días hábiles): las puntas que quedan en el libro `
    + `suelen ser órdenes viejas o lejanas. El spread, la prima y el control de split no son confiables hasta la próxima rueda.</p>`;
}

// Cantidad disponible en la punta vendedora (q_ask): una punta de 3 nominales
// no sirve para una compra grande aunque el precio sea bueno.
function cmCantidad(p) {
  if (!p || !p.ask || p.q_ask === null || p.q_ask === undefined) return "";
  return `<span class="acc-tenue cm-cantidad">${fmtNum(p.q_ask, 0)} disponibles en la punta</span>`;
}

function cmPrimaTexto(moneda, v) {
  const abs = `${fmtNum(Math.abs(v), 2)} %`;
  if (Math.abs(v) < 0.005) return `En ${moneda} pagás lo mismo que en NY (≈ 0,00 %)`;
  return v > 0 ? `En ${moneda} pagás ${abs} sobre NY` : `En ${moneda} pagás ${abs} menos que en NY`;
}

// Fase 2 (v2.37): comparacion contra Nueva York. Si el ratio parece
// desactualizado (split) la prima NO se muestra.
function cmBloqueNy(d, e) {
  if (d.ny_error) return `<p class="frontera-aviso">${escapeAttr(d.ny_error)}</p>`;
  const n = e.ny;
  if (!n) return "";
  if (n.estado_ny === "sin_ratio") {
    return `<p class="frontera-aviso">Sin ratio cargado para ${escapeAttr(e.ticker)} en la lista de BYMA: no se compara contra NY.</p>`;
  }
  if (n.estado_ny === "sin_precio_ny") {
    return `<p class="frontera-aviso">Sin precio de NY para ${escapeAttr(n.ticker_origen)} (data912 usa_stocks/usa_adrs; yfinance solo para ETFs): no se compara contra NY.</p>`;
  }
  if (n.estado_ny === "sin_punta") {
    return `<p class="frontera-aviso">Sin punta vendedora en pesos: no se compara contra NY.</p>`;
  }
  const fuenteNy = n.panel_ny === "yfinance" ? "yfinance, ETF, demora ~15 min" : `data912 ${escapeAttr(n.panel_ny)}, último operado`;
  const detalle = `NY: ${escapeAttr(n.ticker_origen)} US$ ${fmtNum(n.precio_ny, 2)} (${fuenteNy}) · `
    + `ratio ${escapeAttr(n.ratio_texto)} (BYMA, lista del ${n.fecha_lista.slice(8, 10)}/${n.fecha_lista.slice(5, 7)}/${n.fecha_lista.slice(0, 4)}) · `
    + `CCL ${fmtNum(n.ccl, 2)} (dolarapi)`
    + (n.ccl_implicito === null ? "" : ` · CCL implícito del CEDEAR ${fmtNum(n.ccl_implicito, 2)}`);
  if (n.estado_ny === "ratio_desactualizado") {
    return `<p class="frontera-aviso frontera-aviso-error">Ratio posiblemente desactualizado (¿split?): la lista dice ${escapeAttr(n.ratio_texto)} `
      + `y el mercado implica ${fmtNum(n.ratio_implicito, 2)} (${fmtNum(n.desvio_ratio_pct, 1)} % de diferencia, tope ${fmtNum(d.desvio_max_ratio_pct, 0)} %). `
      + `No se muestra la prima.</p><p class="eventos-nota">${detalle}</p>`;
  }
  const lineas = [`<li>${cmPrimaTexto("pesos", n.prima_ars_pct)} <span class="acc-tenue">(NY implícito US$ ${fmtNum(n.ny_implicito_ars, 2)})</span></li>`];
  lineas.push(n.prima_usd_pct === null
    ? `<li class="acc-tenue">En dólares: sin punta vendedora de la especie D.</li>`
    : `<li>${cmPrimaTexto("dólares", n.prima_usd_pct)} <span class="acc-tenue">(NY implícito US$ ${fmtNum(n.ny_implicito_usd, 2)})</span></li>`);
  return `<ul class="cm-recs cm-ny">${lineas.join("")}</ul><p class="eventos-nota">${detalle}</p>`;
}

function renderCedearMonedaTabla() {
  const d = cedearMonedaData;
  const body = document.getElementById("cedear-moneda-body");
  if (!d.evaluaciones.length) {
    body.innerHTML = `<tr><td colspan="7" class="acc-tenue">No hay CEDEARs en cartera ni en la watchlist.</td></tr>`;
  } else {
    body.innerHTML = d.evaluaciones.map((e) => {
      const ok = e.estado === "ok";
      const recA = ok ? `<span class="${cmClaseMoneda(e.recomendacion_a)}">${CM_REC_A[e.recomendacion_a]}</span>` : `<span class="acc-tenue" title="${escapeAttr(cmMotivoBloqueo(e))}">${escapeAttr(e.estado.replace(/_/g, " "))}</span>`;
      const recB = ok ? `<span class="${cmClaseMoneda(e.recomendacion_b)}">${e.recomendacion_b === "pesos_via_mep" ? "Vía MEP → pesos" : "Dólares"}</span>` : "";
      const origen = e.origen.includes("watchlist") && !e.origen.includes("cartera") ? ` <span class="acc-tenue">(watchlist)</span>` : "";
      return `<tr>
        <td><a href="#" class="cm-ticker" data-ticker="${escapeAttr(e.ticker)}">${escapeAttr(e.ticker)}</a>${origen}</td>
        <td class="frontera-cifra" style="text-align:right">${e.ars && e.ars.ask ? fmtNum(e.ars.ask, 2) : "—"}</td>
        <td class="frontera-cifra" style="text-align:right">${e.usd && e.usd.ask ? fmtNum(e.usd.ask, 3) : "—"}</td>
        <td class="frontera-cifra" style="text-align:right">${fmtNum(e.dolar_implicito, 2)}</td>
        <td class="frontera-cifra ${ok ? cmClaseMoneda(e.recomendacion_a) : "acc-tenue"}" style="text-align:right">${cmSpread(e.spread_pct)}</td>
        <td>${recA}</td>
        <td>${recB}</td>
      </tr>`;
    }).join("");
  }
  document.getElementById("cedear-moneda-nota").textContent =
    (d.fuera_de_horario ? "FUERA DEL HORARIO DE BYMA: puntas no confiables. " : "")
    + `CEDEARs en cartera + watchlist, ordenados por spread (positivo = el CEDEAR en pesos está más barato). `
    + `Dato de data912 a las ${cmHora(d.fecha_dato)} · MEP venta ${fmtNum(d.mep.venta, 2)} (dolarapi). `
    + `Se bloquea sin punta vendedora o con dólar implícito a más de ${fmtNum(d.desvio_max_vs_mep_pct, 0)} % del MEP.`;
  document.getElementById("cedear-moneda-opciones").innerHTML =
    d.evaluaciones.map((e) => `<option value="${escapeAttr(e.ticker)}"></option>`).join("");
  const umbralEl = document.getElementById("cedear-moneda-umbral");
  if (document.activeElement !== umbralEl) umbralEl.value = d.umbral_solo_usd_pct;
}

async function cargarCedearMoneda() {
  const body = document.getElementById("cedear-moneda-body");
  try {
    cedearMonedaData = await apiJson("/api/cedear-moneda");
    renderCedearMonedaTabla();
  } catch (err) {
    body.innerHTML = `<tr><td colspan="7" class="acc-ficha-error">${escapeAttr(err.message)}</td></tr>`;
  }
}

async function evaluarCedearMoneda(tickerForzado) {
  const input = document.getElementById("cedear-moneda-ticker");
  const estadoEl = document.getElementById("cedear-moneda-estado");
  const ticker = (tickerForzado ?? input.value).trim().toUpperCase();
  estadoEl.className = "acc-ficha-estado";
  if (!ticker) {
    estadoEl.textContent = "Escribí el ticker BYMA del CEDEAR en pesos (ej. NFLX).";
    estadoEl.classList.add("acc-ficha-estado-error");
    return;
  }
  input.value = ticker;
  const boton = document.getElementById("cedear-moneda-evaluar-btn");
  boton.disabled = true;
  estadoEl.textContent = "Consultando puntas...";
  try {
    const d = await apiJson(`/api/cedear-moneda/${encodeURIComponent(ticker)}`);
    estadoEl.textContent = "";
    renderCedearMonedaUno(d);
  } catch (err) {
    estadoEl.textContent = err.message;
    estadoEl.classList.add("acc-ficha-estado-error");
    document.getElementById("cedear-moneda-resultado").innerHTML = "";
  } finally {
    boton.disabled = false;
  }
}

async function guardarUmbralCedear() {
  const estadoEl = document.getElementById("cedear-moneda-estado");
  const valor = parseFloat(document.getElementById("cedear-moneda-umbral").value);
  estadoEl.className = "acc-ficha-estado";
  if (Number.isNaN(valor)) {
    estadoEl.textContent = "El umbral tiene que ser un número entre 0 y 100.";
    estadoEl.classList.add("acc-ficha-estado-error");
    return;
  }
  try {
    await apiJson("/api/parametros-rv/umbral_cedear_solo_usd_pct", "PUT", { valor });
    estadoEl.textContent = `Umbral guardado: ${fmtNum(valor, 2)} %.`;
    await cargarCedearMoneda();
    const t = document.getElementById("cedear-moneda-ticker").value.trim();
    if (t && document.getElementById("cedear-moneda-resultado").innerHTML) await evaluarCedearMoneda(t);
  } catch (err) {
    estadoEl.textContent = err.message;
    estadoEl.classList.add("acc-ficha-estado-error");
  }
}

// ============================================================
// Panel: ¿Caro o barato vs. el resto? (Fase 3, 2026-09-25) -- ver
// cedear_caro_barato.py. Desvio del CCL implicito de cada CEDEAR contra la
// mediana del mercado. Sin badges si la referencia no alcanza (n < 20) o
// si BYMA esta fuera de horario.
// ============================================================

let caroBaratoData = null;

function cbPct(v, dec = 2) {
  if (v === null || v === undefined) return "—";
  if (Math.abs(v) < 0.005) return "≈ 0,00 %";
  return `${v > 0 ? "+" : "−"}${fmtNum(Math.abs(v), dec)} %`;
}

function cbKpi(label, valor, sub) {
  return `<div class="acc-kpi"><p class="acc-kpi-label">${label}</p><p class="acc-kpi-valor">${valor}</p><p class="acc-kpi-sub">${sub}</p></div>`;
}

function renderCaroBarato() {
  const d = caroBaratoData;
  const ref = d.referencia;
  document.getElementById("cb-kpis").innerHTML =
    cbKpi("CCL de referencia (mediana)", ref.estado === "ok" ? fmtNum(ref.ccl, 2) : "—", `n = ${ref.n} CEDEARs líquidos`)
    + cbKpi("CCL de bonos", fmtNum(d.ccl_bonos, 2), "dolarapi")
    + cbKpi("Prima de mercado", cbPct(d.prima_mercado_pct), "de los CEDEARs sobre el CCL de bonos")
    + cbKpi("CCL implícito de KO", d.ko.estado === "ok" ? fmtNum(d.ko.ccl, 2) : "—",
            d.ko.estado === "ok" ? "2ª referencia (Sin Elefantes Blancos)" : escapeAttr(d.ko.motivo || "fuera del set"));

  const avisos = [];
  if (d.fuera_de_horario) {
    avisos.push("Fuera del horario de BYMA (11 a 17 h, días hábiles): las puntas no son precio. Se muestran los desvíos, sin badges.");
  }
  if (ref.estado !== "ok") avisos.push(`Sin referencia: ${escapeAttr(ref.motivo)}. No se calculan desvíos ni badges.`);
  document.getElementById("cb-avisos").innerHTML = avisos.map((a) => `<p class="frontera-aviso frontera-aviso-error">${a}</p>`).join("");

  const umbralEl = document.getElementById("cb-umbral");
  if (document.activeElement !== umbralEl) umbralEl.value = d.umbral_alerta_pct;

  const todos = document.getElementById("cb-universo").value === "todos";
  const filas = d.filas.filter((f) => todos || f.origen.length);
  const body = document.getElementById("cb-body");
  if (!filas.length) {
    body.innerHTML = `<tr><td colspan="7" class="acc-tenue">No hay CEDEARs para mostrar.</td></tr>`;
  } else {
    body.innerHTML = filas.map((f) => {
      let badge = f.badge ? ` <span class="frontera-badge cb-badge-${f.badge}">${f.badge}</span>` : "";
      // Fuera del set de referencia no hay badge aunque el desvio pase el umbral: se muestra el motivo.
      if (!f.badge && f.aviso_sin_badge) {
        badge = ` <span class="acc-tenue cb-sin-badge" title="Sin badge: fuera de la mediana">${escapeAttr(f.aviso_sin_badge)}</span>`;
      }
      const fuera = f.en_referencia ? "" : ` title="Fuera de la mediana: ${escapeAttr(f.motivo_exclusion)}"`;
      const marca = f.en_referencia ? "" : ` <span class="acc-tenue"${fuera}>*</span>`;
      const origen = todos && f.origen.length ? ` <span class="acc-tenue">(${f.origen.join(", ")})</span>` : "";
      let ny = "—";
      if (f.precio_ny) {
        ny = `US$ ${fmtNum(f.precio_ny, 2)} <span class="acc-tenue">${f.fuente_ny === "yfinance" ? "yfinance, ETF" : escapeAttr(f.fuente_ny)} · ${escapeAttr(f.ratio_texto)}</span>`;
      } else if (f.estado_ny) {
        ny = `<span class="acc-tenue">${escapeAttr(f.estado_ny.replace(/_/g, " "))}</span>`;
      }
      return `<tr>
        <td><a href="#" class="cm-ticker cb-ticker" data-ticker="${escapeAttr(f.ticker)}">${escapeAttr(f.ticker)}</a>${marca}${origen}</td>
        <td class="frontera-cifra" style="text-align:right">${fmtNum(f.ccl_implicito, 2)}</td>
        <td class="frontera-cifra" style="text-align:right">${cbPct(f.desvio_vs_ref_pct)}${badge}</td>
        <td class="frontera-cifra" style="text-align:right">${cbPct(f.desvio_vs_ko_pct)}</td>
        <td class="frontera-cifra" style="text-align:right">${fmtNum(f.valor_teorico_ars, 2)}</td>
        <td class="frontera-cifra" style="text-align:right">${fmtNum(f.precio_ars, 2)}</td>
        <td>${ny}</td>
      </tr>`;
    }).join("");
  }
  const dist = d.distribucion_desvio_abs_pct;
  document.getElementById("cb-nota").textContent =
    `Desvío < 0 = el CEDEAR está más barato que el resto (su CCL implícito es menor que la mediana). `
    + `Mediana de los CEDEARs con puntas y precio NY de data912, sin ETFs de yfinance y con al menos USD ${fmtNum(d.reglas.min_monto_operado_usd, 0)} operados hoy `
    + `(* = fuera de la mediana, pasá el mouse para ver por qué). Badge si |desvío| > ${fmtNum(d.umbral_alerta_pct, 2)} %, solo para papeles dentro de la mediana`
    + (dist.p90 === null ? "." : ` — hoy dentro de la mediana: |desvío| p50 ${fmtNum(dist.p50, 2)} %, p90 ${fmtNum(dist.p90, 2)} %, máx ${fmtNum(dist.max, 2)} %.`)
    + ` Dato de data912 a las ${cmHora(d.fecha_dato)}.`;
}

async function cargarCaroBarato() {
  const body = document.getElementById("cb-body");
  try {
    caroBaratoData = await apiJson("/api/cedear-caro-barato");
    renderCaroBarato();
  } catch (err) {
    body.innerHTML = `<tr><td colspan="7" class="acc-ficha-error">${escapeAttr(err.message)}</td></tr>`;
  }
}

async function guardarUmbralCaroBarato() {
  const estadoEl = document.getElementById("cb-estado");
  const valor = parseFloat(document.getElementById("cb-umbral").value);
  estadoEl.className = "acc-ficha-estado";
  if (Number.isNaN(valor)) {
    estadoEl.textContent = "El umbral tiene que ser un número entre 0 y 100.";
    estadoEl.classList.add("acc-ficha-estado-error");
    return;
  }
  try {
    await apiJson("/api/parametros-rv/desvio_cedear_alerta_pct", "PUT", { valor });
    estadoEl.textContent = `Umbral guardado: ${fmtNum(valor, 2)} %.`;
    await cargarCaroBarato();
  } catch (err) {
    estadoEl.textContent = err.message;
    estadoEl.classList.add("acc-ficha-estado-error");
  }
}

document.getElementById("cb-universo").addEventListener("change", () => { if (caroBaratoData) renderCaroBarato(); });
document.getElementById("cb-umbral-btn").addEventListener("click", guardarUmbralCaroBarato);
document.getElementById("cb-body").addEventListener("click", (ev) => {
  const a = ev.target.closest(".cb-ticker");
  if (!a) return;
  ev.preventDefault();
  evaluarCedearMoneda(a.dataset.ticker);
  document.getElementById("panel-cedear-moneda").scrollIntoView({ behavior: "smooth", block: "start" });
});

document.getElementById("cedear-moneda-evaluar-btn").addEventListener("click", () => evaluarCedearMoneda());
document.getElementById("cedear-moneda-ticker").addEventListener("keydown", (ev) => {
  if (ev.key === "Enter") evaluarCedearMoneda();
});
document.getElementById("cedear-moneda-umbral-btn").addEventListener("click", guardarUmbralCedear);
document.getElementById("cedear-moneda-body").addEventListener("click", (ev) => {
  const a = ev.target.closest(".cm-ticker");
  if (!a) return;
  ev.preventDefault();
  evaluarCedearMoneda(a.dataset.ticker);
});

// ============================================================
// Panel: analizador / simulador de bonos (Epica 11, 2026-09-08)
// ============================================================

let mercadoBonosData = { especies: [], categorias: [], en_cartera_total: 0 };
let mercadoBonosSort = { campo: "tir_pct", desc: true };
let mercadoBonosCategoria = "cartera"; // "cartera" | "todas" | clave de categoria
let mercadoBonosVariantes = false;
let mercadoBonosCurva = true;

// Color FIJO por categoria. Las 8 categorias que aparecen hoy en la
// cartera (on/bonar/boncap/provincial/letra/cer/global/bopreal) tienen
// cada una un --series distinto (1-8) -- antes habia colisiones (on y
// boncap compartian azul, letra y provincial dorado) y no se
// diferenciaban en el panel de tenencias (el usuario, 2026-09-10). Las
// categorias que hoy no estan en cartera reusan un color.
const CATEGORIA_COLOR_VAR = {
  on: "--series-3",          // azul
  bonar: "--series-2",       // teal
  boncap: "--series-5",      // violeta
  provincial: "--series-4",  // coral
  letra: "--series-7",       // dorado
  cer: "--series-6",         // verde
  global: "--series-8",      // rosa
  bopreal: "--series-1",     // ambar
  // sin tenencia hoy -- reusan
  tamar: "--series-1", dual: "--series-6", dollar_linked: "--series-4",
  nacional: "--text-secondary", otro: "--text-secondary",
};

function _colorCategoria(cat) {
  return getComputedStyle(document.documentElement)
    .getPropertyValue(CATEGORIA_COLOR_VAR[cat] || "--text-secondary").trim() || "#8b98a5";
}

// Ajuste cuadratico y = a + bx + cx^2 por minimos cuadrados (para la
// linea guia de "la curva"). Devuelve [a,b,c] o null si el sistema es
// degenerado.
function _polyfit2(xs, ys) {
  const n = xs.length;
  let Sx = 0, Sx2 = 0, Sx3 = 0, Sx4 = 0, Sy = 0, Sxy = 0, Sx2y = 0;
  for (let i = 0; i < n; i++) {
    const x = xs[i], y = ys[i], x2 = x * x;
    Sx += x; Sx2 += x2; Sx3 += x2 * x; Sx4 += x2 * x2;
    Sy += y; Sxy += x * y; Sx2y += x2 * y;
  }
  const M = [[n, Sx, Sx2], [Sx, Sx2, Sx3], [Sx2, Sx3, Sx4]];
  const V = [Sy, Sxy, Sx2y];
  // eliminacion de Gauss 3x3
  for (let c = 0; c < 3; c++) {
    let piv = c;
    for (let r = c + 1; r < 3; r++) if (Math.abs(M[r][c]) > Math.abs(M[piv][c])) piv = r;
    if (Math.abs(M[piv][c]) < 1e-9) return null;
    [M[c], M[piv]] = [M[piv], M[c]];
    [V[c], V[piv]] = [V[piv], V[c]];
    for (let r = 0; r < 3; r++) {
      if (r === c) continue;
      const f = M[r][c] / M[c][c];
      for (let k = 0; k < 3; k++) M[r][k] -= f * M[c][k];
      V[r] -= f * V[c];
    }
  }
  return [V[0] / M[0][0], V[1] / M[1][1], V[2] / M[2][2]];
}

function renderCurvaBonos(filas) {
  const caja = document.getElementById("mercado-curva-caja");
  const svg = document.getElementById("mercado-curva-svg");
  const nota = document.getElementById("mercado-curva-nota");
  if (!mercadoBonosCurva) { caja.hidden = true; return; }
  caja.hidden = false;

  const pts0 = filas.filter((b) => b.tir_pct != null && b.duration != null && b.duration > 0);
  if (pts0.length < 3) {
    svg.innerHTML = "";
    nota.textContent = "Pocos puntos con TIR y duration para dibujar la curva.";
    return;
  }
  // Clip de outliers de TIR (dato corrupto del feed masivo de Rava, ej.
  // un provincial defaulteado a 700%) -- solo para que la escala del
  // grafico sea legible; la tabla los muestra igual.
  const tirsOrd = pts0.map((b) => b.tir_pct).slice().sort((a, b) => a - b);
  const p75 = tirsOrd[Math.floor(tirsOrd.length * 0.75)] ?? 0;
  const capTir = Math.max(60, p75 * 2.5);
  const pts = pts0.filter((b) => b.tir_pct >= -10 && b.tir_pct <= capTir);
  const ocultos = pts0.length - pts.length;
  if (pts.length < 3) {
    svg.innerHTML = "";
    nota.textContent = "Pocos puntos con TIR razonable para dibujar la curva.";
    return;
  }

  const W = 620, H = 210, padL = 40, padR = 12, padT = 12, padB = 24;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const durs = pts.map((b) => b.duration);
  const dxMin = Math.min(...durs), dxMax = Math.max(...durs);
  const durMax = Math.ceil(dxMax * 1.05);

  // Linea guia: ajuste cuadratico, SOLO con una categoria homogenea y
  // >=4 puntos, y dibujada solo dentro del rango real de duration (no
  // se extrapola a x=0 ni al borde).
  const catPuntual = mercadoBonosCategoria !== "cartera" && mercadoBonosCategoria !== "todas";
  let fitCoef = null;
  const fitYs = [];
  if (catPuntual && pts.length >= 4) {
    fitCoef = _polyfit2(durs, pts.map((b) => b.tir_pct));
    if (fitCoef) {
      const [a, bb, cc] = fitCoef;
      for (let i = 0; i <= 40; i++) {
        const d = dxMin + (i / 40) * (dxMax - dxMin);
        fitYs.push(a + bb * d + cc * d * d);
      }
    }
  }

  let tirMin = Math.min(...pts.map((b) => b.tir_pct), ...fitYs);
  let tirMax = Math.max(...pts.map((b) => b.tir_pct), ...fitYs);
  const m = Math.max((tirMax - tirMin) * 0.12, 0.3);
  tirMin -= m; tirMax += m;

  const x = (d) => padL + (d / durMax) * plotW;
  const y = (t) => padT + plotH - ((t - tirMin) / (tirMax - tirMin)) * plotH;

  // grid + ejes
  const partes = [];
  const xticks = durMax <= 6 ? [0, 1, 2, 3, 4, 5, 6] : durMax <= 12 ? [0, 2, 4, 6, 8, 10, 12] : [0, 5, 10, 15, 20, 25];
  for (const d of xticks.filter((v) => v <= durMax)) {
    partes.push(`<line class="mercado-curva-grid" x1="${x(d).toFixed(1)}" y1="${padT}" x2="${x(d).toFixed(1)}" y2="${padT + plotH}"/>`);
    partes.push(`<text class="mercado-curva-eje" x="${x(d).toFixed(1)}" y="${H - 8}" text-anchor="middle">${d}</text>`);
  }
  const nY = 4;
  for (let i = 0; i <= nY; i++) {
    const t = tirMin + (i / nY) * (tirMax - tirMin);
    partes.push(`<line class="mercado-curva-grid" x1="${padL}" y1="${y(t).toFixed(1)}" x2="${padL + plotW}" y2="${y(t).toFixed(1)}"/>`);
    partes.push(`<text class="mercado-curva-eje" x="${padL - 5}" y="${(y(t) + 3).toFixed(1)}" text-anchor="end">${t.toFixed(1)}%</text>`);
  }

  // linea guia (ya calculada arriba en fitYs, dentro del rango de datos)
  if (fitCoef && fitYs.length) {
    const seg = fitYs.map((ty, i) => {
      const d = dxMin + (i / 40) * (dxMax - dxMin);
      return `${i === 0 ? "M" : "L"} ${x(d).toFixed(1)} ${y(ty).toFixed(1)}`;
    });
    partes.push(`<path class="mercado-curva-fit" d="${seg.join(" ")}"/>`);
  }

  // puntos
  for (const b of pts) {
    const col = _colorCategoria(b.categoria);
    const r = b.en_cartera ? 4.5 : 2.8;
    const stroke = b.en_cartera ? `stroke="var(--text-primary)" stroke-width="1.5"` : "";
    partes.push(
      `<circle class="mercado-curva-punto" data-ticker="${b.ticker}" cx="${x(b.duration).toFixed(1)}" cy="${y(b.tir_pct).toFixed(1)}" r="${r}" fill="${col}" ${stroke}>` +
      `<title>${b.ticker} · TIR ${fmtNum(b.tir_pct)}% · dur ${fmtNum(b.duration)}a · ${b.categoria_label}${b.en_cartera ? " · en cartera" : ""}</title></circle>`
    );
  }

  svg.innerHTML = partes.join("\n");
  nota.textContent = `${pts.length} especies · eje X = duration (años), eje Y = TIR anual` +
    (catPuntual ? " · línea = ajuste cuadrático de la categoría" : " · elegí una categoría para ver la línea de ajuste") +
    (ocultos ? ` · ${ocultos} fuera de escala (TIR anómala del feed)` : "");
}
let bonoActual = null; // ticker analizado

function fmtNum(v, dec = 2) {
  if (v === null || v === undefined) return "—";
  return v.toLocaleString("es-AR", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function metricaCell(label, valor, sufijo = "") {
  const v = valor === null || valor === undefined ? "—" : `${fmtNum(valor)}${sufijo}`;
  return `<div class="simulador-metrica"><span class="m-label">${label}</span><span class="m-valor">${v}</span></div>`;
}

function renderMetricasMercado(j) {
  const m = j.mercado || {};
  if (m.motivo) return `<p class="simulador-nota">${m.motivo}</p>`;
  return [
    metricaCell("TIR", m.tir_pct, "%"),
    metricaCell("TNA", m.tna_pct, "%"),
    metricaCell("TEM", m.tem_pct, "%"),
    metricaCell("Duration", m.duration_rava, "a"),
    metricaCell("Mod. dur", m.duration_modificada, "a"),
    metricaCell("Convexidad", m.convexidad),
    metricaCell("Current yield", m.current_yield_pct, "%"),
    metricaCell("Paridad", m.paridad_pct, "%"),
    metricaCell("Precio impl. (VN100)", m.precio_implicito_vn100),
    metricaCell("Años al vto.", j.anios_al_vencimiento),
  ].join("");
}

function renderMetricasPosicion(j) {
  const p = j.posicion;
  const met = (p && p.metricas) || {};
  return [
    metricaCell("Cantidad", p.cantidad),
    metricaCell("Tu precio (VN100)", p.precio_vn100),
    metricaCell("Tu TIR", met.tir_pct, "%"),
    metricaCell("Tu paridad", met.paridad_pct, "%"),
  ].join("");
}

function renderCronogramaBono(j) {
  const cont = document.getElementById("simulador-cronograma-chart");
  const nota = document.getElementById("simulador-cronograma-nota");
  const flujos = j.cronograma || [];
  if (!flujos.length) {
    cont.innerHTML = `<p class="eventos-vacio">Sin pagos futuros.</p>`;
    nota.textContent = "";
    return;
  }
  const max = Math.max(...flujos.map((f) => f.total), 1e-9);
  const colorInteres = colorForClave("interes");
  const colorAmort = colorForClave("amortizacion");
  cont.innerHTML = flujos.map((f) => {
    const alt = (f.total / max) * 100;
    const fracInt = f.total ? f.cupon / f.total : 0;
    const anio = f.fecha.slice(0, 4);
    const mes = f.fecha.slice(5, 7);
    const titulo = `${f.fecha}: interés ${fmtNum(f.cupon, 4)} + amortización ${fmtNum(f.amortizacion, 4)} (por VN 100)`;
    return `
      <div class="flujo-col" style="cursor:default" title="${titulo}">
        <div class="flujo-bar" style="height:${Math.max(alt, 1)}%">
          <div class="flujo-seg" style="height:${fracInt * 100}%;background:${colorInteres}"></div>
          <div class="flujo-seg" style="height:${(1 - fracInt) * 100}%;background:${colorAmort}"></div>
        </div>
        <p class="flujo-col-label">${mes}/${anio.slice(2)}</p>
      </div>`;
  }).join("");
  nota.innerHTML =
    `<span class="swatch" style="background:${colorInteres}"></span>Interés &nbsp; ` +
    `<span class="swatch" style="background:${colorAmort}"></span>Amortización &nbsp;—&nbsp; ` +
    `${j.n_pagos_futuros} pagos, total a cobrar ${fmtNum(j.total_a_cobrar_vn100)} por VN 100` +
    (j.fuente_cronograma === "rava_vivo" ? " (cronograma traído de Rava en vivo — bono fuera de cartera)" : "");
}

function renderSimulacionBono(j) {
  const wrap = document.getElementById("simulador-metricas-sim");
  const nota = document.getElementById("simulador-nota-sim");
  if (!j.simulacion) {
    wrap.hidden = true;
    nota.hidden = true;
    return;
  }
  const s = j.simulacion;
  const m = s.metricas;
  wrap.innerHTML = [
    metricaCell("Precio (VN100)", s.precio_vn100),
    metricaCell("TIR", m.tir_pct, "%"),
    metricaCell("TNA", m.tna_pct, "%"),
    metricaCell("Duration", m.duration_macaulay, "a"),
    metricaCell("Mod. dur", m.duration_modificada, "a"),
    metricaCell("Current yield", m.current_yield_pct, "%"),
    metricaCell("Paridad", m.paridad_pct, "%"),
  ].join("");
  wrap.hidden = false;
  if (s.vs_mercado) {
    nota.textContent =
      `Precio simulado ${fmtPct(s.vs_mercado.delta_precio_pct)} vs. el que implica la TIR de Rava de hoy (${fmtNum(s.vs_mercado.precio_mercado_vn100)}). ${s.vs_mercado.nota}`;
    nota.hidden = false;
  } else {
    nota.hidden = true;
  }
}

async function analizarBono(ticker, extra = {}) {
  ticker = (ticker || "").trim().toUpperCase();
  if (!ticker) return;
  const err = document.getElementById("simulador-error");
  const resultado = document.getElementById("simulador-rv-resultado");
  const btn = document.getElementById("simulador-analizar-btn");
  btn.textContent = "Analizando…";
  btn.disabled = true;
  const params = new URLSearchParams();
  if (extra.precio != null && extra.precio !== "") params.set("precio", extra.precio);
  if (extra.tirObjetivo != null && extra.tirObjetivo !== "") params.set("tir_objetivo", extra.tirObjetivo);
  const qs = params.toString() ? `?${params}` : "";

  try {
    let resp;
    try {
      resp = await fetch(`/api/simular-bono/${encodeURIComponent(ticker)}${qs}`);
    } catch (e) {
      err.textContent = `No se pudo consultar ${ticker}.`;
      err.hidden = false;
      return;
    }
    if (!resp.ok) {
      const d = await resp.json().catch(() => ({}));
      err.textContent = `${ticker}: ${d.detail || "sin cobertura"}.`;
      err.hidden = false;
      resultado.hidden = true;
      return;
    }
    err.hidden = true;
    const j = await resp.json();
    bonoActual = ticker;
    document.getElementById("simulador-ticker-input").value = ticker;

    const badgeCartera = j.en_cartera ? `<span class="simulador-badge">en cartera</span>` : "";
    document.getElementById("simulador-titulo-bono").innerHTML =
      `${j.ticker} <span class="simulador-sub">${j.nombre || ""} · ${j.tipo_instrumento || "—"} · ley ${j.legislacion || "—"} · vto ${j.fecha_vencimiento} · paga en ${j.moneda_pago || "?"}</span>${badgeCartera}`;

    document.getElementById("simulador-metricas-mercado").innerHTML = renderMetricasMercado(j);

    const posWrap = document.getElementById("simulador-posicion-wrap");
    if (j.posicion) {
      document.getElementById("simulador-metricas-posicion").innerHTML = renderMetricasPosicion(j);
      posWrap.hidden = false;
    } else {
      posWrap.hidden = true;
    }

    document.getElementById("simulador-moneda-label").textContent = j.moneda_pago || "moneda de pago";
    renderSimulacionBono(j);
    renderCronogramaBono(j);
    resultado.hidden = false;
  } finally {
    btn.textContent = "Analizar";
    btn.disabled = false;
  }
}

function renderMercadoBonosChips() {
  const cont = document.getElementById("mercado-bonos-chips");
  const chips = [
    { clave: "cartera", label: "En cartera", total: mercadoBonosData.en_cartera_total },
    ...mercadoBonosData.categorias.filter((c) => c.total > 0),
    { clave: "todas", label: "Todas", total: mercadoBonosData.especies.length },
  ];
  cont.innerHTML = chips.map((c) => `
    <button type="button" class="mercado-chip${c.clave === mercadoBonosCategoria ? " activo" : ""}"
            data-cat="${c.clave}">${c.label} <span class="mercado-chip-n">${c.total}</span></button>
  `).join("");
}

function _mercadoBonoFilaHTML(b) {
  const marcaCartera = b.en_cartera ? ` <span class="mercado-badge-cartera">●</span>` : "";
  const emisor = b.emisor || "—";
  const tituloEmisor = escapeAttr(b.descripcion || b.emisor || "");
  return `
    <tr class="mercado-bono-fila" data-ticker="${b.ticker}">
      <td>${b.ticker}${marcaCartera}</td>
      <td class="mercado-emisor-celda" title="${tituloEmisor}">${emisor}</td>
      <td class="mercado-cat-celda">${b.categoria_label}</td>
      <td>${b.legislacion && b.legislacion !== "-" ? b.legislacion : "—"}</td>
      <td class="eventos-monto">${fmtNum(b.precio_limpio_vn100)}</td>
      <td class="eventos-monto">${b.paridad_pct == null ? "—" : fmtNum(b.paridad_pct)}</td>
      <td class="eventos-monto">${fmtNum(b.tir_pct)}</td>
      <td class="eventos-monto">${fmtNum(b.duration)}</td>
      <td class="eventos-monto">${fmtNum(b.dm)}</td>
      <td class="eventos-monto">${fmtNum(b.convexity)}</td>
      <td>${b.vencimiento || "—"}</td>
    </tr>`;
}

function renderMercadoBonos() {
  const buscar = document.getElementById("mercado-bonos-buscar").value.trim().toUpperCase();
  const ley = document.getElementById("mercado-bonos-ley").value;
  const { campo, desc } = mercadoBonosSort;
  const cat = mercadoBonosCategoria;

  let filas = mercadoBonosData.especies.filter((b) => {
    if (!mercadoBonosVariantes && b.es_variante_especie) return false;
    if (cat === "cartera" && !b.en_cartera) return false;
    if (cat !== "cartera" && cat !== "todas" && b.categoria !== cat) return false;
    if (ley && b.legislacion !== ley) return false;
    if (buscar && !b.ticker.toUpperCase().includes(buscar)) return false;
    return true;
  });

  renderCurvaBonos(filas.slice());

  const cmp = (a, a2) => {
    let x = a[campo], y = a2[campo];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "string") return desc ? y.localeCompare(x) : x.localeCompare(y);
    return desc ? y - x : x - y;
  };

  const totalMercado = mercadoBonosVariantes
    ? mercadoBonosData.especies.length
    : mercadoBonosData.especies.filter((b) => !b.es_variante_especie).length;
  document.getElementById("mercado-bonos-subtitulo").textContent =
    `${filas.length} de ${totalMercado} especies · actualizado ${mercadoBonosData.especies[0]?.actualizado_en || "—"} (Rava)`;

  const body = document.getElementById("mercado-bonos-body");
  if (cat === "todas") {
    // agrupado por categoria, con subheader
    const porCat = {};
    for (const b of filas) (porCat[b.categoria] ||= []).push(b);
    body.innerHTML = mercadoBonosData.categorias
      .filter((c) => porCat[c.clave]?.length)
      .map((c) => {
        const rows = porCat[c.clave].sort(cmp).map(_mercadoBonoFilaHTML).join("");
        return `<tr class="mercado-grupo-header"><td colspan="11">${c.label} · ${porCat[c.clave].length}</td></tr>${rows}`;
      })
      .join("");
  } else {
    body.innerHTML = filas.sort(cmp).map(_mercadoBonoFilaHTML).join("") ||
      `<tr><td colspan="11" class="eventos-vacio">Sin especies para este filtro.</td></tr>`;
  }
}

// ============================================================
// Comparador de bonos multi-bono (2026-09-11: "poder
// comparar varios a la vez, minimo 3"). CERO cambios de backend --
// cada fila es un llamado independiente a /api/simular-bono/{ticker}
// (mismo endpoint que el Analizador de un solo bono de arriba), en
// paralelo, pintados lado a lado en una tabla. Filas dinamicas: arranca
// con 3, "+ Agregar bono" suma mas, cada fila se puede quitar (menos la
// ultima -- si el usuario la quita se limpia en vez de desaparecer, para no
// quedarse con la tabla vacia sin darse cuenta).
// ============================================================

let comparadorFilaId = 0;

function _comparadorFilaHTML(id) {
  return `
    <div class="simulador-buscador comparador-fila" data-fila-id="${id}">
      <input type="text" class="comparador-ticker" list="simulador-tickers-datalist"
             placeholder="Ticker" autocomplete="off" spellcheck="false" />
      <input type="number" step="0.01" class="comparador-precio" placeholder="Precio (opc.)" />
      <input type="number" step="0.1" class="comparador-tir" placeholder="TIR objetivo % (opc.)" />
      <button type="button" class="comparador-quitar-btn" title="Quitar esta fila">✕</button>
    </div>`;
}

function agregarFilaComparador() {
  comparadorFilaId += 1;
  document.getElementById("comparador-filas").insertAdjacentHTML("beforeend", _comparadorFilaHTML(comparadorFilaId));
}

function inicializarComparador(nFilas = 3) {
  document.getElementById("comparador-filas").innerHTML = "";
  for (let i = 0; i < nFilas; i++) agregarFilaComparador();
}

function _comparadorEscenario(j) {
  if (!j.simulacion) return "Mercado";
  return j.simulacion.input.tir_objetivo_pct != null ? "TIR objetivo" : "Precio";
}

function _comparadorFilaResultadoHTML(j) {
  if (j.error) {
    return `<tr><td>${j.ticker}</td><td colspan="11" class="eventos-vacio">${j.error}</td></tr>`;
  }
  const m = j.simulacion ? j.simulacion.metricas : (j.mercado || {});
  if (m.motivo) {
    return `<tr><td>${j.ticker}</td><td colspan="11" class="eventos-vacio">${m.motivo}</td></tr>`;
  }
  const precioUsado = j.simulacion ? j.simulacion.precio_vn100 : j.mercado.precio_implicito_vn100;
  const duration = j.simulacion ? m.duration_macaulay : j.mercado.duration_rava;
  const badgeCartera = j.en_cartera ? ` <span class="simulador-badge">cartera</span>` : "";
  return `
    <tr class="mercado-bono-fila" data-ticker="${j.ticker}">
      <td>${j.ticker}${badgeCartera}</td>
      <td>${j.tipo_instrumento || "—"}</td>
      <td>${_comparadorEscenario(j)}</td>
      <td class="eventos-monto">${fmtNum(precioUsado)}</td>
      <td class="eventos-monto">${fmtNum(m.tir_pct)}</td>
      <td class="eventos-monto">${fmtNum(m.tna_pct)}</td>
      <td class="eventos-monto">${fmtNum(duration)}</td>
      <td class="eventos-monto">${fmtNum(m.duration_modificada)}</td>
      <td class="eventos-monto">${fmtNum(m.convexidad)}</td>
      <td class="eventos-monto">${fmtNum(m.current_yield_pct)}</td>
      <td class="eventos-monto">${fmtNum(m.paridad_pct)}</td>
      <td class="eventos-monto">${fmtNum(j.anios_al_vencimiento)}</td>
    </tr>`;
}

async function compararBonos() {
  const filasInput = [...document.querySelectorAll(".comparador-fila")];
  const btn = document.getElementById("comparador-comparar-btn");
  const err = document.getElementById("comparador-error");
  err.hidden = true;

  const pedidos = filasInput
    .map((fila) => ({
      ticker: fila.querySelector(".comparador-ticker").value.trim().toUpperCase(),
      precio: fila.querySelector(".comparador-precio").value,
      tir: fila.querySelector(".comparador-tir").value,
    }))
    .filter((p) => p.ticker);

  if (!pedidos.length) {
    err.textContent = "Cargá al menos un ticker para comparar.";
    err.hidden = false;
    return;
  }

  btn.textContent = "Comparando…";
  btn.disabled = true;
  try {
    const resultados = await Promise.all(pedidos.map(async (p) => {
      const params = new URLSearchParams();
      if (p.precio !== "") params.set("precio", p.precio);
      if (p.tir !== "") params.set("tir_objetivo", p.tir);
      const qs = params.toString() ? `?${params}` : "";
      try {
        const resp = await fetch(`/api/simular-bono/${encodeURIComponent(p.ticker)}${qs}`);
        if (!resp.ok) {
          const d = await resp.json().catch(() => ({}));
          return { ticker: p.ticker, error: d.detail || "sin cobertura" };
        }
        return await resp.json();
      } catch (e) {
        return { ticker: p.ticker, error: "no se pudo consultar" };
      }
    }));

    document.getElementById("comparador-body").innerHTML = resultados.map(_comparadorFilaResultadoHTML).join("");
    document.getElementById("comparador-tabla").hidden = false;
  } finally {
    btn.textContent = "Comparar";
    btn.disabled = false;
  }
}

function wireComparadorBonos() {
  inicializarComparador();
  document.getElementById("comparador-agregar-btn").addEventListener("click", agregarFilaComparador);
  document.getElementById("comparador-comparar-btn").addEventListener("click", compararBonos);
  document.getElementById("comparador-filas").addEventListener("click", (e) => {
    const btnQuitar = e.target.closest(".comparador-quitar-btn");
    if (!btnQuitar) return;
    const filas = document.querySelectorAll(".comparador-fila");
    const fila = btnQuitar.closest(".comparador-fila");
    if (filas.length <= 1) {
      // no desaparece la ultima fila -- se limpia, para no dejar la
      // tabla de setup vacia sin que el usuario se de cuenta.
      fila.querySelector(".comparador-ticker").value = "";
      fila.querySelector(".comparador-precio").value = "";
      fila.querySelector(".comparador-tir").value = "";
      return;
    }
    fila.remove();
  });
}

function wireSimuladorBonos() {
  document.getElementById("simulador-analizar-btn").addEventListener("click", () => {
    analizarBono(document.getElementById("simulador-ticker-input").value);
  });
  document.getElementById("simulador-ticker-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") analizarBono(e.target.value);
  });
  document.getElementById("simulador-simular-btn").addEventListener("click", () => {
    if (!bonoActual) return;
    analizarBono(bonoActual, {
      precio: document.getElementById("simulador-input-precio").value,
      tirObjetivo: document.getElementById("simulador-input-tir").value,
    });
  });
  document.getElementById("simulador-limpiar-btn").addEventListener("click", () => {
    document.getElementById("simulador-input-precio").value = "";
    document.getElementById("simulador-input-tir").value = "";
    if (bonoActual) analizarBono(bonoActual);
  });

  document.getElementById("mercado-bonos-buscar").addEventListener("input", renderMercadoBonos);
  document.getElementById("mercado-bonos-ley").addEventListener("change", renderMercadoBonos);
  document.getElementById("mercado-bonos-variantes").addEventListener("change", (e) => {
    mercadoBonosVariantes = e.target.checked;
    renderMercadoBonos();
  });
  document.getElementById("mercado-bonos-curva").addEventListener("change", (e) => {
    mercadoBonosCurva = e.target.checked;
    renderMercadoBonos();
  });
  document.getElementById("mercado-curva-svg").addEventListener("click", (e) => {
    const pt = e.target.closest(".mercado-curva-punto");
    if (pt) {
      analizarBono(pt.dataset.ticker);
      document.getElementById("panel-simulador-bono").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
  document.getElementById("mercado-bonos-chips").addEventListener("click", (e) => {
    const chip = e.target.closest(".mercado-chip");
    if (!chip) return;
    mercadoBonosCategoria = chip.dataset.cat;
    renderMercadoBonosChips();
    renderMercadoBonos();
  });
  document.querySelectorAll("#mercado-bonos-tabla thead th[data-sort]").forEach((th) => {
    th.style.cursor = "pointer";
    th.addEventListener("click", () => {
      const campo = th.dataset.sort;
      if (mercadoBonosSort.campo === campo) mercadoBonosSort.desc = !mercadoBonosSort.desc;
      else mercadoBonosSort = { campo, desc: true };
      renderMercadoBonos();
    });
  });
  document.getElementById("mercado-bonos-body").addEventListener("click", (e) => {
    const fila = e.target.closest(".mercado-bono-fila");
    if (fila) {
      analizarBono(fila.dataset.ticker);
      document.getElementById("panel-simulador-bono").scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });
}

// ============================================================
// Panel macro (solapas de Analisis, 2026-09-09) -- inflacion AR + REM,
// CPI EEUU, Fed. Mismo contenido en las 2 solapas (Bonos y Acciones).
// ============================================================

// % "plano" para datos macro (inflacion/tasa) -- sin el "+" que antepone
// fmtPct (ese "+" tiene sentido para una ganancia, no para una tasa).
function pctPlano(v, dec = 1) {
  return v == null ? "—" : `${v.toFixed(dec)}%`;
}

// ============================================================
// "Indicadores Macro" (Epica 21, 2026-09-29): una tarjeta por indicador
// activo del catalogo (GET /api/indicadores-macro) -- valor con la fecha
// de la OBSERVACION, delta 3m/12m (pp o %, segun `tipo_delta` del
// catalogo: el front no decide), percentil 10 anios, y lo que el usuario
// escribe sobre el indicador (lapiz -> mini formulario -> PUT
// .../anotacion): umbral (badge ⚠️ si esta cruzado, mismo que las tesis
// de la Epica 12), objetivo (solo referencia) y nota.
//
// Reemplaza las tiles armadas a mano por clave de benchmarks_macro
// (Epicas 12-16) y MACRO_HIST_CONFIG (Epica 13): las secciones y el orden
// salen del catalogo, un indicador nuevo aparece sin tocar este archivo.
//
// Grafico colapsado por tarjeta (toggle "Ver historico", nunca todos
// abiertos a la vez). Ventana por default: 10 anios (pedido del ticket de
// la Epica 21 -- antes era 2 anios para inflacion/riesgo pais), con boton
// para ver toda la serie. Umbral y objetivo se dibujan como lineas
// horizontales. SVG a mano, sin libreria (mismo criterio de siempre).
// ============================================================

const macroHistoricoState = {
  abiertos: new Set(),     // ids con el grafico desplegado
  completo: new Set(),     // ids mostrando la serie COMPLETA (no los 10 años)
  cache: {},               // id -> { ventana: puntos[], completo: puntos[] }
  editando: null,          // id con el formulario de anotacion abierto
};
let indicadoresMacro = []; // ultima respuesta de /api/indicadores-macro

const SUFIJO_UNIDAD = { "%": "%", pp: " pp", pb: " pb", "USD/bbl": " US$/bbl", MUSD: " MUSD", "USD/m²": " US$/m²", x: "x", "USD/oz": " US$/oz", "USD/lb": " US$/lb", "miles/mes": " mil/mes", miles: " mil" };

function _fmtNum(v, dec) {
  return v.toLocaleString("es-AR", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

function fmtValorIndicador(v, ind) {
  return v == null ? "—" : _fmtNum(v, ind.decimales) + (SUFIJO_UNIDAD[ind.unidad] || "");
}

// Delta: `pct` = variacion porcentual; `pp` = diferencia simple, en la
// unidad del indicador (pp si es una tasa en %, pb el riesgo pais, MUSD
// las compras del BCRA).
function fmtDeltaIndicador(d, ind) {
  if (d == null) return "—";
  const signo = d > 0 ? "+" : d < 0 ? "−" : "";
  if (ind.tipo_delta === "pct") return `${signo}${_fmtNum(Math.abs(d), 1)}%`;
  const unidad = ind.unidad === "%" ? " pp" : (SUFIJO_UNIDAD[ind.unidad] || "");
  return `${signo}${_fmtNum(Math.abs(d), ind.decimales)}${unidad}`;
}

// Unidad en la que se expresa el delta (y el umbral de velocidad, v2.44):
// "%" si es variacion porcentual; si es diferencia, la unidad del indicador
// ("pp" si el indicador es una tasa en %).
function unidadDeltaIndicador(ind) {
  if (ind.tipo_delta === "pct") return "%";
  return ind.unidad === "%" ? "pp" : ind.unidad;
}

function renderMiniLineChart(svgEl, puntos, opciones) {
  if (!puntos.length) {
    svgEl.innerHTML = "";
    return;
  }
  const { sufijo = "", decimales = 1, promedio = false, lineasRef = [] } = opciones;
  const W = 800, H = 140;
  const padL = 50, padR = 12, padT = 12, padB = 22;
  const plotW = W - padL - padR;
  const plotH = H - padT - padB;

  const dias = puntos.map((p) => new Date(p.fecha + "T00:00:00").getTime());
  const diaMin = Math.min(...dias), diaMax = Math.max(...dias);
  const spanDias = Math.max(diaMax - diaMin, 1);

  const valores = puntos.map((p) => p.valor);
  // Las lineas de referencia (umbral/objetivo, Epica 21) entran en la
  // escala: si no, un umbral lejos del rango de la serie quedaria afuera.
  const conRef = valores.concat(lineasRef.map((l) => l.valor));
  let vMin = Math.min(...conRef), vMax = Math.max(...conRef);
  if (vMin === vMax) { vMin -= 1; vMax += 1; }
  const margen = (vMax - vMin) * 0.12;
  vMin -= margen; vMax += margen;

  const x = (fecha) => padL + ((new Date(fecha + "T00:00:00").getTime() - diaMin) / spanDias) * plotW;
  const y = (v) => padT + plotH - ((v - vMin) / (vMax - vMin)) * plotH;

  const linePath = puntos.map((p, i) => `${i === 0 ? "M" : "L"} ${x(p.fecha).toFixed(1)} ${y(p.valor).toFixed(1)}`).join(" ");
  const areaPath = `${linePath} L ${x(puntos[puntos.length - 1].fecha).toFixed(1)} ${(padT + plotH).toFixed(1)} `
    + `L ${x(puntos[0].fecha).toFixed(1)} ${(padT + plotH).toFixed(1)} Z`;

  const fmt = (v) => v.toLocaleString("es-AR", { maximumFractionDigits: decimales }) + sufijo;

  // Linea de promedio opcional (2026-09-14, pedido del usuario para el
  // comparador de ratio de bonos) -- generico via `opciones.promedio`,
  // no se activa sola en los demas consumidores de este chart
  // (Indicadores Macro) salvo que lo pidan explicito.
  let lineaPromedio = "";
  if (promedio) {
    const promedioValor = valores.reduce((a, b) => a + b, 0) / valores.length;
    const yProm = y(promedioValor);
    lineaPromedio = `
      <line class="macro-chart-promedio" x1="${padL.toFixed(1)}" y1="${yProm.toFixed(1)}" x2="${(padL + plotW).toFixed(1)}" y2="${yProm.toFixed(1)}"></line>
      <text class="macro-chart-promedio-label" x="${(padL + plotW - 3).toFixed(1)}" y="${(yProm - 4).toFixed(1)}" text-anchor="end">prom. ${fmt(promedioValor)}</text>
    `;
  }

  // Umbral / objetivo de la anotacion del indicador (Epica 21). Etiquetas
  // en extremos opuestos (1ra a la derecha, 2da a la izquierda): con
  // valores cercanos, en el mismo lado se pisaban.
  const lineasReferencia = lineasRef.map((l, i) => {
    const yRef = y(l.valor);
    const posicion = l.posicion || (i % 2 === 0 ? "derecha" : "izquierda");
    const [xLabel, anchor] = posicion === "derecha" ? [padL + plotW - 3, "end"]
      : posicion === "centro" ? [padL + plotW / 2, "middle"] : [padL + 3, "start"];
    return `
      <line class="${l.clase}" x1="${padL.toFixed(1)}" y1="${yRef.toFixed(1)}" x2="${(padL + plotW).toFixed(1)}" y2="${yRef.toFixed(1)}"></line>
      <text class="macro-chart-ref-label" x="${xLabel.toFixed(1)}" y="${(yRef - 3).toFixed(1)}" text-anchor="${anchor}">${l.etiqueta} ${fmt(l.valor)}</text>
    `;
  }).join("");

  // Densidad de labels del eje X simplificada respecto de
  // renderRendimientoChart (esto es secundario/colapsado por default,
  // no justifica portar el algoritmo greedy completo): siempre primero
  // y ultimo, mas hasta 3 intermedios repartidos por indice.
  const idxLabels = new Set([0, puntos.length - 1]);
  const pasos = Math.min(3, puntos.length - 1);
  for (let i = 1; i < pasos; i++) idxLabels.add(Math.round((i * (puntos.length - 1)) / pasos));
  const labelsEjeX = [...idxLabels].sort((a, b) => a - b).map((i) => {
    const p = puntos[i];
    const anchor = i === 0 ? "start" : (i === puntos.length - 1 ? "end" : "middle");
    return `<text class="macro-chart-eje-label" x="${x(p.fecha).toFixed(1)}" y="${H - 6}" text-anchor="${anchor}">${fechaCorta(p.fecha)}</text>`;
  }).join("");

  const ultimo = puntos[puntos.length - 1];
  svgEl.innerHTML = `
    <text class="macro-chart-eje-label" x="${(padL - 6).toFixed(1)}" y="${(padT + 4).toFixed(1)}" text-anchor="end">${fmt(vMax)}</text>
    <text class="macro-chart-eje-label" x="${(padL - 6).toFixed(1)}" y="${(padT + plotH).toFixed(1)}" text-anchor="end">${fmt(vMin)}</text>
    <path class="macro-chart-area" d="${areaPath}"></path>
    ${lineaPromedio}
    ${lineasReferencia}
    <path class="macro-chart-linea" d="${linePath}"></path>
    <circle class="macro-chart-punto" cx="${x(ultimo.fecha).toFixed(1)}" cy="${y(ultimo.valor).toFixed(1)}" r="3"></circle>
    ${labelsEjeX}
  `;
}

function _indicadorPorId(id) {
  return indicadoresMacro.find((x) => x.id === id);
}

async function _fetchSerieIndicador(ind, completo) {
  let url = `/api/indicadores-macro/${encodeURIComponent(ind.id)}/serie`;
  if (!completo) {
    const desde = new Date(ind.fecha + "T00:00:00");
    desde.setFullYear(desde.getFullYear() - 10);
    url += `?desde=${desde.toISOString().slice(0, 10)}`;
  }
  const resp = await fetch(url);
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  return resp.json();
}

async function cargarYRenderizarHistorico(id) {
  const ind = _indicadorPorId(id);
  const chartEl = document.querySelector(`.macro-item-chart[data-hist-id="${id}"]`);
  if (!ind || !chartEl) return;
  const modoCompleto = macroHistoricoState.completo.has(id);
  const claveCache = modoCompleto ? "completo" : "ventana";
  if (!macroHistoricoState.cache[id]) macroHistoricoState.cache[id] = {};
  let puntos = macroHistoricoState.cache[id][claveCache];
  if (!puntos) {
    chartEl.innerHTML = `<p class="eventos-vacio">Cargando...</p>`;
    try {
      puntos = await _fetchSerieIndicador(ind, modoCompleto);
    } catch (err) {
      chartEl.innerHTML = `<p class="eventos-vacio">No se pudo cargar la serie: ${escapeAttr(err.message)}</p>`;
      return;
    }
    macroHistoricoState.cache[id][claveCache] = puntos;
  }

  // Serie de menos de 10 anios: la ventana ya es toda la historia, no hay
  // nada mas para expandir.
  const rangoLabel = modoCompleto
    ? `Serie completa (${puntos.length} puntos)`
    : ind.historia_corta ? `Toda la historia disponible (${puntos.length} puntos)` : "Últimos 10 años";
  const toggleRangoBtn = ind.historia_corta
    ? ""
    : `<button type="button" class="macro-chart-rango-btn" data-hist-id="${id}">${modoCompleto ? "Ver últimos 10 años" : "Ver todo el historial"}</button>`;

  chartEl.innerHTML = `
    <div class="macro-chart-header">
      <span class="macro-chart-rango">${rangoLabel}</span>
      ${toggleRangoBtn}
    </div>
    <svg class="macro-chart-svg" viewBox="0 0 800 140" preserveAspectRatio="none"></svg>
  `;
  if (!puntos.length) {
    chartEl.querySelector(".macro-chart-header").insertAdjacentHTML(
      "afterend", `<p class="eventos-vacio">Sin datos históricos disponibles.</p>`,
    );
    return;
  }
  const a = ind.anotacion || {};
  const lineasRef = [];
  if (a.umbral_valor != null) lineasRef.push({ valor: a.umbral_valor, clase: "macro-chart-umbral", etiqueta: "umbral" });
  if (a.objetivo_valor != null) lineasRef.push({ valor: a.objetivo_valor, clase: "macro-chart-objetivo", etiqueta: "objetivo" });
  // Mediana de 10 anios (Tanda 4): contexto, no decide colores.
  if (ind.mediana_10a != null) lineasRef.push({ valor: ind.mediana_10a, clase: "macro-chart-mediana", etiqueta: "mediana 10a", posicion: "centro" });
  renderMiniLineChart(chartEl.querySelector(".macro-chart-svg"), puntos, {
    decimales: ind.decimales, sufijo: SUFIJO_UNIDAD[ind.unidad] || "", lineasRef,
  });
}

// Contexto que no es la serie en si: horizontes del REM, rango objetivo y
// probabilidades de la Fed, MEP/CCL de la brecha, acumulado de compras.
function _detalleIndicador(ind) {
  const d = ind.detalle;
  if (!d) return "";
  if (ind.id === "rem_inflacion_12m") {
    const partes = [];
    if (d.inflacion_anio_actual_pct != null) partes.push(`${d.anio_actual}: ${pctPlano(d.inflacion_anio_actual_pct)}`);
    if (d.inflacion_anio_sig_pct != null) partes.push(`${d.anio_actual + 1}: ${pctPlano(d.inflacion_anio_sig_pct)}`);
    if (d.tamar_tna_prox12m_pct != null) partes.push(`TAMAR ${pctPlano(d.tamar_tna_prox12m_pct)}`);
    if (d.tc_usd_prox12m != null) partes.push(`$/USD próx. 12m: $${Math.round(d.tc_usd_prox12m).toLocaleString("es-AR")}`);
    // Bugfix (2026-09-15, el usuario): la estimacion de fin de año es un dato
    // distinto del rolling 12m ("fin de este año" vs. "de aca a 12 meses").
    if (d.tc_usd_anio_actual != null) partes.push(`$/USD fin de año: $${Math.round(d.tc_usd_anio_actual).toLocaleString("es-AR")}`);
    return partes.join(" · ");
  }
  if (ind.id === "effr") {
    const partes = [];
    if (d.target_low_pct != null) partes.push(`objetivo ${d.target_low_pct.toFixed(2)}–${d.target_high_pct.toFixed(2)}%`);
    if (d.prox_reunion_fomc) partes.push(`próx. FOMC ${fechaCorta(d.prox_reunion_fomc)}`);
    if (d.probabilidades) {
      const probStr = Object.entries(d.probabilidades)
        .sort((a, b) => b[1] - a[1]).slice(0, 2)
        .map(([r, p]) => `${r} ${p}%`).join(" · ");
      partes.push(`prob.: ${probStr}${d.sesgo ? ` (sesgo ${d.sesgo})` : ""}`);
    }
    return partes.join("<br>");
  }
  if (ind.id === "ccl_mep_brecha_pct") {
    return `MEP ${d.mep.toLocaleString("es-AR")} · CCL ${d.ccl.toLocaleString("es-AR")}`;
  }
  if (ind.id === "prima_riesgo_acciones") {
    return `rendimiento de ganancias ${pctPlano(d.rendimiento_ganancias, 2)} (P/E ${d.pe_forward.toLocaleString("es-AR")}) − TIPS ${pctPlano(d.tips_10y, 2)}`;
  }
  if (ind.id === "compras_bcra") {
    return `últimas 20 ruedas: ${fmtDeltaIndicador(d.acumulado_20_ruedas, ind)} (desde ${fechaCorta(d.desde)})`;
  }
  return "";
}

function _anotacionResumen(ind) {
  const a = ind.anotacion;
  if (!a) return "";
  const partes = [];
  if (a.umbral_valor != null) {
    partes.push(`umbral ${a.umbral_direccion === "mayor_que" ? "&gt;" : "&lt;"} ${fmtValorIndicador(a.umbral_valor, ind)}`);
  }
  if (a.objetivo_valor != null) partes.push(`objetivo ${fmtValorIndicador(a.objetivo_valor, ind)}`);
  return (partes.length ? `<p class="macro-item-anotacion">${partes.join(" · ")}</p>` : "")
    + (a.nota ? `<p class="macro-item-nota">${escapeAttr(a.nota)}</p>` : "");
}

function _formAnotacion(ind) {
  const a = ind.anotacion || {};
  const val = (v) => (v == null ? "" : v);
  const sel = (op) => (a.umbral_direccion === op ? "selected" : "");
  const selD3m = (op) => (a.umbral_d3m_direccion === op ? "selected" : "");
  const unidadD3m = unidadDeltaIndicador(ind);
  return `
    <form class="macro-anotacion-form" data-ind-id="${ind.id}">
      <label class="macro-anotacion-campo">Umbral
        <select name="umbral_direccion">
          <option value="">sin umbral</option>
          <option value="mayor_que" ${sel("mayor_que")}>&gt; mayor que</option>
          <option value="menor_que" ${sel("menor_que")}>&lt; menor que</option>
        </select>
        <input type="number" step="any" name="umbral_valor" placeholder="valor (${escapeAttr(ind.unidad)})" value="${val(a.umbral_valor)}" />
      </label>
      <label class="macro-anotacion-campo" title="Se compara contra el Δ3m de la tarjeta, en ${escapeAttr(unidadD3m)}">Umbral de velocidad (Δ3m)
        <select name="umbral_d3m_direccion">
          <option value="">sin umbral</option>
          <option value="mayor_que" ${selD3m("mayor_que")}>&gt; mayor que</option>
          <option value="menor_que" ${selD3m("menor_que")}>&lt; menor que</option>
        </select>
        <input type="number" step="any" name="umbral_d3m_valor" placeholder="Δ3m en ${escapeAttr(unidadD3m)}" value="${val(a.umbral_d3m_valor)}" />
        <span class="macro-anotacion-unidad">Δ3m en ${escapeAttr(unidadD3m)}</span>
      </label>
      <label class="macro-anotacion-campo">Objetivo
        <input type="number" step="any" name="objetivo_valor" placeholder="nivel de referencia" value="${val(a.objetivo_valor)}" />
      </label>
      <textarea name="nota" rows="2" placeholder="Por qué ese umbral, qué significa para la cartera">${escapeAttr(a.nota || "")}</textarea>
      <div class="macro-anotacion-botones">
        <button type="submit" class="simulador-btn-sec">Guardar</button>
        <button type="button" class="macro-anotacion-cancelar">Cancelar</button>
      </div>
      <p class="macro-anotacion-error" hidden></p>
    </form>`;
}

// Fecha del dato segun la frecuencia (ajuste 2026-09-30): FRED fecha las
// series trimestrales al INICIO del trimestre -- "dato al 01-abr-26" para un
// nivel del 30-06 confundia. Mensual -> "ago-26", trimestral -> "2T-26",
// anual -> "2025"; diaria/semanal, el dia.
function _fechaDato(ind) {
  const [y, m] = ind.fecha.split("-");
  const meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"];
  if (ind.frecuencia === "mensual") return `${meses[parseInt(m, 10) - 1]}-${y.slice(2)}`;
  if (ind.frecuencia === "trimestral") return `${Math.floor((parseInt(m, 10) - 1) / 3) + 1}T-${y.slice(2)}`;
  if (ind.frecuencia === "anual") return y;
  return fechaCorta(ind.fecha);
}

// Umbral de velocidad (v2.44) junto al Δ3m: "(umbral > +150 pb)". El
// grafico no lo dibuja (no esta en la escala del nivel).
function _umbralD3mTexto(ind) {
  const a = ind.anotacion;
  if (!a || a.umbral_d3m_valor == null) return "";
  const op = a.umbral_d3m_direccion === "mayor_que" ? "&gt;" : "&lt;";
  return ` <span class="macro-item-umbral-d3m">(umbral ${op} ${fmtDeltaIndicador(a.umbral_d3m_valor, ind)})</span>`;
}

// Un badge por cada umbral cruzado: ⚠️ nivel, ⚡ velocidad (los dos si
// cruzan ambos). El borde/fondo de alerta de la tarjeta es el mismo para
// los dos; el icono dice cual salto.
function _badgesUmbral(ind) {
  const b = [];
  if (ind.umbral_nivel_cruzado) b.push(`<span class="tesis-card-alerta" title="El valor cruzó el umbral de nivel">⚠️ nivel</span>`);
  if (ind.umbral_d3m_cruzado) b.push(`<span class="tesis-card-alerta" title="El Δ3m cruzó el umbral de velocidad">⚡ velocidad</span>`);
  return b.length ? ` ${b.join(" ")}` : "";
}

function _macroCard(ind) {
  const abierto = macroHistoricoState.abiertos.has(ind.id);
  const editando = macroHistoricoState.editando === ind.id;
  const sinDatos = ind.valor == null;
  const deltas = sinDatos ? "" : `
      <p class="macro-item-deltas">
        <span title="${ind.fecha_ref_3m ? `vs. ${fechaCorta(ind.fecha_ref_3m)}` : "sin dato de hace 3 meses"}">3m ${fmtDeltaIndicador(ind.delta_3m, ind)}</span>${_umbralD3mTexto(ind)} ·
        <span title="${ind.fecha_ref_12m ? `vs. ${fechaCorta(ind.fecha_ref_12m)}` : "sin dato de hace 12 meses"}">12m ${fmtDeltaIndicador(ind.delta_12m, ind)}</span>
      </p>`;
  const pctil = ind.percentil_10a == null ? "" : `
      <p class="macro-item-pctil" title="Posición del valor actual entre las observaciones de los últimos 10 años (0 = mínimo, 100 = máximo)">
        percentil 10 años: <strong>${Math.round(ind.percentil_10a)}</strong>${ind.historia_corta ? ` · ${_fmtNum(ind.anios_historia, 1)} años de historia` : ""}
        ${ind.mediana_10a != null ? `<br>mediana ${ind.historia_corta ? "del período" : "10 años"}: ${fmtValorIndicador(ind.mediana_10a, ind)}` : ""}
        ${ind.nota_ventana ? `<br><em>${escapeAttr(ind.nota_ventana)}</em>` : ""}
      </p>`;
  const detalle = _detalleIndicador(ind);
  const hist = sinDatos ? "" : `
      <button type="button" class="macro-item-toggle" data-hist-id="${ind.id}">${abierto ? "▾ Ocultar histórico" : "▸ Ver histórico"}</button>
      <div class="macro-item-chart" data-hist-id="${ind.id}" ${abierto ? "" : "hidden"}></div>`;
  return `
    <div class="macro-item${ind.umbral_cruzado ? " macro-item-cruzado" : ""}${abierto || editando ? " macro-item-expandido" : ""}" data-ind-id="${ind.id}">
      <div class="macro-item-cabecera">
        <p class="macro-item-label" title="${escapeAttr(`${ind.fuente} — ${ind.serie_origen}`)}">${_puntoSemaforo(ind)}${escapeAttr(ind.nombre)}</p>
        <button type="button" class="macro-item-editar" data-ind-id="${ind.id}" title="Escribir umbral, objetivo o nota">✎</button>
      </div>
      <p class="macro-item-valor">${fmtValorIndicador(ind.valor, ind)}${_badgesUmbral(ind)}</p>
      ${deltas}
      ${pctil}
      ${detalle ? `<p class="macro-item-detalle">${detalle}</p>` : ""}
      ${_anotacionResumen(ind)}
      <p class="macro-item-fecha">${sinDatos ? "sin datos todavía" : `dato: ${_fechaDato(ind)}`}</p>
      ${hist}
      ${editando ? _formAnotacion(ind) : ""}
    </div>`;
}

// ============================================================
// Panel "Maquina economica" (Epica 21, Tanda 4, 2026-09-30) -- GET
// /api/maquina-economica. Cada semaforo toma el peor color de sus
// indicadores segun los umbrales/objetivos del usuario (sin umbral = gris, no
// cuenta); la "fase actual" se escribe a mano y cada revision queda
// guardada (POST .../fase), nunca se pisa.
// ============================================================
const NOMBRE_SEMAFORO = { productividad: "Productividad", ciclo_corto: "Ciclo corto", ciclo_largo: "Ciclo largo", valuacion: "Valuación" };
const maquinaState = { editando: null, historial: {} };  // historial: semaforo -> lista (si esta abierto)

function _puntoSemaforo(ind) {
  if (!ind.semaforo) return "";
  const texto = ind.color === "gris" ? "sin umbral cargado, no cuenta" : ind.color;
  return `<span class="punto-color punto-${ind.color}" title="Semáforo ${NOMBRE_SEMAFORO[ind.semaforo]}: ${texto}"></span>`;
}

function _semaforoCard(s) {
  const editando = maquinaState.editando === s.id;
  const historial = maquinaState.historial[s.id];
  const define = s.define
    ? `lo define: ${escapeAttr(s.define.nombre)} (${fmtValorIndicador(s.define.valor, s.define)})`
    : "ningún indicador con umbral cargado";
  const miembros = s.indicadores.map((i) => `
      <li><span class="punto-color punto-${i.color}"></span>${escapeAttr(i.nombre)}
        <span class="maquina-valor">${fmtValorIndicador(i.valor, i)}</span></li>`).join("");
  const fase = s.fase
    ? `<p class="maquina-fase">${escapeAttr(s.fase.fase)}</p><p class="macro-item-fecha">revisada el ${fechaCorta(s.fase.fecha)}</p>`
    : `<p class="maquina-fase maquina-fase-vacia">Sin fase escrita todavía.</p>`;
  const form = editando ? `
      <form class="maquina-fase-form" data-semaforo="${s.id}">
        <input type="date" name="fecha" value="${new Date().toISOString().slice(0, 10)}" required />
        <textarea name="fase" rows="3" required placeholder="Ej.: fin de expansión, curva re-empinándose, Fed en pausa">${s.fase ? escapeAttr(s.fase.fase) : ""}</textarea>
        <div class="macro-anotacion-botones">
          <button type="submit" class="simulador-btn-sec">Guardar revisión</button>
          <button type="button" class="macro-anotacion-cancelar maquina-cancelar">Cancelar</button>
        </div>
        <p class="macro-anotacion-error" hidden></p>
      </form>` : "";
  const lista = historial ? `
      <ul class="maquina-historial">${historial.length
        ? historial.map((h) => `<li><strong>${fechaCorta(h.fecha)}</strong> ${escapeAttr(h.fase)}</li>`).join("")
        : "<li>Sin revisiones.</li>"}</ul>` : "";
  return `
    <div class="maquina-card maquina-${s.color}" data-semaforo="${s.id}">
      <div class="macro-item-cabecera">
        <p class="maquina-titulo" title="${define}"><span class="punto-color punto-${s.color} punto-grande"></span>${s.nombre}</p>
        <button type="button" class="macro-item-editar maquina-editar" data-semaforo="${s.id}" title="Escribir la fase actual">✎</button>
      </div>
      <p class="macro-item-detalle">${define}</p>
      <ul class="maquina-miembros">${miembros}</ul>
      ${fase}
      ${form}
      ${s.revisiones ? `<button type="button" class="macro-item-toggle maquina-ver-historial" data-semaforo="${s.id}">${historial ? "▾" : "▸"} Historial de fases (${s.revisiones})</button>` : ""}
      ${lista}
    </div>`;
}

let maquinaData = null;

function renderMaquina(data) {
  maquinaData = data;
  document.getElementById("maquina-grid").innerHTML = data.semaforos.map(_semaforoCard).join("");
  const t = data.tesis_macro;
  document.getElementById("maquina-tesis").innerHTML = t
    ? `Tesis macro vigente: <a href="#" class="maquina-link-tesis" data-id="${t.id}">${fechaCorta(t.fecha)} — ${escapeAttr(t.tesis.length > 140 ? t.tesis.slice(0, 140) + "…" : t.tesis)}</a>`
    : "Sin tesis vigente en la bitácora de la cartera (Dashboard → Metas y tesis de la cartera).";
}

async function cargarMaquina() {
  const resp = await fetch("/api/maquina-economica");
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  renderMaquina(await resp.json());
}

function wireMaquina() {
  const panel = document.getElementById("panel-maquina");
  panel.addEventListener("click", async (e) => {
    const link = e.target.closest(".maquina-link-tesis");
    if (link) {
      e.preventDefault();
      cambiarVista("dashboard");
      const destino = document.querySelector(`#panel-tesis-cartera .tesis-card[data-id="${link.dataset.id}"]`)
        || document.getElementById("panel-tesis-cartera");
      destino.scrollIntoView({ behavior: "smooth", block: "center" });
      return;
    }
    const editar = e.target.closest(".maquina-editar");
    if (editar) {
      maquinaState.editando = maquinaState.editando === editar.dataset.semaforo ? null : editar.dataset.semaforo;
      renderMaquina(maquinaData);
      return;
    }
    if (e.target.closest(".maquina-cancelar")) {
      maquinaState.editando = null;
      renderMaquina(maquinaData);
      return;
    }
    const verHist = e.target.closest(".maquina-ver-historial");
    if (verHist) {
      const sid = verHist.dataset.semaforo;
      if (maquinaState.historial[sid]) {
        delete maquinaState.historial[sid];
      } else {
        const resp = await fetch(`/api/maquina-economica/${sid}/fases`);
        maquinaState.historial[sid] = resp.ok ? await resp.json() : [];
      }
      renderMaquina(maquinaData);
    }
  });
  panel.addEventListener("submit", async (e) => {
    const form = e.target.closest(".maquina-fase-form");
    if (!form) return;
    e.preventDefault();
    const sid = form.dataset.semaforo;
    const resp = await fetch(`/api/maquina-economica/${sid}/fase`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ fase: form.elements.fase.value, fecha: form.elements.fecha.value || null }),
    });
    if (!resp.ok) {
      const det = await resp.json().catch(() => ({}));
      const err = form.querySelector(".macro-anotacion-error");
      err.textContent = `No se guardó: ${typeof det.detail === "string" ? det.detail : `HTTP ${resp.status}`}`;
      err.hidden = false;
      return;
    }
    maquinaState.editando = null;
    if (maquinaState.historial[sid]) delete maquinaState.historial[sid];
    await cargarMaquina();
  });
}

function _macroSeccion(titulo, items) {
  if (!items.length) return "";
  return `<h3 class="macro-seccion-titulo">${titulo}</h3>${items.join("")}`;
}

function renderMacroPanel(indicadores) {
  indicadoresMacro = indicadores;
  const secciones = new Map();       // en el orden del catalogo (`orden`)
  for (const ind of indicadores) {
    if (!secciones.has(ind.seccion)) secciones.set(ind.seccion, []);
    secciones.get(ind.seccion).push(_macroCard(ind));
  }
  const html = [...secciones].map(([titulo, items]) => _macroSeccion(escapeAttr(titulo), items)).join("");
  document.getElementById("macro-grid").innerHTML =
    html || `<p class="eventos-vacio">Sin indicadores en el catálogo todavía (correr migrar_v239_indicadores_macro.py).</p>`;
  for (const id of macroHistoricoState.abiertos) cargarYRenderizarHistorico(id);
}

async function recargarIndicadoresMacro() {
  const resp = await fetch("/api/indicadores-macro");
  if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
  renderMacroPanel(await resp.json());
  // Los colores de los semaforos salen de las anotaciones: se recalculan juntos.
  try {
    await cargarMaquina();
  } catch (err) {
    document.getElementById("maquina-grid").innerHTML =
      `<p class="eventos-vacio">No se pudo cargar la máquina económica: ${escapeAttr(err.message)}</p>`;
  }
}

function wireMacroPanel() {
  const grid = document.getElementById("macro-grid");
  grid.addEventListener("click", async (e) => {
    const btnRango = e.target.closest(".macro-chart-rango-btn");
    if (btnRango) {
      const id = btnRango.dataset.histId;
      if (macroHistoricoState.completo.has(id)) macroHistoricoState.completo.delete(id);
      else macroHistoricoState.completo.add(id);
      await cargarYRenderizarHistorico(id);
      return;
    }

    const btnEditar = e.target.closest(".macro-item-editar");
    if (btnEditar) {
      const id = btnEditar.dataset.indId;
      macroHistoricoState.editando = macroHistoricoState.editando === id ? null : id;
      renderMacroPanel(indicadoresMacro);
      return;
    }

    if (e.target.closest(".macro-anotacion-cancelar")) {
      macroHistoricoState.editando = null;
      renderMacroPanel(indicadoresMacro);
      return;
    }

    const btnToggle = e.target.closest(".macro-item-toggle");
    if (!btnToggle) return;
    const id = btnToggle.dataset.histId;
    const chartEl = grid.querySelector(`.macro-item-chart[data-hist-id="${id}"]`);
    const item = btnToggle.closest(".macro-item");
    if (macroHistoricoState.abiertos.has(id)) {
      macroHistoricoState.abiertos.delete(id);
      chartEl.hidden = true;
      if (macroHistoricoState.editando !== id) item.classList.remove("macro-item-expandido");
      btnToggle.textContent = "▸ Ver histórico";
      return;
    }
    macroHistoricoState.abiertos.add(id);
    chartEl.hidden = false;
    item.classList.add("macro-item-expandido");
    btnToggle.textContent = "▾ Ocultar histórico";
    await cargarYRenderizarHistorico(id);
  });

  grid.addEventListener("submit", async (e) => {
    const form = e.target.closest(".macro-anotacion-form");
    if (!form) return;
    e.preventDefault();
    const num = (nombre) => {
      const v = form.elements[nombre].value.trim();
      return v === "" ? null : Number(v);
    };
    const body = {
      umbral_direccion: form.elements.umbral_direccion.value || null,
      umbral_valor: num("umbral_valor"),
      umbral_d3m_direccion: form.elements.umbral_d3m_direccion.value || null,
      umbral_d3m_valor: num("umbral_d3m_valor"),
      objetivo_valor: num("objetivo_valor"),
      nota: form.elements.nota.value.trim() || null,
    };
    const errorEl = form.querySelector(".macro-anotacion-error");
    const resp = await fetch(`/api/indicadores-macro/${encodeURIComponent(form.dataset.indId)}/anotacion`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const det = await resp.json().catch(() => ({}));
      errorEl.textContent = `No se guardó: ${typeof det.detail === "string" ? det.detail : `HTTP ${resp.status}`}`;
      errorEl.hidden = false;
      return;
    }
    macroHistoricoState.editando = null;
    await recargarIndicadoresMacro();
  });
}

// ============================================================
// Paneles: Breakeven de inflacion / Devaluacion implicita (2026-09-11,
// pedido del usuario). Mismo shape en las 2 tablas (bono ancla + bono
// emparejado por duration mas cercana + spread) -- comparten el helper
// de orden, cada uno con su propio estado de sort.
// ============================================================

let breakevenInflacionData = [];
let breakevenInflacionSort = { campo: "duration_cer", desc: false };
let devaluacionImplicitaData = [];
let devaluacionImplicitaSort = { campo: "duration_ars", desc: false };

function _ordenarPorCampo(filas, sort) {
  const { campo, desc } = sort;
  return filas.slice().sort((a, b) => {
    let x = a[campo], y = b[campo];
    if (x === null || x === undefined) return 1;
    if (y === null || y === undefined) return -1;
    if (typeof x === "string") return desc ? y.localeCompare(x) : x.localeCompare(y);
    return desc ? y - x : x - y;
  });
}

function _wireSortTabla(tablaId, obtenerSort, setSort, rerender) {
  document.querySelectorAll(`#${tablaId} thead th[data-sort]`).forEach((th) => {
    th.style.cursor = "pointer";
    th.addEventListener("click", () => {
      const campo = th.dataset.sort;
      const actual = obtenerSort();
      if (actual.campo === campo) setSort({ campo, desc: !actual.desc });
      else setSort({ campo, desc: true });
      rerender();
    });
  });
}

// Badge verde junto al ticker que conviene segun el REM (Epica 14
// Parte 1, 2026-09-14) -- vacio si `conviene` no matchea este lado del
// par (o es null, empate tecnico/sin dato de REM).
function _convieneBadge(conviene, ladoDeEstaCelda) {
  return conviene === ladoDeEstaCelda ? `<span class="conviene-badge" title="Conviene según REM">✓</span>` : "";
}

function renderBreakevenInflacion() {
  const filas = _ordenarPorCampo(breakevenInflacionData, breakevenInflacionSort);
  const remPct = filas.find((f) => f.inflacion_esperada_rem_pct != null)?.inflacion_esperada_rem_pct;
  const leyenda = remPct != null
    ? `<br>✓ = conviene según inflación esperada REM (próx. 12m): ${pctPlano(remPct)}`
    : "";
  document.getElementById("breakeven-inflacion-subtitulo").innerHTML =
    `${filas.length} bonos CER emparejados con su tasa fija de duration más cercana.${leyenda}`;
  document.getElementById("breakeven-inflacion-body").innerHTML = filas.map((f) => `
    <tr>
      <td>${f.ticker_cer}${_convieneBadge(f.conviene, "cer")}</td>
      <td class="eventos-monto">${fmtNum(f.duration_cer)}</td>
      <td class="eventos-monto">${fmtPct(f.tir_cer_pct)}</td>
      <td>${f.ticker_tasa_fija}${_convieneBadge(f.conviene, "tasa_fija")}</td>
      <td class="mercado-cat-celda">${f.categoria_tasa_fija_label}</td>
      <td class="eventos-monto">${fmtNum(f.duration_tasa_fija)}</td>
      <td class="eventos-monto">${fmtPct(f.tir_tasa_fija_pct)}</td>
      <td class="eventos-monto">${fmtNum(f.brecha_duration)}</td>
      <td class="eventos-monto"><strong>${fmtPct(f.breakeven_inflacion_pct)}</strong></td>
    </tr>
  `).join("") || `<tr><td colspan="9" class="eventos-vacio">Sin datos.</td></tr>`;
}

function renderDevaluacionImplicita() {
  const filas = _ordenarPorCampo(devaluacionImplicitaData, devaluacionImplicitaSort);
  const remPct = filas.find((f) => f.devaluacion_esperada_rem_pct != null)?.devaluacion_esperada_rem_pct;
  const leyenda = remPct != null
    ? `<br>✓ = conviene según devaluación esperada REM (próx. 12m): ${pctPlano(remPct)}`
    : "";
  document.getElementById("devaluacion-implicita-subtitulo").innerHTML =
    `${filas.length} bonos a tasa fija (ARS) emparejados con su dólar de duration más cercana.${leyenda}`;
  document.getElementById("devaluacion-implicita-body").innerHTML = filas.map((f) => `
    <tr>
      <td>${f.ticker_ars}${_convieneBadge(f.conviene, "ars")}</td>
      <td class="eventos-monto">${fmtNum(f.duration_ars)}</td>
      <td class="eventos-monto">${fmtPct(f.tir_ars_pct)}</td>
      <td>${f.ticker_usd}${_convieneBadge(f.conviene, "usd")}</td>
      <td class="mercado-cat-celda">${f.categoria_usd_label}</td>
      <td class="eventos-monto">${fmtNum(f.duration_usd)}</td>
      <td class="eventos-monto">${fmtPct(f.tir_usd_pct)}</td>
      <td class="eventos-monto">${fmtNum(f.brecha_duration)}</td>
      <td class="eventos-monto"><strong>${fmtPct(f.devaluacion_implicita_pct)}</strong></td>
    </tr>
  `).join("") || `<tr><td colspan="9" class="eventos-vacio">Sin datos.</td></tr>`;
}

function wireBreakevenDevaluacion() {
  _wireSortTabla("breakeven-inflacion-tabla",
    () => breakevenInflacionSort, (s) => { breakevenInflacionSort = s; }, renderBreakevenInflacion);
  _wireSortTabla("devaluacion-implicita-tabla",
    () => devaluacionImplicitaSort, (s) => { devaluacionImplicitaSort = s; }, renderDevaluacionImplicita);
}

// ============================================================
// Panel: Comparador de ratio entre 2 bonos (Epica 14 Parte 2,
// 2026-09-14) -- el "ratio" que siguen los brokers
// (ej. AL30/GD30, ley local vs. ley NY). Distinto de breakeven/
// devaluacion arriba (que emparejan automatico por duration MAS
// CERCANA dentro de una categoria) -- aca el par lo elige el usuario a
// mano. Fuente: `precios` (cotizacion real de Rava, no
// mercado_renta_fija_historico -- ver ratio_bonos.py para el porque:
// mucho mas historial, pero acotado a los tickers que el usuario alguna vez
// opero). Reusa renderMiniLineChart tal cual (mismo componente que los
// historicos de "Indicadores Macro", Epica 13) -- sin libreria nueva.
// ============================================================

// Por debajo de esto se suma el aviso "Solo N dias" AL LADO del
// grafico (no en vez de) -- con pocos puntos el grafico solo no da
// contexto de cuanto historial hay realmente detras.
const RATIO_BONOS_DIAS_POCOS = 10;
const RATIO_BONOS_VENTANA_DIAS_DEFAULT = 730; // 2 años, mismo default que riesgo_pais/inflacion_interanual (Epica 13)

let ratioBonosUniverso = [];
const ratioBonosState = { tickerA: "AL30", tickerB: "GD30", completo: false };

function _ratioBonosOptionLabel(b) {
  // categoria + moneda (de `precios`, ya clasificada al cargar) junto
  // al ticker -- para que el usuario vea de un vistazo si el par tiene
  // sentido antes de graficar (ej. AL30 "Pesos" vs. AL30D "Dólares":
  // misma emision, especies en moneda distinta -- no comparables).
  return `${b.ticker} — ${b.categoria_label} · ${b.moneda}`;
}

function renderRatioBonosSelects() {
  const especies = [...ratioBonosUniverso].sort((a, b) => a.ticker.localeCompare(b.ticker));
  const opciones = especies.map((b) => `<option value="${b.ticker}">${_ratioBonosOptionLabel(b)}</option>`).join("");
  const selA = document.getElementById("ratio-bonos-ticker-a");
  const selB = document.getElementById("ratio-bonos-ticker-b");
  selA.innerHTML = opciones;
  selB.innerHTML = opciones;

  const tickers = new Set(especies.map((b) => b.ticker));
  selA.value = tickers.has(ratioBonosState.tickerA) ? ratioBonosState.tickerA : (especies[0]?.ticker || "");
  selB.value = tickers.has(ratioBonosState.tickerB) ? ratioBonosState.tickerB : (especies[1]?.ticker || "");
  ratioBonosState.tickerA = selA.value;
  ratioBonosState.tickerB = selB.value;
}

const RATIO_BONOS_PRECIOS_FILAS = 15;

// Precio crudo tal cual lo devuelve Rava (mismo dato que ya usa el
// ratio, no convertido a USD) -- pedido del usuario 2026-09-18: "que
// muestre los precios", no solo el ratio ya calculado. Ultimos N dias,
// mas reciente primero (la serie que llega esta ordenada ASC para el
// grafico -- se invierte solo para esta tabla).
function renderRatioBonosPreciosTabla(puntos) {
  const body = document.getElementById("ratio-bonos-precios-body");
  if (!puntos.length) {
    body.innerHTML = `<tr><td colspan="4" class="eventos-vacio">Sin datos.</td></tr>`;
    return;
  }
  const ultimos = puntos.slice(-RATIO_BONOS_PRECIOS_FILAS).reverse();
  body.innerHTML = ultimos.map((p) => `
    <tr>
      <td>${p.fecha}</td>
      <td style="text-align:right">${fmtNum(p.precio_a)}</td>
      <td style="text-align:right">${fmtNum(p.precio_b)}</td>
      <td style="text-align:right">${p.ratio == null ? "—" : fmtNum(p.ratio, 4)}</td>
    </tr>`).join("");
}

async function renderRatioBonosChart() {
  const { tickerA, tickerB, completo } = ratioBonosState;
  const svgEl = document.getElementById("ratio-bonos-svg");
  const vacioEl = document.getElementById("ratio-bonos-vacio");
  const rangoBtn = document.getElementById("ratio-bonos-rango-btn");
  const promedioEl = document.getElementById("ratio-bonos-promedio");
  if (!tickerA || !tickerB) return;

  document.getElementById("ratio-bonos-subtitulo").textContent = `${tickerA} / ${tickerB}`;
  promedioEl.textContent = "";
  document.getElementById("ratio-bonos-precios-th-a").textContent = `Precio ${tickerA}`;
  document.getElementById("ratio-bonos-precios-th-b").textContent = `Precio ${tickerB}`;

  const desde = completo ? null : new Date(Date.now() - RATIO_BONOS_VENTANA_DIAS_DEFAULT * 86400000).toISOString().slice(0, 10);
  const url = `/api/bonos-ratio-historico?ticker_a=${encodeURIComponent(tickerA)}&ticker_b=${encodeURIComponent(tickerB)}`
    + (desde ? `&desde=${desde}` : "");
  const puntos = await (await fetch(url)).json();

  renderRatioBonosPreciosTabla(puntos);

  if (!puntos.length) {
    svgEl.hidden = true;
    rangoBtn.hidden = true;
    document.getElementById("ratio-bonos-rango").textContent = "";
    vacioEl.hidden = false;
    vacioEl.textContent = `Sin historial en común entre ${tickerA} y ${tickerB} todavía — ningún día con cotización de los dos a la vez.`;
    return;
  }

  svgEl.hidden = false;
  rangoBtn.hidden = false;
  rangoBtn.textContent = completo ? "Ver ventana reciente" : "Ver todo el historial";
  document.getElementById("ratio-bonos-rango").textContent = completo
    ? `Serie completa (${puntos.length} días)`
    : `Últimos ${Math.round(RATIO_BONOS_VENTANA_DIAS_DEFAULT / 365)} año(s) (${puntos.length} días)`;
  renderMiniLineChart(svgEl, puntos.map((p) => ({ fecha: p.fecha, valor: p.ratio })), { decimales: 3, promedio: true });

  // Mismo promedio que dibuja la linea punteada del grafico (formula
  // identica, recalculada aca solo para el texto,
  // 2026-09-14: que quede tambien como numero debajo del tag ticker/ticker).
  const promedio = puntos.reduce((acc, p) => acc + p.ratio, 0) / puntos.length;
  promedioEl.textContent = `Promedio del período: ${promedio.toLocaleString("es-AR", { maximumFractionDigits: 3 })}`;

  if (puntos.length < RATIO_BONOS_DIAS_POCOS) {
    vacioEl.hidden = false;
    vacioEl.textContent = `Solo ${puntos.length} día${puntos.length === 1 ? "" : "s"} de historial disponibles para este par — el gráfico va a ganar contexto con el correr de los días.`;
  } else {
    vacioEl.hidden = true;
  }
}

function wireRatioBonos() {
  const selA = document.getElementById("ratio-bonos-ticker-a");
  const selB = document.getElementById("ratio-bonos-ticker-b");
  selA.addEventListener("change", () => { ratioBonosState.tickerA = selA.value; renderRatioBonosChart(); });
  selB.addEventListener("change", () => { ratioBonosState.tickerB = selB.value; renderRatioBonosChart(); });
  document.getElementById("ratio-bonos-rango-btn").addEventListener("click", () => {
    ratioBonosState.completo = !ratioBonosState.completo;
    renderRatioBonosChart();
  });
}

// ============================================================
// Panel: tenencias de bonos por categoria (barras rankeadas, 2026-09-09)
// ============================================================

function renderBonosCategorias(datos) {
  document.getElementById("bonos-cat-subtitulo").textContent =
    `Total renta fija ${fmtUSD(datos.total_usd)} — ${datos.categorias.length} categorías con tenencia.`;
  const cont = document.getElementById("bonos-cat-bars");
  if (!datos.categorias.length) {
    cont.innerHTML = `<p class="eventos-vacio">Sin bonos en cartera.</p>`;
    return;
  }
  const max = Math.max(...datos.categorias.map((c) => c.valor_usd), 1e-9);
  cont.innerHTML = datos.categorias.map((c) => {
    const color = _colorCategoria(c.categoria);
    const ancho = Math.max((c.valor_usd / max) * 100, 2);
    return `
      <div class="rank-bar-fila" title="${c.categoria_label}: ${fmtUSD(c.valor_usd)} (${c.pct}%)">
        <span class="rank-bar-nombre"><span class="swatch" style="background:${color}"></span>${c.categoria_label}</span>
        <span class="rank-bar-pista"><span class="rank-bar-relleno" style="width:${ancho}%;background:${color}"></span></span>
        <span class="rank-bar-valor"><strong>${c.pct}%</strong> · ${fmtUSD(c.valor_usd)}</span>
      </div>`;
  }).join("");
}

// Sensibilidad a tasas por categoria (BPV/DV01, 2026-09-11) -- mismo
// componente visual de barras rankeadas y mismos colores por categoria
// que "Tenencias por categoria", al lado (misma fila via .bonos-cat-row)
// para comparar concentracion por VALOR vs. concentracion de RIESGO DE
// TASA de un vistazo. bpv_usd ya viene en magnitud positiva del backend
// (ver tir_bonos.py::bpv_cartera) -- se muestra en negativo, misma
// lectura que la tarjeta del header.
function renderBonosSensibilidadCategoria(datos) {
  const totalNeg = -datos.bpv_total_usd;
  const carryTxt = datos.carry_pct_total != null ? ` · Carry total ${datos.carry_pct_total.toFixed(2)}%` : "";
  document.getElementById("bonos-sens-subtitulo").textContent =
    `${fmtUSD(totalNeg)} por +1 pb de suba de tasas${carryTxt} — ${datos.categorias.length} categorías con exposición.`;
  const cont = document.getElementById("bonos-sens-bars");
  if (!datos.categorias.length) {
    cont.innerHTML = `<p class="eventos-vacio">Sin bonos con BPV calculable.</p>`;
    return;
  }
  const max = Math.max(...datos.categorias.map((c) => c.bpv_usd), 1e-9);
  cont.innerHTML = datos.categorias.map((c) => {
    const color = _colorCategoria(c.categoria);
    const ancho = Math.max((c.bpv_usd / max) * 100, 2);
    const pct = datos.bpv_total_usd ? (c.bpv_usd / datos.bpv_total_usd * 100).toFixed(1) : "0.0";
    const carry = c.carry_pct == null ? "" : `<span class="rank-bar-sub">carry ${c.carry_pct.toFixed(2)}%</span>`;
    return `
      <div class="rank-bar-fila" title="${c.categoria_label}: ${fmtUSD(-c.bpv_usd)} por pb (${pct}%), carry ${c.carry_pct ?? "—"}%">
        <span class="rank-bar-nombre"><span class="swatch" style="background:${color}"></span>${c.categoria_label}</span>
        <span class="rank-bar-pista"><span class="rank-bar-relleno" style="width:${ancho}%;background:${color}"></span></span>
        <span class="rank-bar-valor"><strong>${pct}%</strong> · ${fmtUSD(-c.bpv_usd)}${carry}</span>
      </div>`;
  }).join("");
}

// ============================================================
// Panel: Tesis de inversion (bitacora, 2026-09-12). Empezo acotada a
// Bonos (punto 3 de la ronda de Bonos); mismo dia el usuario pidio
// extenderla a la cartera general (Dashboard) y a Acciones -- las 3
// comparten exactamente la misma logica de render/CRUD, factorizada
// aca en `crearBitacoraTesis(config)`. Sin filtros server-side -- se
// trae TODO de cada endpoint y se filtra/renderiza 100% en JS (mismo
// criterio que "Bonos en tenencia"/"Mercado de bonos"), volumen chico
// esperado (bitacora personal).
//
// `config.conTicker` distingue las 2 variantes:
//   true  (bonos/acciones): soporta macro (ticker null) y micro
//         (ticker puntual) -- radio tipo, wrap de ticker, filtro por
//         instrumento, datalist de tickers.
//   false (cartera): sin ticker/tipo/filtro de instrumento -- toda
//         fila es "la cartera completa".
// ============================================================

// Epica 12 Parte 1 (2026-09-12): umbrales de monitoreo estructurados
// sobre una tesis. Epica 21 (2026-09-29): las opciones del <select> ya no
// son una lista a mano -- salen de GET /api/indicadores-catalogo (misma
// fuente que valida el backend), agrupadas por seccion. Se llenan en
// cargar() antes de las bitacoras.
let INDICADOR_UMBRAL_LABEL = {};
let INDICADOR_UMBRAL_OPCIONES = "";

function cargarCatalogoUmbrales(catalogo) {
  INDICADOR_UMBRAL_LABEL = Object.fromEntries(catalogo.map((c) => [c.id, `${c.nombre} (${c.unidad})`]));
  const secciones = new Map();
  for (const c of catalogo) {
    if (!secciones.has(c.seccion)) secciones.set(c.seccion, []);
    secciones.get(c.seccion).push(`<option value="${c.id}">${escapeAttr(INDICADOR_UMBRAL_LABEL[c.id])}</option>`);
  }
  INDICADOR_UMBRAL_OPCIONES = [...secciones]
    .map(([seccion, opciones]) => `<optgroup label="${escapeAttr(seccion)}">${opciones.join("")}</optgroup>`).join("");
}

function crearBitacoraTesis(config) {
  const { prefix, apiBase, conTicker, tesisTabla, conContenedorCobro } = config;
  const id = (sufijo) => `${prefix}-${sufijo}`;
  const el = (sufijo) => document.getElementById(id(sufijo));

  const estado = {
    data: [],
    filtroEstado: "todas", // "todas" | "vigente" | "cerrada"
    filtroInstrumento: "", // "" = todos, "__macro__" = solo macro, o un ticker puntual
    editandoId: null, // null = el form esta en modo "crear"
    umbralesPorTesis: {}, // tesisId -> array de umbrales YA EVALUADOS (con cruzado/valor_actual), cache que evita refetch en cada render
    umbralesAbiertos: new Set(), // tesisId con la seccion de umbrales desplegada
    umbralesCruzadosCount: {}, // tesisId -> cantidad de umbrales cruzados (para el badge de la tarjeta, Epica 12 Parte 2)
  };

  async function cargarUmbrales(tesisId) {
    // Trae la EVALUACION (no el CRUD crudo) -- mismo endpoint que arma
    // el badge, asi la fila individual ya viene con valor_actual/
    // cruzado sin pedirlo aparte (Epica 12 Parte 2, 2026-09-13).
    const resp = await fetch(`/api/umbrales-cruzados?tesis_tabla=${tesisTabla}&tesis_id=${tesisId}`);
    const datos = await resp.json();
    estado.umbralesPorTesis[tesisId] = datos;
    // Sincroniza el conteo del badge de ESTA tesis con lo recien
    // traido -- sin esto, agregar/borrar un umbral no actualizaria el
    // badge de la tarjeta hasta el proximo `cargar()` completo.
    estado.umbralesCruzadosCount[tesisId] = datos.filter((u) => u.cruzado).length;
  }

  async function cargarUmbralesCruzadosResumen() {
    // 1 sola llamada para TODAS las tesis de esta bitacora -- arma el
    // conteo que decide si una tarjeta muestra el badge de alerta, sin
    // esperar a que cada tarjeta se expanda individualmente.
    const resp = await fetch(`/api/umbrales-cruzados?tesis_tabla=${tesisTabla}`);
    const todos = await resp.json();
    estado.umbralesCruzadosCount = {};
    for (const u of todos) {
      if (u.cruzado) estado.umbralesCruzadosCount[u.tesis_id] = (estado.umbralesCruzadosCount[u.tesis_id] || 0) + 1;
    }
  }

  function renderUmbralesBlock(t) {
    const abierto = estado.umbralesAbiertos.has(t.id);
    const datos = estado.umbralesPorTesis[t.id];
    let listaHtml;
    if (!datos) {
      listaHtml = `<p class="eventos-vacio">Cargando...</p>`;
    } else if (!datos.length) {
      listaHtml = `<p class="eventos-vacio">Sin umbrales cargados todavía.</p>`;
    } else {
      listaHtml = datos.map((u) => {
        const actualStr = u.valor_actual != null
          ? ` — actual: ${u.valor_actual.toLocaleString("es-AR", { maximumFractionDigits: 2 })}${u.fecha_valor_actual ? ` (${fechaCorta(u.fecha_valor_actual)})` : ""}`
          : " — sin dato actual todavía";
        return `
        <div class="umbral-fila${u.cruzado ? " umbral-cruzado" : ""}">
          <span class="umbral-texto">
            ${u.cruzado ? "⚠️ " : ""}<strong>${INDICADOR_UMBRAL_LABEL[u.indicador] || u.indicador}</strong>
            ${u.operador === "mayor_que" ? "&gt;" : "&lt;"} ${u.valor_umbral}${actualStr}
            → ${escapeAttr(u.accion)}
          </span>
          <button type="button" class="umbral-btn-eliminar" data-umbral-id="${u.umbral_id}" title="Eliminar umbral">×</button>
        </div>`;
      }).join("");
    }
    return `
      <button type="button" class="tesis-btn-umbrales" data-id="${t.id}">
        ${abierto ? "▾" : "▸"} Umbrales de monitoreo${datos ? ` (${datos.length})` : ""}
      </button>
      <div class="tesis-umbrales" data-tesis-id="${t.id}" ${abierto ? "" : "hidden"}>
        <div class="tesis-umbrales-lista">${listaHtml}</div>
        <form class="tesis-umbral-form" data-tesis-id="${t.id}">
          <select class="umbral-form-indicador">${INDICADOR_UMBRAL_OPCIONES}</select>
          <select class="umbral-form-operador">
            <option value="menor_que">&lt; menor que</option>
            <option value="mayor_que">&gt; mayor que</option>
          </select>
          <input type="number" step="any" class="umbral-form-valor" placeholder="Valor" required />
          <input type="text" class="umbral-form-accion" placeholder="Acción (ej. evaluar toma de ganancia)" required />
          <button type="submit" class="simulador-btn-sec">+ Agregar umbral</button>
        </form>
      </div>`;
  }

  function renderChips() {
    const conteo = { vigente: 0, cerrada: 0 };
    for (const t of estado.data) conteo[t.estado] = (conteo[t.estado] || 0) + 1;
    const chips = [
      { clave: "todas", label: "Todas", total: estado.data.length },
      { clave: "vigente", label: "Vigentes", total: conteo.vigente },
      { clave: "cerrada", label: "Cerradas", total: conteo.cerrada },
    ];
    el("chips").innerHTML = chips.map((c) => `
      <button type="button" class="mercado-chip${c.clave === estado.filtroEstado ? " activo" : ""}"
              data-estado="${c.clave}">${c.label} <span class="mercado-chip-n">${c.total}</span></button>
    `).join("");
  }

  function renderFiltroInstrumento() {
    if (!conTicker) return;
    const sel = el("filtro-instrumento");
    const valorPrevio = sel.value;
    const tickers = [...new Set(estado.data.filter((t) => t.tipo === "micro").map((t) => t.ticker))].sort();
    const hayMacro = estado.data.some((t) => t.tipo === "macro");
    const opciones = [`<option value="">Todos los instrumentos</option>`];
    if (hayMacro) opciones.push(`<option value="__macro__">Macro (cartera)</option>`);
    opciones.push(...tickers.map((tk) => `<option value="${tk}">${tk}</option>`));
    sel.innerHTML = opciones.join("");
    sel.value = valorPrevio; // se conserva si la opcion sigue existiendo, si no vuelve a ""
  }

  function renderContenedorCobroDatalist() {
    if (!conContenedorCobro) return;
    const valores = [...new Set(estado.data.map((t) => t.contenedor_cobro).filter(Boolean))].sort();
    el("contenedores-datalist").innerHTML = valores.map((v) => `<option value="${v}"></option>`).join("");
  }

  function filasFiltradas() {
    return estado.data.filter((t) => {
      if (estado.filtroEstado !== "todas" && t.estado !== estado.filtroEstado) return false;
      if (conTicker) {
        if (estado.filtroInstrumento === "__macro__" && t.tipo !== "macro") return false;
        if (estado.filtroInstrumento && estado.filtroInstrumento !== "__macro__" && t.ticker !== estado.filtroInstrumento) return false;
      }
      return true;
    });
  }

  function renderLista() {
    const filas = filasFiltradas();
    el("subtitulo").textContent = `${filas.length} de ${estado.data.length} tesis.`;
    const cont = el("lista");
    if (!filas.length) {
      cont.innerHTML = `<p class="eventos-vacio">Sin tesis para este filtro.</p>`;
      return;
    }
    cont.innerHTML = filas.map((t) => {
      const badge = !conTicker ? "" : t.tipo === "macro"
        ? `<span class="tesis-card-badge tesis-badge-macro">MACRO</span>`
        : `<span class="tesis-card-badge">${t.ticker}</span>`;
      const convBadge = t.conviccion
        ? `<span class="tesis-card-conviccion conviccion-${t.conviccion}">Convicción ${t.conviccion}</span>` : "";
      const estadoBadge = `<span class="tesis-card-estado${t.estado === "cerrada" ? " estado-cerrada" : ""}">${t.estado}</span>`;
      const nCruzados = estado.umbralesCruzadosCount[t.id] || 0;
      const alertaBadge = nCruzados > 0
        ? `<span class="tesis-card-alerta" title="Desplegá 'Umbrales de monitoreo' para el detalle">⚠️ ${nCruzados} umbral${nCruzados > 1 ? "es" : ""} cruzado${nCruzados > 1 ? "s" : ""}</span>`
        : "";
      const riesgos = t.riesgos ? `<p class="tesis-card-riesgos"><strong>Riesgos:</strong> ${escapeAttr(t.riesgos)}</p>` : "";
      const contenedorCobro = conContenedorCobro && t.contenedor_cobro
        ? `<p class="tesis-card-riesgos"><strong>Cobro en:</strong> ${escapeAttr(t.contenedor_cobro)}</p>` : "";
      const reflexion = t.reflexion_final
        ? `<div class="tesis-card-reflexion"><strong>Reflexión${t.fecha_reflexion ? ` (${fechaCorta(t.fecha_reflexion)})` : ""}:</strong> ${escapeAttr(t.reflexion_final)}</div>`
        : "";
      const cerrarBtn = t.estado === "vigente"
        ? `<button type="button" class="tesis-btn-cerrar" data-id="${t.id}">Cerrar / reflexión</button>` : "";
      return `
        <div class="tesis-card${t.estado === "cerrada" ? " tesis-cerrada" : ""}" data-id="${t.id}">
          <div class="tesis-card-header">
            ${badge}
            <span class="tesis-card-fecha">${fechaCorta(t.fecha)}</span>
            ${convBadge}
            ${estadoBadge}
            ${alertaBadge}
            <div class="tesis-card-acciones">
              <button type="button" class="tesis-btn-editar" data-id="${t.id}">Editar</button>
              ${cerrarBtn}
              <button type="button" class="tesis-btn-eliminar" data-id="${t.id}">Eliminar</button>
            </div>
          </div>
          <p class="tesis-card-texto">${escapeAttr(t.tesis)}</p>
          ${riesgos}
          ${contenedorCobro}
          ${reflexion}
          <div class="tesis-cerrar-form" id="${id("cerrar-form-" + t.id)}" hidden>
            <label>Reflexión final
              <textarea rows="2" id="${id("cerrar-texto-" + t.id)}" placeholder="¿Qué pasó? ¿Acertamos el razonamiento?"></textarea>
            </label>
            <div class="tesis-form-acciones">
              <button type="button" class="tesis-btn-confirmar-cierre" data-id="${t.id}">Confirmar cierre</button>
              <button type="button" class="simulador-btn-sec tesis-btn-cancelar-cierre" data-id="${t.id}">Cancelar</button>
            </div>
          </div>
          ${renderUmbralesBlock(t)}
        </div>`;
    }).join("");
  }

  function resetForm() {
    el("form-id").value = "";
    if (conTicker) {
      document.querySelector(`input[name="${id("tipo")}"][value="macro"]`).checked = true;
      el("form-ticker-wrap").hidden = true;
      el("form-ticker").value = "";
    }
    if (conContenedorCobro) el("form-contenedor-cobro").value = "";
    el("form-fecha").value = new Date().toISOString().slice(0, 10);
    el("form-conviccion").value = "";
    el("form-tesis").value = "";
    el("form-riesgos").value = "";
    el("form-guardar-btn").textContent = conTicker ? "Guardar tesis" : "Guardar";
    el("form-cancelar-btn").hidden = true;
    el("form-error").hidden = true;
    estado.editandoId = null;
  }

  function cargarParaEditar(tesisId) {
    const t = estado.data.find((x) => x.id === tesisId);
    if (!t) return;
    estado.editandoId = tesisId;
    el("form-id").value = tesisId;
    if (conTicker) {
      document.querySelector(`input[name="${id("tipo")}"][value="${t.tipo}"]`).checked = true;
      el("form-ticker-wrap").hidden = t.tipo !== "micro";
      el("form-ticker").value = t.ticker || "";
    }
    if (conContenedorCobro) el("form-contenedor-cobro").value = t.contenedor_cobro || "";
    el("form-fecha").value = t.fecha;
    el("form-conviccion").value = t.conviccion || "";
    el("form-tesis").value = t.tesis;
    el("form-riesgos").value = t.riesgos || "";
    el("form-guardar-btn").textContent = "Guardar cambios";
    el("form-cancelar-btn").hidden = false;
    el("form-error").hidden = true;
    document.getElementById(`panel-${prefix}`).scrollIntoView({ behavior: "smooth", block: "start" });
  }

  async function cargar() {
    const resp = await fetch(apiBase);
    estado.data = await resp.json();
    await cargarUmbralesCruzadosResumen();
    renderChips();
    renderFiltroInstrumento();
    renderContenedorCobroDatalist();
    renderLista();
    if (!el("form-fecha").value) resetForm();
  }

  function wire() {
    if (conTicker) {
      document.querySelectorAll(`input[name="${id("tipo")}"]`).forEach((r) => {
        r.addEventListener("change", (e) => {
          el("form-ticker-wrap").hidden = e.target.value !== "micro";
        });
      });
    }

    el("form-cancelar-btn").addEventListener("click", resetForm);

    el("form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const err = el("form-error");
      err.hidden = true;

      const tipo = conTicker ? document.querySelector(`input[name="${id("tipo")}"]:checked`).value : null;
      const ticker = conTicker && tipo === "micro" ? el("form-ticker").value.trim() : null;
      const tesisTexto = el("form-tesis").value.trim();
      if (conTicker && tipo === "micro" && !ticker) {
        err.textContent = "Falta el ticker para una tesis micro.";
        err.hidden = false;
        return;
      }
      if (!tesisTexto) {
        err.textContent = "La tesis no puede estar vacía.";
        err.hidden = false;
        return;
      }

      const body = {
        fecha: el("form-fecha").value,
        tesis: tesisTexto,
        riesgos: el("form-riesgos").value.trim() || null,
        conviccion: el("form-conviccion").value || null,
      };
      if (conTicker) body.ticker = ticker;
      if (conContenedorCobro) body.contenedor_cobro = el("form-contenedor-cobro").value.trim() || null;

      const idEditando = el("form-id").value;
      try {
        const resp = await fetch(idEditando ? `${apiBase}/${idEditando}` : apiBase, {
          method: idEditando ? "PUT" : "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!resp.ok) {
          const j = await resp.json().catch(() => ({}));
          throw new Error(j.detail || `HTTP ${resp.status}`);
        }
        resetForm();
        await cargar();
      } catch (ex) {
        err.textContent = `No se pudo guardar: ${ex.message}`;
        err.hidden = false;
      }
    });

    el("chips").addEventListener("click", (e) => {
      const chip = e.target.closest(".mercado-chip");
      if (!chip) return;
      estado.filtroEstado = chip.dataset.estado;
      renderChips();
      renderLista();
    });

    if (conTicker) {
      el("filtro-instrumento").addEventListener("change", (e) => {
        estado.filtroInstrumento = e.target.value;
        renderLista();
      });
    }

    el("lista").addEventListener("click", async (e) => {
      const btnEditar = e.target.closest(".tesis-btn-editar");
      if (btnEditar) {
        cargarParaEditar(Number(btnEditar.dataset.id));
        return;
      }

      const btnEliminar = e.target.closest(".tesis-btn-eliminar");
      if (btnEliminar) {
        if (!confirm("¿Eliminar esta tesis? No se puede deshacer.")) return;
        const tesisId = Number(btnEliminar.dataset.id);
        await fetch(`${apiBase}/${tesisId}`, { method: "DELETE" });
        if (estado.editandoId === tesisId) resetForm();
        await cargar();
        return;
      }

      const btnCerrar = e.target.closest(".tesis-btn-cerrar");
      if (btnCerrar) {
        document.getElementById(id("cerrar-form-" + btnCerrar.dataset.id)).hidden = false;
        return;
      }

      const btnCancelarCierre = e.target.closest(".tesis-btn-cancelar-cierre");
      if (btnCancelarCierre) {
        document.getElementById(id("cerrar-form-" + btnCancelarCierre.dataset.id)).hidden = true;
        return;
      }

      const btnConfirmarCierre = e.target.closest(".tesis-btn-confirmar-cierre");
      if (btnConfirmarCierre) {
        const tesisId = btnConfirmarCierre.dataset.id;
        const texto = document.getElementById(id("cerrar-texto-" + tesisId)).value.trim();
        await fetch(`${apiBase}/${tesisId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            estado: "cerrada",
            reflexion_final: texto || null,
            fecha_reflexion: new Date().toISOString().slice(0, 10),
          }),
        });
        await cargar();
        return;
      }

      // Epica 12 Parte 1: toggle de la seccion de umbrales -- carga
      // bajo demanda (cache en estado.umbralesPorTesis) la primera vez
      // que se abre, nunca de a todas las tesis juntas en cada render.
      const btnUmbrales = e.target.closest(".tesis-btn-umbrales");
      if (btnUmbrales) {
        const tesisId = Number(btnUmbrales.dataset.id);
        if (estado.umbralesAbiertos.has(tesisId)) {
          estado.umbralesAbiertos.delete(tesisId);
          renderLista();
        } else {
          estado.umbralesAbiertos.add(tesisId);
          if (!estado.umbralesPorTesis[tesisId]) {
            renderLista(); // pinta "Cargando..." mientras llega el fetch
            await cargarUmbrales(tesisId);
          }
          renderLista();
        }
        return;
      }

      const btnUmbralEliminar = e.target.closest(".umbral-btn-eliminar");
      if (btnUmbralEliminar) {
        const tesisId = Number(e.target.closest(".tesis-umbrales").dataset.tesisId);
        await fetch(`/api/tesis-umbrales/${btnUmbralEliminar.dataset.umbralId}`, { method: "DELETE" });
        await cargarUmbrales(tesisId);
        renderLista();
        return;
      }
    });

    // Delegado en "submit" (no "click") porque el formulario de umbral
    // se recrea en cada renderLista() -- un listener puesto directo en
    // el <form> se perdería en el siguiente render.
    el("lista").addEventListener("submit", async (e) => {
      const form = e.target.closest(".tesis-umbral-form");
      if (!form) return;
      e.preventDefault();
      const tesisId = Number(form.dataset.tesisId);
      const indicador = form.querySelector(".umbral-form-indicador").value;
      const operador = form.querySelector(".umbral-form-operador").value;
      const valor = parseFloat(form.querySelector(".umbral-form-valor").value);
      const accion = form.querySelector(".umbral-form-accion").value.trim();
      if (!accion || Number.isNaN(valor)) return;
      const resp = await fetch("/api/tesis-umbrales", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tesis_tabla: tesisTabla, tesis_id: tesisId, indicador, operador, valor, accion }),
      });
      if (!resp.ok) {
        const j = await resp.json().catch(() => ({}));
        alert(`No se pudo agregar el umbral: ${j.detail || resp.status}`);
        return;
      }
      estado.umbralesAbiertos.add(tesisId);
      await cargarUmbrales(tesisId);
      renderLista();
    });
  }

  return { cargar, wire };
}

const bitacoraTesisBonos = crearBitacoraTesis({ prefix: "tesis-bonos", apiBase: "/api/tesis-bonos", conTicker: true, tesisTabla: "bonos" });
const bitacoraTesisAcciones = crearBitacoraTesis({ prefix: "tesis-acciones", apiBase: "/api/tesis-acciones", conTicker: true, tesisTabla: "acciones" });
const bitacoraTesisCartera = crearBitacoraTesis({ prefix: "tesis-cartera", apiBase: "/api/tesis-cartera", conTicker: false, tesisTabla: "cartera" });
// Epica 15 Parte B (2026-09-15): Real Estate. Fase 3 (2026-09-15) le dio
// pestana propia ("Real Estate" en #menu-superior, vista-real-estate en
// el HTML) -- antes vivia al final del Dashboard.
const bitacoraTesisInmuebles = crearBitacoraTesis({ prefix: "tesis-inmuebles", apiBase: "/api/tesis-inmuebles", conTicker: true, tesisTabla: "inmuebles", conContenedorCobro: true });

// ============================================================
// Real Estate -- propiedades / valuaciones / pasivos (2026-09-18,
// pedido del usuario: "valuacion/deuda/TIR"). `total_inmuebles`/
// `total_pasivos` YA vienen en /api/resumen y la rama "inmuebles" ya
// vive en /api/composicion-jerarquica (mismo fetch de siempre, ver
// cargar() mas abajo) -- este bloque solo agrega la tabla de
// propiedades + los 3 formularios de carga, sin duplicar esos 2 fetches.
// ============================================================

let propiedadesData = [];
let pasivosData = [];

function renderPropiedadesTabla() {
  const body = document.getElementById("propiedades-body");

  // Encabezado "VPN (X%)" y nota con la fuente de la tasa -- ambos
  // sacados del dato que ya trae /api/propiedades (2026-09-19, fix: la
  // tasa pasa de 10% generico a 6% sourced, y tiene que verse de un
  // vistazo de que dato sale sin ir a buscar el ticket). Toma la
  // primera fila activo_fisico con el campo poblado -- hoy todas
  // comparten la misma tasa default, no hay por-fila distinto.
  const conTasa = propiedadesData.find((p) => p.tasa_descuento_pct != null);
  document.getElementById("propiedades-th-vpn").textContent =
    conTasa ? `VPN (${conTasa.tasa_descuento_pct}%)` : "VPN";
  document.getElementById("propiedades-tasa-fuente-nota").textContent =
    conTasa && conTasa.tasa_descuento_fuente ? `Tasa de descuento: ${conTasa.tasa_descuento_fuente}` : "";

  if (!propiedadesData.length) {
    body.innerHTML = `<tr><td colspan="10">Sin propiedades cargadas.</td></tr>`;
    return;
  }
  body.innerHTML = propiedadesData.map((p) => {
    const v = p.valuacion;
    const pas = p.pasivo;
    // tir_pct/vpn_usd ya vienen calculados en /api/propiedades (2026-09-19,
    // pedido del usuario) -- sin fetch aparte por fila.
    const tirClase = p.tir_pct == null ? "" : (p.tir_pct >= 0 ? "ganancia-positivo" : "ganancia-negativo");
    return `
      <tr>
        <td>${p.alias}</td>
        <td>${p.tipo === "activo_fisico" ? "Activo físico" : "Fondo RE"}</td>
        <td style="text-align:right">${p.pct_propiedad}%</td>
        <td style="text-align:right">${v ? fmtUSD(v.valor_usd) : "—"}</td>
        <td>${v ? v.fecha : "—"}</td>
        <td style="text-align:right">${v ? fmtUSD(v.valor_usd_propio) : "—"}</td>
        <td>${pas ? pas.alias : "—"}</td>
        <td style="text-align:right">${pas ? fmtUSD(pas.saldo_usd) : "—"}</td>
        <td style="text-align:right" class="${tirClase}">${p.tir_pct == null ? "—" : fmtNum(p.tir_pct) + "%"}</td>
        <td style="text-align:right">${p.vpn_usd == null ? "—" : fmtUSD(p.vpn_usd)}</td>
      </tr>`;
  }).join("");
}

function poblarSelectsRealEstate() {
  const opciones = propiedadesData.map((p) => `<option value="${p.id}">${p.alias}</option>`).join("");
  document.getElementById("re-alquiler-propiedad").innerHTML = opciones;
  document.getElementById("re-valuacion-propiedad").innerHTML = opciones;

  const selPasivo = document.getElementById("re-pago-pasivo-select");
  selPasivo.innerHTML = pasivosData.length
    ? pasivosData.map((p) => `<option value="${p.id}">${p.alias} (saldo ${fmtUSD(p.saldo_usd)})</option>`).join("")
    : `<option value="">Sin pasivos cargados</option>`;
}

async function cargarRealEstate() {
  const [propResp, pasResp] = await Promise.all([fetch("/api/propiedades"), fetch("/api/pasivos")]);
  propiedadesData = await propResp.json();
  pasivosData = await pasResp.json();
  renderPropiedadesTabla();
  poblarSelectsRealEstate();
  document.getElementById("re-formularios-subtitulo").textContent =
    `${propiedadesData.length} propiedad(es), ${pasivosData.length} pasivo(s) cargado(s).`;
}

// Solo patrimonio_total + composicion-jerarquica cambian con una
// valuacion/pago nuevo (alquiler no toca ninguno de los 2 -- ver
// docstring de /api/resumen) -- se reusan las MISMAS 2 llamadas que ya
// hace cargar() al arrancar, sin reinvocar cargar() entero (evitaria
// volver a wirear listeners de otros paneles, ej. wireRotacionBonos()).
async function refrescarResumenYComposicion() {
  const [resumenResp, composicionResp] = await Promise.all([
    fetch("/api/resumen"), fetch("/api/composicion-jerarquica"),
  ]);
  const resumen = await resumenResp.json();
  composicion = await composicionResp.json();
  renderPatrimonioTotal(resumen);
  renderNotaComposicion(resumen);
  renderComposicion();
}

function wireRealEstateForms() {
  const hoy = new Date().toISOString().slice(0, 10);
  document.getElementById("re-valuacion-fecha").value = hoy;
  document.getElementById("re-alquiler-fecha").value = hoy;
  document.getElementById("re-pago-pasivo-fecha").value = hoy;

  async function enviar(formId, errorId, url, body, tocaPatrimonio) {
    const errorEl = document.getElementById(errorId);
    errorEl.hidden = true;
    const resp = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!resp.ok) {
      const j = await resp.json().catch(() => ({}));
      errorEl.textContent = j.detail || `Error ${resp.status}`;
      errorEl.hidden = false;
      return false;
    }
    document.getElementById(formId).reset();
    document.getElementById("re-valuacion-fecha").value = hoy;
    document.getElementById("re-alquiler-fecha").value = hoy;
    document.getElementById("re-pago-pasivo-fecha").value = hoy;
    await cargarRealEstate();
    if (tocaPatrimonio) await refrescarResumenYComposicion();
    return true;
  }

  document.getElementById("re-alquiler-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await enviar("re-alquiler-form", "re-alquiler-error", "/api/alquileres", {
      propiedad_id: parseInt(document.getElementById("re-alquiler-propiedad").value, 10),
      periodo: document.getElementById("re-alquiler-periodo").value,
      monto: parseFloat(document.getElementById("re-alquiler-monto").value),
      moneda: document.getElementById("re-alquiler-moneda").value,
      fecha_cobro: document.getElementById("re-alquiler-fecha").value,
    }, false);
  });

  document.getElementById("re-valuacion-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await enviar("re-valuacion-form", "re-valuacion-error", "/api/valuaciones", {
      propiedad_id: parseInt(document.getElementById("re-valuacion-propiedad").value, 10),
      fecha: document.getElementById("re-valuacion-fecha").value,
      valor_usd: parseFloat(document.getElementById("re-valuacion-valor").value),
      fuente: document.getElementById("re-valuacion-fuente").value.trim() || null,
    }, true);
  });

  document.getElementById("re-pago-pasivo-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    await enviar("re-pago-pasivo-form", "re-pago-pasivo-error", "/api/pagos-pasivo", {
      pasivo_id: parseInt(document.getElementById("re-pago-pasivo-select").value, 10),
      fecha: document.getElementById("re-pago-pasivo-fecha").value,
      monto_usd: parseFloat(document.getElementById("re-pago-pasivo-monto").value),
    }, true);
  });
}

// ============================================================
// Menu superior: Dashboard / Analisis (Epica 11, 2026-09-07)
// ============================================================

function cambiarVista(vista) {
  document.getElementById("vista-dashboard").classList.toggle("hidden", vista !== "dashboard");
  document.getElementById("vista-indicadores-macro").classList.toggle("hidden", vista !== "indicadores-macro");
  document.getElementById("vista-analisis-bonos").classList.toggle("hidden", vista !== "analisis-bonos");
  document.getElementById("vista-analisis-acciones").classList.toggle("hidden", vista !== "analisis-acciones");
  document.getElementById("vista-real-estate").classList.toggle("hidden", vista !== "real-estate");
  document.getElementById("metricas-header").classList.toggle("hidden", vista !== "dashboard");
  document.querySelectorAll("#menu-superior button").forEach((b) => {
    b.classList.toggle("activo", b.dataset.vista === vista);
  });
}

document.getElementById("menu-superior").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-vista]");
  if (!btn) return;
  cambiarVista(btn.dataset.vista);
  btn.scrollIntoView({ block: "nearest", inline: "center", behavior: "smooth" });
});

// Celular (2026-10-30): el menu se desplaza de costado; .hay-mas muestra la
// flecha mientras quede alguna solapa a la derecha sin ver (ver style.css).
function marcarMenuConMas() {
  const m = document.getElementById("menu-superior");
  m.classList.toggle("hay-mas", m.scrollLeft + m.clientWidth < m.scrollWidth - 4);
}
document.getElementById("menu-superior").addEventListener("scroll", marcarMenuConMas, { passive: true });
window.addEventListener("resize", marcarMenuConMas);
marcarMenuConMas();

// ============================================================
// Paneles colapsables (2026-09-16): las tablas grandes
// de Bonos ocupaban mucho espacio y era tedioso scrollear para ver un
// dato puntual. Generico via [data-panel] en .panel-toggle -- sumar el
// boton al header de un panel alcanza, sin tocar este JS. Estado
// persistido en localStorage por panel (una vez cerrado uno que no se
// consulta seguido, se queda cerrado entre sesiones); no depende de
// datos del fetch, se wirea apenas carga el HTML.
// ============================================================

function aplicarColapsoPanel(panelEl, colapsado) {
  panelEl.classList.toggle("panel-colapsado", colapsado);
  const btn = panelEl.querySelector(".panel-toggle");
  if (btn) btn.textContent = colapsado ? "▸" : "▾";
}

function initPanelesColapsables() {
  document.querySelectorAll(".panel-toggle").forEach((btn) => {
    const panelEl = document.getElementById(btn.dataset.panel);
    if (!panelEl) return;
    const key = `panel-colapsado:${btn.dataset.panel}`;
    let colapsado = false;
    try { colapsado = localStorage.getItem(key) === "1"; } catch { }
    aplicarColapsoPanel(panelEl, colapsado);
    btn.addEventListener("click", () => {
      const nuevo = !panelEl.classList.contains("panel-colapsado");
      aplicarColapsoPanel(panelEl, nuevo);
      try { localStorage.setItem(key, nuevo ? "1" : "0"); } catch { }
    });
  });
}
initPanelesColapsables();

// ============================================================
// Carga inicial
// ============================================================

// Titulo, abreviatura y color de cada broker: de la configuracion del
// usuario (/api/config-ui), no del codigo. Si falla, quedan los nombres
// completos y el color por hash.
// Modo demo (2026-10-29): la API responde 403 a todo lo que no es GET. La
// clase en <body> oculta los controles que escriben (ver style.css) y cada
// panel con carga lleva el aviso arriba, en vez del formulario.
const PANELES_CON_CARGA = [
  "panel-tesis-cartera", "panel-maquina", "panel-macro", "panel-tesis-bonos",
  "panel-tenencias-rv", "ficha-rv", "panel-screener-rv", "panel-rebalanceo",
  "panel-simulador-rv", "panel-cedear-moneda", "panel-cedear-caro-barato",
  "panel-tesis-acciones", "panel-re-formularios", "panel-tesis-inmuebles",
];

function aplicarModoDemo(cfg) {
  const fecha = cfg.fecha_datos ? fechaLarga(cfg.fecha_datos) : "la fecha de corte";
  document.getElementById("autor").textContent =
    `Demo · inversor ficticio · datos de mercado reales al ${fecha} · solo lectura`;
  document.body.classList.add("modo-demo");
  for (const id of PANELES_CON_CARGA) {
    const panel = document.getElementById(id);
    if (!panel || panel.querySelector(".aviso-demo")) continue;
    const aviso = document.createElement("p");
    aviso.className = "aviso-demo";
    aviso.textContent = "Demo: solo lectura. La carga y la edición están deshabilitadas.";
    const cabecera = panel.querySelector(":scope > .panel-header");
    if (cabecera) cabecera.after(aviso);
    else panel.prepend(aviso);
  }
}

// "2026-10-28" -> "28/10/2026"
function fechaLarga(iso) {
  const [anio, mes, dia] = iso.split("-");
  return `${dia}/${mes}/${anio}`;
}

async function cargarConfigUi() {
  try {
    const r = await fetch("/api/config-ui");
    if (!r.ok) return;
    const cfg = await r.json();
    if (cfg.nombre) document.title = `PWOS — ${cfg.nombre}`;
    if (cfg.modo === "demo") {
      aplicarModoDemo(cfg);
    } else {
      document.getElementById("autor").textContent = cfg.titulo || cfg.nombre || "";
    }
    for (const [nombre, b] of Object.entries(cfg.brokers || {})) {
      if (b.abreviatura) LABELS[nombre] = b.abreviatura;
      if (b.color) CLAVE_COLOR[nombre] = b.color;
    }
  } catch { }
}

async function cargar() {
  await cargarConfigUi();
  const respuestas = await Promise.all([
    fetch("/api/resumen"),
    fetch("/api/composicion-jerarquica"),
    fetch("/api/flujo-cobros"),
    fetch("/api/ganancia-por-activo"),
    fetch("/api/rendimiento-serie"),
    fetch("/api/renta-dividendos-cobrados"),
    fetch("/api/efectivo-por-contenedor"),
    fetch("/api/rotacion-bonos"),
    fetch("/api/calendario-flujo"),
    fetch("/api/rebalanceo-rv"),
    fetch("/api/bonos-mercado"),
    fetch("/api/bonos-cartera-por-categoria"),
    fetch("/api/bonos-sensibilidad-por-categoria"),
    fetch("/api/breakeven-inflacion"),
    fetch("/api/devaluacion-implicita"),
    fetch("/api/bonos-ratio-universo"),
  ]);

  const [resumenResp, composicionResp, flujoCobrosResp, gananciaResp, rendimientoResp, rentaDividendosResp, efectivoContResp, rotacionBonosResp, calendarioFlujoResp, rebalanceoRvResp, bonosMercadoResp, bonosCatResp, bonosSensResp, breakevenInflResp, devaluacionResp, ratioBonosUniversoResp] = respuestas;

  if (respuestas.some((r) => !r.ok)) {
    throw new Error("La API respondió con error");
  }

  const resumen = await resumenResp.json();
  composicion = await composicionResp.json();
  flujoCobros = await flujoCobrosResp.json();
  gananciaCompleta = await gananciaResp.json();
  const rendimientoSerie = await rendimientoResp.json();
  rentaDividendosCobrados = await rentaDividendosResp.json();
  const efectivoPorContenedor = await efectivoContResp.json();
  const rotacionBonos = await rotacionBonosResp.json();
  const calendarioFlujo = await calendarioFlujoResp.json();
  rebalanceoData = await rebalanceoRvResp.json();

  document.getElementById("subtitulo").textContent = subtituloConBenchmarks(resumen);
  renderEstadoCotizaciones(resumen);
  renderPatrimonioTotal(resumen);
  renderNotaComposicion(resumen);
  renderGananciaTotal(resumen, rendimientoSerie);
  renderRiesgoTotal(resumen);
  renderSensibilidadTasa(resumen);
  renderRentaFijaTotal(resumen);
  renderGananciaPanel();

  renderComposicion();

  document.getElementById("flujo-toggle").textContent =
    flujoCobros.meses.length > MESES_VISIBLES_DEFAULT ? "Ver todo" : "";
  renderFlujoLeyenda();
  renderFlujoChart();
  renderEventosTabla();
  if (flujoCobros.aviso) {
    document.getElementById("flujo-nota").textContent = `⚠ ${flujoCobros.aviso}`;
  }

  renderRendimientoPanel(rendimientoSerie);
  renderEfectivoPorContenedor(efectivoPorContenedor);
  rotacionBonosData = rotacionBonos;
  renderRotacionChips();
  renderRotacionBonos();
  wireRotacionBonos();
  renderBreakevenTasa();
  wireBreakevenTasa();
  renderCalendarioFlujo(calendarioFlujo);
  wireCalendarioFlujo();
  renderRebalanceo();
  // Aislado a proposito: un error en la solapa Acciones no tiene que
  // tirar abajo el resto de la carga (Bonos, Real Estate, bitacoras).
  try {
    await cargarAccionesRv();
  } catch (err) {
    document.getElementById("tenencias-body").innerHTML =
      `<tr><td colspan="9" class="eventos-vacio">No se pudieron cargar las tenencias: ${escapeAttr(err.message)}</td></tr>`;
  }

  mercadoBonosData = await bonosMercadoResp.json();
  document.getElementById("simulador-tickers-datalist").innerHTML =
    mercadoBonosData.especies.map((b) => `<option value="${b.ticker}"></option>`).join("");
  document.getElementById("tesis-bonos-tickers-datalist").innerHTML =
    mercadoBonosData.especies.map((b) => `<option value="${b.ticker}"></option>`).join("");
  renderMercadoBonosChips();
  renderMercadoBonos();
  wireSimuladorBonos();
  wireComparadorBonos();

  // Indicadores Macro (Epica 21) -- aislado a proposito, mismo criterio
  // que la solapa Acciones: un error aca no tira abajo el resto.
  try {
    await recargarIndicadoresMacro();
  } catch (err) {
    document.getElementById("macro-grid").innerHTML =
      `<p class="eventos-vacio">No se pudieron cargar los indicadores macro: ${escapeAttr(err.message)}</p>`;
  }
  wireMacroPanel();
  wireMaquina();
  renderBonosCategorias(await bonosCatResp.json());
  renderBonosSensibilidadCategoria(await bonosSensResp.json());

  breakevenInflacionData = await breakevenInflResp.json();
  devaluacionImplicitaData = await devaluacionResp.json();
  renderBreakevenInflacion();
  renderDevaluacionImplicita();
  wireBreakevenDevaluacion();

  ratioBonosUniverso = await ratioBonosUniversoResp.json();
  renderRatioBonosSelects();
  wireRatioBonos();
  await renderRatioBonosChart();

  // Datalist de tickers para la bitacora de Acciones: no hay un feed de
  // "mercado completo" de RV (a diferencia de bonos/Rava) -- se usan las
  // posiciones vigentes de RV ya traidas por /api/rebalanceo-rv. Es solo
  // una sugerencia de autocompletado, el campo sigue siendo texto libre
  // (se puede tipear cualquier ticker, tenga posicion o no).
  document.getElementById("tesis-acciones-tickers-datalist").innerHTML =
    rebalanceoData.posiciones.map((p) => `<option value="${p.ticker}"></option>`).join("");

  // Opciones del selector de umbrales de las tesis (Epica 21): antes de
  // renderizar las bitacoras. Si falla, el selector queda vacio y el
  // backend igual valida contra el catalogo.
  try {
    const catResp = await fetch("/api/indicadores-catalogo");
    if (catResp.ok) cargarCatalogoUmbrales(await catResp.json());
  } catch (err) {
    console.error("indicadores-catalogo:", err);
  }

  await bitacoraTesisCartera.cargar();
  bitacoraTesisCartera.wire();
  await bitacoraTesisBonos.cargar();
  bitacoraTesisBonos.wire();
  await bitacoraTesisAcciones.cargar();
  bitacoraTesisAcciones.wire();
  await bitacoraTesisInmuebles.cargar();
  bitacoraTesisInmuebles.wire();

  await cargarRealEstate();
  wireRealEstateForms();
}

cargar().catch((err) => {
  document.getElementById("cascada").innerHTML = `
    <div class="error-banner">
      No se pudo cargar el patrimonio (¿está corriendo el backend? ¿pwos.db accesible?).<br>
      Detalle: ${err.message}
    </div>
  `;
  document.getElementById("subtitulo").textContent = "Error";
});
