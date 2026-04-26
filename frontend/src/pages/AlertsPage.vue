<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { apiFetch, formatDateTime, listItems, toMessage } from "../lib/api";
import { buildWebSocketUrl, createManagedSocket, formatWebSocketStatus } from "../lib/ws";

const ALERT_PAGE_SIZE = 10;
const ALERT_REFRESH_MIN_INTERVAL = 1000;

const devices = ref([]);
const alerts = ref([]);
const alertTotal = ref(0);
const alertSummary = ref({
  total: 0,
  unresolved: 0,
  resolved: 0,
  upper: 0,
  lower: 0,
  recovered: 0,
  affected_devices: 0
});
const loading = ref(false);
const resolvingId = ref(null);
const error = ref("");
const wsStatus = ref("idle");
const wsStatusLabel = computed(() => formatWebSocketStatus(wsStatus.value));
const refreshTimer = ref(null);
const realtimeRefreshTimer = ref(null);
const lastAlertRefreshAt = ref(0);
const wsClient = ref(null);
const selectedAlertId = ref(null);
const activeAlertTab = ref("list");
const currentAlertPage = ref(1);

const filters = reactive({
  device_id: "",
  is_resolved: "false"
});

const resolveForm = reactive({
  note: ""
});

const unresolvedCount = computed(() => alertSummary.value.unresolved);
const resolvedCount = computed(() => alertSummary.value.resolved);
const upperCount = computed(() => alertSummary.value.upper);
const lowerCount = computed(() => alertSummary.value.lower);
const criticalDevices = computed(() => alertSummary.value.affected_devices);
const recoveredCount = computed(() => alertSummary.value.recovered);
const selectedAlert = computed(() =>
  alerts.value.find((item) => item.id === selectedAlertId.value) || alerts.value[0] || null
);
const selectedDevice = computed(() =>
  devices.value.find((item) => item.device_id === selectedAlert.value?.device_id) || null
);
const workflowSteps = computed(() => [
  { key: "triggered", label: "实时触发", value: alertSummary.value.total },
  { key: "active", label: "待确认", value: unresolvedCount.value },
  { key: "tracking", label: "恢复确认", value: recoveredCount.value },
  { key: "closed", label: "闭环归档", value: resolvedCount.value }
]);
const alertTabs = computed(() => [
  { key: "list", label: "告警列表", badge: alertTotal.value },
  { key: "detail", label: "告警详情", badge: selectedAlert.value ? 1 : 0, disabled: !selectedAlert.value }
]);
const totalAlertPages = computed(() => Math.max(1, Math.ceil(alertTotal.value / ALERT_PAGE_SIZE)));
const pagedAlerts = computed(() => alerts.value);

function buildAlertQuery({ includePaging = false } = {}) {
  const query = new URLSearchParams();

  if (includePaging) {
    query.set("limit", String(ALERT_PAGE_SIZE));
    query.set("offset", String((currentAlertPage.value - 1) * ALERT_PAGE_SIZE));
  }
  if (filters.device_id) {
    query.set("device_id", filters.device_id);
  }
  if (filters.is_resolved !== "all") {
    query.set("is_resolved", filters.is_resolved);
  }

  return query;
}

async function loadDevices() {
  devices.value = listItems(await apiFetch("/api/devices"));
}

async function loadAlerts() {
  window.clearTimeout(realtimeRefreshTimer.value);
  realtimeRefreshTimer.value = null;
  lastAlertRefreshAt.value = Date.now();
  loading.value = true;
  error.value = "";

  try {
    const pageQuery = buildAlertQuery({ includePaging: true });
    const summaryQuery = buildAlertQuery();
    const [response, summary] = await Promise.all([
      apiFetch(`/api/data/alerts/page?${pageQuery.toString()}`),
      apiFetch(`/api/data/alerts/summary?${summaryQuery.toString()}`)
    ]);
    alerts.value = response.items;
    alertTotal.value = response.total;
    alertSummary.value = summary;
    if (!alerts.value.some((item) => item.id === selectedAlertId.value)) {
      selectedAlertId.value = alerts.value[0]?.id || null;
    }
    currentAlertPage.value = Math.min(currentAlertPage.value, totalAlertPages.value);
  } catch (nextError) {
    error.value = toMessage(nextError);
  } finally {
    loading.value = false;
  }
}

function scheduleAlertsRefresh() {
  const now = Date.now();
  const elapsed = now - lastAlertRefreshAt.value;
  const delay = Math.max(ALERT_REFRESH_MIN_INTERVAL - elapsed, 0);

  if (realtimeRefreshTimer.value) {
    return;
  }

  realtimeRefreshTimer.value = window.setTimeout(() => {
    realtimeRefreshTimer.value = null;
    loadAlerts();
  }, delay);
}

function connectSocket() {
  disconnectSocket();

  wsClient.value = createManagedSocket({
    buildUrl: () => buildWebSocketUrl("/api/ws"),
    onStatus: (status) => {
      wsStatus.value = status;
    },
    onMessage: async (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "alert" || payload.type === "device_status") {
          scheduleAlertsRefresh();
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

async function resolveAlert(alert = selectedAlert.value) {
  if (!alert) {
    return;
  }

  resolvingId.value = alert.id;
  error.value = "";

  try {
    await apiFetch(`/api/data/alerts/${alert.id}/resolve`, {
      method: "POST",
      body: JSON.stringify({
        resolve_note: resolveForm.note.trim() || null
      })
    });
    resolveForm.note = "";
    await loadAlerts();
  } catch (nextError) {
    error.value = toMessage(nextError);
  } finally {
    resolvingId.value = null;
  }
}

function selectAlert(alert) {
  selectedAlertId.value = alert.id;
  activeAlertTab.value = "detail";
}

function deviceName(deviceId) {
  const device = devices.value.find((item) => item.device_id === deviceId);
  return device?.device_name || deviceId;
}

function alertTypeLabel(alert) {
  if (alert.status === "recovered") {
    return "已恢复";
  }
  if (alert.status === "resolved" || alert.is_resolved) {
    return "已归档";
  }
  return alert.alert_type === "UPPER_LIMIT" ? "上限告警" : "下限告警";
}

function applyAlertFilters() {
  currentAlertPage.value = 1;
  loadAlerts();
}

function switchAlertTab(tab) {
  if (tab.disabled) {
    return;
  }
  activeAlertTab.value = tab.key;
}

function setAlertPage(page) {
  currentAlertPage.value = Math.min(Math.max(page, 1), totalAlertPages.value);
  loadAlerts();
}

watch(alerts, () => {
  if (currentAlertPage.value > totalAlertPages.value) {
    currentAlertPage.value = totalAlertPages.value;
  }
});

async function init() {
  try {
    await loadDevices();
    await loadAlerts();
    connectSocket();
    refreshTimer.value = window.setInterval(() => {
      loadAlerts();
    }, 30000);
  } catch (nextError) {
    error.value = toMessage(nextError);
  }
}

onMounted(init);

onBeforeUnmount(() => {
  window.clearInterval(refreshTimer.value);
  window.clearTimeout(realtimeRefreshTimer.value);
  disconnectSocket();
});
</script>

<template>
  <section class="page-section">
    <div class="page-header">
      <div>
        <p class="eyebrow">告警中心</p>
        <h2>告警处理</h2>
      </div>
      <button class="ghost-btn" type="button" @click="loadAlerts" :disabled="loading">
        {{ loading ? "刷新中..." : "刷新告警" }}
      </button>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>

    <section class="device-summary-strip dashboard-summary-strip alert-summary-strip">
      <article class="summary-metric">
        <span>未处理</span>
        <strong>{{ unresolvedCount }}</strong>
      </article>
      <article class="summary-metric">
        <span>已处理</span>
        <strong>{{ resolvedCount }}</strong>
      </article>
      <article class="summary-metric">
        <span>影响设备</span>
        <strong>{{ criticalDevices }}</strong>
      </article>
      <article class="summary-metric">
        <span>上限告警</span>
        <strong>{{ upperCount }}</strong>
      </article>
      <article class="summary-metric">
        <span>下限告警</span>
        <strong>{{ lowerCount }}</strong>
      </article>
      <article class="summary-metric">
        <span>实时连接</span>
        <strong>{{ wsStatusLabel }}</strong>
      </article>
    </section>

    <div class="device-tabs alert-tabs">
      <button
        v-for="tab in alertTabs"
        :key="tab.key"
        class="device-tab"
        :class="{ active: activeAlertTab === tab.key }"
        type="button"
        :disabled="tab.disabled"
        @click="switchAlertTab(tab)"
      >
        {{ tab.label }} <span>{{ tab.badge }}</span>
      </button>
    </div>

    <section class="alert-workflow-layout">
      <template v-if="activeAlertTab === 'list'">
        <article class="panel alert-process-panel">
          <div class="panel-head">
            <div>
              <p class="mini-label">处理流程</p>
              <h2>告警闭环</h2>
            </div>
            <div class="status-strip">
              <span class="status-pill" :data-kind="unresolvedCount ? 'ok' : 'idle'">
                未处理 {{ unresolvedCount }}
              </span>
              <span class="status-pill" :data-kind="wsStatus === 'connected' ? 'ok' : 'idle'">
                {{ wsStatusLabel }}
              </span>
            </div>
          </div>

          <div class="workflow-step-list">
            <article v-for="(step, index) in workflowSteps" :key="step.key" class="workflow-step">
              <span>{{ String(index + 1).padStart(2, "0") }}</span>
              <div>
                <strong>{{ step.label }}</strong>
                <p>{{ step.value }} 条</p>
              </div>
            </article>
          </div>

          <div class="alert-filter-stack">
            <label>
              <span>设备</span>
              <select v-model="filters.device_id" @change="applyAlertFilters">
                <option value="">全部设备</option>
                <option v-for="device in devices" :key="device.device_id" :value="device.device_id">
                  {{ device.device_name }} · {{ device.device_id }}
                </option>
              </select>
            </label>
            <label>
              <span>状态</span>
              <select v-model="filters.is_resolved" @change="applyAlertFilters">
                <option value="false">仅未处理</option>
                <option value="true">仅已处理</option>
                <option value="all">全部</option>
              </select>
            </label>
          </div>
        </article>

        <article class="panel alert-queue-panel">
          <div class="panel-head">
            <div>
              <p class="mini-label">告警队列</p>
              <h2>待处理与历史告警</h2>
            </div>
            <span class="muted-text">第 {{ currentAlertPage }} / {{ totalAlertPages }} 页 · 共 {{ alertTotal }} 条</span>
          </div>

          <div class="alert-queue-list">
            <button
              v-for="alert in pagedAlerts"
              :key="alert.id"
              class="alert-queue-card"
              :class="{ active: selectedAlert?.id === alert.id }"
              :data-resolved="alert.is_resolved"
              type="button"
              @click="selectAlert(alert)"
            >
              <div>
                <div class="alert-title-row">
                  <strong>{{ alert.device_name || deviceName(alert.device_id) }}</strong>
                  <span class="device-protocol-tag">{{ alertTypeLabel(alert) }}</span>
                </div>
                <p>{{ alert.sensor_field_cn || alert.sensor_field_en }} · {{ alert.device_id }}</p>
              </div>
              <div class="alert-queue-meta">
                <span>当前 {{ alert.current_value }}</span>
                <span>阈值 {{ alert.threshold_value }}</span>
                <span>{{ formatDateTime(alert.last_triggered_at || alert.first_triggered_at || alert.created_at) }}</span>
              </div>
            </button>
            <div v-if="!alerts.length" class="empty-state">当前没有符合条件的告警。</div>
          </div>

          <div class="pagination-bar alert-pagination">
            <button class="ghost-btn" type="button" :disabled="currentAlertPage <= 1" @click="setAlertPage(currentAlertPage - 1)">
              上一页
            </button>
            <span>每页 10 条</span>
            <button class="ghost-btn" type="button" :disabled="currentAlertPage >= totalAlertPages" @click="setAlertPage(currentAlertPage + 1)">
              下一页
            </button>
          </div>
        </article>
      </template>

      <article v-else class="panel alert-detail-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">处置台</p>
            <h2>告警详情</h2>
          </div>
          <span v-if="selectedAlert" class="status-pill" :data-kind="selectedAlert.is_resolved ? 'idle' : 'ok'">
            {{ selectedAlert.is_resolved ? "已处理" : "待处理" }}
          </span>
        </div>

        <template v-if="selectedAlert">
          <div class="alert-card-main">
            <div>
              <div class="alert-title-row">
                <strong>{{ selectedDevice?.device_name || selectedAlert.device_id }}</strong>
                <span class="device-protocol-tag">{{ alertTypeLabel(selectedAlert) }}</span>
              </div>
              <p>{{ selectedAlert.sensor_field_cn || selectedAlert.sensor_field_en }} · {{ selectedAlert.device_id }}</p>
            </div>
          </div>

          <div class="alert-value-grid alert-detail-grid">
            <div><span>当前值</span><strong>{{ selectedAlert.current_value }}</strong></div>
            <div><span>阈值</span><strong>{{ selectedAlert.threshold_value }}</strong></div>
            <div><span>发生次数</span><strong>{{ selectedAlert.occurrence_count || 1 }}</strong></div>
          </div>

          <div class="alert-timeline">
            <div><span>首次发生</span><strong>{{ formatDateTime(selectedAlert.first_triggered_at || selectedAlert.created_at) }}</strong></div>
            <div><span>最近发生</span><strong>{{ formatDateTime(selectedAlert.last_triggered_at || selectedAlert.created_at) }}</strong></div>
            <div><span>恢复时间</span><strong>{{ formatDateTime(selectedAlert.recovered_at) }}</strong></div>
            <div><span>处理时间</span><strong>{{ formatDateTime(selectedAlert.resolved_at) }}</strong></div>
          </div>

          <div v-if="selectedAlert.resolve_note || selectedAlert.is_resolved" class="alert-resolution-box">
            <span>处理记录</span>
            <strong>{{ selectedAlert.resolve_note || "已完成处理，暂无备注。" }}</strong>
            <p v-if="selectedAlert.is_resolved">
              {{ selectedAlert.resolved_by_name || `用户 #${selectedAlert.resolved_by}` || "未知" }}
            </p>
          </div>

          <label v-if="!selectedAlert.is_resolved" class="alert-note-editor">
            <span>处理备注</span>
            <textarea v-model="resolveForm.note" rows="5" placeholder="填写排查结论、处置动作或交接说明" />
          </label>

          <button
            v-if="!selectedAlert.is_resolved"
            class="primary-btn"
            type="button"
            @click="resolveAlert()"
            :disabled="resolvingId === selectedAlert.id"
          >
            {{ resolvingId === selectedAlert.id ? "处理中..." : "确认处理并归档" }}
          </button>
        </template>

        <div v-else class="empty-state">请选择一条告警查看详情。</div>
      </article>
    </section>
  </section>
</template>
