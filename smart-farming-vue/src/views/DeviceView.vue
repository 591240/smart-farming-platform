<script setup lang="ts">
// 设备管理页 — F03 FR-01/02/09（06 规范 4.7）+ F09 FR-04/06
// - 设备表格（名称/类型图标/来源/绑定圈舍/在线状态点/最近心跳/启停/操作）
// - [添加设备] 单入口；mqtt_topic 服务端自动生成
// - 环境数据只来自真实设备：EMQX → MQTT 桥接（backend/app/services/mqtt_bridge.py），
//   或设备带 X-Device-Key 直连 /device/ingest。2026-10-23 移除浏览器造数引擎与"模拟注入"下拉。
// - F09 摄像头：[预览] 占位弹窗（真实拉流待 mediamtx 接入）+ [注入演示事件]
//   演示入口保留但显式标注「演示注入」，产生的告警带「（模拟）」前缀，不与真实识别混淆。

import { computed, nextTick, ref, watch } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { Cpu, Camera, ChevronDown, Plus, Trash2, Activity, X, Syringe } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { useUserFarms, useUserPens } from '@/composables/use-farm-data';
import {
  apiCreateDevice,
  apiDeleteDevice,
  apiListDevices,
  apiRotateDeviceSecret,
  apiUpdateDevice,
  type DeviceItem,
} from '@/api/device-api';
import {
  apiInjectVisionEvent,
  apiListVisionEvents,
  VISION_EVENT_TYPES,
  VISION_EVENT_META,
  VISION_DISCLAIMER,
  type VisionEventType,
  type VisionAnomaly,
} from '@/api/vision-api';
import { ApiError } from '@/lib/api-contract';

function relTime(iso: string | null): string {
  if (!iso) return '从未上报';
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 10_000) return '刚刚';
  if (diff < 60_000) return `${Math.floor(diff / 1000)} 秒前`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  return `${Math.floor(diff / 3_600_000)} 小时前`;
}

function dangerEnter(e: MouseEvent) {
  (e.currentTarget as HTMLElement).style.color = 'var(--danger)';
}

function dangerLeave(e: MouseEvent) {
  (e.currentTarget as HTMLElement).style.color = '';
}

const inputCls =
  'w-full h-10 px-3 rounded-[10px] bg-card border border-border-strong text-[14px] text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-colors';

const DEVICE_TYPE_OPTIONS = [
  { v: 'sensor', label: '环境传感器', desc: '温度/湿度/氨气/二氧化碳' },
  { v: 'camera', label: '摄像头', desc: '预览与视觉事件（阶段 9）' },
] as const;

const LEVEL_STYLE: Record<'low' | 'mid' | 'high', { label: string; color: string; bg: string }> = {
  high: { label: '高', color: 'var(--danger)', bg: 'rgba(217,83,79,.06)' },
  mid: { label: '中', color: 'var(--warning)', bg: 'rgba(232,162,61,.07)' },
  low: { label: '低', color: 'var(--success)', bg: 'rgba(46,158,107,.07)' },
};

const { farms } = useUserFarms();
const queryClient = useQueryClient();
const { data, isLoading: loading } = useQuery({
  queryKey: ['devices'],
  queryFn: async () => (await apiListDevices()).items,
  refetchInterval: 5_000, // 在线状态/心跳随引擎 tick 刷新
});
const devices = computed(() => data.value ?? []);

const addOpen = ref(false);
const confirmDelete = ref<DeviceItem | null>(null);
const toast = ref<string | null>(null);
// F09：视觉注入弹窗与 [预览] 模拟弹窗（仅 camera 行可开）
const injectTarget = ref<DeviceItem | null>(null);
const previewTarget = ref<DeviceItem | null>(null);
// C-24：设备上行密钥只在创建/轮换时以明文出现一次（库里只存 sha256），用状态横幅承接
const keyReveal = ref<{ name: string; secret: string; topic: string } | null>(null);
const keyRevealExample = computed(() => {
  const k = keyReveal.value;
  if (!k) return '';
  const sn = k.topic.split('/')[3] || '{设备编号}';
  return JSON.stringify({
    msg_id: 'demo-001',
    device_id: sn,
    topic: k.topic || '{topic}',
    metrics: { temperature: 25.5, humidity: 60 },
  });
});

const cancelDeleteBtn = ref<HTMLButtonElement | null>(null);

function showToast(msg: string) {
  toast.value = msg;
  window.setTimeout(() => (toast.value = null), 2600);
}

function reload() {
  queryClient.invalidateQueries({ queryKey: ['devices'] });
}

async function rotateKey(d: DeviceItem) {
  try {
    const r = await apiRotateDeviceSecret(d.device_id);
    keyReveal.value = { name: d.device_name, secret: r.device_secret, topic: d.mqtt_topic };
    showToast('设备密钥已重签发，旧密钥立即失效');
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '网络异常，请重试');
  }
}

async function handleDelete() {
  if (!confirmDelete.value) return;
  try {
    await apiDeleteDevice(confirmDelete.value.device_id);
    confirmDelete.value = null;
    reload();
    showToast('设备已删除，历史数据保留');
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '网络异常，请重试');
  }
}

async function toggleEnable(d: DeviceItem) {
  try {
    await apiUpdateDevice(d.device_id, { is_enable: !d.is_enable });
    reload();
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '网络异常，请重试');
  }
}

function isOnline(d: DeviceItem) {
  return d.online_status === 'online' && d.is_enable;
}

// ===== 添加设备弹窗（状态置于页面，打开时按初值重置）=====
const addFarmId = ref<number | ''>('');
const { pens: addPens } = useUserPens(
  computed(() => (addOpen.value && typeof addFarmId.value === 'number' ? addFarmId.value : null)),
);
const addPenId = ref<number | ''>('');
const addType = ref<'sensor' | 'camera'>('sensor');
const addName = ref('');
const addSn = ref('');
const addRtsp = ref('');
const addErr = ref<string | null>(null);
const addLoading = ref(false);
const connGuideOpen = ref(false);

// 真机接入说明（07 协议）：HTTP 直连公网可用；MQTT 需 EMQX 开放 1883 并为设备建认证
const ingestUrl = `${window.location.origin}/api/device/ingest`;
const topicPreview = computed(
  () =>
    `farm/${addFarmId.value || '{farm_id}'}/pen/${addPenId.value || '{pen_id}'}/${addSn.value.trim() || '{设备编号}'}/data`,
);
const ingestExample = computed(() =>
  JSON.stringify(
    {
      msg_id: 'demo-001',
      device_id: addSn.value.trim() || '{设备编号}',
      topic: topicPreview.value,
      metrics: { temperature: 25.5, humidity: 60, nh3: 10, co2: 800 },
    },
    null,
    0,
  ),
);

function openAddDialog() {
  addFarmId.value = farms.value[0]?.farm_id ?? '';
  addPenId.value = '';
  addType.value = 'sensor';
  addName.value = '';
  addSn.value = `DEV${String(Date.now()).slice(-6)}`;
  addRtsp.value = '';
  addErr.value = null;
  addLoading.value = false;
  connGuideOpen.value = true; // 传感器不需要填地址，接入方式对新手不直观——默认展开说明
  addOpen.value = true;
}

// 切到摄像头收起说明（它填 RTSP 地址即可），切回传感器自动展开
watch(addType, (t) => {
  connGuideOpen.value = t === 'sensor';
});

watch(
  [addPens, addPenId, addOpen],
  ([pens, penId]) => {
    if (!addOpen.value) return;
    if (pens.length > 0 && !pens.some((p) => p.pen_id === penId)) addPenId.value = pens[0].pen_id;
  },
  { immediate: true },
);

function onAddFarmChange(e: Event) {
  addFarmId.value = Number((e.target as HTMLSelectElement).value);
}

function onAddPenChange(e: Event) {
  addPenId.value = Number((e.target as HTMLSelectElement).value);
}

function selectAddType(v: 'sensor' | 'camera') {
  addType.value = v;
  addErr.value = null; // 切换类型时清掉上一次提交残留的报错
}

async function submitAdd() {
  addErr.value = null;
  if (!addName.value.trim()) {
    addErr.value = '请填写设备名称';
    return;
  }
  if (!addPenId.value) {
    addErr.value = '请选择绑定圈舍';
    return;
  }
  addLoading.value = true;
  try {
    const created = await apiCreateDevice({
      farm_id: addFarmId.value as number,
      pen_id: addPenId.value as number,
      device_type: addType.value,
      device_name: addName.value.trim(),
      device_sn: addSn.value.trim(),
      ...(addType.value === 'camera' ? { rtsp_url: addRtsp.value.trim() } : {}),
    });
    handleCreated(created.device_secret, addName.value.trim(), created.mqtt_topic);
  } catch (e) {
    addErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    addLoading.value = false;
  }
}

function handleCreated(secret?: string, dname?: string, topic?: string) {
  addOpen.value = false;
  if (secret) keyReveal.value = { name: dname ?? '新设备', secret, topic: topic ?? '' };
  reload();
  showToast('设备已注册，上行密钥只显示这一次，请立即保存');
}

// ===== F09 FR-04 视觉事件模拟注入（vision_sim 页面等价物：类型选择 + 置信度滑条 + 异常演练）=====
const injectEventType = ref<VisionEventType>('huddle');
const injectConfidence = ref(0.87);
const injectAnomaly = ref<VisionAnomaly>('none');
const injectSubmitting = ref(false);

function openInject(d: DeviceItem) {
  injectEventType.value = 'huddle';
  injectConfidence.value = 0.87;
  injectAnomaly.value = 'none';
  injectSubmitting.value = false;
  injectTarget.value = d;
}

async function submitInject() {
  if (!injectTarget.value) return;
  injectSubmitting.value = true;
  try {
    const res = await apiInjectVisionEvent({
      device_id: injectTarget.value.device_id,
      event_type: injectEventType.value,
      confidence: injectConfidence.value,
      anomaly: injectAnomaly.value,
    });
    handleInjectResult(res.message);
    if (res.ok) injectTarget.value = null;
  } catch (e) {
    handleInjectResult(e instanceof ApiError ? e.message : '注入失败，请重试');
  } finally {
    injectSubmitting.value = false;
  }
}

function handleInjectResult(msg: string) {
  showToast(msg);
  queryClient.invalidateQueries({ queryKey: ['vision-events'] });
  queryClient.invalidateQueries({ queryKey: ['dashboard-alerts'] });
}

function onConfidenceInput(e: Event) {
  injectConfidence.value = Number((e.target as HTMLInputElement).value);
}

function onAnomalyChange(e: Event) {
  injectAnomaly.value = (e.target as HTMLSelectElement).value as VisionAnomaly;
}

const injectLevelHint = computed(() =>
  injectAnomaly.value === 'low_confidence'
    ? '演练：低置信将丢弃'
    : injectEventType.value === 'death'
      ? injectConfidence.value >= 0.5
        ? 'death ≥0.5 → high（人工核实引导）'
        : 'death <0.5 → 丢弃'
      : injectConfidence.value >= 0.7
        ? '≥0.7 → mid 告警'
        : '[0.5, 0.7) → low 提示',
);

// ===== F09 FR-06 [预览] 模拟模式（06 规范 4.7：视频区占位 + 事件时间线 + 免责文案）=====
// 弹窗状态置于页面，关闭时用 enabled 还原「仅打开时取数/轮询」。
const {
  data: previewData,
  isLoading: previewLoading,
} = useQuery({
  queryKey: computed(() => ['vision-events', previewTarget.value?.pen_id ?? 0]),
  queryFn: () => apiListVisionEvents({ pen_id: previewTarget.value!.pen_id, page_size: 30 }),
  refetchInterval: 10_000,
  enabled: computed(() => !!previewTarget.value),
});
const previewEvents = computed(() =>
  (previewData.value?.items ?? []).filter((e) => e.device_sn === previewTarget.value?.device_sn),
);

function openPreview(d: DeviceItem) {
  previewTarget.value = d;
}

watch(confirmDelete, (v) => {
  if (v) nextTick(() => cancelDeleteBtn.value?.focus());
});
</script>

<template>
  <div class="px-8 py-6 max-w-[1400px]">
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">设备管理</h1>
        <p class="text-[14px] text-text-secondary mt-0.5">
          共 {{ devices.length }} 台设备 ·
          {{ devices.filter((d) => d.online_status === 'online' && d.is_enable).length }} 台在线
        </p>
      </div>
      <div class="flex items-center gap-2.5">
        <button
          @click="openAddDialog"
          class="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-[10px] text-[14px] font-medium hover:bg-primary-600 transition-colors"
        >
          <Plus :size="16" :stroke-width="1.5" />
          添加设备
        </button>
      </div>
    </div>

    <div v-if="keyReveal" class="mb-5 rounded-[12px] border border-primary-100 bg-primary-50/60 px-5 py-4">
      <div class="flex items-start justify-between gap-4">
        <div class="min-w-0">
          <p class="text-[13.5px] font-medium text-primary-700 mb-1.5">
            「{{ keyReveal.name }}」设备上行密钥 —— 只显示这一次，库里仅存 sha256
          </p>
          <code
            class="block truncate text-[13px] tnum text-text-primary bg-card border border-border rounded-[8px] px-3 py-2 select-all"
          >
            {{ keyReveal.secret }}
          </code>
          <div class="text-[12.5px] text-text-secondary mt-2 leading-[1.7] space-y-1.5">
            <p>
              真实硬件上报（无需农户登录）：
              <code class="px-1 rounded bg-card">POST {{ ingestUrl }}</code>，请求头
              <code class="px-1 rounded bg-card break-all">X-Device-Key: {{ keyReveal.secret }}</code>
            </p>
            <p v-if="keyReveal.topic">
              MQTT 通道主题 <code class="px-1 rounded bg-card tnum">{{ keyReveal.topic }}</code>（QoS 1，需 EMQX 开放 1883），payload 同下。
            </p>
            <code
              class="block px-3 py-2 rounded-[8px] bg-card border border-border text-[11.5px] tnum text-text-primary break-all select-all"
            >{{ keyRevealExample }}</code>
            <p>密钥丢了用行内「重签发密钥」，旧值立即失效（07 协议 3.3 / 03-API 7.4）。</p>
          </div>
        </div>
        <button
          @click="keyReveal = null"
          aria-label="关闭密钥提示"
          class="shrink-0 p-1.5 rounded-[8px] text-text-tertiary hover:bg-bg-subtle transition-colors"
        >
          <X :size="16" :stroke-width="1.5" />
        </button>
      </div>
    </div>

    <!-- 设备表格（<768 转卡片单列） -->
    <div v-if="loading" class="bg-card rounded-[12px] p-6 card-highlight skeleton-shimmer h-[240px]" />
    <div
      v-else-if="devices.length === 0"
      class="bg-card rounded-[12px] p-14 card-highlight flex flex-col items-center text-center"
    >
      <div class="w-16 h-16 rounded-full bg-primary-50 flex items-center justify-center mb-5">
        <Cpu :size="28" :stroke-width="1.5" class="text-primary" />
      </div>
      <h3 class="text-[16px] font-semibold text-text-primary mb-2">还没有设备</h3>
      <p class="text-[13.5px] text-text-secondary mb-6 max-w-[400px] leading-[1.7]">
        添加环境节点后，设备经 MQTT（EMQX）或 X-Device-Key 直连上报温度/湿度/氨气/二氧化碳，
        数据异常将自动产生告警并推送。没有设备时看板曲线为空态。
      </p>
      <button
        @click="openAddDialog"
        class="flex items-center gap-2 px-5 py-2.5 bg-primary text-white rounded-[10px] text-[14px] font-medium hover:bg-primary-600 transition-colors"
      >
        <Plus :size="16" :stroke-width="1.5" />
        添加设备
      </button>
    </div>
    <div v-else class="space-y-2.5">
      <div
        v-for="d in devices"
        :key="d.device_id"
        :class="
          cn(
            'flex flex-wrap items-center gap-x-4 gap-y-2 bg-card rounded-[12px] px-5 py-3.5 card-highlight border',
            !isOnline(d) && d.is_enable ? 'border-transparent' : 'border-transparent',
          )
        "
        :style="!isOnline(d) && d.is_enable ? { backgroundColor: 'rgba(232,162,61,.04)' } : undefined"
      >
        <!-- 类型图标 -->
        <div class="w-9 h-9 rounded-[10px] bg-bg-subtle flex items-center justify-center shrink-0">
          <Activity v-if="d.device_type === 'sensor'" :size="17" class="text-primary" :stroke-width="1.5" />
          <Camera v-else :size="17" class="text-primary" :stroke-width="1.5" />
        </div>

        <!-- 名称 + SN -->
        <div class="min-w-[140px] flex-1">
          <div class="flex items-center gap-2">
            <span class="text-[14px] font-medium text-text-primary truncate">{{ d.device_name }}</span>
            <span class="px-1.5 py-0.5 rounded-[4px] bg-bg-subtle text-text-tertiary text-[11px]">
              {{ d.source === 'sim' ? '模拟' : '真实' }}
            </span>
          </div>
          <div class="text-[12px] text-text-tertiary tnum mt-0.5">{{ d.device_sn }}</div>
        </div>

        <!-- 绑定圈舍 -->
        <div class="text-[13px] text-text-secondary min-w-[80px]">{{ d.pen_name }}</div>

        <!-- 在线状态 -->
        <div class="flex items-center gap-1.5 min-w-[110px]">
          <span :class="cn('w-2 h-2 rounded-full', isOnline(d) ? 'bg-success animate-breathe' : 'bg-text-disabled')" />
          <span :class="cn('text-[13px]', isOnline(d) ? 'text-text-secondary' : 'text-text-tertiary')">
            {{ d.is_enable ? (isOnline(d) ? '在线' : `离线（心跳 ${relTime(d.last_heartbeat)}）`) : '已停用' }}
          </span>
        </div>

        <!-- C-24：真实设备上行用的密钥重签发（旧值立即失效，新明文只显示一次） -->
        <button
          v-if="d.device_type === 'sensor'"
          @click="rotateKey(d)"
          class="px-3 py-1.5 rounded-[8px] text-[12.5px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          title="真实设备用 X-Device-Key 上报，无需农户登录（07 协议 3.3）"
        >
          重签发密钥
        </button>

        <!-- 摄像头：[预览] 模拟弹窗 + [注入模拟事件]（F09 FR-04/06，仅 camera 行） -->
        <template v-if="d.device_type !== 'sensor'">
          <button
            @click="openPreview(d)"
            class="px-3 py-1.5 rounded-[8px] text-[13px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          >
            预览
          </button>
          <button
            @click="openInject(d)"
            class="flex items-center gap-1.5 px-3 py-1.5 rounded-[8px] text-[13px] font-medium bg-primary-50 text-primary-700 hover:bg-primary-100 transition-colors"
          >
            <Syringe :size="14" :stroke-width="1.5" />
            注入模拟事件
          </button>
        </template>

        <!-- 启停开关 -->
        <button
          role="switch"
          :aria-checked="d.is_enable"
          :aria-label="`启停 ${d.device_name}`"
          @click="toggleEnable(d)"
          :class="
            cn(
              'relative w-[36px] h-[20px] rounded-full transition-colors shrink-0',
              d.is_enable ? 'bg-primary' : 'bg-border-strong',
            )
          "
        >
          <span
            :class="
              cn(
                'absolute top-[2px] w-4 h-4 rounded-full bg-white transition-all',
                d.is_enable ? 'left-[18px]' : 'left-[2px]',
              )
            "
          />
        </button>

        <!-- 删除 -->
        <button
          @click="confirmDelete = d"
          class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle transition-colors"
          @mouseenter="dangerEnter"
          @mouseleave="dangerLeave"
          :aria-label="`删除 ${d.device_name}`"
        >
          <Trash2 :size="15" :stroke-width="1.5" />
        </button>
      </div>
    </div>

    <!-- ===== 添加设备弹窗 ===== -->
    <div v-if="addOpen" class="fixed inset-0 z-50 flex items-center justify-center" @click="addOpen = false">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[500px] max-w-[90vw] p-6 animate-modal-in max-h-[90vh] overflow-y-auto"
        style="box-shadow: var(--e3)"
        @click.stop
      >
        <div class="flex items-center justify-between mb-1.5">
          <h2 class="text-[16px] font-semibold text-text-primary">添加设备</h2>
          <button
            @click="addOpen = false"
            class="p-1 rounded-[6px] hover:bg-bg-subtle transition-colors"
            aria-label="关闭"
          >
            <X :size="18" class="text-text-tertiary" :stroke-width="1.5" />
          </button>
        </div>
        <p class="text-[12.5px] text-text-tertiary mb-3">
          MQTT 主题由系统按圈舍与设备编号自动生成；创建后返回一次性上行密钥，真实设备凭密钥上报
        </p>

        <!-- 真机接入说明（07 协议） -->
        <div class="mb-5 rounded-[10px] border border-border bg-bg-subtle px-4 py-3">
          <button
            type="button"
            @click="connGuideOpen = !connGuideOpen"
            class="w-full flex items-center justify-between gap-2 text-left"
          >
            <span class="text-[13px] font-medium text-text-primary">真机接入说明（传感器如何连上来）</span>
            <ChevronDown
              :size="15"
              :stroke-width="1.5"
              :class="cn('text-text-tertiary shrink-0 transition-transform', connGuideOpen && 'rotate-180')"
            />
          </button>
          <div v-if="connGuideOpen" class="mt-3 space-y-2.5 text-[12.5px] leading-[1.8] text-text-secondary">
            <p
              class="px-3 py-2 rounded-[8px]"
              style="background-color: rgba(46, 158, 107, 0.07); border: 1px solid rgba(46, 158, 107, 0.18)"
            >
              为什么传感器不用填网址？——摄像头是平台主动去连它拉视频流，所以要填地址；
              温度传感器是反过来<b class="text-text-primary">它主动把读数推给平台</b>，
              平台不用知道它在哪，它只需要下面这把密钥。
            </p>
            <div>
              <p class="font-medium text-text-primary mb-1">① HTTP 直连（推荐，公网直接可用）</p>
              <p>
                硬件（ESP32 / STM32 / 网关）定时 <code class="px-1 rounded bg-card">POST {{ ingestUrl }}</code>，
                请求头带 <code class="px-1 rounded bg-card">X-Device-Key: &lt;上行密钥&gt;</code>，无需农户登录。
              </p>
              <code
                class="block mt-1.5 px-3 py-2 rounded-[8px] bg-card border border-border text-[11.5px] tnum text-text-primary break-all select-all"
              >{{ ingestExample }}</code>
            </div>
            <div>
              <p class="font-medium text-text-primary mb-1">② MQTT（EMQX，服务器 1883 端口）</p>
              <p>
                上报主题 <code class="px-1 rounded bg-card tnum">{{ topicPreview }}</code>（QoS 1），
                payload 同①的 JSON。连接需 MQTT 账号密码（EMQX 内置数据库认证，匿名已禁用），账号找管理员开通。
              </p>
            </div>
            <p>
              指标白名单：<code class="px-1 rounded bg-card">temperature</code> 温度℃ ·
              <code class="px-1 rounded bg-card">humidity</code> 湿度%RH ·
              <code class="px-1 rounded bg-card">nh3</code> 氨气ppm ·
              <code class="px-1 rounded bg-card">co2</code> 二氧化碳ppm（数值 ≥ 0；
              <code class="px-1 rounded bg-card">msg_id</code> 保持唯一用于去重）。
              密钥丢了用列表里的「重签发密钥」，旧值立即失效。
            </p>
          </div>
        </div>

        <label for="dv-name" class="text-[13px] text-text-secondary mb-1.5 block">
          设备名称 <span style="color: var(--danger)">*</span>
        </label>
        <input
          id="dv-name"
          v-model="addName"
          maxlength="30"
          placeholder="如：1号圈环境节点"
          :class="cn(inputCls, 'mb-4')"
        />

        <label class="text-[13px] text-text-secondary mb-1.5 block">设备类型</label>
        <div class="grid grid-cols-2 gap-2 mb-4">
          <button
            v-for="o in DEVICE_TYPE_OPTIONS"
            :key="o.v"
            type="button"
            @click="selectAddType(o.v)"
            :class="
              cn(
                'px-3.5 py-2.5 rounded-[10px] text-left border transition-all',
                addType === o.v ? 'bg-primary-50 border-primary' : 'bg-bg-subtle border-transparent hover:bg-bg-inset',
              )
            "
          >
            <div :class="cn('text-[13.5px] font-medium', addType === o.v ? 'text-primary-700' : 'text-text-primary')">
              {{ o.label }}
            </div>
            <div class="text-[12px] text-text-tertiary mt-0.5">{{ o.desc }}</div>
          </button>
        </div>

        <div class="grid grid-cols-2 gap-3 mb-4">
          <div>
            <label for="dv-farm" class="text-[13px] text-text-secondary mb-1.5 block">养殖场</label>
            <select id="dv-farm" :value="addFarmId" @change="onAddFarmChange" :class="inputCls">
              <option v-for="f in farms" :key="f.farm_id" :value="f.farm_id">{{ f.farm_name }}</option>
            </select>
          </div>
          <div>
            <label for="dv-pen" class="text-[13px] text-text-secondary mb-1.5 block">
              绑定圈舍 <span style="color: var(--danger)">*</span>
            </label>
            <select
              id="dv-pen"
              :value="addPenId"
              @change="onAddPenChange"
              :disabled="addPens.length === 0"
              :class="cn(inputCls, addPens.length === 0 && 'opacity-50')"
            >
              <option v-if="addPens.length === 0" value="">该场暂无圈舍</option>
              <option v-for="p in addPens" :key="p.pen_id" :value="p.pen_id">{{ p.pen_name }}</option>
            </select>
          </div>
        </div>

        <label for="dv-sn" class="text-[13px] text-text-secondary mb-1.5 block">设备编号（全局唯一）</label>
        <input id="dv-sn" v-model="addSn" maxlength="64" :class="cn(inputCls, 'mb-4')" />

        <template v-if="addType === 'camera'">
          <label for="dv-rtsp" class="text-[13px] text-text-secondary mb-1.5 block">RTSP 地址</label>
          <input id="dv-rtsp" v-model="addRtsp" placeholder="rtsp://..." :class="cn(inputCls, 'mb-4')" />
        </template>

        <div
          v-if="addErr"
          class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
          style="background-color: rgba(217,83,79,.07); border: 1px solid rgba(217,83,79,.25); color: var(--danger)"
          role="alert"
        >
          {{ addErr }}
        </div>

        <div class="flex justify-end gap-3">
          <button
            @click="addOpen = false"
            class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          >
            取消
          </button>
          <button
            @click="submitAdd"
            :disabled="addLoading"
            :class="
              cn(
                'px-5 py-2 rounded-[10px] text-[14px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors flex items-center gap-2',
                addLoading && 'opacity-70 cursor-not-allowed',
              )
            "
          >
            <span
              v-if="addLoading"
              class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin"
            />
            {{ addLoading ? '注册中…' : '确认添加' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ===== 删除确认 ===== -->
    <div v-if="confirmDelete" class="fixed inset-0 z-50 flex items-center justify-center" @click="confirmDelete = null">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[420px] max-w-[90vw] p-6 animate-modal-in"
        style="box-shadow: var(--e3)"
        @click.stop
      >
        <h2 class="text-[16px] font-semibold text-text-primary mb-3">删除设备</h2>
        <p class="text-[13.5px] text-text-secondary leading-[1.7]">
          确定删除「{{ confirmDelete.device_name }}」吗？历史采集数据与告警记录将全部保留。
        </p>
        <div class="flex justify-end gap-3 mt-6">
          <button
            ref="cancelDeleteBtn"
            @click="confirmDelete = null"
            class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          >
            取消
          </button>
          <button
            @click="handleDelete"
            class="px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors"
            style="background-color: var(--danger)"
          >
            确认删除
          </button>
        </div>
      </div>
    </div>

    <!-- ===== F09 FR-04 注入模拟事件弹窗 ===== -->
    <div v-if="injectTarget" class="fixed inset-0 z-50 flex items-center justify-center" @click="injectTarget = null">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[460px] max-w-[92vw] p-6 animate-modal-in"
        style="box-shadow: var(--e3)"
        @click.stop
        role="dialog"
        aria-modal="true"
        aria-label="注入模拟事件"
      >
        <div class="flex items-center justify-between mb-1">
          <h2 class="text-[16px] font-semibold text-text-primary">注入模拟事件</h2>
          <button
            @click="injectTarget = null"
            class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle transition-colors"
            aria-label="关闭"
          >
            <X :size="16" :stroke-width="1.5" />
          </button>
        </div>
        <p class="text-[12.5px] text-text-tertiary mb-4">
          {{ injectTarget.device_name }} · {{ injectTarget.pen_name }} ——
          与边缘网关同构 payload，走引擎同一校验链（去重/回表/枚举/分级）
        </p>

        <div class="text-[13px] text-text-secondary mb-2">事件类型</div>
        <div class="flex flex-wrap gap-2 mb-4">
          <button
            v-for="t in VISION_EVENT_TYPES"
            :key="t"
            @click="injectEventType = t"
            :class="
              cn(
                'px-3 py-1.5 rounded-[8px] text-[13px] transition-colors border',
                injectEventType === t
                  ? 'bg-primary-50 text-primary-700 border-primary-300 font-medium'
                  : 'bg-bg-subtle text-text-secondary border-transparent hover:bg-bg-inset',
              )
            "
          >
            {{ VISION_EVENT_META[t].label }}
          </button>
        </div>

        <div class="flex items-center justify-between mb-2">
          <div class="text-[13px] text-text-secondary">置信度</div>
          <div class="text-[13px] text-text-primary font-medium tnum">
            {{ injectConfidence.toFixed(2) }}（{{ Math.round(injectConfidence * 100) }}%）<span
              class="text-[12px] text-text-tertiary ml-2"
              >{{ injectLevelHint }}</span
            >
          </div>
        </div>
        <input
          type="range"
          min="0.3"
          max="0.95"
          step="0.01"
          :value="injectConfidence"
          @input="onConfidenceInput"
          class="w-full mb-4 accent-[var(--primary)]"
          aria-label="置信度"
        />

        <div class="text-[13px] text-text-secondary mb-2">异常演练（TC-D 矩阵）</div>
        <select :value="injectAnomaly" @change="onAnomalyChange" :class="cn(inputCls, 'mb-2')">
          <option value="none">正常注入</option>
          <option value="low_confidence">低置信演练（低于 0.5 丢弃计数）</option>
          <option value="duplicate">重复 msg_id 演练（QoS1 去重）</option>
          <option value="bad_payload">bad_payload 演练（缺字段跳过不崩溃）</option>
        </select>

        <p class="text-[11.5px] text-text-tertiary leading-[1.6] mb-4">
          注入产生的告警将带「（模拟）」标注，避免与真实网关识别混淆；死亡类事件仅提示人工核实，系统不自动调整存栏。
        </p>

        <div class="flex justify-end gap-3">
          <button
            @click="injectTarget = null"
            class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          >
            取消
          </button>
          <button
            @click="submitInject"
            :disabled="injectSubmitting"
            :class="
              cn(
                'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium bg-primary hover:bg-primary-600 transition-colors',
                injectSubmitting && 'opacity-70 cursor-not-allowed',
              )
            "
          >
            {{ injectSubmitting ? '注入中…' : '注入事件' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ===== F09 FR-06 [预览] 模拟模式弹窗（06 规范 4.7：视频区占位 + 事件时间线 + 免责文案） ===== -->
    <div v-if="previewTarget" class="fixed inset-0 z-50 flex items-center justify-center" @click="previewTarget = null">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[12px] w-[560px] max-w-[94vw] max-h-[86vh] overflow-y-auto p-6 animate-modal-in"
        style="box-shadow: var(--e3)"
        @click.stop
        role="dialog"
        aria-modal="true"
        :aria-label="`预览 ${previewTarget.device_name}`"
      >
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-[16px] font-semibold text-text-primary">{{ previewTarget.device_name }} · 预览</h2>
          <button
            @click="previewTarget = null"
            class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle transition-colors"
            aria-label="关闭"
          >
            <X :size="16" :stroke-width="1.5" />
          </button>
        </div>

        <!-- 视频区：模拟占位（决策 4：不模拟视频流；真实拉流随 mediamtx 部署，C-01） -->
        <div
          class="h-[180px] rounded-[10px] flex flex-col items-center justify-center text-center mb-4"
          style="background-color: rgba(20,30,24,0.04); border: 1px dashed var(--border-strong, rgba(20,30,24,.18))"
        >
          <Camera :size="30" :stroke-width="1.5" class="text-text-disabled mb-3" />
          <p class="text-[13.5px] text-text-secondary">模拟模式 · 真实摄像头接入后经 mediamtx 转流播放（WebRTC/HLS）</p>
          <p class="text-[12px] text-text-tertiary mt-1.5">
            设备 SN {{ previewTarget.device_sn }} · RTSP {{ previewTarget.rtsp_url || '—' }} ·
            {{ previewTarget.is_enable ? (previewTarget.online_status === 'online' ? '在线' : '离线') : '已停用' }}
          </p>
        </div>

        <!-- 事件时间线 -->
        <div class="text-[13px] font-medium text-text-primary mb-2">最近视觉事件</div>
        <p v-if="previewLoading" class="text-[13px] text-text-tertiary py-4">加载中…</p>
        <p v-else-if="previewEvents.length === 0" class="text-[13px] text-text-tertiary py-4 text-center">
          暂无事件——可用「注入模拟事件」体验完整链路
        </p>
        <div v-else class="space-y-2 mb-4">
          <div
            v-for="e in previewEvents"
            :key="e.result_id"
            class="flex items-center gap-3 px-3 py-2.5 rounded-[8px]"
            style="background-color: var(--bg-subtle, rgba(20,30,24,.03))"
          >
            <span
              class="px-1.5 py-0.5 rounded-[4px] text-[11px] font-medium shrink-0"
              :style="{ backgroundColor: LEVEL_STYLE[e.level].bg, color: LEVEL_STYLE[e.level].color }"
            >{{ LEVEL_STYLE[e.level].label }}置信</span>
            <span class="text-[13px] text-text-primary font-medium">{{ e.event_label }}</span>
            <span class="text-[12px] text-text-tertiary tnum">{{ Math.round(e.confidence * 100) }}%</span>
            <span class="text-[12px] text-text-tertiary ml-auto shrink-0">{{ relTime(e.collect_time) }}</span>
          </div>
        </div>
        <div v-if="!previewLoading && previewEvents.length > 0" class="mb-1" />

        <!-- 免责固定文案（三处核对之一，与告警卡/预览卡逐字一致） -->
        <p
          class="text-[11.5px] text-text-tertiary leading-[1.6] pt-3"
          style="border-top: 1px solid rgba(20,30,24,.08)"
        >
          {{ VISION_DISCLAIMER }}
        </p>
      </div>
    </div>

    <div
      v-if="toast"
      class="fixed bottom-8 left-1/2 -translate-x-1/2 z-[60] px-4 py-2.5 rounded-[10px] bg-[#2A332C] text-white text-[13px] animate-slide-in"
      style="box-shadow: var(--e3)"
      role="status"
    >
      {{ toast }}
    </div>
  </div>
</template>
