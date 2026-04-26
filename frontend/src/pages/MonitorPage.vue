<script setup>
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { apiFetch, formatDateTime, listItems, toMessage } from "../lib/api";
import { buildWebSocketUrl, createManagedSocket, formatWebSocketStatus } from "../lib/ws";

const EVENT_ORDER = ["device_data", "device_status", "alert", "connected", "pong", "system", "raw"];
const EVENT_LABELS = {
  device_data: "数据上报",
  device_status: "状态变化",
  alert: "阈值告警",
  connected: "连接应答",
  pong: "心跳回包",
  system: "系统事件",
  raw: "原始消息"
};
const EVENT_COLORS = {
  device_data: "#2f7fe5",
  device_status: "#2b9d62",
  alert: "#d96d38",
  connected: "#5d7be0",
  pong: "#7b8aa4",
  system: "#46566f",
  raw: "#8a6db0"
};

const devices = ref([]);
const selectedDeviceId = ref("");
const wsStatus = ref("idle");
const wsStatusLabel = computed(() => formatWebSocketStatus(wsStatus.value));
const logs = ref([]);
const wsClient = ref(null);
const poller = ref(null);
const error = ref("");
const monitorTab = ref("overview");

const realtimeCount = computed(() => logs.value.length);

const eventMetrics = computed(() =>
  EVENT_ORDER.map((type) => {
    const count = logs.value.filter((entry) => entry.type === type).length;
    return {
      type,
      label: EVENT_LABELS[type],
      count,
      color: EVENT_COLORS[type]
    };
  }).filter((item) => item.count > 0 || item.type === "device_data" || item.type === "alert")
);

const eventTotal = computed(() =>
  eventMetrics.value.reduce((total, item) => total + item.count, 0)
);

const dominantEventCount = computed(() =>
  Math.max(...eventMetrics.value.map((item) => item.count), 1)
);

const activeDeviceMetrics = computed(() => {
  const counts = new Map();

  for (const entry of logs.value) {
    const deviceId =
      entry.payload?.device_id ||
      entry.payload?.alert?.device_id ||
      entry.payload?.message?.device_id ||
      "";

    if (!deviceId) {
      continue;
    }

    counts.set(deviceId, (counts.get(deviceId) || 0) + 1);
  }

  return Array.from(counts.entries())
    .sort((left, right) => right[1] - left[1])
    .slice(0, 5)
    .map(([deviceId, count]) => {
      const device = devices.value.find((item) => item.device_id === deviceId);
      return {
        deviceId,
        count,
        deviceName: device?.device_name || deviceId,
        onlineStatus: device?.online_status || false
      };
    });
});

const gatewayRows = computed(() =>
  Object.entries(poller.value?.poller?.gateways || {}).map(([gatewayIp, status]) => ({
    gatewayIp,
    ...status
  }))
);

watch(selectedDeviceId, () => {
  connectSocket();
});

async function loadMetadata() {
  try {
    const [nextDevices, nextPoller] = await Promise.all([
      apiFetch("/api/devices"),
      apiFetch("/api/monitor/poller")
    ]);

    devices.value = listItems(nextDevices);
    poller.value = nextPoller;

    if (!selectedDeviceId.value && devices.value.length > 0) {
      selectedDeviceId.value = devices.value[0].device_id;
    }
  } catch (nextError) {
    error.value = toMessage(nextError);
  }
}

function connectSocket() {
  disconnectSocket();

  const query = selectedDeviceId.value
    ? `?device_id=${encodeURIComponent(selectedDeviceId.value)}`
    : "";
  wsClient.value = createManagedSocket({
    buildUrl: () => buildWebSocketUrl(`/api/ws${query}`),
    onStatus: (status) => {
      wsStatus.value = status;
    },
    onOpen: () => {
      pushLog("system", { message: "WebSocket 已连接", device_id: selectedDeviceId.value || "all" });
    },
    onMessage: (event) => {
      try {
        const payload = JSON.parse(event.data);
        pushLog(payload.type || "message", payload);
      } catch {
        pushLog("raw", { raw: event.data });
      }
    }
  });
  wsClient.value.connect();
}

function disconnectSocket() {
  if (wsClient.value) {
    wsClient.value.disconnect();
    wsClient.value = null;
  }
}

function pushLog(type, payload) {
  logs.value = [
    {
      id: `${Date.now()}-${Math.random().toString(16).slice(2)}`,
      type,
      payload,
      createdAt: new Date().toISOString()
    },
    ...logs.value
  ].slice(0, 60);
}

function clearLogs() {
  logs.value = [];
}

onMounted(async () => {
  await loadMetadata();
  connectSocket();
});

onBeforeUnmount(() => {
  disconnectSocket();
});
</script>

<template>
  <section class="page-section">
    <div class="page-header">
      <div>
        <p class="eyebrow">实时监控</p>
        <h2>工业消息监控台</h2>
      </div>

      <div class="toolbar-actions">
        <button class="ghost-btn" type="button" @click="loadMetadata">刷新元数据</button>
        <button class="ghost-btn" type="button" @click="clearLogs">清空日志</button>
      </div>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>

    <section class="device-summary-strip dashboard-summary-strip">
      <article class="summary-metric">
        <span>连接状态</span>
        <strong>{{ wsStatusLabel }}</strong>
      </article>
      <article class="summary-metric">
        <span>实时事件</span>
        <strong>{{ realtimeCount }}</strong>
      </article>
      <article class="summary-metric">
        <span>监听对象</span>
        <strong>{{ selectedDeviceId || "全部广播" }}</strong>
      </article>
      <article class="summary-metric">
        <span>轮询器</span>
        <strong>{{ poller?.poller?.status || "unknown" }}</strong>
      </article>
      <article class="summary-metric">
        <span>CPU</span>
        <strong>{{ poller?.system?.cpu_percent ?? "-" }}%</strong>
      </article>
      <article class="summary-metric">
        <span>内存</span>
        <strong>{{ poller?.system?.memory_percent ?? "-" }}%</strong>
      </article>
    </section>

    <div class="device-board-tabs detail-tabs">
      <button class="device-tab" :class="{ active: monitorTab === 'overview' }" type="button" @click="monitorTab = 'overview'">运行概览</button>
      <button class="device-tab" :class="{ active: monitorTab === 'events' }" type="button" @click="monitorTab = 'events'">事件分析</button>
      <button class="device-tab" :class="{ active: monitorTab === 'gateways' }" type="button" @click="monitorTab = 'gateways'">网关健康</button>
    </div>

    <section v-if="monitorTab === 'overview'" class="content-grid dashboard-command-grid">
      <article class="panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">连接控制</p>
            <h2>订阅范围</h2>
          </div>
          <span class="status-pill" :data-kind="wsStatus === 'connected' ? 'ok' : 'idle'">
            {{ wsStatusLabel }}
          </span>
        </div>

        <div class="form-grid single-column">
          <label>
            <span>监听设备</span>
            <select v-model="selectedDeviceId">
              <option value="">全部广播</option>
              <option v-for="device in devices" :key="device.device_id" :value="device.device_id">
                {{ device.device_name }} · {{ device.device_id }}
              </option>
            </select>
          </label>
        </div>
      </article>

      <article class="panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">活跃设备</p>
            <h2>消息热点排行</h2>
          </div>
        </div>

        <div class="industrial-status-board compact-status-board">
          <div v-for="item in activeDeviceMetrics" :key="item.deviceId" class="industrial-status-row compact-status-row">
            <div class="industrial-status-main">
              <strong>{{ item.deviceName }}</strong>
              <p>{{ item.deviceId }}</p>
            </div>
            <div class="industrial-status-badges">
              <span class="chip" :data-online="item.onlineStatus">
                {{ item.onlineStatus ? "在线" : "离线" }}
              </span>
              <span class="small-muted">{{ item.count }} 条</span>
            </div>
          </div>
          <div v-if="!activeDeviceMetrics.length" class="empty-state">等待实时事件到来。</div>
        </div>
      </article>
    </section>

    <section v-else-if="monitorTab === 'events'" class="content-grid dashboard-command-grid">
      <article class="panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">事件总览</p>
            <h2>事件计数结构</h2>
          </div>
          <span class="muted-text">总计 {{ eventTotal }} 条</span>
        </div>

        <div class="event-radar">
          <div class="event-ring">
            <strong>{{ eventTotal }}</strong>
            <span>事件</span>
          </div>
          <div class="event-type-grid">
            <article v-for="item in eventMetrics" :key="item.type" class="event-type-card">
              <span class="legend-dot" :style="{ background: item.color }" />
              <strong>{{ item.count }}</strong>
              <p>{{ item.label }}</p>
            </article>
          </div>
        </div>
      </article>

      <article class="panel industrial-map-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">事件结构</p>
            <h2>事件计数条图</h2>
          </div>
        </div>

        <div class="event-bar-list event-bar-list-compact">
          <div v-for="item in eventMetrics" :key="item.type" class="event-bar-item">
            <div class="event-bar-meta">
              <strong>{{ item.label }}</strong>
              <span>{{ item.count }} 条 · {{ eventTotal ? Math.round((item.count / eventTotal) * 100) : 0 }}%</span>
            </div>
            <div class="bar-metric-track">
              <div
                class="bar-metric-fill"
                :style="{
                  width: `${Math.max(4, Math.round((item.count / dominantEventCount) * 100))}%`,
                  background: item.color
                }"
              />
            </div>
          </div>
        </div>
      </article>
    </section>

    <section v-else class="content-grid dashboard-command-grid">
      <article class="panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">采集链路</p>
            <h2>网关健康状态</h2>
          </div>
          <span class="muted-text">{{ poller?.poller?.last_cycle_at ? formatDateTime(poller.poller.last_cycle_at) : "暂无周期" }}</span>
        </div>

        <div class="industrial-status-board compact-status-board">
          <div v-for="gateway in gatewayRows" :key="gateway.gatewayIp" class="industrial-status-row compact-status-row">
            <div class="industrial-status-main">
              <strong>{{ gateway.gatewayIp }}</strong>
              <p>{{ gateway.last_error || "连接正常" }}</p>
            </div>
            <span class="chip" :data-online="gateway.connected">
              {{ gateway.connected ? "正常" : "异常" }}
            </span>
          </div>
          <div v-if="!gatewayRows.length" class="empty-state">暂无 Modbus 网关状态。</div>
        </div>
      </article>
    </section>
  </section>
</template>
