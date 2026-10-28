<script setup lang="ts">
// Tab3 采食与增重（FR-08/09/11 ③）
// - 采食柱状图（7 天均值虚线 + 降 15% 异常提示跳问诊 FR-09）
// - 日增重折线（品种标准虚线 + 连续 2 次 ratio<0.8 mid 提醒 TC-F10）
// - 称重登记弹窗（骤降确认 EX-15）
import { computed, ref } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { AlertTriangle, ArrowRight, Scale, TrendingUp } from 'lucide-vue-next';
import { todayLocal } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import type { UserPen } from '@/api/auth-api';
import { apiCreateWeightRecord, apiGetDailyGain, apiListFeedingRecords } from '@/api/feed-api';
import FeedingDialog from './FeedingDialog.vue';

const props = defineProps<{ pen: UserPen }>();
const emit = defineEmits<{ toast: [kind: 'ok' | 'err', text: string]; aiConsult: [prefill: string] }>();

const queryClient = useQueryClient();

function fmtDate(d: Date): string {
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
function todayStr(): string {
  return todayLocal();
}
function addDays(dateStr: string, delta: number): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!m) return dateStr;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  d.setDate(d.getDate() + delta);
  return fmtDate(d);
}
function errText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

interface IntakeDay {
  date: string;
  planned: number | null;
  actual: number;
}

const weighOpen = ref(false);
const confirmNeed = ref<{ message: string; action: () => void } | null>(null);
const today = todayStr();
const from = addDays(today, -13);

// 近 14 天饲喂记录（采食曲线聚合源，FR-09）
const { data: recordsData } = useQuery({
  queryKey: computed(() => ['feeding-records', props.pen.pen_id, from, today]),
  queryFn: () => apiListFeedingRecords({ pen_id: props.pen.pen_id, from, to: today, page_size: 62 }),
});
const { data: gainData } = useQuery({
  queryKey: computed(() => ['daily-gain', props.pen.pen_id]),
  queryFn: () => apiGetDailyGain(props.pen.pen_id),
});

// 按日聚合（无登记日 0 柱，口径透明 FR-09）
const intakeDays = computed<IntakeDay[]>(() => {
  const map = new Map<string, IntakeDay>();
  for (let i = 13; i >= 0; i--) {
    const d = addDays(today, -i);
    map.set(d, { date: d, planned: null, actual: 0 });
  }
  for (const r of recordsData.value?.items ?? []) {
    const day = map.get(r.feed_date);
    if (!day) continue;
    day.actual += r.amount_kg;
    if (r.planned_kg !== null && (day.planned === null || r.planned_kg > day.planned)) {
      day.planned = r.planned_kg;
    }
  }
  return [...map.values()];
});

// 采食异常判定（前端计算，F06 决策 3；TC-F09：较前 7 天均值下降超 15%）
const intakeAlert = computed(() => {
  const latest = [...intakeDays.value].reverse().find((d) => d.actual > 0);
  if (!latest) return null;
  const idx = intakeDays.value.findIndex((d) => d.date === latest.date);
  const prev7 = intakeDays.value.slice(Math.max(0, idx - 7), idx);
  if (prev7.length === 0) return null;
  const prevAvg = prev7.reduce((s, d) => s + d.actual, 0) / prev7.length;
  if (prevAvg <= 0) return null;
  const dropPct = Math.round((1 - latest.actual / prevAvg) * 100);
  if (dropPct < 15) return null;
  return { dropPct, date: latest.date, actual: latest.actual, prevAvg };
});

// 增重偏低提醒（TC-F10：连续 2 次 ratio<0.8 → mid 提醒 + 归因建议）
const gainAlert = computed(() => {
  const data = gainData.value;
  if (!data || data.standard_line === null || data.daily_gain.length < 2) return null;
  const standard = data.standard_line;
  const last2 = data.daily_gain.slice(-2);
  if (last2.every((g) => (g.gain ?? 0) < standard * 0.8)) {
    return { ratio: last2[last2.length - 1].gain / standard };
  }
  return null;
});

function invalidateWeight() {
  queryClient.invalidateQueries({ queryKey: ['daily-gain', props.pen.pen_id] });
}

// ===== 采食柱状图（计划灰柱 + 实际主色渐变圆角顶 + 7 天均值虚线，FR-09）=====

const chartH = 170;
const maxVal = computed(() => Math.max(1, ...intakeDays.value.map((d) => Math.max(d.actual, d.planned ?? 0))));
// 7 天均值线：最近 7 个自然日实际均值
const last7 = computed(() => intakeDays.value.slice(-7));
const avg7 = computed(() => last7.value.reduce((s, d) => s + d.actual, 0) / Math.max(1, last7.value.length));
const avgY = computed(() => chartH - (avg7.value / maxVal.value) * chartH);

function plannedH(d: IntakeDay): number {
  return d.planned !== null ? (d.planned / maxVal.value) * chartH : 0;
}
function actualH(d: IntakeDay): number {
  return (d.actual / maxVal.value) * chartH;
}
function barTitle(d: IntakeDay): string {
  return `${d.date}：实际 ${d.actual}kg${d.planned !== null ? ` / 计划 ${d.planned}kg` : ''}`;
}
const axisIndices = computed(() => [0, Math.floor(intakeDays.value.length / 2), intakeDays.value.length - 1]);

// ===== 日增重折线（渐变面积 + 品种标准虚线 + 单次 low 标注，TC-F10/F11）=====

const gains = computed(() => gainData.value?.daily_gain ?? []);
const points = computed(() => gainData.value?.points ?? []);
const standardLine = computed(() => gainData.value?.standard_line ?? null);

const W = 400;
const H = 180;
const gainMax = computed(() =>
  Math.max(0.1, ...gains.value.map((g) => g.gain), ...(standardLine.value !== null ? [standardLine.value] : [])),
);
const gainMin = computed(() =>
  Math.min(0, ...gains.value.map((g) => g.gain), ...(standardLine.value !== null ? [standardLine.value] : [])),
);
const gainRange = computed(() => gainMax.value - gainMin.value || 1);
function xOf(i: number, len: number): number {
  return len <= 1 ? W / 2 : (i / (len - 1)) * (W - 24) + 12;
}
function yOf(v: number): number {
  return 12 + (1 - (v - gainMin.value) / gainRange.value) * (H - 30);
}
const lineD = computed(() =>
  gains.value.map((g, i) => `${i === 0 ? 'M' : 'L'} ${xOf(i, gains.value.length)} ${yOf(g.gain)}`).join(' '),
);
const areaD = computed(() =>
  gains.value.length >= 2
    ? `${lineD.value} L ${xOf(gains.value.length - 1, gains.value.length)} ${H} L ${xOf(0, gains.value.length)} ${H} Z`
    : '',
);
const standardY = computed(() => (standardLine.value !== null ? yOf(standardLine.value) : 0));
function isLowGain(gain: number): boolean {
  return standardLine.value !== null && gain < standardLine.value * 0.8;
}
const lastGain = computed(() => (gains.value.length > 0 ? gains.value[gains.value.length - 1] : null));
const lastGainColor = computed(() => {
  if (!lastGain.value) return 'var(--text-primary)';
  return standardLine.value !== null && lastGain.value.gain < standardLine.value * 0.8
    ? 'var(--warning)'
    : 'var(--text-primary)';
});
function standardPct(gain: number): number {
  return standardLine.value !== null ? Math.round((gain / standardLine.value) * 100) : 0;
}

// ===== 称重登记弹窗（FR-08：avg_weight + sample_count；骤降 >10% 40005 确认重发 EX-15）=====

const weighDate = ref(todayStr());
const avgWeight = ref('');
const sampleCount = ref('');
const remark = ref('');
const weighLoading = ref(false);
const weighErr = ref<string | null>(null);

// 数字输入框按 React 版保留字符串状态（v-model 会因 type=number 自动转成 number）
function onAvgWeightInput(e: Event) {
  avgWeight.value = (e.target as HTMLInputElement).value;
}
function onSampleCountInput(e: Event) {
  sampleCount.value = (e.target as HTMLInputElement).value;
}

function openWeigh() {
  weighDate.value = todayStr();
  avgWeight.value = '';
  sampleCount.value = '';
  remark.value = '';
  weighLoading.value = false;
  weighErr.value = null;
  weighOpen.value = true;
}

async function submitWeigh(confirm = false) {
  const avg = Number(avgWeight.value);
  const sample = Number(sampleCount.value);
  if (!Number.isFinite(avg) || avg <= 0) {
    weighErr.value = '请填写正确的均重（大于 0）';
    return;
  }
  if (!Number.isInteger(sample) || sample <= 0) {
    weighErr.value = '请填写称重头数';
    return;
  }
  weighLoading.value = true;
  weighErr.value = null;
  try {
    const res = await apiCreateWeightRecord({
      pen_id: props.pen.pen_id,
      weigh_date: weighDate.value,
      avg_weight_kg: avg,
      sample_count: sample,
      remark: remark.value.trim() || undefined,
      confirm: confirm || undefined,
    });
    const gainText =
      res.daily_gain_kg !== null
        ? `，日增重 ${res.daily_gain_kg} kg/天${
            res.vs_standard ? `（为标准的 ${Math.round(res.vs_standard.ratio * 100)}%）` : ''
          }`
        : '（首次称重，下次称重后自动计算日增重）';
    weighOpen.value = false;
    emit('toast', 'ok', `称重已登记${gainText}`);
    invalidateWeight();
  } catch (e) {
    if (e instanceof ApiError && e.code === 40005) {
      // EX-15：骤降确认——确认后附 confirm 重发
      const action = () => submitWeigh(true);
      weighOpen.value = false;
      confirmNeed.value = { message: e.message, action };
      return;
    }
    weighErr.value = errText(e, '登记失败，请重试');
  } finally {
    weighLoading.value = false;
  }
}

function runConfirmAction() {
  const action = confirmNeed.value?.action;
  confirmNeed.value = null;
  action?.();
}
</script>

<template>
  <div class="space-y-4">
    <!-- 异常提示条（置顶部橙色 + 归因分析跳问诊，06 规范 4.6） -->
    <div
      v-if="intakeAlert"
      class="flex items-center gap-3 px-4 py-3 rounded-[12px] border-l-4 border-warning flex-wrap"
      style="background-color: rgba(232,162,61,0.08)"
    >
      <AlertTriangle :size="18" class="shrink-0" style="color: var(--warning)" :stroke-width="1.5" />
      <span class="text-[14px] text-text-primary">
        采食异常（{{ pen.pen_name }} 较前 7 天均值下降
        <span class="font-medium tnum">{{ intakeAlert.dropPct }}%</span>，{{ intakeAlert.date }} 实际
        <span class="tnum">{{ intakeAlert.actual }}</span> kg / 均值
        <span class="tnum">{{ intakeAlert.prevAvg.toFixed(0) }}</span> kg）
      </span>
      <button
        @click="
          emit(
            'aiConsult',
            `${pen.pen_name}采食下降${intakeAlert.dropPct}%（近 7 天日均 ${intakeAlert.prevAvg.toFixed(0)}kg，${intakeAlert.date} 实际 ${intakeAlert.actual}kg），请帮我分析可能的原因。`,
          )
        "
        class="ml-auto text-[13px] text-primary font-medium hover:text-primary-600 transition-colors flex items-center gap-1"
      >
        归因分析
        <ArrowRight :size="14" :stroke-width="1.5" />
      </button>
    </div>
    <div
      v-if="gainAlert"
      class="flex items-center gap-3 px-4 py-3 rounded-[12px] border-l-4 border-warning flex-wrap"
      style="background-color: rgba(232,162,61,0.08)"
    >
      <TrendingUp :size="18" class="shrink-0" style="color: var(--warning)" :stroke-width="1.5" />
      <span class="text-[14px] text-text-primary">
        日增重持续低于标准 80%（最近两次均偏低，当前为标准的
        <span class="font-medium tnum">{{ Math.round(gainAlert.ratio * 100) }}%</span>），建议排查适口性、饲养密度、隐性发病与寄生虫
      </span>
      <button
        @click="
          emit(
            'aiConsult',
            `${pen.pen_name}日增重连续两次低于品种标准的 80%，请帮我分析可能的原因（适口性/密度/隐性发病/寄生虫方向）。`,
          )
        "
        class="ml-auto text-[13px] text-primary font-medium hover:text-primary-600 transition-colors flex items-center gap-1"
      >
        归因分析
        <ArrowRight :size="14" :stroke-width="1.5" />
      </button>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <!-- 采食柱状图（圆角顶 + 7 天均值虚线） -->
      <div class="bg-card rounded-[12px] p-6 card-highlight">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-[16px] font-semibold text-text-primary">日采食量（近 14 天）</h3>
          <span
            v-if="intakeAlert"
            class="text-[12px] px-2 py-0.5 rounded-[6px] font-medium"
            style="background-color: rgba(232,162,61,.12); color: var(--warning)"
          >
            下降 {{ intakeAlert.dropPct }}%
          </span>
        </div>
        <div>
          <div class="relative" :style="{ height: `${chartH + 24}px` }">
            <!-- 均值虚线（有数据时展示） -->
            <div v-if="avg7 > 0" class="absolute left-0 right-0 flex items-center" :style="{ top: `${avgY}px` }">
              <div class="flex-1 border-t border-dashed" style="border-color: var(--warning)" />
              <span class="text-[10.5px] tnum ml-1" style="color: var(--warning)">
                7 天均值 {{ avg7.toFixed(0) }}
              </span>
            </div>
            <div class="absolute inset-x-0 bottom-6 flex items-end justify-between gap-1.5">
              <div
                v-for="d in intakeDays"
                :key="d.date"
                class="flex-1 relative"
                :style="{ height: `${chartH}px` }"
                :title="barTitle(d)"
              >
                <!-- 计划刻度 -->
                <div
                  v-if="d.planned !== null && d.planned > 0"
                  class="absolute left-1/2 w-[14px] -translate-x-1/2 rounded-t-[4px] bg-bg-inset"
                  :style="{ bottom: 0, height: `${plannedH(d)}px` }"
                />
                <!-- 实际柱（圆角顶 + 主色渐变） -->
                <div
                  class="absolute left-1/2 -translate-x-1/2 w-[12px] rounded-t-[4px] bg-gradient-to-t from-primary to-primary-200"
                  :style="{ bottom: 0, height: `${actualH(d)}px`, opacity: d.actual > 0 ? 1 : 0.25 }"
                />
              </div>
            </div>
            <!-- X 轴标签（首/中/尾抽样，避免挤压） -->
            <div class="absolute inset-x-0 bottom-0 flex justify-between">
              <span v-for="i in axisIndices" :key="i" class="text-[10.5px] text-text-tertiary tnum">
                {{ intakeDays[i]?.date.slice(5) }}
              </span>
            </div>
          </div>
          <div class="flex items-center gap-4 mt-2 justify-center">
            <span class="flex items-center gap-1.5 text-[12px] text-text-tertiary">
              <span class="w-3 h-3 rounded-[2px] bg-bg-inset" /> 计划
            </span>
            <span class="flex items-center gap-1.5 text-[12px] text-text-tertiary">
              <span class="w-3 h-3 rounded-[2px] bg-primary" /> 实际
            </span>
            <span class="flex items-center gap-1.5 text-[12px] text-text-tertiary">
              <span class="w-3 border-t border-dashed" style="border-color: var(--warning)" /> 7 天均值
            </span>
          </div>
        </div>
      </div>

      <!-- 日增重折线（渐变面积 + 品种标准虚线） -->
      <div class="bg-card rounded-[12px] p-6 card-highlight">
        <div class="flex items-center justify-between mb-4">
          <h3 class="text-[16px] font-semibold text-text-primary">日增重 vs 品种标准</h3>
          <button
            @click="openWeigh"
            class="px-3 py-1.5 rounded-[8px] bg-primary-50 text-primary-700 text-[12.5px] font-medium hover:bg-primary-100 transition-colors"
          >
            称重登记
          </button>
        </div>
        <div v-if="points.length === 0" class="h-[220px] flex flex-col items-center justify-center">
          <Scale :size="28" style="color: var(--text-disabled)" :stroke-width="1.5" />
          <p class="text-[13px] text-text-tertiary mt-2">还没有称重记录</p>
          <p class="text-[11.5px] text-text-disabled mt-0.5">定期称重后自动计算日增重与标准对比</p>
        </div>
        <div v-else>
          <div class="relative">
            <svg
              :viewBox="`0 0 ${W} ${H}`"
              class="w-full"
              :style="{ height: '190px' }"
              preserveAspectRatio="none"
            >
              <defs>
                <linearGradient id="gainGradF6" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stop-color="var(--primary)" stop-opacity="0.12" />
                  <stop offset="100%" stop-color="var(--primary)" stop-opacity="0" />
                </linearGradient>
              </defs>
              <path v-if="areaD" :d="areaD" fill="url(#gainGradF6)" />
              <path
                v-if="gains.length >= 2"
                :d="lineD"
                fill="none"
                stroke="var(--primary)"
                stroke-width="2"
                stroke-linecap="round"
                stroke-linejoin="round"
              />
              <g v-for="(g, i) in gains" :key="g.date">
                <circle :cx="xOf(i, gains.length)" :cy="yOf(g.gain)" r="3.5" fill="var(--primary)" />
                <!-- ratio<0.8 单次标注 low（level 由服务端返回，前端按标准线比对渲染色） -->
                <circle
                  v-if="isLowGain(g.gain)"
                  :cx="xOf(i, gains.length)"
                  :cy="yOf(g.gain)"
                  r="6"
                  fill="none"
                  stroke="var(--warning)"
                  stroke-width="1.5"
                />
              </g>
              <!-- 品种标准虚线 -->
              <line
                v-if="standardLine !== null"
                x1="0"
                :y1="standardY"
                :x2="W"
                :y2="standardY"
                stroke="var(--chart-standard)"
                stroke-width="1.2"
                stroke-dasharray="6 4"
              />
            </svg>
            <!-- X 轴日期 -->
            <div class="absolute bottom-0 left-0 right-0 flex justify-between px-1">
              <span v-for="g in gains" :key="g.date" class="text-[10.5px] text-text-tertiary tnum">
                {{ g.date.slice(5) }}
              </span>
            </div>
          </div>
          <div class="flex items-center gap-4 mt-3 justify-center flex-wrap">
            <span class="flex items-center gap-1.5 text-[12px] text-text-tertiary">
              <span class="w-3 h-[2px] bg-primary" /> 实际日增重
            </span>
            <span v-if="standardLine !== null" class="flex items-center gap-1.5 text-[12px] text-text-tertiary">
              <span class="w-3 border-t border-dashed" style="border-color: var(--chart-standard)" /> 品种标准
              <span class="tnum">{{ standardLine }}</span> kg
            </span>
            <span v-if="standardLine === null" class="text-[12px] text-text-disabled">该品种暂无统一标准线</span>
          </div>
          <!-- 最近一次增重 vs 标准（TC-F10 数据区） -->
          <div v-if="lastGain" class="mt-3 pt-3 border-t border-border flex items-center justify-between text-[13px]">
            <span class="text-text-secondary"> 最近一次（{{ lastGain.date }}） </span>
            <span class="font-medium tnum" :style="{ color: lastGainColor }">
              {{ lastGain.gain }} kg/天
              <span v-if="standardLine !== null" class="text-text-tertiary font-normal">
                · 标准的 {{ standardPct(lastGain.gain) }}%
              </span>
            </span>
          </div>
        </div>
      </div>
    </div>

    <!-- 称重登记弹窗 -->
    <FeedingDialog
      v-if="weighOpen"
      :title="`称重登记 · ${pen.pen_name}`"
      subtitle="定期称重（建议 2~4 周一次）后自动计算日增重并与品种标准对比"
      :error="weighErr"
      :loading="weighLoading"
      submit-text="确认登记"
      @close="weighOpen = false"
      @cancel="weighOpen = false"
      @submit="submitWeigh(false)"
    >
      <div class="space-y-4 mb-5">
        <div>
          <label class="text-[13px] text-text-secondary block mb-1.5">称重日期</label>
          <input
            type="date"
            v-model="weighDate"
            :max="todayStr()"
            class="w-full px-3.5 py-2.5 rounded-[10px] border border-border-strong bg-bg-page text-[14px] tnum outline-none focus:border-primary"
          />
        </div>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <label class="text-[13px] text-text-secondary block mb-1.5">平均体重（kg）</label>
            <input
              type="number"
              min="0"
              step="0.1"
              :value="avgWeight"
              @input="onAvgWeightInput"
              placeholder="抽样均重"
              class="w-full px-3.5 py-2.5 rounded-[10px] border border-border-strong bg-bg-page text-[14px] tnum outline-none focus:border-primary"
            />
          </div>
          <div>
            <label class="text-[13px] text-text-secondary block mb-1.5">称重头数</label>
            <input
              type="number"
              min="1"
              step="1"
              :value="sampleCount"
              @input="onSampleCountInput"
              placeholder="抽样数量"
              class="w-full px-3.5 py-2.5 rounded-[10px] border border-border-strong bg-bg-page text-[14px] tnum outline-none focus:border-primary"
            />
          </div>
        </div>
        <div>
          <label class="text-[13px] text-text-secondary block mb-1.5">备注（选填）</label>
          <input
            type="text"
            v-model="remark"
            placeholder="如：空腹称重、抽样方式"
            class="w-full px-3.5 py-2.5 rounded-[10px] border border-border-strong bg-bg-page text-[14px] outline-none focus:border-primary"
          />
        </div>
      </div>
    </FeedingDialog>

    <FeedingDialog
      v-if="confirmNeed"
      title="请确认录入"
      submit-text="确认无误"
      @close="confirmNeed = null"
      @cancel="confirmNeed = null"
      @submit="runConfirmAction"
    >
      <div
        class="flex items-start gap-2.5 px-3.5 py-3 rounded-[10px] mb-5"
        style="background-color: rgba(232,162,61,.08); border: 1px solid rgba(232,162,61,.25)"
      >
        <AlertTriangle :size="16" :stroke-width="1.8" class="shrink-0 mt-0.5" style="color: var(--warning)" />
        <p class="text-[13.5px] text-text-secondary leading-[1.7]">{{ confirmNeed.message }}</p>
      </div>
    </FeedingDialog>
  </div>
</template>
