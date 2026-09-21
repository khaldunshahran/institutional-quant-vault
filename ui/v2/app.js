/* Quant Terminal v2 — Institutional Slate.
   Vanilla JS, no external dependencies. Paper-only: no live-trading UI exists here. */

"use strict";

/* ---------------- helpers ---------------- */

function usd(v) {
  if (v === null || v === undefined || isNaN(Number(v))) return "—";
  const n = Number(v);
  const body = Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (n < 0 ? "-" : "") + "$" + body;
}

function pct(v, digits) {
  if (v === null || v === undefined || isNaN(Number(v))) return "—";
  const d = digits !== undefined ? digits : 1;
  return Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }) + "%";
}

function num(v, digits) {
  if (v === null || v === undefined || isNaN(Number(v))) return "—";
  const d = digits !== undefined ? digits : 2;
  return Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

function signClass(v) {
  if (v === null || v === undefined || isNaN(Number(v))) return "";
  const n = Number(v);
  return n > 0 ? "pos" : (n < 0 ? "neg" : "");
}

function clockNow() {
  return new Date().toLocaleTimeString("en-GB", { hour12: false });
}

function parseUtc(s) {
  if (!s) return NaN;
  let t = Date.parse(String(s).replace(" UTC", "Z").replace(" ", "T"));
  if (isNaN(t)) t = Date.parse(String(s));
  return t;
}

function fmtAxisTime(ms) {
  const d = new Date(ms);
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return mm + "-" + dd + " " + hh + ":" + mi;
}

function esc(s) {
  return String(s === null || s === undefined ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/* ---------------- fetch + error banner ---------------- */

const banner = document.getElementById("error-banner");
const bannerText = document.getElementById("error-banner-text");
let bannerTimer = null;

function showBanner(msg) {
  bannerText.textContent = msg;
  banner.hidden = false;
  if (bannerTimer) clearTimeout(bannerTimer);
  bannerTimer = setTimeout(() => { banner.hidden = true; }, 30000);
}

function hideBanner() { banner.hidden = true; }

async function getJSON(url) {
  try {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error("HTTP " + r.status);
    return await r.json();
  } catch (e) {
    showBanner("Data refresh failed at " + clockNow() + " (" + url + ": " + e.message + ") — retrying automatically.");
    return null;
  }
}

async function postJSON(url, payload) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload || {}),
  });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return await r.json();
}

function setUpdated(id) {
  const el = document.getElementById(id);
  if (el) el.textContent = "updated " + clockNow();
}

/* ---------------- tabs ---------------- */

document.querySelectorAll(".tab").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".tabpage").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "market") refreshMarket(true);
  });
});

/* ---------------- shared state ---------------- */

const state = {
  status: null,      // /api/autotrade/status
  overview: null,    // /api/v2/overview
  integrity: null,   // /api/v2/integrity
  history: null,     // /api/autotrade/history
  marketSymbol: null,
  marketInterval: "15m",
  candles: null,
};

/* ---------------- header ---------------- */

async function refreshHeader() {
  const s = await getJSON("/api/autotrade/status");
  if (!s) return;
  state.status = s;

  const eq = (s.equity_usd !== undefined && s.equity_usd !== null)
    ? s.equity_usd
    : (state.overview ? state.overview.equity_usd : null);
  document.getElementById("hdr-equity").textContent = usd(eq);

  const dp = s.daily_pnl_usd, tg = s.daily_profit_target_usd;
  let dpTxt = usd(dp);
  if (dp !== null && dp !== undefined && tg !== null && tg !== undefined && Number(tg) !== 0) {
    dpTxt += " / " + usd(tg);
  }
  const dpEl = document.getElementById("hdr-daypnl");
  dpEl.textContent = dpTxt;
  dpEl.className = "tm-value " + signClass(dp);
  if (s.circuit_breaker_active) {
    dpEl.textContent += "  [circuit breaker]";
    dpEl.classList.add("warn-text");
  }

  const open = (s.open_positions || []).length;
  const cap = s.max_concurrent_positions;
  document.getElementById("hdr-positions").textContent =
    open + (cap ? " / " + cap : "");

  const eng = document.getElementById("hdr-engine");
  eng.textContent = s.running ? "Running" : "Stopped";
  eng.className = "tm-value " + (s.running ? "pos" : "");

  const dot = document.getElementById("hdr-health-dot");
  const htxt = document.getElementById("hdr-health-text");
  const secs = s.seconds_since_tick;
  dot.className = "health-dot";
  if (secs === null || secs === undefined) {
    htxt.textContent = "no data";
  } else {
    htxt.textContent = Math.round(secs) + "s ago";
    dot.className += secs < 60 ? " ok" : (secs < 300 ? " warn" : " bad");
  }

  const tgl = document.getElementById("btn-autopilot");
  tgl.textContent = s.running ? "Stop paper autopilot" : "Start paper autopilot";
}

document.getElementById("btn-autopilot").addEventListener("click", async () => {
  const currently = !!(state.status && state.status.running);
  const action = currently ? "stop" : "start";
  if (!confirm("Are you sure you want to " + action + " the PAPER autopilot?")) return;
  try {
    await postJSON("/api/autotrade/toggle", { enabled: !currently });
    await refreshHeader();
    await refreshPositions();
    await refreshSystem();
  } catch (e) {
    showBanner("Paper autopilot toggle failed at " + clockNow() + ": " + e.message);
  }
});

/* ---------------- canvas utils ---------------- */

function fitCanvas(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(1, Math.round(rect.width || canvas.clientWidth || 800));
  const defaultH = canvas.id === "equity-chart" ? 260 : 380;
  const h = Math.max(1, Math.round(rect.height || parseFloat(canvas.getAttribute("height")) || defaultH));
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { ctx, w, h };
}

const C = {
  bg: "#0B0D10", card: "#101317", border: "#1E242B",
  accent: "#4B91F5", profit: "#35C79A", loss: "#F06A6A",
  warn: "#F5B63D", text: "#E8EAED", secondary: "#9AA3AD",
};

/* ---------------- overview: equity chart ---------------- */

function drawEquityChart(curve, startBal) {
  const canvas = document.getElementById("equity-chart");
  const { ctx, w, h } = fitCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  const empty = document.getElementById("equity-empty");
  if (!curve || curve.length < 2) {
    canvas.style.display = "none";
    empty.hidden = false;
    return;
  }
  canvas.style.display = "block";
  empty.hidden = true;

  const padL = 64, padR = 10, padT = 12, padB = 24;
  const iw = w - padL - padR, ih = h - padT - padB;

  let peak = -Infinity, lo = Infinity;
  const peaks = [];
  for (const p of curve) {
    peak = Math.max(peak, p.equity);
    lo = Math.min(lo, p.equity);
    peaks.push(peak);
  }
  const hi = Math.max(peak, startBal || 0);
  lo = Math.min(lo, startBal || Infinity);
  const span = (hi - lo) || 1;
  lo -= span * 0.05; const hi2 = hi + span * 0.05;

  const X = i => padL + (i / (curve.length - 1)) * iw;
  const Y = v => padT + (1 - (v - lo) / (hi2 - lo)) * ih;

  // gridlines + y labels
  ctx.strokeStyle = C.border; ctx.fillStyle = C.secondary;
  ctx.font = "11px sans-serif"; ctx.lineWidth = 1;
  for (let g = 0; g <= 4; g++) {
    const v = lo + (hi2 - lo) * g / 4;
    const y = Y(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.fillText(usd(v), 4, y + 4);
  }

  // drawdown shading: fill between running peak and equity
  ctx.beginPath();
  ctx.moveTo(X(0), Y(peaks[0]));
  for (let i = 1; i < curve.length; i++) ctx.lineTo(X(i), Y(peaks[i]));
  for (let i = curve.length - 1; i >= 0; i--) ctx.lineTo(X(i), Y(curve[i].equity));
  ctx.closePath();
  ctx.fillStyle = "rgba(240,106,106,0.18)";
  ctx.fill();

  // peak line (dashed)
  ctx.beginPath();
  ctx.moveTo(X(0), Y(peaks[0]));
  for (let i = 1; i < curve.length; i++) ctx.lineTo(X(i), Y(peaks[i]));
  ctx.setLineDash([4, 4]); ctx.strokeStyle = C.secondary; ctx.stroke();
  ctx.setLineDash([]);

  // equity line
  ctx.beginPath();
  ctx.moveTo(X(0), Y(curve[0].equity));
  for (let i = 1; i < curve.length; i++) ctx.lineTo(X(i), Y(curve[i].equity));
  ctx.strokeStyle = C.accent; ctx.lineWidth = 2; ctx.stroke();

  // last point dot
  const li = curve.length - 1;
  ctx.beginPath(); ctx.arc(X(li), Y(curve[li].equity), 3.5, 0, Math.PI * 2);
  ctx.fillStyle = C.accent; ctx.fill();

  // x labels
  ctx.fillStyle = C.secondary;
  const ticks = [0, Math.floor(curve.length / 3), Math.floor(2 * curve.length / 3), li];
  ticks.forEach(i => {
    const t = parseUtc(curve[i].t);
    const label = isNaN(t) ? "" : fmtAxisTime(t);
    ctx.fillText(label, Math.min(X(i) - 30, w - 110), h - 8);
  });
}

function renderOverview(o) {
  const k = o.kpis || {};

  const np = document.getElementById("kpi-netpnl");
  np.textContent = usd(o.net_pnl_usd);
  np.className = "kpi-value " + signClass(o.net_pnl_usd);
  document.getElementById("kpi-netpnl-sub").textContent =
    "from " + (o.starting_balance_usd !== undefined ? usd(o.starting_balance_usd) : "—") + " start";

  const wr = document.getElementById("kpi-winrate");
  wr.textContent = pct(k.win_rate_pct);
  document.getElementById("kpi-winrate-sub").textContent =
    "avg win " + usd(k.avg_win_usd) + " · avg loss " + usd(k.avg_loss_usd);

  const pf = document.getElementById("kpi-pf");
  pf.textContent = (k.profit_factor === 99.0) ? "∞" : num(k.profit_factor);
  pf.className = "kpi-value " + (Number(k.profit_factor) >= 1 ? "pos" : "neg");
  document.getElementById("kpi-pf-sub").textContent =
    (k.wins || 0) + " wins · " + (k.losses || 0) + " losses";

  const ex = document.getElementById("kpi-expectancy");
  ex.textContent = usd(k.expectancy_usd);
  ex.className = "kpi-value " + signClass(k.expectancy_usd);

  const dd = document.getElementById("kpi-dd");
  dd.textContent = pct(k.max_drawdown_pct);
  dd.className = "kpi-value " + (Number(k.max_drawdown_pct) > 0 ? "neg" : "");
  document.getElementById("kpi-dd-sub").textContent = usd(k.max_drawdown_usd);

  document.getElementById("kpi-tpd").textContent = num(k.trades_per_day, 1);
  document.getElementById("kpi-total").textContent = num(k.total_trades, 0);
  document.getElementById("kpi-total-sub").textContent =
    (k.wins || 0) + " W / " + (k.losses || 0) + " L";

  const tb = document.querySelector("#symbol-table tbody");
  const rows = o.per_symbol || [];
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="4" class="empty-row">No trades yet.</td></tr>';
  } else {
    tb.innerHTML = rows.map(r =>
      "<tr><td>" + esc(r.symbol) + "</td>" +
      '<td class="num">' + num(r.trades, 0) + "</td>" +
      '<td class="num ' + signClass(r.net_pnl) + '">' + usd(r.net_pnl) + "</td>" +
      '<td class="num">' + pct(r.win_rate_pct) + "</td></tr>"
    ).join("");
  }

  drawEquityChart(o.equity_curve, o.starting_balance_usd);
}

async function refreshOverview() {
  const o = await getJSON("/api/v2/overview");
  if (!o) return;
  state.overview = o;
  renderOverview(o);
  setUpdated("updated-overview");
}

/* ---------------- positions ---------------- */

function sideSign(side) {
  return String(side || "").toUpperCase().indexOf("SHORT") >= 0 ? -1 : 1;
}

function timeInTrade(openTimeStr) {
  const t = parseUtc(openTimeStr);
  if (isNaN(t)) return esc(openTimeStr || "—");
  let mins = Math.max(0, Math.floor((Date.now() - t) / 60000));
  const h = Math.floor(mins / 60); mins -= h * 60;
  return h > 0 ? h + "h " + mins + "m" : mins + "m";
}

function renderPositions(status) {
  const tb = document.querySelector("#positions-table tbody");
  const pos = status.open_positions || [];
  const empty = document.getElementById("positions-empty");

  if (!pos.length) {
    tb.innerHTML = "";
    empty.hidden = false;
  } else {
    empty.hidden = true;
    tb.innerHTML = pos.map(p => {
      const entry = Number(p.entry_price);
      const upnl = Number(p.unrealized_pnl_usd);
      const margin = Number(p.margin_collateral_usd);
      const roe = (margin > 0 && !isNaN(upnl)) ? (upnl / margin * 100) : null;
      const fee = (Number(p.entry_fee_usd) || 0) + (Number(p.funding_paid_usd) || 0);

      let dist = "—";
      const sgn = sideSign(p.side);
      if (!isNaN(entry) && entry > 0) {
        const parts = [];
        if (p.stop_loss) {
          const d = sgn * (Number(p.stop_loss) - entry) / entry * 100;
          parts.push("SL " + (d > 0 ? "+" : "") + d.toFixed(1) + "%");
        }
        if (p.tp1) {
          const d = sgn * (Number(p.tp1) - entry) / entry * 100;
          parts.push("TP1 " + (d > 0 ? "+" : "") + d.toFixed(1) + "%");
        }
        if (parts.length) dist = parts.join(" · ");
      }

      const mark = Number(p.current_price !== undefined ? p.current_price : (p.mark_price !== undefined ? p.mark_price : null));
      const markTxt = (!isNaN(mark) && mark > 0) ? num(mark) : "—";
      const sym = esc(p.symbol);
      return "<tr><td><strong>" + sym + "</strong></td>" +
        "<td>" + esc(p.side) + "</td>" +
        '<td class="num">' + num(entry) + "</td>" +
        '<td class="num">' + markTxt + "</td>" +
        '<td class="num ' + signClass(upnl) + '">' + usd(upnl) + "</td>" +
        '<td class="num ' + signClass(roe) + '">' + (roe === null ? "—" : pct(roe)) + "</td>" +
        "<td>" + timeInTrade(p.open_time_str) + "</td>" +
        '<td class="num">' + esc(dist) + "</td>" +
        '<td class="num">' + usd(fee) + "</td>" +
        '<td><button class="btn btn-danger btn-close" data-symbol="' + sym + '">Close</button></td></tr>';
    }).join("");

    tb.querySelectorAll(".btn-close").forEach(btn => {
      btn.addEventListener("click", async () => {
        const sym = btn.dataset.symbol;
        if (!confirm("Close the open " + sym + " position? This is a paper trade.")) return;
        btn.disabled = true;
        try {
          const res = await postJSON("/api/autotrade/close", { symbol: sym });
          if (res && res.success === false) throw new Error(res.error || "close rejected");
          await refreshHeader();
          await refreshPositions();
          await refreshOverview();
        } catch (e) {
          showBanner("Close " + sym + " failed at " + clockNow() + ": " + e.message);
          btn.disabled = false;
        }
      });
    });
  }
}

async function refreshPositions() {
  const s = state.status || await getJSON("/api/autotrade/status");
  if (!s) return;
  state.status = s;
  renderPositions(s);
  setUpdated("updated-positions");

  // pending entries from integrity
  const integ = state.integrity || await getJSON("/api/v2/integrity");
  if (!integ) return;
  state.integrity = integ;
  const box = document.getElementById("pending-chips");
  const pend = integ.pending_entries || [];
  if (!pend.length) {
    box.innerHTML = '<span class="empty-row">No pending entries.</span>';
  } else {
    box.innerHTML = pend.map(p => {
      if (typeof p === "string") return '<span class="chip">' + esc(p) + "</span>";
      return '<span class="chip">' + esc(p.symbol || JSON.stringify(p)) + "</span>";
    }).join("");
  }
}

/* ---------------- market ---------------- */

const INTERVAL_MS = { "15m": 15 * 60000, "1h": 3600000, "4h": 4 * 3600000 };

function populateSymbols() {
  const sel = document.getElementById("market-symbol");
  let syms = (state.integrity && state.integrity.universe_symbols) || [];
  if (!syms.length && state.status && state.status.universe_symbols) syms = state.status.universe_symbols;
  if (!syms.length && state.overview && state.overview.per_symbol) {
    syms = state.overview.per_symbol.map(r => r.symbol);
  }
  syms = Array.from(new Set(syms.map(s => String(s).toUpperCase())));
  if (!syms.length) syms = ["BTCUSDT"];
  const prev = state.marketSymbol;
  sel.innerHTML = syms.map(s => "<option value=\"" + esc(s) + "\">" + esc(s) + "</option>").join("");
  state.marketSymbol = syms.indexOf(prev) >= 0 ? prev : syms[0];
  sel.value = state.marketSymbol;
}

document.getElementById("market-symbol").addEventListener("change", e => {
  state.marketSymbol = e.target.value;
  refreshMarket(true);
});
document.getElementById("market-interval").addEventListener("change", e => {
  state.marketInterval = e.target.value;
  refreshMarket(true);
});

function drawCandleChart(candles, markers, levels) {
  const canvas = document.getElementById("candle-chart");
  const { ctx, w, h } = fitCanvas(canvas);
  ctx.clearRect(0, 0, w, h);
  const empty = document.getElementById("market-empty");
  if (!candles || candles.length < 2) {
    canvas.style.display = "none";
    empty.hidden = false;
    return;
  }
  canvas.style.display = "block";
  empty.hidden = true;

  const padL = 10, padR = 64, padT = 12, padB = 30;
  const volH = Math.round((h - padT - padB) * 0.18);
  const priceH = h - padT - padB - volH - 8;
  const iw = w - padL - padR;

  let lo = Infinity, hi = -Infinity, vmax = 0;
  for (const c of candles) {
    lo = Math.min(lo, c.low); hi = Math.max(hi, c.high);
    vmax = Math.max(vmax, c.volume || 0);
  }
  if (levels) for (const L of levels) {
    if (L.price) { lo = Math.min(lo, L.price); hi = Math.max(hi, L.price); }
  }
  const span = (hi - lo) || 1;
  lo -= span * 0.06; hi += span * 0.06;

  const n = candles.length;
  const slot = iw / n;
  const bw = Math.max(1, Math.floor(slot * 0.7));
  const X = i => padL + i * slot + slot / 2;
  const Y = v => padT + (1 - (v - lo) / (hi - lo)) * priceH;
  const VY = v => padT + priceH + 8 + volH * (1 - (vmax ? v / vmax : 0));

  // gridlines + price labels
  ctx.font = "11px sans-serif"; ctx.lineWidth = 1;
  ctx.strokeStyle = C.border; ctx.fillStyle = C.secondary;
  for (let g = 0; g <= 4; g++) {
    const v = lo + (hi - lo) * g / 4, y = Y(v);
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
    ctx.fillText(num(v), w - padR + 6, y + 4);
  }

  // volume bars
  ctx.fillStyle = "#2a323b";
  for (let i = 0; i < n; i++) {
    const c = candles[i], x = X(i) - bw / 2;
    ctx.fillRect(x, VY(c.volume || 0), bw, padT + priceH + 8 + volH - VY(c.volume || 0));
  }

  // candles
  for (let i = 0; i < n; i++) {
    const c = candles[i], x = X(i);
    const up = c.close >= c.open;
    const col = up ? C.profit : C.loss;
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(x, Y(c.high)); ctx.lineTo(x, Y(c.low)); ctx.stroke();
    const yO = Y(c.open), yC = Y(c.close);
    ctx.fillRect(x - bw / 2, Math.min(yO, yC), bw, Math.max(1, Math.abs(yC - yO)));
  }

  // SL/TP/entry levels
  if (levels) {
    ctx.font = "10px sans-serif";
    for (const L of levels) {
      if (!L.price) continue;
      const y = Y(L.price);
      ctx.setLineDash(L.dash || []);
      ctx.strokeStyle = L.color; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(w - padR, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = L.color;
      ctx.fillText(L.label + " " + num(L.price), w - padR + 6, y + 3);
    }
  }

  // entry/exit markers (approximate candle match)
  if (markers) {
    for (const m of markers) {
      const i = m.index;
      if (i < 0 || i >= n) continue;
      const x = X(i), y = Y(m.price);
      if (m.kind === "entry") {
        const up = m.side === "LONG";
        ctx.fillStyle = C.accent;
        ctx.beginPath();
        if (up) { ctx.moveTo(x, y - 14); ctx.lineTo(x - 5, y - 5); ctx.lineTo(x + 5, y - 5); }
        else { ctx.moveTo(x, y + 14); ctx.lineTo(x - 5, y + 5); ctx.lineTo(x + 5, y + 5); }
        ctx.closePath(); ctx.fill();
      } else {
        ctx.strokeStyle = m.pnl >= 0 ? C.profit : C.loss;
        ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(x, y, 4.5, 0, Math.PI * 2); ctx.stroke();
        ctx.lineWidth = 1;
      }
    }
  }

  // x labels
  ctx.fillStyle = C.secondary;
  const ticks = [0, Math.floor(n / 3), Math.floor(2 * n / 3), n - 1];
  ticks.forEach(i => {
    ctx.fillText(fmtAxisTime(candles[i].time), Math.min(X(i) - 30, w - padR - 90), h - 10);
  });
}

async function refreshMarket(force) {
  if (!state.integrity) {
    const integ = await getJSON("/api/v2/integrity");
    if (integ) state.integrity = integ;
  }
  populateSymbols();
  if (!state.marketSymbol) return;

  const sym = state.marketSymbol, iv = state.marketInterval;
  const kl = await getJSON("/api/jev/klines?symbol=" + encodeURIComponent(sym) +
    "&interval=" + encodeURIComponent(iv) + "&limit=100");
  if (!kl || !kl.candles) return;
  state.candles = kl.candles;

  // history markers for this symbol
  if (!state.history) {
    const hist = await getJSON("/api/autotrade/history");
    if (hist) state.history = hist;
  }
  const markers = [];
  const trades = (state.history && state.history.closed_trades) || [];
  const ivMs = INTERVAL_MS[iv] || INTERVAL_MS["15m"];
  for (const t of trades) {
    if (String(t.symbol).toUpperCase() !== sym.toUpperCase()) continue;
    const tm = parseUtc(t.closed_at);
    if (isNaN(tm)) continue;
    let best = -1, bestD = Infinity;
    for (let i = 0; i < state.candles.length; i++) {
      const d = Math.abs(state.candles[i].time - tm);
      if (d < bestD) { bestD = d; best = i; }
    }
    if (bestD > 2 * ivMs) continue; // too far from visible range — skip
    const side = String(t.side || "").toUpperCase().indexOf("SHORT") >= 0 ? "SHORT" : "LONG";
    markers.push({ kind: "entry", index: best, price: Number(t.entry_price), side });
    markers.push({ kind: "exit", index: best, price: Number(t.exit_price), pnl: Number(t.pnl_usd) });
  }

  // SL/TP lines from open position
  const levels = [];
  const pos = ((state.status && state.status.open_positions) || [])
    .find(p => String(p.symbol).toUpperCase() === sym.toUpperCase());
  if (pos) {
    if (pos.stop_loss) levels.push({ label: "SL", price: Number(pos.stop_loss), color: C.loss, dash: [5, 4] });
    if (pos.tp1) levels.push({ label: "TP1", price: Number(pos.tp1), color: C.profit, dash: [5, 4] });
    if (pos.tp2) levels.push({ label: "TP2", price: Number(pos.tp2), color: C.profit, dash: [2, 3] });
    if (pos.entry_price) levels.push({ label: "Entry", price: Number(pos.entry_price), color: C.secondary, dash: [] });
  }

  drawCandleChart(state.candles, markers, levels);
  setUpdated("updated-market");
}

/* ---------------- lab ---------------- */

async function refreshIntegrity() {
  const integ = await getJSON("/api/v2/integrity");
  if (!integ) return;
  state.integrity = integ;
  setUpdated("updated-integrity");

  const fb = document.getElementById("lab-frozen-badge");
  fb.textContent = integ.universe_frozen ? "FROZEN" : "NOT FROZEN";
  fb.className = "badge " + (integ.universe_frozen ? "badge-frozen" : "badge-warn");

  const box = document.getElementById("lab-symbols");
  const syms = integ.universe_symbols || [];
  box.innerHTML = syms.length
    ? syms.map(s => '<span class="chip">' + esc(s) + "</span>").join("")
    : '<span class="empty-row">No symbols reported.</span>';

  const lg = integ.ledger || {};
  document.getElementById("lab-ledger").innerHTML = lg.exists
    ? esc(lg.events) + " events · last " + esc(lg.last_write || "—")
    : '<span class="warn-text">No ledger yet — created on next engine run</span>';

  document.getElementById("lab-history").textContent = num(integ.history_trades, 0);
  const rd = integ.recent_decisions || {};
  document.getElementById("lab-dec-sampled").textContent = num(rd.sampled, 0);
  document.getElementById("lab-dec-skipped").textContent = num(rd.skipped, 0);
  const dg = document.getElementById("lab-dec-degraded");
  dg.textContent = num(rd.telemetry_degraded, 0);
  dg.className = Number(rd.telemetry_degraded) > 0 ? "warn-text" : "";

  // keep market symbol list fresh
  populateSymbols();
}

async function refreshDecisions() {
  const d = await getJSON("/api/v2/decisions?limit=50");
  if (!d) return;
  setUpdated("updated-decisions");
  const tb = document.querySelector("#decisions-table tbody");
  const rows = d.decisions || [];
  if (!rows.length) {
    tb.innerHTML = '<tr><td colspan="4" class="empty-row">No decisions logged yet.</td></tr>';
    return;
  }
  tb.innerHTML = rows.map(r => {
    let reasonText = r.reason || "";
    if (r.details) {
      if (typeof r.details === "object") {
        const parts = [];
        if (r.details.setup) parts.push(r.details.setup);
        if (r.details.side) parts.push(r.details.side);
        if (r.details.score !== undefined) parts.push("score " + r.details.score);
        if (r.details.hurst !== undefined) parts.push("H=" + r.details.hurst);
        if (r.details.obi !== undefined) parts.push("OBI=" + r.details.obi);
        if (parts.length) {
          reasonText += " (" + parts.join(", ") + ")";
        } else {
          try {
            reasonText += " — " + JSON.stringify(r.details);
          } catch (_) {}
        }
      } else {
        reasonText += " — " + String(r.details);
      }
    }
    const fullReasonEsc = esc(reasonText);
    const shortReasonEsc = esc(reasonText.length > 120 ? reasonText.slice(0, 117) + "..." : reasonText);
    return "<tr><td>" + esc(r.ts_utc || "") + "</td>" +
      "<td>" + esc(r.symbol || "") + "</td>" +
      "<td>" + esc(r.outcome || "") + "</td>" +
      '<td title="' + fullReasonEsc + '">' + shortReasonEsc + "</td></tr>";
  }).join("");
}

/* ---------------- system ---------------- */

function boolTxt(v) { return v ? "yes" : "no"; }

async function refreshSystem() {
  const s = state.status || await getJSON("/api/autotrade/status");
  if (!s) return;
  state.status = s;
  setUpdated("updated-system");

  document.getElementById("sys-running").textContent = boolTxt(s.running);
  document.getElementById("sys-thread").textContent = boolTxt(s.thread_alive);
  document.getElementById("sys-tick").textContent =
    (s.seconds_since_tick === null || s.seconds_since_tick === undefined)
      ? "—" : Math.round(s.seconds_since_tick) + "s";
  document.getElementById("sys-price-fail").textContent = num(s.price_fetch_failures, 0);
  document.getElementById("sys-hist-fail").textContent = num(s.history_write_failures, 0);
  document.getElementById("sys-mode").textContent = esc(s.execution_mode || "—");

  document.getElementById("risk-alloc").textContent = usd(s.max_daily_allocation_usd);
  document.getElementById("risk-target").textContent = usd(s.daily_profit_target_usd);
  document.getElementById("risk-lev").textContent =
    (s.leverage === null || s.leverage === undefined) ? "—" : esc(s.leverage) + "x";
  document.getElementById("risk-maxpos").textContent = num(s.max_concurrent_positions, 0);
  document.getElementById("risk-notional").textContent = usd(s.notional_per_trade_usd);
}

async function refreshLogs() {
  const l = await getJSON("/api/logs");
  const pre = document.getElementById("logs-pre");
  if (!l) { pre.textContent = "Failed to load logs."; return; }
  const txt = typeof l.logs === "string" ? l.logs : JSON.stringify(l.logs || l, null, 1);
  const lines = txt.split("\n");
  pre.textContent = lines.slice(-200).join("\n") || "No log lines.";
}

/* ---------------- boot + refresh loops ---------------- */

async function initialLoad() {
  await refreshHeader();
  await refreshOverview();
  await refreshIntegrity();
  await refreshPositions();
  await refreshDecisions();
  await refreshSystem();
  await refreshLogs();
  await refreshMarket(true);
  hideBanner();
}

// Every 10s: header, overview, positions (engine state + numbers).
setInterval(async () => {
  await refreshHeader();
  await refreshOverview();
  await refreshPositions();
}, 10000);

// Every 30s: klines, decisions, integrity, system, logs.
setInterval(async () => {
  await refreshIntegrity();
  await refreshDecisions();
  await refreshMarket(true);
  await refreshSystem();
  await refreshLogs();
}, 30000);

// Redraw canvases on resize (charts re-render on next refresh; also redraw cached data).
window.addEventListener("resize", () => {
  if (state.overview) drawEquityChart(state.overview.equity_curve, state.overview.starting_balance_usd);
});

initialLoad();
