(function () {
  "use strict";

  const DEFAULT_NODE_ID = "rpi-radio";
  const NODE_STORAGE_KEY = "rpipulse:selected-node-id";
  let nodesCachePromise = null;

  async function fetchJson(url, options) {
    try {
      const response = await fetch(url, {
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        ...(options || {}),
      });
      if (!response.ok) {
        return null;
      }
      return await response.json();
    } catch (_err) {
      return null;
    }
  }

  async function putJson(url, payload) {
    try {
      const response = await fetch(url, {
        method: "PUT",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await response.json().catch(function () {
        return null;
      });
      return { ok: response.ok, status: response.status, data };
    } catch (_err) {
      return { ok: false, status: 0, data: null };
    }
  }

  function getAdminToken() {
    try {
      return sessionStorage.getItem("rpipulse_admin_token") || "";
    } catch (_err) {
      return "";
    }
  }


  function asArray(payload) {
    if (Array.isArray(payload)) return payload;
    if (!payload || typeof payload !== "object") return [];
    if (Array.isArray(payload.items)) return payload.items;
    if (Array.isArray(payload.series)) return payload.series;
    if (Array.isArray(payload.detections)) return payload.detections;
    return [];
  }

  function fmtTs(value) {
    if (value === null || value === undefined || value === "") return "--";
    const date = typeof value === "number" ? new Date(value) : new Date(String(value));
    if (Number.isNaN(date.getTime())) return "--";
    return date.toLocaleString();
  }

  function toNumber(value) {
    if (value === null || value === undefined || value === "") return null;
    const n = Number(value);
    return Number.isFinite(n) ? n : null;
  }

  function setText(id, value, fallback) {
    const node = document.getElementById(id);
    if (!node) return;
    node.textContent = value ?? fallback ?? node.textContent;
  }

  function renderRows(tbodyId, rows, rowBuilder, emptyMessage, colSpan) {
    const body = document.getElementById(tbodyId);
    if (!body) return;
    if (!rows || rows.length === 0) {
      body.innerHTML = `<tr><td class="py-2" colspan="${colSpan || 4}">${emptyMessage}</td></tr>`;
      return;
    }
    body.innerHTML = rows.map(rowBuilder).join("");
  }

  function normalizeNodes(payload) {
    if (!payload || !Array.isArray(payload.nodes)) return [];
    return payload.nodes
      .map(function (row) {
        if (!row || !row.id) return null;
        return {
          id: String(row.id),
          name: String(row.name || row.id),
          host: row.host ? String(row.host) : "",
        };
      })
      .filter(Boolean);
  }

  async function loadNodes() {
    if (!nodesCachePromise) {
      nodesCachePromise = (async function () {
        const payload = await fetchJson("/api/nodes");
        return normalizeNodes(payload);
      })();
    }
    return nodesCachePromise;
  }

  function readStoredNodeId() {
    try {
      if (!window.localStorage) return "";
      return window.localStorage.getItem(NODE_STORAGE_KEY) || "";
    } catch (_err) {
      return "";
    }
  }

  function persistSelectedNodeId(nodeId) {
    try {
      if (!window.localStorage) return;
      if (!nodeId) {
        window.localStorage.removeItem(NODE_STORAGE_KEY);
        return;
      }
      window.localStorage.setItem(NODE_STORAGE_KEY, nodeId);
    } catch (_err) {
      // Ignore storage restrictions.
    }
  }

  function pickSelectedNodeId(nodes, requestedId) {
    if (!nodes.length) return "";

    if (requestedId && nodes.some(function (node) { return node.id === requestedId; })) {
      return requestedId;
    }

    const storedId = readStoredNodeId();
    if (storedId && nodes.some(function (node) { return node.id === storedId; })) {
      return storedId;
    }

    if (nodes.length === 1) {
      return nodes[0].id;
    }

    const preferred = nodes.find(function (node) { return node.id === DEFAULT_NODE_ID; });
    return preferred ? preferred.id : nodes[0].id;
  }

  async function resolveNodeContext(requestedId) {
    const nodes = await loadNodes();
    const selectedId = pickSelectedNodeId(nodes, requestedId);
    persistSelectedNodeId(selectedId);
    return { nodes, selectedId };
  }

  function withQuery(url, params) {
    if (!params) return url;
    const search = new URLSearchParams();
    Object.keys(params).forEach(function (key) {
      const value = params[key];
      if (value === null || value === undefined || value === "") return;
      search.set(key, String(value));
    });
    const qs = search.toString();
    return qs ? `${url}?${qs}` : url;
  }

  function nodeApiUrl(nodeId, nodeSuffix, localPath, params) {
    const base = nodeId
      ? `/api/nodes/${encodeURIComponent(nodeId)}${nodeSuffix}`
      : localPath;
    return withQuery(base, params);
  }

  function dashboardKpisUrl(nodeId) {
    return nodeApiUrl(nodeId, "/kpis/now", "/api/kpis/now");
  }

  function hourlySeriesUrl(nodeId) {
    return nodeApiUrl(nodeId, "/series/hourly", "/api/series/hourly");
  }

  function dailySeriesUrl(nodeId) {
    return nodeApiUrl(nodeId, "/series/daily", "/api/series/daily");
  }

  function observationsLatestUrl(nodeId, limit) {
    return nodeApiUrl(nodeId, "/observations/latest", "/api/observations/latest", { limit: limit || 20 });
  }

  function detectionsRecentUrl(nodeId, seconds, limit) {
    return nodeApiUrl(nodeId, "/detections/recent", "/api/detections/recent", {
      seconds: seconds || 300,
      limit: limit || 200,
    });
  }

  function detectionsTopUrl(nodeId, limit) {
    const now = Date.now();
    const from = now - 24 * 60 * 60 * 1000;
    return nodeApiUrl(nodeId, "/detections/top", "/api/detections/top", {
      from_ms: from,
      to_ms: now,
      limit: limit || 50,
    });
  }

  function sensorConfigGetUrl(nodeId) {
    return nodeApiUrl(nodeId, "/config", "/api/config");
  }

  function sensorConfigPutUrl(nodeId) {
    const base = sensorConfigGetUrl(nodeId);
    // Only the HUB proxy PUT requires token. The node-local /api/config does not.
    if (!base.startsWith("/api/nodes/")) return base;
    const token = getAdminToken();
    if (!token) return base;
    return `${base}?token=${encodeURIComponent(token)}`;
  }

  function normalizeConfigForSensorsForm(config) {
    if (!config || typeof config !== "object") return null;
    // Support both naming styles.
    const interval = config.scan_interval_s ?? config.interval;
    const duration = config.scan_duration_s ?? config.duration;
    const rssi = config.rssi_min_dbm ?? config.rssi_threshold;
    const retention = config.retention_days;
    return {
      scan_interval_s: interval,
      scan_duration_s: duration,
      rssi_min_dbm: rssi,
      retention_days: retention,
    };
  }

  function normalizeSensorsPayloadForApi(payload) {
    if (!payload || typeof payload !== "object") return payload;
    // Convert UI field names -> API field names used by backend.
    return {
      interval: payload.scan_interval_s,
      duration: payload.scan_duration_s,
      rssi_threshold: payload.rssi_min_dbm,
      retention_days: payload.retention_days,
    };
  }

  function unwrapConfigPayload(payload) {
    if (!payload || typeof payload !== "object") return null;
    if (payload.config && typeof payload.config === "object") return payload.config;
    return payload;
  }

  function detectionDeviceId(row) {
    const raw = row && (row.anon_device_id || row.device_id || row.device || row.id);
    if (!raw) return "--";
    const text = String(raw);
    if (text.startsWith("anon_")) return text;
    if (text.length <= 8) return text;
    return `${text.slice(0, 4)}••${text.slice(-4)}`;
  }

  function detectionTransport(row) {
    const transport = row && row.transport ? String(row.transport) : "UNKNOWN";
    return transport.toUpperCase();
  }

  function detectionLastSeen(row) {
    return row.last_seen || row.last_seen_iso || row.last_seen_at || row.time || row.ts_iso || row.ts_ms || row.last_seen_ms;
  }

  function detectionFirstSeen(row) {
    return row.first_seen || row.first_seen_iso || row.first_seen_at || row.first_seen_ms;
  }

  async function hydrateDashboard(nodeId) {
    const [kpis, hourlySeries, latest, recentDetections] = await Promise.all([
      fetchJson(dashboardKpisUrl(nodeId)),
      fetchJson(hourlySeriesUrl(nodeId)),
      fetchJson(observationsLatestUrl(nodeId, 5)),
      fetchJson(detectionsRecentUrl(nodeId, 300, 10)),
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
    if (kpis && kpis.trend) {
      const trend = kpis.trend;
      if (trend.delta_pct === null || trend.delta_pct === undefined) {
        setText("kpi-trend-delta", "N/A");
      } else {
        const v = Number(trend.delta_pct);
        const arrow = v > 0 ? "↑" : v < 0 ? "↓" : "→";
        setText("kpi-trend-delta", `${arrow} ${Math.abs(v).toFixed(1)}%`);
      }
      if (Number.isFinite(trend.avg_last_60m)) {
        setText("kpi-trend-current", Number(trend.avg_last_60m).toFixed(2));
      }
      if (Number.isFinite(trend.avg_prev_60m)) {
        setText("kpi-trend-prev", Number(trend.avg_prev_60m).toFixed(2));
      }
    }

    const hourlyItems = asArray(hourlySeries).slice(-8).reverse();
    renderRows(
      "dashboard-hourly-body",
      hourlyItems,
      function (row) {
        return `<tr>
        <td class="py-2">${row.hour || "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_unique) ? row.peak_unique : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.avg_unique) ? row.avg_unique.toFixed(2) : "--"}</td>
      </tr>`;
      },
      "No hourly points yet.",
      3
    );

    const latestItems = asArray(latest);
    renderRows(
      "dashboard-latest-body",
      latestItems,
      function (row) {
        return `<tr>
        <td class="py-2">${fmtTs(row.ts_iso || row.ts_ms)}</td>
        <td class="py-2 text-right">${Number.isFinite(row.unique_devices_count) ? row.unique_devices_count : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.raw_count) ? row.raw_count : "--"}</td>
      </tr>`;
      },
      "No observations yet.",
      3
    );

    const detections = asArray(recentDetections);
    renderRows(
      "dashboard-detections-body",
      detections.slice(0, 8),
      function (row) {
        return `<tr>
        <td class="py-2">${fmtTs(detectionLastSeen(row))}</td>
        <td class="py-2">${detectionDeviceId(row)}</td>
        <td class="py-2">${detectionTransport(row)}</td>
        <td class="py-2 text-right">${toNumber(row.rssi_dbm ?? row.rssi) ?? "--"}</td>
      </tr>`;
      },
      "No detections yet.",
      4
    );
    setText(
      "dashboard-detections-note",
      recentDetections
        ? `Last 5 minutes via ${nodeId ? "/api/nodes/{id}/detections/recent" : "/api/detections/recent"}`
        : "Detection API not available yet"
    );
  }

  async function hydrateTrends(nodeId) {
    const [daily, hourly, topDetections] = await Promise.all([
      fetchJson(dailySeriesUrl(nodeId)),
      fetchJson(hourlySeriesUrl(nodeId)),
      fetchJson(detectionsTopUrl(nodeId, 20)),
    ]);

    const dailyItems = asArray(daily);
    const hourlyItems = asArray(hourly);

    renderRows(
      "trends-daily-body",
      dailyItems.slice().reverse(),
      function (row) {
        return `<tr>
        <td class="py-2">${row.day || "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_unique) ? row.peak_unique : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.avg_unique) ? row.avg_unique.toFixed(2) : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_raw) ? row.peak_raw : "--"}</td>
      </tr>`;
      },
      "No daily data yet.",
      4
    );

    renderRows(
      "trends-hourly-body",
      hourlyItems.slice().reverse().slice(0, 24),
      function (row) {
        return `<tr>
        <td class="py-2">${row.hour || "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_unique) ? row.peak_unique : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.avg_unique) ? row.avg_unique.toFixed(2) : "--"}</td>
        <td class="py-2 text-right">${Number.isFinite(row.peak_raw) ? row.peak_raw : "--"}</td>
      </tr>`;
      },
      "No hourly data yet.",
      4
    );

    const topItems = asArray(topDetections);
    renderRows(
      "trends-top-detections-body",
      topItems,
      function (row) {
        return `<tr>
          <td class="py-2">${detectionDeviceId(row)}</td>
          <td class="py-2">${detectionTransport(row)}</td>
          <td class="py-2 text-right">${toNumber(row.seen_count) ?? "--"}</td>
          <td class="py-2 text-right">${toNumber(row.rssi_dbm ?? row.rssi) ?? "--"}</td>
        </tr>`;
      },
      "No top detections yet.",
      4
    );

    const transports = topItems.reduce(function (acc, row) {
      const key = detectionTransport(row);
      const count = toNumber(row.seen_count) || 0;
      acc[key] = (acc[key] || 0) + count;
      return acc;
    }, {});
    const transportBody = document.getElementById("trends-transport-body");
    if (transportBody) {
      const entries = Object.entries(transports).sort(function (a, b) {
        return b[1] - a[1];
      });
      if (!entries.length) {
        transportBody.innerHTML = `<li class="py-2 text-text-muted">No transport mix data yet.</li>`;
      } else {
        transportBody.innerHTML = entries
          .map(function (entry) {
            return `<li class="py-2 flex items-center justify-between border-b border-surface-highlight/70"><span>${entry[0]}</span><span class="font-mono text-primary">${entry[1]}</span></li>`;
          })
          .join("");
      }
    }
    setText(
      "trends-top-note",
      topDetections
        ? `Top devices over last 24h via ${nodeId ? "/api/nodes/{id}/detections/top" : "/api/detections/top"}`
        : "Top detections API not available yet"
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

    const labels = dailyItems.map(function (item) {
      return item.day || "--";
    });
    const data = dailyItems.map(function (item) {
      return Number.isFinite(item.peak_unique) ? item.peak_unique : 0;
    });

    if (window.__rpipulseDailyChart) {
      window.__rpipulseDailyChart.destroy();
    }

    window.__rpipulseDailyChart = new window.Chart(canvas, {
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
    if (note) {
      note.textContent = `Daily peak chart rendered from ${nodeId ? "/api/nodes/{id}/series/daily" : "/api/series/daily"}.`;
    }
  }

  function normalizeDetectionRow(row) {
    return {
      time: detectionLastSeen(row),
      anon_device_id: row.anon_device_id || row.device_id || row.device || row.id || "--",
      transport: detectionTransport(row),
      rssi_dbm: toNumber(row.rssi_dbm ?? row.rssi),
      seen_count: toNumber(row.seen_count) || 0,
      first_seen: detectionFirstSeen(row),
      last_seen: detectionLastSeen(row),
    };
  }

  function upsertDetection(items, row) {
    const device = row.anon_device_id;
    const transport = row.transport;
    const key = `${device}::${transport}`;
    const idx = items.findIndex(function (item) {
      return `${item.anon_device_id}::${item.transport}` === key;
    });
    if (idx === -1) {
      items.unshift(row);
    } else {
      items[idx] = row;
    }
    items.sort(function (a, b) {
      return new Date(b.last_seen).getTime() - new Date(a.last_seen).getTime();
    });
    if (items.length > 300) items.length = 300;
  }

  async function hydrateMonitor() {
    const streamState = document.getElementById("monitor-stream-state");
    const deviceInput = document.getElementById("monitor-filter-device");
    const rssiInput = document.getElementById("monitor-filter-rssi");
    const transportInput = document.getElementById("monitor-filter-transport");
    const refreshBtn = document.getElementById("monitor-refresh-btn");
    const resultCount = document.getElementById("monitor-result-count");

    var rows = [];

    function refreshTransportOptions(sourceRows) {
      if (!transportInput) return;
      const selected = transportInput.value || "";
      const set = new Set([""]);
      sourceRows.forEach(function (row) {
        set.add(row.transport);
      });
      const options = Array.from(set).sort();
      transportInput.innerHTML = options
        .map(function (value) {
          const selectedAttr = value === selected ? " selected" : "";
          return `<option value="${value}"${selectedAttr}>${value || "ALL"}</option>`;
        })
        .join("");
    }

    function applyFilters() {
      const filterDevice = deviceInput ? deviceInput.value.trim().toLowerCase() : "";
      const minRssi = rssiInput ? toNumber(rssiInput.value) : null;
      const filterTransport = transportInput ? transportInput.value : "";

      const filtered = rows.filter(function (row) {
        if (filterDevice && String(row.anon_device_id).toLowerCase().indexOf(filterDevice) === -1) return false;
        if (minRssi !== null && row.rssi_dbm !== null && row.rssi_dbm < minRssi) return false;
        if (minRssi !== null && row.rssi_dbm === null) return false;
        if (filterTransport && row.transport !== filterTransport) return false;
        return true;
      });

      renderRows(
        "monitor-detections-body",
        filtered,
        function (row) {
          return `<tr>
            <td class="p-3">${fmtTs(row.time)}</td>
            <td class="p-3">${detectionDeviceId(row)}</td>
            <td class="p-3">${row.transport}</td>
            <td class="p-3 text-right">${row.rssi_dbm ?? "--"}</td>
            <td class="p-3 text-right">${row.seen_count}</td>
            <td class="p-3">${fmtTs(row.first_seen)}</td>
            <td class="p-3">${fmtTs(row.last_seen)}</td>
          </tr>`;
        },
        "No detections match current filters.",
        7
      );

      if (resultCount) resultCount.textContent = `${filtered.length} / ${rows.length}`;
    }

    async function loadRecent() {
      const payload = await fetchJson("/api/detections/recent?seconds=300&limit=200");
      if (!payload) {
        if (streamState && !rows.length) streamState.textContent = "Waiting for detection API...";
        applyFilters();
        return;
      }
      rows = asArray(payload).map(normalizeDetectionRow);
      refreshTransportOptions(rows);
      applyFilters();
      if (streamState) streamState.textContent = "Connected";
    }

    [deviceInput, rssiInput, transportInput].forEach(function (el) {
      if (!el) return;
      el.addEventListener("input", applyFilters);
      el.addEventListener("change", applyFilters);
    });

    if (refreshBtn) refreshBtn.addEventListener("click", loadRecent);

    await loadRecent();

    if (typeof window.EventSource !== "function") {
      return;
    }

    try {
      const source = new window.EventSource("/api/stream/detections");
      source.addEventListener("open", function () {
        if (streamState) streamState.textContent = "Streaming";
      });

      function handleSse(event) {
        try {
          const parsed = JSON.parse(event.data);
          const list = Array.isArray(parsed) ? parsed : [parsed.item || parsed];
          list.forEach(function (item) {
            if (!item || typeof item !== "object") return;
            upsertDetection(rows, normalizeDetectionRow(item));
          });
          refreshTransportOptions(rows);
          applyFilters();
        } catch (_err) {
          // Ignore malformed payloads and keep stream alive.
        }
      }

      source.addEventListener("detection", handleSse);
      source.addEventListener("message", handleSse);
      source.addEventListener("error", function () {
        if (streamState) streamState.textContent = "Connected (polling)";
      });
    } catch (_err) {
      // Optional SSE endpoint: fallback is periodic refresh.
    }

    window.setInterval(loadRecent, 15000);
  }

  function ensureToastContainer() {
    let node = document.getElementById("toast-container");
    if (node) return node;
    node = document.createElement("div");
    node.id = "toast-container";
    node.className = "fixed bottom-4 right-4 z-[70] flex flex-col gap-2";
    document.body.appendChild(node);
    return node;
  }

  function showToast(message, kind) {
    const container = ensureToastContainer();
    const toast = document.createElement("div");
    const palette = kind === "error" ? "border-accent-red text-accent-red" : "border-status-green text-status-green";
    toast.className = `rounded-md border bg-surface px-4 py-3 text-sm font-mono shadow-xl ${palette}`;
    toast.textContent = message;
    container.appendChild(toast);
    window.setTimeout(function () {
      toast.remove();
    }, 2600);
  }

  async function hydrateSensors(nodeId) {
    const form = document.getElementById("sensors-config-form");
    if (!form) return;

    const saveBtn = document.getElementById("sensors-save-btn");
    const reloadBtn = document.getElementById("sensors-reload-btn");
    const statusNode = document.getElementById("sensors-status");
    const loadedNode = document.getElementById("sensors-last-loaded");
    const configGetUrl = sensorConfigGetUrl(nodeId);
    const configPutUrl = sensorConfigPutUrl(nodeId);

    const fields = {
      scan_interval_s: document.getElementById("scan_interval_s"),
      scan_duration_s: document.getElementById("scan_duration_s"),
      rssi_min_dbm: document.getElementById("rssi_min_dbm"),
      retention_days: document.getElementById("retention_days"),
    };

    function setStatus(text, isError) {
      if (!statusNode) return;
      statusNode.textContent = text;
      statusNode.classList.toggle("text-accent-red", Boolean(isError));
      statusNode.classList.toggle("text-text-muted", !isError);
    }

    function collectPayload() {
      return {
        scan_interval_s: toNumber(fields.scan_interval_s && fields.scan_interval_s.value),
        scan_duration_s: toNumber(fields.scan_duration_s && fields.scan_duration_s.value),
        rssi_min_dbm: toNumber(fields.rssi_min_dbm && fields.rssi_min_dbm.value),
        retention_days: toNumber(fields.retention_days && fields.retention_days.value),
      };
    }

    function applyPayload(payload) {
      Object.keys(fields).forEach(function (key) {
        const input = fields[key];
        if (!input) return;
        const value = payload && payload[key] !== undefined && payload[key] !== null ? payload[key] : "";
        input.value = String(value);
      });
    }

    async function loadConfig() {
      setStatus(`Loading config from ${configGetUrl} ...`, false);
      const payload = await fetchJson(configGetUrl);
      const config = unwrapConfigPayload(payload);
      const formConfig = normalizeConfigForSensorsForm(config);
      if (!config) {
        setStatus(`${configGetUrl} not available yet.`, true);
        return;
      }
      applyPayload(formConfig || {});
      if (loadedNode) loadedNode.textContent = fmtTs(new Date().toISOString());
      setStatus("Config loaded.", false);
    }

    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      const payload = collectPayload();
      if (saveBtn) saveBtn.setAttribute("disabled", "disabled");
      setStatus("Saving config ...", false);
      const apiPayload = normalizeSensorsPayloadForApi(payload);
      const response = await putJson(configPutUrl, apiPayload);
      if (saveBtn) saveBtn.removeAttribute("disabled");

      if (!response.ok) {
        setStatus(`Could not save config (status ${response.status || "n/a"}).`, true);
        showToast("Error guardando configuración", "error");
        return;
      }

      const updatedConfig = unwrapConfigPayload(response.data);
      const updatedFormConfig = normalizeConfigForSensorsForm(updatedConfig);
      if (updatedFormConfig && typeof updatedFormConfig === "object") {
        applyPayload(updatedFormConfig);
      }
      setStatus("Config saved.", false);
      if (loadedNode) loadedNode.textContent = fmtTs(new Date().toISOString());
      showToast("Configuración guardada", "success");
    });

    if (reloadBtn) {
      reloadBtn.addEventListener("click", function () {
        loadConfig();
      });
    }

    await loadConfig();
  }

  async function initDashboard() {
    const selector = document.getElementById("dashboard-node-select");
    const hint = document.getElementById("dashboard-node-hint");

    function updateHint(nodes, selectedId) {
      if (!hint) return;
      if (!selectedId) {
        hint.textContent = nodes.length ? "Selecciona un nodo (o usa hub local)" : "Modo standalone (hub local)";
        return;
      }
      const node = nodes.find(function (item) {
        return item.id === selectedId;
      });
      if (!node) {
        hint.textContent = "Nodo seleccionado";
        return;
      }
      hint.textContent = `Fuente: ${node.name} (${node.host || "host n/a"})`;
    }

    const context = await resolveNodeContext();
    const nodes = context.nodes;
    const initialNodeId = context.selectedId || "";

    if (!selector) {
      updateHint(nodes, initialNodeId);
      await hydrateDashboard(initialNodeId || null);
      return;
    }

    selector.innerHTML = "";
    const localOption = document.createElement("option");
    localOption.value = "";
    localOption.textContent = "Hub local";
    selector.appendChild(localOption);

    nodes.forEach(function (node) {
      const option = document.createElement("option");
      option.value = node.id;
      option.textContent = node.name;
      selector.appendChild(option);
    });

    if (initialNodeId) {
      selector.value = initialNodeId;
    }

    updateHint(nodes, selector.value || "");
    await hydrateDashboard(selector.value || null);

    selector.addEventListener("change", function () {
      const selectedId = selector.value || "";
      persistSelectedNodeId(selectedId);
      updateHint(nodes, selectedId);
      hydrateDashboard(selectedId || null);
    });
  }

  async function init() {
    const pageRoot = document.querySelector("[data-page]");
    if (!pageRoot) return;
    const page = pageRoot.getAttribute("data-page");

    if (page === "dashboard") {
      await initDashboard();
      return;
    }

    if (page === "trends") {
      const context = await resolveNodeContext();
      await hydrateTrends(context.selectedId || null);
      return;
    }

    if (page === "monitor") {
      await hydrateMonitor();
      return;
    }

    if (page === "sensors") {
      const context = await resolveNodeContext();
      await hydrateSensors(context.selectedId || null);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      init();
    });
  } else {
    init();
  }
})();
