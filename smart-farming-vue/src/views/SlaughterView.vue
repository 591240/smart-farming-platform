<script setup lang="ts">
// 出栏登记页 — F07 FR-09（06 规范 4.5 步骤式，mock 切真）
// - 步骤式表单：选圈舍 → 数量/体重/去向 → 校验结果区 → 确认
// - 校验结果区：通过=绿卡（success @ 8% 底 + 对勾）；拦截=红卡「文件感」（白底 danger 1.5px 描边），
//   表格列出药品名/末次用药日期/休药期剩余天数三要素（EX-11），「强制继续」→ 承诺备注弹窗 → 红色确认
// - 合规原文固定卡片底部："依据《兽药管理条例》，休药期未满的畜禽严禁出栏、屠宰、销售"
// - 用药面板：登记用药（兽药库下拉，违禁药已过滤）+ 近期用药列表（休药期剩余天数，未满红标）
// - 入口：场舍管理页 [出栏] 快捷按钮跳转并预选圈舍（F02 占位替换）

import { computed, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { Truck, Check, X, AlertTriangle, Syringe, ChevronRight, ChevronLeft, FileText } from 'lucide-vue-next';
import { cn, todayLocal } from '@/lib/utils';
import { useUserFarms, useUserPens } from '@/composables/use-farm-data';
import { ApiError, type ApiError as ApiErrorType } from '@/lib/api-contract';
import {
  apiSlaughter,
  apiCreateMedicineUsage,
  apiListMedicineUsages,
  apiListMedicineLib,
  type SlaughterBlockingMed,
} from '@/api/breeding-api';
import { apiListIndividuals, type IndividualItem } from '@/api/individual-api';

const COMPLIANCE_TEXT = '依据《兽药管理条例》，休药期未满的畜禽严禁出栏、屠宰、销售';

const STEPS = [
  { n: 1, label: '选择圈舍' },
  { n: 2, label: '数量与去向' },
  { n: 3, label: '校验与确认' },
] as const;

// ===== 主页面 =====
const route = useRoute();
const router = useRouter();
const queryClient = useQueryClient();
const { farms, loading: farmsLoading } = useUserFarms();
const selectedFarmId = ref<number | null>(null);
const currentFarm = computed(
  () => farms.value.find((f) => f.farm_id === selectedFarmId.value) ?? farms.value[0] ?? null,
);
const { pens, loading: pensLoading } = useUserPens(computed(() => currentFarm.value?.farm_id ?? null));

// 场舍管理页 [出栏] 跳转携带 pen_id 预选圈舍（F02 占位替换）
// 场舍管理页跳转预选（?pen_id=）
const rawPenId = route.query.pen_id;
const initialPenId = rawPenId !== undefined && rawPenId !== '' ? Number(rawPenId) : undefined;
const penId = ref<number | null>(initialPenId ?? null);
watch(
  [farms, selectedFarmId],
  () => {
    if (
      farms.value.length > 0 &&
      (!selectedFarmId.value || !farms.value.some((f) => f.farm_id === selectedFarmId.value))
    ) {
      selectedFarmId.value = farms.value[0].farm_id;
    }
  },
  { immediate: true },
);

const sellablePens = computed(() => pens.value.filter((p) => p.male_count + p.female_count > 0));
const currentPen = computed(() => sellablePens.value.find((p) => p.pen_id === penId.value) ?? null);

const step = ref<1 | 2 | 3>(1);
const maleCount = ref(0);
const femaleCount = ref(0);
const avgWeight = ref('');
const destination = ref('');
const err = ref<string | null>(null);
const loading = ref(false);

// 校验结果：成功绿卡 / 拦截红卡（40001 携带三要素）
const result = ref<{ change_id: number; withdrawal_check: 'passed' | 'forced' } | null>(null);
const blocking = ref<SlaughterBlockingMed[] | null>(null);
const forceOpen = ref(false);
const forceNote = ref('');
const forceErr = ref<string | null>(null);
const forceLoading = ref(false);
const medOpen = ref(false);
const toast = ref<string | null>(null);

// F14 强关联耳标：本圈在栏个体（部分出栏必须勾选；整栏出栏服务端自动清档）
const slaughterInds = ref<IndividualItem[]>([]);
const selTags = ref<Set<string>>(new Set());
watch(
  () => currentPen.value?.pen_id,
  (_id, _old, onCleanup) => {
    let cancelled = false;
    onCleanup(() => {
      cancelled = true;
    });
    selTags.value = new Set();
    if (!currentPen.value) {
      slaughterInds.value = [];
      return;
    }
    // 逐页取完在栏个体：单页上限 100，超过 100 头的圈舍此前会漏掉耳标，
    // 部分出栏时选中不了、也无法核对整栏清单。
    const penIdNow = currentPen.value.pen_id;
    void (async () => {
      const all: IndividualItem[] = [];
      try {
        for (let page = 1; ; page += 1) {
          const r = await apiListIndividuals({ pen_id: penIdNow, status: 'active', page, page_size: 100 });
          all.push(...r.items);
          if (page * r.page_size >= r.total) break;
        }
        if (!cancelled) slaughterInds.value = all;
      } catch {
        if (!cancelled) slaughterInds.value = [];
      }
    })();
  },
  { immediate: true },
);

const usageQuery = useQuery({
  queryKey: computed(() => ['pen-medicine-usages', penId.value ?? 0]),
  queryFn: async () => (await apiListMedicineUsages(penId.value as number)).items,
  enabled: computed(() => !!penId.value),
  staleTime: 15_000,
});
const usages = computed(() => usageQuery.data.value ?? []);

let toastTimer: number | null = null;
function showToast(msg: string) {
  toast.value = msg;
  toastTimer = window.setTimeout(() => (toast.value = null), 2600);
}
onBeforeUnmount(() => {
  if (toastTimer !== null) window.clearTimeout(toastTimer);
});

function selectPen(id: number) {
  penId.value = id;
  maleCount.value = 0;
  femaleCount.value = 0;
  avgWeight.value = '';
  destination.value = '';
  err.value = null;
  result.value = null;
  blocking.value = null;
  step.value = 2;
}

function resetFlow() {
  result.value = null;
  blocking.value = null;
  err.value = null;
  step.value = 1;
  maleCount.value = 0;
  femaleCount.value = 0;
  avgWeight.value = '';
  destination.value = '';
  selTags.value = new Set();
}

async function submit(force = false, note = '') {
  const pen = currentPen.value;
  if (!pen) return;
  err.value = null;
  // F14 强关联：非整栏出栏必须勾选耳标；整栏出栏自动携带全部在栏耳标（服务端兜底校验）
  const isFull = maleCount.value === pen.male_count && femaleCount.value === pen.female_count;
  let tagPayload: string[] = [];
  if (isFull) {
    if (slaughterInds.value.length === maleCount.value + femaleCount.value) {
      tagPayload = slaughterInds.value.map((i) => i.ear_tag_no);
    }
  } else {
    const selMale = slaughterInds.value.filter(
      (i) => selTags.value.has(i.ear_tag_no) && i.gender === 'male',
    ).length;
    if (selTags.value.size !== maleCount.value + femaleCount.value) {
      err.value = `部分出栏需勾选出栏耳标（已选 ${selTags.value.size}/${maleCount.value + femaleCount.value}）`;
      step.value = 2;
      return;
    }
    if (selMale !== maleCount.value) {
      err.value = `出栏耳标公母分布与数量不符（需公 ${maleCount.value} / 母 ${femaleCount.value}）`;
      step.value = 2;
      return;
    }
    tagPayload = [...selTags.value];
  }
  loading.value = true;
  try {
    const res = await apiSlaughter(pen.pen_id, {
      male_count: maleCount.value,
      female_count: femaleCount.value,
      ...(avgWeight.value ? { avg_weight_kg: Number(avgWeight.value) } : {}),
      ...(destination.value.trim() ? { destination: destination.value.trim() } : {}),
      ...(force ? { force: true, force_note: note } : {}),
      ...(tagPayload.length > 0 ? { tag_nos: tagPayload } : {}),
    });
    result.value = { change_id: res.change_id, withdrawal_check: res.withdrawal_check };
    blocking.value = null;
    step.value = 3;
    queryClient.invalidateQueries({ queryKey: ['user-pens'] });
    queryClient.invalidateQueries({ queryKey: ['user-farms'] });
    queryClient.invalidateQueries({ queryKey: ['pen-medicine-usages'] });
    showToast(force ? '已按强制出栏留痕登记' : '出栏登记成功，存栏已扣减');
  } catch (e) {
    const apiErr = e as ApiErrorType;
    if (apiErr instanceof ApiError && apiErr.code === 40001) {
      // EX-11：休药期拦截——红卡三要素（data.med_name/usage_date/withdrawal_until/left_days）
      const data = apiErr.data as { blocking?: SlaughterBlockingMed[] } | undefined;
      blocking.value = data?.blocking ?? [];
      result.value = null;
    } else {
      err.value = apiErr instanceof ApiError ? apiErr.message : '网络异常，请重试';
    }
  } finally {
    loading.value = false;
  }
}

async function onSubmitClick() {
  if (maleCount.value + femaleCount.value === 0) {
    err.value = '请填写出栏数量';
    return;
  }
  err.value = null;
  await submit(false);
}

async function submitForce() {
  if (!forceNote.value.trim()) {
    forceErr.value = '请填写承诺备注';
    return;
  }
  forceLoading.value = true;
  forceErr.value = null;
  try {
    await submit(true, forceNote.value.trim());
    forceOpen.value = false;
    forceNote.value = '';
  } finally {
    forceLoading.value = false;
  }
}

function setStepValue(n: 1 | 2 | 3) {
  step.value = n;
}

// ===== F14 耳标勾选 =====
const isFullExit = computed(
  () =>
    currentPen.value !== null &&
    maleCount.value === currentPen.value.male_count &&
    femaleCount.value === currentPen.value.female_count,
);
const maleInds = computed(() => slaughterInds.value.filter((i) => i.gender === 'male'));
const femaleInds = computed(() => slaughterInds.value.filter((i) => i.gender === 'female'));
const insufficient = computed(
  () => maleInds.value.length < maleCount.value || femaleInds.value.length < femaleCount.value,
);
const tagGroups = computed(() => [
  { label: '出栏公畜', tags: maleInds.value, cap: maleCount.value },
  { label: '出栏母畜', tags: femaleInds.value, cap: femaleCount.value },
]);

function groupSelected(tags: IndividualItem[]): number {
  return tags.filter((i) => selTags.value.has(i.ear_tag_no)).length;
}

function tagChecked(tag: string): boolean {
  return selTags.value.has(tag);
}

function tagDisabled(tags: IndividualItem[], tag: IndividualItem, cap: number): boolean {
  return !selTags.value.has(tag.ear_tag_no) && groupSelected(tags) >= cap;
}

function toggleTag(tag: string) {
  const next = new Set(selTags.value);
  if (next.has(tag)) next.delete(tag);
  else next.add(tag);
  selTags.value = next;
}

// ===== 通用组件 =====
function clampCount(v: number, max?: number): number {
  return max !== undefined ? Math.min(max, Math.max(0, v)) : Math.max(0, v);
}

function stepCount(which: 'male' | 'female', delta: number) {
  const pen = currentPen.value;
  if (!pen) return;
  if (which === 'male') maleCount.value = clampCount(maleCount.value + delta, pen.male_count);
  else femaleCount.value = clampCount(femaleCount.value + delta, pen.female_count);
}

function onCountInput(which: 'male' | 'female', e: Event) {
  const pen = currentPen.value;
  const max = which === 'male' ? pen?.male_count : pen?.female_count;
  const n = parseInt((e.target as HTMLInputElement).value, 10);
  const v = clampCount(Number.isFinite(n) ? n : 0, max);
  if (which === 'male') maleCount.value = v;
  else femaleCount.value = v;
}

// ===== 强制出栏承诺备注弹窗 =====
function openForce() {
  forceNote.value = '';
  forceErr.value = null;
  forceOpen.value = true;
}

// ===== 用药登记弹窗（FR-05）=====
const medQuery = useQuery({
  queryKey: computed(() => ['medicine-lib']),
  queryFn: async () => (await apiListMedicineLib()).items,
  staleTime: 60_000,
  enabled: computed(() => medOpen.value),
});
const meds = computed(() => medQuery.data.value ?? []);
const medId = ref(0);
const usageDate = ref(todayLocal());
const dosage = ref('');
const note = ref('');
const medErr = ref<string | null>(null);
const medLoading = ref(false);

watch([meds, medId], () => {
  if (meds.value.length > 0 && medId.value === 0) medId.value = meds.value[0].med_id;
});

const selectedMed = computed(() => meds.value.find((m) => m.med_id === medId.value));
const medUntilPreview = computed(() => {
  const med = selectedMed.value;
  return med ? addDaysStr(usageDate.value, med.withdrawal_period) : '—';
});

function openMed() {
  medId.value = 0;
  usageDate.value = todayLocal();
  dosage.value = '';
  note.value = '';
  medErr.value = null;
  medLoading.value = false;
  if (meds.value.length > 0) medId.value = meds.value[0].med_id;
  medOpen.value = true;
}

async function submitMed() {
  medErr.value = null;
  if (!medId.value) {
    medErr.value = '请选择药品';
    return;
  }
  const pen = currentPen.value;
  if (!pen) return;
  medLoading.value = true;
  try {
    const res = await apiCreateMedicineUsage({
      pen_id: pen.pen_id,
      med_id: medId.value,
      usage_date: usageDate.value,
      ...(dosage.value.trim() ? { dosage: dosage.value.trim() } : {}),
      ...(note.value.trim() ? { operator_note: note.value.trim() } : {}),
    });
    medOpen.value = false;
    queryClient.invalidateQueries({ queryKey: ['pen-medicine-usages'] });
    showToast(`已登记用药，休药期至 ${res.withdrawal_until}`);
  } catch (e) {
    medErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    medLoading.value = false;
  }
}

function addDaysStr(dateStr: string, days: number): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!m) return '—';
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  d.setDate(d.getDate() + days);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
</script>

<template>
  <div v-if="!farmsLoading && farms.length === 0" class="px-8 py-6 max-w-[1400px]">
    <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4] mb-6">出栏登记</h1>
    <div class="bg-card rounded-[12px] p-14 card-highlight flex flex-col items-center text-center">
      <div class="w-16 h-16 rounded-full bg-primary-50 flex items-center justify-center mb-5">
        <Truck :size="28" :stroke-width="1.5" class="text-primary" />
      </div>
      <h3 class="text-[16px] font-semibold text-text-primary mb-2">还没有养殖场</h3>
      <p class="text-[13.5px] text-text-secondary mb-6 max-w-[360px] leading-[1.7]">
        创建养殖场与圈舍后，即可进行出栏登记（含休药期合规校验）。
      </p>
      <button
        class="flex items-center gap-2 px-5 py-2.5 bg-primary text-white rounded-[10px] text-[14px] font-medium hover:bg-primary-600 transition-colors"
        @click="router.push('/farm')"
      >
        去创建养殖场
      </button>
    </div>
  </div>

  <div v-else class="px-8 py-6 max-w-[1400px]">
    <div class="mb-6">
      <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">出栏登记</h1>
      <p class="text-[14px] text-text-secondary mt-0.5">
        {{ currentFarm ? `${currentFarm.farm_name} · 出栏含休药期硬校验，登记后三表联动落台账` : '加载中…' }}
      </p>
    </div>

    <div class="flex flex-col lg:flex-row gap-5 items-start">
      <!-- ===== 左侧：步骤式表单 ===== -->
      <div class="w-full lg:flex-1 min-w-0">
        <!-- 步骤条：允许点击回退到已完成步骤；不允许向前跳（跳过休药期校验） -->
        <div class="flex items-center gap-2 mb-5">
          <template v-for="(s, i) in STEPS" :key="s.n">
            <ChevronRight v-if="i > 0" :size="16" class="text-text-tertiary shrink-0" />
            <button
              type="button"
              :disabled="step <= s.n"
              :title="step > s.n ? `返回「${s.label}」重新填写` : undefined"
              :class="
                cn(
                  'flex items-center gap-2 px-1 rounded-[8px] transition-colors duration-[var(--duration-fast)]',
                  step > s.n ? 'cursor-pointer hover:bg-primary-50' : 'cursor-default',
                )
              "
              @click="setStepValue(s.n)"
            >
              <span
                :class="
                  cn(
                    'w-6 h-6 rounded-full flex items-center justify-center text-[12px] font-medium tnum',
                    step >= s.n ? 'bg-primary text-white' : 'bg-bg-inset text-text-tertiary',
                  )
                "
              >
                <Check v-if="step > s.n" :size="13" :stroke-width="2" />
                <template v-else>{{ s.n }}</template>
              </span>
              <span :class="cn('text-[13px]', step >= s.n ? 'text-text-primary font-medium' : 'text-text-tertiary')">
                {{ s.label }}
              </span>
            </button>
          </template>
        </div>

        <!-- 步骤 1：选圈舍 -->
        <div v-if="step === 1" class="bg-card rounded-[12px] p-6 card-highlight">
          <h2 class="text-[16px] font-semibold text-text-primary mb-4">选择要出栏的圈舍</h2>
          <p v-if="pensLoading" class="text-[13px] text-text-tertiary py-6 text-center">加载中…</p>
          <div v-else-if="sellablePens.length === 0" class="text-center py-8">
            <p class="text-[14px] text-text-secondary mb-4">当前没有存栏的圈舍</p>
            <button
              class="px-4 py-2 rounded-[10px] bg-primary text-white text-[14px] font-medium hover:bg-primary-600 transition-colors"
              @click="router.push('/farm')"
            >
              去场舍管理调整存栏
            </button>
          </div>
          <div v-else class="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <button
              v-for="p in sellablePens"
              :key="p.pen_id"
              class="text-left p-4 rounded-[10px] border border-border hover:border-primary hover:bg-primary-50/40 transition-all duration-[var(--duration-fast)]"
              @click="selectPen(p.pen_id)"
            >
              <div class="flex items-center justify-between mb-1.5">
                <span class="text-[14px] font-medium text-text-primary">{{ p.pen_name }}</span>
                <span class="text-[12px] text-text-tertiary">{{ p.variety_name }}</span>
              </div>
              <div class="text-[13px] text-text-secondary tnum">
                公 {{ p.male_count }} · 母 {{ p.female_count }} · 合计 {{ p.male_count + p.female_count }} 头
              </div>
            </button>
          </div>
        </div>

        <!-- 步骤 2：数量与去向 -->
        <div v-if="step === 2 && currentPen" class="bg-card rounded-[12px] p-6 card-highlight">
          <div class="flex items-center justify-between mb-5">
            <h2 class="text-[16px] font-semibold text-text-primary">{{ currentPen?.pen_name }} · 出栏数量</h2>
            <button
              class="flex items-center gap-1 text-[13px] text-text-secondary hover:text-text-primary transition-colors"
              @click="step = 1"
            >
              <ChevronLeft :size="14" :stroke-width="1.5" />
              重选圈舍
            </button>
          </div>
          <div class="grid grid-cols-2 gap-4 mb-4">
            <div>
              <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">
                出栏公畜（存栏 {{ currentPen?.male_count ?? 0 }}）
              </span>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  :disabled="maleCount <= 0"
                  class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                  @click="stepCount('male', -1)"
                >
                  −
                </button>
                <input
                  type="number"
                  :value="maleCount"
                  class="w-full text-center text-[16px] font-medium tnum text-text-primary bg-bg-subtle rounded-[8px] py-1.5 outline-none border border-transparent focus:border-primary transition-colors"
                  :min="0"
                  :max="currentPen?.male_count ?? 0"
                  :step="1"
                  @input="onCountInput('male', $event)"
                />
                <button
                  type="button"
                  :disabled="maleCount >= (currentPen?.male_count ?? 0)"
                  class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                  @click="stepCount('male', 1)"
                >
                  +
                </button>
              </div>
            </div>
            <div>
              <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">
                出栏母畜（存栏 {{ currentPen?.female_count ?? 0 }}）
              </span>
              <div class="flex items-center gap-2">
                <button
                  type="button"
                  :disabled="femaleCount <= 0"
                  class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                  @click="stepCount('female', -1)"
                >
                  −
                </button>
                <input
                  type="number"
                  :value="femaleCount"
                  class="w-full text-center text-[16px] font-medium tnum text-text-primary bg-bg-subtle rounded-[8px] py-1.5 outline-none border border-transparent focus:border-primary transition-colors"
                  :min="0"
                  :max="currentPen?.female_count ?? 0"
                  :step="1"
                  @input="onCountInput('female', $event)"
                />
                <button
                  type="button"
                  :disabled="femaleCount >= (currentPen?.female_count ?? 0)"
                  class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                  @click="stepCount('female', 1)"
                >
                  +
                </button>
              </div>
            </div>
          </div>

          <!-- F14 强关联耳标：非整栏出栏勾选个体；整栏出栏自动清档 -->
          <template v-if="maleCount + femaleCount !== 0">
            <p v-if="isFullExit" class="text-[12.5px] text-text-tertiary mb-4">
              整栏出栏：全部在栏个体（{{ slaughterInds.length }} 个耳标）将自动办理离栏。
            </p>
            <div
              v-else-if="insufficient"
              class="px-3.5 py-2.5 rounded-[10px] text-[12.5px] mb-4"
              :style="{
                backgroundColor: 'rgba(192,127,45,.08)',
                border: '1px solid rgba(192,127,45,.3)',
                color: 'var(--warning, #c07f2d)',
              }"
            >
              本圈在栏耳标不足（公 {{ maleInds.length }}/{{ maleCount }} · 母 {{ femaleInds.length }}/{{ femaleCount }}）， 请先到左侧「个体耳标」页一键补齐，或改为整栏出栏。
            </div>
            <div v-else class="mb-4">
              <p class="text-[12.5px] text-text-secondary mb-1.5">
                出栏耳标 <span style="color: var(--danger)">*</span><span class="text-text-tertiary">（勾选个体，已选 {{ selTags.size }}/{{ maleCount + femaleCount }}）</span>
              </p>
              <template v-for="g in tagGroups" :key="g.label">
                <div v-if="g.tags.length !== 0" class="mb-2.5">
                  <p class="text-[12px] text-text-tertiary mb-1">
                    {{ g.label }}（在栏 {{ g.tags.length }}，勾选 {{ groupSelected(g.tags) }}）
                  </p>
                  <div class="flex flex-wrap gap-1.5 max-h-[110px] overflow-y-auto p-2 rounded-[10px] bg-bg-subtle">
                    <button
                      v-for="it in g.tags"
                      :key="it.individual_id"
                      type="button"
                      :disabled="tagDisabled(g.tags, it, g.cap)"
                      :class="
                        cn(
                          'px-2 py-1 rounded-[6px] text-[12px] tnum border transition-colors',
                          tagChecked(it.ear_tag_no)
                            ? 'bg-primary text-white border-primary'
                            : 'bg-card text-text-secondary border-border hover:border-primary disabled:opacity-40 disabled:cursor-not-allowed',
                        )
                      "
                      @click="toggleTag(it.ear_tag_no)"
                    >
                      {{ it.ear_tag_no }}
                    </button>
                  </div>
                </div>
              </template>
            </div>
          </template>

          <div class="grid grid-cols-2 gap-4 mb-5">
            <div>
              <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">出栏均重 kg（选填）</span>
              <input
                type="number"
                :min="0"
                :step="0.1"
                :value="avgWeight"
                placeholder="填写出栏后可在喂食中心结算料肉比"
                class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary transition-colors"
                @input="avgWeight = ($event.target as HTMLInputElement).value"
              />
            </div>
            <div>
              <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">去向（选填）</span>
              <input
                :value="destination"
                placeholder="如：本地屠宰场"
                class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary transition-colors"
                @input="destination = ($event.target as HTMLInputElement).value"
              />
            </div>
          </div>
          <div
            v-if="err"
            class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
            :style="{
              backgroundColor: 'rgba(217,83,79,.07)',
              border: '1px solid rgba(217,83,79,.25)',
              color: 'var(--danger)',
            }"
            role="alert"
          >
            {{ err }}
          </div>
          <div class="flex justify-end">
            <button
              :disabled="loading"
              :class="
                cn(
                  'flex items-center gap-2 px-5 py-2 rounded-[10px] text-[14px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors',
                  loading && 'opacity-70 cursor-not-allowed',
                )
              "
              @click="onSubmitClick"
            >
              <span v-if="loading" class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin" />
              校验并提交出栏
            </button>
          </div>
        </div>

        <!-- 步骤 3：校验结果区 -->
        <div v-if="step === 3" class="bg-card rounded-[12px] p-6 card-highlight">
          <!-- 拦截红卡（文件感：白底红缘，正文表格列三要素） -->
          <template v-if="blocking && blocking.length > 0">
            <h2 class="text-[16px] font-semibold text-text-primary mb-4">出栏被拦截 · 休药期未满</h2>
            <div class="rounded-[10px] bg-card p-5 mb-5" :style="{ border: '1.5px solid var(--danger)' }">
              <div class="flex items-center gap-2 mb-3">
                <AlertTriangle :size="18" :stroke-width="1.5" :style="{ color: 'var(--danger)' }" />
                <span class="text-[14px] font-semibold" :style="{ color: 'var(--danger)' }">休药期未满，禁止出栏</span>
              </div>
              <table class="w-full text-[13px] mb-3">
                <thead>
                  <tr class="text-text-tertiary">
                    <th class="text-left font-medium pb-2">药品名</th>
                    <th class="text-left font-medium pb-2">末次用药日期</th>
                    <th class="text-left font-medium pb-2">休药期至</th>
                    <th class="text-right font-medium pb-2">剩余天数</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(m, i) in blocking ?? []" :key="i" class="border-t border-border">
                    <td class="py-2 text-text-primary font-medium">{{ m.med_name }}</td>
                    <td class="py-2 text-text-secondary tnum">{{ m.usage_date }}</td>
                    <td class="py-2 text-text-secondary tnum">{{ m.withdrawal_until }}</td>
                    <td class="py-2 text-right tnum font-medium" style="color: var(--danger)">{{ m.left_days }} 天</td>
                  </tr>
                </tbody>
              </table>
              <p class="text-[12.5px] text-text-tertiary leading-[1.6]">
                请核对末次用药日期与休药期；如用药已过休药期仍被拦截，请检查「近期用药」是否有漏登记。
              </p>
            </div>
            <div class="flex items-center justify-between">
              <button class="text-[13px] text-text-secondary hover:text-text-primary transition-colors" @click="step = 2">
                返回修改
              </button>
              <button
                class="text-[13px] text-text-tertiary underline hover:text-text-secondary transition-colors"
                @click="openForce"
              >
                强制继续（自行承担合规责任）
              </button>
            </div>
          </template>
          <!-- 通过绿卡 -->
          <template v-else-if="result">
            <h2 class="text-[16px] font-semibold text-text-primary mb-4">出栏登记成功</h2>
            <div class="rounded-[10px] p-5 mb-5 flex items-start gap-3" :style="{ backgroundColor: 'rgba(46,158,107,.08)' }">
              <Check :size="20" :stroke-width="2" class="mt-0.5 shrink-0" :style="{ color: 'var(--success)' }" />
              <div>
                <p class="text-[14px] font-medium text-text-primary">
                  休药期校验通过，已登记出栏 公{{ maleCount }} / 母{{ femaleCount }}（合计 {{ maleCount + femaleCount }} 头）
                </p>
                <p class="text-[13px] text-text-secondary mt-1">
                  存栏已扣减，出栏流水、生命周期事件与台账已联动记录{{ result?.withdrawal_check === 'forced' ? '（强制出栏，承诺备注已留痕）' : '' }}。 出栏均重已填时，可到喂食中心「成本结算」查看料肉比。
                </p>
              </div>
            </div>
            <div class="flex items-center justify-between">
              <button class="text-[13px] text-text-secondary hover:text-text-primary transition-colors" @click="resetFlow">
                继续登记其他圈舍
              </button>
              <button
                class="flex items-center gap-1.5 px-4 py-2 rounded-[10px] bg-primary text-white text-[14px] font-medium hover:bg-primary-600 transition-colors"
                @click="router.push('/records')"
              >
                <FileText :size="15" :stroke-width="1.5" />
                查看台账
              </button>
            </div>
          </template>
          <p v-else class="text-[13px] text-text-tertiary py-6 text-center">暂无校验结果，请返回上一步重新提交。</p>
        </div>

        <!-- 合规原文（06 规范 4.5：固定卡片底部） -->
        <p class="text-[12px] text-text-tertiary mt-4 leading-[1.6]">⚠ {{ COMPLIANCE_TEXT }}</p>
      </div>

      <!-- ===== 右侧：用药面板（辅助自查 + 登记）===== -->
      <div class="w-full lg:w-[340px] shrink-0">
        <div class="bg-card rounded-[12px] p-5 card-highlight">
          <div class="flex items-center justify-between mb-3">
            <h3 class="text-[16px] font-semibold text-text-primary">近期用药</h3>
            <button
              :disabled="!currentPen"
              :class="
                cn(
                  'flex items-center gap-1.5 px-3 py-1.5 rounded-[8px] text-[13px] font-medium transition-colors',
                  currentPen ? 'bg-primary-50 text-primary-700 hover:bg-primary-100' : 'bg-bg-inset text-text-disabled cursor-not-allowed',
                )
              "
              @click="openMed"
            >
              <Syringe :size="14" :stroke-width="1.5" />
              登记用药
            </button>
          </div>
          <p v-if="!currentPen" class="text-[13px] text-text-tertiary leading-[1.6]">选择圈舍后，可查看该圈近 90 天用药与休药期剩余天数。</p>
          <p v-else-if="usages.length === 0" class="text-[13px] text-text-tertiary leading-[1.6]">近 90 天无用药记录。用药后请及时登记，休药期校验以登记数据为准。</p>
          <div v-else class="space-y-2">
            <div
              v-for="u in usages"
              :key="u.usage_id"
              class="px-3.5 py-2.5 rounded-[10px]"
              :style="{ backgroundColor: u.withdrawal_left_days > 0 ? 'rgba(217,83,79,.05)' : 'var(--bg-subtle)' }"
            >
              <div class="flex items-center justify-between">
                <span class="text-[13px] font-medium text-text-primary">{{ u.med_name }}</span>
                <span
                  class="text-[12px] tnum font-medium"
                  :style="{ color: u.withdrawal_left_days > 0 ? 'var(--danger)' : 'var(--success)' }"
                >
                  {{ u.withdrawal_left_days > 0 ? `休药期剩 ${u.withdrawal_left_days} 天` : '已过休药期' }}
                </span>
              </div>
              <p class="text-[12px] text-text-tertiary mt-0.5">{{ u.usage_date }} 用药 · 休药期至 {{ u.withdrawal_until }}</p>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 强制出栏承诺备注弹窗（必填）===== -->
    <div v-if="forceOpen" class="fixed inset-0 z-50 flex items-center justify-center" @click="forceOpen = false">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[480px] max-w-[90vw] p-6 animate-modal-in"
        style="box-shadow: var(--e3)"
        @click.stop
      >
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-[16px] font-semibold text-text-primary">强制出栏确认</h2>
          <button class="p-1 rounded-[6px] hover:bg-bg-subtle" aria-label="关闭" @click="forceOpen = false">
            <X :size="18" class="text-text-tertiary" :stroke-width="1.5" />
          </button>
        </div>
        <p class="text-[13px] text-text-secondary leading-[1.7] mb-4">
          该圈舍存在休药期未满的用药记录。强制出栏将按你的承诺备注留痕登记，合规责任由你自行承担。
        </p>
        <textarea
          :value="forceNote"
          placeholder="如：已告知休药期要求，自行承担合规责任"
          class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary transition-colors resize-none h-[80px] mb-4"
          @input="forceNote = ($event.target as HTMLTextAreaElement).value"
        />
        <div
          v-if="forceErr"
          class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
          :style="{
            backgroundColor: 'rgba(217,83,79,.07)',
            border: '1px solid rgba(217,83,79,.25)',
            color: 'var(--danger)',
          }"
          role="alert"
        >
          {{ forceErr }}
        </div>
        <div class="flex justify-end gap-3">
          <button
            class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
            @click="forceOpen = false"
          >
            取消
          </button>
          <button
            :disabled="forceLoading"
            :class="
              cn(
                'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors flex items-center gap-2',
                'bg-danger hover:brightness-95',
                forceLoading && 'opacity-70 cursor-not-allowed',
              )
            "
            @click="submitForce"
          >
            <span v-if="forceLoading" class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin" />
            确认强制出栏
          </button>
        </div>
      </div>
    </div>

    <!-- ===== 用药登记弹窗 ===== -->
    <div v-if="medOpen && currentPen" class="fixed inset-0 z-50 flex items-center justify-center" @click="medOpen = false">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[500px] max-w-[90vw] p-6 animate-modal-in"
        style="box-shadow: var(--e3)"
        @click.stop
      >
        <div class="flex items-center justify-between mb-1.5">
          <h2 class="text-[16px] font-semibold text-text-primary">登记用药</h2>
          <button class="p-1 rounded-[6px] hover:bg-bg-subtle" aria-label="关闭" @click="medOpen = false">
            <X :size="18" class="text-text-tertiary" :stroke-width="1.5" />
          </button>
        </div>
        <p class="text-[12.5px] text-text-tertiary mb-5">{{ currentPen?.pen_name }} · 休药期截止日由系统按药品休药期自动计算</p>
        <div class="space-y-4">
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">药品（兽药库）</span>
            <select
              :value="medId"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary outline-none focus:border-primary transition-colors"
              @change="medId = Number(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="m in meds" :key="m.med_id" :value="m.med_id">
                {{ m.med_name }}（休药期 {{ m.withdrawal_period }} 天）
              </option>
            </select>
          </div>
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">用药日期</span>
            <input
              type="date"
              :value="usageDate"
              :max="todayLocal()"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary tnum outline-none focus:border-primary transition-colors"
              @input="usageDate = ($event.target as HTMLInputElement).value"
            />
          </div>
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">用法用量（选填）</span>
            <input
              :value="dosage"
              placeholder="如：按说明书拌料，连用 5 天"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary transition-colors"
              @input="dosage = ($event.target as HTMLInputElement).value"
            />
          </div>
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">备注（选填）</span>
            <input
              :value="note"
              placeholder="如：呼吸道预防"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary transition-colors"
              @input="note = ($event.target as HTMLInputElement).value"
            />
          </div>
          <p v-if="selectedMed" class="text-[12px] text-text-tertiary">
            登记后休药期至 {{ medUntilPreview }}，期间该圈出栏将被拦截
          </p>
          <div
            v-if="medErr"
            class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
            :style="{
              backgroundColor: 'rgba(217,83,79,.07)',
              border: '1px solid rgba(217,83,79,.25)',
              color: 'var(--danger)',
            }"
            role="alert"
          >
            {{ medErr }}
          </div>
          <div class="flex justify-end gap-3">
            <button
              class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
              @click="medOpen = false"
            >
              取消
            </button>
            <button
              :disabled="medLoading"
              :class="
                cn(
                  'px-5 py-2 rounded-[10px] text-[14px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors flex items-center gap-2',
                  medLoading && 'opacity-70 cursor-not-allowed',
                )
              "
              @click="submitMed"
            >
              <span v-if="medLoading" class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin" />
              确认登记
            </button>
          </div>
        </div>
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
