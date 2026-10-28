<script setup lang="ts">
// 喂食中心页 — F06 FR-11（06 规范 4.6 四 Tab，mock 切真）
// - Tab1 饲喂日历：按天视图（日期切换）、每圈"计划 vs 实际"进度条、按计划一键打卡、
//   按圈登记弹窗（超 3 倍 40005 确认 EX-14）、连续 3 天偏差 >20% 校准提示（TC-F09b）、演示数据载入
// - Tab2 当前配方：菜谱卡（原料表 + 40px 成本大数字 + 营养达标表 EX-16 红字 + 依据来源 +
//   "AI 生成，仅供参考"）、换个配方弹窗（排除原料约束 + 替代方案横排卡片 + 成本色阶）、
//   原料价格弹窗（改价即时重算 TC-F05 + >30 天灰标 EX-17）
// - Tab3 采食与增重：采食柱状图（7 天均值虚线 + 降 15% 异常提示跳问诊 FR-09）、
//   日增重折线（品种标准虚线 + 连续 2 次 ratio<0.8 mid 提醒 TC-F10）、称重登记弹窗（骤降确认 EX-15）
// - Tab4 成本结算：日均成本卡（无配方 "—" 引导生成）+ 料肉比卡（无称重/未出栏 null+reason，EX-6b）
// - 异常提示条置顶部橙色，附 [归因分析] 跳 AI 问诊并 sessionStorage 预填上下文（06 规范 4.9）
// - 交互：激活新配方当日计划不改写（次日生效，口径 458）；<768 Tab 纵向堆叠
import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { Calendar, ChefHat, DollarSign, Scale, TrendingUp, Wheat } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { usePrimaryFarm, useUserPens } from '@/composables/use-farm-data';
import { apiGetFeedConversion, apiGetFeedPlan } from '@/api/feed-api';
import FeedingCalendarPanel from '@/components/feeding/FeedingCalendarPanel.vue';
import FeedingRecipePanel from '@/components/feeding/FeedingRecipePanel.vue';
import FeedingIntakePanel from '@/components/feeding/FeedingIntakePanel.vue';

const router = useRouter();
const activeTab = ref(0);
const selectedPenId = ref<number | null>(null);
const toast = ref<{ kind: 'ok' | 'err'; text: string } | null>(null);
let toastTimer: number | null = null;

const primaryFarm = usePrimaryFarm();
const farmId = computed(() => primaryFarm.value?.farm_id ?? null);
const { pens } = useUserPens(farmId);

// 默认选中第一个圈（Tab2/3/4 单圈视图共用）
watch(
  [pens, selectedPenId],
  ([list, id]) => {
    if (list.length > 0 && (id === null || !list.some((p) => p.pen_id === id))) {
      selectedPenId.value = list[0].pen_id;
    }
  },
  { immediate: true },
);

function showToast(kind: 'ok' | 'err', text: string) {
  toast.value = { kind, text };
  if (toastTimer) window.clearTimeout(toastTimer);
  toastTimer = window.setTimeout(() => (toast.value = null), 2600);
}

onBeforeUnmount(() => {
  if (toastTimer) window.clearTimeout(toastTimer);
});

/** 异常提示条 [归因分析]：sessionStorage 预填上下文并跳转 AI 问诊（06 规范 4.9，FR-09） */
function gotoAiConsult(prefill: string) {
  sessionStorage.setItem('sf_ai_prefill', prefill);
  router.push('/ai-chat');
}

const tabs = [
  { label: '饲喂日历', icon: Calendar },
  { label: '当前配方', icon: ChefHat },
  { label: '采食与增重', icon: TrendingUp },
  { label: '成本结算', icon: DollarSign },
];

const selectedPen = computed(() => pens.value.find((p) => p.pen_id === selectedPenId.value) ?? null);
const farmSubtitle = computed(() =>
  primaryFarm.value ? `${primaryFarm.value.farm_name} · 配方大脑与饲喂闭环` : '配方大脑与饲喂闭环',
);

function onSelectPen(e: Event) {
  selectedPenId.value = Number((e.target as HTMLSelectElement).value);
}

// ===== Tab4 成本结算（FR-10/11 ④）=====
// React 版 CostTab 仅在 Tab4 挂载；用 enabled 还原「切到 Tab4 才取数」的时机。
const costPen = computed(() => (activeTab.value === 3 ? selectedPen.value : null));

const { data: costPlanData } = useQuery({
  queryKey: computed(() => ['feed-plan', costPen.value?.pen_id ?? 0]),
  queryFn: () => apiGetFeedPlan(costPen.value!.pen_id),
  enabled: computed(() => !!costPen.value),
});
const { data: costConversionData, isLoading: costConversionLoading } = useQuery({
  queryKey: computed(() => ['feed-conversion', costPen.value?.pen_id ?? 0]),
  queryFn: () => apiGetFeedConversion(costPen.value!.pen_id),
  enabled: computed(() => !!costPen.value),
});

const costPlan = computed(() => costPlanData.value ?? null);
const costFormula = computed(() => costPlan.value?.active_formula ?? null);
const costConversion = computed(() => costConversionData.value ?? null);
const costStock = computed(() =>
  costPen.value ? (costPlan.value?.stock ?? costPen.value.male_count + costPen.value.female_count) : 0,
);
const fcrPass = computed(() => (costConversion.value?.fcr ?? Infinity) <= 3.0);
const fcrToneClass = computed(() => cn('font-medium', fcrPass.value ? 'text-success' : 'text-warning'));
</script>

<template>
  <div class="px-8 py-6 max-w-[1400px]">
    <div class="flex items-center justify-between mb-6 gap-4 flex-wrap">
      <div>
        <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">喂食中心</h1>
        <p class="text-[14px] text-text-secondary mt-0.5">{{ farmSubtitle }}</p>
      </div>
      <!-- 圈舍选择器（Tab2/3/4 单圈视图） -->
      <select
        v-if="activeTab > 0"
        :value="selectedPenId ?? ''"
        @change="onSelectPen"
        class="px-3 py-2 rounded-[10px] border border-border bg-card text-[13.5px] text-text-primary outline-none focus:border-primary"
        aria-label="选择圈舍"
      >
        <option v-for="p in pens" :key="p.pen_id" :value="p.pen_id">{{ p.pen_name }}</option>
      </select>
    </div>

    <!-- Tab 切换（<768 换行堆叠，FR-11 兼容） -->
    <div class="flex items-center gap-1 mb-6 bg-bg-subtle rounded-[10px] p-1 w-fit max-w-full flex-wrap">
      <button
        v-for="(tab, i) in tabs"
        :key="i"
        @click="activeTab = i"
        :class="
          cn(
            'flex items-center gap-1.5 px-4 py-2 rounded-[8px] text-[13px] transition-all duration-[var(--duration-fast)]',
            activeTab === i ? 'bg-card text-text-primary font-medium e1' : 'text-text-secondary hover:text-text-primary',
          )
        "
      >
        <component :is="tab.icon" :size="14" :stroke-width="1.5" />
        {{ tab.label }}
      </button>
    </div>

    <!-- Tab 内容 -->
    <div v-if="pens.length === 0 && activeTab !== 0" class="bg-card rounded-[12px] p-10 card-highlight text-center">
      <ChefHat :size="36" class="mx-auto mb-3" style="color: var(--text-disabled)" :stroke-width="1.5" />
      <p class="text-[14px] text-text-secondary">
        还没有圈舍——去
        <a href="/farm" class="text-primary font-medium hover:underline">场舍管理</a>
        创建圈舍后开始使用配方大脑
      </p>
    </div>
    <template v-else>
      <FeedingCalendarPanel
        v-if="activeTab === 0"
        :pens="pens"
        :farm-id="primaryFarm?.farm_id ?? null"
        @toast="showToast"
        @ai-consult="gotoAiConsult"
      />
      <FeedingRecipePanel v-else-if="activeTab === 1 && selectedPen" :pen="selectedPen" @toast="showToast" />
      <FeedingIntakePanel
        v-else-if="activeTab === 2 && selectedPen"
        :pen="selectedPen"
        @toast="showToast"
        @ai-consult="gotoAiConsult"
      />
      <div v-else-if="activeTab === 3 && selectedPen" class="grid grid-cols-1 md:grid-cols-2 gap-4">
        <!-- 日均饲料成本卡（无配方显示"—"并引导生成，FR-10） -->
        <div class="bg-card rounded-[12px] p-6 card-highlight">
          <div class="flex items-center gap-2 mb-4">
            <DollarSign :size="18" class="text-primary" :stroke-width="1.5" />
            <h3 class="text-[16px] font-semibold text-text-primary">日均饲料成本</h3>
          </div>
          <template v-if="costFormula">
            <div class="flex items-baseline gap-1 mb-2">
              <span class="text-[40px] font-semibold leading-[1.1] tnum text-text-primary">
                {{ costFormula.cost_per_head_day.toFixed(2) }}
              </span>
              <span class="text-[14px] text-text-secondary">元/头/天</span>
            </div>
            <p class="text-[13px] text-text-secondary">
              每头 <span class="tnum font-medium text-text-primary">{{ costFormula.cost_per_head_day.toFixed(2) }}</span> 元 ×
              <span class="tnum font-medium text-text-primary">{{ costStock }}</span> 头 ≈
              <span class="tnum font-medium text-text-primary">
                {{ (costFormula.cost_per_head_day * costStock).toFixed(0) }}
              </span>
              元/天
            </p>
            <p class="text-[11.5px] text-text-tertiary mt-1">
              按当前生效配方（{{ costFormula.label }}）与最新原料价核算，改价后自动重算
            </p>
          </template>
          <template v-else>
            <div class="flex items-baseline gap-1 mb-2">
              <span class="text-[40px] font-semibold leading-[1.1] tnum text-text-disabled">—</span>
              <span class="text-[14px] text-text-tertiary">元/头/天</span>
            </div>
            <p class="text-[13px] text-text-secondary mb-3">还没有生效配方，生成配方后自动按最新原料价核算</p>
            <button
              @click="activeTab = 1"
              class="px-4 py-2 rounded-[10px] bg-primary-50 text-primary-700 text-[13px] font-medium hover:bg-primary-100 transition-colors inline-flex items-center gap-1.5"
            >
              <Wheat :size="14" :stroke-width="1.5" />
              去生成配方
            </button>
          </template>
        </div>

        <!-- 料肉比卡（出栏后结算；无称重 null+reason 禁止 0，EX-6b/TC-F12） -->
        <div class="bg-card rounded-[12px] p-6 card-highlight">
          <div class="flex items-center gap-2 mb-4">
            <Scale :size="18" class="text-primary" :stroke-width="1.5" />
            <h3 class="text-[16px] font-semibold text-text-primary">出栏批次料肉比</h3>
          </div>
          <p v-if="costConversionLoading" class="text-[13.5px] text-text-tertiary py-6">加载中…</p>
          <template v-else-if="costConversion && costConversion.fcr !== null">
            <div class="flex items-baseline gap-1 mb-2">
              <span class="text-[40px] font-semibold leading-[1.1] tnum text-text-primary">{{ costConversion.fcr }}</span>
              <span class="text-[14px] text-text-secondary">: 1</span>
            </div>
            <p class="text-[13px] text-text-secondary mb-3">
              {{ costConversion.slaughter_date }} 出栏 {{ costConversion.head_count }} 头 ·
              <span :class="fcrToneClass">{{ fcrPass ? '达标' : '偏高' }}</span>
              （育肥期参考 ≤3.0）
            </p>
            <div class="mt-2 pt-3 border-t border-border space-y-1.5">
              <div class="flex justify-between text-[13px]">
                <span class="text-text-secondary">总耗料（出栏日前）</span>
                <span class="text-text-primary font-medium tnum">{{ costConversion.total_feed_kg?.toLocaleString() }} kg</span>
              </div>
              <div class="flex justify-between text-[13px]">
                <span class="text-text-secondary">总增重（出栏总重 − 期初）</span>
                <span class="text-text-primary font-medium tnum">{{ costConversion.total_gain_kg?.toLocaleString() }} kg</span>
              </div>
              <div class="flex justify-between text-[13px]">
                <span class="text-text-secondary">期初口径</span>
                <span class="text-text-tertiary tnum">
                  首称 {{ costConversion.first_avg_weight_kg }} kg × {{ costConversion.head_count }} 头（{{
                    costConversion.first_weigh_date
                  }}）
                </span>
              </div>
            </div>
          </template>
          <template v-else>
            <div class="flex items-baseline gap-1 mb-2">
              <span class="text-[40px] font-semibold leading-[1.1] tnum text-text-disabled">—</span>
              <span class="text-[14px] text-text-tertiary">: 1</span>
            </div>
            <p class="text-[13px] text-text-secondary">{{ costConversion?.reason ?? '暂无结算数据' }}</p>
            <p class="text-[11.5px] text-text-tertiary mt-1">
              料肉比 = 出栏日前总耗料 ÷ 总增重；定期称重并在出栏登记后自动结算
            </p>
          </template>
        </div>
      </div>
    </template>

    <!-- 轻提示（操作反馈） -->
    <div
      v-if="toast"
      class="fixed bottom-6 right-6 z-[60] px-4 py-3 rounded-[10px] text-[13.5px] e3 max-w-[360px]"
      :style="{ backgroundColor: toast.kind === 'ok' ? 'rgba(46,158,107,.95)' : 'rgba(217,83,79,.95)', color: '#fff' }"
      role="status"
    >
      {{ toast.text }}
    </div>
  </div>
</template>
