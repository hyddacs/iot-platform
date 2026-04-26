<script setup>
import { computed, onMounted, reactive, ref } from "vue";

import { apiFetch, formatDateTime, listItems, toMessage } from "../lib/api";

const CHART_COLORS = ["#2f7fe5", "#d96d38", "#d2ab32", "#2b9d62", "#6d7be0", "#bd6aa0"];

const devices = ref([]);
const selectedDeviceId = ref("");
const loading = ref(false);
const error = ref("");
const records = ref([]);
const sensors = ref([]);
const alerts = ref([]);
const recordMode = ref("latest");
const aggregateBucket = ref("auto");
const limit = ref(100);
const selectedField = ref("");
const historyTab = ref("summary");
const detailPage = ref(1);
const DETAIL_PAGE_SIZE = 10;
const hoverState = reactive({
  visible: false,
  chartKey: "",
  x: 0,
  y: 0,
  label: "",
  value: ""
});
const filters = reactive({
  start: "",
  end: ""
});

const hasSelection = computed(() => Boolean(selectedDeviceId.value));

const selectedDevice = computed(() =>
  devices.value.find((device) => device.device_id === selectedDeviceId.value) || null
);

const selectedSensor = computed(() =>
  sensors.value.find((sensor) => sensor.field_en === selectedField.value) || null
);

const selectedFieldLabel = computed(() =>
  selectedSensor.value
    ? `${selectedSensor.value.field_cn} · ${selectedSensor.value.field_en}${selectedSensor.value.unit ? ` (${selectedSensor.value.unit})` : ""}`
    : selectedField.value
);

const selectedFieldAlerts = computed(() =>
  alerts.value.filter((alert) => alert.sensor_field_en === selectedField.value)
);

const numericFields = computed(() => {
  const keys = new Set();

  for (const record of records.value) {
    const data = record.data || {};
    for (const [key, value] of Object.entries(data)) {
      if (typeof value === "number") {
        keys.add(key);
      }
    }
  }

  return Array.from(keys);
});

const fieldSummaries = computed(() =>
  numericFields.value.map((field) => {
    const values = records.value
      .map((record) => record.data?.[field])
      .filter((value) => typeof value === "number");

    if (!values.length) {
      return {
        field,
        count: 0,
        min: "-",
        max: "-",
        avg: "-"
      };
    }

    const sum = values.reduce((total, value) => total + value, 0);
    return {
      field,
      count: values.length,
      min: Math.min(...values).toFixed(2),
      max: Math.max(...values).toFixed(2),
      avg: (sum / values.length).toFixed(2)
    };
  })
);

const selectedFieldRecords = computed(() =>
  selectedField.value
    ? records.value
        .filter((record) => typeof record.data?.[selectedField.value] === "number")
        .map((record) => ({
          ...record,
          value: record.data[selectedField.value]
        }))
    : []
);

const selectedFieldStats = computed(() => {
  const values = selectedFieldRecords.value.map((record) => record.value).slice().sort((left, right) => left - right);
  if (!values.length) {
    return {
      min: "-",
      max: "-",
      avg: "-",
      latest: "-",
      median: "-",
      p90: "-",
      stddev: "-",
      range: "-",
      aboveThreshold: 0,
      belowThreshold: 0,
      alertCount: 0
    };
  }

  const sum = values.reduce((total, value) => total + value, 0);
  const avg = sum / values.length;
  const variance = values.reduce((total, value) => total + (value - avg) ** 2, 0) / values.length;
  const latest = selectedFieldRecords.value[0]?.value;
  const lower = selectedSensor.value?.lower_threshold;
  const upper = selectedSensor.value?.upper_threshold;

  return {
    min: Math.min(...values).toFixed(2),
    max: Math.max(...values).toFixed(2),
    avg: avg.toFixed(2),
    latest: typeof latest === "number" ? latest.toFixed(2) : "-",
    median: percentile(values, 0.5).toFixed(2),
    p90: percentile(values, 0.9).toFixed(2),
    stddev: Math.sqrt(variance).toFixed(2),
    range: (Math.max(...values) - Math.min(...values)).toFixed(2),
    aboveThreshold: upper === null || upper === undefined ? 0 : values.filter((value) => value > upper).length,
    belowThreshold: lower === null || lower === undefined ? 0 : values.filter((value) => value < lower).length,
    alertCount: selectedFieldAlerts.value.length
  };
});

const distributionBuckets = computed(() => {
  const values = selectedFieldRecords.value.map((record) => record.value).filter((value) => typeof value === "number");
  if (!values.length) {
    return [];
  }

  const min = Math.min(...values);
  const max = Math.max(...values);
  const bucketCount = Math.min(8, Math.max(4, Math.ceil(Math.sqrt(values.length))));
  const span = (max - min || 1) / bucketCount;
  const buckets = Array.from({ length: bucketCount }, (_, index) => ({
    label: `${(min + span * index).toFixed(1)}-${(min + span * (index + 1)).toFixed(1)}`,
    count: 0
  }));

  values.forEach((value) => {
    const index = Math.min(bucketCount - 1, Math.floor((value - min) / span));
    buckets[index].count += 1;
  });

  const maxCount = Math.max(...buckets.map((item) => item.count), 1);
  return buckets.map((item) => ({
    ...item,
    ratio: Math.round((item.count / maxCount) * 100)
  }));
});

const detailTotalPages = computed(() =>
  Math.max(1, Math.ceil(selectedFieldRecords.value.length / DETAIL_PAGE_SIZE))
);

const pagedSelectedFieldRecords = computed(() => {
  const start = (detailPage.value - 1) * DETAIL_PAGE_SIZE;
  return selectedFieldRecords.value.slice(start, start + DETAIL_PAGE_SIZE);
});

const trendOverview = computed(() => {
  const values = selectedFieldRecords.value.map((record) => record.value);
  if (values.length < 2) {
    return { change: "-", direction: "stable" };
  }

  const latest = values[0];
  const earliest = values[values.length - 1];
  const delta = latest - earliest;
  return {
    change: `${delta >= 0 ? "+" : ""}${delta.toFixed(2)}`,
    direction: delta > 0 ? "up" : delta < 0 ? "down" : "stable"
  };
});

const aggregateBucketLabel = computed(() => {
  const labels = {
    auto: "自动粒度",
    minute: "分钟聚合",
    hour: "小时聚合"
  };
  return labels[aggregateBucket.value] || "自动粒度";
});

const recordScopeLabel = computed(() =>
  recordMode.value === "all"
    ? `${aggregateBucketLabel.value} ${records.value.length} 桶`
    : `最新 ${limit.value} 条`
);

const selectedFieldChart = computed(() =>
  buildChartModel({
    key: selectedField.value || "selected",
    label: selectedFieldLabel.value || "selected",
    color: "#2f7fe5",
    thresholds: [
      selectedSensor.value?.lower_threshold !== null && selectedSensor.value?.lower_threshold !== undefined
        ? { label: "下限", value: selectedSensor.value.lower_threshold }
        : null,
      selectedSensor.value?.upper_threshold !== null && selectedSensor.value?.upper_threshold !== undefined
        ? { label: "上限", value: selectedSensor.value.upper_threshold }
        : null
    ].filter(Boolean),
    alerts: selectedFieldAlerts.value,
    points: selectedFieldRecords.value
      .slice()
      .reverse()
      .map((record) => ({
        label: formatDateTime(record.reported_at || record.created_at),
        timeLabel: formatTimeLabel(record.reported_at || record.created_at),
        value: record.value,
        time: new Date(record.reported_at || record.created_at).getTime()
      }))
  })
);

const multiChartModels = computed(() =>
  numericFields.value
    .map((field, index) =>
      buildChartModel({
        key: field,
        label: field,
        color: CHART_COLORS[index % CHART_COLORS.length],
        points: records.value
          .slice()
          .reverse()
          .map((record) => ({
            label: formatDateTime(record.reported_at || record.created_at),
            timeLabel: formatTimeLabel(record.reported_at || record.created_at),
            time: new Date(record.reported_at || record.created_at).getTime(),
            value: record.data?.[field]
          }))
          .filter((item) => typeof item.value === "number")
      })
    )
    .filter(Boolean)
);

function buildSmoothPath(points) {
  if (points.length < 2) {
    return "";
  }

  let path = `M ${points[0].x} ${points[0].y}`;
  for (let index = 0; index < points.length - 1; index += 1) {
    const current = points[index];
    const next = points[index + 1];
    const midX = (current.x + next.x) / 2;
    const midY = (current.y + next.y) / 2;
    path += ` Q ${current.x} ${current.y} ${midX} ${midY}`;
  }
  const last = points[points.length - 1];
  path += ` T ${last.x} ${last.y}`;
  return path;
}

function buildAreaPath(points, height, baseline) {
  if (!points.length) {
    return "";
  }

  return [
    `M ${points[0].x} ${baseline}`,
    ...points.map((point, index) => `${index === 0 ? "L" : "L"} ${point.x} ${point.y}`),
    `L ${points[points.length - 1].x} ${baseline}`,
    "Z"
  ].join(" ");
}

function percentile(sortedValues, ratio) {
  if (!sortedValues.length) {
    return 0;
  }
  const index = Math.min(sortedValues.length - 1, Math.max(0, Math.round((sortedValues.length - 1) * ratio)));
  return sortedValues[index];
}

function buildChartModel({ key, label, color, points, thresholds = [], alerts = [] }) {
  if (points.length < 2) {
    return null;
  }

  const width = 720;
  const height = 320;
  const padding = { top: 24, right: 20, bottom: 42, left: 48 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const values = points.map((item) => item.value);
  const times = points.map((item) => item.time).filter((item) => Number.isFinite(item));
  const actualMin = Math.min(...values);
  const actualMax = Math.max(...values);
  const actualRange = actualMax - actualMin;
  const paddingValue = actualRange === 0 ? Math.max(Math.abs(actualMax) * 0.08, 1) : actualRange * 0.08;
  const min = actualMin - paddingValue;
  const max = actualMax + paddingValue;
  const range = max - min || 1;
  const minTime = times.length ? Math.min(...times) : Number.NaN;
  const maxTime = times.length ? Math.max(...times) : Number.NaN;

  const normalizedPoints = points.map((point, index) => {
    const x = Number.isFinite(point.time) && Number.isFinite(minTime) && Number.isFinite(maxTime) && maxTime !== minTime
      ? padding.left + ((point.time - minTime) / (maxTime - minTime)) * plotWidth
      : padding.left + (index / Math.max(points.length - 1, 1)) * plotWidth;
    const y = padding.top + plotHeight - ((point.value - min) / range) * plotHeight;
    return {
      ...point,
      x,
      y
    };
  });

  const baseline = height - padding.bottom;
  const yTicks = Array.from({ length: 5 }, (_, index) => {
    const value = max - (range / 4) * index;
    const y = padding.top + (plotHeight / 4) * index;
    return { value: value.toFixed(2), y };
  });
  const thresholdLines = thresholds
    .filter((threshold) => typeof threshold.value === "number")
    .map((threshold) => ({
      ...threshold,
      y: padding.top + plotHeight - ((threshold.value - min) / range) * plotHeight
    }))
    .filter((threshold) => threshold.y >= padding.top && threshold.y <= baseline);
  const alertMarkers = alerts
    .map((alert) => {
      const time = new Date(alert.created_at).getTime();
      if (!Number.isFinite(time) || !Number.isFinite(minTime) || !Number.isFinite(maxTime)) {
        return null;
      }
      const ratio = maxTime === minTime ? 1 : (time - minTime) / (maxTime - minTime);
      return {
        x: padding.left + Math.min(1, Math.max(0, ratio)) * plotWidth,
        y: padding.top + 10,
        label: `${alert.alert_type} ${formatDateTime(alert.created_at)}`
      };
    })
    .filter(Boolean);

  const xTickIndexes = Array.from(
    new Set([
      0,
      Math.floor((normalizedPoints.length - 1) * 0.33),
      Math.floor((normalizedPoints.length - 1) * 0.66),
      normalizedPoints.length - 1
    ])
  );

  return {
    key,
    label,
    color,
    width,
    height,
    padding,
    baseline,
    points: normalizedPoints,
    path: buildSmoothPath(normalizedPoints),
    areaPath: buildAreaPath(normalizedPoints, height, baseline),
    thresholdLines,
    alertMarkers,
    yTicks,
    xTicks: xTickIndexes.map((index) => ({
      x: normalizedPoints[index].x,
      label: normalizedPoints[index].timeLabel || normalizedPoints[index].label
    })),
    latest: normalizedPoints[normalizedPoints.length - 1]?.value
  };
}

function showTooltip(chartKey, point) {
  hoverState.visible = true;
  hoverState.chartKey = chartKey;
  hoverState.x = point.x;
  hoverState.y = point.y;
  hoverState.label = point.label;
  hoverState.value = String(point.value);
}

function hideTooltip() {
  hoverState.visible = false;
}

function formatTimeLabel(value) {
  if (!value) {
    return "-";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "-";
  }
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  const hour = String(date.getHours()).padStart(2, "0");
  const minute = String(date.getMinutes()).padStart(2, "0");
  return `${month}-${day} ${hour}:${minute}`;
}

function formatRecordSource(record) {
  if (record.source === "minute") {
    return "分钟聚合";
  }
  if (record.source === "hour") {
    return "小时聚合";
  }
  return record.reported_at ? "设备上报时间" : "平台入库时间";
}

async function loadDevices() {
  devices.value = listItems(await apiFetch("/api/devices"));
  if (!selectedDeviceId.value && devices.value.length > 0) {
    selectedDeviceId.value = devices.value[0].device_id;
  }
}

async function loadDeviceContext() {
  if (!selectedDeviceId.value) {
    sensors.value = [];
    alerts.value = [];
    return;
  }

  const query = new URLSearchParams({
    device_id: selectedDeviceId.value,
    limit: "200",
    offset: "0"
  });
  const [nextSensors, nextAlerts] = await Promise.all([
    apiFetch(`/api/devices/${selectedDeviceId.value}/sensors`),
    apiFetch(`/api/data/alerts?${query.toString()}`)
  ]);
  sensors.value = nextSensors;
  alerts.value = nextAlerts;
}

async function loadHistory() {
  if (!selectedDeviceId.value) {
    records.value = [];
    return;
  }

  loading.value = true;
  error.value = "";

  try {
    await loadDeviceContext();
    records.value = await fetchHistoricalRecords();
    if (!selectedField.value || !numericFields.value.includes(selectedField.value)) {
      selectedField.value = numericFields.value[0] || "";
    }
    detailPage.value = 1;
  } catch (nextError) {
    error.value = toMessage(nextError);
  } finally {
    loading.value = false;
  }
}

function buildHistoryQuery(limitValue, offsetValue = 0, includeTotal = true) {
  const query = new URLSearchParams({
    device_id: selectedDeviceId.value,
    limit: String(limitValue),
    offset: String(offsetValue),
    include_total: String(includeTotal)
  });

  if (filters.start) {
    query.set("start_time", `${filters.start}:00`);
  }
  if (filters.end) {
    query.set("end_time", `${filters.end}:00`);
  }

  return query;
}

function buildAggregateQuery() {
  const query = new URLSearchParams({
    device_id: selectedDeviceId.value,
    bucket: aggregateBucket.value,
    limit: "2000",
    include_total: "true"
  });

  if (filters.start) {
    query.set("start_time", `${filters.start}:00`);
  }
  if (filters.end) {
    query.set("end_time", `${filters.end}:00`);
  }

  return query;
}

async function fetchHistoricalRecords() {
  if (recordMode.value !== "all") {
    const response = await apiFetch(`/api/data/historical?${buildHistoryQuery(limit.value).toString()}`);
    return listItems(response);
  }

  const aggregateResponse = await apiFetch(`/api/data/historical/aggregated?${buildAggregateQuery().toString()}`);
  return listItems(aggregateResponse);
}

function normalizeLimit() {
  const numericLimit = Number(limit.value);
  if (!Number.isFinite(numericLimit)) {
    limit.value = 100;
    return;
  }
  limit.value = Math.min(1000, Math.max(1, Math.round(numericLimit)));
}

function updateRecordMode(mode) {
  recordMode.value = mode;
  if (mode === "latest") {
    normalizeLimit();
  }
  loadHistory();
}

function updateLimit() {
  normalizeLimit();
  if (recordMode.value === "latest") {
    loadHistory();
  }
}

function setQuickRange(hours) {
  const end = new Date();
  const start = new Date(end.getTime() - hours * 60 * 60 * 1000);
  filters.start = toLocalInputValue(start);
  filters.end = toLocalInputValue(end);
  loadHistory();
}

function toLocalInputValue(date) {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 16);
}

function exportCsv() {
  const rows = [
    ["time", "field", "value", "unit"],
    ...selectedFieldRecords.value.map((record) => [
      record.reported_at || record.created_at,
      selectedField.value,
      record.value,
      selectedSensor.value?.unit || ""
    ])
  ];
  const csv = rows.map((row) => row.map((cell) => `"${String(cell).replaceAll('"', '""')}"`).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${selectedDeviceId.value}-${selectedField.value || "history"}.csv`;
  link.click();
  URL.revokeObjectURL(url);
}

async function init() {
  try {
    await loadDevices();
    await loadDeviceContext();
    await loadHistory();
  } catch (nextError) {
    error.value = toMessage(nextError);
  }
}

onMounted(init);
</script>

<template>
  <section class="page-section">
    <div class="page-header">
      <div>
        <p class="eyebrow">历史数据</p>
        <h2>趋势分析看板</h2>
      </div>
      <button class="ghost-btn" type="button" @click="loadHistory" :disabled="loading || !hasSelection">
        {{ loading ? "加载中..." : "刷新历史" }}
      </button>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>

    <article class="panel">
      <div class="form-grid history-filter-grid history-page-filter-grid">
        <label>
          <span>设备</span>
          <select v-model="selectedDeviceId" @change="loadHistory">
            <option v-for="device in devices" :key="device.device_id" :value="device.device_id">
              {{ device.device_name }} · {{ device.device_id }}
            </option>
          </select>
        </label>
        <div class="history-date-range">
          <label>
            <span>开始时间</span>
            <input v-model="filters.start" type="datetime-local" />
          </label>
          <label>
            <span>结束时间</span>
            <input v-model="filters.end" type="datetime-local" />
          </label>
        </div>
        <label>
          <span>记录范围</span>
          <select v-model="recordMode" @change="updateRecordMode(recordMode)">
            <option value="latest">最新明细</option>
            <option value="all">聚合趋势</option>
          </select>
        </label>
        <label>
          <span>数据粒度</span>
          <select v-model="aggregateBucket" :disabled="recordMode !== 'all'" @change="loadHistory">
            <option value="auto">自动选择</option>
            <option value="minute">分钟聚合</option>
            <option value="hour">小时聚合</option>
          </select>
        </label>
        <label>
          <span>最新条数</span>
          <input
            v-model.number="limit"
            type="number"
            min="1"
            max="1000"
            step="1"
            :disabled="recordMode === 'all'"
            @change="updateLimit"
          />
        </label>
        <div class="quick-range-group">
          <button class="ghost-btn" type="button" @click="setQuickRange(1)">近 1 小时</button>
          <button class="ghost-btn" type="button" @click="setQuickRange(24)">近 24 小时</button>
          <button class="ghost-btn" type="button" @click="setQuickRange(168)">近 7 天</button>
        </div>
        <button class="primary-btn" type="button" @click="loadHistory" :disabled="loading || !hasSelection">
          {{ loading ? "查询中..." : "查询历史数据" }}
        </button>
      </div>
    </article>

    <section class="device-summary-strip dashboard-summary-strip history-summary-strip">
      <article class="summary-metric">
        <span>当前设备</span>
        <strong>{{ selectedDevice?.device_name || "未选择" }}</strong>
      </article>
      <article class="summary-metric">
        <span>协议类型</span>
        <strong>{{ selectedDevice?.protocol_type || "-" }}</strong>
      </article>
      <article class="summary-metric">
        <span>历史样本</span>
        <strong>{{ recordScopeLabel }}</strong>
      </article>
      <article class="summary-metric">
        <span>数值字段</span>
        <strong>{{ numericFields.length }}</strong>
      </article>
      <article class="summary-metric">
        <span>当前分析字段</span>
        <strong>{{ selectedFieldLabel || "-" }}</strong>
      </article>
      <article class="summary-metric">
        <span>趋势变化</span>
        <strong>{{ trendOverview.change }}</strong>
      </article>
    </section>

    <div class="device-board-tabs detail-tabs">
      <button class="device-tab" :class="{ active: historyTab === 'summary' }" type="button" @click="historyTab = 'summary'">统计总览</button>
      <button class="device-tab" :class="{ active: historyTab === 'trend' }" type="button" @click="historyTab = 'trend'">趋势看板</button>
      <button class="device-tab" :class="{ active: historyTab === 'compare' }" type="button" @click="historyTab = 'compare'">多字段对比</button>
      <button class="device-tab" :class="{ active: historyTab === 'records' }" type="button" @click="historyTab = 'records'">明细记录</button>
    </div>

    <section v-if="historyTab === 'summary'" class="content-grid history-summary-layout">
      <article class="panel span-two">
        <div class="panel-head">
          <div>
            <p class="mini-label">字段总览</p>
            <h2>采集字段统计</h2>
          </div>
          <div class="history-field-select">
            <span class="mini-label">重点分析字段</span>
            <select v-model="selectedField">
              <option v-for="field in numericFields" :key="field" :value="field">
                {{ sensors.find((sensor) => sensor.field_en === field)?.field_cn || field }} · {{ field }}
              </option>
            </select>
          </div>
        </div>

        <div class="field-summary-grid">
          <div v-for="item in fieldSummaries" :key="item.field" class="field-summary-card">
            <p class="mini-label">{{ item.field }}</p>
            <strong>{{ item.avg }}</strong>
            <div class="field-summary-meta">
              <span>样本 {{ item.count }}</span>
              <span>最小 {{ item.min }}</span>
              <span>最大 {{ item.max }}</span>
            </div>
          </div>
        </div>
      </article>

      <article class="panel history-analysis-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">统计分析</p>
            <h2>{{ selectedFieldLabel || "请选择字段" }}</h2>
          </div>
        </div>

        <div class="analysis-metric-grid">
          <div><span>中位数</span><strong>{{ selectedFieldStats.median }}</strong></div>
          <div><span>P90</span><strong>{{ selectedFieldStats.p90 }}</strong></div>
          <div><span>标准差</span><strong>{{ selectedFieldStats.stddev }}</strong></div>
          <div><span>极差</span><strong>{{ selectedFieldStats.range }}</strong></div>
          <div><span>高于上限</span><strong>{{ selectedFieldStats.aboveThreshold }}</strong></div>
          <div><span>低于下限</span><strong>{{ selectedFieldStats.belowThreshold }}</strong></div>
        </div>
      </article>

      <article class="panel history-analysis-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">分布分析</p>
            <h2>数值分布</h2>
          </div>
        </div>

        <div class="distribution-bars">
          <div v-for="bucket in distributionBuckets" :key="bucket.label" class="distribution-row">
            <span>{{ bucket.label }}</span>
            <div class="bar-metric-track">
              <div class="bar-metric-fill" :style="{ width: `${bucket.ratio}%`, background: '#2f7fe5' }" />
            </div>
            <strong>{{ bucket.count }}</strong>
          </div>
          <div v-if="!distributionBuckets.length" class="empty-state">暂无分布数据。</div>
        </div>
      </article>
    </section>

    <section v-else-if="historyTab === 'trend'" class="content-grid dashboard-command-grid">
      <article class="panel span-two">
        <div class="panel-head">
          <div>
            <p class="mini-label">重点趋势</p>
            <h2>{{ selectedFieldLabel || "请选择字段" }} 趋势图</h2>
          </div>
          <div class="toolbar-actions">
            <span class="muted-text">
              {{ trendOverview.direction === "up" ? "近期呈上升" : trendOverview.direction === "down" ? "近期呈下降" : "近期波动平稳" }}
            </span>
            <button class="ghost-btn" type="button" @click="exportCsv" :disabled="!selectedFieldRecords.length">导出 CSV</button>
          </div>
        </div>

        <div v-if="selectedFieldChart" class="history-trend-card">
          <div class="history-chart-summary history-chart-summary-wide">
            <div>
              <span class="mini-label">最新值</span>
              <strong>{{ selectedFieldStats.latest }}</strong>
            </div>
            <div>
              <span class="mini-label">平均值</span>
              <strong>{{ selectedFieldStats.avg }}</strong>
            </div>
            <div>
              <span class="mini-label">最小值</span>
              <strong>{{ selectedFieldStats.min }}</strong>
            </div>
            <div>
              <span class="mini-label">最大值</span>
              <strong>{{ selectedFieldStats.max }}</strong>
            </div>
          </div>

          <div class="history-mini-chart-wrap history-large-chart-wrap" @mouseleave="hideTooltip">
            <svg :viewBox="`0 0 ${selectedFieldChart.width} ${selectedFieldChart.height}`" class="history-chart-svg">
              <defs>
                <linearGradient :id="`gradient-${selectedFieldChart.key}`" x1="0" x2="0" y1="0" y2="1">
                  <stop offset="0%" stop-color="#2f7fe5" stop-opacity="0.25" />
                  <stop offset="100%" stop-color="#2f7fe5" stop-opacity="0.02" />
                </linearGradient>
              </defs>

              <g v-for="tick in selectedFieldChart.yTicks" :key="`${selectedFieldChart.key}-y-${tick.y}`">
                <line
                  :x1="selectedFieldChart.padding.left"
                  :x2="selectedFieldChart.width - selectedFieldChart.padding.right"
                  :y1="tick.y"
                  :y2="tick.y"
                  class="history-grid-line"
                />
                <text
                  :x="selectedFieldChart.padding.left - 8"
                  :y="tick.y + 4"
                  class="history-axis-label history-axis-label-y"
                >
                  {{ tick.value }}
                </text>
              </g>

              <g v-for="tick in selectedFieldChart.xTicks" :key="`${selectedFieldChart.key}-x-${tick.x}`">
                <line
                  :x1="tick.x"
                  :x2="tick.x"
                  :y1="selectedFieldChart.padding.top"
                  :y2="selectedFieldChart.baseline"
                  class="history-grid-line history-grid-line-vertical"
                />
                <text :x="tick.x" :y="selectedFieldChart.height - 12" class="history-axis-label history-axis-label-x">
                  {{ tick.label }}
                </text>
              </g>

              <path
                :d="selectedFieldChart.areaPath"
                :fill="`url(#gradient-${selectedFieldChart.key})`"
                class="history-area-fill"
              />
              <g v-for="line in selectedFieldChart.thresholdLines" :key="`${line.label}-${line.value}`">
                <line
                  :x1="selectedFieldChart.padding.left"
                  :x2="selectedFieldChart.width - selectedFieldChart.padding.right"
                  :y1="line.y"
                  :y2="line.y"
                  class="history-threshold-line"
                />
                <text :x="selectedFieldChart.width - selectedFieldChart.padding.right" :y="line.y - 6" class="history-threshold-label">
                  {{ line.label }} {{ line.value }}
                </text>
              </g>
              <path :d="selectedFieldChart.path" class="history-trend-line" />
              <circle
                v-for="marker in selectedFieldChart.alertMarkers"
                :key="marker.label"
                :cx="marker.x"
                :cy="marker.y"
                r="5"
                class="history-alert-marker"
              />

              <circle
                v-for="point in selectedFieldChart.points"
                :key="`${selectedFieldChart.key}-${point.label}-${point.x}`"
                :cx="point.x"
                :cy="point.y"
                r="4"
                class="history-trend-point"
                @mouseenter="showTooltip(selectedFieldChart.key, point)"
              />
            </svg>

            <div
              v-if="hoverState.visible && hoverState.chartKey === selectedFieldChart.key"
              class="history-tooltip"
              :style="{ left: `${hoverState.x}px`, top: `${hoverState.y}px` }"
            >
              <strong>{{ hoverState.value }}</strong>
              <span>{{ hoverState.label }}</span>
            </div>
          </div>
        </div>
        <div v-else class="empty-state">当前设备暂无足够的数值型历史数据。</div>
      </article>
    </section>

    <section v-else-if="historyTab === 'compare'" class="content-grid dashboard-command-grid">
      <article class="panel span-two">
        <div class="panel-head">
          <div>
            <p class="mini-label">多字段对比</p>
            <h2>并行趋势看板</h2>
          </div>
          <span class="muted-text">{{ multiChartModels.length }} 个字段可视化</span>
        </div>

        <div v-if="multiChartModels.length" class="history-mini-grid">
          <article v-for="chart in multiChartModels" :key="chart.key" class="history-mini-card">
            <div class="history-mini-head">
              <div>
                <p class="mini-label">{{ chart.key }}</p>
                <h3>{{ chart.label }}</h3>
              </div>
              <span class="history-mini-latest" :style="{ color: chart.color }">{{ chart.latest }}</span>
            </div>

            <div class="history-mini-legend">
              <span class="legend-dot" :style="{ background: chart.color }" />
              <span>{{ chart.label }}</span>
            </div>

            <div class="history-mini-chart-wrap" @mouseleave="hideTooltip">
              <svg :viewBox="`0 0 ${chart.width} ${chart.height}`" class="history-mini-chart">
                <g v-for="tick in chart.yTicks" :key="`${chart.key}-y-${tick.y}`">
                  <line
                    :x1="chart.padding.left"
                    :x2="chart.width - chart.padding.right"
                    :y1="tick.y"
                    :y2="tick.y"
                    class="history-grid-line"
                  />
                  <text :x="chart.padding.left - 8" :y="tick.y + 4" class="history-axis-label history-axis-label-y">
                    {{ tick.value }}
                  </text>
                </g>

                <path :d="chart.path" class="history-trend-line" :style="{ stroke: chart.color }" />

                <circle
                  v-for="point in chart.points"
                  :key="`${chart.key}-${point.label}-${point.x}`"
                  :cx="point.x"
                  :cy="point.y"
                  r="4"
                  class="history-trend-point"
                  :style="{ fill: chart.color }"
                  @mouseenter="showTooltip(chart.key, point)"
                />
              </svg>

              <div
                v-if="hoverState.visible && hoverState.chartKey === chart.key"
                class="history-tooltip"
                :style="{ left: `${hoverState.x}px`, top: `${hoverState.y}px` }"
              >
                <strong>{{ hoverState.value }}</strong>
                <span>{{ hoverState.label }}</span>
              </div>
            </div>
          </article>
        </div>
        <div v-else class="empty-state">暂无可视化字段。</div>
      </article>
    </section>

    <section v-else class="content-grid dashboard-command-grid">
      <article class="panel span-two">
        <div class="panel-head">
          <div>
            <p class="mini-label">明细记录</p>
            <h2>历史数据表</h2>
          </div>
          <span class="muted-text">{{ selectedFieldRecords.length }} 条字段记录</span>
        </div>

        <div class="history-record-table-wrap">
          <table class="history-record-table">
            <thead>
              <tr>
                <th>时间</th>
                <th>字段</th>
                <th>值</th>
                <th>数据源</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="record in pagedSelectedFieldRecords" :key="record.id">
                <td>{{ formatDateTime(record.reported_at || record.created_at) }}</td>
                <td>{{ selectedField }}</td>
                <td>{{ record.value }}</td>
                <td>{{ formatRecordSource(record) }}</td>
              </tr>
              <tr v-if="!selectedFieldRecords.length">
                <td colspan="4" class="empty-state">当前字段暂无记录。</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="history-pagination">
          <button class="ghost-btn" type="button" @click="detailPage -= 1" :disabled="detailPage === 1">上一页</button>
          <span>第 {{ detailPage }} / {{ detailTotalPages }} 页 · 每页 10 条</span>
          <button class="ghost-btn" type="button" @click="detailPage += 1" :disabled="detailPage >= detailTotalPages">下一页</button>
        </div>
      </article>
    </section>
  </section>
</template>
