<script setup lang="ts">
// 场舍管理页 — F02 养殖场/圈舍/存栏管理（06 规范 4.3）
// - 左侧养殖场列表卡（增删改，FR-01/02）+ 右侧圈舍卡片网格（FR-09）
// - 调整存栏弹窗：四类型（产仔/死淘/购入/手动，不含"出栏"——入口占位策略）+ 公母步进器 + 备注
// - 转群弹窗：同场、同物种目标圈（FR-07/EX-B3）
// - 出栏登记：F07 起替换占位 toast → 跳转 /slaughter 出栏登记页（休药期校验，携带 pen_id 预选）
// - 提交成功：卡片数字 600ms 滚动补间 + primary-100 短暂高亮（06 规范动效编排）
// - 空态：无养殖场 → 建场引导；有场无圈 → 建圈引导（06 规范 3.3）

import { computed, defineComponent, h, nextTick, onBeforeUnmount, onMounted, ref, watch, type PropType } from 'vue';
import { useRouter } from 'vue-router';
import {
  Warehouse,
  Plus,
  ArrowRightLeft,
  Truck,
  Settings2,
  X,
  Pencil,
  Trash2,
  MapPin,
  AlertTriangle,
} from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { useUserFarms, useUserPens, type UserFarm, type UserPen } from '@/composables/use-farm-data';
import { apiCreatePen, apiListVarieties } from '@/api/auth-api';
import {
  apiCreateFarm,
  apiUpdateFarm,
  apiDeleteFarm,
  apiUpdatePen,
  apiDeletePen,
  apiStockChange,
  apiTransfer,
  type StockChangeType,
} from '@/api/farm-api';
import { apiListIndividuals, type IndividualItem } from '@/api/individual-api';
import { ApiError } from '@/lib/api-contract';
import { stageOptions, speciesName } from '@/lib/species-data';
import { PROVINCES, resolveRegion } from '@/lib/region-data';

const inputCls =
  'w-full h-10 px-3 rounded-[10px] bg-card border border-border-strong text-[14px] text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-colors';

type StockType = Exclude<StockChangeType, 'sale'>;

const STOCK_TYPES: { value: StockType; label: string; desc: string }[] = [
  { value: 'birth', label: '产仔', desc: '新增数量' },
  { value: 'death', label: '死淘', desc: '按死淘数量填写' },
  { value: 'purchase', label: '购入', desc: '新增数量' },
  { value: 'manual', label: '手动', desc: '库存盘点修正' },
];

const MANUAL_DIRECTIONS = [
  { v: 'inc', label: '调增 +' },
  { v: 'dec', label: '调减 −' },
] as const;

/** F14：耳标行解析（textarea 每行一个 → 去空行） */
function parseTagLines(text: string): string[] {
  return text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);
}

/** F14：按前缀顺序生成耳标号（扫描已有档案顺延序号） */
function buildAutoTags(existing: string[], prefix: string, letter: 'M' | 'F', count: number): string[] {
  const re = new RegExp(`^${prefix}-${letter}(\\d+)$`);
  let seq = 0;
  for (const t of existing) {
    const m = t.match(re);
    if (m) seq = Math.max(seq, Number(m[1]));
  }
  return Array.from({ length: count }, (_, i) => `${prefix}-${letter}${String(seq + i + 1).padStart(3, '0')}`);
}

function dangerEnter(e: MouseEvent) {
  (e.currentTarget as HTMLElement).style.color = 'var(--danger)';
}

function dangerLeave(e: MouseEvent) {
  (e.currentTarget as HTMLElement).style.color = '';
}

// ===== 通用弹窗壳 =====
const Modal = defineComponent({
  props: {
    title: { type: String, required: true },
    subtitle: { type: String, default: undefined },
  },
  emits: ['close'],
  setup(props, { slots, emit }) {
    return () =>
      h(
        'div',
        { class: 'fixed inset-0 z-50 flex items-center justify-center', onClick: () => emit('close') },
        [
          h('div', { class: 'absolute inset-0 bg-[rgba(20,30,24,0.45)]' }),
          h(
            'div',
            {
              class:
                'relative bg-card rounded-[16px] w-[500px] max-w-[90vw] p-6 animate-modal-in max-h-[90vh] overflow-y-auto',
              style: { boxShadow: 'var(--e3)' },
              onClick: (e: MouseEvent) => e.stopPropagation(),
            },
            [
              h('div', { class: 'flex items-center justify-between mb-1.5' }, [
                h('h2', { class: 'text-[16px] font-semibold text-text-primary' }, props.title),
                h(
                  'button',
                  {
                    onClick: () => emit('close'),
                    class: 'p-1 rounded-[6px] hover:bg-bg-subtle transition-colors',
                    'aria-label': '关闭',
                  },
                  [h(X, { size: 18, class: 'text-text-tertiary', strokeWidth: 1.5 })],
                ),
              ]),
              props.subtitle ? h('p', { class: 'text-[12.5px] text-text-tertiary mb-5' }, props.subtitle) : null,
              slots.default?.(),
            ],
          ),
        ],
      );
  },
});

const FormError = defineComponent({
  props: {
    err: { type: String as PropType<string | null>, default: null },
  },
  setup(props) {
    return () =>
      props.err
        ? h(
            'div',
            {
              class: 'mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]',
              style: {
                backgroundColor: 'rgba(217,83,79,.07)',
                border: '1px solid rgba(217,83,79,.25)',
                color: 'var(--danger)',
              },
              role: 'alert',
            },
            props.err,
          )
        : null;
  },
});

const ModalActions = defineComponent({
  props: {
    loading: { type: Boolean, required: true },
    submitText: { type: String, required: true },
  },
  emits: ['cancel', 'submit'],
  setup(props, { emit }) {
    return () =>
      h('div', { class: 'flex justify-end gap-3' }, [
        h(
          'button',
          {
            onClick: () => emit('cancel'),
            class:
              'px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors',
          },
          '取消',
        ),
        h(
          'button',
          {
            onClick: () => emit('submit'),
            disabled: props.loading,
            class: cn(
              'px-5 py-2 rounded-[10px] text-[14px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors flex items-center gap-2',
              props.loading && 'opacity-70 cursor-not-allowed',
            ),
          },
          [
            props.loading
              ? h('span', { class: 'w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin' })
              : null,
            props.loading ? '提交中…' : props.submitText,
          ],
        ),
      ]);
  },
});

// ===== 步进器（EX-10：负数/非法输入钳制为 0，键盘可达） =====
const Stepper = defineComponent({
  props: {
    id: { type: String, required: true },
    value: { type: Number, required: true },
  },
  emits: {
    change: (v: number) => typeof v === 'number',
  },
  setup(props, { emit }) {
    return () =>
      h('div', { class: 'flex items-center gap-2' }, [
        h(
          'button',
          {
            type: 'button',
            onClick: () => emit('change', Math.max(0, props.value - 1)),
            disabled: props.value <= 0,
            class:
              'w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium disabled:opacity-40 disabled:cursor-not-allowed',
          },
          '−',
        ),
        h('input', {
          id: props.id,
          type: 'number',
          value: props.value,
          min: 0,
          step: 1,
          class:
            'w-full text-center text-[16px] font-medium tnum text-text-primary bg-bg-subtle rounded-[8px] py-1.5 outline-none border border-transparent focus:border-primary transition-colors',
          onInput: (e: Event) => {
            // EX-10 前端钳制：负数/非数字一律归 0，不发起非法请求
            const el = e.target as HTMLInputElement;
            const n = parseInt(el.value, 10);
            const v = Number.isFinite(n) ? Math.max(0, n) : 0;
            emit('change', v);
            // 与受控输入一致：父级钳制后以 props.value 回写（含父级上限钳制）
            nextTick(() => {
              if (el.value !== String(props.value)) el.value = String(props.value);
            });
          },
        }),
        h(
          'button',
          {
            type: 'button',
            onClick: () => emit('change', props.value + 1),
            class:
              'w-[32px] h-[32px] rounded-[8px] bg-bg-subtle flex items-center justify-center text-text-secondary hover:bg-bg-inset transition-colors text-[16px] font-medium',
          },
          '+',
        ),
      ]);
  },
});

/** F14：离栏个体勾选组（公/母分组；选中数受步进器数量约束） */
const TagSelectGroup = defineComponent({
  props: {
    title: { type: String, required: true },
    tags: { type: Array as PropType<IndividualItem[]>, required: true },
    selected: { type: Object as PropType<Set<string>>, required: true },
    cap: { type: Number, required: true },
  },
  emits: {
    toggle: (tag: string) => typeof tag === 'string',
  },
  setup(props, { emit }) {
    return () => {
      if (props.tags.length === 0) return null;
      return h('div', { class: 'mb-3' }, [
        h('p', { class: 'text-[12.5px] text-text-secondary mb-1.5' }, [
          `${props.title} `,
          h(
            'span',
            { class: 'text-text-tertiary' },
            `（在栏 ${props.tags.length} 个，勾选 ${props.selected.size}/${props.cap}）`,
          ),
        ]),
        h(
          'div',
          { class: 'flex flex-wrap gap-1.5 max-h-[120px] overflow-y-auto p-2 rounded-[10px] bg-bg-subtle' },
          props.tags.map((it) => {
            const checked = props.selected.has(it.ear_tag_no);
            const disabled = !checked && props.selected.size >= props.cap;
            return h(
              'button',
              {
                key: it.individual_id,
                type: 'button',
                onClick: () => emit('toggle', it.ear_tag_no),
                disabled,
                class: cn(
                  'px-2 py-1 rounded-[6px] text-[12px] tnum border transition-colors',
                  checked
                    ? 'bg-primary text-white border-primary'
                    : 'bg-card text-text-secondary border-border hover:border-primary disabled:opacity-40 disabled:cursor-not-allowed',
                ),
              },
              it.ear_tag_no,
            );
          }),
        ),
      ]);
    };
  },
});

// ===== 危险操作二次确认（红色；焦点默认在取消按钮，06 规范无障碍项） =====
const ConfirmDialog = defineComponent({
  props: {
    title: { type: String, required: true },
    confirmText: { type: String, required: true },
    danger: { type: Boolean, default: false },
    onConfirm: { type: Function as PropType<() => Promise<void>>, required: true },
  },
  emits: ['cancel'],
  setup(props, { slots, emit }) {
    const loading = ref(false);
    const cancelRef = ref<HTMLButtonElement | null>(null);

    onMounted(() => cancelRef.value?.focus());

    async function handleConfirm() {
      loading.value = true;
      try {
        await props.onConfirm();
      } finally {
        loading.value = false;
      }
    }

    return () =>
      h('div', { class: 'fixed inset-0 z-50 flex items-center justify-center', onClick: () => emit('cancel') }, [
        h('div', { class: 'absolute inset-0 bg-[rgba(20,30,24,0.45)]' }),
        h(
          'div',
          {
            class: 'relative bg-card rounded-[16px] w-[420px] max-w-[90vw] p-6 animate-modal-in',
            style: { boxShadow: 'var(--e3)' },
            onClick: (e: MouseEvent) => e.stopPropagation(),
          },
          [
            h('h2', { class: 'text-[16px] font-semibold text-text-primary mb-3' }, props.title),
            h('p', { class: 'text-[13.5px] text-text-secondary leading-[1.7]' }, slots.default?.()),
            h('div', { class: 'flex justify-end gap-3 mt-6' }, [
              h(
                'button',
                {
                  ref: cancelRef,
                  onClick: () => emit('cancel'),
                  class:
                    'px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors',
                },
                '取消',
              ),
              h(
                'button',
                {
                  onClick: handleConfirm,
                  disabled: loading.value,
                  class: cn(
                    'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors',
                    loading.value && 'opacity-70 cursor-not-allowed',
                  ),
                  style: { backgroundColor: 'var(--danger)' },
                },
                loading.value ? '处理中…' : props.confirmText,
              ),
            ]),
          ],
        ),
      ]);
  },
});

// ===== 数字滚动补间（600ms）+ primary-100 短暂高亮 =====
const AnimatedNumber = defineComponent({
  props: {
    value: { type: Number, required: true },
  },
  setup(props) {
    const display = ref(props.value);
    const flash = ref(false);
    let prev = props.value;
    let raf = 0;
    let flashTimer: number | null = null;

    watch(
      () => props.value,
      (value) => {
        const from = prev;
        prev = value;
        if (from === value) return;
        if (raf) {
          cancelAnimationFrame(raf);
          raf = 0;
        }
        if (flashTimer !== null) {
          window.clearTimeout(flashTimer);
          flashTimer = null;
        }
        flash.value = true;
        const t0 = performance.now();
        const duration = 600;
        const tick = (t: number) => {
          const p = Math.min(1, (t - t0) / duration);
          const eased = 1 - Math.pow(1 - p, 3); // ease-out cubic
          display.value = Math.round(from + (value - from) * eased);
          if (p < 1) raf = requestAnimationFrame(tick);
        };
        raf = requestAnimationFrame(tick);
        flashTimer = window.setTimeout(() => {
          flash.value = false;
          flashTimer = null;
        }, 1000);
      },
    );

    onBeforeUnmount(() => {
      cancelAnimationFrame(raf);
      if (flashTimer !== null) window.clearTimeout(flashTimer);
    });

    return () =>
      h(
        'span',
        {
          class: 'inline-block rounded-[8px] px-1.5 transition-colors duration-500',
          style: flash.value ? { backgroundColor: 'var(--primary-100)' } : undefined,
        },
        String(display.value),
      );
  },
});

// ===== 主页面 =====
const router = useRouter();
const { farms, loading: farmsLoading, error: farmsError, reload: reloadFarms } = useUserFarms();
const selectedFarmId = ref<number | null>(null);
const currentFarm = computed<UserFarm | null>(
  () => farms.value.find((f) => f.farm_id === selectedFarmId.value) ?? farms.value[0] ?? null,
);
const currentFarmId = computed(() => currentFarm.value?.farm_id ?? null);
const { pens, loading: pensLoading, reload: reloadPens } = useUserPens(currentFarmId);
const currentFarmSpeciesName = computed(() => speciesName(currentFarm.value?.species_code ?? ''));

const pageSubtitle = computed(() => {
  const farm = currentFarm.value;
  if (farm) return `${farm.farm_name} · ${farm.location}`;
  return farmsLoading.value ? '加载中…' : '—';
});

watch(
  [farms, selectedFarmId],
  ([list, sel]) => {
    if (list.length > 0 && (!sel || !list.some((f) => f.farm_id === sel))) {
      selectedFarmId.value = list[0].farm_id;
    }
  },
  { immediate: true },
);

// 弹窗状态（互斥打开）
const farmFormOpen = ref<'create' | UserFarm | null>(null);
const farmDelete = ref<UserFarm | null>(null);
const penFormOpen = ref<'create' | UserPen | null>(null);
const penDelete = ref<UserPen | null>(null);
const adjustPen = ref<UserPen | null>(null);
const transferPen = ref<UserPen | null>(null);
const toast = ref<string | null>(null);

function showToast(msg: string) {
  toast.value = msg;
  window.setTimeout(() => (toast.value = null), 2600);
}

async function handleDeleteFarm() {
  if (!farmDelete.value) return;
  try {
    await apiDeleteFarm(farmDelete.value.farm_id);
    farmDelete.value = null;
    reloadFarms(); // guide_done 计算判定自动回退（F01 决策 2 自愈），下次进入引导
    showToast('养殖场已删除，下设数据保留为档案');
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '网络异常，请重试');
  }
}

async function handleDeletePen() {
  if (!penDelete.value) return;
  try {
    await apiDeletePen(penDelete.value.pen_id);
    penDelete.value = null;
    reloadPens();
    showToast('圈舍已删除，历史流水与台账保留');
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '网络异常，请重试');
  }
}

function goSell(pen: UserPen) {
  router.push({ path: '/slaughter', query: { pen_id: pen.pen_id } });
}

function stageLabelFor(pen: UserPen): string {
  return stageOptions(pen.species_code).find((o) => o.value === pen.batch_stage)?.label ?? '未设阶段';
}

// ===== 养殖场表单（新建/编辑，FR-01）=====
const ffName = ref('');
const ffProvCode = ref('');
const ffCityCode = ref('');
const ffCountyCode = ref('');
const ffAddress = ref('');
const ffContact = ref('');
const ffPhone = ref('');
const ffErr = ref<string | null>(null);
const ffLoading = ref(false);

const ffIsEdit = computed(() => !!farmFormOpen.value && farmFormOpen.value !== 'create');
const ffFarm = computed<UserFarm | null>(() => {
  const open = farmFormOpen.value;
  return open && open !== 'create' ? open : null;
});
const ffProvince = computed(() => PROVINCES.find((p) => p.code === ffProvCode.value));
const ffCity = computed(() => ffProvince.value?.children?.find((c) => c.code === ffCityCode.value));
const ffRegion = computed(() => resolveRegion(ffProvCode.value, ffCityCode.value, ffCountyCode.value));
const ffSubtitle = computed(() => (ffIsEdit.value ? ffFarm.value?.farm_name : '一个账号可管理多个养殖场'));

function openFarmForm(target: 'create' | UserFarm) {
  farmFormOpen.value = target;
  const farm = target === 'create' ? null : target;
  ffName.value = farm?.farm_name ?? '';
  ffProvCode.value = '';
  ffCityCode.value = '';
  ffCountyCode.value = '';
  ffAddress.value = farm?.address ?? '';
  ffContact.value = '';
  ffPhone.value = '';
  ffErr.value = null;
  ffLoading.value = false;
}

async function submitFarmForm() {
  ffErr.value = null;
  if (!ffName.value.trim()) {
    ffErr.value = '请填写养殖场名称';
    return;
  }
  if (!ffIsEdit.value && !ffRegion.value) {
    ffErr.value = '请选择省市区';
    return;
  }
  ffLoading.value = true;
  try {
    const region = ffRegion.value;
    if (ffIsEdit.value && ffFarm.value) {
      // 只提交有值/有变化的字段，避免空值覆盖已有联系人信息
      await apiUpdateFarm(ffFarm.value.farm_id, {
        farm_name: ffName.value.trim(),
        ...(region
          ? { province: region.province, city: region.city, county: region.county, adcode: region.adcode }
          : {}),
        address: ffAddress.value.trim(),
        ...(ffContact.value.trim() ? { contact_person: ffContact.value.trim() } : {}),
        ...(ffPhone.value.trim() ? { phone: ffPhone.value.trim() } : {}),
      });
    } else if (region) {
      await apiCreateFarm({
        farm_name: ffName.value.trim(),
        province: region.province,
        city: region.city,
        county: region.county,
        adcode: region.adcode,
        address: ffAddress.value.trim(),
        contact_person: ffContact.value.trim(),
        phone: ffPhone.value.trim(),
      });
    }
    farmFormOpen.value = null;
    reloadFarms();
  } catch (e) {
    ffErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    ffLoading.value = false;
  }
}

// ===== 圈舍表单（新建：含初始存栏；编辑：名称/品种/阶段，FR-03）=====
const pfPenName = ref('');
const pfVarietyCode = ref('common');
const pfVarieties = ref<{ variety_code: string; variety_name: string }[]>([]);
const pfMale = ref(0);
const pfFemale = ref(0);
const pfStage = ref('');
const pfErr = ref<string | null>(null);
const pfLoading = ref(false);

const pfIsEdit = computed(() => !!penFormOpen.value && penFormOpen.value !== 'create');
const pfPen = computed<UserPen | null>(() => {
  const open = penFormOpen.value;
  return open && open !== 'create' ? open : null;
});
const pfSubtitle = computed(() => {
  const farm = currentFarm.value;
  if (!farm) return '';
  return `${farm.farm_name} · 物种继承主物种（${speciesName(farm.species_code)}），不可修改`;
});
const pfStageOptions = computed(() => stageOptions(currentFarm.value?.species_code ?? ''));

let pfVarietiesSeq = 0;
watch(
  [() => penFormOpen.value, () => currentFarm.value?.species_code],
  ([open, species]) => {
    const seq = ++pfVarietiesSeq;
    if (!open || !species) return;
    apiListVarieties(species)
      .then((r) => {
        if (seq === pfVarietiesSeq) pfVarieties.value = r.items;
      })
      .catch(() => {
        if (seq === pfVarietiesSeq) pfVarieties.value = [{ variety_code: 'common', variety_name: '通用' }];
      });
  },
  { immediate: true },
);

function openPenForm(target: 'create' | UserPen) {
  penFormOpen.value = target;
  const pen = target === 'create' ? null : target;
  pfPenName.value = pen?.pen_name ?? '';
  pfVarietyCode.value = pen?.variety_code ?? 'common';
  pfMale.value = 0;
  pfFemale.value = 0;
  pfStage.value = pen?.batch_stage ?? '';
  pfErr.value = null;
  pfLoading.value = false;
  pfVarieties.value = [];
}

async function submitPenForm() {
  pfErr.value = null;
  if (!pfPenName.value.trim()) {
    pfErr.value = '请填写圈舍名称';
    return;
  }
  pfLoading.value = true;
  try {
    if (pfIsEdit.value && pfPen.value) {
      await apiUpdatePen(pfPen.value.pen_id, {
        pen_name: pfPenName.value.trim(),
        variety_code: pfVarietyCode.value,
        batch_stage: pfStage.value || null,
      });
    } else {
      const farm = currentFarm.value;
      if (!farm) return;
      await apiCreatePen({
        farm_id: farm.farm_id,
        pen_name: pfPenName.value.trim(),
        variety_code: pfVarietyCode.value,
        male_count: pfMale.value,
        female_count: pfFemale.value,
        batch_stage: pfStage.value || null,
      });
    }
    penFormOpen.value = null;
    reloadPens();
  } catch (e) {
    pfErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    pfLoading.value = false;
  }
}

// ===== 调整存栏弹窗（FR-06：四类型 + 公母步进器 + 备注；F14 强关联耳标）=====
const asType = ref<StockType>('birth');
const asDirection = ref<'inc' | 'dec'>('inc'); // manual 方向（±）
const asMale = ref(0);
const asFemale = ref(0);
const asRemark = ref('');
const asErr = ref<string | null>(null);
const asLoading = ref(false);

// F14：本圈个体档案（含离栏，用于顺延编号；勾选仅取在栏）
const asInds = ref<IndividualItem[]>([]);
const asMaleTagText = ref('');
const asFemaleTagText = ref('');
const asExitSelected = ref<Set<string>>(new Set());

const asIsDecrease = computed(() => asType.value === 'death' || (asType.value === 'manual' && asDirection.value === 'dec'));
const asTagPrefix = computed(() => `P${adjustPen.value?.pen_id ?? 0}`);
const asActiveInds = computed(() => asInds.value.filter((i) => i.status === 'active'));
const asActiveMaleInds = computed(() => asActiveInds.value.filter((i) => i.gender === 'male'));
const asActiveFemaleInds = computed(() => asActiveInds.value.filter((i) => i.gender === 'female'));
const asExitMaleSel = computed(
  () => [...asExitSelected.value].filter((t) => asActiveMaleInds.value.some((i) => i.ear_tag_no === t)).length,
);
const asExitFemaleSel = computed(() => asExitSelected.value.size - asExitMaleSel.value);
const asTypeDesc = computed(() =>
  asType.value === 'manual'
    ? '盘点修正等非业务场景使用，将计入台账"手动调整"，便于审计'
    : STOCK_TYPES.find((t) => t.value === asType.value)?.desc,
);
const asSubtitle = computed(() => {
  const pen = adjustPen.value;
  if (!pen) return '';
  return `${pen.pen_name} · 当前公 ${pen.male_count} / 母 ${pen.female_count} · 耳标号前缀 ${asTagPrefix.value}`;
});
const asRemarkPlaceholder = computed(() =>
  asType.value === 'death'
    ? '如：疑似呼吸道疾病，已隔离'
    : asType.value === 'manual'
      ? '必填：请说明调整原因，如盘点修正 -2'
      : '如：3号母猪产仔11头',
);

let asIndsSeq = 0;
watch(
  () => adjustPen.value?.pen_id,
  (penId) => {
    const seq = ++asIndsSeq;
    asInds.value = [];
    if (!penId) return;
    apiListIndividuals({ pen_id: penId, status: 'all', page_size: 100 })
      .then((r) => {
        if (seq === asIndsSeq && adjustPen.value?.pen_id === penId) asInds.value = r.items;
      })
      .catch(() => {
        if (seq === asIndsSeq && adjustPen.value?.pen_id === penId) asInds.value = [];
      });
  },
);

function openAdjustDialog(pen: UserPen) {
  adjustPen.value = pen;
  asType.value = 'birth';
  asDirection.value = 'inc';
  asMale.value = 0;
  asFemale.value = 0;
  asRemark.value = '';
  asErr.value = null;
  asLoading.value = false;
  asInds.value = [];
  asMaleTagText.value = '';
  asFemaleTagText.value = '';
  asExitSelected.value = new Set();
}

function asSelectType(t: StockType) {
  asType.value = t;
  asExitSelected.value = new Set();
}

function asSelectDirection(d: 'inc' | 'dec') {
  asDirection.value = d;
  asExitSelected.value = new Set();
}

function asAutoFillTags(gender: 'male' | 'female') {
  const count = gender === 'male' ? asMale.value : asFemale.value;
  if (count <= 0) return;
  const tags = buildAutoTags(
    asInds.value.map((i) => i.ear_tag_no),
    asTagPrefix.value,
    gender === 'male' ? 'M' : 'F',
    count,
  );
  if (gender === 'male') asMaleTagText.value = tags.join('\n');
  else asFemaleTagText.value = tags.join('\n');
}

function asToggleExit(tag: string) {
  const next = new Set(asExitSelected.value);
  if (next.has(tag)) next.delete(tag);
  else next.add(tag);
  asExitSelected.value = next;
}

async function submitAdjust() {
  asErr.value = null;
  if (asMale.value === 0 && asFemale.value === 0) {
    asErr.value = '请填写变动数量';
    return;
  }
  if (asType.value === 'manual' && !asRemark.value.trim()) {
    asErr.value = '请填写调整原因';
    return;
  }
  // F14 强关联前端预检（服务端仍为权威校验）
  if (asIsDecrease.value) {
    if (asExitSelected.value.size !== asMale.value + asFemale.value) {
      asErr.value = `请勾选与数量一致的离栏耳标（已选 ${asExitSelected.value.size}/${asMale.value + asFemale.value}）`;
      return;
    }
    if (asExitMaleSel.value !== asMale.value || asExitFemaleSel.value !== asFemale.value) {
      asErr.value = `离栏耳标公母分布与数量不符（需公 ${asMale.value} / 母 ${asFemale.value}）`;
      return;
    }
  } else {
    if (parseTagLines(asMaleTagText.value).length !== asMale.value || parseTagLines(asFemaleTagText.value).length !== asFemale.value) {
      asErr.value = `请填写与数量一致的耳标（公 ${asMale.value} 个、母 ${asFemale.value} 个，每行一个，可点「按序生成」）`;
      return;
    }
  }
  const pen = adjustPen.value;
  if (!pen) return;
  const sign = asIsDecrease.value ? -1 : 1;
  asLoading.value = true;
  try {
    await apiStockChange(pen.pen_id, {
      change_type: asType.value,
      male_delta: asMale.value * sign,
      female_delta: asFemale.value * sign,
      remark: asRemark.value.trim() || undefined,
      ...(asIsDecrease.value
        ? { exit_tag_nos: [...asExitSelected.value] }
        : { male_tags: parseTagLines(asMaleTagText.value), female_tags: parseTagLines(asFemaleTagText.value) }),
    });
    adjustPen.value = null;
    reloadPens();
  } catch (e) {
    asErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    asLoading.value = false;
  }
}

// 离栏个体不足（历史未建档差额）→ 引导先去个体耳标页补齐
const asExitInsufficient = computed(
  () => asIsDecrease.value && (asActiveMaleInds.value.length < asMale.value || asActiveFemaleInds.value.length < asFemale.value),
);

// ===== 转群弹窗（FR-07：目标圈限同场、同物种；EX-B3 前端校验；F14 强关联耳标）=====
const trTargetId = ref<number | ''>('');
const trMale = ref(0);
const trFemale = ref(0);
const trRemark = ref('');
const trErr = ref<string | null>(null);
const trLoading = ref(false);

// F14：源圈在栏个体勾选（转群必须指定个体，两笔流水后迁移归属）
const trInds = ref<IndividualItem[]>([]);
const trSelected = ref<Set<string>>(new Set());

// 同场（当前网格即同场）+ 同物种 + 非自身（EX-B3）
const trCandidates = computed<UserPen[]>(() => {
  const source = transferPen.value;
  if (!source) return [];
  return pens.value.filter((p) => p.pen_id !== source.pen_id && p.species_code === source.species_code);
});
const trActiveMaleInds = computed(() => trInds.value.filter((i) => i.gender === 'male'));
const trActiveFemaleInds = computed(() => trInds.value.filter((i) => i.gender === 'female'));
const trSelMale = computed(
  () => [...trSelected.value].filter((t) => trActiveMaleInds.value.some((i) => i.ear_tag_no === t)).length,
);
const trSelFemale = computed(() => trSelected.value.size - trSelMale.value);
const trSourceTotal = computed(() => {
  const source = transferPen.value;
  return source ? source.male_count + source.female_count : 0;
});
const trTransferInsufficient = computed(
  () => trActiveMaleInds.value.length < trMale.value || trActiveFemaleInds.value.length < trFemale.value,
);
const trSubtitle = computed(() => {
  const source = transferPen.value;
  if (!source) return '';
  return `${source.pen_name}（公 ${source.male_count} / 母 ${source.female_count}）→ 转入同场同物种圈舍`;
});
const trMaxMale = computed(() => transferPen.value?.male_count ?? 0);
const trMaxFemale = computed(() => transferPen.value?.female_count ?? 0);

let trIndsSeq = 0;
watch(
  () => transferPen.value?.pen_id,
  (penId) => {
    const seq = ++trIndsSeq;
    trInds.value = [];
    if (!penId) return;
    apiListIndividuals({ pen_id: penId, status: 'active', page_size: 100 })
      .then((r) => {
        if (seq === trIndsSeq && transferPen.value?.pen_id === penId) trInds.value = r.items;
      })
      .catch(() => {
        if (seq === trIndsSeq && transferPen.value?.pen_id === penId) trInds.value = [];
      });
  },
);

function openTransferDialog(pen: UserPen) {
  transferPen.value = pen;
  trTargetId.value =
    pens.value.find((p) => p.pen_id !== pen.pen_id && p.species_code === pen.species_code)?.pen_id ?? '';
  trMale.value = 0;
  trFemale.value = 0;
  trRemark.value = '';
  trErr.value = null;
  trLoading.value = false;
  trInds.value = [];
  trSelected.value = new Set();
}

function trToggle(tag: string) {
  const next = new Set(trSelected.value);
  if (next.has(tag)) next.delete(tag);
  else next.add(tag);
  trSelected.value = next;
}

async function submitTransfer() {
  trErr.value = null;
  if (!trTargetId.value) {
    trErr.value = '请选择目标圈舍';
    return;
  }
  if (trMale.value === 0 && trFemale.value === 0) {
    trErr.value = '请填写转群数量';
    return;
  }
  const source = transferPen.value;
  if (!source) return;
  if (trMale.value > source.male_count || trFemale.value > source.female_count) {
    trErr.value = '转出数量超过源圈存栏';
    return;
  }
  if (trSelected.value.size !== trMale.value + trFemale.value) {
    trErr.value = `请勾选与数量一致的转群耳标（已选 ${trSelected.value.size}/${trMale.value + trFemale.value}）`;
    return;
  }
  if (trSelMale.value !== trMale.value || trSelFemale.value !== trFemale.value) {
    trErr.value = `转群耳标公母分布与数量不符（需公 ${trMale.value} / 母 ${trFemale.value}）`;
    return;
  }
  trLoading.value = true;
  try {
    await apiTransfer(source.pen_id, {
      target_pen_id: Number(trTargetId.value),
      male_count: trMale.value,
      female_count: trFemale.value,
      remark: trRemark.value.trim() || undefined,
      tag_nos: [...trSelected.value],
    });
    transferPen.value = null;
    reloadPens();
  } catch (e) {
    trErr.value = e instanceof ApiError ? e.message : '网络异常，请重试';
  } finally {
    trLoading.value = false;
  }
}

const penDeleteTotal = computed(() =>
  penDelete.value ? penDelete.value.male_count + penDelete.value.female_count : 0,
);
</script>

<template>
  <div class="px-8 py-6 max-w-[1400px]">
    <div class="flex items-center justify-between mb-6">
      <div>
        <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">场舍管理</h1>
        <p class="text-[14px] text-text-secondary mt-0.5">{{ pageSubtitle }}</p>
      </div>
      <button
        v-if="currentFarm"
        class="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-[10px] text-[14px] font-medium hover:bg-primary-600 transition-colors duration-[var(--duration-fast)]"
        @click="openPenForm('create')"
      >
        <Plus :size="16" :stroke-width="1.5" />
        新建圈舍
      </button>
    </div>

    <div class="flex flex-col lg:flex-row gap-5 items-start">
      <!-- 左侧养殖场列表卡（06 规范 4.3：可增删改） -->
      <div class="w-full lg:w-[280px] shrink-0">
        <div class="flex items-center justify-between mb-2.5 px-1">
          <span class="text-[13px] font-medium text-text-secondary">养殖场（{{ farms.length }}）</span>
          <button
            class="flex items-center gap-1 text-[12.5px] text-primary hover:text-primary-700 font-medium transition-colors"
            @click="openFarmForm('create')"
          >
            <Plus :size="13" :stroke-width="1.5" />
            新建养殖场
          </button>
        </div>

        <div v-if="farmsLoading" class="bg-card rounded-[12px] p-4 card-highlight skeleton-shimmer h-[120px]" />
        <div v-else-if="farmsError" class="bg-card rounded-[12px] p-6 card-highlight text-center">
          <AlertTriangle :size="24" :stroke-width="1.5" class="text-warning mx-auto mb-2.5" />
          <p class="text-[13px] text-text-secondary mb-4">养殖场列表加载失败——不是没有数据，请先重试</p>
          <button
            class="px-4 py-2 bg-primary text-white rounded-[10px] text-[13px] font-medium hover:bg-primary-600 transition-colors"
            @click="reloadFarms"
          >
            重新加载
          </button>
        </div>
        <div v-else-if="farms.length === 0" class="bg-card rounded-[12px] p-6 card-highlight text-center">
          <Warehouse :size="24" :stroke-width="1.5" class="text-primary mx-auto mb-2.5" />
          <p class="text-[13px] text-text-secondary mb-4">还没有养殖场，先创建一个吧</p>
          <button
            class="px-4 py-2 bg-primary text-white rounded-[10px] text-[13px] font-medium hover:bg-primary-600 transition-colors"
            @click="openFarmForm('create')"
          >
            去创建养殖场
          </button>
        </div>
        <div v-else class="flex flex-col gap-2.5">
          <div
            v-for="farm in farms"
            :key="farm.farm_id"
            :class="
              cn(
                'bg-card rounded-[12px] p-4 card-highlight cursor-pointer transition-all duration-[var(--duration-fast)] border',
                currentFarm?.farm_id === farm.farm_id ? 'border-primary e1' : 'border-transparent hover:border-border',
              )
            "
            @click="selectedFarmId = farm.farm_id"
          >
            <div class="flex items-start justify-between gap-2">
              <div class="min-w-0">
                <h3 class="text-[14.5px] font-semibold text-text-primary truncate">{{ farm.farm_name }}</h3>
                <p class="text-[12px] text-text-tertiary mt-0.5 flex items-center gap-1">
                  <MapPin :size="11" :stroke-width="1.5" />
                  {{ farm.location || '未设置地区' }}
                </p>
              </div>
              <div class="flex items-center gap-0.5 shrink-0">
                <button
                  class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle hover:text-text-secondary transition-colors"
                  :aria-label="'编辑 ' + farm.farm_name"
                  @click.stop="openFarmForm(farm)"
                >
                  <Pencil :size="13.5" :stroke-width="1.5" />
                </button>
                <button
                  class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle transition-colors"
                  :aria-label="'删除 ' + farm.farm_name"
                  @click.stop="farmDelete = farm"
                  @mouseenter="dangerEnter"
                  @mouseleave="dangerLeave"
                >
                  <Trash2 :size="13.5" :stroke-width="1.5" />
                </button>
              </div>
            </div>
            <div class="flex items-center gap-3 mt-2.5 text-[12px] text-text-secondary tnum">
              <span>{{ farm.pen_count }} 个圈舍</span>
              <span class="w-px h-3 bg-border" />
              <span>存栏 {{ farm.total_male + farm.total_female }} 头</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 右侧圈舍区域 -->
      <div class="flex-1 w-full min-w-0">
        <!-- 加载中 -->
        <div v-if="farmsLoading || pensLoading" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          <div
            v-for="i in 3"
            :key="i"
            class="bg-card rounded-[12px] p-6 card-highlight skeleton-shimmer h-[280px]"
          />
        </div>

        <!-- 空态：去创建圈舍（06 规范 3.3） -->
        <div
          v-if="!farmsLoading && !pensLoading && currentFarm && pens.length === 0"
          class="bg-card rounded-[12px] p-14 card-highlight flex flex-col items-center text-center"
        >
          <div class="w-16 h-16 rounded-full bg-primary-50 flex items-center justify-center mb-5">
            <Warehouse :size="28" :stroke-width="1.5" class="text-primary" />
          </div>
          <h3 class="text-[16px] font-semibold text-text-primary mb-2">还没有圈舍</h3>
          <p class="text-[13.5px] text-text-secondary mb-6 max-w-[360px] leading-[1.7]">
            创建圈舍后即可开始录入存栏、关联传感器与饲喂计划。 物种将自动继承你的主物种（{{ currentFarmSpeciesName }}）。
          </p>
          <button
            class="flex items-center gap-2 px-5 py-2.5 bg-primary text-white rounded-[10px] text-[14px] font-medium hover:bg-primary-600 transition-colors"
            @click="openPenForm('create')"
          >
            <Plus :size="16" :stroke-width="1.5" />
            去创建圈舍
          </button>
        </div>

        <!-- 圈舍卡片网格（<768 单列，06 规范断点表） -->
        <div v-if="!pensLoading && pens.length > 0" class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
          <div
            v-for="pen in pens"
            :key="pen.pen_id"
            class="bg-card rounded-[12px] p-6 card-highlight hover-lift"
          >
            <!-- 头部 -->
            <div class="flex items-center justify-between mb-4">
              <div class="flex items-center gap-2 min-w-0">
                <Warehouse :size="18" class="text-primary shrink-0" :stroke-width="1.5" />
                <h3 class="text-[16px] font-semibold text-text-primary truncate">{{ pen.pen_name }}</h3>
              </div>
              <div class="flex items-center gap-1.5 shrink-0">
                <span class="px-2 py-0.5 rounded-[6px] bg-primary-50 text-primary-700 text-[12px] font-medium">
                  {{ pen.variety_name }}
                </span>
                <span class="px-2 py-0.5 rounded-[6px] bg-bg-subtle text-text-secondary text-[12px]">
                  {{ stageLabelFor(pen) }}
                </span>
                <!-- 编辑/删除移至头部，给底部主操作留足宽度（窄卡下避免文字竖排） -->
                <div class="w-px h-3.5 bg-border mx-0.5" />
                <button
                  class="p-1.5 rounded-[8px] text-text-tertiary hover:bg-bg-subtle hover:text-text-secondary transition-colors duration-[var(--duration-fast)]"
                  :aria-label="'编辑 ' + pen.pen_name"
                  @click="openPenForm(pen)"
                >
                  <Pencil :size="14" :stroke-width="1.5" />
                </button>
                <button
                  class="p-1.5 rounded-[8px] text-text-tertiary hover:bg-bg-subtle transition-colors duration-[var(--duration-fast)]"
                  :aria-label="'删除 ' + pen.pen_name"
                  @click="penDelete = pen"
                  @mouseenter="dangerEnter"
                  @mouseleave="dangerLeave"
                >
                  <Trash2 :size="14" :stroke-width="1.5" />
                </button>
              </div>
            </div>

            <!-- 存栏大数字 - 公·母·合计并排（600ms 滚动补间 + primary-100 高亮） -->
            <div class="flex items-center justify-center gap-0 py-4 mb-4">
              <div class="text-center flex-1">
                <div class="text-[12px] text-text-tertiary mb-1">公</div>
                <div class="text-[32px] font-semibold leading-[1.1] tnum text-text-primary">
                  <AnimatedNumber :value="pen.male_count" />
                </div>
              </div>
              <div class="w-px h-12 bg-border mx-4" />
              <div class="text-center flex-1">
                <div class="text-[12px] text-text-tertiary mb-1">母</div>
                <div class="text-[32px] font-semibold leading-[1.1] tnum text-text-primary">
                  <AnimatedNumber :value="pen.female_count" />
                </div>
              </div>
              <div class="w-px h-12 bg-border mx-4" />
              <div class="text-center flex-1">
                <div class="text-[12px] text-text-tertiary mb-1">合计</div>
                <div class="text-[32px] font-semibold leading-[1.1] tnum text-primary">
                  <AnimatedNumber :value="pen.male_count + pen.female_count" />
                </div>
              </div>
            </div>

            <div class="text-[12px] text-text-tertiary mb-4 text-center">
              建圈时间 {{ pen.create_time.slice(0, 10) }}
            </div>

            <!-- 快捷按钮（3 个主操作等宽，禁止逐字折行） -->
            <div class="grid grid-cols-3 gap-2">
              <button
                class="flex items-center justify-center gap-1.5 px-2 py-2 rounded-[10px] bg-primary-50 text-primary-700 text-[13px] font-medium hover:bg-primary-100 transition-colors duration-[var(--duration-fast)] whitespace-nowrap"
                @click="openAdjustDialog(pen)"
              >
                <Settings2 :size="14" :stroke-width="1.5" class="shrink-0" />
                调整存栏
              </button>
              <button
                class="flex items-center justify-center gap-1.5 px-2 py-2 rounded-[10px] bg-bg-subtle text-text-secondary text-[13px] font-medium hover:bg-bg-inset transition-colors duration-[var(--duration-fast)] whitespace-nowrap"
                @click="openTransferDialog(pen)"
              >
                <ArrowRightLeft :size="14" :stroke-width="1.5" class="shrink-0" />
                转群
              </button>
              <button
                class="flex items-center justify-center gap-1.5 px-2 py-2 rounded-[10px] bg-bg-subtle text-text-secondary text-[13px] font-medium hover:bg-bg-inset transition-colors duration-[var(--duration-fast)] whitespace-nowrap"
                @click="goSell(pen)"
              >
                <Truck :size="14" :stroke-width="1.5" class="shrink-0" />
                出栏
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ===== 弹窗层 ===== -->
    <Modal
      v-if="farmFormOpen"
      :title="ffIsEdit ? '编辑养殖场' : '新建养殖场'"
      :subtitle="ffSubtitle"
      @close="farmFormOpen = null"
    >
      <label for="ff-name" class="text-[13px] text-text-secondary mb-1.5 block">
        养殖场名称 <span style="color: var(--danger)">*</span>
      </label>
      <input id="ff-name" v-model="ffName" maxlength="30" placeholder="请输入养殖场名称" :class="cn(inputCls, 'mb-4')" />

      <label class="text-[13px] text-text-secondary mb-1.5 block">
        省市区 <span style="color: var(--danger)">*</span>
        <span v-if="ffIsEdit" class="text-text-tertiary">（已设 {{ ffFarm?.location }}，不选则保持不变）</span>
      </label>
      <div class="grid grid-cols-3 gap-2 mb-1.5">
        <select
          aria-label="省份"
          v-model="ffProvCode"
          :class="inputCls"
          @change="ffCityCode = ''; ffCountyCode = ''"
        >
          <option value="">{{ ffIsEdit ? '保持不变' : '选择省份' }}</option>
          <option v-for="p in PROVINCES" :key="p.code" :value="p.code">{{ p.name }}</option>
        </select>
        <select
          aria-label="城市"
          v-model="ffCityCode"
          :disabled="!ffProvince"
          :class="cn(inputCls, !ffProvince && 'opacity-50 cursor-not-allowed')"
          @change="ffCountyCode = ''"
        >
          <option value="">选择城市</option>
          <option v-for="c in ffProvince?.children ?? []" :key="c.code" :value="c.code">{{ c.name }}</option>
        </select>
        <select
          aria-label="区县"
          v-model="ffCountyCode"
          :disabled="!ffCity"
          :class="cn(inputCls, !ffCity && 'opacity-50 cursor-not-allowed')"
        >
          <option value="">选择区县</option>
          <option v-for="d in ffCity?.children ?? []" :key="d.code" :value="d.code">{{ d.name }}</option>
        </select>
      </div>
      <p v-if="ffRegion" class="text-[12px] text-text-tertiary mb-4 tnum">
        行政区划代码（自动带出）：{{ ffRegion.adcode }}
      </p>
      <div class="h-2.5"></div>

      <label for="ff-address" class="text-[13px] text-text-secondary mb-1.5 block">
        详细地址 <span class="text-text-tertiary">（选填）</span>
      </label>
      <input id="ff-address" v-model="ffAddress" maxlength="60" placeholder="如：XX镇XX村" :class="cn(inputCls, 'mb-4')" />

      <div class="grid grid-cols-2 gap-3 mb-4">
        <div>
          <label for="ff-contact" class="text-[13px] text-text-secondary mb-1.5 block">
            联系人 <span class="text-text-tertiary">（选填）</span>
          </label>
          <input id="ff-contact" v-model="ffContact" maxlength="20" :class="inputCls" />
        </div>
        <div>
          <label for="ff-phone" class="text-[13px] text-text-secondary mb-1.5 block">
            电话 <span class="text-text-tertiary">（选填）</span>
          </label>
          <input id="ff-phone" v-model="ffPhone" maxlength="20" :class="inputCls" />
        </div>
      </div>

      <FormError :err="ffErr" />
      <ModalActions
        :loading="ffLoading"
        :submit-text="ffIsEdit ? '保存修改' : '确认创建'"
        @cancel="farmFormOpen = null"
        @submit="submitFarmForm"
      />
    </Modal>

    <ConfirmDialog
      v-if="farmDelete"
      title="删除养殖场"
      danger
      confirm-text="确认删除"
      :on-confirm="handleDeleteFarm"
      @cancel="farmDelete = null"
    >
      确定删除「{{ farmDelete?.farm_name }}」吗？下设圈舍、历史台账将全部保留为档案，但不再显示。
      <span v-if="farms.length === 1" class="block mt-2 text-[12.5px] text-text-tertiary">
        这是你的唯一养殖场，删除后需重新完成新手引导。
      </span>
    </ConfirmDialog>

    <Modal
      v-if="penFormOpen && currentFarm"
      :title="pfIsEdit ? '编辑圈舍' : '新建圈舍'"
      :subtitle="pfSubtitle"
      @close="penFormOpen = null"
    >
      <label for="np-name" class="text-[13px] text-text-secondary mb-1.5 block">
        圈舍名称 <span style="color: var(--danger)">*</span>
        <span v-if="!pfIsEdit" class="text-text-tertiary">（留空自动命名为"{{ pens.length + 1 }}号圈舍"）</span>
      </label>
      <input id="np-name" v-model="pfPenName" maxlength="20" :class="cn(inputCls, 'mb-4')" />

      <label for="np-variety" class="text-[13px] text-text-secondary mb-1.5 block">
        品种 <span style="color: var(--danger)">*</span>
      </label>
      <select id="np-variety" v-model="pfVarietyCode" :class="cn(inputCls, 'mb-4')">
        <option v-for="v in pfVarieties" :key="v.variety_code" :value="v.variety_code">
          {{ v.variety_name }}
        </option>
      </select>

      <template v-if="!pfIsEdit">
        <div class="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label for="np-male" class="text-[13px] text-text-secondary mb-1.5 block">
              公（头） <span class="text-text-tertiary">（初始，选填）</span>
            </label>
            <Stepper id="np-male" :value="pfMale" @change="pfMale = Math.max(0, Math.min(99999, $event))" />
          </div>
          <div>
            <label for="np-female" class="text-[13px] text-text-secondary mb-1.5 block">
              母（头） <span class="text-text-tertiary">（初始，选填）</span>
            </label>
            <Stepper id="np-female" :value="pfFemale" @change="pfFemale = Math.max(0, Math.min(99999, $event))" />
          </div>
        </div>
        <p class="text-[12px] text-text-tertiary mb-4">
          初始存栏将自动记一笔"购入"流水，保证台账可追溯（后续调整请在圈舍卡操作）。
        </p>
      </template>
      <p v-if="pfIsEdit" class="text-[12px] text-text-tertiary mb-4">
        存栏数量仅可通过「调整存栏」变动（保证流水可追溯），此处不可修改。
      </p>

      <label for="np-stage" class="text-[13px] text-text-secondary mb-1.5 block">
        生长阶段 <span class="text-text-tertiary">（选填）</span>
      </label>
      <select id="np-stage" v-model="pfStage" :class="cn(inputCls, 'mb-4')">
        <option value="">暂不确定</option>
        <option v-for="o in pfStageOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
      </select>

      <FormError :err="pfErr" />
      <ModalActions
        :loading="pfLoading"
        :submit-text="pfIsEdit ? '保存修改' : '确认创建'"
        @cancel="penFormOpen = null"
        @submit="submitPenForm"
      />
    </Modal>

    <ConfirmDialog
      v-if="penDelete"
      title="删除圈舍"
      danger
      confirm-text="确认删除"
      :on-confirm="handleDeletePen"
      @cancel="penDelete = null"
    >
      确定删除「{{ penDelete?.pen_name }}」吗？
      <span v-if="penDeleteTotal > 0" class="block mt-2 text-[13px] font-medium" style="color: var(--danger)">
        该圈仍有存栏 {{ penDeleteTotal }} 头，删除后数量记录将不可见（历史流水与台账保留）。
      </span>
    </ConfirmDialog>

    <Modal v-if="adjustPen" title="调整存栏" :subtitle="asSubtitle" @close="adjustPen = null">
      <!-- 类型单选（四项，不含"出栏"——入口占位策略，见 F02 PRD 四节） -->
      <div class="grid grid-cols-4 gap-2 mb-1.5">
        <button
          v-for="t in STOCK_TYPES"
          :key="t.value"
          type="button"
          :class="
            cn(
              'py-2 rounded-[10px] text-[13px] font-medium border transition-all duration-[var(--duration-fast)]',
              asType === t.value
                ? 'bg-primary-50 text-primary-700 border-primary'
                : 'bg-bg-subtle text-text-secondary border-transparent hover:bg-bg-inset',
            )
          "
          @click="asSelectType(t.value)"
        >
          {{ t.label }}
        </button>
      </div>
      <p class="text-[12px] text-text-tertiary mb-4">{{ asTypeDesc }}</p>

      <!-- manual 调整方向 -->
      <div v-if="asType === 'manual'" class="flex items-center gap-2 mb-4">
        <span class="text-[13px] text-text-secondary">方向</span>
        <div class="flex rounded-[10px] overflow-hidden border border-border">
          <button
            v-for="o in MANUAL_DIRECTIONS"
            :key="o.v"
            type="button"
            :class="
              cn(
                'px-3.5 py-1.5 text-[13px] font-medium transition-colors',
                asDirection === o.v ? 'bg-primary text-white' : 'bg-card text-text-secondary hover:bg-bg-subtle',
              )
            "
            @click="asSelectDirection(o.v)"
          >
            {{ o.label }}
          </button>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-4 mb-4">
        <div>
          <label for="sc-male" class="text-[13px] text-text-secondary mb-1.5 block">
            公（{{ asType === 'death' ? '死淘数' : asType === 'manual' ? '变动数' : '数量' }}）
          </label>
          <Stepper id="sc-male" :value="asMale" @change="asMale = Math.max(0, Math.min(99999, $event))" />
        </div>
        <div>
          <label for="sc-female" class="text-[13px] text-text-secondary mb-1.5 block">
            母（{{ asType === 'death' ? '死淘数' : asType === 'manual' ? '变动数' : '数量' }}）
          </label>
          <Stepper id="sc-female" :value="asFemale" @change="asFemale = Math.max(0, Math.min(99999, $event))" />
        </div>
      </div>

      <!-- F14 强关联：入栏录耳标 / 离栏选耳标 -->
      <div v-if="!asIsDecrease && asMale + asFemale > 0" class="mb-4">
        <p class="text-[12.5px] text-text-secondary mb-1.5">
          入栏耳标 <span style="color: var(--danger)">*</span>
          <span class="text-text-tertiary">（每行一个，与数量一致；也可先补齐数量后点「按序生成」）</span>
        </p>
        <div class="grid grid-cols-2 gap-3">
          <div>
            <div class="flex items-center justify-between mb-1">
              <span class="text-[12px] text-text-tertiary">公（{{ parseTagLines(asMaleTagText).length }}/{{ asMale }}）</span>
              <button
                type="button"
                class="text-[12px] text-primary hover:text-primary-700 font-medium transition-colors"
                @click="asAutoFillTags('male')"
              >
                按序生成
              </button>
            </div>
            <textarea
              aria-label="公耳标列表"
              v-model="asMaleTagText"
              rows="4"
              :placeholder="asTagPrefix + '-M001'"
              :class="cn(inputCls, 'h-auto py-2 resize-none font-mono text-[13px] leading-[1.6]')"
            ></textarea>
          </div>
          <div>
            <div class="flex items-center justify-between mb-1">
              <span class="text-[12px] text-text-tertiary">母（{{ parseTagLines(asFemaleTagText).length }}/{{ asFemale }}）</span>
              <button
                type="button"
                class="text-[12px] text-primary hover:text-primary-700 font-medium transition-colors"
                @click="asAutoFillTags('female')"
              >
                按序生成
              </button>
            </div>
            <textarea
              aria-label="母耳标列表"
              v-model="asFemaleTagText"
              rows="4"
              :placeholder="asTagPrefix + '-F001'"
              :class="cn(inputCls, 'h-auto py-2 resize-none font-mono text-[13px] leading-[1.6]')"
            ></textarea>
          </div>
        </div>
      </div>
      <div v-if="asIsDecrease" class="mb-4">
        <p class="text-[12.5px] text-text-secondary mb-1.5">
          离栏耳标 <span style="color: var(--danger)">*</span>
          <span class="text-text-tertiary">（勾选本圈在栏个体，已选 {{ asExitSelected.size }}/{{ asMale + asFemale }}）</span>
        </p>
        <div
          v-if="asExitInsufficient"
          class="px-3.5 py-2.5 rounded-[10px] text-[12.5px]"
          :style="{
            backgroundColor: 'rgba(192,127,45,.08)',
            border: '1px solid rgba(192,127,45,.3)',
            color: 'var(--warning, #c07f2d)',
          }"
        >
          本圈在栏耳标不足（公 {{ asActiveMaleInds.length }}/{{ asMale }} · 母 {{ asActiveFemaleInds.length }}/{{ asFemale }}），
          请先到左侧「个体耳标」页一键补齐后再操作。
        </div>
        <template v-else>
          <TagSelectGroup title="公" :tags="asActiveMaleInds" :selected="asExitSelected" :cap="asMale" @toggle="asToggleExit" />
          <TagSelectGroup title="母" :tags="asActiveFemaleInds" :selected="asExitSelected" :cap="asFemale" @toggle="asToggleExit" />
          <p v-if="asActiveInds.length === 0" class="text-[12px] text-text-tertiary">本圈暂无在栏耳标档案（新入栏时请录入耳标）。</p>
        </template>
      </div>

      <label for="sc-remark" class="text-[13px] text-text-secondary mb-1.5 block">
        备注
        <span v-if="asType === 'manual'" style="color: var(--danger)">*</span>
        <span v-else class="text-text-tertiary">（{{ asType === 'death' ? '建议填写死淘原因' : '选填' }}）</span>
      </label>
      <textarea
        id="sc-remark"
        v-model="asRemark"
        maxlength="100"
        rows="2"
        :placeholder="asRemarkPlaceholder"
        :class="cn(inputCls, 'h-auto py-2 mb-4 resize-none')"
      ></textarea>

      <FormError :err="asErr" />
      <ModalActions :loading="asLoading" submit-text="确认调整" @cancel="adjustPen = null" @submit="submitAdjust" />
    </Modal>

    <Modal v-if="transferPen" title="转群" :subtitle="trSubtitle" @close="transferPen = null">
      <p v-if="trCandidates.length === 0" class="text-[13px] text-text-secondary py-6 text-center">
        当前养殖场内没有其他同物种圈舍，无法转群。
        <br />
        可先新建一个圈舍（物种继承主物种，天然一致）。
      </p>
      <template v-else>
        <label for="tr-target" class="text-[13px] text-text-secondary mb-1.5 block">
          目标圈舍 <span style="color: var(--danger)">*</span>
        </label>
        <select id="tr-target" v-model="trTargetId" :class="cn(inputCls, 'mb-4')">
          <option v-for="p in trCandidates" :key="p.pen_id" :value="p.pen_id">
            {{ p.pen_name }}（公 {{ p.male_count }} / 母 {{ p.female_count }}）
          </option>
        </select>

        <div class="grid grid-cols-2 gap-4 mb-4">
          <div>
            <label for="tr-male" class="text-[13px] text-text-secondary mb-1.5 block">
              转出公（头） <span class="text-text-tertiary">≤ {{ trMaxMale }}</span>
            </label>
            <Stepper id="tr-male" :value="trMale" @change="trMale = Math.max(0, Math.min(trMaxMale, $event))" />
          </div>
          <div>
            <label for="tr-female" class="text-[13px] text-text-secondary mb-1.5 block">
              转出母（头） <span class="text-text-tertiary">≤ {{ trMaxFemale }}</span>
            </label>
            <Stepper id="tr-female" :value="trFemale" @change="trFemale = Math.max(0, Math.min(trMaxFemale, $event))" />
          </div>
        </div>

        <!-- F14 强关联：转群指定个体 -->
        <div class="mb-4">
          <p class="text-[12.5px] text-text-secondary mb-1.5">
            转群耳标 <span style="color: var(--danger)">*</span>
            <span class="text-text-tertiary">（勾选源圈在栏个体，已选 {{ trSelected.size }}/{{ trMale + trFemale }}）</span>
          </p>
          <div
            v-if="trTransferInsufficient"
            class="px-3.5 py-2.5 rounded-[10px] text-[12.5px]"
            :style="{
              backgroundColor: 'rgba(192,127,45,.08)',
              border: '1px solid rgba(192,127,45,.3)',
              color: 'var(--warning, #c07f2d)',
            }"
          >
            源圈在栏耳标不足（公 {{ trActiveMaleInds.length }}/{{ trMale }} · 母 {{ trActiveFemaleInds.length }}/{{ trFemale }}），
            请先到左侧「个体耳标」页一键补齐后再转群。
          </div>
          <template v-else>
            <TagSelectGroup title="公" :tags="trActiveMaleInds" :selected="trSelected" :cap="trMale" @toggle="trToggle" />
            <TagSelectGroup title="母" :tags="trActiveFemaleInds" :selected="trSelected" :cap="trFemale" @toggle="trToggle" />
            <p v-if="trInds.length === 0" class="text-[12px] text-text-tertiary">源圈暂无在栏耳标档案（新入栏时请录入耳标）。</p>
          </template>
        </div>

        <label for="tr-remark" class="text-[13px] text-text-secondary mb-1.5 block">
          备注 <span class="text-text-tertiary">（选填）</span>
        </label>
        <input id="tr-remark" v-model="trRemark" maxlength="60" placeholder="如：断奶转保育" :class="cn(inputCls, 'mb-4')" />

        <p class="text-[12px] text-text-tertiary mb-4">
          转群将在两圈各记一笔联动流水（转出/转入互指），场内总存栏（{{ trSourceTotal }} 头）不变；耳标随个体迁移到目标圈。
        </p>
        <FormError :err="trErr" />
        <ModalActions :loading="trLoading" submit-text="确认转群" @cancel="transferPen = null" @submit="submitTransfer" />
      </template>
    </Modal>

    <!-- 全局 toast -->
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
