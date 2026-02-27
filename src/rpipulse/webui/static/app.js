const el = (id) => document.getElementById(id);

function fmtNumber(value) {
  if (value === null || value === undefined) return "--";
  return Number(value).toFixed(0);
}

function fmtFloat(value) {
  if (value === null || value === undefined) return "--";
  return Number(value).toFixed(2);
}

function drawSeries(svgId, points, key) {
  const svg = el(svgId);
  if (!svg) return;

  if (!points || points.length === 0) {
    svg.innerHTML = `<text x="50" y="16" text-anchor="middle" fill="#9b9b9b" font-size="4">No data</text>`;
    return;
  }

  const values = points.map((p) => Number(p[key] ?? 0));
  const max = Math.max(...values, 1);

  const path = values
    .map((v, i) => {
      const x = (i / Math.max(values.length - 1, 1)) * 100;
      const y = 28 - (v / max) * 24;
      return `${i === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  svg.innerHTML = `
    <line x1="0" y1="28" x2="100" y2="28" stroke="#2a2a2a" stroke-width="0.4" />
    <path d="${path}" fill="none" stroke="#d69e1c" stroke-width="0.8" />
  `;
}

async function getJson(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json();
}

async function refresh() {
  const [health, latest, daily, hourly, kpis] = await Promise.all([
    getJson("/api/health"),
    getJson("/api/observations/latest"),
    getJson("/api/series/daily?days=30"),
    getJson("/api/series/hourly?days=7"),
    getJson("/api/kpis/now"),
  ]);

  el("updated-at").textContent = `updated ${new Date().toLocaleTimeString()}`;

  el("health-status").textContent = health.status;
  el("health-db").textContent = health.db_path;
  el("health-observations").textContent = fmtNumber(health.observations);

  const obs = latest.observation;
  el("latest-json").textContent = JSON.stringify(obs, null, 2);
  el("active-devices").textContent = obs ? fmtNumber(obs.unique_devices_count) : "--";

  const trend = kpis.trend?.delta_pct;
  el("trend-text").textContent =
    trend === null || trend === undefined
      ? "trend --"
      : `trend ${trend >= 0 ? "+" : ""}${trend.toFixed(2)}% vs prev 60m`;

  el("samples-24h").textContent = fmtNumber(kpis.last_24h?.samples);
  el("avg-24h").textContent = fmtFloat(kpis.last_24h?.avg_unique);
  el("peak-24h").textContent = fmtNumber(kpis.last_24h?.peak_unique);
  el("peak-raw-24h").textContent = fmtNumber(kpis.last_24h?.peak_raw);

  drawSeries("chart-daily", daily.series, "peak_unique");
  drawSeries("chart-hourly", hourly.series, "peak_unique");
}

function wireSse() {
  const evt = new EventSource("/api/stream/observations");
  evt.addEventListener("observation", (event) => {
    try {
      const payload = JSON.parse(event.data);
      if (payload?.observation) {
        el("latest-json").textContent = JSON.stringify(payload.observation, null, 2);
        el("active-devices").textContent = fmtNumber(payload.observation.unique_devices_count);
      }
    } catch (_) {
      // ignore malformed SSE payloads
    }
  });
}

refresh().catch((err) => {
  el("latest-json").textContent = `error: ${err.message}`;
});
setInterval(() => refresh().catch(() => undefined), 15000);
wireSse();
