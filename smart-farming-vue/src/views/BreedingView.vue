<script setup lang="ts">
// 母畜繁殖管理页 — F07 FR-08（06 规范 4.4，mock 切真）
// - 顶部待产提醒卡片行：预产期倒计时大数字（tabular），3 天内 danger 红字 + 微脉动（全页唯一情绪焦点动效）
// - 提醒待办列表：预产期/断奶/免疫三类 todo（GET /lifecycle/reminders 惰性生成），处理=标记完成（TC-B03）
// - 母畜表格：耳标号/圈舍/胎次/状态标签/配种日期/预产期/操作；状态标签色固定（06 规范 4.4）
// - 单头档案抽屉（e3）：生命周期事件时间线（台账 lifecycle 类按 tag_no 聚合，F04 契约复用）；
//   登记入口按状态给出合法动作、非法动作置灰（EX-12 前端表达；服务端 40003 为唯一权威）
// - 批次事件登记（阉割/去角/转阶段）：pen 级，无个体状态变化
// - 演示档案：由 backend/seed_story.py 落库（浏览器内核下线后不再有前端注入入口）

import { computed, onBeforeUnmount, ref, watch, type Component, type Ref } from 'vue';
import { useRouter } from 'vue-router';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import {
  Baby,
  Bell,
  Check,
  ClipboardList,
  HeartCrack,
  HeartHandshake,
  Milk,
  Plus,
  Scissors,
  Stethoscope,
  Trash2,
  X,
} from 'lucide-vue-next';
import { cn, todayLocal } from '@/lib/utils';
import { useUserFarms, useUserPens } from '@/composables/use-farm-data';
import { stageOptions } from '@/lib/species-data';
import { ApiError } from '@/lib/api-contract';
import {
  apiCreateBreedingFemale,
  apiCreateLifecycleEvent,
  apiHandleReminder,
  apiListBreedingFemales,
  apiListReminders,
  apiUpdateBreedingFemale,
  type BreedStatus,
  type BreedingFemaleItem,
  type LifecycleEventType,
  type ReminderItem,
} from '@/api/breeding-api';
import { apiListRecords } from '@/api/record-api';

// ===== 状态标签色（06 规范 4.4 固定口径：空怀=灰/已配种=info 蓝/妊娠=primary 浅绿/哺乳=warning 浅橙/淘汰=灰划线）=====
const STATUS_META: Record<BreedStatus, { label: string; color: string; bg: string; strike?: boolean }> = {
  open: { label: '空怀', color: 'var(--text-secondary)', bg: 'var(--bg-subtle)' },
  bred: { label: '已配种', color: 'var(--info)', bg: 'rgba(91,141,239,.08)' },
  pregnant: { label: '妊娠', color: 'var(--primary-700)', bg: 'var(--primary-50)' },
  nursing: { label: '哺乳', color: 'var(--warning)', bg: 'rgba(232,162,61,.08)' },
  culled: { label: '淘汰', color: 'var(--text-tertiary)', bg: 'var(--bg-subtle)', strike: true },
};

/** 各状态合法登记动作（与 mock EVENT_MATRIX 一致；非法动作前端置灰，服务端 40003 兜底） */
type LegalAction = { type: LifecycleEventType; label: string; icon: Component };

const LEGAL_ACTIONS: Partial<Record<BreedStatus, LegalAction[]>> = {
  open: [{ type: 'breeding', label: '登记配种', icon: HeartHandshake }],
  bred: [
    { type: 'pregnancy_check', label: '妊娠检查', icon: Stethoscope },
    { type: 'abortion', label: '登记流产', icon: HeartCrack },
  ],
  pregnant: [
    { type: 'birth', label: '登记分娩', icon: Baby },
    { type: 'abortion', label: '登记流产', icon: HeartCrack },
  ],
  nursing: [{ type: 'weaning', label: '登记断奶', icon: Milk }],
  culled: [],
};

const REMINDER_TYPE_META: Record<ReminderItem['reminder_type'], { label: string; color: string; bg: string }> = {
  due_date: { label: '预产期', color: 'var(--danger)', bg: 'rgba(217,83,79,.07)' },
  weaning: { label: '断奶', color: 'var(--warning)', bg: 'rgba(232,162,61,.08)' },
  vaccine: { label: '免疫', color: 'var(--info)', bg: 'rgba(91,141,239,.08)' },
};

function daysLeft(dateStr: string): number {
  const today = new Date();
  const t = new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime();
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(dateStr);
  if (!m) return 0;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])).getTime();
  return Math.round((d - t) / 86_400_000);
}

function isUrgent(f: BreedingFemaleItem): boolean {
  return daysLeft(f.due_date ?? '') <= 3;
}

function dueCardStyle(f: BreedingFemaleItem): { border: string; backgroundColor: string | undefined } {
  const urgent = isUrgent(f);
  return {
    border: `1px solid ${urgent ? 'rgba(217,83,79,.35)' : 'var(--border)'}`,
    backgroundColor: urgent ? 'rgba(217,83,79,.04)' : undefined,
  };
}

// ===== 主页面 =====
const router = useRouter();
const queryClient = useQueryClient();
const { farms, loading: farmsLoading } = useUserFarms();
const selectedFarmId = ref<number | null>(null);
const currentFarm = computed(
  () => farms.value.find((f) => f.farm_id === selectedFarmId.value) ?? farms.value[0] ?? null,
);
const { pens } = useUserPens(computed(() => currentFarm.value?.farm_id ?? null));

const statusFilter = ref<BreedStatus | 'all'>('all');
const createOpen = ref(false);
const drawerFemale = ref<BreedingFemaleItem | null>(null);
const eventFemale = ref<BreedingFemaleItem | null>(null);
const eventType = ref<LifecycleEventType>('breeding');
const cullTarget = ref<BreedingFemaleItem | null>(null);
const batchOpen = ref(false);
const toast = ref<string | null>(null);

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

const femalesQuery = useQuery({
  queryKey: computed(() => ['breeding-females', currentFarm.value?.farm_id ?? 0]),
  queryFn: async () => {
    // 页面标题/提醒/圈舍都属于 currentFarm，列表也必须按场过滤（此前拉的是名下全部母畜）；
    // 并逐页取完，避免第 101 头之后在列表与状态计数里凭空消失。
    const farmId = currentFarm.value!.farm_id;
    const all: BreedingFemaleItem[] = [];
    for (let page = 1; ; page += 1) {
      const r = await apiListBreedingFemales({ farm_id: farmId, page, page_size: 100 });
      all.push(...r.items);
      if (page * r.page_size >= r.total) break;
    }
    return all;
  },
  enabled: computed(() => !!currentFarm.value),
  staleTime: 15_000,
});
const remindersQuery = useQuery({
  queryKey: computed(() => ['breeding-reminders', currentFarm.value?.farm_id ?? 0]),
  queryFn: async () => (await apiListReminders(currentFarm.value!.farm_id)).items,
  enabled: computed(() => !!currentFarm.value),
  staleTime: 15_000,
});
// 档案时间线数据源：台账 lifecycle 类（F04 契约复用，按 tag_no 聚合到个体）
const timelineQuery = useQuery({
  queryKey: computed(() => ['lifecycle-timeline', currentFarm.value?.farm_id ?? 0]),
  queryFn: async () =>
    (
      await apiListRecords({
        farm_id: currentFarm.value!.farm_id,
        record_type: 'lifecycle',
        page_size: 200,
      })
    ).items,
  enabled: computed(() => !!currentFarm.value),
  staleTime: 15_000,
});

const females = computed(() => femalesQuery.data.value ?? []);
const reminders = computed(() => remindersQuery.data.value ?? []);
const timeline = computed(() => timelineQuery.data.value ?? []);
const pendingReminders = computed(() => reminders.value.filter((r) => r.status === 'pending'));

function refreshAll() {
  queryClient.invalidateQueries({ queryKey: ['breeding-females'] });
  queryClient.invalidateQueries({ queryKey: ['breeding-reminders'] });
  queryClient.invalidateQueries({ queryKey: ['lifecycle-timeline'] });
  queryClient.invalidateQueries({ queryKey: ['user-pens'] }); // 分娩/淘汰改动存栏
  queryClient.invalidateQueries({ queryKey: ['user-farms'] });
}

let toastTimer: number | null = null;
function showToast(msg: string) {
  toast.value = msg;
  toastTimer = window.setTimeout(() => (toast.value = null), 2600);
}
onBeforeUnmount(() => {
  if (toastTimer !== null) window.clearTimeout(toastTimer);
});

async function handleReminderDone(r: ReminderItem) {
  try {
    await apiHandleReminder(r.reminder_id, { action: 'done' });
    queryClient.invalidateQueries({ queryKey: ['breeding-reminders'] });
    showToast('已标记完成');
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '网络异常，请重试');
  }
}

// 待产卡片行：妊娠档案按预产期升序（06 规范 4.4 情绪焦点）
const pregnant = computed(() =>
  females.value
    .filter((f) => f.breed_status === 'pregnant' && f.due_date)
    .sort((a, b) => (a.due_date ?? '').localeCompare(b.due_date ?? '')),
);

const statusTabs: { key: BreedStatus | 'all'; label: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'open', label: '空怀' },
  { key: 'bred', label: '已配种' },
  { key: 'pregnant', label: '妊娠' },
  { key: 'nursing', label: '哺乳' },
  { key: 'culled', label: '淘汰' },
];
const filtered = computed(() =>
  statusFilter.value === 'all'
    ? females.value
    : females.value.filter((f) => f.breed_status === statusFilter.value),
);

function statusCount(key: BreedStatus | 'all'): number {
  return key === 'all' ? females.value.length : females.value.filter((f) => f.breed_status === key).length;
}

function firstAction(f: BreedingFemaleItem): LegalAction | undefined {
  return (LEGAL_ACTIONS[f.breed_status] ?? [])[0];
}

function openFirstEvent(f: BreedingFemaleItem) {
  const action = firstAction(f);
  if (action) openEvent(f, action.type);
}

function reminderTiming(r: ReminderItem): string {
  return r.days_left < 0
    ? ` · 已过期 ${-r.days_left} 天`
    : r.days_left === 0
      ? ' · 就是今天'
      : ` · 还有 ${r.days_left} 天`;
}

function onCullEnter(e: MouseEvent) {
  (e.currentTarget as HTMLElement).style.color = 'var(--danger)';
}
function onCullLeave(e: MouseEvent) {
  (e.currentTarget as HTMLElement).style.color = '';
}

// ===== 新建档案弹窗（FR-01）=====
const createPenId = ref(0);
const createTagNo = ref('');
const createParity = ref(0);
const createStatus = ref<BreedStatus>('open');
const createBreedDate = ref('');
const createRemark = ref('');
const createErr = ref<string | null>(null);
const createLoading = ref(false);

function openCreate() {
  createPenId.value = pens.value[0]?.pen_id ?? 0;
  createTagNo.value = '';
  createParity.value = 0;
  createStatus.value = 'open';
  createBreedDate.value = '';
  createRemark.value = '';
  createErr.value = null;
  createLoading.value = false;
  createOpen.value = true;
}

async function submitCreate() {
  // 提交瞬间取值：请求期间继续编辑不影响本次结果
  const penId = createPenId.value;
  const tagNo = createTagNo.value;
  const parity = createParity.value;
  const status = createStatus.value;
  const breedDate = createBreedDate.value;
  const remark = createRemark.value;

  createErr.value = null;
  if (!penId) {
    createErr.value = '请先创建圈舍';
    return;
  }
  if (!tagNo.trim()) {
    createErr.value = '请填写耳标编号';
    return;
  }
  if (status !== 'open' && !breedDate) {
    createErr.value = '补录非空怀状态需填写配种日期';
    return;
  }
  createLoading.value = true;
  try {
    await apiCreateBreedingFemale({
      pen_id: penId,
      tag_no: tagNo.trim(),
      parity,
      breed_status: status,
      ...(breedDate ? { breed_date: breedDate } : {}),
      ...(remark.trim() ? { remark: remark.trim() } : {}),
    });
    handleCreateSaved(`已建档 ${tagNo.trim()}`);
  } catch (e) {
    createErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    createLoading.value = false;
  }
}

function handleCreateSaved(msg: string) {
  createOpen.value = false;
  refreshAll();
  showToast(msg);
}

// ===== 事件登记弹窗（FR-02；按事件类型给字段）=====
const EVENT_TITLES: Record<LifecycleEventType, string> = {
  breeding: '登记配种',
  pregnancy_check: '妊娠检查',
  abortion: '登记流产',
  birth: '登记分娩',
  weaning: '登记断奶',
  castration: '阉割',
  dehorning: '去角',
  stage_change: '转阶段',
  cull: '淘汰登记',
};

const eventDate = ref(todayLocal());
const eventRemark = ref('');
const semenType = ref('人工授精');
const checkResult = ref('阳性');
const liveMale = ref(0);
const liveFemale = ref(0);
const deadCount = ref(0);
const eventErr = ref<string | null>(null);
const eventLoading = ref(false);

function openEvent(female: BreedingFemaleItem, type: LifecycleEventType) {
  // 抽屉叠在弹窗之上，弹窗打开时切换动作要保留已填表单态，因此只换数据不重建
  if (eventFemale.value) {
    eventFemale.value = female;
    eventType.value = type;
    return;
  }
  eventFemale.value = female;
  eventType.value = type;
  eventDate.value = todayLocal();
  eventRemark.value = '';
  semenType.value = '人工授精';
  checkResult.value = '阳性';
  liveMale.value = 0;
  liveFemale.value = 0;
  deadCount.value = 0;
  eventErr.value = null;
  eventLoading.value = false;
}

async function submitEvent() {
  // 提交瞬间取值
  const female = eventFemale.value;
  if (!female) return;
  const type = eventType.value;
  const date = eventDate.value;
  const semen = semenType.value;
  const check = checkResult.value;
  const male = liveMale.value;
  const femaleCount = liveFemale.value;
  const dead = deadCount.value;
  const remark = eventRemark.value;

  eventErr.value = null;
  if (type === 'birth' && male + femaleCount + dead <= 0) {
    eventErr.value = '请填写产仔数（活仔/死胎）';
    return;
  }
  eventLoading.value = true;
  try {
    const detail: Record<string, number | string> | undefined =
      type === 'breeding'
        ? { semen_type: semen }
        : type === 'pregnancy_check'
          ? { result: check }
          : type === 'birth'
            ? { live_male: male, live_female: femaleCount, dead_count: dead }
            : undefined;
    const res = await apiCreateLifecycleEvent({
      event_type: type,
      female_id: female.female_id,
      event_date: date,
      ...(detail ? { detail } : {}),
      ...(remark.trim() ? { remark: remark.trim() } : {}),
    });
    const extra =
      type === 'breeding' && res.due_date
        ? `，预产期 ${res.due_date}（已自动计算）`
        : type === 'weaning' && res.parity !== undefined
          ? `，胎次更新为 ${res.parity}`
          : type === 'birth'
            ? `，活仔已计入「${female.pen_name}」存栏`
            : '';
    handleEventSaved(`${female.tag_no} ${EVENT_TITLES[type]}成功${extra}`);
  } catch (e) {
    eventErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    eventLoading.value = false;
  }
}

function handleEventSaved(msg: string) {
  eventFemale.value = null;
  refreshAll();
  showToast(msg);
}

// ===== 淘汰确认（cull：death 存栏 −1 联动）=====
const cullErr = ref<string | null>(null);
const cullLoading = ref(false);

function openCull(female: BreedingFemaleItem) {
  cullTarget.value = female;
  cullErr.value = null;
  cullLoading.value = false;
}

async function submitCull() {
  const female = cullTarget.value;
  if (!female) return;
  cullLoading.value = true;
  cullErr.value = null;
  try {
    await apiCreateLifecycleEvent({ event_type: 'cull', female_id: female.female_id });
    handleCullSaved(`${female.tag_no} 已淘汰（存栏 −1 并记台账）`);
  } catch (e) {
    cullErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    cullLoading.value = false;
  }
}

function handleCullSaved(msg: string) {
  cullTarget.value = null;
  refreshAll();
  showToast(msg);
}

// ===== 批次事件登记（阉割/去角/转阶段：pen 级，无个体状态变化）=====
type BatchType = 'castration' | 'dehorning' | 'stage_change';
const BATCH_TYPES: { v: BatchType; label: string }[] = [
  { v: 'castration', label: '阉割' },
  { v: 'dehorning', label: '去角' },
  { v: 'stage_change', label: '转阶段' },
];

const batchPenId = ref(0);
const batchType = ref<BatchType>('castration');
const batchToStage = ref('fattening');
const batchNote = ref('');
const batchErr = ref<string | null>(null);
const batchLoading = ref(false);
const currentPen = computed(() => pens.value.find((p) => p.pen_id === batchPenId.value));
const batchStageOptions = computed(() => stageOptions(currentPen.value?.species_code ?? 'pig'));

function openBatch() {
  batchPenId.value = pens.value[0]?.pen_id ?? 0;
  batchType.value = 'castration';
  batchToStage.value = 'fattening';
  batchNote.value = '';
  batchErr.value = null;
  batchLoading.value = false;
  batchOpen.value = true;
}

async function submitBatch() {
  // 提交瞬间取值
  const penId = batchPenId.value;
  const type = batchType.value;
  const toStage = batchToStage.value;
  const note = batchNote.value;
  const speciesCode = currentPen.value?.species_code ?? 'pig';

  batchErr.value = null;
  if (!penId) {
    batchErr.value = '请先创建圈舍';
    return;
  }
  batchLoading.value = true;
  try {
    const detail: Record<string, number | string> =
      type === 'stage_change' ? { to_stage: toStage } : { method: note.trim() || '按常规操作' };
    await apiCreateLifecycleEvent({
      event_type: type,
      pen_id: penId,
      ...(type === 'stage_change'
        ? { detail }
        : { detail, remark: note.trim() || undefined }),
    });
    handleBatchSaved(
      type === 'stage_change'
        ? `已转阶段为「${stageOptions(speciesCode).find((s) => s.value === toStage)?.label ?? toStage}」`
        : '批次事件已登记并记台账',
    );
  } catch (e) {
    batchErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    batchLoading.value = false;
  }
}

function handleBatchSaved(msg: string) {
  batchOpen.value = false;
  refreshAll();
  showToast(msg);
}

// ===== 单头档案抽屉（e3：时间线 + 按状态合法动作）=====
const drawerEditing = ref(false);
const drawerRemark = ref('');
const drawerParity = ref(0);
const drawerErr = ref<string | null>(null);

function openDrawer(female: BreedingFemaleItem) {
  drawerFemale.value = female;
  drawerEditing.value = false;
  drawerRemark.value = female.remark;
  drawerParity.value = female.parity;
  drawerErr.value = null;
}

function closeDrawer() {
  drawerFemale.value = null;
}

const drawerData = computed<BreedingFemaleItem | null>(() => {
  const base = drawerFemale.value;
  if (!base) return null;
  return females.value.find((f) => f.female_id === base.female_id) ?? base;
});
const drawerTimeline = computed(() => {
  const base = drawerFemale.value;
  if (!base) return [];
  return timeline.value.filter((r) => r.record_ext?.tag_no === base.tag_no);
});
const drawerActions = computed<LegalAction[]>(() =>
  drawerData.value ? LEGAL_ACTIONS[drawerData.value.breed_status] ?? [] : [],
);
const drawerEvents = computed(() =>
  [...drawerTimeline.value].sort((a, b) => b.create_time.localeCompare(a.create_time)),
);

async function saveDrawerEdit() {
  const female = drawerData.value;
  if (!female) return;
  const parity = drawerParity.value;
  const remark = drawerRemark.value;
  drawerErr.value = null;
  try {
    await apiUpdateBreedingFemale(female.female_id, { parity, remark: remark.trim() });
    drawerEditing.value = false;
    refreshAll();
    showToast('档案已更新');
  } catch (e) {
    drawerErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  }
}

// 数字输入：非法/负数即时回 0，并同步 DOM 值
function nonNegIntInputHandler(target: Ref<number>) {
  return (e: Event) => {
    const el = e.target as HTMLInputElement;
    const n = Math.max(0, Number.parseInt(el.value, 10) || 0);
    target.value = n;
    if (el.value !== String(n)) el.value = String(n);
  };
}

function stepperInputHandler(target: Ref<number>) {
  return (e: Event) => {
    const el = e.target as HTMLInputElement;
    const n = Number.parseInt(el.value, 10);
    const v = Number.isFinite(n) ? Math.max(0, n) : 0;
    target.value = v;
    if (el.value !== String(v)) el.value = String(v);
  };
}

const onCreateParityInput = nonNegIntInputHandler(createParity);
const onDrawerParityInput = nonNegIntInputHandler(drawerParity);
const onLiveMaleInput = stepperInputHandler(liveMale);
const onLiveFemaleInput = stepperInputHandler(liveFemale);
const onDeadCountInput = stepperInputHandler(deadCount);
</script>

<template>
  <div v-if="!farmsLoading && farms.length === 0" class="px-8 py-6 max-w-[1400px]">
    <div class="mb-6">
      <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">繁殖管理</h1>
      <p class="text-[14px] text-text-secondary mt-0.5">母畜档案 · 预产期 · 断奶 · 免疫提醒</p>
    </div>
    <div class="bg-card rounded-[12px] p-14 card-highlight flex flex-col items-center text-center">
      <div class="w-16 h-16 rounded-full bg-primary-50 flex items-center justify-center mb-5">
        <HeartHandshake :size="28" :stroke-width="1.5" class="text-primary" />
      </div>
      <h3 class="text-[16px] font-semibold text-text-primary mb-2">还没有养殖场</h3>
      <p class="text-[13.5px] text-text-secondary mb-6 max-w-[360px] leading-[1.7]">
        创建养殖场与母猪圈后，即可建立母畜个体档案，预产期将自动计算并提醒。
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
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">繁殖管理</h1>
        <p class="text-[14px] text-text-secondary mt-0.5">
          {{ currentFarm ? `${currentFarm.farm_name} · ${females.length} 头母畜档案` : '加载中…' }}
        </p>
      </div>
      <div class="flex items-center gap-2">
        <button
          class="flex items-center gap-2 px-4 py-2 rounded-[10px] bg-bg-subtle text-text-secondary text-[14px] font-medium hover:bg-bg-inset transition-colors duration-[var(--duration-fast)]"
          @click="openBatch"
        >
          <Scissors :size="16" :stroke-width="1.5" />
          批次事件
        </button>
        <button
          class="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-[10px] text-[14px] font-medium hover:bg-primary-600 transition-colors duration-[var(--duration-fast)]"
          @click="openCreate"
        >
          <Plus :size="16" :stroke-width="1.5" />
          新建档案
        </button>
      </div>
    </div>

    <!-- ① 待产提醒卡片行（06 规范 4.4：倒计时大数字，3 天内红字微脉动） -->
    <div
      v-if="pregnant.length > 0"
      class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 mb-6"
    >
      <div
        v-for="f in pregnant"
        :key="f.female_id"
        class="bg-card rounded-[12px] p-5 card-highlight"
        :style="dueCardStyle(f)"
      >
        <div class="flex items-center justify-between mb-2">
          <span class="text-[14px] font-semibold text-text-primary">{{ f.tag_no }}</span>
          <span class="text-[12px] text-text-tertiary">{{ f.pen_name }} · 第 {{ f.parity + 1 }} 胎</span>
        </div>
        <div class="flex items-end gap-2">
          <span
            :class="cn('text-[40px] font-semibold leading-[1.1] tnum', isUrgent(f) ? 'animate-due-pulse' : '')"
            :style="{ color: isUrgent(f) ? 'var(--danger)' : 'var(--text-primary)' }"
          >
            {{ daysLeft(f.due_date ?? '') }}
          </span>
          <span class="text-[13px] text-text-secondary mb-1.5">天后预产</span>
        </div>
        <p class="text-[12px] text-text-tertiary mt-1">
          预产期 {{ f.due_date }} · 配种 {{ f.breed_date ?? '—' }}
        </p>
      </div>
    </div>

    <!-- ② 提醒待办（todo 型：处理=标记完成，TC-B03；过期 due_date 持续置顶 EX-13） -->
    <div v-if="pendingReminders.length > 0" class="bg-card rounded-[12px] p-5 card-highlight mb-6">
      <div class="flex items-center gap-2 mb-3">
        <Bell :size="16" :stroke-width="1.5" class="text-warning" />
        <h3 class="text-[16px] font-semibold text-text-primary">生产提醒待办</h3>
        <span class="text-[12px] text-text-tertiary">
          （预产期前 3 天 / 断奶 / 免疫节点，同步展示在告警中心）
        </span>
      </div>
      <div class="space-y-2">
        <div
          v-for="r in pendingReminders"
          :key="r.reminder_id"
          class="flex items-center gap-3 px-3.5 py-2.5 rounded-[10px]"
          :style="{ backgroundColor: r.days_left < 0 ? 'rgba(217,83,79,.05)' : 'var(--bg-subtle)' }"
        >
          <span
            class="px-1.5 py-0.5 rounded-[6px] text-[11px] font-medium shrink-0"
            :style="{
              backgroundColor: REMINDER_TYPE_META[r.reminder_type].bg,
              color: REMINDER_TYPE_META[r.reminder_type].color,
            }"
          >
            {{ REMINDER_TYPE_META[r.reminder_type].label }}
          </span>
          <div class="flex-1 min-w-0">
            <p class="text-[13px] text-text-primary truncate">{{ r.title }}</p>
            <p class="text-[12px] text-text-tertiary">{{ r.remind_date }}{{ reminderTiming(r) }}</p>
          </div>
          <button
            class="flex items-center gap-1 px-3 py-1.5 rounded-[8px] text-[13px] text-primary-700 bg-primary-50 hover:bg-primary-100 transition-colors shrink-0"
            @click="handleReminderDone(r)"
          >
            <Check :size="13" :stroke-width="1.5" />
            标记完成
          </button>
        </div>
      </div>
    </div>

    <!-- ③ 状态筛选 -->
    <div class="flex items-center gap-1 mb-4 bg-bg-subtle rounded-[10px] p-1 w-fit flex-wrap">
      <button
        v-for="tab in statusTabs"
        :key="tab.key"
        :class="
          cn(
            'flex items-center gap-1.5 px-4 py-2 rounded-[8px] text-[13px] transition-all duration-[var(--duration-fast)]',
            statusFilter === tab.key
              ? 'bg-card text-text-primary font-medium e1'
              : 'text-text-secondary hover:text-text-primary',
          )
        "
        @click="statusFilter = tab.key"
      >
        {{ tab.label }}
        <span v-if="statusCount(tab.key) > 0" class="text-[11px] text-text-tertiary tnum">
          {{ statusCount(tab.key) }}
        </span>
      </button>
    </div>

    <!-- ④ 母畜表格（<768 转卡片列表，06 规范 2.3 断点表） -->
    <div v-if="filtered.length > 0" class="bg-card rounded-[12px] card-highlight overflow-hidden">
      <table class="w-full">
        <thead>
          <tr class="bg-subtle text-[12px] font-medium text-text-secondary">
            <th class="text-left px-6 py-3">耳标号</th>
            <th class="text-left px-4 py-3 hidden md:table-cell">圈舍</th>
            <th class="text-right px-4 py-3">胎次</th>
            <th class="text-left px-4 py-3">状态</th>
            <th class="text-left px-4 py-3 hidden lg:table-cell">配种日期</th>
            <th class="text-left px-4 py-3 hidden lg:table-cell">预产期</th>
            <th class="text-right px-6 py-3">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="f in filtered"
            :key="f.female_id"
            class="border-t border-border hover:bg-primary-50/40 transition-colors duration-[var(--duration-fast)]"
          >
            <td class="px-6 py-3.5 text-[14px] font-medium text-text-primary">{{ f.tag_no }}</td>
            <td class="px-4 py-3.5 text-[13px] text-text-secondary hidden md:table-cell">{{ f.pen_name }}</td>
            <td class="px-4 py-3.5 text-[14px] text-text-primary text-right tnum">{{ f.parity }}</td>
            <td class="px-4 py-3.5">
              <span
                :class="cn('px-2 py-0.5 rounded-[6px] text-[12px] font-medium', STATUS_META[f.breed_status].strike && 'line-through')"
                :style="{
                  backgroundColor: STATUS_META[f.breed_status].bg,
                  color: STATUS_META[f.breed_status].color,
                }"
              >
                {{ STATUS_META[f.breed_status].label }}
              </span>
            </td>
            <td class="px-4 py-3.5 text-[13px] text-text-secondary tnum hidden lg:table-cell">
              {{ f.breed_date ?? '—' }}
            </td>
            <td class="px-4 py-3.5 text-[13px] text-text-secondary tnum hidden lg:table-cell">
              {{ f.due_date ?? '—' }}
            </td>
            <td class="px-6 py-3.5">
              <div class="flex items-center justify-end gap-2">
                <button
                  class="flex items-center gap-1 px-3 py-1.5 rounded-[8px] text-[13px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
                  @click="openDrawer(f)"
                >
                  <ClipboardList :size="13" :stroke-width="1.5" />
                  档案
                </button>
                <button
                  v-if="firstAction(f)"
                  class="px-3 py-1.5 rounded-[8px] text-[13px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors"
                  @click="openFirstEvent(f)"
                >
                  {{ firstAction(f)?.label }}
                </button>
                <button
                  v-if="f.breed_status !== 'culled'"
                  class="px-2 py-1.5 rounded-[8px] text-text-tertiary bg-bg-subtle hover:bg-bg-inset transition-colors"
                  style="--hover-color: var(--danger)"
                  :aria-label="`淘汰 ${f.tag_no}`"
                  @mouseenter="onCullEnter"
                  @mouseleave="onCullLeave"
                  @click="openCull(f)"
                >
                  <Trash2 :size="13" :stroke-width="1.5" />
                </button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="bg-card rounded-[12px] p-14 card-highlight flex flex-col items-center text-center">
      <div class="w-16 h-16 rounded-full bg-primary-50 flex items-center justify-center mb-5">
        <HeartHandshake :size="28" :stroke-width="1.5" class="text-primary" />
      </div>
      <h3 class="text-[16px] font-semibold text-text-primary mb-2">
        {{ females.length === 0 ? '还没有母畜档案' : '该状态下暂无档案' }}
      </h3>
      <p class="text-[13.5px] text-text-secondary mb-6 max-w-[360px] leading-[1.7]">
        {{
          females.length === 0
            ? '建档后登记配种即可自动计算预产期（猪 114 天），提前 3 天提醒。'
            : '切换其他状态筛选查看。'
        }}
      </p>
      <div v-if="females.length === 0" class="flex items-center gap-3">
        <button
          class="flex items-center gap-2 px-5 py-2.5 bg-primary text-white rounded-[10px] text-[14px] font-medium hover:bg-primary-600 transition-colors"
          @click="openCreate"
        >
          <Plus :size="16" :stroke-width="1.5" />
          新建档案
        </button>
      </div>
    </div>

    <!-- ===== 弹窗层 ===== -->
    <div
      v-if="createOpen && currentFarm"
      class="fixed inset-0 z-50 flex items-center justify-center"
      @click="createOpen = false"
    >
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[500px] max-w-[90vw] p-6 animate-modal-in max-h-[90vh] overflow-y-auto"
        style="box-shadow: var(--e3)"
        @click.stop
      >
        <div class="flex items-center justify-between mb-1.5">
          <h2 class="text-[16px] font-semibold text-text-primary">新建母畜档案</h2>
          <button
            class="p-1 rounded-[6px] hover:bg-bg-subtle transition-colors"
            aria-label="关闭"
            @click="createOpen = false"
          >
            <X :size="18" :stroke-width="1.5" class="text-text-tertiary" />
          </button>
        </div>
        <p class="text-[12.5px] text-text-tertiary mb-5">建档后繁殖状态只能通过登记事件变更</p>
        <div class="space-y-4">
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">所属圈舍</span>
            <select
              :value="createPenId"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary outline-none focus:border-primary transition-colors"
              @change="createPenId = Number(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="p in pens" :key="p.pen_id" :value="p.pen_id">{{ p.pen_name }}</option>
            </select>
          </div>
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">耳标编号</span>
            <input
              v-model="createTagNo"
              placeholder="如 M-021"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary transition-colors"
            />
          </div>
          <div class="grid grid-cols-2 gap-4">
            <div>
              <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">已有胎次</span>
              <input
                type="number"
                min="0"
                :value="createParity"
                class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary tnum outline-none focus:border-primary transition-colors"
                @input="onCreateParityInput"
              />
            </div>
            <div>
              <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">当前状态</span>
              <select
                :value="createStatus"
                class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary outline-none focus:border-primary transition-colors"
                @change="createStatus = ($event.target as HTMLSelectElement).value as BreedStatus"
              >
                <option value="open">空怀</option>
                <option value="bred">已配种</option>
                <option value="pregnant">妊娠</option>
                <option value="nursing">哺乳</option>
              </select>
            </div>
          </div>
          <div v-if="createStatus !== 'open'">
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">
              配种日期（补录，用于自动算预产期）
            </span>
            <input
              v-model="createBreedDate"
              type="date"
              :max="todayLocal()"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary tnum outline-none focus:border-primary transition-colors"
            />
          </div>
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">备注（选填）</span>
            <input
              v-model="createRemark"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary transition-colors"
            />
          </div>
          <div
            v-if="createErr"
            class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
            style="background-color: rgba(217,83,79,.07); border: 1px solid rgba(217,83,79,.25); color: var(--danger)"
            role="alert"
          >
            {{ createErr }}
          </div>
          <div class="flex justify-end gap-3">
            <button
              class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
              @click="createOpen = false"
            >
              取消
            </button>
            <button
              :disabled="createLoading"
              :class="
                cn(
                  'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors flex items-center gap-2',
                  'bg-primary hover:bg-primary-600',
                  createLoading && 'opacity-70 cursor-not-allowed',
                )
              "
              @click="submitCreate"
            >
              <span
                v-if="createLoading"
                class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin"
              />
              {{ createLoading ? '提交中…' : '确认建档' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="batchOpen && currentFarm"
      class="fixed inset-0 z-50 flex items-center justify-center"
      @click="batchOpen = false"
    >
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[500px] max-w-[90vw] p-6 animate-modal-in max-h-[90vh] overflow-y-auto"
        style="box-shadow: var(--e3)"
        @click.stop
      >
        <div class="flex items-center justify-between mb-1.5">
          <h2 class="text-[16px] font-semibold text-text-primary">批次事件登记</h2>
          <button
            class="p-1 rounded-[6px] hover:bg-bg-subtle transition-colors"
            aria-label="关闭"
            @click="batchOpen = false"
          >
            <X :size="18" :stroke-width="1.5" class="text-text-tertiary" />
          </button>
        </div>
        <p class="text-[12.5px] text-text-tertiary mb-5">阉割/去角/转阶段为圈舍级事件，不改变母畜个体状态</p>
        <div class="space-y-4">
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">圈舍</span>
            <select
              :value="batchPenId"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary outline-none focus:border-primary transition-colors"
              @change="batchPenId = Number(($event.target as HTMLSelectElement).value)"
            >
              <option v-for="p in pens" :key="p.pen_id" :value="p.pen_id">{{ p.pen_name }}</option>
            </select>
          </div>
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">事件类型</span>
            <div class="grid grid-cols-3 gap-2">
              <button
                v-for="o in BATCH_TYPES"
                :key="o.v"
                type="button"
                :class="
                  cn(
                    'py-2 rounded-[10px] text-[13px] font-medium border transition-all duration-[var(--duration-fast)]',
                    batchType === o.v
                      ? 'bg-primary-50 text-primary-700 border-primary'
                      : 'bg-bg-subtle text-text-secondary border-transparent hover:bg-bg-inset',
                  )
                "
                @click="batchType = o.v"
              >
                {{ o.label }}
              </button>
            </div>
          </div>
          <div v-if="batchType === 'stage_change'">
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">目标阶段</span>
            <select
              v-model="batchToStage"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary outline-none focus:border-primary transition-colors"
            >
              <option v-for="s in batchStageOptions" :key="s.value" :value="s.value">{{ s.label }}</option>
            </select>
          </div>
          <div v-else>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">操作方式（选填）</span>
            <input
              v-model="batchNote"
              placeholder="如：14 日龄阉割，术后涂碘酒"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary transition-colors"
            />
          </div>
          <div
            v-if="batchErr"
            class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
            style="background-color: rgba(217,83,79,.07); border: 1px solid rgba(217,83,79,.25); color: var(--danger)"
            role="alert"
          >
            {{ batchErr }}
          </div>
          <div class="flex justify-end gap-3">
            <button
              class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
              @click="batchOpen = false"
            >
              取消
            </button>
            <button
              :disabled="batchLoading"
              :class="
                cn(
                  'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors flex items-center gap-2',
                  'bg-primary hover:bg-primary-600',
                  batchLoading && 'opacity-70 cursor-not-allowed',
                )
              "
              @click="submitBatch"
            >
              <span
                v-if="batchLoading"
                class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin"
              />
              {{ batchLoading ? '提交中…' : '确认登记' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="eventFemale"
      class="fixed inset-0 z-50 flex items-center justify-center"
      @click="eventFemale = null"
    >
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[500px] max-w-[90vw] p-6 animate-modal-in max-h-[90vh] overflow-y-auto"
        style="box-shadow: var(--e3)"
        @click.stop
      >
        <div class="flex items-center justify-between mb-1.5">
          <h2 class="text-[16px] font-semibold text-text-primary">{{ EVENT_TITLES[eventType] }}</h2>
          <button
            class="p-1 rounded-[6px] hover:bg-bg-subtle transition-colors"
            aria-label="关闭"
            @click="eventFemale = null"
          >
            <X :size="18" :stroke-width="1.5" class="text-text-tertiary" />
          </button>
        </div>
        <p class="text-[12.5px] text-text-tertiary mb-5">
          {{ eventFemale.tag_no }} · {{ eventFemale.pen_name }} · 当前「{{ eventFemale.breed_status_label }}」
        </p>
        <div class="space-y-4">
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">事件日期</span>
            <input
              v-model="eventDate"
              type="date"
              :max="todayLocal()"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary tnum outline-none focus:border-primary transition-colors"
            />
          </div>

          <div v-if="eventType === 'breeding'">
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">配种方式</span>
            <div class="flex rounded-[10px] overflow-hidden border border-border">
              <button
                v-for="o in ['人工授精', '本交']"
                :key="o"
                type="button"
                :class="
                  cn(
                    'flex-1 px-3.5 py-1.5 text-[13px] font-medium transition-colors',
                    semenType === o ? 'bg-primary text-white' : 'bg-card text-text-secondary hover:bg-bg-subtle',
                  )
                "
                @click="semenType = o"
              >
                {{ o }}
              </button>
            </div>
            <p class="text-[12px] text-text-tertiary mt-1.5">
              保存后自动计算预产期（配种日 + 物种妊娠期天数），并生成预产期前 3 天提醒
            </p>
          </div>

          <div v-if="eventType === 'pregnancy_check'">
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">检查结果</span>
            <div class="flex rounded-[10px] overflow-hidden border border-border">
              <button
                v-for="o in ['阳性', '待复查']"
                :key="o"
                type="button"
                :class="
                  cn(
                    'flex-1 px-3.5 py-1.5 text-[13px] font-medium transition-colors',
                    checkResult === o ? 'bg-primary text-white' : 'bg-card text-text-secondary hover:bg-bg-subtle',
                  )
                "
                @click="checkResult = o"
              >
                {{ o }}
              </button>
            </div>
            <p class="text-[12px] text-text-tertiary mt-1.5">阴性（未配准）请登记「流产」回退空怀状态</p>
          </div>

          <template v-if="eventType === 'birth'">
            <div class="grid grid-cols-3 gap-3">
              <div>
                <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">活仔 · 公</span>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    :disabled="liveMale <= 0"
                    class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                    @click="liveMale = Math.max(0, liveMale - 1)"
                  >
                    −
                  </button>
                  <input
                    type="number"
                    :value="liveMale"
                    class="w-full text-center text-[16px] font-medium tnum text-text-primary bg-bg-subtle rounded-[8px] py-1.5 outline-none border border-transparent focus:border-primary transition-colors"
                    min="0"
                    step="1"
                    @input="onLiveMaleInput"
                  />
                  <button
                    type="button"
                    class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium"
                    @click="liveMale = liveMale + 1"
                  >
                    +
                  </button>
                </div>
              </div>
              <div>
                <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">活仔 · 母</span>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    :disabled="liveFemale <= 0"
                    class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                    @click="liveFemale = Math.max(0, liveFemale - 1)"
                  >
                    −
                  </button>
                  <input
                    type="number"
                    :value="liveFemale"
                    class="w-full text-center text-[16px] font-medium tnum text-text-primary bg-bg-subtle rounded-[8px] py-1.5 outline-none border border-transparent focus:border-primary transition-colors"
                    min="0"
                    step="1"
                    @input="onLiveFemaleInput"
                  />
                  <button
                    type="button"
                    class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium"
                    @click="liveFemale = liveFemale + 1"
                  >
                    +
                  </button>
                </div>
              </div>
              <div>
                <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">死胎</span>
                <div class="flex items-center gap-2">
                  <button
                    type="button"
                    :disabled="deadCount <= 0"
                    class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium disabled:opacity-40 disabled:cursor-not-allowed"
                    @click="deadCount = Math.max(0, deadCount - 1)"
                  >
                    −
                  </button>
                  <input
                    type="number"
                    :value="deadCount"
                    class="w-full text-center text-[16px] font-medium tnum text-text-primary bg-bg-subtle rounded-[8px] py-1.5 outline-none border border-transparent focus:border-primary transition-colors"
                    min="0"
                    step="1"
                    @input="onDeadCountInput"
                  />
                  <button
                    type="button"
                    class="w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium"
                    @click="deadCount = deadCount + 1"
                  >
                    +
                  </button>
                </div>
              </div>
            </div>
            <p class="text-[12px] text-text-tertiary -mt-1">
              活仔将作为 birth 存栏变动计入「{{ eventFemale.pen_name }}」；断奶后仔猪转群须在场舍管理页显式登记
            </p>
          </template>

          <p v-if="eventType === 'weaning'" class="text-[12px] text-text-tertiary -mt-1">
            断奶后胎次 +1；仔猪存栏不随事件搬动，转保育/育肥圈请到场舍管理页使用「转群」
          </p>

          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">备注（选填）</span>
            <input
              v-model="eventRemark"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary transition-colors"
            />
          </div>
          <div
            v-if="eventErr"
            class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
            style="background-color: rgba(217,83,79,.07); border: 1px solid rgba(217,83,79,.25); color: var(--danger)"
            role="alert"
          >
            {{ eventErr }}
          </div>
          <div class="flex justify-end gap-3">
            <button
              class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
              @click="eventFemale = null"
            >
              取消
            </button>
            <button
              :disabled="eventLoading"
              :class="
                cn(
                  'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors flex items-center gap-2',
                  'bg-primary hover:bg-primary-600',
                  eventLoading && 'opacity-70 cursor-not-allowed',
                )
              "
              @click="submitEvent"
            >
              <span
                v-if="eventLoading"
                class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin"
              />
              {{ eventLoading ? '提交中…' : `确认${EVENT_TITLES[eventType]}` }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <div
      v-if="cullTarget"
      class="fixed inset-0 z-50 flex items-center justify-center"
      @click="cullTarget = null"
    >
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[500px] max-w-[90vw] p-6 animate-modal-in max-h-[90vh] overflow-y-auto"
        style="box-shadow: var(--e3)"
        @click.stop
      >
        <div class="flex items-center justify-between mb-1.5">
          <h2 class="text-[16px] font-semibold text-text-primary">淘汰登记</h2>
          <button
            class="p-1 rounded-[6px] hover:bg-bg-subtle transition-colors"
            aria-label="关闭"
            @click="cullTarget = null"
          >
            <X :size="18" :stroke-width="1.5" class="text-text-tertiary" />
          </button>
        </div>
        <p class="text-[12.5px] text-text-tertiary mb-5">{{ cullTarget.tag_no }} · {{ cullTarget.pen_name }}</p>
        <p class="text-[14px] text-text-secondary leading-[1.7] mb-4">
          淘汰将同时在该圈舍记一笔死淘存栏（母 −1）并落生命周期台账，档案状态置为「淘汰」后不可再登记事件。确认淘汰吗？
        </p>
        <div
          v-if="cullErr"
          class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
          style="background-color: rgba(217,83,79,.07); border: 1px solid rgba(217,83,79,.25); color: var(--danger)"
          role="alert"
        >
          {{ cullErr }}
        </div>
        <div class="flex justify-end gap-3">
          <button
            class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
            @click="cullTarget = null"
          >
            取消
          </button>
          <button
            :disabled="cullLoading"
            :class="
              cn(
                'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors flex items-center gap-2',
                'bg-danger hover:brightness-95',
                cullLoading && 'opacity-70 cursor-not-allowed',
              )
            "
            @click="submitCull"
          >
            <span
              v-if="cullLoading"
              class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin"
            />
            {{ cullLoading ? '提交中…' : '确认淘汰' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="drawerFemale" class="fixed inset-0 z-50 flex justify-end" @click="closeDrawer">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        v-if="drawerData"
        class="relative bg-card w-[520px] max-w-[92vw] h-full overflow-y-auto p-6 animate-slide-in"
        style="box-shadow: var(--e3)"
        @click.stop
      >
        <div class="flex items-center justify-between mb-1">
          <h2 class="text-[16px] font-semibold text-text-primary">{{ drawerData.tag_no }} 的档案</h2>
          <button class="p-1 rounded-[6px] hover:bg-bg-subtle transition-colors" aria-label="关闭" @click="closeDrawer">
            <X :size="18" :stroke-width="1.5" class="text-text-tertiary" />
          </button>
        </div>
        <p class="text-[12.5px] text-text-tertiary mb-5">
          {{ drawerData.pen_name }} · 第 {{ drawerData.parity }} 胎 · 建档 {{ drawerData.create_time.slice(0, 10) }}
        </p>

        <!-- 状态与关键日期 -->
        <div class="flex items-center gap-2 flex-wrap mb-5">
          <span
            class="px-2 py-0.5 rounded-[6px] text-[12px] font-medium"
            :style="{
              backgroundColor: STATUS_META[drawerData.breed_status].bg,
              color: STATUS_META[drawerData.breed_status].color,
            }"
          >
            {{ STATUS_META[drawerData.breed_status].label }}
          </span>
          <span class="text-[13px] text-text-secondary">配种 {{ drawerData.breed_date ?? '—' }}</span>
          <span class="text-[13px] text-text-secondary">预产期 {{ drawerData.due_date ?? '—' }}</span>
        </div>

        <!-- 合法登记动作（EX-12 前端表达：非法动作不展示，服务端 40003 兜底） -->
        <div v-if="drawerActions.length > 0" class="flex flex-wrap gap-2 mb-6">
          <button
            v-for="a in drawerActions"
            :key="a.type"
            class="flex items-center gap-1.5 px-3.5 py-2 rounded-[10px] bg-primary-50 text-primary-700 text-[13px] font-medium hover:bg-primary-100 transition-colors"
            @click="drawerFemale && openEvent(drawerFemale, a.type)"
          >
            <component :is="a.icon" :size="14" :stroke-width="1.5" />
            {{ a.label }}
          </button>
        </div>
        <p v-if="drawerActions.length === 0" class="text-[13px] text-text-tertiary mb-6">
          已淘汰的档案不再支持登记事件。
        </p>

        <!-- 时间线（台账 lifecycle 类按 tag_no 聚合，F04 契约复用） -->
        <h3 class="text-[14px] font-semibold text-text-primary mb-3">生命周期时间线</h3>
        <div v-if="drawerEvents.length > 0" class="relative pl-5 mb-6">
          <div class="absolute left-[5px] top-2 bottom-2 w-px bg-border" />
          <div v-for="ev in drawerEvents" :key="ev.record_id" class="relative pb-4">
            <div class="absolute left-[-19px] top-1.5 w-[11px] h-[11px] rounded-full bg-primary" />
            <p class="text-[13px] font-medium text-text-primary">{{ ev.summary }}</p>
            <p class="text-[12px] text-text-tertiary">
              {{ ev.record_ext?.event_date ?? ev.create_time.slice(0, 10) }}{{ ev.record_ext?.event_type ? ` · ${ev.record_ext.event_type}` : '' }}
            </p>
          </div>
        </div>
        <p v-else class="text-[13px] text-text-tertiary mb-6">暂无事件记录，登记配种后从这里查看完整时间线。</p>

        <!-- 编辑（PUT 禁改 breed_status，40003） -->
        <div v-if="drawerEditing" class="space-y-3 mb-4">
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">胎次</span>
            <input
              type="number"
              min="0"
              :value="drawerParity"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary tnum outline-none focus:border-primary transition-colors"
              @input="onDrawerParityInput"
            />
          </div>
          <div>
            <span class="text-[13px] font-medium text-text-secondary mb-1.5 block">备注</span>
            <input
              v-model="drawerRemark"
              class="w-full px-3 py-2 rounded-[10px] border border-border-strong bg-card text-[14px] text-text-primary outline-none focus:border-primary transition-colors"
            />
          </div>
          <div
            v-if="drawerErr"
            class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
            style="background-color: rgba(217,83,79,.07); border: 1px solid rgba(217,83,79,.25); color: var(--danger)"
            role="alert"
          >
            {{ drawerErr }}
          </div>
          <div class="flex justify-end gap-3">
            <button
              class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
              @click="drawerEditing = false"
            >
              取消
            </button>
            <button
              class="px-5 py-2 rounded-[10px] text-[14px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors"
              @click="saveDrawerEdit"
            >
              保存
            </button>
          </div>
        </div>
        <div v-else class="flex items-center justify-between">
          <p class="text-[13px] text-text-secondary">备注：{{ drawerData.remark || '—' }}</p>
          <button
            class="px-3 py-1.5 rounded-[8px] text-[13px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
            @click="drawerEditing = true"
          >
            编辑档案
          </button>
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
