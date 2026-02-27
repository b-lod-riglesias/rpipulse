(function () {
  "use strict";

  async function fetchJson(url) {
    try {
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      if (!response.ok) return null;
      return await response.json();
    } catch (_err) {
      return null;
    }
  }

  function fmtTs(value) {
    if (!value) return "--";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return "--";
    return d.toLocaleString();
  }

  function setText(id, value, fallback) {
    const node = document.getElementById(id);
    if (!node) return;
    node.textContent = value ?? fallback ?? node.textContent;
  }

  function renderRows(tbodyId, rows, rowBuilder, emptyMessage) {
    const body = document.getElementById(tbodyId);
    if (!body) return;
    if (!rows || rows.length === 0) {
      body.innerHTML = `<tr><td class="py-2" colspan="4">${emptyMessage}</td></tr>`;
      return;
    }
    body.innerHTML = rows.map(rowBuilder).join("");
  }

  async function hydrateDashboard() {
    const [kpis, hourlySeries, latest] = await Promise.all([
      fetchJson("/api/kpis/now"),
      fetchJson("/api/series/hourly"),
      fetchJson("/api/observations/latest?limit=5"),
    ]);

    const obs = kpis && kpis.observation ? kpis.observation : null;
    setText("kpi-active-devices", obs && Number.isFinite(obs.unique_devices_count) ? String(obs.unique_devices_count) : "--");
    setText("kpi-capacity", kpis && Number.isFinite(kpis.capacity) ? String(kpis.capacity) : "60");
    setText("kpi-load", obs && Number.isFinite(obs.load_pct) ? `${obs.load_pct}%` : "--");
    setText("kpi-raw", obs && Number.isFinite(obs.raw_count) ? String(obs.raw_count) : "--");
    setText("kpi-ts", obs ? fmtTs(obs.ts_iso || obs.ts_ms) : "--");
    setText("kpi-sensor-status", kpis && kpis.status ? String(kpis.status).toUpperCase() : "UNKNOWN");
    setText("updated-at", `UPDATED: ${new Date().toLocaleTimeString()}`);
    if (kpis && kpis.hourly && Number.isFinite(kpis.hourly.avg_unique)) {
      setText("kpi-hourly-avg", `Avg: ${kpis.hourly.avg_unique.toFixed(2)}`);
    }

    const hourlyItems = hourlySeries && Array.isArray(hourlySeries.items) ? hourlySeries.items.slice(-8).reverse() : [];
    renderRows(
      "dashboard-hourly-body",
      hourlyItems,
      (row) => `<tr>
        <td class="py-2">${row.hour || "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_unique) ? row.peak_unique : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.avg_unique) ? row.avg_unique.toFixed(2) : "--"}</td>
      </tr>`,
      "No hourly points yet."
    );

    const latestItems = latest && Array.isArray(latest.items) ? latest.items : [];
    renderRows(
      "dashboard-latest-body",
      latestItems,
      (row) => `<tr>
        <td class="py-2">${fmtTs(row.ts_iso || row.ts_ms)}</td>
        <td class="py-2 text-right">${Number.isFinite(row.unique_devices_count) ? row.unique_devices_count : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.raw_count) ? row.raw_count : "--"}</td>
      </tr>`,
      "No observations yet."
    );
  }

  async function hydrateTrends() {
    const [daily, hourly] = await Promise.all([fetchJson("/api/series/daily"), fetchJson("/api/series/hourly")]);
    const dailyItems = daily && Array.isArray(daily.items) ? daily.items : [];
    const hourlyItems = hourly && Array.isArray(hourly.items) ? hourly.items : [];

    renderRows(
      "trends-daily-body",
      dailyItems.slice().reverse(),
      (row) => `<tr>
        <td class="py-2">${row.day || "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_unique) ? row.peak_unique : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.avg_unique) ? row.avg_unique.toFixed(2) : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_raw) ? row.peak_raw : "--"}</td>
      </tr>`,
      "No daily data yet."
    );

    renderRows(
      "trends-hourly-body",
      hourlyItems.slice().reverse().slice(0, 24),
      (row) => `<tr>
        <td class="py-2">${row.hour || "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_unique) ? row.peak_unique : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.avg_unique) ? row.avg_unique.toFixed(2) : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_raw) ? row.peak_raw : "--"}</td>
      </tr>`,
      "No hourly data yet."
    );

    const canvas = document.getElementById("trends-chart");
    const note = document.getElementById("trends-chart-note");
    if (!canvas) return;

    if (typeof window.Chart !== "function") {
      if (note) note.textContent = "Chart.js unavailable, using table fallback.";
      return;
    }

    if (!dailyItems.length) {
      if (note) note.textContent = "No daily series available yet.";
      return;
    }

    const labels = dailyItems.map((item) => item.day || "--");
    const data = dailyItems.map((item) => (Number.isFinite(item.peak_unique) ? item.peak_unique : 0));
    new window.Chart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "Daily Peak Unique",
            data,
            borderColor: "#D69E1C",
            backgroundColor: "rgba(214, 158, 28, 0.2)",
            fill: true,
            tension: 0.25,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#EAEAEA" } } },
        scales: {
          x: { ticks: { color: "#888888" }, grid: { color: "#2A2A2A" } },
          y: { ticks: { color: "#888888" }, grid: { color: "#2A2A2A" } },
        },
      },
    });
    if (note) note.textContent = "Daily peak chart rendered from /api/series/daily.";
  }

  function prependMonitorRow(obs) {
    const body = document.getElementById("monitor-observations-body");
    if (!body || !obs) return;
    const row = document.createElement("tr");
    row.className = "new-row";
    row.innerHTML = `<td class="p-3">${fmtTs(obs.ts_iso || obs.ts_ms)}</td>
      <td class="p-3 text-right">${Number.isFinite(obs.unique_devices_count) ? obs.unique_devices_count : "--"}</td>
      <td class="p-3 text-right">${Number.isFinite(obs.raw_count) ? obs.raw_count : "--"}</td>`;

    const current = body.querySelector("tr td[colspan]");
    if (current) body.innerHTML = "";
    body.prepend(row);

    while (body.children.length > 50) {
      body.removeChild(body.lastElementChild);
    }
  }

  async function hydrateMonitor() {
    const streamState = document.getElementById("monitor-stream-state");

    const seed = await fetchJson("/api/observations/latest?limit=20");
    const seedItems = seed && Array.isArray(seed.items) ? seed.items : [];
    if (seedItems.length) {
      const body = document.getElementById("monitor-observations-body");
      if (body) body.innerHTML = "";
      seedItems.forEach((item) => prependMonitorRow(item));
    }

    if (typeof window.EventSource !== "function") {
      if (streamState) streamState.textContent = "EventSource unavailable";
      return;
    }

    try {
      const source = new window.EventSource("/api/stream/observations");
      source.addEventListener("open", function () {
        if (streamState) streamState.textContent = "Connected";
      });
      source.addEventListener("observation", function (event) {
        try {
          prependMonitorRow(JSON.parse(event.data));
        } catch (_err) {
          // Keep running with last known state.
        }
      });
      source.addEventListener("error", function () {
        if (streamState) streamState.textContent = "Disconnected";
      });
    } catch (_err) {
      if (streamState) streamState.textContent = "SSE unavailable";
    }
  }

  function init() {
    const pageRoot = document.querySelector("[data-page]");
    if (!pageRoot) return;
    const page = pageRoot.getAttribute("data-page");
    if (page === "dashboard") hydrateDashboard();
    if (page === "trends") hydrateTrends();
    if (page === "monitor") hydrateMonitor();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
