// ---------- Cascading Country -> City selects ----------
let AIRPORTS = [];            // [[iata, city, country, name], ...]
let AIRPORTS_BY_COUNTRY = {}; // { "Spain": [[iata, city, country, name], ...] }

async function loadAirports() {
  const r = await fetch("/api/airports", { cache: "force-cache" });
  AIRPORTS = await r.json();
  AIRPORTS_BY_COUNTRY = {};
  for (const a of AIRPORTS) {
    const c = a[2];
    if (!AIRPORTS_BY_COUNTRY[c]) AIRPORTS_BY_COUNTRY[c] = [];
    AIRPORTS_BY_COUNTRY[c].push(a);
  }
  // Sort airports within each country by city, then airport name.
  for (const c in AIRPORTS_BY_COUNTRY) {
    AIRPORTS_BY_COUNTRY[c].sort((x, y) =>
      x[1].localeCompare(y[1]) || x[3].localeCompare(y[3])
    );
  }
  populateCountrySelects();
}

function populateCountrySelects() {
  const countries = Object.keys(AIRPORTS_BY_COUNTRY).sort((a, b) => a.localeCompare(b));
  document.querySelectorAll(".ap-country").forEach(sel => {
    const target = sel.dataset.target;
    sel.innerHTML =
      `<option value="">— Elige país —</option>` +
      countries.map(c => `<option value="${c}">${c} (${AIRPORTS_BY_COUNTRY[c].length})</option>`).join("");
    sel.addEventListener("change", () => onCountryChange(target, sel.value));
  });
}

function onCountryChange(target, country) {
  const citySel = document.querySelector(`select.ap-city[data-target="${target}"]`);
  const hidden = document.querySelector(`input[type="hidden"][name="${target}"]`);
  hidden.value = "";
  if (!country) {
    citySel.innerHTML = `<option value="">Primero elige país</option>`;
    citySel.disabled = true;
    return;
  }
  const list = AIRPORTS_BY_COUNTRY[country] || [];
  citySel.innerHTML =
    `<option value="">— Elige ciudad / aeropuerto —</option>` +
    list.map(a => `<option value="${a[0]}">${a[1]} — ${a[3]} (${a[0]})</option>`).join("");
  citySel.disabled = false;
  citySel.onchange = () => { hidden.value = citySel.value; };
  // Trigger initial hidden sync in case the value was pre-set programmatically.
  if (citySel.value) hidden.value = citySel.value;
}

loadAirports();

// ---------- Maletas facturadas sigue a la cantidad de adultos ----------
// El campo es "por pax"; lo arrancamos = adultos y lo mantenemos sincronizado
// hasta que el usuario lo edite a mano (entonces respeta su valor).
(function () {
  const adultsInput = document.querySelector('input[name="adults"]');
  const bagsInput = document.querySelector('input[name="checked_bags"]');
  if (!adultsInput || !bagsInput) return;
  let bagsTouched = false;
  bagsInput.addEventListener("input", () => { bagsTouched = true; });
  function syncBags() {
    if (bagsTouched) return;
    const a = Math.max(1, parseInt(adultsInput.value || "1", 10) || 1);
    bagsInput.value = Math.min(a, parseInt(bagsInput.max || "4", 10));
  }
  adultsInput.addEventListener("input", syncBags);
  syncBags();  // estado inicial
})();

// ---------- Price tracker ----------
const tracksListEl = document.getElementById("tracks-list");
const tracksCountEl = document.getElementById("tracks-count");
const tracksHelpEl = document.getElementById("tracks-help");

async function loadTracks() {
  const r = await fetch("/api/tracks");
  const data = await r.json();
  renderTracks(data);
}

function fmtDate(iso) {
  if (!iso) return "—";
  return iso.replace("T", " ").replace("+00:00", " UTC").slice(0, 19);
}

function renderTracks(data) {
  const tracks = data.tracks || [];
  tracksCountEl.textContent = tracks.length ? `· ${tracks.length}` : "";

  if (!data.smtp_configured) {
    tracksHelpEl.innerHTML = `<strong style="color: var(--accent2)">⚠ Email no configurado</strong> —
      las alertas se guardan y se ven aquí, pero <em>no se envía email</em> hasta que
      configures SMTP_HOST / SMTP_USER / SMTP_PASS en el entorno (ver README).`;
  } else {
    tracksHelpEl.innerHTML = `Las alertas revisan precios cada ${Math.round(data.poll_interval_s / 3600)}h
      y envían email cuando bajan al objetivo o tocan un nuevo mínimo histórico.`;
  }

  if (!tracks.length) {
    tracksListEl.innerHTML = `<p class="muted">Sin alertas. Haz una búsqueda y pulsa "Trackear esta ruta".</p>`;
    return;
  }

  tracksListEl.innerHTML = tracks.map(t => {
    const hist = t.history || [];
    const sparkline = hist.length
      ? hist.slice().reverse().map(h => h.price_usd != null ? `$${h.price_usd.toFixed(0)}` : "—").join(" → ")
      : "(sin checks aún)";
    return `<div class="track ${t.active ? "" : "inactive"}">
      <div class="track-main">
        <strong>${t.origin} → ${t.destination}</strong> ·
        ${t.depart_date}${t.return_date ? ` ↔ ${t.return_date}` : " (ida)"} ·
        ${t.adults} adulto${t.children ? `+${t.children} niño` : ""}${t.infants ? `+${t.infants} bebé` : ""}
      </div>
      <div class="track-stats">
        <span>Último: <strong>${t.last_price_usd != null ? fmtMoney(t.last_price_usd) : "—"}</strong></span>
        <span>Mínimo histórico: <strong>${t.best_seen_usd != null ? fmtMoney(t.best_seen_usd) : "—"}</strong></span>
        <span>Objetivo: ${t.threshold_usd != null ? fmtMoney(t.threshold_usd) : "(sin objetivo)"}</span>
        <span>Email: ${t.email || "(ninguno)"}</span>
        <span>Revisado: ${fmtDate(t.last_check_at)}</span>
      </div>
      <div class="track-hist">${sparkline}</div>
      <button type="button" class="track-del" data-id="${t.id}">Eliminar</button>
    </div>`;
  }).join("");

  tracksListEl.querySelectorAll(".track-del").forEach(b => {
    b.addEventListener("click", async () => {
      if (!confirm("¿Eliminar esta alerta?")) return;
      await fetch(`/api/tracks/${b.dataset.id}`, { method: "DELETE" });
      loadTracks();
    });
  });
}

document.getElementById("track-btn").addEventListener("click", async () => {
  if (!LAST_SEARCH) return;
  const { payload } = LAST_SEARCH;
  const email = prompt("Email para recibir alertas (vacío = solo verlas en la app):", "");
  const thresholdRaw = prompt(
    "Precio objetivo USD — te avisamos cuando baje a ese o por debajo (vacío = avisar solo en nuevos mínimos):",
    ""
  );
  const body = {
    origin: payload.origin,
    destination: payload.destination,
    depart_date: payload.depart_date,
    return_date: payload.return_date,
    adults: payload.adults,
    children: payload.children,
    infants: payload.infants,
    max_stops: payload.max_stops,
    email: email || null,
    threshold_usd: thresholdRaw ? parseFloat(thresholdRaw) : null,
  };
  const r = await fetch("/api/tracks", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.ok) {
    alert("✓ Alerta creada. Mírala en 'Mis alertas' al final de la página.");
    loadTracks();
    document.getElementById("tracks-section").scrollIntoView({ behavior: "smooth" });
  } else {
    const err = await r.json().catch(() => ({ detail: r.statusText }));
    alert("Error: " + (err.detail || r.statusText));
  }
});

loadTracks();
setInterval(loadTracks, 60000);  // refresh once a minute

// ---------- Composite / hunter renderers ----------
function renderComposite(items) {
  const block = document.getElementById("composite-block");
  const el = document.getElementById("composite");
  const cnt = document.getElementById("composite-count");
  if (!items.length) { block.hidden = true; return; }
  block.hidden = false;
  cnt.textContent = `· ${items.length}`;
  el.innerHTML = items.map(c => `
    <div class="composite-row">
      <strong>${c.origin} (${c.origin_city || ""}) → ${c.destination} (${c.destination_city || ""})</strong>
      <div class="muted">
        ${c.distance_to_origin_km ? `+${c.distance_to_origin_km} km del origen pedido · ` : ""}
        ${c.distance_to_destination_km ? `+${c.distance_to_destination_km} km del destino pedido · ` : ""}
        ${c.savings_usd ? `<span style="color:var(--good)">ahorras ${fmtMoney(c.savings_usd)}</span>` : ""}
      </div>
      <div class="composite-price">${fmtMoney(c.price_usd)}</div>
    </div>`).join("");
}

function renderHiddenCity(items, primaryOneWay) {
  const block = document.getElementById("hidden-city-block");
  const el = document.getElementById("hidden-city");
  const cnt = document.getElementById("hc-count");
  if (!items.length) { block.hidden = true; return; }
  block.hidden = false;
  cnt.textContent = `· ${items.length}`;
  const header = primaryOneWay
    ? `<p class="muted">Comparando contra ida directa: <strong>${fmtMoney(primaryOneWay)}</strong></p>`
    : "";
  el.innerHTML = header + items.map(c => `
    <div class="hc-row">
      <strong>Comprar ticket a ${c.ticketed_city} (${c.ticketed_to})</strong>
      <div class="muted">${c.ticketed_name}, ${c.ticketed_country}</div>
      <div class="muted">
        Precio ida: <strong>${fmtMoney(c.price_one_way_usd)}</strong>
        ${c.savings_usd ? `<span style="color:var(--good)"> · ahorras ${fmtMoney(c.savings_usd)} vs ida directa</span>` : ""}
      </div>
      <div class="hc-warn">⚠ Verifica en la web de la aerolínea que este itinerario realmente escale en tu destino antes de comprar. No factures maletas (irían a la ciudad final, no a tu destino real).</div>
    </div>`).join("");
}

// Fallback Google Flights URL when the server didn't supply a precise one
// (e.g. older server build). Uses the free-text query form — not as exact as
// the tfs token but always works and never yields a broken /undefined link.
function gfFallback(legCode, dateStr) {
  const m = /^([A-Z]{3})-([A-Z]{3})$/.exec(legCode || "");
  if (!m) return "https://www.google.com/travel/flights";
  const [, o, d] = m;
  let q = `Flights from ${o} to ${d}`;
  if (dateStr) q += ` on ${dateStr}`;
  return "https://www.google.com/travel/flights?q=" + encodeURIComponent(q);
}

function stLeg(code, link, dateStr) {
  const href = (link && link !== "undefined") ? link : gfFallback(code, dateStr);
  return `<a href="${href}" target="_blank" rel="noopener">${code} (${dateStr || "?"}) →</a>`;
}

function stOptionRow(o) {
  return `
    <div class="st-row">
      <div class="st-route">
        <strong>vía ${o.hub_city} (${o.hub})</strong>
        <span class="muted">2 tickets separados</span>
      </div>
      <div class="st-legs muted">
        ${o.leg1} ${fmtMoney(o.leg1_usd)} (${o.leg1_date}) +
        ${o.leg2} ${fmtMoney(o.leg2_usd)} (${o.leg2_date}, ${o.leg2_when})
      </div>
      <div class="st-links">
        ${stLeg(o.leg1, o.leg1_link, o.leg1_date)}
        ${stLeg(o.leg2, o.leg2_link, o.leg2_date)}
      </div>
      <div class="st-total">${fmtMoney(o.total_usd)}</div>
    </div>`;
}

// st is the dict { is_round_trip, outbound[], return[], best_combined_usd }.
// Accepts a legacy array too (older server) and treats it as outbound-only.
function selfTransferRows(st) {
  if (Array.isArray(st)) st = { is_round_trip: false, outbound: st, return: [] };
  if (!st || (!st.outbound || !st.outbound.length) && (!st["return"] || !st["return"].length)) {
    return `<p class="muted">No se encontraron opciones de self-transfer (posible bloqueo temporal de Google o ruta sin hub viable).</p>`;
  }
  const out = st.outbound || [];
  const ret = st["return"] || [];
  let html = "";
  if (st.best_combined_usd != null) {
    html += `<div class="st-combined">Mejor combinación ida+vuelta self-transfer: <strong>${fmtMoney(st.best_combined_usd)}</strong> total (${ret.length ? "ida + vuelta, 4 tickets" : "solo ida, 2 tickets"})</div>`;
  }
  if (out.length) {
    html += `<div class="st-half-h">✈ IDA — ${out[0].leg1_date}</div>`;
    html += out.map(stOptionRow).join("");
  }
  if (st.is_round_trip) {
    if (ret.length) {
      html += `<div class="st-half-h" style="margin-top:10px">✈ VUELTA (${ret[0].leg1_date})</div>`;
      html += ret.map(stOptionRow).join("");
    } else {
      html += `<div class="st-warn">⚠ No se encontró self-transfer para la VUELTA en esta fecha (Google pudo bloquear, o no hay hub viable). Revisa la vuelta aparte o usa otra fecha.</div>`;
    }
  }
  return html;
}

function renderSelfTransfer(st) {
  const block = document.getElementById("selftransfer-block");
  const el = document.getElementById("selftransfer");
  const cnt = document.getElementById("st-count");
  if (!block) return;
  const hasAny = st && ((st.outbound && st.outbound.length) ||
                        (st["return"] && st["return"].length));
  if (!hasAny) { block.hidden = true; return; }
  block.hidden = false;
  const n = (st.outbound ? st.outbound.length : 0) +
            (st["return"] ? st["return"].length : 0);
  cnt.textContent = `· ${n}`;
  el.innerHTML = selfTransferRows(st);
}

function offGdsRows(items) {
  return items.map(c => `
    <a class="offgds-row" href="${c.link}" target="_blank" rel="noopener">
      <div class="og-name">${c.name} <span class="og-country">${c.country}</span></div>
      <div class="og-note">${c.note}</div>
      <div class="og-cta">Abrir su web →</div>
    </a>`).join("");
}

function renderOffGds(items) {
  const block = document.getElementById("offgds-block");
  const el = document.getElementById("offgds");
  const cnt = document.getElementById("offgds-count");
  if (!block) return;
  if (!items.length) { block.hidden = true; return; }
  block.hidden = false;
  cnt.textContent = `· ${items.length}`;
  el.innerHTML = offGdsRows(items);
}

function renderWeekday(items) {
  const block = document.getElementById("weekday-block");
  const chart = document.getElementById("weekday-chart");
  const note = document.getElementById("weekday-note");
  if (!items.length) { block.hidden = true; return; }
  block.hidden = false;
  const valid = items.filter(i => i.total_usd != null);
  if (!valid.length) {
    chart.innerHTML = `<p class="muted">No se pudieron obtener precios para ningún día.</p>`;
    return;
  }
  const minP = Math.min(...valid.map(i => i.total_usd));
  const maxP = Math.max(...valid.map(i => i.total_usd));
  const minDay = valid.find(i => i.total_usd === minP);
  const maxDay = valid.find(i => i.total_usd === maxP);
  const savings = maxP - minP;
  const pct = maxP > 0 ? (savings / maxP * 100) : 0;
  note.innerHTML = (savings > 5)
    ? `Volar en <strong style="color:var(--good)">${minDay.weekday}</strong> sale <strong>${fmtMoney(savings)}</strong> (${pct.toFixed(0)}%) más barato que en <strong style="color:var(--bad)">${maxDay.weekday}</strong>. Una muestra por día — directional, no estadístico.`
    : `Diferencia pequeña entre días (${fmtMoney(savings)}). Para esta ruta el día de la semana importa poco.`;
  chart.innerHTML = items.map(d => {
    if (d.total_usd == null) {
      return `<div class="wd-bar unavailable">
        <div class="wd-name">${d.weekday}</div>
        <div class="wd-fill" style="width:0%"></div>
        <div class="wd-price">n/d</div>
      </div>`;
    }
    const width = ((d.total_usd - minP * 0.9) / (maxP - minP * 0.9 + 1) * 100).toFixed(0);
    const cls = d.total_usd === minP ? "cheapest" : d.total_usd === maxP ? "expensive" : "";
    return `<div class="wd-bar ${cls}">
      <div class="wd-name">${d.weekday} <span class="wd-date">${d.depart_date.slice(5)}</span></div>
      <div class="wd-fill" style="width:${Math.max(width, 5)}%"></div>
      <div class="wd-price">${fmtMoney(d.total_usd)}<small> ${d.airline || ""}</small></div>
    </div>`;
  }).join("");
}

function renderAmadeus(items) {
  const block = document.getElementById("amadeus-block");
  const el = document.getElementById("amadeus");
  const cnt = document.getElementById("ama-count");
  if (!items.length) { block.hidden = true; return; }
  block.hidden = false;
  cnt.textContent = `· ${items.length}`;
  el.innerHTML = items.slice(0, 10).map(a => `
    <div class="ama-row">
      <strong>${a.airline}</strong>
      <span>${a.depart_at.slice(0, 16).replace("T", " ")} → ${a.arrive_at.slice(0, 16).replace("T", " ")}</span>
      <span class="muted">${a.duration} · ${a.stops === 0 ? "directo" : a.stops + " escala(s)"}</span>
      <span class="ama-price">${fmtMoney(a.price_usd)}</span>
    </div>`).join("");
}

// ---------- Deals feed ----------
async function loadDeals() {
  const el = document.getElementById("deals-list");
  const cnt = document.getElementById("deals-count");
  try {
    const r = await fetch("/api/deals");
    const data = await r.json();
    cnt.textContent = data.count ? `· ${data.count}` : "";
    if (!data.deals.length) {
      el.innerHTML = `<p class="muted">No hay deals activos en este momento.</p>`;
      return;
    }
    el.innerHTML = data.deals.slice(0, 30).map(d => `
      <a class="deal-row" href="${d.link}" target="_blank" rel="noopener">
        <div class="deal-src">${d.source}</div>
        <div class="deal-title">${d.title}</div>
        <div class="deal-meta">
          ${d.origin_iata ? `${d.origin_iata} → ${d.destination_iata || "?"}` : "(no parseado)"} ·
          ${d.price_usd ? `<strong>$${d.price_usd.toFixed(0)}</strong>` : "precio en post"} ·
          ${d.published ? d.published.slice(0, 16) : ""}
        </div>
      </a>`).join("");
  } catch (e) {
    el.innerHTML = `<p class="muted">Error cargando deals: ${e.message}</p>`;
  }
}

loadDeals();
setInterval(loadDeals, 30 * 60 * 1000);  // refresh every 30 min

// ---------- Search form ----------
const form = document.getElementById("search-form");
const statusEl = document.getElementById("status");
const resultsEl = document.getElementById("results");
const summaryEl = document.getElementById("summary");
const notesEl = document.getElementById("notes");
const flightsEl = document.getElementById("flights");
const countEl = document.getElementById("count");
const arbitrageEl = document.getElementById("arbitrage");
const flexEl = document.getElementById("flex");
const goBtn = document.getElementById("go");

// Default depart date = today + 30
const dep = document.querySelector('input[name="depart_date"]');
const ret = document.querySelector('input[name="return_date"]');
const today = new Date();
const defaultDep = new Date();
defaultDep.setDate(defaultDep.getDate() + 30);
dep.value = defaultDep.toISOString().slice(0, 10);
dep.min = today.toISOString().slice(0, 10);
ret.min = dep.value;

// When depart changes: bump return's min and clear return if it's now invalid.
dep.addEventListener("change", () => {
  if (!dep.value) return;
  ret.min = dep.value;
  if (ret.value && ret.value < dep.value) {
    ret.value = "";
  }
});

function fmtMoney(n) {
  if (n == null) return "—";
  return "$" + n.toFixed(2);
}

function stopsLabel(s) {
  if (s === 0) return '<span class="stops-pill nonstop">directo</span>';
  return `<span class="stops-pill">${s} escala${s > 1 ? "s" : ""}</span>`;
}

function flightCard(f, ctx) {
  ctx = ctx || {};
  const isRT = !!ctx.return_date;
  const tripBadge = isRT
    ? `<span class="trip-pill rt">Ida y vuelta</span>`
    : `<span class="trip-pill ow">Solo ida</span>`;
  const priceLabel = isRT ? "total ida+vuelta" : "total";
  const links = [];
  if (f.booking_links.airline_direct)
    links.push(`<a class="primary" href="${f.booking_links.airline_direct}" target="_blank" rel="noopener">Comprar en aerolínea</a>`);
  links.push(`<a href="${f.booking_links.google}" target="_blank" rel="noopener">Google Flights</a>`);
  links.push(`<a href="${f.booking_links.kayak}" target="_blank" rel="noopener">Kayak</a>`);
  links.push(`<a href="${f.booking_links.skyscanner}" target="_blank" rel="noopener">Skyscanner</a>`);

  const extras = f.extras_breakdown && f.extras_breakdown.length
    ? `<div class="extras-detail">${f.extras_breakdown.map(b => `${b.label}: $${b.amount}`).join(" + ")}</div>`
    : `<div class="extras-detail">sin extras estimados</div>`;

  const legNote = isRT
    ? `<div class="muted leg-note">Vuelo de IDA mostrado · regreso ${ctx.return_date} se elige al comprar</div>`
    : "";

  return `<div class="flight ${f.is_best ? "best" : ""}">
    <div>
      <div class="airline">${f.airline}</div>
      ${tripBadge}
      ${stopsLabel(f.stops)}
      ${f.plus_days ? `<span class="stops-pill">+${f.plus_days} día${f.plus_days > 1 ? "s" : ""}</span>` : ""}
    </div>
    <div class="times">
      <div>${f.departure}</div>
      <div class="muted">→ ${f.arrival}</div>
      ${legNote}
    </div>
    <div class="duration">
      <div>${f.duration}</div>
      <div class="muted">${f.origin_airport} → ${f.destination_airport}</div>
    </div>
    <div class="price">
      <div class="total">${fmtMoney(f.total_usd)}</div>
      <div class="base">${priceLabel}: base ${fmtMoney(f.base_price_usd)} (${f.raw_price})</div>
      <div class="extras">+ extras ${fmtMoney(f.extras_usd)}</div>
      ${extras}
    </div>
    <div class="links">${links.join("")}</div>
  </div>`;
}

function posCard(p, cheapest) {
  const link = p.google_link || "#";
  if (p.best_price_usd == null) {
    return `<a class="pos-card error" href="${link}" target="_blank" rel="noopener" title="Abrir Google Flights en ${p.pos}">
      <div class="pos">${p.pos}</div>
      <div class="price">no disponible</div>
      <div class="raw">${(p.error || "").slice(0, 60)}</div>
      <div class="pos-cta">Abrir Google Flights →</div>
    </a>`;
  }
  const isCheapest = p.pos === cheapest;
  return `<a class="pos-card ${isCheapest ? "cheapest" : ""}" href="${link}" target="_blank" rel="noopener" title="Abrir Google Flights en mercado ${p.pos}">
    <div class="pos">${p.pos} ${isCheapest ? '<span class="badge">MÁS BARATO</span>' : ""}</div>
    <div class="price">${fmtMoney(p.best_price_usd)}</div>
    <div class="raw">precio local: ${p.best_price_raw} · ${p.n_flights} vuelos</div>
    <div class="pos-cta">Abrir Google Flights →</div>
  </a>`;
}

function flexCard(d, cheapestDep, requestedDep, origin, destination, payload) {
  let cls = "flex-day";
  if (d.depart_date === cheapestDep) cls += " cheapest";
  if (d.depart_date === requestedDep) cls += " requested";
  if (d.total_usd == null) cls += " unavailable";
  const pair = d.return_date
    ? `${d.depart_date}<br>↔ ${d.return_date}`
    : d.depart_date;
  // Build a Google Flights URL for THIS specific date pair.
  const params = new URLSearchParams({
    origin: origin, destination: destination,
    depart_date: d.depart_date,
    return_date: d.return_date || "",
    adults: payload.adults, children: payload.children, infants: payload.infants,
    seniors: payload.seniors || 0,
    checked_bags: payload.checked_bags || 0,
    pick_seat: payload.pick_seat ? "true" : "false",
  });
  return `<a class="${cls}" href="#" data-date="${d.depart_date}" data-rdate="${d.return_date || ''}" title="Re-buscar para esta fecha">
    <div class="date">${pair}</div>
    <div class="price">${d.total_usd != null ? fmtMoney(d.total_usd) : "n/d"}</div>
    <div class="airline">${d.airline || ""}</div>
  </a>`;
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const fd = new FormData(form);
  const origin = (fd.get("origin") || "").trim().toUpperCase();
  const destination = (fd.get("destination") || "").trim().toUpperCase();
  if (!origin || !destination) {
    statusEl.textContent = "Selecciona origen y destino de la lista (autocompletado).";
    statusEl.className = "error";
    return;
  }
  if (origin === destination) {
    statusEl.textContent = "Origen y destino no pueden ser el mismo aeropuerto.";
    statusEl.className = "error";
    return;
  }
  const depDate = fd.get("depart_date");
  const retDate = fd.get("return_date");
  if (!depDate) {
    statusEl.textContent = "Elige fecha de ida.";
    statusEl.className = "error";
    return;
  }
  if (retDate && retDate < depDate) {
    statusEl.textContent = "La fecha de vuelta no puede ser anterior a la de ida.";
    statusEl.className = "error";
    return;
  }
  const payload = {
    origin: origin,
    destination: destination,
    depart_date: fd.get("depart_date"),
    return_date: fd.get("return_date") || null,
    adults: parseInt(fd.get("adults") || "1", 10),
    children: parseInt(fd.get("children") || "0", 10),
    infants: parseInt(fd.get("infants") || "0", 10),
    seniors: parseInt(fd.get("seniors") || "0", 10),
    checked_bags: parseInt(fd.get("checked_bags") || "0", 10),
    pick_seat: fd.get("pick_seat") === "true",
    flex_days: parseInt(fd.get("flex_days") || "0", 10),
    probe_pos: fd.get("probe_pos") === "true",
    max_stops: fd.get("max_stops") ? parseInt(fd.get("max_stops"), 10) : null,
    composite: fd.get("composite") === "on",
    hidden_city: fd.get("hidden_city") === "on",
    use_amadeus: fd.get("use_amadeus") === "on",
    weekday_analysis: fd.get("weekday_analysis") === "on",
    self_transfer: fd.get("self_transfer") === "on",
  };

  goBtn.disabled = true;
  statusEl.innerHTML = "Buscando… (puede tardar 10-40s si hay arbitraje y/o fechas flex)";
  statusEl.className = "";

  const t0 = Date.now();
  try {
    const res = await fetch("/api/search", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      const detail = err.detail;
      // 404 with structured suggestions
      if (res.status === 404 && detail && typeof detail === "object") {
        renderNoFlights(detail, payload);
        return;
      }
      throw new Error(typeof detail === "string" ? detail : res.statusText);
    }
    const data = await res.json();
    render(data, payload);
    statusEl.textContent = `Listo en ${((Date.now() - t0) / 1000).toFixed(1)}s`;
  } catch (err) {
    statusEl.textContent = "Error: " + err.message;
    statusEl.className = "error";
  } finally {
    goBtn.disabled = false;
  }
});

function renderNoFlights(detail, payload) {
  const ao = detail.alternative_origins || [];
  const ad = detail.alternative_destinations || [];

  let html = `<div class="no-flights">
    <div class="nf-msg">${detail.message}</div>`;

  if (ao.length || ad.length) {
    html += `<div class="nf-sub">Aeropuertos cercanos donde podrías conseguir vuelos:</div>
             <div class="nf-grid">`;
    const renderBtn = (a, side) => `
      <button class="nf-btn" data-side="${side}" data-iata="${a.iata}" data-country="${a.country}">
        <span class="nf-city">${a.city} (${a.iata})</span>
        <span class="nf-meta">${a.distance_km} km · desde $${a.best_price_usd}</span>
      </button>`;
    if (ao.length) {
      html += `<div class="nf-col"><strong>Cambiar origen (${detail.origin})</strong>`;
      for (const a of ao) html += renderBtn(a, "origin");
      html += `</div>`;
    }
    if (ad.length) {
      html += `<div class="nf-col"><strong>Cambiar destino (${detail.destination})</strong>`;
      for (const a of ad) html += renderBtn(a, "destination");
      html += `</div>`;
    }
    html += `</div>`;
  }

  const st = detail.self_transfer;
  const stHasAny = st && ((st.outbound && st.outbound.length) ||
                          (st["return"] && st["return"].length) ||
                          (Array.isArray(st) && st.length));
  if (stHasAny) {
    html += `<div class="nf-sub" style="margin-top:14px">
      🔗 <strong>Self-transfer</strong> (tickets separados vía hub — barato pero
      sin protección de conexión, usa escala holgada):</div>
      <div class="nf-st">${selfTransferRows(st)}</div>`;
  }

  const og = detail.offgds_carriers || [];
  if (og.length) {
    html += `<div class="nf-sub" style="margin-top:14px">
      ✈ <strong>Aerolíneas fuera de Google Flights</strong> (revísalas directo —
      Conviasa, Laser, etc. venden solo en su web):</div>
      <div class="nf-st">${offGdsRows(og)}</div>`;
  }
  html += `</div>`;

  statusEl.innerHTML = html;
  statusEl.className = "warn";

  statusEl.querySelectorAll(".nf-btn").forEach(b => {
    b.addEventListener("click", () => {
      const side = b.dataset.side;       // "origin" or "destination"
      const iata = b.dataset.iata;
      const country = b.dataset.country;
      // Pick the country in the dropdown, then the airport.
      const countrySel = document.querySelector(`select.ap-country[data-target="${side}"]`);
      countrySel.value = country;
      countrySel.dispatchEvent(new Event("change"));
      const citySel = document.querySelector(`select.ap-city[data-target="${side}"]`);
      citySel.value = iata;
      citySel.dispatchEvent(new Event("change"));
      document.querySelector(`input[type="hidden"][name="${side}"]`).value = iata;
      // Resubmit
      form.dispatchEvent(new Event("submit", { cancelable: true }));
    });
  });
}

function renderSplitVsBlock(sb) {
  const el = document.getElementById("split-block");
  if (!el) return;
  if (!sb) { el.hidden = true; el.innerHTML = ""; return; }
  el.hidden = false;
  const blockWin = sb.cheaper === "block";
  const splitWin = sb.cheaper === "split";
  el.innerHTML = `
    <h2>🧮 ¿Comprar el bloque o por tramos?</h2>
    <p class="muted">Comparación sobre tarifa base (sin extras). Tramos separados = 2 reservas; cada aerolínea cobra su propio equipaje/asiento.</p>
    <div class="sb-grid">
      <div class="sb-card ${blockWin ? "sb-win" : ""}">
        <div class="sb-label">Billete único ida+vuelta ${blockWin ? '<span class="sb-badge">MEJOR</span>' : ""}</div>
        <div class="sb-price">${fmtMoney(sb.block_base_usd)}</div>
        <div class="sb-sub">${sb.block_airline} · 1 reserva, protección entre tramos</div>
      </div>
      <div class="sb-card ${splitWin ? "sb-win" : ""}">
        <div class="sb-label">Tramos por separado ${splitWin ? '<span class="sb-badge">MEJOR</span>' : ""}</div>
        <div class="sb-price">${fmtMoney(sb.split_total_usd)}</div>
        <div class="sb-sub">ida ${fmtMoney(sb.split_out_usd)} + vuelta ${fmtMoney(sb.split_return_usd)} · 2 reservas</div>
      </div>
    </div>
    <div class="sb-verdict">
      ${splitWin
        ? `Comprando por tramos ahorras <strong>${fmtMoney(sb.savings_usd)}</strong>. Vale la pena si viajas ligero (el equipaje se cobra 2 veces).`
        : `El bloque único es <strong>${fmtMoney(sb.savings_usd)}</strong> más barato y además te protege si una aerolínea cancela. Cómpralo junto.`}
    </div>`;
}

let LAST_SEARCH = null;  // remember last query payload for tracker

function render(data, payload) {
  LAST_SEARCH = { data, payload };
  resultsEl.hidden = false;

  const cheapest = data.flights[0];
  const rerunBadge = data.rerun_pos
    ? `<div class="rerun-badge">🎯 Búsqueda re-ejecutada en mercado <strong>${data.rerun_pos}</strong> · ahorras <strong>${fmtMoney(data.rerun_savings_usd)}</strong> vs US/USD. Las opciones listadas abajo son las del mercado más barato.</div>`
    : "";
  summaryEl.innerHTML = `
    <strong>${data.origin} → ${data.destination}</strong> ·
    ${data.depart_date}${data.return_date ? ` ↔ ${data.return_date}` : " (solo ida)"} ·
    ${data.adults + (payload.seniors || 0)} adulto(s)${data.children ? `, ${data.children} niño(s)` : ""}${data.infants ? `, ${data.infants} bebé(s)` : ""}.
    ${cheapest ? `<br>Más barato: <strong>${cheapest.airline}</strong> por <strong>${fmtMoney(cheapest.total_usd)}</strong> total.` : ""}
    ${rerunBadge}
  `;

  renderSplitVsBlock(data.split_vs_block);

  const recEl = document.getElementById("recommendations");
  recEl.innerHTML = (data.recommendations || []).map(r =>
    `<div class="rec rec-${r.severity}">
      <strong>${r.title}</strong>
      <span class="rec-detail">${r.detail}</span>
    </div>`
  ).join("");

  document.getElementById("track-row").hidden = false;

  notesEl.innerHTML = (data.notes || [])
    .map((n) => `<div class="note">${n}</div>`)
    .join("");

  countEl.textContent = `· ${data.flights.length}`;
  const tripCtx = { return_date: data.return_date };
  flightsEl.innerHTML = data.flights.slice(0, 50)
    .map(f => flightCard(f, tripCtx)).join("");

  const cheapestPos = data.cheapest_pos;
  arbitrageEl.innerHTML = data.arbitrage.length
    ? data.arbitrage.map((p) => posCard(p, cheapestPos)).join("")
    : `<p class="muted">Arbitraje desactivado.</p>`;

  // Composite / hunter sections
  renderComposite(data.composite || []);
  renderHiddenCity(data.hidden_city || [], data.primary_one_way_usd);
  renderAmadeus(data.amadeus || []);
  renderWeekday(data.weekday_analysis || []);
  renderSelfTransfer(data.self_transfer || []);
  renderOffGds(data.offgds_carriers || []);

  if (data.flex_calendar && data.flex_calendar.length) {
    const valid = data.flex_calendar.filter((d) => d.total_usd != null);
    const cheapestDep = valid.length
      ? valid.reduce((a, b) => (a.total_usd < b.total_usd ? a : b)).depart_date
      : null;
    flexEl.innerHTML = data.flex_calendar
      .map((d) => flexCard(d, cheapestDep, data.depart_date, data.origin, data.destination, payload))
      .join("");
    // Click on a flex cell -> rewrite dates and resubmit the form.
    flexEl.querySelectorAll(".flex-day").forEach(el => {
      el.addEventListener("click", (ev) => {
        ev.preventDefault();
        const newDep = el.dataset.date;
        const newRet = el.dataset.rdate;
        if (!newDep) return;
        document.querySelector('input[name="depart_date"]').value = newDep;
        document.querySelector('input[name="return_date"]').value = newRet || "";
        form.dispatchEvent(new Event("submit", { cancelable: true }));
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
    });
  } else {
    flexEl.innerHTML = `<p class="muted">Flex ± días desactivado.</p>`;
  }
}
