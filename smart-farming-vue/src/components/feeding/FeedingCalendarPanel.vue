<script setup lang="ts">
// Tab1 饲喂日历（FR-07/FR-11 ①）
// - 每圈「计划 vs 实际」进度条 + 按计划一键打卡 + 按圈登记弹窗（40005 确认重发）
// - TC-F09b：连续 3 天实际偏离计划 >20% → 校准提示（前端计算，F06 决策 3）
import { computed, ref, watch } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { AlertTriangle, ArrowRight, CheckCircle2, ChevronLeft, ChevronRight, Cog, Scale } from 'lucide-vue-next';
import { cn, todayLocal } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import type { UserPen } from '@/api/auth-api';
import type { FeedType, OverviewItem } from '@/api/feed-api';
import {
  apiCreateFeedingRecord,
  apiFeedingOverview,
  apiGetFeedPlan,
  apiListFeedingRecords,
  apiSeedFeedingDemo,
} from '@/api/feed-api';
import FeedingDialog from './FeedingDialog.vue';

const props = defineProps<{ pens: UserPen[]; farmId: number | null }>();
const emit = defineEmits<{ toast: [kind: 'ok' | 'err', text: string]; aiConsult: [prefill: string] }>();

const queryClient = useQueryClient();

const FEED_TYPE_LABEL: Record<FeedType, string> = {
  concentrate: '精料',
  roughage: '粗料',
  silage: '青贮',
  other: '其他',
};

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

function fmtDayCn(dateStr: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!m) return dateStr;
  return `${Number(m[1])}年${Number(m[2])}月${Number(m[3])}日`;
}

function errText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

const date = ref(todayStr());
const clocking = ref(false);
const recordPen = ref<UserPen | null>(null);
const dialogPen = ref<UserPen | null>(null);
const confirmNeed = ref<{ message: string; action: () => void } | null>(null);

// 本页其余 Tab、圈舍下拉、表头场名都来自 primaryFarm，日历也必须按同一场取数。
// 此前不带 farm_id，接口返回名下全部农场，于是日历混入别的场、一键打卡会跨场写入。
const { data: overviewData, isLoading: overviewLoading } = useQuery({
  queryKey: computed(() => ['feeding-overview', props.farmId ?? 0, date.value]),
  queryFn: () => apiFeedingOverview(date.value, props.farmId ?? undefined),
  enabled: computed(() => !!props.farmId),
});
const items = computed<OverviewItem[]>(() => overviewData.value?.items ?? []);
const pendingCount = computed(() => items.value.filter((i) => !i.registered && i.planned_kg !== null).length);

// TC-F09b：连续 3 天实际偏离计划 >20% → 校准提示（前端计算，F06 决策 3）
const deviationPens = ref<string[]>([]);
watch(
  [() => props.pens, date],
  async (_next, _prev, onCleanup) => {
    let cancelled = false;
    onCleanup(() => {
      cancelled = true;
    });
    async function checkDeviation() {
      const pens = props.pens;
      const from = addDays(todayStr(), -2);
      const alerts: string[] = [];
      for (const pen of pens) {
        try {
          const { items: recs } = await apiListFeedingRecords({ pen_id: pen.pen_id, from, to: todayStr() });
          const byDate = new Map<string, { planned: number; actual: number }>();
          for (const r of recs) {
            if (r.planned_kg === null) continue;
            const cur = byDate.get(r.feed_date) ?? { planned: 0, actual: 0 };
            cur.planned += r.planned_kg;
            cur.actual += r.amount_kg;
            byDate.set(r.feed_date, cur);
          }
          const days = [...byDate.entries()].sort((a, b) => b[0].localeCompare(a[0])).slice(0, 3);
          if (days.length === 3 && days.every(([, v]) => Math.abs(v.actual - v.planned) / v.planned > 0.2)) {
            alerts.push(pen.pen_name);
          }
        } catch {
          /* 单圈失败不影响整体 */
        }
      }
      if (!cancelled) deviationPens.value = alerts;
    }
    if (props.pens.length > 0) checkDeviation();
    else deviationPens.value = [];
  },
  { immediate: true },
);

function invalidateFeeding() {
  queryClient.invalidateQueries({ queryKey: ['feeding-overview'] });
  queryClient.invalidateQueries({ queryKey: ['feeding-records'] });
  queryClient.invalidateQueries({ queryKey: ['feed-conversion'] });
  queryClient.invalidateQueries({ queryKey: ['daily-gain'] });
}

// 按计划一键打卡：对未登记且有计划的圈逐个 as_planned 登记（TC-F07）
async function checkInAll() {
  if (clocking.value) return;
  const targets = items.value.filter((i) => !i.registered && i.planned_kg !== null);
  if (targets.length === 0) {
    emit('toast', 'err', '没有可打卡的圈舍（均已登记或暂无饲喂计划）');
    return;
  }
  clocking.value = true;
  try {
    for (const t of targets) {
      await apiCreateFeedingRecord({ pen_id: t.pen_id, feed_type: 'concentrate', as_planned: true });
    }
    emit('toast', 'ok', `已按计划打卡 ${targets.length} 个圈舍`);
    invalidateFeeding();
  } catch (e) {
    emit('toast', 'err', errText(e, '打卡失败，请重试'));
  } finally {
    clocking.value = false;
  }
}

// 演示数据载入（用户主动触发）：走真后端 `POST /feeding/seed-demo`，计划量由引擎现算，
// 每条记录都带 remark 标注演示来源，幂等保护在服务端
async function loadDemo() {
  try {
    const res = await apiSeedFeedingDemo();
    emit('toast', 'ok', res.message);
    invalidateFeeding();
  } catch (e) {
    emit('toast', 'err', errText(e, '演示数据载入失败'));
  }
}

const hasAnyRecord = computed(() => items.value.some((i) => i.registered));
const isToday = computed(() => date.value === todayStr());
const unregisteredNames = computed(() =>
  items.value
    .filter((i) => !i.registered && i.planned_kg !== null)
    .map((i) => i.pen_name)
    .join('、'),
);

function rowProgress(row: OverviewItem): number {
  const planned = row.planned_kg;
  const actual = row.actual_kg ?? 0;
  return planned !== null && planned > 0 ? (actual / planned) * 100 : 0;
}
function rowUnregistered(row: OverviewItem): boolean {
  return !row.registered;
}
function rowOverPlanned(row: OverviewItem): boolean {
  return row.planned_kg !== null && (row.actual_kg ?? 0) > row.planned_kg;
}
function rowRegistrationDisabled(row: OverviewItem): boolean {
  return row.planned_kg === null && row.plan_source === 'none' && !row.registered;
}
function onRecord(row: OverviewItem) {
  const pen = props.pens.find((p) => p.pen_id === row.pen_id);
  if (pen) openRecord(pen);
}

// ===== 按圈登记弹窗（FR-07：feed_type + amount/as_planned + 40005 确认重发）=====

const feedType = ref<FeedType>('concentrate');
const amount = ref('');
const remark = ref('');
const recordLoading = ref(false);
const recordErr = ref<string | null>(null);
const planSourceLabel = ref('');

// 数字输入框按 React 版保留字符串状态（v-model 会因 type=number 自动转成 number）
function onAmountInput(e: Event) {
  amount.value = (e.target as HTMLInputElement).value;
}

function openRecord(pen: UserPen) {  dialogPen.value = pen;
  recordPen.value = pen;
  feedType.value = 'concentrate';
  amount.value = '';
  remark.value = '';
  recordLoading.value = false;
  recordErr.value = null;
  planSourceLabel.value = '';
  // 拉计划量展示参考（登记时服务端实时快照，口径 458）
  apiGetFeedPlan(pen.pen_id)
    .then((plan) => {
      planSourceLabel.value =
        plan.daily_plan_kg !== null
          ? `当日计划量 ${plan.daily_plan_kg} kg（${plan.plan_source === 'formula' ? '生效配方' : '标准兜底'}）`
          : '暂无饲喂计划（请先生成配方，或手工填写数量）';
    })
    .catch(() => (planSourceLabel.value = ''));
}

async function submitRecord(confirm = false) {
  const pen = dialogPen.value;
  if (!pen) return;
  const amountNum = amount.value.trim() === '' ? null : Number(amount.value);
  if (amountNum !== null && (!Number.isFinite(amountNum) || amountNum <= 0)) {
    recordErr.value = '投喂量需大于 0';
    return;
  }
  recordLoading.value = true;
  recordErr.value = null;
  try {
    const res = await apiCreateFeedingRecord({
      pen_id: pen.pen_id,
      feed_date: date.value,
      feed_type: feedType.value,
      amount_kg: amountNum ?? undefined,
      remark: remark.value.trim() || undefined,
      confirm: confirm || undefined,
    });
    recordPen.value = null;
    emit('toast', 'ok', `已登记 ${pen.pen_name} ${FEED_TYPE_LABEL[feedType.value]} ${res.amount_kg} kg`);
    invalidateFeeding();
  } catch (e) {
    if (e instanceof ApiError && e.code === 40005) {
      // EX-14：超 3 倍确认——确认后附 confirm 重发
      const action = () => submitRecord(true);
      recordPen.value = null;
      confirmNeed.value = { message: e.message, action };
      return;
    }
    recordErr.value = errText(e, '登记失败，请重试');
  } finally {
    recordLoading.value = false;
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
    <!-- 操作栏：日期切换 + 一键打卡 -->
    <div class="flex items-center justify-between gap-4 flex-wrap">
      <div class="flex items-center gap-2">
        <button
          @click="date = addDays(date, -1)"
          class="p-2 rounded-[8px] border border-border bg-card hover:bg-bg-subtle transition-colors"
          aria-label="前一天"
        >
          <ChevronLeft :size="16" :stroke-width="1.5" />
        </button>
        <span class="text-[14px] text-text-primary font-medium tnum px-1">{{ fmtDayCn(date) }}</span>
        <span v-if="isToday" class="px-2 py-0.5 rounded-[6px] bg-primary-50 text-primary-700 text-[12px] font-medium">今天</span>
        <button
          @click="date = date >= todayStr() ? date : addDays(date, 1)"
          :disabled="isToday"
          class="p-2 rounded-[8px] border border-border bg-card hover:bg-bg-subtle transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          aria-label="后一天"
        >
          <ChevronRight :size="16" :stroke-width="1.5" />
        </button>
      </div>
      <div class="flex items-center gap-3">
        <button
          v-if="!hasAnyRecord && pens.length > 0"
          @click="loadDemo"
          class="flex items-center gap-2 px-4 py-2 rounded-[10px] border border-border bg-card text-text-secondary text-[13.5px] hover:border-primary hover:text-primary transition-colors"
        >
          <Cog :size="15" :stroke-width="1.5" />
          载入演示数据
        </button>
        <button
          @click="checkInAll"
          :disabled="clocking || pendingCount === 0"
          :class="
            cn(
              'flex items-center gap-2 px-4 py-2 rounded-[10px] text-[14px] font-medium transition-colors',
              pendingCount === 0 || clocking
                ? 'bg-bg-subtle text-text-disabled cursor-not-allowed'
                : 'bg-primary text-white hover:bg-primary-600',
            )
          "
        >
          <CheckCircle2 :size="16" :stroke-width="1.5" />
          {{ clocking ? '打卡中…' : `按计划一键打卡${pendingCount > 0 ? `（${pendingCount}）` : ''}` }}
        </button>
      </div>
    </div>

    <!-- 校准提示（TC-F09b：连续 3 天偏差 >20%） -->
    <div
      v-if="deviationPens.length > 0"
      class="flex items-center gap-3 px-4 py-3 rounded-[12px] border-l-4 border-warning"
      style="background-color: rgba(232,162,61,0.08)"
    >
      <Scale :size="18" class="shrink-0" style="color: var(--warning)" :stroke-width="1.5" />
      <span class="text-[14px] text-text-primary">
        <span class="font-medium">{{ deviationPens.join('、') }}</span> 连续 3 天实际长期偏离计划，建议校准计划或排查原因
      </span>
    </div>

    <!-- 日历行：每圈一条"计划 vs 实际"进度条 -->
    <div class="bg-card rounded-[12px] p-6 card-highlight">
      <p v-if="overviewLoading" class="text-[13.5px] text-text-tertiary py-8 text-center">加载中…</p>
      <p v-if="!overviewLoading && items.length === 0" class="text-[13.5px] text-text-tertiary py-8 text-center">
        还没有圈舍——去
        <a href="/farm" class="text-primary font-medium hover:underline">场舍管理</a>
        创建圈舍后开始记录饲喂
      </p>
      <div class="space-y-4">
        <div v-for="row in items" :key="row.pen_id" class="flex items-center gap-4">
          <div class="w-[130px] shrink-0">
            <div class="text-[14px] font-medium text-text-primary">{{ row.pen_name }}</div>
            <!-- 计划来源标注（FR-11：生效配方 / 标准兜底 / 请先生成配方） -->
            <span
              :class="
                cn(
                  'text-[11px]',
                  row.plan_source === 'formula' && 'text-primary',
                  row.plan_source === 'standard' && 'text-text-tertiary',
                  row.plan_source === 'none' && 'text-text-disabled',
                )
              "
            >
              {{ row.plan_source === 'formula' ? '生效配方' : row.plan_source === 'standard' ? '标准兜底' : '请先生成配方' }}
            </span>
          </div>
          <div class="flex-1">
            <div class="relative h-8 bg-bg-subtle rounded-[8px] overflow-hidden">
              <!-- 实际进度条（主色渐变；未登记 warning @ 20% 高亮） -->
              <div
                :class="
                  cn(
                    'absolute left-0 top-0 h-full rounded-[8px] transition-all duration-[var(--duration-slow)]',
                    !rowUnregistered(row) && !rowOverPlanned(row) ? 'bg-gradient-to-r from-primary-200 to-primary' : '',
                  )
                "
                :style="{
                  width: `${Math.min(rowProgress(row), 100)}%`,
                  backgroundColor: rowUnregistered(row)
                    ? 'rgba(232,162,61,.2)'
                    : rowOverPlanned(row)
                      ? 'rgba(232,162,61,.45)'
                      : undefined,
                }"
              />
              <!-- 计划刻度竖线（border-strong） -->
              <div
                v-if="row.planned_kg !== null && row.planned_kg > 0"
                class="absolute top-0 h-full w-[2px] bg-border-strong"
                style="left: 100%"
              />
              <!-- 数字标签 -->
              <div class="absolute inset-0 flex items-center px-3">
                <span class="text-[13px] font-medium tnum text-text-primary">
                  {{ rowUnregistered(row) ? '未登记' : `${row.actual_kg ?? 0}` }}
                </span>
                <span class="text-[12px] text-text-tertiary tnum ml-1">
                  / {{ row.planned_kg === null ? '— 无计划' : `${row.planned_kg} kg` }}
                </span>
              </div>
            </div>
          </div>
          <div class="w-[52px] text-right shrink-0">
            <span
              :class="
                cn(
                  'text-[13px] font-medium tnum',
                  rowProgress(row) >= 90 && row.planned_kg !== null
                    ? 'text-success'
                    : rowProgress(row) > 0
                      ? 'text-warning'
                      : 'text-text-tertiary',
                )
              "
            >
              {{ rowProgress(row) > 0 && row.planned_kg !== null ? `${Math.round(rowProgress(row))}%` : '—' }}
            </span>
          </div>
          <button
            @click="onRecord(row)"
            :disabled="rowRegistrationDisabled(row)"
            class="shrink-0 px-3 py-1.5 rounded-[8px] text-[12.5px] font-medium bg-primary-50 text-primary-700 hover:bg-primary-100 transition-colors disabled:opacity-40 disabled:cursor-not-allowed"
          >
            登记
          </button>
        </div>
      </div>
    </div>

    <!-- 采食异常提示（前端判定数据由 Tab3 图共享逻辑；此处展示未登记提醒 + 问诊入口） -->
    <div
      v-if="items.some((i) => !i.registered && i.planned_kg !== null)"
      class="flex items-center gap-3 px-4 py-3 rounded-[12px] border-l-4 border-warning"
      style="background-color: rgba(232,162,61,0.08)"
    >
      <AlertTriangle :size="18" class="shrink-0" style="color: var(--warning)" :stroke-width="1.5" />
      <span class="text-[14px] text-text-primary">{{ unregisteredNames }} 今日尚未登记饲喂</span>
      <button
        @click="emit('aiConsult', '圈舍今日尚未登记饲喂，最近采食情况需要排查，请帮我分析可能的原因。')"
        class="ml-auto text-[13px] text-primary font-medium hover:text-primary-600 transition-colors flex items-center gap-1"
      >
        AI 归因分析
        <ArrowRight :size="14" :stroke-width="1.5" />
      </button>
    </div>

    <!-- 按圈登记弹窗 -->
    <FeedingDialog
      v-if="recordPen && dialogPen"
      :title="`登记饲喂 · ${dialogPen.pen_name}`"
      :subtitle="date === todayStr() ? '今天' : fmtDayCn(date)"
      :error="recordErr"
      :loading="recordLoading"
      submit-text="确认登记"
      @close="recordPen = null"
      @cancel="recordPen = null"
      @submit="submitRecord(false)"
    >
      <p v-if="planSourceLabel" class="text-[12.5px] text-text-tertiary mb-4">{{ planSourceLabel }}</p>
      <div class="space-y-4 mb-5">
        <div>
          <label class="text-[13px] text-text-secondary block mb-1.5">饲料类型</label>
          <div class="flex gap-2 flex-wrap">
            <button
              v-for="t in Object.keys(FEED_TYPE_LABEL) as FeedType[]"
              :key="t"
              @click="feedType = t"
              :class="
                cn(
                  'px-3.5 py-2 rounded-[8px] text-[13px] transition-colors',
                  feedType === t
                    ? 'bg-primary-50 text-primary-700 font-medium border border-primary'
                    : 'bg-bg-subtle text-text-secondary border border-transparent hover:text-text-primary',
                )
              "
            >
              {{ FEED_TYPE_LABEL[t] }}
            </button>
          </div>
        </div>
        <div>
          <label class="text-[13px] text-text-secondary block mb-1.5">投喂量（kg）</label>
          <input
            type="number"
            min="0"
            step="0.1"
            :value="amount"
            @input="onAmountInput"
            placeholder="填写实际投喂量"
            class="w-full px-3.5 py-2.5 rounded-[10px] border border-border-strong bg-bg-page text-[14px] tnum outline-none focus:border-primary"
          />
        </div>
        <div>
          <label class="text-[13px] text-text-secondary block mb-1.5">备注（选填）</label>
          <input
            type="text"
            v-model="remark"
            placeholder="如：加料原因、天气影响等"
            class="w-full px-3.5 py-2.5 rounded-[10px] border border-border-strong bg-bg-page text-[14px] outline-none focus:border-primary"
          />
        </div>
      </div>
    </FeedingDialog>

    <!-- 40005 确认弹窗（确认后重发） -->
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
