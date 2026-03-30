(function () {
  "use strict";

  const NODE_STORAGE_KEY = "rpipulse:selected-node-ids";
  const LEGACY_NODE_STORAGE_KEY = "rpipulse:selected-node-id";
  const NODE_QUERY_PARAM = "nodes";
  const NODE_QUERY_ALL = "all";
  const NODE_QUERY_LOCAL = "local";
  const ALL_NODES_OPTION_VALUE = "__all__";
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

  async function postJson(url, payload) {
    try {
      const response = await fetch(url, {
        method: "POST",
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

  function setAdminToken(token) {
    try {
      if (!token) {
        sessionStorage.removeItem("rpipulse_admin_token");
        return;
      }
      sessionStorage.setItem("rpipulse_admin_token", token);
    } catch (_err) {
      // Ignore storage restrictions.
    }
  }

  function downloadTextFile(filename, content) {
    if (!filename || content === null || content === undefined) return;
    const blob = new Blob([String(content)], { type: "application/x-sh;charset=utf-8" });
    const url = window.URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    window.setTimeout(function () {
      window.URL.revokeObjectURL(url);
    }, 1000);
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

  function sanitizeNodeIds(value) {
    if (!Array.isArray(value)) return [];
    const seen = new Set();
    const ids = [];
    value.forEach(function (item) {
      if (!item) return;
      const text = String(item).trim();
      if (!text || seen.has(text)) return;
      seen.add(text);
      ids.push(text);
    });
    return ids;
  }

  function readStoredSelection() {
    try {
      if (!window.localStorage) return { exists: false, nodeIds: [] };
      const raw = window.localStorage.getItem(NODE_STORAGE_KEY);
      if (raw !== null) {
        const parsed = JSON.parse(raw);
        const list = sanitizeNodeIds(parsed);
        return { exists: true, nodeIds: list };
      }
      const legacy = window.localStorage.getItem(LEGACY_NODE_STORAGE_KEY) || "";
      if (legacy) {
        return { exists: true, nodeIds: [legacy] };
      }
      return { exists: false, nodeIds: [] };
    } catch (_err) {
      return { exists: false, nodeIds: [] };
    }
  }

  function persistSelectedNodeIds(nodeIds) {
    const selected = sanitizeNodeIds(nodeIds);
    try {
      if (!window.localStorage) return;
      window.localStorage.setItem(NODE_STORAGE_KEY, JSON.stringify(selected));
      if (selected.length) {
        window.localStorage.setItem(LEGACY_NODE_STORAGE_KEY, selected[0]);
      } else {
        window.localStorage.removeItem(LEGACY_NODE_STORAGE_KEY);
      }
    } catch (_err) {
      // Ignore storage restrictions.
    }
  }

  function readSelectionFromUrl() {
    try {
      const url = new URL(window.location.href);
      if (!url.searchParams.has(NODE_QUERY_PARAM)) {
        return { exists: false, kind: "", nodeIds: [] };
      }
      const raw = String(url.searchParams.get(NODE_QUERY_PARAM) || "").trim();
      if (!raw || raw === NODE_QUERY_LOCAL) {
        return { exists: true, kind: NODE_QUERY_LOCAL, nodeIds: [] };
      }
      if (raw === NODE_QUERY_ALL) {
        return { exists: true, kind: NODE_QUERY_ALL, nodeIds: [] };
      }
      const csvIds = sanitizeNodeIds(raw.split(","));
      return { exists: true, kind: "partial", nodeIds: csvIds };
    } catch (_err) {
      return { exists: false, kind: "", nodeIds: [] };
    }
  }

  function selectionQueryValue(nodes, selectedIds) {
    const selected = sanitizeNodeIds(selectedIds);
    if (!selected.length) return NODE_QUERY_LOCAL;
    if (nodes.length && selected.length === nodes.length) return NODE_QUERY_ALL;
    return selected.join(",");
  }

  function updateSelectionInUrl(nodes, selectedIds) {
    if (!window.history || typeof window.history.replaceState !== "function") return;
    try {
      const url = new URL(window.location.href);
      url.searchParams.set(NODE_QUERY_PARAM, selectionQueryValue(nodes, selectedIds));
      const nextUrl = `${url.pathname}${url.search}${url.hash}`;
      window.history.replaceState(null, "", nextUrl);
    } catch (_err) {
      // Ignore invalid URL state.
    }
  }

  function syncSelectionAcrossPageLinks(nodes, selectedIds) {
    const value = selectionQueryValue(nodes, selectedIds);
    document.querySelectorAll('a[href^="/dashboard"], a[href^="/trends"], a[href^="/monitor"], a[href^="/sensors"]').forEach(function (anchor) {
      const href = anchor.getAttribute("href");
      if (!href) return;
      try {
        const url = new URL(href, window.location.origin);
        url.searchParams.set(NODE_QUERY_PARAM, value);
        anchor.setAttribute("href", `${url.pathname}${url.search}${url.hash}`);
      } catch (_err) {
        // Ignore malformed links.
      }
    });
  }

  function pickSelectedNodeIds(nodes, requestedIds) {
    if (!nodes.length) return [];
    const available = new Set(nodes.map(function (node) { return node.id; }));
    const availableIds = nodes.map(function (node) { return node.id; });

    const fromUrl = readSelectionFromUrl();
    if (fromUrl.exists) {
      if (fromUrl.kind === NODE_QUERY_LOCAL) return [];
      if (fromUrl.kind === NODE_QUERY_ALL) return availableIds;
      const urlIds = sanitizeNodeIds(fromUrl.nodeIds).filter(function (id) {
        return available.has(id);
      });
      return urlIds.length ? urlIds : availableIds;
    }

    const requested = sanitizeNodeIds(requestedIds).filter(function (id) {
      return available.has(id);
    });
    if (requested.length) {
      return requested;
    }

    const storedSelection = readStoredSelection();
    const stored = sanitizeNodeIds(storedSelection.nodeIds).filter(function (id) {
      return available.has(id);
    });
    if (storedSelection.exists) {
      if (!storedSelection.nodeIds.length) return [];
      if (stored.length) return stored;
    }

    return availableIds;
  }

  async function resolveNodeContext(requestedIds) {
    const nodes = await loadNodes();
    const selectedIds = pickSelectedNodeIds(nodes, requestedIds);
    persistSelectedNodeIds(selectedIds);
    updateSelectionInUrl(nodes, selectedIds);
    syncSelectionAcrossPageLinks(nodes, selectedIds);
    return { nodes, selectedIds };
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

  function dataApiUrl(nodeIds, suffix, localPath, params) {
    const selected = sanitizeNodeIds(nodeIds);
    const extraParams = { ...(params || {}) };
    let base = localPath;

    if (selected.length === 1) {
      base = `/api/nodes/${encodeURIComponent(selected[0])}${suffix}`;
    } else if (selected.length > 1) {
      base = `/api/aggregate${suffix}`;
      extraParams.nodes = selected.join(",");
    }

    const queryParams = Object.keys(extraParams).length ? extraParams : null;
    return withQuery(base, queryParams);
  }

  function sourceEndpointHint(nodeIds, suffix, localPath) {
    const selected = sanitizeNodeIds(nodeIds);
    if (selected.length === 1) return `/api/nodes/{id}${suffix}`;
    if (selected.length > 1) return `/api/aggregate${suffix}`;
    return localPath;
  }

  function dashboardKpisUrl(nodeIds) {
    return dataApiUrl(nodeIds, "/kpis/now", "/api/kpis/now");
  }

  function hourlySeriesUrl(nodeIds) {
    return dataApiUrl(nodeIds, "/series/hourly", "/api/series/hourly");
  }

  function dailySeriesUrl(nodeIds) {
    return dataApiUrl(nodeIds, "/series/daily", "/api/series/daily");
  }

  function observationsLatestUrl(nodeIds, limit) {
    return dataApiUrl(nodeIds, "/observations/latest", "/api/observations/latest", { limit: limit || 20 });
  }

  function detectionsRecentUrl(nodeIds, seconds, limit) {
    return dataApiUrl(nodeIds, "/detections/recent", "/api/detections/recent", {
      seconds: seconds || 300,
      limit: limit || 200,
    });
  }

  function detectionsTopUrl(nodeIds, limit) {
    const now = Date.now();
    const from = now - 24 * 60 * 60 * 1000;
    return dataApiUrl(nodeIds, "/detections/top", "/api/detections/top", {
      from_ms: from,
      to_ms: now,
      limit: limit || 50,
    });
  }

  function detectionsStreamUrl(nodeIds) {
    return dataApiUrl(nodeIds, "/stream/detections", "/api/stream/detections");
  }

  function sensorConfigGetUrl(nodeId) {
    const base = nodeId
      ? `/api/nodes/${encodeURIComponent(nodeId)}/config`
      : "/api/config";
    return base;
  }

  function sensorConfigPutUrl(nodeId) {
    const base = sensorConfigGetUrl(nodeId);
    // Only the HUB proxy PUT requires token. The node-local /api/config does not.
    if (!base.startsWith("/api/nodes/")) return base;
    const token = getAdminToken();
    if (!token) return base;
    return `${base}?token=${encodeURIComponent(token)}`;
  }

  function raspberryBootstrapUrl() {
    const token = getAdminToken();
    const base = "/api/admin/raspberries/bootstrap";
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

  function sourceHintText(nodes, selectedIds) {
    const selected = sanitizeNodeIds(selectedIds);
    if (!selected.length) {
      return nodes.length ? "Selecciona nodos (o usa hub local)" : "Modo standalone (hub local)";
    }
    if (nodes.length && selected.length === nodes.length) {
      return `Fuente: Todos (${nodes.length} nodos)`;
    }
    const byId = new Map(nodes.map(function (node) {
      return [node.id, node];
    }));
    if (selected.length === 1) {
      const node = byId.get(selected[0]);
      if (!node) return "Fuente: Nodo seleccionado";
      return `Fuente: ${node.name} (${node.host || "host n/a"})`;
    }
    const names = selected.map(function (id) {
      const node = byId.get(id);
      return node ? node.name : id;
    });
    return `Fuente: ${names.join(" + ")}`;
  }

  async function hydrateDashboard(nodeIds) {
    const [kpis, hourlySeries, latest, recentDetections] = await Promise.all([
      fetchJson(dashboardKpisUrl(nodeIds)),
      fetchJson(hourlySeriesUrl(nodeIds)),
      fetchJson(observationsLatestUrl(nodeIds, 5)),
      fetchJson(detectionsRecentUrl(nodeIds, 300, 10)),
    ]);

    const obs = kpis && kpis.observation ? kpis.observation : null;
    const selected = sanitizeNodeIds(nodeIds);
    const isMulti = selected.length > 1;
    setText("kpi-active-devices", obs && Number.isFinite(obs.unique_devices_count) ? String(obs.unique_devices_count) : "--");
    setText("kpi-capacity", kpis && Number.isFinite(kpis.capacity) ? String(kpis.capacity) : "60");
    setText("kpi-load", obs && Number.isFinite(obs.load_pct) ? `${obs.load_pct}%` : "--");
    setText("kpi-raw", obs && Number.isFinite(obs.raw_count) ? String(obs.raw_count) : "--");
    setText("kpi-sum-raw", obs && Number.isFinite(obs.raw_count) ? String(obs.raw_count) : "--");
    setText("kpi-sum-capacity", kpis && Number.isFinite(kpis.capacity) ? String(kpis.capacity) : "--");
    setText("kpi-ts", obs ? fmtTs(obs.ts_iso || obs.ts_ms) : "--");
    setText("kpi-sensor-status", kpis && kpis.status ? String(kpis.status).toUpperCase() : "UNKNOWN");
    setText("kpi-active-label", isMulti ? "Total Devices" : "Active Devices");
    setText("dashboard-latest-unique-label", isMulti ? "Total" : "Unique");
    setText("kpi-hourly-title", isMulti ? "Traffic Sum (Hourly Peak)" : "Live Traffic (Hourly Peak)");
    setText("kpi-sum-raw-label", isMulti ? "Σ Raw" : "Raw");
    setText("kpi-sum-capacity-label", isMulti ? "Σ Capacity" : "Capacity");
    setText("kpi-capacity-unit-label", isMulti ? "Σ CAP" : "CAP");
    setText("kpi-trend-title", isMulti ? "60m Trend (Total)" : "60m Trend");
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
        ? `Last 5 minutes via ${sourceEndpointHint(nodeIds, "/detections/recent", "/api/detections/recent")}`
        : "Detection API not available yet"
    );
  }

  async function hydrateTrends(nodeIds) {
    const isMulti = sanitizeNodeIds(nodeIds).length > 1;
    const [daily, hourly, topDetections] = await Promise.all([
      fetchJson(dailySeriesUrl(nodeIds)),
      fetchJson(hourlySeriesUrl(nodeIds)),
      fetchJson(detectionsTopUrl(nodeIds, 20)),
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
        ? `Top devices over last 24h via ${sourceEndpointHint(nodeIds, "/detections/top", "/api/detections/top")}`
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
            label: isMulti ? "Daily Peak Total" : "Daily Peak Unique",
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
      note.textContent = `Daily peak chart rendered from ${sourceEndpointHint(nodeIds, "/series/daily", "/api/series/daily")}.`;
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

  async function hydrateMonitor(nodes, selectedIds) {
    const streamState = document.getElementById("monitor-stream-state");
    const deviceInput = document.getElementById("monitor-filter-device");
    const rssiInput = document.getElementById("monitor-filter-rssi");
    const transportInput = document.getElementById("monitor-filter-transport");
    const refreshBtn = document.getElementById("monitor-refresh-btn");
    const resultCount = document.getElementById("monitor-result-count");
    const sourceHint = document.getElementById("monitor-source-hint");
    const nodeIds = sanitizeNodeIds(selectedIds);

    if (sourceHint) {
      sourceHint.textContent = sourceHintText(nodes, nodeIds);
    }

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
      const payload = await fetchJson(detectionsRecentUrl(nodeIds, 300, 200));
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
      const source = new window.EventSource(detectionsStreamUrl(nodeIds));
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

    const bootstrapForm = document.getElementById("raspberry-bootstrap-form");
    const bootstrapBtn = document.getElementById("raspberry-bootstrap-btn");
    const bootstrapStatus = document.getElementById("raspberry-bootstrap-status");
    const bootstrapResult = document.getElementById("raspberry-bootstrap-result");
    const shellCommandNode = document.getElementById("raspberry-shell-command");
    const downloadLink = document.getElementById("raspberry-download-link");
    const adminTokenInput = document.getElementById("raspberry_admin_token");

    function setBootstrapStatus(text, isError) {
      if (!bootstrapStatus) return;
      bootstrapStatus.textContent = text;
      bootstrapStatus.classList.toggle("text-accent-red", Boolean(isError));
      bootstrapStatus.classList.toggle("text-text-muted", !isError);
    }

    if (bootstrapForm) {
      if (adminTokenInput && !adminTokenInput.value) {
        adminTokenInput.value = getAdminToken();
      }

      bootstrapForm.addEventListener("submit", async function (event) {
        event.preventDefault();
        if (bootstrapBtn) bootstrapBtn.setAttribute("disabled", "disabled");

        const tokenValue = adminTokenInput ? String(adminTokenInput.value || "").trim() : "";
        setAdminToken(tokenValue);

        const payload = {
          id: (document.getElementById("raspberry_node_id") || {}).value || "",
          name: (document.getElementById("raspberry_name") || {}).value || "",
          host: (document.getElementById("raspberry_host") || {}).value || "",
          bootstrap_user: (document.getElementById("raspberry_bootstrap_user") || {}).value || "",
          ssh_port: toNumber((document.getElementById("raspberry_ssh_port") || {}).value),
          http_port: toNumber((document.getElementById("raspberry_http_port") || {}).value),
          node_user: "rpipulse",
          enabled: true,
        };

        setBootstrapStatus("Generating sync executable ...", false);
        const response = await postJson(raspberryBootstrapUrl(), payload);
        if (bootstrapBtn) bootstrapBtn.removeAttribute("disabled");

        if (!response.ok || !response.data) {
          setBootstrapStatus(`Could not generate sync executable (status ${response.status || "n/a"}).`, true);
          showToast("Error generando ejecutable", "error");
          return;
        }

        const bootstrap = response.data.bootstrap || {};
        if (shellCommandNode) shellCommandNode.textContent = bootstrap.shell_command || "--";
        if (downloadLink) {
          downloadLink.href = bootstrap.short_url ? `${bootstrap.short_url}?download=1` : "#";
          downloadLink.setAttribute("download", bootstrap.script_filename || "bootstrap.sh");
        }
        if (bootstrapResult) bootstrapResult.classList.remove("hidden");
        nodesCachePromise = null;
        setBootstrapStatus("Raspberry registrada y enlace corto generado.", false);
        showToast("Raspberry añadida", "success");
      });
    }

    await loadConfig();
  }

  async function initDashboard() {
    const selector = document.getElementById("dashboard-node-select");
    const hint = document.getElementById("dashboard-node-hint");

    function selectedIdsFromSelector(nodes) {
      if (!selector) return [];
      const selectedValues = Array.from(selector.selectedOptions).map(function (option) {
        return option.value || "";
      });
      if (selectedValues.includes("")) return [];
      if (selectedValues.includes(ALL_NODES_OPTION_VALUE)) {
        return nodes.map(function (node) { return node.id; });
      }
      const valid = new Set(nodes.map(function (node) { return node.id; }));
      return sanitizeNodeIds(selectedValues).filter(function (id) {
        return valid.has(id);
      });
    }

    function syncSelectorSelection(nodes, selectedIds) {
      if (!selector) return;
      const selected = sanitizeNodeIds(selectedIds);
      const selectedSet = new Set(selected);
      const allSelected = Boolean(nodes.length) && selected.length === nodes.length;
      Array.from(selector.options).forEach(function (option) {
        if (option.value === "") {
          option.selected = selected.length === 0;
          return;
        }
        if (option.value === ALL_NODES_OPTION_VALUE) {
          option.selected = allSelected;
          return;
        }
        option.selected = selectedSet.has(option.value);
      });
    }

    function updateHint(nodes, selectedIds) {
      if (!hint) return;
      hint.textContent = sourceHintText(nodes, selectedIds);
    }

    const context = await resolveNodeContext();
    const nodes = context.nodes;
    const initialNodeIds = sanitizeNodeIds(context.selectedIds);

    if (!selector) {
      updateHint(nodes, initialNodeIds);
      await hydrateDashboard(initialNodeIds);
      return;
    }

    selector.innerHTML = "";
    const localOption = document.createElement("option");
    localOption.value = "";
    localOption.textContent = "Hub local";
    selector.appendChild(localOption);
    const allOption = document.createElement("option");
    allOption.value = ALL_NODES_OPTION_VALUE;
    allOption.textContent = "Todos";
    selector.appendChild(allOption);

    nodes.forEach(function (node) {
      const option = document.createElement("option");
      option.value = node.id;
      option.textContent = node.name;
      selector.appendChild(option);
    });

    selector.multiple = true;
    selector.size = Math.max(4, Math.min(nodes.length + 2, 8));
    syncSelectorSelection(nodes, initialNodeIds);

    updateHint(nodes, initialNodeIds);
    await hydrateDashboard(initialNodeIds);

    selector.addEventListener("change", function () {
      const selectedIds = selectedIdsFromSelector(nodes);
      persistSelectedNodeIds(selectedIds);
      updateSelectionInUrl(nodes, selectedIds);
      syncSelectionAcrossPageLinks(nodes, selectedIds);
      syncSelectorSelection(nodes, selectedIds);
      updateHint(nodes, selectedIds);
      hydrateDashboard(selectedIds);
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
      setText("trends-source-hint", sourceHintText(context.nodes, context.selectedIds));
      await hydrateTrends(context.selectedIds);
      return;
    }

    if (page === "monitor") {
      const context = await resolveNodeContext();
      await hydrateMonitor(context.nodes, context.selectedIds);
      return;
    }

    if (page === "sensors") {
      const context = await resolveNodeContext();
      const selectedIds = sanitizeNodeIds(context.selectedIds);
      const configNodeId = selectedIds.length === 1 ? selectedIds[0] : null;
      await hydrateSensors(configNodeId);
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
