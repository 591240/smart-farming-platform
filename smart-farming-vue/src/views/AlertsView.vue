<script setup lang="ts">
// 告警中心页 — F03 FR-06/07（06 规范 4.8，mock 切真）
// - 数据源：GET /alert/list（真实告警，含传感器超标与设备离线）
// - 实时性：subscribeAlerts 订阅消费管道推送（mock 等价 /ws/alerts），新告警滑入 + 色条闪烁
// - 状态闭环：pending → acknowledged（一键已读）→ resolved（备注必填，42201 拦截）；处理落台账
// - F14 个体绑定：卡片 [记入个体] 把告警内容写为该个体的发病/诊疗事件——与"处理"解耦，
//   已处理或判误报的告警同样可以补档（追溯的是动物，不是告警状态）
// - 类型筛选：vision 自 F09 起有源（vision_sim 注入/边缘网关事件）；disease 仍空态展示（F03 PRD 四节）
// - F09 免责三处之一：vision 告警卡底部固定免责文案（VISION_DISCLAIMER 常量同源，逐字一致）
// - 生产提醒不在本页：唯一归属为繁殖管理（/breeding），避免同一份 todo 两处展示、两处可写

import { computed, onBeforeUnmount, onMounted, ref, watch, type Component } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/vue-query';
import {
  Activity,
  CloudRain,
  Eye,
  HeartPulse,
  Cpu,
  Bell,
  Check,
  X,
  Filter,
  MapPin,
  NotebookPen,
} from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import {
  apiHandleAlert,
  apiListAlerts,
  type AlertItem,
  type AlertStatus,
  type AlertType,
} from '@/api/device-api';
import { subscribeAlerts } from '@/lib/alert-stream';
import { ApiError } from '@/lib/api-contract';
import { VISION_DISCLAIMER } from '@/lib/vision-constants';
import RecordToIndividualDialog from '@/components/individual/RecordToIndividualDialog.vue';
import type { PickedIndividual } from '@/components/individual/types';

/** F09：预览卡 [查看全部] 跳 /alerts?type=vision —— 初始筛选读 URL（白名单校验，非法值回落 all） */
const VALID_TYPES: (AlertType | 'all')[] = ['all', 'sensor', 'climate', 'vision', 'disease', 'device_offline'];

// 风险色用 CSS 变量 + 内联 style（Tailwind v4 不支持自定义色 /N 透明度语法，见 AGENTS.md）
const RISK_STYLE: Record<AlertItem['risk_level'], { label: string; color: string; bg: string }> = {
  high: { label: '高危', color: 'var(--danger)', bg: 'rgba(217,83,79,.06)' },
  mid: { label: '中危', color: 'var(--warning)', bg: 'rgba(232,162,61,.07)' },
  low: { label: '低危', color: 'var(--success)', bg: 'rgba(46,158,107,.07)' },
};

const TYPE_META: Record<AlertType, { label: string; icon: Component }> = {
  sensor: { label: '传感器', icon: Activity },
  climate: { label: '气象', icon: CloudRain },
  vision: { label: '视觉', icon: Eye },
  disease: { label: '疫病', icon: HeartPulse },
  device_offline: { label: '设备离线', icon: Cpu },
};

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return '刚刚';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return iso.slice(0, 16).replace('T', ' ');
}

const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();

// 初始筛选读 URL（等价原路由 validateSearch：type 白名单校验，非法值回落 all）
const searchType = typeof route.query.type === 'string' ? route.query.type : undefined;
const initialType: AlertType | 'all' =
  searchType && searchType !== 'all' && (VALID_TYPES as string[]).includes(searchType)
    ? (searchType as AlertType)
    : 'all';
// focus：从大屏点进来时定位并高亮某一条（非数字一律忽略，避免脏 URL 卡住页面）
const focusId = computed<number | null>(() => {
  const f = Number(route.query.focus);
  return Number.isInteger(f) && f > 0 ? f : null;
});

const statusFilter = ref<AlertStatus | 'all'>('all');
const typeFilter = ref<AlertType | 'all'>(initialType);

// 服务端筛选 + 分页加载：此前固定拉最新 100 条再在前端 filter，超过 100 条后
// 旧告警永远看不到，「待处理」Tab 的结果也只是前 100 条里的样本。
const { data: alertPages, fetchNextPage, hasNextPage, isFetchingNextPage } = useInfiniteQuery({
  queryKey: computed(() => ['alerts', statusFilter.value, typeFilter.value]),
  initialPageParam: 1,
  queryFn: ({ pageParam }) =>
    apiListAlerts({
      status: statusFilter.value === 'all' ? undefined : statusFilter.value,
      type: typeFilter.value === 'all' ? undefined : typeFilter.value,
      page: pageParam,
      page_size: 50,
    }),
  getNextPageParam: (last) => (last.page * last.page_size < last.total ? last.page + 1 : undefined),
  refetchInterval: 15_000,
});
const alertList = computed(() => alertPages.value?.pages.flatMap((p) => p.items) ?? []);

// 标签计数走服务端：与顶栏红点同源，不受列表分页影响。
const countBy = (status?: AlertStatus) => apiListAlerts({ status, page: 1, page_size: 1 });
const { data: serverCounts } = useQuery({
  queryKey: ['alerts', 'counts'],
  refetchInterval: 15_000,
  queryFn: async () => {
    const [all, pending, acknowledged, resolved, falseAlarm] = await Promise.all([
      countBy(), countBy('pending'), countBy('acknowledged'), countBy('resolved'), countBy('false_alarm'),
    ]);
    return {
      all: all.total,
      pending: pending.total,
      acknowledged: acknowledged.total,
      resolved: resolved.total,
      false_alarm: falseAlarm.total,
    };
  },
});

// 大屏/其他入口带 `?focus=<alert_id>` 进来时：滚到那一条并短暂描边高亮。
// 找不到（例如已被处理、不在当前筛选）就什么都不做——不弹错、不改筛选，
// 因为"这条已经不在这份列表里"本身就是正确信息，硬造定位反而误导。
const flashId = ref<number | null>(null);
let flashTimer: number | null = null;

function clearFlashTimer() {
  if (flashTimer !== null) {
    window.clearTimeout(flashTimer);
    flashTimer = null;
  }
}

function focusAlert() {
  const id = focusId.value;
  if (!id || alertList.value.length === 0) return;
  const el = document.querySelector(`[data-alert-id="${id}"]`);
  if (!el) return;
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
  flashId.value = id;
  clearFlashTimer();
  flashTimer = window.setTimeout(() => {
    flashId.value = null;
    flashTimer = null;
  }, 2400);
}

// 列表长度变化后（DOM 已更新）再按 focusId 定位
watch(
  () => [focusId.value, alertList.value.length] as const,
  () => {
    clearFlashTimer();
    focusAlert();
  },
  { flush: 'post' },
);

// 实时订阅（等价 WebSocket 推送）：新告警到达即刷新列表
let unsubscribe: (() => void) | null = null;
onMounted(() => {
  focusAlert();
  unsubscribe = subscribeAlerts(() => queryClient.invalidateQueries({ queryKey: ['alerts'] }));
});
onBeforeUnmount(() => {
  clearFlashTimer();
  unsubscribe?.();
});

const handleDialog = ref<AlertItem | null>(null);
const handleNote = ref('');
const handleErr = ref<string | null>(null);
const archiveAlert = ref<AlertItem | null>(null);
const toast = ref<string | null>(null);

const counts = computed(
  () =>
    serverCounts.value ?? {
      all: alertList.value.length,
      pending: alertList.value.filter((a) => a.status === 'pending').length,
      acknowledged: alertList.value.filter((a) => a.status === 'acknowledged').length,
      resolved: alertList.value.filter((a) => a.status === 'resolved').length,
      false_alarm: alertList.value.filter((a) => a.status === 'false_alarm').length,
    },
);

const statusTabs: { key: AlertStatus | 'all'; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'pending', label: '未处理' },
  { key: 'acknowledged', label: '已读未处理' },
  { key: 'resolved', label: '已处理' },
  { key: 'false_alarm', label: '误报' },
];

const typeFilters: { key: AlertType | 'all'; label: string; icon: Component }[] = (
  ['all', 'sensor', 'climate', 'vision', 'disease', 'device_offline'] as (AlertType | 'all')[]
).map((key) => {
  const meta = key === 'all' ? { label: '全部类型', icon: Filter } : TYPE_META[key];
  return { key, label: meta.label, icon: meta.icon };
});

function showToast(msg: string) {
  toast.value = msg;
  window.setTimeout(() => (toast.value = null), 2600);
}

async function markAcknowledged(a: AlertItem) {
  try {
    await apiHandleAlert(a.alert_id, { action: 'acknowledge' });
    queryClient.invalidateQueries({ queryKey: ['alerts'] });
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '网络异常，请重试');
  }
}

async function submitHandle(action: 'resolve' | 'false_alarm') {
  if (!handleDialog.value) return;
  const note = handleNote.value.trim();
  // 误报同样必须写理由：它会落一条「判定误报」台账，是事后追溯"这条为什么没处理"的唯一依据
  if (!note) {
    handleErr.value = '请填写理由';
    return;
  }
  handleErr.value = null;
  try {
    await apiHandleAlert(handleDialog.value.alert_id, { action, note });
    handleDialog.value = null;
    handleNote.value = '';
    queryClient.invalidateQueries({ queryKey: ['alerts'] });
    showToast(action === 'resolve' ? '已处理并记入台账' : '已判定为误报并记入台账');
  } catch (e) {
    // EX-C8：备注为空等服务端 42201 拦截
    handleErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  }
}

function openHandle(alert: AlertItem) {
  handleNote.value = '';
  handleErr.value = null;
  handleDialog.value = alert;
}

// F06 联动：跳转 3D 场景定位告警位置（search 键名与大屏页读取端一致）
function locateAlert(alert: AlertItem) {
  router.push({
    path: '/showcase3d',
    query: {
      alert_type: alert.alert_type,
      pen_name: alert.pen_name,
      content: alert.alert_content,
    },
  });
}

function onArchived(picked: PickedIndividual) {
  archiveAlert.value = null;
  showToast(`已记入 ${picked.ear_tag_no} 的个体档案`);
}

// ===== 告警卡片辅助（原 AlertCard 函数组件内联展开，样式/文案逐行一致） =====
function riskOf(alert: AlertItem) {
  return RISK_STYLE[alert.risk_level] ?? RISK_STYLE.mid;
}

function metaOf(alert: AlertItem) {
  return TYPE_META[alert.alert_type];
}

function typeLabel(t: AlertType): string {
  return TYPE_META[t]?.label ?? t;
}

function isFresh(alert: AlertItem): boolean {
  return Date.now() - new Date(alert.create_time).getTime() < 5_000;
}

function cardStyle(alert: AlertItem): Record<string, string> {
  const risk = riskOf(alert);
  return {
    borderLeftColor: risk.color,
    boxShadow: 'var(--e1)',
    ...(alert.status === 'pending' ? { backgroundColor: risk.bg } : {}),
  };
}

function flashStyle(alert: AlertItem): Record<string, string> | undefined {
  return flashId.value === alert.alert_id
    ? { boxShadow: '0 0 0 2px var(--primary)', borderRadius: '12px', transition: 'box-shadow .3s' }
    : undefined;
}
</script>

<template>
  <div class="px-8 py-6 max-w-[1400px]">
    <div class="mb-6">
      <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">告警中心</h1>
      <p class="text-[14px] text-text-secondary mt-0.5">
        {{ counts.pending }} 条未处理 · {{ counts.acknowledged }} 条待跟进
      </p>
    </div>

    <!-- 状态筛选 Tab -->
    <div class="flex items-center gap-1 mb-4 bg-bg-subtle rounded-[10px] p-1 w-fit">
      <button
        v-for="tab in statusTabs"
        :key="tab.key"
        @click="statusFilter = tab.key"
        :class="
          cn(
            'flex items-center gap-1.5 px-4 py-2 rounded-[8px] text-[13px] transition-all duration-[var(--duration-fast)]',
            statusFilter === tab.key
              ? 'bg-card text-text-primary font-medium e1'
              : 'text-text-secondary hover:text-text-primary',
          )
        "
      >
        {{ tab.label }}
        <span
          v-if="counts[tab.key] > 0"
          :class="
            cn(
              'min-w-[18px] h-[18px] flex items-center justify-center rounded-full text-[11px] font-medium px-1',
              statusFilter === tab.key ? 'bg-primary-100 text-primary-700' : 'bg-bg-inset text-text-tertiary',
            )
          "
        >
          {{ counts[tab.key] }}
        </span>
      </button>
    </div>

    <!-- 类型筛选 -->
    <div class="flex items-center gap-2 mb-6 flex-wrap">
      <button
        v-for="opt in typeFilters"
        :key="opt.key"
        @click="typeFilter = opt.key"
        :class="
          cn(
            'flex items-center gap-1.5 px-3 py-1.5 rounded-[8px] text-[13px] transition-colors duration-[var(--duration-fast)]',
            typeFilter === opt.key
              ? 'bg-primary-100 text-primary-700 font-medium'
              : 'bg-bg-subtle text-text-secondary hover:bg-bg-inset',
          )
        "
      >
        <component :is="opt.icon" :size="14" :stroke-width="1.5" />
        {{ opt.label }}
      </button>
    </div>

    <!-- 告警列表（5 秒内新产生的卡片带滑入动画） -->
    <div class="space-y-3">
      <div
        v-for="alert in alertList"
        :key="alert.alert_id"
        :data-alert-id="alert.alert_id"
        :style="flashStyle(alert)"
      >
        <div
          :class="
            cn(
              'relative flex items-start gap-4 bg-card rounded-[12px] p-5 border-l-4 transition-all',
              'duration-[var(--duration-base)]',
              alert.status === 'resolved' ? 'opacity-60' : '',
              isFresh(alert) ? 'animate-slide-in' : '',
            )
          "
          :style="cardStyle(alert)"
        >
          <div
            class="w-10 h-10 rounded-[10px] flex items-center justify-center shrink-0"
            :style="{ backgroundColor: riskOf(alert).bg }"
          >
            <component :is="metaOf(alert).icon" :size="20" :style="{ color: riskOf(alert).color }" :stroke-width="1.5" />
          </div>

          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 mb-1">
              <h4 class="text-[14px] font-medium text-text-primary">
                {{ alert.pen_name }} · {{ metaOf(alert)?.label ?? alert.alert_type }}
              </h4>
              <span
                class="px-1.5 py-0.5 rounded-[4px] text-[11px] font-medium"
                :style="{ backgroundColor: riskOf(alert).bg, color: riskOf(alert).color }"
              >
                {{ riskOf(alert).label }}
              </span>
            </div>
            <p class="text-[13px] text-text-secondary mb-2">{{ alert.alert_content }}</p>
            <div class="flex items-center gap-3">
              <span class="text-[12px] text-text-tertiary" :title="alert.create_time">
                {{ relTime(alert.create_time) }}
              </span>
              <span v-if="alert.status === 'pending'" class="flex items-center gap-1 text-[12px] text-primary">
                <span class="w-2 h-2 rounded-full bg-primary animate-breathe" />
                未处理
              </span>
              <span v-if="alert.status === 'acknowledged'" class="text-[12px] text-text-tertiary">已读未处理</span>
              <span v-if="alert.status === 'resolved'" class="flex items-center gap-1 text-[12px] text-success">
                <Check :size="12" :stroke-width="1.5" />
                已处理
              </span>
              <span v-if="alert.status === 'false_alarm'" class="text-[12px] text-text-tertiary">判定误报（不计入待办）</span>
            </div>
            <p
              v-if="(alert.status === 'resolved' || alert.status === 'false_alarm') && alert.handled_note"
              class="text-[12.5px] text-text-tertiary mt-1.5"
            >
              {{ alert.status === 'false_alarm' ? '误报理由：' : '处理：' }}{{ alert.handled_note }}
            </p>
            <!-- F09 免责三处之一：vision 告警卡底部固定免责文案（与预览卡/[预览] 弹窗同源常量逐字一致） -->
            <p v-if="alert.alert_type === 'vision'" class="text-[11.5px] text-text-tertiary mt-1.5">{{ VISION_DISCLAIMER }}</p>
          </div>

          <div class="flex items-center gap-2 shrink-0 flex-wrap justify-end">
            <!-- F06 联动：跳转 3D 场景定位告警位置 -->
            <button
              @click="locateAlert(alert)"
              title="在 3D 场景中定位该告警位置"
              class="flex items-center gap-1 px-3 py-1.5 rounded-[8px] text-[13px] text-primary-700 bg-primary-50 hover:bg-primary-100 transition-colors"
            >
              <MapPin :size="13" :stroke-width="1.5" />
              3D 定位
            </button>
            <!-- F14：设备离线不是动物发病，不给归档入口 -->
            <button
              v-if="alert.alert_type !== 'device_offline'"
              @click="archiveAlert = alert"
              title="把这条告警记为该个体的发病/诊疗档案"
              class="flex items-center gap-1 px-3 py-1.5 rounded-[8px] text-[13px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
            >
              <NotebookPen :size="13" :stroke-width="1.5" />
              记入个体
            </button>
            <button
              v-if="alert.status === 'pending'"
              @click="markAcknowledged(alert)"
              class="px-3 py-1.5 rounded-[8px] text-[13px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
            >
              已读
            </button>
            <button
              v-if="alert.status !== 'resolved'"
              @click="openHandle(alert)"
              class="px-3 py-1.5 rounded-[8px] text-[13px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors duration-[var(--duration-fast)]"
            >
              处理
            </button>
          </div>
        </div>
      </div>

      <div v-if="alertList.length === 0" class="text-center py-16">
        <Bell :size="40" class="mx-auto mb-3" :style="{ color: 'var(--text-disabled)' }" :stroke-width="1.5" />
        <p class="text-[14px]" :style="{ color: 'var(--text-tertiary)' }">
          暂无告警记录
        </p>
        <p class="text-[12.5px] mt-1" :style="{ color: 'var(--text-disabled)' }">
          传感器超标（连续 3 次）或设备离线时，告警将出现在这里
        </p>
      </div>
      <div v-if="hasNextPage" class="text-center pt-1">
        <button
          @click="fetchNextPage()"
          :disabled="isFetchingNextPage"
          class="px-4 py-2 rounded-[10px] text-[13.5px] text-text-secondary bg-bg-subtle hover:bg-bg-inset disabled:opacity-60 transition-colors"
        >
          {{ isFetchingNextPage ? '加载中…' : '加载更多' }}
        </button>
      </div>
    </div>

    <!-- 处理弹窗 -->
    <div v-if="handleDialog" class="fixed inset-0 z-50 flex items-center justify-center" @click="handleDialog = null">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[480px] max-w-[90vw] p-6 animate-modal-in"
        :style="{ boxShadow: 'var(--e3)' }"
        @click.stop
      >
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-[16px] font-semibold text-text-primary">处理告警</h2>
          <button @click="handleDialog = null" class="p-1 rounded-[6px] hover:bg-bg-subtle" aria-label="关闭">
            <X :size="18" class="text-text-tertiary" :stroke-width="1.5" />
          </button>
        </div>
        <div class="mb-4">
          <p class="text-[13px] text-text-tertiary mb-1">
            {{ handleDialog.pen_name }} · {{ typeLabel(handleDialog.alert_type) }}
          </p>
          <p class="text-[14px] font-medium text-text-primary">{{ handleDialog.alert_content }}</p>
        </div>
        <div class="mb-4">
          <label for="al-note" class="text-[13px] text-text-secondary mb-2 block">
            处理备注 <span :style="{ color: 'var(--danger)' }">*</span>
          </label>
          <textarea
            id="al-note"
            v-model="handleNote"
            placeholder="记录处理措施，如：已开风机通风降温"
            class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary focus:ring-1 focus:ring-primary/20 transition-colors resize-none h-[80px]"
          />
        </div>
        <div
          v-if="handleErr"
          class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
          :style="{
            backgroundColor: 'rgba(217,83,79,.07)',
            border: '1px solid rgba(217,83,79,.25)',
            color: 'var(--danger)',
          }"
          role="alert"
        >
          {{ handleErr }}
        </div>
        <div class="flex justify-end gap-3">
          <button
            @click="handleDialog = null"
            class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          >
            取消
          </button>
          <button
            @click="submitHandle('false_alarm')"
            :disabled="!handleNote.trim()"
            :class="
              cn(
                'px-4 py-2 rounded-[10px] text-[14px] font-medium transition-colors',
                handleNote.trim()
                  ? 'text-text-secondary bg-bg-subtle hover:bg-bg-inset'
                  : 'text-text-disabled bg-bg-inset cursor-not-allowed',
              )
            "
          >
            判定误报
          </button>
          <button
            @click="submitHandle('resolve')"
            :disabled="!handleNote.trim()"
            :class="
              cn(
                'px-4 py-2 rounded-[10px] text-[14px] font-medium transition-colors',
                handleNote.trim()
                  ? 'bg-primary text-white hover:bg-primary-600'
                  : 'bg-bg-inset text-text-disabled cursor-not-allowed',
              )
            "
          >
            标记已处理
          </button>
        </div>
      </div>
    </div>

    <RecordToIndividualDialog
      v-if="archiveAlert"
      :prefill="{ symptom: archiveAlert.alert_content }"
      @close="archiveAlert = null"
      @saved="onArchived"
    />

    <div
      v-if="toast"
      class="fixed bottom-8 left-1/2 -translate-x-1/2 z-[60] px-4 py-2.5 rounded-[10px] bg-[#2A332C] text-white text-[13px] animate-slide-in"
      :style="{ boxShadow: 'var(--e3)' }"
      role="status"
    >
      {{ toast }}
    </div>
  </div>
</template>
