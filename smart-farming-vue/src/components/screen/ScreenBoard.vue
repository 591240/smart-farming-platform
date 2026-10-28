<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import VChart from 'vue-echarts';
import { use } from 'echarts/core';
import { CanvasRenderer } from 'echarts/renderers';
import { BarChart, LineChart } from 'echarts/charts';
import { GridComponent, MarkLineComponent, TooltipComponent } from 'echarts/components';
import {
  Maximize2, MonitorPlay, Activity, Warehouse, Cpu, BellRing, ScrollText, Video,
  Thermometer, Droplets, Wind, AlertTriangle, UtensilsCrossed, Coins, Layers,
} from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { getTokenExpMs } from '@/api/client';
import { apiListFarms, apiListPens } from '@/api/auth-api';
import { apiGetWeather, type WeatherResponse } from '@/api/weather-api';
import { apiListDevices, apiListAlerts, apiSensorHistory } from '@/api/device-api';
import { apiListRecords, type LedgerRecordItem } from '@/api/record-api';
import { apiFeedingOverview, apiGetFeedPlan } from '@/api/feed-api';
import { apiListVisionEvents, VISION_DISCLAIMER } from '@/api/vision-api';
import { subscribeAlerts } from '@/lib/alert-stream';
import SourceBadge from '@/components/screen/SourceBadge.vue';
import KpiCard from '@/components/screen/KpiCard.vue';
import ScreenPanel from '@/components/screen/ScreenPanel.vue';
import StatBlock from '@/components/screen/StatBlock.vue';
import MiniStat from '@/components/screen/MiniStat.vue';
import EmptyHint from '@/components/screen/EmptyHint.vue';

use([CanvasRenderer, BarChart, LineChart, GridComponent, TooltipComponent, MarkLineComponent]);

const props = defineProps<{ variant: 'embedded' | 'projection' }>();

const projection = computed(() => props.variant === 'projection');
const router = useRouter();
const qc = useQueryClient();

// 取数失败必须可见：下面 13 个 useQuery 此前只解构 data，后端一挂页面就继续显示
// 「暂无告警，环境运行平稳」——把故障渲染成正常，是大屏最坏的失败模式。
// 逐个加 isError 既啰嗦又护不住以后新增的查询，所以订阅 query cache 按 screen- 前缀汇总。
const SCREEN_LABELS: Record<string, string> = {
  'screen-farms': '农场', 'screen-pens': '圈舍', 'screen-stock-records': '存栏流水',
  'screen-devices': '设备', 'screen-feed-overview': '饲喂概览', 'screen-feed-plan-cost': '饲喂成本',
  'screen-weather': '气象', 'screen-alerts': '告警', 'screen-vision': '视觉事件',
  'screen-records': '台账', 'screen-env': '环境曲线',
};
const degraded = ref<string[]>([]);
let unsubCache: (() => void) | undefined;
onMounted(() => {
  const sync = () => {
    const names = new Set<string>();
    for (const q of qc.getQueryCache().getAll()) {
      const first = Array.isArray(q.queryKey) ? q.queryKey[0] : undefined;
      if (q.state.status === 'error' && typeof first === 'string' && first.startsWith('screen-')) {
        names.add(SCREEN_LABELS[first] ?? first);
      }
    }
    degraded.value = [...names];
  };
  sync();
  unsubCache = qc.getQueryCache().subscribe(sync);
});
onBeforeUnmount(() => unsubCache?.());
const anyDegraded = computed(() => degraded.value.length > 0);

const REFRESH_MS = 15_000;
const ALERT_FALLBACK_MS = 60_000;

const METRICS = [
  { key: 'temperature', label: '温度', unit: '℃', token: 'var(--chart-1)', icon: Thermometer },
  { key: 'humidity', label: '湿度', unit: '%RH', token: 'var(--chart-2)', icon: Droplets },
  { key: 'nh3', label: '氨气', unit: 'ppm', token: 'var(--chart-3)', icon: Wind },
  { key: 'co2', label: '二氧化碳', unit: 'ppm', token: 'var(--text-tertiary)', icon: Activity },
] as const;

type MetricKey = (typeof METRICS)[number]['key'];

const LEDGER_TYPE_LABEL: Record<string, string> = {
  stock_change: '存栏变动', sensor: '传感器告警', device_offline: '设备离线',
  climate: '气象告警', vision: '视觉告警', disease: '疫病告警', medicine: '用药',
  feed: '饲喂', vet_review: '兽医复核', lifecycle: '生命周期', slaughter: '出栏',
};
const ALERT_TYPE_LABEL: Record<string, string> = {
  sensor: '传感器告警', device_offline: '设备离线', climate: '气象告警',
  vision: '视觉告警', disease: '疫病告警',
};
const VISION_LEVEL_LABEL: Record<string, string> = { low: '提示', mid: '关注', high: '紧急' };

const RISK_TOKEN: Record<string, string> = { high: 'var(--danger)', mid: 'var(--warning)', low: 'var(--success)' };
const RISK_TEXT: Record<string, string> = { high: 'text-danger', mid: 'text-warning', low: 'text-success' };

// 图表走 canvas，不能用 CSS var，取 tokens 的静态等值（与 styles.css :root 一致）
const PALETTE = {
  chart1: '#2E9E6B',
  chart2: '#5B8DEF',
  chart3: '#E8A23D',
  text3: '#8C958E',
  text1: '#1E2420',
  border: '#E7E9E4',
  borderStrong: '#D5D8D1',
  success: '#2E9E6B',
  danger: '#D9534F',
  popover: '#FFFFFF',
};
const METRIC_HEX: Record<string, string> = {
  'var(--chart-1)': PALETTE.chart1,
  'var(--chart-2)': PALETTE.chart2,
  'var(--chart-3)': PALETTE.chart3,
  'var(--text-tertiary)': PALETTE.text3,
};
function rgba(hex: string, alpha: number): string {
  const n = Number.parseInt(hex.slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}
const TOOLTIP_STYLE = {
  backgroundColor: PALETTE.popover,
  borderColor: PALETTE.border,
  borderRadius: 12,
  textStyle: { color: PALETTE.text1, fontSize: 12 },
  extraCssText: 'box-shadow: var(--e2);',
};

function dateKey(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function localDateKey(iso: string): string {
  return dateKey(new Date(iso));
}
function hhmm(iso: string): string {
  const d = new Date(iso);
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}
function freshness(at: number | undefined): string {
  return at ? `更新于 ${new Date(at).toTimeString().slice(0, 5)}` : '';
}

const clock = ref('');
const penId = ref(0);
const metric = ref<MetricKey>('temperature');
const sessionLeft = ref<number | null>(null);
const pickedFarm = ref<number | null>(null);

let timer: number | null = null;
onMounted(() => {
  const tick = () => {
    const d = new Date();
    const p = (n: number) => String(n).padStart(2, '0');
    clock.value = `${dateKey(d)} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
    const exp = getTokenExpMs();
    sessionLeft.value = exp ? Math.max(0, exp - d.getTime()) : null;
  };
  tick();
  timer = window.setInterval(tick, 1000);
});
onBeforeUnmount(() => {
  if (timer) window.clearInterval(timer);
});

// ----- 存栏：按「当前场」作用域 -----
const { data: farmData } = useQuery({ queryKey: ['screen-farms'], queryFn: apiListFarms, refetchInterval: REFRESH_MS });
const farms = computed(() => farmData.value?.items ?? []);
const activeFarmId = computed(() => pickedFarm.value ?? farms.value[0]?.farm_id ?? 0);
const activeFarm = computed(() => farms.value.find((f) => f.farm_id === activeFarmId.value) ?? null);

const { data: penData, dataUpdatedAt: pensAt } = useQuery({
  queryKey: computed(() => ['screen-pens', activeFarmId.value]),
  queryFn: () => apiListPens(activeFarmId.value),
  enabled: computed(() => activeFarmId.value > 0),
  refetchInterval: REFRESH_MS,
});
const pens = computed(() => penData.value?.items ?? []);
const totalStock = computed(() => pens.value.reduce((s, p) => s + p.male_count + p.female_count, 0));
const totalMale = computed(() => pens.value.reduce((s, p) => s + p.male_count, 0));
const totalFemale = computed(() => pens.value.reduce((s, p) => s + p.female_count, 0));
const topPens = computed(() =>
  [...pens.value]
    .sort((a, b) => b.male_count + b.female_count - (a.male_count + a.female_count))
    .slice(0, 5),
);
const maxPenStock = computed(() => Math.max(1, ...topPens.value.map((p) => p.male_count + p.female_count)));

// 切场后曲线圈舍必须落在本场
watch(
  [pens, penId],
  () => {
    if (pens.value.length > 0 && !pens.value.some((p) => p.pen_id === penId.value)) {
      penId.value = pens.value[0].pen_id;
    }
  },
  { immediate: true },
);

// ----- 近 7 日净变动 -----
const weekAgo = dateKey(new Date(Date.now() - 6 * 86_400_000));
const { data: changeData, dataUpdatedAt: changeAt } = useQuery({
  queryKey: computed(() => ['screen-stock-records', activeFarmId.value, weekAgo]),
  queryFn: () =>
    apiListRecords({ farm_id: activeFarmId.value, record_type: 'stock_change', from: weekAgo, page: 1, page_size: 100 }),
  enabled: computed(() => activeFarmId.value > 0),
  refetchInterval: REFRESH_MS,
});
const netByDay = computed(() => {
  const map = new Map<string, number>();
  for (let i = 6; i >= 0; i--) map.set(dateKey(new Date(Date.now() - i * 86_400_000)), 0);
  for (const r of changeData.value?.items ?? []) {
    const ext = r.record_ext as Record<string, number> | undefined;
    if (!ext) continue;
    const net = (Number(ext.male_delta) || 0) + (Number(ext.female_delta) || 0);
    const key = localDateKey(r.create_time);
    if (map.has(key)) map.set(key, (map.get(key) ?? 0) + net);
  }
  return [...map.entries()].map(([date, net]) => ({ date: date.slice(5), net }));
});
const truncated = computed(() => (changeData.value?.total ?? 0) > 100);

// ----- 设备接入 -----
const { data: deviceData, dataUpdatedAt: devicesAt } = useQuery({
  queryKey: computed(() => ['screen-devices', activeFarmId.value]),
  queryFn: () => apiListDevices(activeFarmId.value),
  enabled: computed(() => activeFarmId.value > 0),
  refetchInterval: REFRESH_MS,
});
const devices = computed(() => deviceData.value?.items ?? []);
const onlineCount = computed(() => devices.value.filter((d) => d.online_status === 'online').length);
const cameraCount = computed(() => devices.value.filter((d) => d.device_type === 'camera').length);
const hasRealDevice = computed(() => devices.value.some((d) => d.source === 'real'));

// ----- 饲喂 -----
const todayKey = dateKey(new Date());
const firstPenId = computed(() => pens.value[0]?.pen_id ?? 0);
const { data: feedOvData, dataUpdatedAt: feedAt } = useQuery({
  queryKey: computed(() => ['screen-feed-overview', activeFarmId.value, todayKey]),
  queryFn: () => apiFeedingOverview(todayKey, activeFarmId.value),
  enabled: computed(() => pens.value.length > 0),
  refetchInterval: REFRESH_MS,
});
const feedItems = computed(() => feedOvData.value?.items ?? []);
const totalPlanned = computed(() => feedItems.value.reduce((s, i) => s + (i.planned_kg ?? 0), 0));
const totalActual = computed(() => feedItems.value.reduce((s, i) => s + (i.actual_kg ?? 0), 0));
const feedProgress = computed(() =>
  totalPlanned.value > 0 ? Math.round((totalActual.value / totalPlanned.value) * 100) : 0,
);
const donePens = computed(() => feedItems.value.filter((i) => i.registered).length);

const { data: feedPlan } = useQuery({
  queryKey: computed(() => ['screen-feed-plan-cost', firstPenId.value]),
  queryFn: () => apiGetFeedPlan(firstPenId.value),
  enabled: computed(() => firstPenId.value > 0),
  refetchInterval: 60_000,
});
const dailyCost = computed(() => feedPlan.value?.active_formula?.cost_per_head_day ?? 0);
const costSub = computed(() => (feedPlan.value?.plan_source === 'formula' ? '激活配方成本' : '未生成配方'));

// ----- 气象 -----
const { data: weatherData, dataUpdatedAt: weatherAt } = useQuery({
  queryKey: computed(() => ['screen-weather', activeFarmId.value]),
  queryFn: () => apiGetWeather(activeFarmId.value),
  enabled: computed(() => activeFarmId.value > 0),
  refetchInterval: REFRESH_MS,
});
const weather = computed(() => weatherData.value as WeatherResponse | undefined);

// ----- 告警 -----
let unsubscribe: (() => void) | null = null;
onMounted(() => {
  unsubscribe = subscribeAlerts(() => {
    void qc.invalidateQueries({ queryKey: ['screen-alerts'] });
  });
});
onBeforeUnmount(() => unsubscribe?.());

const { data: alertData, dataUpdatedAt: alertAt } = useQuery({
  queryKey: computed(() => ['screen-alerts', 'list', activeFarmId.value]),
  queryFn: () => apiListAlerts({ farm_id: activeFarmId.value, page: 1, page_size: 8 }),
  refetchInterval: ALERT_FALLBACK_MS,
  enabled: computed(() => activeFarmId.value > 0),
});
const { data: pendingData } = useQuery({
  queryKey: computed(() => ['screen-alerts', 'pending', activeFarmId.value]),
  queryFn: () => apiListAlerts({ farm_id: activeFarmId.value, status: 'pending', page: 1, page_size: 1 }),
  refetchInterval: ALERT_FALLBACK_MS,
  enabled: computed(() => activeFarmId.value > 0),
});
const { data: todayData } = useQuery({
  queryKey: computed(() => ['screen-alerts', 'today', activeFarmId.value, todayKey]),
  queryFn: () => apiListAlerts({ farm_id: activeFarmId.value, from: todayKey, page: 1, page_size: 1 }),
  refetchInterval: ALERT_FALLBACK_MS,
  enabled: computed(() => activeFarmId.value > 0),
});
const alerts = computed(() => alertData.value?.items ?? []);
const pendingTotal = computed(() => pendingData.value?.total ?? 0);
const todayAlerts = computed(() => todayData.value?.total ?? 0);

// ----- 视觉识别事件 -----
const { data: visionData, dataUpdatedAt: visionAt } = useQuery({
  queryKey: computed(() => ['screen-vision', activeFarmId.value]),
  queryFn: () => apiListVisionEvents({ farm_id: activeFarmId.value, page: 1, page_size: 8 }),
  refetchInterval: REFRESH_MS,
  enabled: computed(() => activeFarmId.value > 0),
});
const visionEvents = computed(() => visionData.value?.items ?? []);

// ----- 台账动态 -----
const { data: recordData, dataUpdatedAt: recordsAt } = useQuery({
  queryKey: computed(() => ['screen-records', activeFarmId.value]),
  queryFn: () => apiListRecords({ farm_id: activeFarmId.value, page: 1, page_size: 8 }),
  enabled: computed(() => activeFarmId.value > 0),
  refetchInterval: REFRESH_MS,
});
const records = computed<LedgerRecordItem[]>(() => recordData.value?.items ?? []);

// ----- 环境曲线 -----
const from24h = new Date(Date.now() - 24 * 3_600_000).toISOString();
const { data: historyData, dataUpdatedAt: envAt } = useQuery({
  queryKey: computed(() => ['screen-env', penId.value, metric.value, from24h]),
  queryFn: () => apiSensorHistory({ pen_id: penId.value, metric: metric.value, from: from24h }),
  enabled: computed(() => penId.value > 0),
  refetchInterval: REFRESH_MS,
});
const activeMetric = computed(() => METRICS.find((m) => m.key === metric.value)!);
const envChartData = computed(() =>
  (historyData.value?.points ?? []).map((pt) => ({ time: hhmm(pt.time), v: Math.round(pt.value * 10) / 10 })),
);
const envValues = computed(() => envChartData.value.map((d) => d.v));
const lastEnv = computed(() => (envChartData.value.length > 0 ? envChartData.value[envChartData.value.length - 1].v : null));
const minEnv = computed(() => (envValues.value.length > 0 ? Math.min(...envValues.value) : null));
const maxEnv = computed(() => (envValues.value.length > 0 ? Math.max(...envValues.value) : null));

const farmLabel = computed(() =>
  activeFarm.value
    ? `${activeFarm.value.farm_name}${activeFarm.value.location ? ` · ${activeFarm.value.location}` : ''}`
    : '云上牧场',
);
const multiFarm = computed(() => farms.value.length > 1);
const fillStyle = (ratio: number) => ({ flex: `${ratio} 1 0`, minHeight: 0 });

const barOption = computed(() => ({
  animation: false,
  grid: { top: 6, right: 4, left: -22, bottom: -4 },
  tooltip: { trigger: 'axis', ...TOOLTIP_STYLE },
  xAxis: {
    type: 'category',
    data: netByDay.value.map((d) => d.date),
    axisTick: { show: false },
    axisLine: { lineStyle: { color: PALETTE.border } },
    axisLabel: { color: PALETTE.text3, fontSize: 10 },
  },
  yAxis: {
    type: 'value',
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: { color: PALETTE.text3, fontSize: 10 },
    splitLine: { lineStyle: { color: PALETTE.border } },
  },
  series: [
    {
      type: 'bar',
      name: '净变动',
      barWidth: '55%',
      data: netByDay.value.map((d) => ({
        value: d.net,
        itemStyle: { color: d.net >= 0 ? PALETTE.success : PALETTE.danger, borderRadius: [3, 3, 0, 0] },
      })),
      markLine: { silent: true, symbol: 'none', lineStyle: { color: PALETTE.borderStrong }, data: [{ yAxis: 0 }] },
    },
  ],
}));

const areaOption = computed(() => {
  const hex = METRIC_HEX[activeMetric.value.token] ?? PALETTE.chart1;
  return {
    animation: false,
    grid: { top: 4, right: 6, left: -14, bottom: -4 },
    tooltip: { trigger: 'axis', ...TOOLTIP_STYLE },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: envChartData.value.map((d) => d.time),
      axisTick: { show: false },
      axisLine: { lineStyle: { color: PALETTE.border } },
      axisLabel: { color: PALETTE.text3, fontSize: 10, hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: { color: PALETTE.text3, fontSize: 10 },
      splitLine: { lineStyle: { color: PALETTE.border } },
    },
    series: [
      {
        type: 'line',
        name: `${activeMetric.value.label} ${activeMetric.value.unit}`,
        data: envChartData.value.map((d) => d.v),
        smooth: true,
        symbol: 'none',
        lineStyle: { color: hex, width: 2 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: rgba(hex, 0.26) },
              { offset: 1, color: rgba(hex, 0.02) },
            ],
          },
        },
      },
    ],
  };
});

function toggleFullscreen() {
  if (document.fullscreenElement) void document.exitFullscreen();
  else void document.documentElement.requestFullscreen().catch(() => undefined);
}
function gotoDashboard() {
  router.push('/dashboard');
}
function gotoProjection() {
  router.push('/screen');
}
function gotoAlert(alertId: number) {
  router.push({ path: '/alerts', query: { focus: String(alertId) } });
}
</script>

<template>
  <div
    class="flex flex-col bg-bg-page"
    :class="projection ? 'h-screen overflow-hidden' : 'h-full p-6 gap-4'"
  >
    <!-- 接口失败横幅：宁可刺眼，也不要让"取不到"看起来像"一切正常" -->
    <div
      v-if="anyDegraded"
      role="alert"
      class="shrink-0 flex items-center gap-2 px-4 py-2 bg-warning/10 border-b border-warning/40 text-[12.5px] text-warning"
    >
      <AlertTriangle :size="14" class="shrink-0" />
      <span>
        {{ degraded.length }} 项数据获取失败（{{ degraded.slice(0, 3).join('、') }}{{ degraded.length > 3 ? '等' : '' }}），
        页面数值可能已过期，不代表当前状态
      </span>
    </div>
    <!-- ===== Header ===== -->
    <header
      v-if="projection"
      class="shrink-0 flex items-center gap-4 px-6 py-3 border-b border-border bg-card"
    >
      <MonitorPlay :size="22" class="text-primary" :stroke-width="1.5" />
      <div>
        <h1 class="text-[19px] font-semibold text-text-primary leading-tight tracking-wide">云上牧场 · 数字大屏</h1>
        <p class="text-[11.5px] text-text-tertiary">{{ farmLabel }} · 智慧养殖多智能体平台</p>
      </div>
      <div class="flex-1" />
      <select
        v-if="multiFarm"
        :value="activeFarmId"
        aria-label="选择养殖场"
        class="h-[28px] px-2 rounded-[6px] text-[12.5px] bg-bg-subtle border border-border text-text-primary outline-none"
        @change="pickedFarm = Number(($event.target as HTMLSelectElement).value)"
      >
        <option v-for="f in farms" :key="f.farm_id" :value="f.farm_id">{{ f.farm_name }}</option>
      </select>
      <SourceBadge :real="hasRealDevice" :count="devices.length" />
      <span class="tnum text-[15px] tracking-wider text-text-secondary">{{ clock }}</span>
      <span
        v-if="sessionLeft !== null && sessionLeft < 30 * 60_000"
        class="text-[11.5px] px-2 py-1 rounded-[6px] text-warning"
        style="background-color: rgba(232,162,61,.12)"
      >
        登录态 {{ Math.floor(sessionLeft / 60_000) }} 分钟后失效
      </span>
      <button
        class="flex items-center gap-1.5 h-[32px] px-3.5 rounded-[8px] text-[12.5px] border border-border text-text-secondary hover:bg-bg-subtle hover:text-text-primary transition-colors duration-[var(--duration-fast)]"
        @click="toggleFullscreen"
      >
        <Maximize2 :size="14" :stroke-width="1.5" />
        全屏
      </button>
      <button
        class="flex items-center gap-1.5 h-[32px] px-3.5 rounded-[8px] text-[12.5px] font-medium bg-primary text-primary-foreground hover:bg-primary-600 transition-colors duration-[var(--duration-fast)]"
        @click="gotoDashboard"
      >
        退出大屏
      </button>
    </header>

    <div v-else class="shrink-0 flex items-end gap-3">
      <div>
        <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">数字大屏</h1>
        <p class="text-[14px] text-text-secondary mt-0.5">{{ farmLabel }}</p>
      </div>
      <div class="flex-1" />
      <select
        v-if="multiFarm"
        :value="activeFarmId"
        aria-label="选择养殖场"
        class="h-[28px] px-2 rounded-[6px] text-[12.5px] bg-bg-subtle border border-border text-text-primary outline-none"
        @change="pickedFarm = Number(($event.target as HTMLSelectElement).value)"
      >
        <option v-for="f in farms" :key="f.farm_id" :value="f.farm_id">{{ f.farm_name }}</option>
      </select>
      <SourceBadge :real="hasRealDevice" :count="devices.length" />
      <span class="tnum text-[14px] text-text-secondary">{{ clock }}</span>
      <button
        class="flex items-center gap-1.5 h-[32px] px-3.5 rounded-[8px] text-[13px] border border-border text-text-secondary hover:bg-bg-subtle hover:text-text-primary transition-colors duration-[var(--duration-fast)]"
        @click="gotoProjection"
      >
        <Maximize2 :size="14" :stroke-width="1.5" />
        全屏投影
      </button>
    </div>

    <!-- KPI 条 -->
    <div
      class="shrink-0 grid gap-3"
      :class="projection ? 'grid-cols-5 px-3 pt-3' : 'grid-cols-2 md:grid-cols-3 xl:grid-cols-5'"
    >
      <KpiCard
        :icon="Layers"
        label="总存栏"
        :value="totalStock"
        unit="头"
        :sub="pens.length > 0 ? `公 ${totalMale} / 母 ${totalFemale} · ${pens.length} 圈舍` : '尚未创建圈舍'"
        accent="text-primary"
      />
      <KpiCard
        :icon="UtensilsCrossed"
        label="今日采食完成度"
        :value="feedProgress"
        unit="%"
        :sub="totalPlanned > 0 ? `${totalActual.toFixed(1)} / ${totalPlanned.toFixed(1)} kg · ${donePens}/${feedItems.length} 圈已登记` : '今日尚无投料计划'"
        :accent="feedProgress >= 95 ? 'text-success' : feedProgress >= 60 ? 'text-warning' : 'text-danger'"
      />
      <KpiCard
        :icon="Coins"
        label="饲料日均成本"
        :value="dailyCost"
        unit="元/头"
        :digits="1"
        :sub="dailyCost > 0 ? costSub : '去喂食中心生成配方'"
        accent="text-text-primary"
      />
      <KpiCard
        :icon="BellRing"
        label="待处理告警"
        :value="pendingTotal"
        unit="条"
        :sub="`今日新增 ${todayAlerts} 条`"
        :accent="pendingTotal > 0 ? 'text-danger' : 'text-success'"
      />
      <KpiCard
        :icon="Cpu"
        label="设备在线"
        :value="onlineCount"
        :unit="`/ ${devices.length}`"
        :sub="`摄像头 ${cameraCount} 台 · 传感器 ${devices.length - cameraCount} 台`"
        :accent="onlineCount > 0 ? 'text-text-primary' : 'text-danger'"
        :fresh="devicesAt ? freshness(devicesAt) : undefined"
      />
    </div>

    <!-- 主体三列 -->
    <main
      class="grid grid-cols-1 lg:grid-cols-12 gap-3"
      :class="projection ? 'flex-1 min-h-0 p-3 overflow-hidden' : 'flex-1 min-h-[560px]'"
    >
      <!-- 左列：存栏结构 + 净变动 + 今日采食执行 -->
      <section class="lg:col-span-3 flex flex-col gap-3 min-h-0">
        <ScreenPanel :icon="Warehouse" title="存栏结构" :fresh="pensAt ? freshness(pensAt) : undefined" :panel-style="fillStyle(1)">
          <EmptyHint v-if="pens.length === 0" text="暂无场舍数据，请先在场舍管理创建" />
          <div v-else class="flex-1 min-h-0 flex flex-col">
            <div class="flex items-baseline gap-2 shrink-0">
              <span class="tnum text-[38px] font-semibold leading-none text-primary">{{ totalStock }}</span>
              <span class="text-[12.5px] text-text-secondary">头在栏</span>
            </div>
            <div class="grid grid-cols-3 gap-2 mt-3 shrink-0">
              <MiniStat label="养殖场" :value="farms.length" />
              <MiniStat label="圈舍" :value="pens.length" />
              <MiniStat label="均栏" :value="pens.length ? Math.round(totalStock / pens.length) : 0" />
            </div>
            <div class="mt-3 space-y-2 flex-1 min-h-0 overflow-y-auto pr-1">
              <p class="text-[11.5px] text-text-tertiary sticky top-0 bg-card">存栏 Top5 圈舍</p>
              <div v-for="p in topPens" :key="p.pen_id">
                <div class="flex justify-between text-[11.5px] mb-1">
                  <span class="text-text-secondary">{{ p.pen_name }}</span>
                  <span class="tnum text-text-primary">{{ p.male_count + p.female_count }} 头</span>
                </div>
                <div class="h-[5px] rounded-full bg-bg-inset overflow-hidden">
                  <div
                    class="h-full rounded-full bg-primary"
                    :style="{ width: `${((p.male_count + p.female_count) / maxPenStock) * 100}%` }"
                  />
                </div>
              </div>
            </div>
          </div>
        </ScreenPanel>

        <ScreenPanel :icon="Activity" title="近 7 日存栏净变动" :fresh="changeAt ? freshness(changeAt) : undefined" :panel-style="fillStyle(1)">
          <div class="flex-1 min-h-0">
            <VChart class="h-full w-full" :option="barOption" autoresize />
          </div>
          <p class="text-[10.5px] mt-1 shrink-0 text-text-tertiary">
            涨绿跌红 · 按日聚合存栏流水{{ truncated ? '（仅统计最近 100 条）' : '' }}
          </p>
        </ScreenPanel>

        <ScreenPanel :icon="UtensilsCrossed" title="今日采食执行" :fresh="feedAt ? freshness(feedAt) : undefined" :panel-style="fillStyle(1)">
          <EmptyHint v-if="feedItems.length === 0" text="今日暂无投料计划，喂食中心生成配方后显示" />
          <div v-else class="flex-1 min-h-0 overflow-y-auto pr-1 space-y-2">
            <div v-for="i in feedItems" :key="i.pen_id">
              <div class="flex justify-between text-[11.5px] mb-1">
                <span class="text-text-secondary">
                  {{ i.pen_name }}
                  <span v-if="!i.registered" class="text-text-tertiary"> · 未登记</span>
                </span>
                <span
                  class="tnum"
                  :class="
                    !i.registered
                      ? 'text-text-tertiary'
                      : (i.actual_kg ?? 0) / Math.max(1, i.planned_kg ?? 0) >= 0.95
                        ? 'text-success'
                        : (i.actual_kg ?? 0) / Math.max(1, i.planned_kg ?? 0) >= 0.6
                          ? 'text-warning'
                          : 'text-danger'
                  "
                >
                  {{ (i.actual_kg ?? 0).toFixed(1) }} / {{ (i.planned_kg ?? 0).toFixed(1) }} kg
                </span>
              </div>
              <div class="h-[6px] rounded-full bg-bg-inset overflow-hidden">
                <div
                  class="h-full rounded-full transition-all duration-[var(--duration-base)]"
                  :class="
                    !i.registered
                      ? 'bg-text-disabled'
                      : (i.actual_kg ?? 0) / Math.max(1, i.planned_kg ?? 0) >= 0.95
                        ? 'bg-success'
                        : (i.actual_kg ?? 0) / Math.max(1, i.planned_kg ?? 0) >= 0.6
                          ? 'bg-warning'
                          : 'bg-danger'
                  "
                  :style="{ width: `${(i.planned_kg ?? 0) > 0 ? Math.min(100, Math.round(((i.actual_kg ?? 0) / (i.planned_kg ?? 0)) * 100)) : 0}%` }"
                />
              </div>
            </div>
            <p v-if="feedItems[0]?.basis" class="text-[10.5px] pt-1 text-text-tertiary">计划依据：{{ feedItems[0].basis }}</p>
          </div>
        </ScreenPanel>
      </section>

      <!-- 中列：环境曲线 + 视觉事件 -->
      <section class="lg:col-span-5 flex flex-col gap-3 min-h-0">
        <ScreenPanel :icon="activeMetric.icon" title="圈舍环境实时曲线" :fresh="envAt ? freshness(envAt) : undefined" :panel-style="fillStyle(1.35)">
          <template #extra>
            <select
              v-if="pens.length > 0"
              :value="penId"
              class="h-[26px] px-2 rounded-[6px] text-[11.5px] bg-bg-subtle border border-border text-text-primary outline-none"
              @change="penId = Number(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="p in pens" :key="p.pen_id" :value="p.pen_id">{{ p.pen_name }}</option>
            </select>
          </template>
          <div class="flex items-center gap-1 shrink-0 mb-2">
            <button
              v-for="m in METRICS"
              :key="m.key"
              :class="
                cn(
                  'flex items-center gap-1.5 h-[26px] px-2.5 rounded-[6px] text-[11.5px] transition-colors duration-[var(--duration-fast)]',
                  metric === m.key
                    ? 'bg-bg-subtle border border-border-strong text-text-primary font-medium'
                    : 'border border-transparent text-text-tertiary hover:text-text-secondary',
                )
              "
              @click="metric = m.key"
            >
              <component :is="m.icon" :size="12" :stroke-width="1.5" :style="{ color: m.token }" />
              {{ m.label }}
            </button>
            <span class="flex-1" />
            <span v-if="lastEnv !== null" class="text-[11.5px] text-text-secondary">
              当前 <b class="tnum text-[20px] text-text-primary">{{ lastEnv }}</b> {{ activeMetric.unit }}
            </span>
          </div>

          <EmptyHint
            v-if="envChartData.length === 0"
            :text="penId ? `该圈舍 24 小时内无${activeMetric.label}读数` : '暂无传感器数据'"
          />
          <template v-else>
            <div class="flex-1 min-h-0">
              <VChart class="h-full w-full" :option="areaOption" autoresize />
            </div>
            <p class="text-[10.5px] mt-1 shrink-0 text-text-tertiary">
              近 24 小时 · 区间 {{ minEnv }}~{{ maxEnv }} {{ activeMetric.unit }} · 共 {{ envChartData.length }} 个读数
            </p>
          </template>
        </ScreenPanel>

        <ScreenPanel :icon="Video" title="视觉识别事件" :fresh="visionAt ? freshness(visionAt) : undefined" :panel-style="fillStyle(1)">
          <EmptyHint v-if="visionEvents.length === 0" text="暂无视觉事件 · 接入边缘网关后自动出现" />
          <div v-else class="flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-1">
            <div
              v-for="v in visionEvents"
              :key="v.result_id"
              class="rounded-[8px] px-2.5 py-2 flex items-start gap-2 bg-bg-subtle"
              :style="{ borderLeft: `3px solid ${RISK_TOKEN[v.level]}` }"
            >
              <div class="min-w-0 flex-1">
                <p class="text-[12px] leading-snug truncate text-text-primary">{{ v.event_label }}</p>
                <p class="text-[10.5px] mt-0.5 text-text-tertiary">
                  {{ v.pen_name }} · 置信度 {{ Math.round(v.confidence * 100) }}% · {{ hhmm(v.collect_time) }}
                </p>
              </div>
              <span class="shrink-0 px-1.5 py-0.5 rounded-[5px] text-[10px]" :class="RISK_TEXT[v.level]">
                {{ VISION_LEVEL_LABEL[v.level] ?? v.level }}
              </span>
            </div>
            <p class="text-[10px] pt-1 text-text-tertiary">{{ VISION_DISCLAIMER }}</p>
          </div>
        </ScreenPanel>
      </section>

      <!-- 右列：气象 + 告警 + 台账 -->
      <section class="lg:col-span-4 flex flex-col gap-3 min-h-0">
        <ScreenPanel :icon="Activity" title="本地气象（3 日）" :fresh="weatherAt ? freshness(weatherAt) : undefined" compact>
          <template v-if="weather?.forecast?.length">
            <div class="grid grid-cols-3 gap-2 shrink-0">
              <div v-for="d in weather.forecast.slice(0, 3)" :key="d.date" class="rounded-[8px] px-2 py-2 text-center bg-bg-subtle">
                <p class="text-[10.5px] text-text-tertiary">{{ d.date.slice(5).replace('-', '/') }}</p>
                <p class="text-[12px] mt-0.5 text-text-primary">{{ d.text_day }}</p>
                <p class="text-[11px] mt-0.5 tnum text-warning">{{ d.temp_min }}~{{ d.temp_max }}℃</p>
              </div>
            </div>
            <p v-if="weather.degraded" class="text-[10px] mt-1.5 text-warning">
              降级数据 · 更新于 {{ weather.data_time?.slice(11, 16) ?? '--' }}
            </p>
          </template>
          <EmptyHint v-else text="气象服务暂不可用（完善养殖场地址后显示）" />
        </ScreenPanel>

        <ScreenPanel :icon="BellRing" title="告警态势" :fresh="freshness(alertAt)" :panel-style="fillStyle(1.25)">
          <div class="grid grid-cols-2 gap-2 mb-2.5 shrink-0">
            <StatBlock label="待处理" :value="pendingTotal" :tone="anyDegraded ? 'text-text-tertiary' : pendingTotal > 0 ? 'text-danger' : 'text-success'" />
            <StatBlock label="今日新增" :value="todayAlerts" tone="text-warning" />
          </div>
          <EmptyHint
            v-if="alerts.length === 0"
            :text="anyDegraded ? '告警数据获取失败，不代表当前无告警' : '暂无告警，环境运行平稳'"
          />
          <div v-else class="flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-1">
            <button
              v-for="a in alerts"
              :key="a.alert_id"
              type="button"
              title="点击跳到告警中心并定位这条"
              class="w-full text-left rounded-[8px] px-2.5 py-2 flex items-start gap-2 bg-bg-subtle hover:bg-bg-inset transition-colors cursor-pointer"
              :style="{ borderLeft: `3px solid ${RISK_TOKEN[a.risk_level]}` }"
              @click="gotoAlert(a.alert_id)"
            >
              <AlertTriangle
                v-if="a.risk_level !== 'low'"
                :size="12"
                :stroke-width="1.5"
                class="mt-0.5 shrink-0"
                :class="RISK_TEXT[a.risk_level]"
              />
              <div class="min-w-0">
                <p class="text-[12px] leading-snug truncate text-text-primary">{{ a.alert_content }}</p>
                <p class="text-[10.5px] mt-0.5 text-text-tertiary">
                  {{ a.pen_name }} · {{ ALERT_TYPE_LABEL[a.alert_type] ?? a.alert_type }} · {{ hhmm(a.create_time) }}
                  <template v-if="a.status === 'resolved'"> · 已处理</template>
                </p>
              </div>
            </button>
          </div>
        </ScreenPanel>

        <ScreenPanel :icon="ScrollText" title="台账动态" :fresh="recordsAt ? freshness(recordsAt) : undefined" :panel-style="fillStyle(1)">
          <EmptyHint v-if="records.length === 0" text="暂无台账记录" />
          <div v-else class="flex-1 min-h-0 overflow-y-auto space-y-1.5 pr-1">
            <div v-for="r in records" :key="r.record_id" class="rounded-[8px] px-2.5 py-2 flex items-start gap-2 bg-bg-subtle">
              <span
                class="shrink-0 px-1.5 py-0.5 rounded-[5px] text-[10px] font-medium mt-0.5"
                :class="r.record_type === 'stock_change' ? 'text-success bg-primary-100' : 'text-text-secondary bg-bg-inset'"
              >
                {{ LEDGER_TYPE_LABEL[r.record_type] ?? r.record_type }}
              </span>
              <div class="min-w-0">
                <p class="text-[12px] leading-snug text-text-primary">{{ r.summary }}</p>
                <p class="text-[10.5px] mt-0.5 text-text-tertiary">{{ r.pen_name }} · {{ hhmm(r.create_time) }}</p>
              </div>
            </div>
          </div>
        </ScreenPanel>
      </section>
    </main>
  </div>
</template>
