<script setup lang="ts">
// Tab2 当前配方（FR-11 ②，FR-02~05）
// - 菜谱卡（原料表 + 40px 成本大数字 + 营养达标表 EX-16 红字 + 依据来源 + "AI 生成，仅供参考"）
// - 换个配方弹窗（排除原料约束 + 替代方案横排卡片 + 成本色阶）
// - 原料价格弹窗（改价即时重算 TC-F05 + >30 天灰标 EX-17）
// - 分阶段饲喂方案参考（2026-10-14 配食分类完善）
import { computed, ref } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { ChefHat, Coins, Info, Scale, Wheat } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import type { UserPen } from '@/api/auth-api';
import type { NutritionStandard } from '@/lib/feed-data';
import { stageOptions } from '@/lib/species-data';
import {
  apiActivateFormula,
  apiFeedStandards,
  apiFormulaAlternatives,
  apiGetFeedPlan,
  apiListFeedPrices,
  apiUpdateFeedPrice,
  type AlternativeOption,
  type FeedPriceItem,
  type NutritionCheckItem,
} from '@/api/feed-api';
import FeedingDialog from './FeedingDialog.vue';

const props = defineProps<{ pen: UserPen }>();
const emit = defineEmits<{ toast: [kind: 'ok' | 'err', text: string] }>();

const queryClient = useQueryClient();

const STAGE_LABEL: Record<string, string> = {
  nursing: '哺乳期',
  weaning: '保育期',
  growing: '育成期',
  fattening: '育肥期',
  gestation: '妊娠期',
  lactation: '泌乳期',
};

function errText(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback;
}

const formulaOpen = ref(false);
const priceOpen = ref(false);

const { data: planData, isLoading: planLoading } = useQuery({
  queryKey: computed(() => ['feed-plan', props.pen.pen_id]),
  queryFn: () => apiGetFeedPlan(props.pen.pen_id),
});
const pricesQuery = useQuery({
  queryKey: ['feed-prices'],
  queryFn: apiListFeedPrices,
  staleTime: 30_000,
});
const pricesData = pricesQuery.data;

const plan = computed(() => planData.value ?? null);
const formula = computed(() => plan.value?.active_formula ?? null);
const stock = computed(() => plan.value?.stock ?? 0);
const priceMap = computed(
  () => new Map((pricesData.value?.items ?? []).map((p) => [p.ingredient_name, p] as const)),
);
const stageLabel = computed(() => STAGE_LABEL[props.pen.batch_stage ?? ''] ?? '通用阶段');

function priceText(name: string): string {
  const price = priceMap.value.get(name);
  return price ? price.unit_price.toFixed(2) : '—';
}

const nutritionEntries = computed<{ name: string; n: NutritionCheckItem }[]>(() =>
  Object.entries(formula.value?.nutrition_check ?? {}).map(([name, n]) => ({ name, n })),
);

function invalidateFormula() {
  queryClient.invalidateQueries({ queryKey: ['feed-plan', props.pen.pen_id] });
  queryClient.invalidateQueries({ queryKey: ['feeding-overview'] });
  queryClient.invalidateQueries({ queryKey: ['feed-conversion', props.pen.pen_id] });
}

// ===== 分阶段饲喂方案（数据源 = GET /feed/standards，与配方计算同一份标准）=====

function stageLabelOf(std: NutritionStandard, speciesCode: string): string {
  if (std.stage) {
    return stageOptions(speciesCode).find((o) => o.value === std.stage)?.label ?? STAGE_LABEL[std.stage] ?? std.stage;
  }
  if (std.sow_phase === 'gestation') return '妊娠母猪（按繁殖状态自动识别）';
  if (std.sow_phase === 'lactation') return '哺乳母猪（按繁殖状态自动识别）';
  return '母畜通用（未设阶段时兜底）';
}

const { data: standardsData } = useQuery({
  queryKey: computed(() => ['feed-standards', props.pen.variety_code ?? '']),
  queryFn: () => apiFeedStandards({ variety_code: props.pen.variety_code ?? undefined }),
  enabled: computed(() => !!props.pen.variety_code),
});
const standardsPool = computed<NutritionStandard[]>(() => standardsData.value?.items ?? []);
const sortedStandards = computed(() => {
  const opts = stageOptions(props.pen.species_code).map((o) => o.value);
  const orderOf = (s: NutritionStandard) => {
    const idx = s.stage ? opts.indexOf(s.stage) : opts.length;
    return idx === -1 ? opts.length + 1 : idx;
  };
  return [...standardsPool.value].sort((a, b) => orderOf(a) - orderOf(b));
});
function isCurrentStandard(s: NutritionStandard): boolean {
  return s.stage !== null && s.stage === props.pen.batch_stage;
}
function standardKey(s: NutritionStandard, i: number): string {
  return `${s.variety_code}|${s.stage ?? s.sow_phase ?? 'female'}|${i}`;
}
function formulaSummary(s: NutritionStandard): string {
  return s.formula.map((f) => `${f.name}${f.ratio_pct}%`).join(' · ');
}

// ===== 换配方弹窗（生成/替代方案，FR-02/03）=====

const exclude = ref<string[]>([]);
const options = ref<AlternativeOption[] | null>(null);
const selectedId = ref<number | null>(null);
const formulaLoading = ref(false);
const activating = ref(false);
const formulaErr = ref<string | null>(null);

const ingredientNames = computed(() => (pricesData.value?.items ?? []).map((p) => p.ingredient_name));
const selected = computed(() => options.value?.find((o) => o.formula_id === selectedId.value)?.formula ?? null);
const selectedNutritionEntries = computed<{ name: string; n: NutritionCheckItem }[]>(() =>
  Object.entries(selected.value?.nutrition_check ?? {}).map(([name, n]) => ({ name, n })),
);

function openFormula() {
  exclude.value = [];
  options.value = null;
  selectedId.value = null;
  formulaLoading.value = false;
  activating.value = false;
  formulaErr.value = null;
  formulaOpen.value = true;
  // 打开配方弹窗时强制重新取价（['feed-prices'] 默认 staleTime=0）
  void pricesQuery.refetch();
}

// 成本色阶（06 规范 4.6：低=绿、中=灰、高=橙，非红绿刺激）
function costTone(cost: number): 'low' | 'mid' | 'high' {
  const costs = (options.value ?? []).map((o) => o.cost_per_head_day);
  if (costs.length <= 1) return 'mid';
  const min = Math.min(...costs);
  const max = Math.max(...costs);
  if (cost === min && min !== max) return 'low';
  if (cost === max && min !== max) return 'high';
  return 'mid';
}
const TONE_COLOR: Record<string, string> = { low: 'var(--success)', mid: 'var(--text-tertiary)', high: 'var(--warning)' };

function toggleExclude(name: string) {
  const active = exclude.value.includes(name);
  exclude.value = active ? exclude.value.filter((n) => n !== name) : [...exclude.value, name];
}

async function fetchOptions() {
  formulaLoading.value = true;
  formulaErr.value = null;
  try {
    const res = await apiFormulaAlternatives({
      pen_id: props.pen.pen_id,
      constraints:
        exclude.value.length > 0 ? { exclude_ingredients: exclude.value, reason: '缺货或价格因素' } : undefined,
    });
    options.value = res.options;
    selectedId.value = null;
  } catch (e) {
    formulaErr.value = errText(e, '方案获取失败，请重试');
    options.value = null;
  } finally {
    formulaLoading.value = false;
  }
}

async function activate() {
  if (!selected.value) return;
  activating.value = true;
  try {
    await apiActivateFormula(selected.value.formula_id);
    const label = selected.value.label;
    formulaOpen.value = false;
    emit('toast', 'ok', `已设为生效配方（${label}），饲喂计划次日按新配方计算`);
    invalidateFormula();
  } catch (e) {
    emit('toast', 'err', errText(e, '激活失败，请重试'));
  } finally {
    activating.value = false;
  }
}

// ===== 原料价格弹窗（FR-05：GET 列表 + 行内改价；price_age_days >30 灰标 EX-17）=====

const editing = ref<string | null>(null);
const editValue = ref('');
const saving = ref(false);

function openPrice() {
  editing.value = null;
  editValue.value = '';
  saving.value = false;
  priceOpen.value = true;
  // 打开改价弹窗时强制重新取价
  void pricesQuery.refetch();
}

function startEdit(p: FeedPriceItem) {
  editing.value = p.ingredient_name;
  editValue.value = String(p.unit_price);
}

// 数字输入框保留字符串状态（v-model 会因 type=number 自动转成 number）
function onEditValueInput(e: Event) {
  editValue.value = (e.target as HTMLInputElement).value;
}

// 行内改价输入框挂载后自动聚焦
const vFocus = {
  mounted(el: HTMLInputElement) {
    el.focus();
  },
};

async function save(name: string) {
  const v = Number(editValue.value);
  if (!Number.isFinite(v) || v <= 0) {
    emit('toast', 'err', '单价需大于 0');
    return;
  }
  saving.value = true;
  try {
    const res = await apiUpdateFeedPrice({ ingredient_name: name, unit_price: v });
    editing.value = null;
    emit(
      'toast',
      'ok',
      res.affected.length > 0
        ? `${name} 价格已更新，${res.affected.length} 个生效配方成本已重算`
        : `${name} 价格已更新`,
    );
    queryClient.invalidateQueries({ queryKey: ['feed-prices'] });
    queryClient.invalidateQueries({ queryKey: ['feed-plan', props.pen.pen_id] });
    queryClient.invalidateQueries({ queryKey: ['feed-conversion', props.pen.pen_id] });
  } catch (e) {
    emit('toast', 'err', errText(e, '改价失败，请重试'));
  } finally {
    saving.value = false;
  }
}

const priceItems = computed(() => pricesData.value?.items ?? []);
</script>

<template>
  <div class="space-y-4">
    <div v-if="planLoading" class="bg-card rounded-[12px] p-10 card-highlight text-center">
      <p class="text-[13.5px] text-text-tertiary">加载中…</p>
    </div>
    <!-- 菜谱卡质感配方卡（06 规范 4.6） -->
    <div v-else-if="formula" class="bg-card rounded-[12px] p-6 card-highlight">
      <div class="flex items-start justify-between mb-6 gap-4 flex-wrap">
        <div>
          <h3 class="text-[16px] font-semibold text-text-primary">{{ pen.pen_name }} · {{ formula.label }}</h3>
          <p class="text-[13px] text-text-secondary mt-0.5">
            {{ STAGE_LABEL[pen.batch_stage ?? ''] ?? '通用阶段' }} · 存栏 {{ plan?.stock ?? 0 }} 头 · 每日
            {{ formula.feeds_per_day }} 顿
          </p>
          <p v-if="formula.activate_time" class="text-[11.5px] text-text-tertiary mt-0.5">
            生效于 {{ formula.activate_time.slice(0, 10) }} · 配方 #{{ formula.formula_id }}
          </p>
        </div>
        <div class="text-right">
          <div class="text-[12px] text-text-tertiary mb-1">每天每头成本</div>
          <div class="flex items-baseline gap-1">
            <span class="text-[40px] font-semibold leading-[1.1] tnum text-primary">
              {{ formula.cost_per_head_day.toFixed(2) }}
            </span>
            <span class="text-[14px] text-text-secondary">元/头</span>
          </div>
          <p v-if="plan?.stock" class="text-[11.5px] text-text-tertiary mt-0.5 tnum">
            全圈 {{ (formula.cost_per_head_day * stock).toFixed(0) }} 元/天
          </p>
        </div>
      </div>

      <!-- 原料表格（数字列 tabular 右对齐） -->
      <div class="border border-border rounded-[10px] overflow-hidden mb-6">
        <table class="w-full">
          <thead>
            <tr class="bg-bg-subtle">
              <th class="text-left px-4 py-2.5 text-[12px] font-medium text-text-secondary">原料名称</th>
              <th class="text-right px-4 py-2.5 text-[12px] font-medium text-text-secondary">占比</th>
              <th class="text-right px-4 py-2.5 text-[12px] font-medium text-text-secondary">每天每头 (kg)</th>
              <th class="text-right px-4 py-2.5 text-[12px] font-medium text-text-secondary">每顿喂量 (kg)</th>
              <th class="text-right px-4 py-2.5 text-[12px] font-medium text-text-secondary">单价 (元/kg)</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="ing in formula.ingredients"
              :key="ing.name"
              class="border-t border-border hover:bg-primary-50/40 transition-colors"
            >
              <td class="px-4 py-3 text-[14px] text-text-primary">{{ ing.name }}</td>
              <td class="px-4 py-3 text-[14px] text-text-primary text-right tnum">{{ ing.ratio_pct }}%</td>
              <td class="px-4 py-3 text-[14px] text-text-primary text-right tnum">
                {{ ing.daily_kg_per_head.toFixed(2) }}
              </td>
              <td class="px-4 py-3 text-[14px] text-text-primary text-right tnum">
                {{ (ing.daily_kg_per_head / Math.max(1, formula.feeds_per_day)).toFixed(2) }}
              </td>
              <td class="px-4 py-3 text-[14px] text-text-secondary text-right tnum">
                {{ priceText(ing.name) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- 营养达标表（不达标 danger 红字 + "不达标"文字标注，EX-16 无障碍口径） -->
      <div class="mb-4">
        <h4 class="text-[14px] font-medium text-text-primary mb-3 flex items-center gap-2">
          <Scale :size="16" class="text-primary" :stroke-width="1.5" />
          营养达标情况
        </h4>
        <div class="grid grid-cols-2 md:grid-cols-4 gap-3">
          <div v-for="entry in nutritionEntries" :key="entry.name" class="bg-bg-subtle rounded-[10px] p-3 text-center">
            <div class="text-[12px] text-text-tertiary mb-1">{{ entry.name }}</div>
            <div class="text-[16px] font-semibold tnum" :style="{ color: entry.n.pass ? 'var(--success)' : 'var(--danger)' }">
              {{ entry.n.actual }}
            </div>
            <div class="text-[11px] text-text-tertiary mt-0.5">要求 {{ entry.n.required }}</div>
            <div v-if="!entry.n.pass" class="text-[11px] font-medium mt-0.5" style="color: var(--danger)">不达标</div>
          </div>
        </div>
      </div>

      <!-- 底部：依据来源 + 免责 + 操作 -->
      <div class="flex items-center justify-between pt-4 border-t border-border gap-4 flex-wrap">
        <div>
          <p class="text-[12px] text-text-tertiary flex items-center gap-1">
            <Info :size="12" :stroke-width="1.5" />
            {{ formula.basis }}
          </p>
          <p class="text-[11.5px] mt-1" style="color: var(--text-tertiary)">
            {{ formula.disclaimer }} · 营养标准为参考估算值，不替代兽医与营养师建议
          </p>
        </div>
        <div class="flex items-center gap-2">
          <button
            @click="openPrice"
            class="flex items-center gap-2 px-4 py-2 rounded-[10px] border border-border bg-card text-text-secondary text-[13px] font-medium hover:border-primary hover:text-primary transition-colors"
          >
            <Coins :size="14" :stroke-width="1.5" />
            原料价格
          </button>
          <button
            @click="openFormula"
            class="flex items-center gap-2 px-4 py-2 rounded-[10px] bg-primary-50 text-primary-700 text-[13px] font-medium hover:bg-primary-100 transition-colors"
          >
            <Wheat :size="14" :stroke-width="1.5" />
            换个配方
          </button>
        </div>
      </div>
    </div>

    <!-- 无配方空态（FR-11 四类空态之一） -->
    <div v-else class="bg-card rounded-[12px] p-10 card-highlight text-center">
      <ChefHat :size="36" class="mx-auto mb-3" style="color: var(--text-disabled)" :stroke-width="1.5" />
      <p class="text-[15px] text-text-secondary mb-1">还没有生效配方</p>
      <p class="text-[13px] text-text-tertiary mb-5">
        生成可执行配方（原料比例 + 顿数 + 每顿喂量 + 成本 + 依据来源）后，饲喂计划将按配方自动计算
      </p>
      <button
        @click="openFormula"
        class="px-5 py-2.5 rounded-[10px] bg-primary text-white text-[14px] font-medium hover:bg-primary-600 transition-colors inline-flex items-center gap-2"
      >
        <Wheat :size="15" :stroke-width="1.5" />
        生成配方
      </button>
      <div class="mt-5 pt-4 border-t border-border">
        <button
          @click="openPrice"
          class="text-[13px] text-primary font-medium hover:underline inline-flex items-center gap-1"
        >
          <Coins :size="13" :stroke-width="1.5" />
          先核对原料价格
        </button>
      </div>
    </div>

    <!-- 分阶段饲喂方案参考（2026-10-14 配食分类完善：当前品种全部阶段档位一览，当前阶段高亮） -->
    <div v-if="standardsPool.length > 0" class="bg-card rounded-[12px] p-6 card-highlight">
      <div class="flex items-start justify-between gap-4 flex-wrap mb-1">
        <h3 class="text-[16px] font-semibold text-text-primary flex items-center gap-2">
          <Wheat :size="17" class="text-primary" :stroke-width="1.5" />
          分阶段饲喂方案
        </h3>
        <p class="text-[12px] text-text-tertiary flex items-center gap-1">
          <Info :size="12" :stroke-width="1.5" />
          想换阶段？去「场舍管理」修改圈舍生长阶段，配方与计划自动按新阶段生成
        </p>
      </div>
      <p class="text-[12.5px] text-text-secondary mb-5">
        同一品种在不同生长阶段 / 生理状态下的饲喂标准不同（下面高亮的是当前圈舍所处阶段）：
      </p>
      <div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
        <div
          v-for="(s, i) in sortedStandards"
          :key="standardKey(s, i)"
          :class="
            cn(
              'rounded-[10px] border p-4 transition-colors',
              isCurrentStandard(s) ? 'border-primary bg-primary-50/50' : 'border-border bg-bg-subtle',
            )
          "
        >
          <div class="flex items-center justify-between gap-2 mb-2">
            <span :class="cn('text-[13.5px] font-medium', isCurrentStandard(s) ? 'text-primary-700' : 'text-text-primary')">
              {{ stageLabelOf(s, pen.species_code) }}
            </span>
            <span
              v-if="isCurrentStandard(s)"
              class="text-[10.5px] font-medium px-2 py-0.5 rounded-full bg-primary text-white shrink-0"
            >
              当前方案
            </span>
          </div>
          <p class="text-[12px] text-text-secondary tnum mb-2">
            {{ s.min_weight }}~{{ s.max_weight }}kg · 日采食 {{ s.nutrition.daily_feed_kg_per_head }}kg/头 · 每日
            {{ s.nutrition.feeds_per_day }} 顿
          </p>
          <div class="flex flex-wrap gap-1 mb-2.5">
            <span
              v-for="ind in s.nutrition.indicators"
              :key="ind.name"
              class="text-[11px] px-2 py-0.5 rounded-full bg-card border border-border text-text-secondary"
            >
              {{ ind.name }} {{ ind.required }}
            </span>
          </div>
          <p class="text-[11.5px] text-text-secondary leading-relaxed">
            {{ formulaSummary(s) }}
          </p>
        </div>
      </div>
      <p class="text-[11px] text-text-tertiary mt-4">
        营养标准为公开饲养标准整理的参考估算值，不替代兽医与营养师建议
      </p>
    </div>

    <!-- 换配方弹窗（生成/替代方案，FR-02/03） -->
    <FeedingDialog
      v-if="formulaOpen"
      title="换个配方"
      :subtitle="`${pen.pen_name} · ${stageLabel} · 方案由配方大脑按营养标准与最新原料价生成`"
      :width="640"
      :error="formulaErr"
      :loading="activating"
      :submit-disabled="!selected"
      submit-text="设为生效配方"
      @close="formulaOpen = false"
      @cancel="formulaOpen = false"
      @submit="activate"
    >
      <!-- 排除原料约束（FR-02 constraints） -->
      <div class="mb-5">
        <p class="text-[13px] text-text-secondary mb-2">排除原料（缺货/涨价时勾选，选填）</p>
        <div class="flex flex-wrap gap-2">
          <button
            v-for="name in ingredientNames"
            :key="name"
            @click="toggleExclude(name)"
            :class="
              cn(
                'px-3 py-1.5 rounded-[8px] text-[12.5px] transition-colors',
                exclude.includes(name) ? 'text-white font-medium' : 'bg-bg-subtle text-text-secondary hover:text-text-primary',
              )
            "
            :style="exclude.includes(name) ? { backgroundColor: 'var(--danger)' } : undefined"
          >
            {{ name }}
          </button>
          <span v-if="ingredientNames.length === 0" class="text-[12.5px] text-text-tertiary">价格库加载中…</span>
        </div>
        <button
          @click="fetchOptions"
          :disabled="formulaLoading || ingredientNames.length === 0"
          class="mt-4 w-full px-4 py-2.5 rounded-[10px] bg-primary text-white text-[14px] font-medium hover:bg-primary-600 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {{ formulaLoading ? '生成中…' : options ? '按约束重新生成方案' : '生成配方方案' }}
        </button>
      </div>

      <!-- 方案横排卡片（TC-F02：2~3 套 + 成本对比 + 达标说明） -->
      <div v-if="options && options.length > 0" class="mb-4">
        <p class="text-[13px] text-text-secondary mb-2">候选方案（点击选中查看详情）</p>
        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
          <button
            v-for="opt in options"
            :key="opt.formula_id"
            @click="selectedId = opt.formula_id"
            :class="
              cn(
                'text-left p-4 rounded-[12px] border transition-all',
                selectedId === opt.formula_id ? 'border-primary' : 'border-border hover:border-border-strong',
              )
            "
            :style="selectedId === opt.formula_id ? { backgroundColor: 'rgba(38,137,92,.05)' } : undefined"
          >
            <p class="text-[13px] font-medium text-text-primary mb-1.5">{{ opt.label }}</p>
            <div class="flex items-baseline gap-1 mb-2">
              <span class="text-[20px] font-semibold tnum" :style="{ color: TONE_COLOR[costTone(opt.cost_per_head_day)] }">
                {{ opt.cost_per_head_day.toFixed(2) }}
              </span>
              <span class="text-[11px] text-text-tertiary">元/头/天</span>
            </div>
            <span v-if="opt.nutrition_pass" class="text-[11.5px] font-medium" style="color: var(--success)">
              ✓ 营养达标
            </span>
            <span v-else class="text-[11.5px] font-medium" style="color: var(--danger)">
              缺失：{{ opt.miss?.join('、') }}
            </span>
          </button>
        </div>
      </div>

      <!-- 选中方案详情（原料表 + 营养表 + 依据来源，TC-F01 五要素核对） -->
      <div v-if="selected" class="border border-border rounded-[12px] p-4 mb-4">
        <div class="flex items-center justify-between mb-3">
          <h4 class="text-[14px] font-semibold text-text-primary">{{ selected.label }} · 详情</h4>
          <span class="text-[12px] text-text-tertiary">每日 {{ selected.feeds_per_day }} 顿</span>
        </div>
        <table class="w-full mb-3">
          <thead>
            <tr>
              <th class="text-left text-[11.5px] font-medium text-text-tertiary py-1.5">原料</th>
              <th class="text-right text-[11.5px] font-medium text-text-tertiary py-1.5">占比</th>
              <th class="text-right text-[11.5px] font-medium text-text-tertiary py-1.5">每顿喂量 (kg)</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ing in selected.ingredients" :key="ing.name" class="border-t border-border">
              <td class="py-2 text-[13px] text-text-primary">{{ ing.name }}</td>
              <td class="py-2 text-[13px] text-text-primary text-right tnum">{{ ing.ratio_pct }}%</td>
              <td class="py-2 text-[13px] text-text-primary text-right tnum">
                {{ (ing.daily_kg_per_head / Math.max(1, selected.feeds_per_day)).toFixed(2) }}
              </td>
            </tr>
          </tbody>
        </table>
        <div class="flex flex-wrap gap-x-4 gap-y-1 mb-2">
          <span
            v-for="entry in selectedNutritionEntries"
            :key="entry.name"
            class="text-[11.5px] tnum"
            :style="{ color: entry.n.pass ? 'var(--text-tertiary)' : 'var(--danger)' }"
          >
            {{ entry.name }} {{ entry.n.actual }}（要求 {{ entry.n.required }}）{{ !entry.n.pass ? ' · 不达标' : '' }}
          </span>
        </div>
        <p class="text-[11px] text-text-tertiary">{{ selected.basis }}</p>
        <p class="text-[11px] mt-0.5" style="color: var(--text-tertiary)">
          {{ selected.disclaimer }} · 参考估算值，不替代兽医与营养师建议
        </p>
      </div>
    </FeedingDialog>

    <!-- 原料价格弹窗（FR-05：改价重算 + 灰标） -->
    <FeedingDialog
      v-if="priceOpen"
      title="原料价格"
      subtitle="改价只作用于你自己的配方成本（内置/行情参考价为全平台共享）；内置参考价按 2026-10 全国批发均价校准"
      :width="560"
      @close="priceOpen = false"
    >
      <div class="border border-border rounded-[10px] overflow-hidden mb-4">
        <table class="w-full">
          <thead>
            <tr class="bg-bg-subtle">
              <th class="text-left px-4 py-2.5 text-[12px] font-medium text-text-secondary">原料</th>
              <th class="text-right px-4 py-2.5 text-[12px] font-medium text-text-secondary">单价 (元/kg)</th>
              <th class="text-right px-4 py-2.5 text-[12px] font-medium text-text-secondary">更新时间</th>
              <th class="w-[76px] px-4 py-2.5" />
            </tr>
          </thead>
          <tbody>
            <tr v-for="p in priceItems" :key="p.ingredient_name" class="border-t border-border">
              <td class="px-4 py-2.5 text-[13.5px] text-text-primary">
                {{ p.ingredient_name }}
                <span
                  v-if="p.scope === 'own'"
                  class="ml-1.5 text-[10.5px] px-1 py-0.5 rounded-[4px] bg-bg-subtle text-text-tertiary"
                  :title="
                    p.global_unit_price != null
                      ? `全局参考价 ${p.global_unit_price.toFixed(2)}；这条改价只影响你自己的配方成本`
                      : '这条改价只影响你自己的配方成本'
                  "
                >
                  我的改价
                </span>
                <span
                  v-else-if="!p.is_builtin"
                  class="ml-1.5 text-[10.5px] px-1 py-0.5 rounded-[4px] bg-bg-subtle text-text-tertiary"
                >
                  自建
                </span>
              </td>
              <td class="px-4 py-2.5 text-right">
                <input
                  v-if="editing === p.ingredient_name"
                  type="number"
                  min="0"
                  step="0.01"
                  :value="editValue"
                  @input="onEditValueInput"
                  v-focus
                  @keydown.enter="save(p.ingredient_name)"
                  class="w-[90px] px-2 py-1 rounded-[6px] border border-primary bg-bg-page text-[13px] tnum text-right outline-none"
                />
                <span v-else class="text-[13.5px] tnum text-text-primary">{{ p.unit_price.toFixed(2) }}</span>
              </td>
              <td class="px-4 py-2.5 text-right">
                <span :class="cn('text-[12px] tnum', p.price_age_days > 30 ? 'text-text-disabled' : 'text-text-tertiary')">
                  {{ p.price_date }}
                  <span v-if="p.price_age_days > 30" class="block text-[10.5px]" title="建议改价">
                    价格更新于 {{ p.price_age_days }} 天前，建议改价
                  </span>
                </span>
              </td>
              <td class="px-4 py-2.5 text-right">
                <button
                  v-if="editing === p.ingredient_name"
                  @click="save(p.ingredient_name)"
                  :disabled="saving"
                  class="text-[12px] text-primary font-medium hover:underline disabled:opacity-50"
                >
                  {{ saving ? '保存中…' : '保存' }}
                </button>
                <button
                  v-else
                  @click="startEdit(p)"
                  class="text-[12px] text-primary font-medium hover:underline"
                >
                  改价
                </button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="text-[11.5px] text-text-tertiary leading-[1.7] -mt-2 mb-4">
        内置参考价数据来源：农业农村部 500 县集贸监测、行情宝、生意社、Mysteel 全国粉企汇总、卓创资讯鱼粉市场日报
        （2026-10-13/14 全国批发均价交叉校准）。各地实际采购价有差异，请点击「改价」录入本场进价——成本核算以最新价为准。
      </p>
      <template #footer>
        <div class="flex justify-end">
          <button
            @click="priceOpen = false"
            class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          >
            关闭
          </button>
        </div>
      </template>
    </FeedingDialog>
  </div>
</template>
