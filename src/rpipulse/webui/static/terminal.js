(function () {
  "use strict";

  // State
  let terminal = null;
  let fitAddon = null;
  let ws = null;
  let currentNode = null;
  let isConnected = false;
  let reconnectAttempts = 0;
  const MAX_RECONNECT = 3;

  // DOM Elements
  const nodeSelectorContainer = document.getElementById("node-selector-container");
  const btnRefreshNodes = document.getElementById("btn-refresh-nodes");
  const btnOpenTerminal = document.getElementById("btn-open-terminal");
  const btnCloseTerminal = document.getElementById("btn-close-terminal");
  const btnClearTerminal = document.getElementById("btn-clear-terminal");
  const connectionStatus = document.getElementById("connection-status");
  const currentNodeEl = document.getElementById("current-node");
  const currentIpEl = document.getElementById("current-ip");
  const terminalContainer = document.getElementById("terminal-container");
  const terminalPlaceholder = document.getElementById("terminal-placeholder");
  const terminalTitle = document.getElementById("terminal-title");

  // Modal elements
  const tokenModal = document.getElementById("token-modal");
  const tokenInput = document.getElementById("admin-token-input");
  const btnCancelToken = document.getElementById("btn-cancel-token");
  const btnConfirmToken = document.getElementById("btn-confirm-token");

  // Token management
  function getStoredToken() {
    return sessionStorage.getItem("rpipulse_admin_token");
  }

  function setStoredToken(token) {
    sessionStorage.setItem("rpipulse_admin_token", token);
  }

  // Node management
  async function loadNodes() {
    try {
      const response = await fetch("/api/nodes");
      if (!response.ok) throw new Error("Failed to load nodes");
      const data = await response.json();
      renderNodes(data.nodes || []);
    } catch (err) {
      console.error("Error loading nodes:", err);
      nodeSelectorContainer.innerHTML = '<div class="text-sm text-accent-red py-2">Error loading nodes</div>';
    }
  }

  function renderNodes(nodes) {
    if (!nodes || nodes.length === 0) {
      nodeSelectorContainer.innerHTML = '<div class="text-sm text-text-muted py-2">No nodes configured</div>';
      return;
    }

    nodeSelectorContainer.innerHTML = nodes
      .map(
        (node) => `
        <button 
          class="node-select-btn w-full text-left px-4 py-3 rounded-md transition-all duration-200 border ${
            currentNode && currentNode.id === node.id
              ? "bg-primary/20 border-primary text-primary"
              : "bg-surface-highlight/50 border-transparent hover:bg-surface-highlight text-text-main hover:border-surface-highlight"
          }"
          data-node-id="${node.id}"
          data-node-name="${node.name}"
          data-node-ip="${node.ip}"
        >
          <div class="flex items-center justify-between">
            <span class="font-medium">${node.name}</span>
            <span class="text-xs px-2 py-0.5 rounded-full ${
              currentNode && currentNode.id === node.id ? "bg-primary text-bg-dark" : "bg-surface text-text-muted"
            }">${node.ip}</span>
          </div>
          <div class="text-xs text-text-muted mt-1 font-mono">${node.hostname || node.id}</div>
        </button>
      `
      )
      .join("");

    // Add click handlers
    document.querySelectorAll(".node-select-btn").forEach((btn) => {
      btn.addEventListener("click", () => selectNode(btn.dataset));
    });
  }

  function selectNode(nodeData) {
    currentNode = {
      id: nodeData.nodeId,
      name: nodeData.nodeName,
      ip: nodeData.nodeIp,
    };

    // Update UI
    document.querySelectorAll(".node-select-btn").forEach((btn) => {
      btn.classList.remove("bg-primary/20", "border-primary", "text-primary");
      btn.classList.add("bg-surface-highlight/50", "border-transparent", "hover:bg-surface-highlight", "text-text-main", "hover:border-surface-highlight");
      if (btn.dataset.nodeId === currentNode.id) {
        btn.classList.add("bg-primary/20", "border-primary", "text-primary");
        btn.classList.remove("bg-surface-highlight/50", "border-transparent", "hover:bg-surface-highlight", "hover:border-surface-highlight");
      }
    });

    currentNodeEl.textContent = currentNode.name;
    currentIpEl.textContent = currentNode.ip;
    btnOpenTerminal.disabled = false;

    // Update connection status
    if (isConnected) {
      updateConnectionStatus("connected");
    }
  }

  function updateConnectionStatus(status) {
    connectionStatus.classList.remove("status-connected", "status-disconnected", "status-connecting");

    switch (status) {
      case "connected":
        connectionStatus.textContent = "CONNECTED";
        connectionStatus.classList.add("status-connected");
        btnCloseTerminal.disabled = false;
        btnOpenTerminal.disabled = true;
        reconnectAttempts = 0;
        break;
      case "disconnected":
        connectionStatus.textContent = "DISCONNECTED";
        connectionStatus.classList.add("status-disconnected");
        btnCloseTerminal.disabled = true;
        btnOpenTerminal.disabled = !currentNode;
        break;
      case "connecting":
        connectionStatus.textContent = "CONNECTING...";
        connectionStatus.classList.add("status-connecting");
        btnOpenTerminal.disabled = true;
        btnCloseTerminal.disabled = true;
        break;
    }
  }

  // Terminal initialization
  function initTerminal() {
    // Clear placeholder
    terminalPlaceholder.style.display = "none";

    // Create terminal if not exists
    if (!terminal) {
      terminal = new window.Terminal({
        cursorBlink: true,
        fontSize: 14,
        fontFamily: '"JetBrains Mono", "Fira Code", monospace',
        theme: {
          background: "#0d0d0d",
          foreground: "#e0e0e0",
          cursor: "#D69E1C",
          cursorAccent: "#0d0d0d",
          selectionBackground: "rgba(214, 158, 28, 0.3)",
          black: "#000000",
          red: "#C0392B",
          green: "#27AE60",
          yellow: "#D69E1C",
          blue: "#2980B9",
          magenta: "#8E44AD",
          cyan: "#1ABC9C",
          white: "#E0E0E0",
          brightBlack: "#7F8C8D",
          brightRed: "#E74C3C",
          brightGreen: "#2ECC71",
          brightYellow: "#F1C40F",
          brightBlue: "#3498DB",
          brightMagenta: "#9B59B6",
          brightCyan: "#16A085",
          brightWhite: "#FFFFFF",
        },
        allowTransparency: true,
        scrollback: 10000,
      });

      fitAddon = new window.FitAddon.FitAddon();
      terminal.loadAddon(fitAddon);
      terminal.open(terminalContainer);
      fitAddon.fit();

      // Handle terminal input
      terminal.onData((data) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "input", data: data }));
        }
      });

      // Handle resize
      terminal.onResize((size) => {
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: "resize", cols: size.cols, rows: size.rows }));
        }
      });

      // Welcome message
      terminal.writeln("\x1b[1;34m╔════════════════════════════════════════════════════════╗\x1b[0m");
      terminal.writeln("\x1b[1;34m║\x1b[0m   \x1b[1;33mRpiPulse Terminal\x1b[0m                                      \x1b[1;34m║\x1b[0m");
      terminal.writeln("\x1b[1;34m║\x1b[0m   Remote shell connection                              \x1b[1;34m║\x1b[0m");
      terminal.writeln("\x1b[1;34m╚════════════════════════════════════════════════════════╝\x1b[0m");
      terminal.writeln("");
      terminal.writeln("\x1b[32m✓\x1b[0m Terminal initialized. Waiting for connection...");
      terminal.writeln("");
    } else {
      terminal.focus();
    }

    // Handle window resize
    window.addEventListener("resize", () => {
      if (fitAddon && terminal) {
        fitAddon.fit();
      }
    });
  }

  function clearTerminal() {
    if (terminal) {
      terminal.clear();
    }
  }

  function disposeTerminal() {
    if (terminal) {
      terminal.dispose();
      terminal = null;
      fitAddon = null;
    }
    if (terminalPlaceholder) {
      terminalPlaceholder.style.display = "flex";
    }
  }

  // WebSocket connection
  function connectWebSocket() {
    if (!currentNode) return;

    const token = getStoredToken();
    if (!token) {
      // Show token modal
      tokenModal.classList.remove("hidden");
      tokenInput.focus();
      return;
    }

    establishConnection(token);
  }

  function establishConnection(token) {
    updateConnectionStatus("connecting");

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/terminal/${currentNode.id}?token=${encodeURIComponent(token)}`;

    try {
      ws = new WebSocket(wsUrl);

      ws.onopen = () => {
        console.log("WebSocket connected");
        isConnected = true;
        updateConnectionStatus("connected");

        // Update terminal title
        terminalTitle.textContent = `${currentNode.name} (${currentNode.ip})`;

        // Send initial resize
        if (terminal && fitAddon) {
          const size = fitAddon.proposeDimensions();
          if (size) {
            ws.send(JSON.stringify({ type: "resize", cols: size.cols, rows: size.rows }));
          }
        }
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "output" && terminal) {
            terminal.write(msg.data);
          } else if (msg.type === "error") {
            terminal.writeln(`\x1b[31mError: ${msg.message}\x1b[0m`);
          } else if (msg.type === "close") {
            disconnectTerminal();
            terminal.writeln("\x1b[33mConnection closed by remote host.\x1b[0m");
          }
        } catch (err) {
          // Plain text fallback
          if (terminal) {
            terminal.write(event.data);
          }
        }
      };

      ws.onerror = (error) => {
        console.error("WebSocket error:", error);
        terminal.writeln("\x1b[31mConnection error occurred.\x1b[0m");
      };

      ws.onclose = (event) => {
        console.log("WebSocket closed:", event.code, event.reason);
        isConnected = false;
        updateConnectionStatus("disconnected");

        // Auto-reconnect logic (optional)
        if (event.code !== 1000 && reconnectAttempts < MAX_RECONNECT) {
          reconnectAttempts++;
          terminal.writeln(`\x1b[33mReconnecting... (attempt ${reconnectAttempts}/${MAX_RECONNECT})\x1b[0m`);
          setTimeout(() => connectWebSocket(), 2000);
        }
      };
    } catch (err) {
      console.error("Failed to create WebSocket:", err);
      updateConnectionStatus("disconnected");
    }
  }

  function disconnectTerminal() {
    if (ws) {
      ws.close(1000, "User disconnected");
      ws = null;
    }
    isConnected = false;
    updateConnectionStatus("disconnected");
    terminalTitle.textContent = "No terminal connected";
  }

  // Modal handlers
  function showTokenModal() {
    tokenModal.classList.remove("hidden");
    tokenInput.value = "";
    tokenInput.focus();
  }

  function hideTokenModal() {
    tokenModal.classList.add("hidden");
    tokenInput.value = "";
  }

  function handleTokenConfirm() {
    const token = tokenInput.value.trim();
    if (token) {
      setStoredToken(token);
      hideTokenModal();
      establishConnection(token);
    }
  }

  // Event listeners
  if (btnRefreshNodes) {
    btnRefreshNodes.addEventListener("click", loadNodes);
  }

  if (btnOpenTerminal) {
    btnOpenTerminal.addEventListener("click", () => {
      if (!currentNode) return;
      initTerminal();
      connectWebSocket();
    });
  }

  if (btnCloseTerminal) {
    btnCloseTerminal.addEventListener("click", disconnectTerminal);
  }

  if (btnClearTerminal) {
    btnClearTerminal.addEventListener("click", () => {
      if (terminal) {
        clearTerminal();
        btnClearTerminal.disabled = false;
      }
    });
  }

  if (btnCancelToken) {
    btnCancelToken.addEventListener("click", hideTokenModal);
  }

  if (btnConfirmToken) {
    btnConfirmToken.addEventListener("click", handleTokenConfirm);
  }

  if (tokenInput) {
    tokenInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        handleTokenConfirm();
      } else if (e.key === "Escape") {
        hideTokenModal();
      }
    });
  }

  // Click outside modal to close
  if (tokenModal) {
    tokenModal.addEventListener("click", (e) => {
      if (e.target === tokenModal) {
        hideTokenModal();
      }
    });
  }

  // Initialize
  async function init() {
    await loadNodes();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
