<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from "vue";

import { apiFetch, formatDateTime, toMessage } from "../lib/api";
import { buildWebSocketUrl, createManagedSocket } from "../lib/ws";

const DASHBOARD_REFRESH_MIN_INTERVAL = 1000;
const METRIC_RECORD_PAGE_SIZE = 5;

const loading = ref(false);
const error = ref("");
const stats = ref(null);
const alerts = ref([]);
const wsClient = ref(null);
const refreshTimer = ref(null);
const lastRealtimeRefreshAt = ref(0);
const selectedDashboardMetric = ref(null);
const selectedMetricRecordPage = ref(1);
const metricDetailRecords = ref([]);
const metricDetailTotal = ref(0);
const metricDetailLoading = ref(false);
const metricDetailError = ref("");

const onlineRate = computed(() => {
  if (!stats.value?.total_devices) {
    return 0;
  }

  return Math.round((stats.value.online_devices / stats.value.total_devices) * 100);
});

const protocolMetrics = computed(() => {
  const total = Math.max(stats.value?.total_devices || 0, 1);
  const httpDevices = stats.value?.http_devices || 0;
  const modbusDevices = stats.value?.modbus_devices || 0;

  return [
    {
      key: "http",
      protocolType: "http_active",
      label: "HTTP 主动上报",
      value: httpDevices,
      ratio: Math.round((httpDevices / total) * 100),
      color: "#2f7fe5"
    },
    {
      key: "modbus",
      protocolType: "modbus_gateway",
      label: "Modbus 轮询",
      value: modbusDevices,
      ratio: Math.round((modbusDevices / total) * 100),
      color: "#2b9d62"
    }
  ].sort((left, right) => right.value - left.value);
});

const statusMetrics = computed(() => {
  const total = Math.max(stats.value?.total_devices || 0, 1);
  const online = stats.value?.online_devices || 0;
  const offline = stats.value?.offline_devices || 0;

  return [
    {
      key: "online",
      label: "在线设备",
      value: online,
      ratio: Math.round((online / total) * 100),
      color: "#2b9d62"
    },
    {
      key: "offline",
      label: "离线设备",
      value: offline,
      ratio: Math.round((offline / total) * 100),
      color: "#c87a34"
    }
  ].sort((left, right) => right.value - left.value);
});

const unresolvedAlerts = computed(() => alerts.value.filter((item) => !item.is_resolved));
const alertDeviceCount = computed(() => stats.value?.alert_devices_unresolved ?? 0);
const upperAlertCount = computed(() => stats.value?.upper_alerts_unresolved ?? 0);
const lowerAlertCount = computed(() => stats.value?.lower_alerts_unresolved ?? 0);
const unresolvedAlertTotal = computed(() => stats.value?.total_alerts_unresolved ?? unresolvedAlerts.value.length);

const recentAlerts = computed(() => unresolvedAlerts.value.slice(0, 5));
const latestAlert = computed(() => recentAlerts.value[0] || null);
const alertFieldCount = computed(() => stats.value?.alert_fields_unresolved ?? 0);
const latestAlertTime = computed(() =>
  formatDateTime(
    latestAlert.value?.last_triggered_at ||
      latestAlert.value?.first_triggered_at ||
      latestAlert.value?.created_at
  )
);

function getAlertDeviceName(alert) {
  if (!alert) {
    return "暂无";
  }

  return alert.device_name || alert.device_id;
}

function getAlertSensorName(alert) {
  if (!alert) {
    return "暂无";
  }

  return alert.sensor_field_cn || alert.sensor_field_en || "未配置传感器";
}

function getAlertDeviceMeta(alert) {
  if (!alert) {
    return "暂无";
  }

  return `${alert.device_id} · ${getAlertSensorName(alert)}`;
}

function formatAlertRecord(alert) {
  return {
    label: getAlertDeviceName(alert),
    value: `${alert.current_value} / ${alert.threshold_value}`,
    note: `${getAlertDeviceMeta(alert)} · ${formatDateTime(alert.last_triggered_at || alert.first_triggered_at || alert.created_at)}`
  };
}

function formatDeviceRecord(device) {
  const protocolLabel = device.protocol_type === "http_active" ? "HTTP 主动上报" : "Modbus 轮询";
  return {
    label: device.device_name || device.device_id,
    value: device.online_status ? "在线" : "离线",
    note: `${device.device_id} · ${protocolLabel} · ${formatDateTime(device.last_active_time)}`
  };
}

const alertSummaryMetrics = computed(() => [
  {
    key: "affected-devices",
    label: "影响设备",
    value: alertDeviceCount.value,
    note: `涉及 ${alertDeviceCount.value} 台设备`,
    detailRows: [
      { label: "统计口径", value: "未处理告警涉及的唯一设备数" },
      { label: "影响设备", value: `${alertDeviceCount.value} 台` }
    ],
    source: { type: "alerts", params: { is_resolved: "false" } }
  },
  {
    key: "upper-alerts",
    label: "上限告警",
    value: upperAlertCount.value,
    note: "超过传感器上限阈值",
    detailRows: [
      { label: "统计口径", value: "未处理告警中的上限告警数量" },
      { label: "占比", value: `${unresolvedAlertTotal.value ? Math.round((upperAlertCount.value / unresolvedAlertTotal.value) * 100) : 0}%` }
    ],
    source: { type: "alerts", params: { is_resolved: "false", alert_type: "UPPER_LIMIT" } }
  },
  {
    key: "lower-alerts",
    label: "下限告警",
    value: lowerAlertCount.value,
    note: "低于传感器下限阈值",
    detailRows: [
      { label: "统计口径", value: "未处理告警中的下限告警数量" },
      { label: "占比", value: `${unresolvedAlertTotal.value ? Math.round((lowerAlertCount.value / unresolvedAlertTotal.value) * 100) : 0}%` }
    ],
    source: { type: "alerts", params: { is_resolved: "false", alert_type: "LOWER_LIMIT" } }
  },
  {
    key: "total-alerts",
    label: "告警总数",
    value: unresolvedAlertTotal.value,
    note: "当前未处理告警总量",
    detailRows: [
      { label: "未处理告警", value: `${unresolvedAlertTotal.value} 条` },
      { label: "影响设备", value: `${alertDeviceCount.value} 台` }
    ],
    source: { type: "alerts", params: { is_resolved: "false" } }
  }
]);
const alertInsightMetrics = computed(() => [
  {
    key: "device",
    label: "最近告警设备",
    value: getAlertDeviceName(latestAlert.value),
    note: latestAlert.value ? getAlertDeviceMeta(latestAlert.value) : "当前没有未处理告警",
    detailRows: [
      { label: "设备编号", value: latestAlert.value?.device_id || "暂无" },
      { label: "设备名称", value: getAlertDeviceName(latestAlert.value) },
      { label: "传感器名称", value: getAlertSensorName(latestAlert.value) },
      { label: "告警类型", value: latestAlert.value?.alert_type === "UPPER_LIMIT" ? "上限告警" : latestAlert.value?.alert_type === "LOWER_LIMIT" ? "下限告警" : "暂无" }
    ],
    source: latestAlert.value
      ? { type: "alerts", params: { is_resolved: "false", device_id: latestAlert.value.device_id } }
      : { type: "none" }
  },
  {
    key: "field",
    label: "告警字段",
    value: `${alertFieldCount.value} 个`,
    note: "未处理字段覆盖",
    detailRows: [
      { label: "字段数量", value: `${alertFieldCount.value} 个` },
      { label: "字段列表", value: Array.from(new Set(unresolvedAlerts.value.map((item) => item.sensor_field_cn || item.sensor_field_en))).join("、") || "暂无" }
    ],
    source: { type: "alerts", params: { is_resolved: "false" } }
  },
  {
    key: "time",
    label: "最近发生",
    value: latestAlertTime.value,
    note: "按最近触发时间",
    detailRows: [
      { label: "最近触发", value: latestAlertTime.value },
      { label: "告警来源", value: latestAlert.value?.device_id || "暂无" }
    ],
    source: { type: "alerts", params: { is_resolved: "false" } }
  },
  {
    key: "pressure",
    label: "处理状态",
    value: unresolvedAlertTotal.value ? "待处理" : "正常",
    note: `未处理 ${unresolvedAlertTotal.value} 条`,
    detailRows: [
      { label: "状态", value: unresolvedAlertTotal.value ? "待处理" : "正常" },
      { label: "未处理", value: `${unresolvedAlertTotal.value} 条` }
    ],
    source: { type: "alerts", params: { is_resolved: "false" } }
  }
]);

const alertTrend = computed(() => {
  const dayMs = 24 * 60 * 60 * 1000;
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const buckets = Array.from({ length: 7 }, (_, index) => {
    const date = new Date(today.getTime() - (6 - index) * dayMs);
    return {
      date,
      label: `${date.getMonth() + 1}/${date.getDate()}`,
      count: 0
    };
  });

  for (const alert of unresolvedAlerts.value) {
    const rawTime = alert.last_triggered_at || alert.first_triggered_at || alert.created_at;
    const date = new Date(rawTime);
    if (Number.isNaN(date.getTime())) {
      continue;
    }
    date.setHours(0, 0, 0, 0);
    const diff = Math.floor((today.getTime() - date.getTime()) / dayMs);
    if (diff >= 0 && diff < buckets.length) {
      buckets[buckets.length - 1 - diff].count += 1;
    }
  }

  const width = 520;
  const height = 132;
  const padding = { top: 16, right: 16, bottom: 28, left: 28 };
  const rawMaxCount = Math.max(...buckets.map((item) => item.count), 0);
  const maxCount = Math.max(rawMaxCount, 1);
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const points = buckets.map((bucket, index) => {
    const x = padding.left + (index / (buckets.length - 1)) * chartWidth;
    const y = padding.top + (1 - bucket.count / maxCount) * chartHeight;
    return { ...bucket, x, y };
  });
  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(" ");
  const areaPath = `${path} L ${points.at(-1).x.toFixed(2)} ${height - padding.bottom} L ${points[0].x.toFixed(2)} ${height - padding.bottom} Z`;

  return {
    width,
    height,
    padding,
    maxCount: rawMaxCount,
    points,
    path,
    areaPath
  };
});

const operationMetrics = computed(() => [
  {
    key: "online",
    label: "在线设备",
    value: `${stats.value?.online_devices ?? 0} 台`,
    note: `在线率 ${onlineRate.value}%`,
    detailRows: [
      { label: "在线设备", value: `${stats.value?.online_devices ?? 0} 台` },
      { label: "设备总数", value: `${stats.value?.total_devices ?? 0} 台` },
      { label: "在线率", value: `${onlineRate.value}%` }
    ],
    source: { type: "devices", params: { online_status: "true" } }
  },
  {
    key: "offline",
    label: "离线设备",
    value: `${stats.value?.offline_devices ?? 0} 台`,
    note: "需关注离线设备",
    detailRows: [
      { label: "离线设备", value: `${stats.value?.offline_devices ?? 0} 台` },
      { label: "离线占比", value: `${stats.value?.total_devices ? Math.round(((stats.value.offline_devices || 0) / stats.value.total_devices) * 100) : 0}%` }
    ],
    source: { type: "devices", params: { online_status: "false" } }
  },
  {
    key: "alerts",
    label: "未处理告警",
    value: `${unresolvedAlertTotal.value} 条`,
    note: `影响设备 ${alertDeviceCount.value} 台`,
    detailRows: [
      { label: "未处理告警", value: `${unresolvedAlertTotal.value} 条` },
      { label: "影响设备", value: `${alertDeviceCount.value} 台` }
    ],
    source: { type: "alerts", params: { is_resolved: "false" } }
  },
  {
    key: "protocol",
    label: "接入协议",
    value: `${protocolMetrics.value.filter((item) => item.value > 0).length} 类`,
    note: "当前协议类型",
    detailRows: protocolMetrics.value.map((item) => ({
      label: item.label,
      value: `${item.value} 台 · ${item.ratio}%`
    })),
    source: { type: "devices", params: {} }
  }
]);
const operationFootMetrics = computed(() =>
  [0, 1].map((index) => {
    const metric = protocolMetrics.value[index];
    return {
      key: `protocol-${index}`,
      label: `接入协议${index + 1}`,
      value: metric?.label || "暂无",
      note: `${metric?.value || 0} 台`,
      detailRows: [
        { label: "协议类型", value: metric?.label || "暂无" },
        { label: "设备数量", value: `${metric?.value || 0} 台` },
        { label: "占比", value: `${metric?.ratio || 0}%` }
      ],
      source: metric
        ? { type: "devices", params: { protocol_type: metric.protocolType } }
        : { type: "none" }
    };
  })
);

const metricRecordPageCount = computed(() =>
  Math.max(1, Math.ceil((metricDetailTotal.value || 0) / METRIC_RECORD_PAGE_SIZE))
);
const pagedMetricRecords = computed(() => metricDetailRecords.value);
const metricRecordStart = computed(() =>
  metricDetailTotal.value
    ? (selectedMetricRecordPage.value - 1) * METRIC_RECORD_PAGE_SIZE + 1
    : 0
);
const metricRecordEnd = computed(() =>
  Math.min(
    selectedMetricRecordPage.value * METRIC_RECORD_PAGE_SIZE,
    metricDetailTotal.value || 0
  )
);

async function loadMetricRecords(page = selectedMetricRecordPage.value) {
  if (!selectedDashboardMetric.value?.source || selectedDashboardMetric.value.source.type === "none") {
    metricDetailRecords.value = [];
    metricDetailTotal.value = 0;
    return;
  }

  selectedMetricRecordPage.value = page;
  metricDetailLoading.value = true;
  metricDetailError.value = "";
  const offset = (page - 1) * METRIC_RECORD_PAGE_SIZE;
  const query = new URLSearchParams({
    limit: String(METRIC_RECORD_PAGE_SIZE),
    offset: String(offset),
    ...selectedDashboardMetric.value.source.params
  });

  try {
    if (selectedDashboardMetric.value.source.type === "alerts") {
      const response = await apiFetch(`/api/data/alerts/page?${query.toString()}`);
      metricDetailRecords.value = response.items.map(formatAlertRecord);
      metricDetailTotal.value = response.total;
      return;
    }

    const response = await apiFetch(`/api/devices?${query.toString()}`);
    metricDetailRecords.value = response.items.map(formatDeviceRecord);
    metricDetailTotal.value = response.total;
  } catch (nextError) {
    metricDetailRecords.value = [];
    metricDetailTotal.value = 0;
    metricDetailError.value = toMessage(nextError);
  } finally {
    metricDetailLoading.value = false;
  }
}

function openMetricDetail(metric, group) {
  selectedMetricRecordPage.value = 1;
  metricDetailRecords.value = [];
  metricDetailTotal.value = 0;
  metricDetailError.value = "";
  selectedDashboardMetric.value = {
    group,
    ...metric,
    detailRows: metric.detailRows || [],
    source: metric.source || { type: "none" }
  };
  loadMetricRecords(1);
}

function closeMetricDetail() {
  selectedDashboardMetric.value = null;
  selectedMetricRecordPage.value = 1;
  metricDetailRecords.value = [];
  metricDetailTotal.value = 0;
  metricDetailError.value = "";
}

function setMetricRecordPage(page) {
  loadMetricRecords(Math.min(Math.max(page, 1), metricRecordPageCount.value));
}

async function loadPage() {
  window.clearTimeout(refreshTimer.value);
  refreshTimer.value = null;
  lastRealtimeRefreshAt.value = Date.now();
  loading.value = true;
  error.value = "";

  try {
    const [nextStats, nextAlerts] = await Promise.all([
      apiFetch("/api/data/stats"),
      apiFetch("/api/data/alerts/page?limit=50&offset=0&is_resolved=false")
    ]);

    stats.value = nextStats;
    alerts.value = nextAlerts.items;
  } catch (nextError) {
    error.value = toMessage(nextError);
  } finally {
    loading.value = false;
  }
}

function scheduleRealtimeRefresh() {
  const now = Date.now();
  const elapsed = now - lastRealtimeRefreshAt.value;
  const delay = Math.max(DASHBOARD_REFRESH_MIN_INTERVAL - elapsed, 0);

  if (refreshTimer.value) {
    return;
  }

  refreshTimer.value = window.setTimeout(() => {
    refreshTimer.value = null;
    loadPage();
  }, delay);
}

function connectSocket() {
  disconnectSocket();

  wsClient.value = createManagedSocket({
    buildUrl: () => buildWebSocketUrl("/api/ws"),
    onMessage: async (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "alert") {
          scheduleRealtimeRefresh();
        }
      } catch {
        return;
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

onMounted(async () => {
  await loadPage();
  connectSocket();
});

onBeforeUnmount(() => {
  window.clearTimeout(refreshTimer.value);
  disconnectSocket();
});
</script>

<template>
  <section class="page-section">
    <div class="page-header">
      <div>
        <p class="eyebrow">工作台</p>
        <h2>数据监控总览</h2>
      </div>

      <button class="ghost-btn" type="button" @click="loadPage" :disabled="loading">
        {{ loading ? "刷新中..." : "刷新概览" }}
      </button>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>

    <section class="device-summary-strip dashboard-summary-strip">
      <article class="summary-metric">
        <span>设备在线率</span>
        <strong>{{ onlineRate }}%</strong>
      </article>
      <article class="summary-metric">
        <span>接入设备总数</span>
        <strong>{{ stats?.total_devices ?? 0 }}</strong>
      </article>
      <article class="summary-metric">
        <span>在线设备</span>
        <strong>{{ stats?.online_devices ?? 0 }}</strong>
      </article>
      <article class="summary-metric">
        <span>未处理告警</span>
        <strong>{{ unresolvedAlertTotal }}</strong>
      </article>
      <article class="summary-metric">
        <span>离线设备</span>
        <strong>{{ stats?.offline_devices ?? 0 }}</strong>
      </article>
      <article class="summary-metric">
        <span>影响设备</span>
        <strong>{{ alertDeviceCount }}</strong>
      </article>
    </section>

    <section class="content-grid dashboard-command-grid">
      <article class="panel dashboard-alert-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">告警总览</p>
            <h2>当前告警态势</h2>
          </div>
          <div class="status-strip">
            <span class="status-pill" :data-kind="unresolvedAlertTotal ? 'ok' : 'idle'">
              未处理 {{ unresolvedAlertTotal }}
            </span>
            <span class="status-pill" data-kind="idle">影响设备 {{ alertDeviceCount }}</span>
          </div>
        </div>

        <div class="dashboard-alert-grid dashboard-alert-grid-fixed">
          <div class="analysis-metric-grid">
            <button
              v-for="item in alertSummaryMetrics"
              :key="item.key"
              class="dashboard-stat-tile"
              type="button"
              @click="openMetricDetail(item, '告警总览')"
            >
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
              <p>{{ item.note }}</p>
            </button>
          </div>
          <div class="dashboard-alert-insight-grid">
            <button
              v-for="item in alertInsightMetrics"
              :key="item.key"
              class="dashboard-alert-insight"
              :class="{ primary: item.key === 'device' }"
              type="button"
              @click="openMetricDetail(item, '告警态势')"
            >
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
              <p>{{ item.note }}</p>
            </button>
          </div>
        </div>

        <div class="dashboard-alert-trend">
          <div class="dashboard-trend-head">
            <span class="mini-label">近期告警趋势</span>
            <strong>近 7 天 · 峰值 {{ alertTrend.maxCount }}</strong>
          </div>
          <svg :viewBox="`0 0 ${alertTrend.width} ${alertTrend.height}`" class="dashboard-alert-trend-svg">
            <defs>
              <linearGradient id="dashboard-alert-trend-fill" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stop-color="#d96d38" stop-opacity="0.20" />
                <stop offset="100%" stop-color="#d96d38" stop-opacity="0.02" />
              </linearGradient>
            </defs>
            <line
              :x1="alertTrend.padding.left"
              :x2="alertTrend.width - alertTrend.padding.right"
              :y1="alertTrend.height - alertTrend.padding.bottom"
              :y2="alertTrend.height - alertTrend.padding.bottom"
              class="history-grid-line"
            />
            <path :d="alertTrend.areaPath" fill="url(#dashboard-alert-trend-fill)" />
            <path :d="alertTrend.path" class="dashboard-alert-trend-line" />
            <g v-for="point in alertTrend.points" :key="point.label">
              <circle :cx="point.x" :cy="point.y" r="4" class="dashboard-alert-trend-point" />
              <text :x="point.x" :y="alertTrend.height - 9" class="history-axis-label history-axis-label-x">
                {{ point.label }}
              </text>
              <text :x="point.x" :y="point.y - 8" class="dashboard-alert-trend-count">
                {{ point.count }}
              </text>
            </g>
          </svg>
        </div>
      </article>

      <article class="panel dashboard-health-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">运行统计</p>
            <h2>平台运行状态</h2>
          </div>
          <span class="status-pill" data-kind="idle">在线率 {{ onlineRate }}%</span>
        </div>

        <div class="operation-board">
          <div class="operation-ring" :style="{ '--gauge': `${onlineRate}%` }">
            <strong>{{ onlineRate }}%</strong>
            <span>在线率</span>
          </div>
          <div class="operation-metric-list">
            <button
              v-for="item in operationMetrics"
              :key="item.key"
              class="operation-metric"
              type="button"
              @click="openMetricDetail(item, '平台运行状态')"
            >
              <span>{{ item.label }}</span>
              <strong>{{ item.value }}</strong>
              <p>{{ item.note }}</p>
            </button>
          </div>
        </div>

        <div class="operation-foot-grid">
          <button
            v-for="item in operationFootMetrics"
            :key="item.key"
            class="operation-foot-metric"
            type="button"
            @click="openMetricDetail(item, '平台运行状态')"
          >
            <span>{{ item.label }}</span>
            <strong>{{ item.value }}</strong>
            <p>{{ item.note }}</p>
          </button>
        </div>
      </article>

      <article class="panel dashboard-status-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">在线结构</p>
            <h2>状态分布</h2>
          </div>
        </div>

        <div class="bar-metric-list">
          <div v-for="item in statusMetrics" :key="item.key" class="bar-metric-item">
            <div class="bar-metric-meta">
              <strong>{{ item.label }}</strong>
              <span>{{ item.value }} 台 · {{ item.ratio }}%</span>
            </div>
            <div class="bar-metric-track">
              <div class="bar-metric-fill" :style="{ width: `${item.ratio}%`, background: item.color }" />
            </div>
          </div>
        </div>
      </article>

      <article class="panel dashboard-protocol-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">接入结构</p>
            <h2>协议分布</h2>
          </div>
        </div>

        <div class="bar-metric-list">
          <div v-for="item in protocolMetrics" :key="item.key" class="bar-metric-item">
            <div class="bar-metric-meta">
              <strong>{{ item.label }}</strong>
              <span>{{ item.value }} 台 · {{ item.ratio }}%</span>
            </div>
            <div class="bar-metric-track">
              <div class="bar-metric-fill" :style="{ width: `${item.ratio}%`, background: item.color }" />
            </div>
          </div>
        </div>
      </article>
    </section>

    <div v-if="selectedDashboardMetric" class="modal-backdrop" @click.self="closeMetricDetail">
      <article class="metric-detail-modal" role="dialog" aria-modal="true">
        <div class="metric-detail-head">
          <div>
            <p class="mini-label">{{ selectedDashboardMetric.group }}</p>
            <h2>{{ selectedDashboardMetric.label }}</h2>
          </div>
          <button class="ghost-btn" type="button" @click="closeMetricDetail">关闭</button>
        </div>

        <div class="metric-detail-hero">
          <span>当前数值</span>
          <strong>{{ selectedDashboardMetric.value }}</strong>
          <p>{{ selectedDashboardMetric.note }}</p>
        </div>

        <div class="metric-detail-grid">
          <div v-for="row in selectedDashboardMetric.detailRows" :key="row.label">
            <span>{{ row.label }}</span>
            <strong>{{ row.value }}</strong>
          </div>
        </div>

        <div class="metric-detail-records">
          <div class="metric-detail-record-head">
            <p class="mini-label">相关记录</p>
            <span>
              {{ metricRecordStart }}-{{ metricRecordEnd }} / {{ metricDetailTotal }}
            </span>
          </div>

          <p v-if="metricDetailError" class="error-text">{{ metricDetailError }}</p>
          <div v-if="metricDetailLoading" class="metric-detail-empty">
            <strong>加载中</strong>
            <p>正在读取当前统计项明细</p>
          </div>

          <template v-else-if="metricDetailTotal">
            <div v-for="record in pagedMetricRecords" :key="`${record.label}-${record.note}`">
              <strong>{{ record.label }}</strong>
              <span>{{ record.value }}</span>
              <p>{{ record.note }}</p>
            </div>

            <div class="metric-record-pagination">
              <button
                class="ghost-btn"
                type="button"
                :disabled="selectedMetricRecordPage <= 1"
                @click="setMetricRecordPage(selectedMetricRecordPage - 1)"
              >
                上一页
              </button>
              <span>第 {{ selectedMetricRecordPage }} / {{ metricRecordPageCount }} 页</span>
              <button
                class="ghost-btn"
                type="button"
                :disabled="selectedMetricRecordPage >= metricRecordPageCount"
                @click="setMetricRecordPage(selectedMetricRecordPage + 1)"
              >
                下一页
              </button>
            </div>
          </template>

          <div v-else class="metric-detail-empty">
            <strong>暂无记录</strong>
            <p>当前统计项没有可展示的明细记录</p>
          </div>
        </div>
      </article>
    </div>
  </section>
</template>
