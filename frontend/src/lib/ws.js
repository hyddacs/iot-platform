export function buildWebSocketUrl(path = "/api/ws") {
  const target = new URL(path, window.location.origin);
  target.protocol = target.protocol === "https:" ? "wss:" : "ws:";
  return target.toString();
}

export function formatWebSocketStatus(status) {
  const labels = {
    idle: "待连接",
    connecting: "连接中",
    connected: "在线",
    reconnecting: "重连中",
    error: "连接异常",
    closed: "已关闭"
  };

  return labels[status] || "未知";
}

export function createManagedSocket({ buildUrl, onMessage, onStatus, onOpen }) {
  let socket = null;
  let heartbeat = null;
  let reconnectTimer = null;
  let stopped = true;
  let retries = 0;

  function setStatus(status) {
    if (onStatus) {
      onStatus(status);
    }
  }

  function clearTimers() {
    window.clearInterval(heartbeat);
    window.clearTimeout(reconnectTimer);
    heartbeat = null;
    reconnectTimer = null;
  }

  function scheduleReconnect() {
    if (stopped) {
      return;
    }

    retries += 1;
    const delay = Math.min(30000, 1000 * 2 ** Math.min(retries, 5));
    setStatus("reconnecting");
    reconnectTimer = window.setTimeout(connect, delay);
  }

  function connect() {
    clearTimers();
    stopped = false;

    if (socket) {
      socket.close();
    }

    socket = new WebSocket(buildUrl());
    setStatus("connecting");

    socket.onopen = () => {
      retries = 0;
      setStatus("connected");
      if (onOpen) {
        onOpen();
      }
      heartbeat = window.setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ type: "ping" }));
        }
      }, 15000);
    };

    socket.onmessage = (event) => onMessage(event);

    socket.onerror = () => {
      setStatus("error");
    };

    socket.onclose = () => {
      clearTimers();
      if (stopped) {
        setStatus("closed");
      } else {
        scheduleReconnect();
      }
    };
  }

  function disconnect() {
    stopped = true;
    clearTimers();
    if (socket) {
      socket.close();
      socket = null;
    } else {
      setStatus("closed");
    }
  }

  return { connect, disconnect };
}
