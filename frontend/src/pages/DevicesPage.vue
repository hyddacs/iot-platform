<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";

import { apiFetch, formatDateTime, listItems, toMessage } from "../lib/api";
import { buildWebSocketUrl, createManagedSocket, formatWebSocketStatus } from "../lib/ws";

function createSensorDraft() {
  return {
    field_en: "",
    field_cn: "",
    unit: "",
    channel_no: "",
    coeff: 1,
    lower_threshold: "",
    upper_threshold: "",
    is_monitored: true
  };
}

const HISTORY_PAGE_SIZE = 10;

const devices = ref([]);
const sensors = ref([]);
const sensorPreviewMap = ref({});
const selectedDeviceId = ref("");
const searchKeyword = ref("");
const loading = ref(false);
const savingDevice = ref(false);
const savingSensor = ref(false);
const error = ref("");
const success = ref("");
const panelOpen = ref(false);
const panelMode = ref("create");
const detailOpen = ref(false);
const detailTab = ref("detail");
const sensorEditorOpen = ref(false);
const editingSensorId = ref(null);
const livePayload = ref(null);
const wsStatus = ref("idle");
const wsStatusLabel = computed(() => formatWebSocketStatus(wsStatus.value));
const wsClient = ref(null);
const listWsClient = ref(null);
const historyLoading = ref(false);
const historyView = ref("table");
const historyRecords = ref([]);
const historySensor = ref(null);
const searchTimer = ref(null);
const historyRange = reactive({
  start: "",
  end: ""
});
const historyPagination = reactive({
  page: 1,
  total: 0,
  limit: HISTORY_PAGE_SIZE
});
const devicePagination = reactive({
  total: 0,
  limit: 100,
  offset: 0
});
const confirmDialog = reactive({
  open: false,
  title: "",
  message: "",
  confirmText: "确认",
  danger: false,
  resolve: null
});
const notifyDialog = reactive({
  open: false,
  title: "",
  message: ""
});
const createStep = ref("protocol");

const deviceForm = reactive({
  device_id: "",
  device_name: "",
  protocol_type: "http_active",
  description: "",
  gateway_ip: "",
  slave_id: "",
  is_active: true,
  is_maintenance: false,
  is_test_device: false,
  offline_threshold: 900,
  factory: "",
  workshop: "",
  production_line: "",
  device_group: ""
});

const sensorForm = reactive(createSensorDraft());
const draftSensors = ref([createSensorDraft()]);

const filteredDevices = computed(() => devices.value);

const selectedDevice = computed(() =>
  devices.value.find((item) => item.device_id === selectedDeviceId.value) || null
);

const accessDocs = computed(() => {
  if (!selectedDevice.value || selectedDevice.value.protocol_type !== "http_active") {
    return null;
  }

  const reportUrl = `${window.location.origin}/api/data/report/http`;
  const sampleData = Object.fromEntries(
    sensors.value.slice(0, 4).map((sensor, index) => [sensor.field_en, Number((index + 1) * 10)])
  );
  const payload = {
    device_id: selectedDevice.value.device_id,
    device_secret: selectedDevice.value.device_secret || "创建设备后生成的密钥",
    timestamp: new Date().toISOString(),
    data: Object.keys(sampleData).length ? sampleData : { temperature: 25.6, humidity: 61.2 }
  };

  return {
    reportUrl,
    json: JSON.stringify(payload, null, 2),
    curl: [
      `curl -X POST "${reportUrl}" \\`,
      `  -H "Content-Type: application/json" \\`,
      `  -d '${JSON.stringify(payload)}'`
    ].join("\n")
  };
});

const deviceSummary = computed(() => {
  const total = devices.value.length;
  const online = devices.value.filter((item) => item.online_status).length;
  return {
    total,
    online,
    offline: total - online
  };
});

const sensorRows = computed(() =>
  sensors.value.map((sensor) => ({
    ...sensor,
    realtimeValue: livePayload.value?.[sensor.field_en] ?? null
  }))
);

const historyChartPoints = computed(() =>
  historyRecords.value
    .slice()
    .reverse()
    .map((record) => ({
      label: formatDateTime(record.reported_at || record.created_at),
      value: Number(record.value)
    }))
    .filter((item) => Number.isFinite(item.value))
);

const deviceHistoryChart = computed(() => {
  if (historyChartPoints.value.length < 2) {
    return null;
  }

  const width = 760;
  const height = 280;
  const padding = { top: 26, right: 28, bottom: 44, left: 58 };
  const chartWidth = width - padding.left - padding.right;
  const chartHeight = height - padding.top - padding.bottom;
  const values = historyChartPoints.value.map((item) => item.value);
  const minValue = Math.min(...values);
  const maxValue = Math.max(...values);
  const range = maxValue - minValue || 1;
  const paddedMin = minValue - range * 0.08;
  const paddedMax = maxValue + range * 0.08;
  const paddedRange = paddedMax - paddedMin || 1;

  const points = historyChartPoints.value.map((point, index) => {
    const x = padding.left + (index / (historyChartPoints.value.length - 1)) * chartWidth;
    const y = padding.top + (1 - (point.value - paddedMin) / paddedRange) * chartHeight;
    return { ...point, x, y };
  });

  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`)
    .join(" ");
  const areaPath = `${path} L ${points.at(-1).x.toFixed(2)} ${height - padding.bottom} L ${points[0].x.toFixed(2)} ${height - padding.bottom} Z`;
  const yTicks = Array.from({ length: 5 }, (_, index) => {
    const ratio = index / 4;
    const value = paddedMax - ratio * paddedRange;
    return {
      y: padding.top + ratio * chartHeight,
      value: Number(value.toFixed(2))
    };
  });
  const xStep = Math.max(1, Math.ceil(points.length / 4));
  const xTicks = points
    .filter((_, index) => index % xStep === 0 || index === points.length - 1)
    .map((point) => ({
      x: point.x,
      label: point.label
    }));

  return {
    width,
    height,
    padding,
    path,
    areaPath,
    points,
    yTicks,
    xTicks
  };
});

watch(
  () => deviceForm.protocol_type,
  (protocol) => {
    if (protocol === "http_active") {
      deviceForm.gateway_ip = "";
      deviceForm.slave_id = "";
      deviceForm.is_test_device = false;
    }
  }
);

watch(
  () => [detailOpen.value, selectedDeviceId.value],
  ([isOpen, deviceId]) => {
    if (isOpen && deviceId && detailTab.value === "detail") {
      connectSocket(deviceId);
    } else {
      disconnectSocket();
    }
  }
);

watch(searchKeyword, () => {
  window.clearTimeout(searchTimer.value);
  searchTimer.value = window.setTimeout(() => {
    devicePagination.offset = 0;
    loadDevices();
  }, 350);
});

watch(
  () => detailTab.value,
  (tab) => {
    if (tab === "history") {
      disconnectSocket();
      sensorEditorOpen.value = false;
      if (!historySensor.value && sensors.value.length) {
        historySensor.value = sensors.value[0];
      }
      if (selectedDeviceId.value && historySensor.value) {
        historyPagination.page = 1;
        loadSensorHistory();
      }
    } else if (detailOpen.value && selectedDeviceId.value) {
      connectSocket(selectedDeviceId.value);
    }
  }
);

function normalizeSensorPayload(form) {
  return {
    field_en: form.field_en.trim(),
    field_cn: form.field_cn.trim(),
    unit: form.unit.trim() || null,
    channel_no: form.channel_no === "" ? null : Number(form.channel_no),
    coeff: Number(form.coeff),
    lower_threshold: form.lower_threshold === "" ? null : Number(form.lower_threshold),
    upper_threshold: form.upper_threshold === "" ? null : Number(form.upper_threshold),
    is_monitored: Boolean(form.is_monitored)
  };
}

function validateSensorPayload(payload, sensorList, currentIndex = null) {
  if (!payload.field_en || !payload.field_cn) {
    throw new Error("请填写完整的传感器字段名称");
  }

  if (
    payload.lower_threshold !== null &&
    payload.upper_threshold !== null &&
    payload.lower_threshold > payload.upper_threshold
  ) {
    throw new Error("下限阈值不能大于上限阈值");
  }

  sensorList.forEach((item, index) => {
    if (currentIndex !== null && index === currentIndex) {
      return;
    }

    const nextPayload = normalizeSensorPayload(item);
    if (nextPayload.field_en && nextPayload.field_en === payload.field_en) {
      throw new Error(`传感器字段英文名重复: ${payload.field_en}`);
    }

    if (
      payload.channel_no !== null &&
      nextPayload.channel_no !== null &&
      nextPayload.channel_no === payload.channel_no
    ) {
      throw new Error(`传感器通道号重复: ${payload.channel_no}`);
    }
  });
}

async function loadDevices() {
  loading.value = true;
  error.value = "";

  try {
    const query = new URLSearchParams({
      limit: String(devicePagination.limit),
      skip: String(devicePagination.offset)
    });
    if (searchKeyword.value.trim()) {
      query.set("keyword", searchKeyword.value.trim());
    }

    const response = await apiFetch(`/api/devices?${query.toString()}`);
    const deviceList = listItems(response);
    devices.value = deviceList;
    devicePagination.total = response.total ?? deviceList.length;
    sensorPreviewMap.value = Object.fromEntries(
      deviceList.map((device) => [device.device_id, device.sensor_preview || ""])
    );

    if (
      selectedDeviceId.value &&
      !devices.value.some((item) => item.device_id === selectedDeviceId.value)
    ) {
      selectedDeviceId.value = "";
      detailOpen.value = false;
      sensors.value = [];
      livePayload.value = null;
    }
  } catch (nextError) {
    error.value = toMessage(nextError);
  } finally {
    loading.value = false;
  }
}

async function loadDeviceDetails(deviceId) {
  try {
    sensors.value = await apiFetch(`/api/devices/${deviceId}/sensors`);
  } catch (nextError) {
    error.value = toMessage(nextError);
  }
}

async function refreshSelectedDevice() {
  await loadDevices();
  if (selectedDeviceId.value && detailOpen.value) {
    await loadDeviceDetails(selectedDeviceId.value);
  }
}

function connectSocket(deviceId) {
  disconnectSocket();

  wsClient.value = createManagedSocket({
    buildUrl: () => buildWebSocketUrl(`/api/ws?device_id=${encodeURIComponent(deviceId)}`),
    onStatus: (status) => {
      wsStatus.value = status;
    },
    onMessage: (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "device_data" && payload.device_id === selectedDeviceId.value) {
          livePayload.value = payload.data || null;
        }

        if (payload.type === "device_status" && payload.device_id) {
          devices.value = devices.value.map((item) =>
            item.device_id === payload.device_id ? { ...item, online_status: payload.online } : item
          );
        }
      } catch {
        wsStatus.value = "error";
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
  wsStatus.value = "idle";
}

function connectListSocket() {
  disconnectListSocket();

  listWsClient.value = createManagedSocket({
    buildUrl: () => buildWebSocketUrl("/api/ws"),
    onMessage: (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "device_status" && payload.device_id) {
          patchDeviceRuntime(payload.device_id, {
            online_status: payload.online,
            last_active_time: payload.timestamp || null
          });
        }
        if (payload.type === "device_data" && payload.device_id) {
          patchDeviceRuntime(payload.device_id, {
            online_status: true,
            last_active_time: payload.timestamp || new Date().toISOString()
          });
        }
      } catch {
        return;
      }
    }
  });
  listWsClient.value.connect();
}

function disconnectListSocket() {
  if (listWsClient.value) {
    listWsClient.value.disconnect();
    listWsClient.value = null;
  }
}

function patchDeviceRuntime(deviceId, patch) {
  devices.value = devices.value.map((item) =>
    item.device_id === deviceId ? { ...item, ...patch } : item
  );
}

function resetDeviceForm() {
  deviceForm.device_id = "";
  deviceForm.device_name = "";
  deviceForm.protocol_type = "http_active";
  deviceForm.description = "";
  deviceForm.gateway_ip = "";
  deviceForm.slave_id = "";
  deviceForm.is_active = true;
  deviceForm.is_maintenance = false;
  deviceForm.is_test_device = false;
  deviceForm.offline_threshold = 900;
  deviceForm.factory = "";
  deviceForm.workshop = "";
  deviceForm.production_line = "";
  deviceForm.device_group = "";
}

function resetSensorEditor() {
  Object.assign(sensorForm, createSensorDraft());
  editingSensorId.value = null;
}

function resetDraftSensors() {
  draftSensors.value = [createSensorDraft()];
}

function openCreatePanel() {
  panelMode.value = "create";
  panelOpen.value = true;
  detailOpen.value = false;
  detailTab.value = "detail";
  sensorEditorOpen.value = false;
  disconnectSocket();
  createStep.value = "protocol";
  success.value = "";
  error.value = "";
  resetDeviceForm();
  resetDraftSensors();
}

function openDeviceListTab() {
  panelOpen.value = false;
  detailOpen.value = false;
  detailTab.value = "detail";
  selectedDeviceId.value = "";
  sensorEditorOpen.value = false;
  livePayload.value = null;
  resetSensorEditor();
  disconnectSocket();
}

function openEditPanel() {
  if (!selectedDevice.value) {
    return;
  }

  panelMode.value = "edit";
  panelOpen.value = true;
  createStep.value = "device";
  success.value = "";
  error.value = "";
  deviceForm.device_id = selectedDevice.value.device_id;
  deviceForm.device_name = selectedDevice.value.device_name;
  deviceForm.protocol_type = selectedDevice.value.protocol_type;
  deviceForm.description = selectedDevice.value.description || "";
  deviceForm.gateway_ip = selectedDevice.value.gateway_ip || "";
  deviceForm.slave_id = selectedDevice.value.slave_id ?? "";
  deviceForm.is_active = selectedDevice.value.is_active;
  deviceForm.is_maintenance = selectedDevice.value.is_maintenance;
  deviceForm.is_test_device = selectedDevice.value.is_test_device;
  deviceForm.offline_threshold = selectedDevice.value.offline_threshold;
  deviceForm.factory = selectedDevice.value.factory || "";
  deviceForm.workshop = selectedDevice.value.workshop || "";
  deviceForm.production_line = selectedDevice.value.production_line || "";
  deviceForm.device_group = selectedDevice.value.device_group || "";
}

function closePanel() {
  panelOpen.value = false;
  resetDraftSensors();
}

async function openDeviceDetail(deviceId) {
  selectedDeviceId.value = deviceId;
  panelOpen.value = false;
  detailOpen.value = true;
  detailTab.value = "detail";
  sensorEditorOpen.value = false;
  livePayload.value = null;
  await loadDeviceDetails(deviceId);
}

function closeDeviceDetail() {
  openDeviceListTab();
}

async function openHistoryTab(sensor = null) {
  if (!selectedDeviceId.value) {
    return;
  }
  panelOpen.value = false;
  if (!sensors.value.length) {
    await loadDeviceDetails(selectedDeviceId.value);
  }
  if (sensor) {
    historySensor.value = sensor;
  } else if (!historySensor.value && sensors.value.length) {
    historySensor.value = sensors.value[0];
  }
  detailOpen.value = true;
  detailTab.value = "history";
}

async function openDocsTab() {
  if (!selectedDeviceId.value) {
    return;
  }
  panelOpen.value = false;
  if (!sensors.value.length) {
    await loadDeviceDetails(selectedDeviceId.value);
  }
  detailOpen.value = true;
  detailTab.value = "docs";
  sensorEditorOpen.value = false;
}

function appendDraftSensor() {
  draftSensors.value = [...draftSensors.value, createSensorDraft()];
}

function removeDraftSensor(index) {
  if (draftSensors.value.length === 1) {
    draftSensors.value = [createSensorDraft()];
    return;
  }
  draftSensors.value = draftSensors.value.filter((_, itemIndex) => itemIndex !== index);
}

async function submitDeviceForm() {
  savingDevice.value = true;
  error.value = "";
  success.value = "";

  try {
    if (!deviceForm.device_id.trim() || !deviceForm.device_name.trim()) {
      throw new Error("请填写设备 ID 和设备名称");
    }

    const payload = {
      device_id: deviceForm.device_id.trim(),
      device_name: deviceForm.device_name.trim(),
      protocol_type: deviceForm.protocol_type,
      description: deviceForm.description.trim() || null,
      is_test_device: deviceForm.is_test_device,
      is_active: deviceForm.is_active,
      is_maintenance: deviceForm.is_maintenance,
      offline_threshold: Number(deviceForm.offline_threshold),
      factory: deviceForm.factory.trim() || null,
      workshop: deviceForm.workshop.trim() || null,
      production_line: deviceForm.production_line.trim() || null,
      device_group: deviceForm.device_group.trim() || null
    };

    if (deviceForm.protocol_type === "modbus_gateway") {
      payload.gateway_ip = deviceForm.gateway_ip.trim();
      payload.slave_id = Number(deviceForm.slave_id);
    }

    if (panelMode.value === "create") {
      const normalizedSensors = draftSensors.value
        .map((item) => normalizeSensorPayload(item))
        .filter((item) => item.field_en || item.field_cn || item.channel_no !== null);

      normalizedSensors.forEach((item, index) => validateSensorPayload(item, draftSensors.value, index));
      payload.sensors = normalizedSensors;

      const created = await apiFetch("/api/devices", {
        method: "POST",
        body: JSON.stringify(payload)
      });
      selectedDeviceId.value = created.device_id;
      success.value = "设备和传感器已创建";
    } else {
      await apiFetch(`/api/devices/${selectedDeviceId.value}`, {
        method: "PUT",
        body: JSON.stringify(payload)
      });
      success.value = "设备信息已更新";
    }

    closePanel();
    await refreshSelectedDevice();
  } catch (nextError) {
    const message = toMessage(nextError);
    error.value = message;
    showErrorDialog(message);
  } finally {
    savingDevice.value = false;
  }
}

function openSensorEditor() {
  if (!selectedDevice.value) {
    return;
  }
  sensorEditorOpen.value = true;
  resetSensorEditor();
  error.value = "";
  success.value = "";
}

function startEditSensor(sensor) {
  sensorEditorOpen.value = true;
  sensorForm.field_en = sensor.field_en;
  sensorForm.field_cn = sensor.field_cn;
  sensorForm.unit = sensor.unit || "";
  sensorForm.channel_no = sensor.channel_no ?? "";
  sensorForm.coeff = sensor.coeff;
  sensorForm.lower_threshold = sensor.lower_threshold ?? "";
  sensorForm.upper_threshold = sensor.upper_threshold ?? "";
  sensorForm.is_monitored = sensor.is_monitored;
  editingSensorId.value = sensor.id;
  error.value = "";
  success.value = "";
}

function closeSensorEditor() {
  sensorEditorOpen.value = false;
  resetSensorEditor();
}

async function saveSensor() {
  if (!selectedDeviceId.value) {
    error.value = "请先选择设备";
    return;
  }

  savingSensor.value = true;
  error.value = "";
  success.value = "";

  try {
    const payload = normalizeSensorPayload(sensorForm);

    if (editingSensorId.value) {
      const currentIndex = sensors.value.findIndex((item) => item.id === editingSensorId.value);
      validateSensorPayload(payload, sensors.value, currentIndex);
      await apiFetch(`/api/devices/${selectedDeviceId.value}/sensors/${editingSensorId.value}`, {
        method: "PUT",
        body: JSON.stringify(payload)
      });
      success.value = "传感器已更新";
    } else {
      validateSensorPayload(payload, sensors.value, null);
      await apiFetch(`/api/devices/${selectedDeviceId.value}/sensors`, {
        method: "POST",
        body: JSON.stringify(payload)
      });
      success.value = "传感器已新增";
    }

    closeSensorEditor();
    await refreshSelectedDevice();
  } catch (nextError) {
    const message = toMessage(nextError);
    error.value = message;
    showErrorDialog(message);
  } finally {
    savingSensor.value = false;
  }
}

async function deleteSensor(sensorId) {
  if (
    !selectedDeviceId.value ||
    !(await requestConfirm({
      title: "删除传感器",
      message: "删除后该传感器将不再参与实时展示和阈值监控，历史数据不会被清理。",
      confirmText: "删除传感器",
      danger: true
    }))
  ) {
    return;
  }

  try {
    await apiFetch(`/api/devices/${selectedDeviceId.value}/sensors/${sensorId}`, {
      method: "DELETE"
    });
    success.value = "传感器已删除";
    await refreshSelectedDevice();
  } catch (nextError) {
    error.value = toMessage(nextError);
  }
}

async function deleteDevice(deviceId) {
  if (
    !(await requestConfirm({
      title: "删除设备",
      message: `确认归档设备 ${deviceId}？设备会从列表隐藏，但历史数据和告警记录会保留。`,
      confirmText: "归档设备",
      danger: true
    }))
  ) {
    return;
  }

  try {
    await apiFetch(`/api/devices/${deviceId}`, { method: "DELETE" });
    success.value = "设备已归档";
    if (selectedDeviceId.value === deviceId) {
      selectedDeviceId.value = "";
      closeDeviceDetail();
    }
    await loadDevices();
  } catch (nextError) {
    error.value = toMessage(nextError);
  }
}

async function updateSelectedDeviceState(patch, message) {
  if (!selectedDevice.value) {
    return;
  }

  try {
    await apiFetch(`/api/devices/${selectedDevice.value.device_id}`, {
      method: "PUT",
      body: JSON.stringify(patch)
    });
    success.value = message;
    await refreshSelectedDevice();
  } catch (nextError) {
    const nextMessage = toMessage(nextError);
    error.value = nextMessage;
    showErrorDialog(nextMessage);
  }
}

async function openHistoryModal(sensor) {
  historyView.value = "table";
  historyPagination.page = 1;
  openHistoryTab(sensor);
}

function normalizeLocalDateTime(value) {
  if (!value) {
    return "";
  }
  return value.length === 16 ? `${value}:00` : value;
}

async function loadSensorHistory() {
  if (!selectedDeviceId.value || !historySensor.value) {
    return;
  }

  historyLoading.value = true;
  error.value = "";

  try {
    const query = new URLSearchParams({
      device_id: selectedDeviceId.value,
      limit: String(HISTORY_PAGE_SIZE),
      offset: String((historyPagination.page - 1) * HISTORY_PAGE_SIZE)
    });

    if (historyRange.start) {
      query.set("start_time", normalizeLocalDateTime(historyRange.start));
    }
    if (historyRange.end) {
      query.set("end_time", normalizeLocalDateTime(historyRange.end));
    }

    const response = await apiFetch(`/api/data/historical?${query.toString()}`);
    const records = listItems(response);
    const normalized = records
      .filter((record) => record.data && historySensor.value.field_en in record.data)
      .map((record) => ({
        id: record.id,
        reported_at: record.reported_at,
        created_at: record.created_at,
        value: record.data[historySensor.value.field_en]
      }));

    historyRecords.value = normalized;
    historyPagination.total = response.total ?? normalized.length;
  } catch (nextError) {
    error.value = toMessage(nextError);
  } finally {
    historyLoading.value = false;
  }
}

function totalHistoryPages() {
  return Math.max(1, Math.ceil(historyPagination.total / HISTORY_PAGE_SIZE));
}

function prevHistoryPage() {
  if (historyPagination.page === 1) {
    return;
  }
  historyPagination.page -= 1;
  loadSensorHistory();
}

function nextHistoryPage() {
  if (historyPagination.page >= totalHistoryPages()) {
    return;
  }
  historyPagination.page += 1;
  loadSensorHistory();
}

function requestConfirm({ title, message, confirmText = "确认", danger = false }) {
  confirmDialog.open = true;
  confirmDialog.title = title;
  confirmDialog.message = message;
  confirmDialog.confirmText = confirmText;
  confirmDialog.danger = danger;
  return new Promise((resolve) => {
    confirmDialog.resolve = resolve;
  });
}

function closeConfirm(result) {
  confirmDialog.open = false;
  if (confirmDialog.resolve) {
    confirmDialog.resolve(result);
  }
  confirmDialog.resolve = null;
}

function showErrorDialog(message) {
  notifyDialog.title = "提交失败";
  notifyDialog.message = message;
  notifyDialog.open = true;
}

onMounted(() => {
  loadDevices();
  connectListSocket();
});
onBeforeUnmount(() => {
  window.clearTimeout(searchTimer.value);
  disconnectSocket();
  disconnectListSocket();
});
</script>

<template>
  <section class="page-section">
    <div class="page-header">
      <div>
        <p class="eyebrow">设备管理</p>
        <h2>{{ detailOpen && selectedDevice ? selectedDevice.device_name : "设备台账" }}</h2>
      </div>

      <div class="toolbar-actions">
        <button v-if="detailOpen" class="ghost-btn" type="button" @click="closeDeviceDetail">返回设备列表</button>
        <button v-else class="ghost-btn" type="button" @click="loadDevices" :disabled="loading">
          {{ loading ? "刷新中..." : "刷新列表" }}
        </button>
        <button class="primary-btn" type="button" @click="openCreatePanel">添加新设备</button>
      </div>
    </div>

    <div v-if="!detailOpen" class="device-summary-strip">
      <article class="summary-metric">
        <span>设备总数</span>
        <strong>{{ devicePagination.total }}</strong>
      </article>
      <article class="summary-metric">
        <span>当前在线</span>
        <strong>{{ deviceSummary.online }}</strong>
      </article>
      <article class="summary-metric">
        <span>当前离线</span>
        <strong>{{ deviceSummary.offline }}</strong>
      </article>
    </div>

    <p v-if="error" class="error-text">{{ error }}</p>
    <p v-if="success" class="success-text">{{ success }}</p>

    <div class="device-board-tabs detail-tabs device-global-tabs">
      <button
        class="device-tab"
        :class="{ active: !detailOpen && !panelOpen }"
        type="button"
        @click="openDeviceListTab"
      >
        设备列表
      </button>
      <button
        v-if="selectedDeviceId"
        class="device-tab"
        :class="{ active: detailOpen && detailTab === 'detail' }"
        type="button"
        @click="openDeviceDetail(selectedDeviceId)"
      >
        设备详情
      </button>
      <button
        v-if="selectedDeviceId"
        class="device-tab"
        :class="{ active: detailOpen && detailTab === 'history' }"
        type="button"
        @click="openHistoryTab()"
      >
        历史数据
      </button>
      <button
        v-if="selectedDeviceId"
        class="device-tab"
        :class="{ active: detailOpen && detailTab === 'docs' }"
        type="button"
        @click="openDocsTab"
      >
        接入文档
      </button>
      <button
        v-if="panelOpen && panelMode === 'create'"
        class="device-tab"
        :class="{ active: panelOpen && panelMode === 'create' }"
        type="button"
        @click="openCreatePanel"
      >
        添加设备
      </button>
    </div>

    <section v-if="!detailOpen && !(panelOpen && panelMode === 'create')" class="device-workspace">
      <article class="panel device-list-panel full-width-panel">
        <div class="device-toolbar">
          <div class="device-toolbar-filters">
            <div class="toolbar-select">{{ searchKeyword ? `匹配 ${devicePagination.total} 台` : "全部设备" }}</div>
            <input
              v-model="searchKeyword"
              class="search-input toolbar-search"
              type="search"
              placeholder="请输入设备分组、设备名称或设备 ID"
            />
          </div>
        </div>

        <div class="device-table">
	          <div class="device-table-head">
	            <span>设备名称 / 备注</span>
	            <span>协议类型</span>
	            <span>区域 / 分组</span>
	            <span>传感器摘要</span>
	            <span>设备状态</span>
	            <span>最后活跃时间</span>
            <span>启用状态</span>
            <span>操作</span>
          </div>

          <div v-if="filteredDevices.length" class="device-table-body">
            <article
              v-for="device in filteredDevices"
              :key="device.device_id"
              class="device-table-row"
            >
              <div class="device-name-cell">
                <strong>{{ device.device_name }}</strong>
                <span>{{ device.device_id }}</span>
              </div>
	              <span class="device-cell-text">
	                {{ device.protocol_type === "modbus_gateway" ? "Modbus 网关" : "HTTP 主动上报" }}
	              </span>
	              <span class="device-cell-text">
	                {{ [device.factory, device.workshop, device.production_line, device.device_group].filter(Boolean).join(" / ") || "未分组" }}
	              </span>
	              <span class="device-cell-text sensor-preview-cell">
                {{ sensorPreviewMap[device.device_id] || "暂未配置传感器" }}
                <small v-if="device.sensor_count">共 {{ device.sensor_count }} 个</small>
              </span>
              <span class="device-status-cell" :data-online="device.online_status">
                <i class="status-dot" />
                {{ device.online_status ? "在线" : "离线" }}
              </span>
              <span class="device-cell-text">
                {{ formatDateTime(device.last_active_time) }}
              </span>
	              <span class="enable-pill" :data-maintenance="device.is_maintenance">
	                {{ device.is_maintenance ? "维护中" : device.is_active ? "已启用" : "已停用" }}
	              </span>
              <div class="device-ops-cell">
                <button class="table-link" type="button" @click="openDeviceDetail(device.device_id)">查看</button>
                <button class="table-link danger-link" type="button" @click="deleteDevice(device.device_id)">删除</button>
              </div>
            </article>
          </div>

          <div v-else class="empty-state device-table-empty">没有匹配的设备记录。</div>
        </div>
        <div class="history-pagination">
          <span>共 {{ devicePagination.total }} 台设备</span>
        </div>
      </article>
    </section>

    <section v-else-if="selectedDevice" class="detail-page">
      <template v-if="detailTab === 'detail'">
      <article class="panel detail-header-panel">
        <div class="panel-head detail-head">
          <div>
            <p class="mini-label">设备详情</p>
            <h2>{{ selectedDevice.device_name }}</h2>
          </div>
          <div class="toolbar-actions">
            <span class="status-pill" :data-kind="wsStatus === 'connected' ? 'ok' : 'idle'">
              {{ wsStatusLabel }}
            </span>
	            <button class="ghost-btn" type="button" @click="openEditPanel">编辑设备</button>
	            <button class="ghost-btn" type="button" @click="updateSelectedDeviceState({ is_active: !selectedDevice.is_active }, selectedDevice.is_active ? '设备已停用' : '设备已启用')">
	              {{ selectedDevice.is_active ? "停用设备" : "启用设备" }}
	            </button>
	            <button class="ghost-btn" type="button" @click="updateSelectedDeviceState({ is_maintenance: !selectedDevice.is_maintenance }, selectedDevice.is_maintenance ? '已退出维护模式' : '已进入维护模式')">
	              {{ selectedDevice.is_maintenance ? "退出维护" : "维护模式" }}
	            </button>
	            <button class="ghost-btn" type="button" @click="openSensorEditor">新增传感器</button>
          </div>
        </div>

        <div class="device-detail-grid compact-detail-grid">
          <div class="detail-tile compact-detail-tile">
            <span class="mini-label">设备 ID</span>
            <strong>{{ selectedDevice.device_id }}</strong>
          </div>
          <div class="detail-tile compact-detail-tile">
            <span class="mini-label">协议类型</span>
            <strong>{{ selectedDevice.protocol_type === "modbus_gateway" ? "Modbus 网关" : "HTTP 主动上报" }}</strong>
          </div>
	          <div class="detail-tile compact-detail-tile">
	            <span class="mini-label">设备属性</span>
	            <strong>{{ selectedDevice.is_test_device ? "测试设备" : "正式设备" }} · {{ selectedDevice.is_maintenance ? "维护中" : selectedDevice.is_active ? "运行中" : "已停用" }}</strong>
	          </div>
	          <div class="detail-tile compact-detail-tile">
	            <span class="mini-label">区域分组</span>
	            <strong>{{ [selectedDevice.factory, selectedDevice.workshop, selectedDevice.production_line, selectedDevice.device_group].filter(Boolean).join(" / ") || "未分组" }}</strong>
	          </div>
          <div class="detail-tile compact-detail-tile">
            <span class="mini-label">离线阈值</span>
            <strong>{{ selectedDevice.offline_threshold }} 秒</strong>
          </div>
          <div class="detail-tile compact-detail-tile">
            <span class="mini-label">最后活跃</span>
            <strong>{{ formatDateTime(selectedDevice.last_active_time) }}</strong>
          </div>
          <div class="detail-tile compact-detail-tile">
            <span class="mini-label">实时数据状态</span>
            <strong>{{ livePayload ? "已接收" : "等待推送" }}</strong>
          </div>
        </div>

        <div v-if="selectedDevice.description" class="description-box compact-description-box">
          {{ selectedDevice.description }}
        </div>
      </article>

      <section class="detail-content-grid" :class="{ 'editor-visible': sensorEditorOpen }">
        <article class="panel sensor-list-panel">
          <div class="panel-head">
            <div>
              <p class="mini-label">传感器列表</p>
              <h2>实时数据与配置</h2>
            </div>
          </div>

          <div class="sensor-card-list sensor-live-list">
            <div v-for="sensor in sensorRows" :key="sensor.id" class="sensor-card sensor-live-card">
              <div class="sensor-live-main">
                <div class="sensor-live-info">
                  <strong>{{ sensor.field_cn }}</strong>
                  <div class="sensor-live-meta-grid">
                    <span>字段 {{ sensor.field_en }}</span>
                    <span>通道 {{ sensor.channel_no ?? "-" }}</span>
                    <span>单位 {{ sensor.unit || "无单位" }}</span>
                  </div>
                </div>
                <div class="sensor-live-reading">
                  <span class="mini-label">实时值</span>
                  <strong>{{ sensor.realtimeValue ?? "--" }}{{ sensor.unit || "" }}</strong>
                </div>
              </div>
              <div class="sensor-live-footer">
                <div class="sensor-threshold-row">
                  <span>下限 {{ sensor.lower_threshold ?? "-" }}</span>
                  <span>上限 {{ sensor.upper_threshold ?? "-" }}</span>
                  <span>系数 {{ sensor.coeff ?? 1 }}</span>
                </div>
                <div class="toolbar-actions">
                  <button class="ghost-btn" type="button" @click="openHistoryModal(sensor)">历史数据</button>
                  <button class="ghost-btn" type="button" @click="startEditSensor(sensor)">编辑</button>
                  <button class="inline-danger" type="button" @click="deleteSensor(sensor.id)">删除</button>
                </div>
              </div>
            </div>
            <div v-if="!sensorRows.length" class="empty-state">该设备暂未配置传感器。</div>
          </div>
        </article>

        <article v-if="sensorEditorOpen" class="panel sensor-editor-panel">
          <div class="panel-head">
            <div>
              <p class="mini-label">传感器编辑</p>
              <h2>{{ editingSensorId ? "编辑传感器" : "新增传感器" }}</h2>
            </div>
            <button class="ghost-btn" type="button" @click="closeSensorEditor">收起</button>
          </div>

          <form class="form-grid" @submit.prevent="saveSensor">
            <label>
              <span>字段英文名</span>
              <input v-model="sensorForm.field_en" type="text" required />
            </label>
            <label>
              <span>字段中文名</span>
              <input v-model="sensorForm.field_cn" type="text" required />
            </label>
            <label>
              <span>单位</span>
              <input v-model="sensorForm.unit" type="text" />
            </label>
            <label>
              <span>通道号</span>
              <input v-model="sensorForm.channel_no" type="number" min="0" />
            </label>
            <label>
              <span>系数</span>
              <input v-model="sensorForm.coeff" type="number" step="0.01" required />
            </label>
            <label class="checkbox-field">
              <span>启用监控</span>
              <input v-model="sensorForm.is_monitored" type="checkbox" />
            </label>
            <label>
              <span>下限阈值</span>
              <input v-model="sensorForm.lower_threshold" type="number" step="0.01" />
            </label>
            <label>
              <span>上限阈值</span>
              <input v-model="sensorForm.upper_threshold" type="number" step="0.01" />
            </label>
            <button class="primary-btn full-span" type="submit" :disabled="savingSensor">
              {{ savingSensor ? "提交中..." : editingSensorId ? "保存传感器" : "新增传感器" }}
            </button>
          </form>
        </article>
      </section>
      </template>

	      <section v-else-if="detailTab === 'history'" class="panel history-page-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">历史数据</p>
            <h2>{{ historySensor?.field_cn || selectedDevice.device_name }}</h2>
          </div>
          <button class="ghost-btn" type="button" @click="detailTab = 'detail'">返回设备详情</button>
        </div>

        <div class="form-grid history-filter-grid device-history-filter-grid">
          <label class="history-sensor-select">
            <span>传感器</span>
            <select
              v-model="historySensor"
              @change="historyPagination.page = 1; loadSensorHistory()"
            >
              <option v-for="sensor in sensors" :key="sensor.id" :value="sensor">
                {{ sensor.field_cn }} · {{ sensor.field_en }}
              </option>
            </select>
          </label>
          <div class="history-date-range">
            <label>
              <span>开始时间</span>
              <input v-model="historyRange.start" type="datetime-local" />
            </label>
            <label>
              <span>结束时间</span>
              <input v-model="historyRange.end" type="datetime-local" />
            </label>
          </div>
          <label class="history-view-select">
            <span>展示方式</span>
            <select v-model="historyView">
              <option value="table">表格</option>
              <option value="chart">简图</option>
            </select>
          </label>
          <button class="primary-btn" type="button" @click="historyPagination.page = 1; loadSensorHistory()" :disabled="historyLoading">
            {{ historyLoading ? "查询中..." : "查询历史数据" }}
          </button>
        </div>

        <div v-if="historyView === 'table'" class="history-table-wrap">
          <table class="history-table">
            <thead>
              <tr>
                <th>设备时间</th>
                <th>平台时间</th>
                <th>值</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="record in historyRecords" :key="record.id">
                <td>{{ formatDateTime(record.reported_at) }}</td>
                <td>{{ formatDateTime(record.created_at) }}</td>
                <td>{{ record.value }}</td>
              </tr>
              <tr v-if="!historyRecords.length">
                <td colspan="3" class="empty-state">当前时间范围没有历史记录。</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-else class="chart-card small-chart-card">
          <svg v-if="deviceHistoryChart" :viewBox="`0 0 ${deviceHistoryChart.width} ${deviceHistoryChart.height}`" class="history-mini-chart device-history-chart">
            <defs>
              <linearGradient id="device-history-gradient" x1="0" x2="0" y1="0" y2="1">
                <stop offset="0%" stop-color="#16c7c0" stop-opacity="0.22" />
                <stop offset="100%" stop-color="#16c7c0" stop-opacity="0.02" />
              </linearGradient>
            </defs>
            <g v-for="tick in deviceHistoryChart.yTicks" :key="`device-y-${tick.y}`">
              <line
                :x1="deviceHistoryChart.padding.left"
                :x2="deviceHistoryChart.width - deviceHistoryChart.padding.right"
                :y1="tick.y"
                :y2="tick.y"
                class="history-grid-line"
              />
              <text :x="deviceHistoryChart.padding.left - 8" :y="tick.y + 4" class="history-axis-label history-axis-label-y">
                {{ tick.value }}
              </text>
            </g>
            <g v-for="tick in deviceHistoryChart.xTicks" :key="`device-x-${tick.x}`">
              <line
                :x1="tick.x"
                :x2="tick.x"
                :y1="deviceHistoryChart.padding.top"
                :y2="deviceHistoryChart.height - deviceHistoryChart.padding.bottom"
                class="history-grid-line history-grid-line-vertical"
              />
              <text :x="tick.x" :y="deviceHistoryChart.height - 14" class="history-axis-label history-axis-label-x">
                {{ tick.label }}
              </text>
            </g>
            <path :d="deviceHistoryChart.areaPath" fill="url(#device-history-gradient)" class="history-area-fill" />
            <path class="history-trend-line" :d="deviceHistoryChart.path" />
            <circle
              v-for="point in deviceHistoryChart.points"
              :key="`${point.label}-${point.x}`"
              :cx="point.x"
              :cy="point.y"
              r="4"
              class="history-trend-point"
            />
          </svg>
          <div v-else class="empty-state">当前数据不足以绘制简图。</div>
        </div>

        <div class="history-pagination">
          <button class="ghost-btn" type="button" @click="prevHistoryPage" :disabled="historyPagination.page === 1 || historyLoading">
            上一页
          </button>
          <span>第 {{ historyPagination.page }} / {{ totalHistoryPages() }} 页 · 共 {{ historyPagination.total }} 条</span>
          <button class="ghost-btn" type="button" @click="nextHistoryPage" :disabled="historyPagination.page >= totalHistoryPages() || historyLoading">
            下一页
          </button>
        </div>
	      </section>

	      <section v-else class="panel history-page-panel">
	        <div class="panel-head">
	          <div>
	            <p class="mini-label">接入文档</p>
	            <h2>{{ selectedDevice.protocol_type === "http_active" ? "HTTP 主动上报示例" : "Modbus 接入参数" }}</h2>
	          </div>
	          <button class="ghost-btn" type="button" @click="detailTab = 'detail'">返回设备详情</button>
	        </div>

	        <div v-if="accessDocs" class="access-doc-grid">
	          <article class="access-doc-card">
	            <span class="mini-label">上报地址</span>
	            <code>{{ accessDocs.reportUrl }}</code>
	          </article>
	          <article class="access-doc-card">
	            <span class="mini-label">JSON 示例</span>
	            <pre>{{ accessDocs.json }}</pre>
	          </article>
	          <article class="access-doc-card">
	            <span class="mini-label">curl 示例</span>
	            <pre>{{ accessDocs.curl }}</pre>
	          </article>
	        </div>

	        <div v-else class="access-doc-grid">
	          <article class="access-doc-card">
	            <span class="mini-label">网关 IP</span>
	            <code>{{ selectedDevice.gateway_ip || "未配置" }}</code>
	          </article>
	          <article class="access-doc-card">
	            <span class="mini-label">从站 ID</span>
	            <code>{{ selectedDevice.slave_id ?? "未配置" }}</code>
	          </article>
	          <article class="access-doc-card">
	            <span class="mini-label">寄存器说明</span>
	            <pre>按传感器通道号从 holding register 0 开始批量读取，最终值 = 原始寄存器值 × 系数。</pre>
	          </article>
	        </div>
	      </section>
	    </section>

    <section v-if="panelOpen" class="panel device-create-panel">
        <div class="panel-head">
          <div>
            <p class="mini-label">{{ panelMode === "create" ? "添加新设备" : "编辑设备" }}</p>
            <h2>{{ panelMode === "create" ? "设备基础信息与传感器配置" : "修改设备基础信息" }}</h2>
          </div>
          <button v-if="panelMode === 'edit'" class="ghost-btn" type="button" @click="closePanel">关闭</button>
        </div>

        <div v-if="panelMode === 'create'" class="wizard-steps">
          <button class="device-tab" :class="{ active: createStep === 'protocol' }" type="button" @click="createStep = 'protocol'">协议</button>
          <button class="device-tab" :class="{ active: createStep === 'device' }" type="button" @click="createStep = 'device'">设备</button>
          <button class="device-tab" :class="{ active: createStep === 'sensors' }" type="button" @click="createStep = 'sensors'">传感器</button>
        </div>

        <form class="form-grid" @submit.prevent="submitDeviceForm">
          <label v-if="panelMode === 'edit' || createStep === 'device'">
            <span>设备 ID</span>
            <input v-model="deviceForm.device_id" type="text" :disabled="panelMode === 'edit'" required />
          </label>
          <label v-if="panelMode === 'edit' || createStep === 'device'">
            <span>设备名称</span>
            <input v-model="deviceForm.device_name" type="text" required />
          </label>
          <label v-if="panelMode === 'edit' || createStep === 'protocol'">
            <span>协议类型</span>
            <select v-model="deviceForm.protocol_type">
              <option value="http_active">HTTP 主动上报</option>
              <option value="modbus_gateway">Modbus 网关</option>
            </select>
          </label>
	          <label v-if="panelMode === 'edit' || createStep === 'protocol'">
	            <span>离线阈值</span>
	            <input v-model="deviceForm.offline_threshold" type="number" min="1" required />
	          </label>
	          <label v-if="panelMode === 'edit' || createStep === 'device'">
	            <span>工厂</span>
	            <input v-model="deviceForm.factory" type="text" placeholder="例如 一厂" />
	          </label>
	          <label v-if="panelMode === 'edit' || createStep === 'device'">
	            <span>车间</span>
	            <input v-model="deviceForm.workshop" type="text" placeholder="例如 注塑车间" />
	          </label>
	          <label v-if="panelMode === 'edit' || createStep === 'device'">
	            <span>产线</span>
	            <input v-model="deviceForm.production_line" type="text" placeholder="例如 A 线" />
	          </label>
	          <label v-if="panelMode === 'edit' || createStep === 'device'">
	            <span>设备分组</span>
	            <input v-model="deviceForm.device_group" type="text" placeholder="例如 温控系统" />
	          </label>
	          <label v-if="panelMode === 'edit' || createStep === 'protocol'" class="checkbox-field">
	            <span>启用设备</span>
	            <input v-model="deviceForm.is_active" type="checkbox" />
	          </label>
	          <label v-if="panelMode === 'edit' || createStep === 'protocol'" class="checkbox-field">
	            <span>维护模式</span>
	            <input v-model="deviceForm.is_maintenance" type="checkbox" />
	          </label>
	          <label v-if="deviceForm.protocol_type === 'modbus_gateway' && (panelMode === 'edit' || createStep === 'device')">
            <span>网关 IP</span>
            <input v-model="deviceForm.gateway_ip" type="text" required />
          </label>
          <label v-if="deviceForm.protocol_type === 'modbus_gateway' && (panelMode === 'edit' || createStep === 'device')">
            <span>从站 ID</span>
            <input v-model="deviceForm.slave_id" type="number" min="1" max="247" required />
          </label>
          <label v-if="deviceForm.protocol_type === 'modbus_gateway' && (panelMode === 'edit' || createStep === 'protocol')" class="checkbox-field">
            <span>测试设备</span>
            <input v-model="deviceForm.is_test_device" type="checkbox" />
          </label>
          <label v-if="panelMode === 'edit' || createStep === 'device'" class="full-span">
            <span>描述</span>
            <textarea v-model="deviceForm.description" rows="3" />
          </label>

          <div v-if="panelMode === 'create' && createStep === 'protocol'" class="wizard-help full-span">
            <strong>{{ deviceForm.protocol_type === "http_active" ? "HTTP 主动上报" : "Modbus 网关轮询" }}</strong>
            <span>
              {{ deviceForm.protocol_type === "http_active" ? "设备创建后会返回密钥，设备端使用设备 ID、密钥和数据字段进行上报。" : "Modbus 设备需要配置网关 IP、从站 ID、通道号和换算系数。" }}
            </span>
          </div>

          <div v-if="panelMode === 'create' && createStep === 'sensors'" class="draft-sensor-block full-span">
            <div class="panel-head">
              <div>
                <p class="mini-label">传感器配置</p>
                <h2>创建时一并提交</h2>
              </div>
              <button class="ghost-btn" type="button" @click="appendDraftSensor">添加传感器</button>
            </div>

            <div class="draft-list">
              <div
                v-for="(sensor, index) in draftSensors"
                :key="`draft-${index}`"
                class="draft-editor-card"
              >
                <div class="panel-head">
                  <div>
                    <p class="mini-label">传感器 {{ index + 1 }}</p>
                  </div>
                  <button class="inline-danger" type="button" @click="removeDraftSensor(index)">移除</button>
                </div>

                <div class="form-grid">
                  <label>
                    <span>字段英文名</span>
                    <input v-model="sensor.field_en" type="text" />
                  </label>
                  <label>
                    <span>字段中文名</span>
                    <input v-model="sensor.field_cn" type="text" />
                  </label>
                  <label>
                    <span>单位</span>
                    <input v-model="sensor.unit" type="text" />
                  </label>
                  <label>
                    <span>通道号</span>
                    <input v-model="sensor.channel_no" type="number" min="0" />
                  </label>
                  <label>
                    <span>系数</span>
                    <input v-model="sensor.coeff" type="number" step="0.01" />
                  </label>
                  <label class="checkbox-field">
                    <span>启用监控</span>
                    <input v-model="sensor.is_monitored" type="checkbox" />
                  </label>
                  <label>
                    <span>下限阈值</span>
                    <input v-model="sensor.lower_threshold" type="number" step="0.01" />
                  </label>
                  <label>
                    <span>上限阈值</span>
                    <input v-model="sensor.upper_threshold" type="number" step="0.01" />
                  </label>
                </div>
              </div>
            </div>
          </div>

          <div v-if="panelMode === 'create'" class="wizard-actions full-span">
            <button class="ghost-btn" type="button" @click="createStep = createStep === 'sensors' ? 'device' : 'protocol'" :disabled="createStep === 'protocol'">
              上一步
            </button>
            <button v-if="createStep !== 'sensors'" class="primary-btn" type="button" @click="createStep = createStep === 'protocol' ? 'device' : 'sensors'">
              下一步
            </button>
            <button v-else class="primary-btn" type="submit" :disabled="savingDevice">
              {{ savingDevice ? "提交中..." : "创建设备" }}
            </button>
          </div>
          <button v-else class="primary-btn full-span" type="submit" :disabled="savingDevice">
            {{ savingDevice ? "提交中..." : panelMode === "create" ? "创建设备" : "保存设备" }}
          </button>
        </form>
    </section>

    <div v-if="confirmDialog.open" class="modal-backdrop" @click.self="closeConfirm(false)">
      <article class="confirm-modal">
        <div>
          <p class="mini-label">操作确认</p>
          <h2>{{ confirmDialog.title }}</h2>
        </div>
        <p class="muted-text">{{ confirmDialog.message }}</p>
        <div class="toolbar-actions confirm-actions">
          <button class="ghost-btn" type="button" @click="closeConfirm(false)">取消</button>
          <button class="primary-btn" :class="{ 'danger-btn': confirmDialog.danger }" type="button" @click="closeConfirm(true)">
            {{ confirmDialog.confirmText }}
          </button>
        </div>
      </article>
    </div>

    <div v-if="notifyDialog.open" class="modal-backdrop" @click.self="notifyDialog.open = false">
      <article class="confirm-modal">
        <div>
          <p class="mini-label">错误提示</p>
          <h2>{{ notifyDialog.title }}</h2>
        </div>
        <p class="muted-text">{{ notifyDialog.message }}</p>
        <div class="toolbar-actions confirm-actions">
          <button class="primary-btn" type="button" @click="notifyDialog.open = false">知道了</button>
        </div>
      </article>
    </div>
  </section>
</template>
