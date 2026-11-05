<script setup lang="ts">
// 台账查询与导出页 — F04 FR-01/02/05（06 规范 4.11，mock 切真）
// - 数据源：GET /record/list（farm/pen/类型/时间筛选 + 分页，时间倒序）
// - 导出：GET /record/export（同筛选全量取数 → CSV 下载 + 成功 toast）
// - 视觉："纸质台账数字化"克制感——无斑马纹、行 hover 才现底色、数字列 tabular 右对齐、
//   类型标签 6px 圆角微标；<768 表格横向滚动（宽表不折行）
// - 台账为只追加档案流（append-only），页面不提供任何编辑/删除入口（FR-04）

import { computed, onBeforeUnmount, ref } from 'vue';
import { useQuery } from '@tanstack/vue-query';
import { ChevronDown, Download, Eraser, NotebookText } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { apiListFarms, apiListPens } from '@/api/auth-api';
import { apiExportRecords, apiListRecords, type LedgerExtColumn, type LedgerRecordItem } from '@/api/record-api';
import { ApiError, RECORD_TYPE_LABEL, RECORD_TYPES } from '@/lib/api-contract';

// 类型微标语义色（03 节标签规范：6px 圆角 + 语义色 8% 底 + 语义色深字；
// 全页强调色克制：存栏=绿、告警类=橙、其余业务类型=中性灰）
const TYPE_TAG: Record<string, { label: string; color: string; bg: string }> = {
  stock_change: { label: '存栏变动', color: 'var(--success)', bg: 'rgba(46,158,107,.08)' },
  sensor: { label: '传感器告警', color: 'var(--warning)', bg: 'rgba(232,162,61,.09)' },
  device_offline: { label: '设备离线', color: 'var(--warning)', bg: 'rgba(232,162,61,.09)' },
  climate: { label: '气象告警', color: 'var(--warning)', bg: 'rgba(232,162,61,.09)' },
  vision: { label: '视觉告警', color: 'var(--warning)', bg: 'rgba(232,162,61,.09)' },
  disease: { label: '疫病告警', color: 'var(--warning)', bg: 'rgba(232,162,61,.09)' },
  medicine: { label: '用药', color: 'var(--text-tertiary)', bg: 'var(--bg-inset)' },
  feed: { label: '饲喂', color: 'var(--text-tertiary)', bg: 'var(--bg-inset)' },
  vet_review: { label: '兽医复核', color: 'var(--text-tertiary)', bg: 'var(--bg-inset)' },
  lifecycle: { label: '生命周期', color: 'var(--text-tertiary)', bg: 'var(--bg-inset)' },
  slaughter: { label: '出栏', color: 'var(--text-tertiary)', bg: 'var(--bg-inset)' },
};

function relTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  if (diff < 60_000) return '刚刚';
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

const SELECT_CLS =
  'h-[36px] px-3 rounded-[10px] border border-border bg-card text-[13.5px] text-text-primary outline-none transition-colors focus:border-primary disabled:opacity-50 disabled:cursor-not-allowed';

// ----- 筛选状态（任一变更即时刷新，FR-05：无需手动点查询）-----
const farmId = ref(0);
const penId = ref(0);
const recordType = ref('');
const from = ref('');
const to = ref('');
const toast = ref<{ kind: 'ok' | 'err'; text: string } | null>(null);
const exporting = ref(false);

// EX-R3 时间范围倒置：前端拦截（服务端另有兜底空集）
const timeInverted = computed(() => from.value !== '' && to.value !== '' && from.value > to.value);

const farmQuery = useQuery({ queryKey: computed(() => ['farms']), queryFn: apiListFarms });
const farms = computed(() => farmQuery.data.value?.items ?? []);
const penQuery = useQuery({
  queryKey: computed(() => ['pens', farmId.value]),
  queryFn: () => apiListPens(farmId.value),
  enabled: computed(() => farmId.value > 0),
});
const pens = computed(() => penQuery.data.value?.items ?? []);

const listQuery = useQuery({
  queryKey: computed(() => [
    'records',
    farmId.value,
    penId.value,
    recordType.value,
    from.value,
    to.value,
  ]),
  queryFn: () =>
    apiListRecords({
      farm_id: farmId.value || undefined,
      pen_id: penId.value || undefined,
      record_type: recordType.value || undefined,
      from: from.value || undefined,
      to: to.value || undefined,
      page: 1,
      page_size: 500, // 按圈舍折叠分组必须整拉（后端上限 500）
    }),
  enabled: computed(() => !timeInverted.value), // 时间倒置时不发请求
});
const records = computed(() => listQuery.data.value?.items ?? []);
const extColumns = computed<LedgerExtColumn[]>(() => listQuery.data.value?.ext_columns ?? []);
const total = computed(() => listQuery.data.value?.total ?? 0);
const isFetching = computed(() => listQuery.isFetching.value);
const isError = computed(() => listQuery.isError.value);
const listError = computed(() => listQuery.error.value);

// 筛选变更统一入口：显式 Partial 增量（farm 变化时联动清空圈舍）
function patchFilter(next: Partial<{ farmId: number; penId: number; recordType: string; from: string; to: string }>) {
  if (next.farmId !== undefined) {
    if (next.farmId !== farmId.value) penId.value = 0; // 换场后原圈舍选择失效
    farmId.value = next.farmId;
  }
  if (next.penId !== undefined) penId.value = next.penId;
  if (next.recordType !== undefined) recordType.value = next.recordType;
  if (next.from !== undefined) from.value = next.from;
  if (next.to !== undefined) to.value = next.to;
}

function onFarmChange(e: Event) {
  patchFilter({ farmId: Number((e.target as HTMLSelectElement).value) });
}
function onPenChange(e: Event) {
  patchFilter({ penId: Number((e.target as HTMLSelectElement).value) });
}
function onTypeChange(e: Event) {
  patchFilter({ recordType: (e.target as HTMLSelectElement).value });
}
function onFromChange(e: Event) {
  patchFilter({ from: (e.target as HTMLInputElement).value });
}
function onToChange(e: Event) {
  patchFilter({ to: (e.target as HTMLInputElement).value });
}

function clearFilters() {
  farmId.value = 0;
  penId.value = 0;
  recordType.value = '';
  from.value = '';
  to.value = '';
}

let toastTimer: number | null = null;
function showToast(kind: 'ok' | 'err', text: string) {
  toast.value = { kind, text };
  toastTimer = window.setTimeout(() => (toast.value = null), 2600);
}
onBeforeUnmount(() => {
  if (toastTimer !== null) window.clearTimeout(toastTimer);
});

// FR-02 导出：loading → 文件下载 + 成功 toast；失败错误提示
async function handleExport() {
  if (exporting.value || timeInverted.value) return;
  exporting.value = true;
  try {
    await apiExportRecords({
      farm_id: farmId.value || undefined,
      pen_id: penId.value || undefined,
      record_type: recordType.value || undefined,
      from: from.value || undefined,
      to: to.value || undefined,
    });
    showToast('ok', '导出成功');
  } catch (e) {
    showToast('err', e instanceof ApiError ? e.message : '导出失败，请重试');
  } finally {
    exporting.value = false;
  }
}

// ===== 按圈舍折叠分组（默认收起，点圈舍行展开明细）=====
interface PenGroup {
  pen_name: string;
  items: LedgerRecordItem[];
  typeSummary: string;
}
const groups = computed<PenGroup[]>(() => {
  const map = new Map<string, PenGroup>();
  for (const r of records.value) {
    let g = map.get(r.pen_name);
    if (!g) {
      g = { pen_name: r.pen_name, items: [], typeSummary: '' };
      map.set(r.pen_name, g);
    }
    g.items.push(r);
  }
  for (const g of map.values()) {
    const counts = new Map<string, number>();
    for (const r of g.items) counts.set(r.record_type, (counts.get(r.record_type) ?? 0) + 1);
    g.typeSummary = [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 3)
      .map(([t, n]) => `${RECORD_TYPE_LABEL[t] ?? t} ${n}`)
      .join(' · ');
  }
  return [...map.values()];
});
const expanded = ref<Record<string, boolean>>({});
function toggleGroup(penName: string) {
  expanded.value[penName] = !expanded.value[penName];
}
const allExpanded = computed(() => groups.value.length > 0 && groups.value.every((g) => expanded.value[g.pen_name]));
function toggleAllGroups() {
  const next = !allExpanded.value;
  const patch: Record<string, boolean> = {};
  for (const g of groups.value) patch[g.pen_name] = next;
  expanded.value = patch;
}
const overCap = computed(() => total.value > 500);

// 类型微标（未知类型回退台账类型字典）
function tagOf(type: string) {
  return (
    TYPE_TAG[type] ?? {
      label: RECORD_TYPE_LABEL[type] ?? type,
      color: 'var(--text-tertiary)',
      bg: 'var(--bg-inset)',
    }
  );
}

// 动态扩展列：数字 tabular 右对齐，无值显示 —（RK-3 历史数据接受缺值）
// 正增量补 + 号（公+6/母-2 直读方向）；非数字值原样展示
function extShown(r: LedgerRecordItem, field: string): string | number {
  const v = r.record_ext?.[field];
  if (v === undefined || v === '') return '—';
  return typeof v === 'number' && v > 0 ? `+${v}` : v;
}
</script>

<template>
  <div class="px-8 py-6 max-w-[1400px]">
    <!-- 标题区 -->
    <div class="mb-5 flex items-start justify-between gap-4 flex-wrap">
      <div>
        <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">台账档案</h1>
        <p class="text-[14px] text-text-secondary mt-0.5">
          养殖过程全量档案 · 只读不可篡改 · 当前筛选共 {{ total.toLocaleString() }} 条
        </p>
      </div>
    </div>

    <!-- 筛选条 + 导出按钮 -->
    <div class="bg-card rounded-[12px] p-4 mb-4 flex items-end gap-3 flex-wrap" style="box-shadow: var(--e1)">
      <div class="flex flex-col gap-1.5">
        <label for="rec-farm" class="text-[12px] text-text-tertiary">养殖场</label>
        <select
          id="rec-farm"
          :value="farmId"
          :class="cn(SELECT_CLS, 'min-w-[150px]')"
          @change="onFarmChange"
        >
          <option :value="0">全部养殖场</option>
          <option v-for="f in farms" :key="f.farm_id" :value="f.farm_id">{{ f.farm_name }}</option>
        </select>
      </div>

      <div class="flex flex-col gap-1.5">
        <label for="rec-pen" class="text-[12px] text-text-tertiary">圈舍</label>
        <select
          id="rec-pen"
          :value="penId"
          :disabled="farmId === 0"
          :class="cn(SELECT_CLS, 'min-w-[140px]')"
          @change="onPenChange"
        >
          <option :value="0">{{ farmId === 0 ? '先选养殖场' : '全部圈舍' }}</option>
          <option v-for="p in pens" :key="p.pen_id" :value="p.pen_id">{{ p.pen_name }}</option>
        </select>
      </div>

      <div class="flex flex-col gap-1.5">
        <label for="rec-type" class="text-[12px] text-text-tertiary">类型</label>
        <select
          id="rec-type"
          :value="recordType"
          :class="cn(SELECT_CLS, 'min-w-[130px]')"
          @change="onTypeChange"
        >
          <option value="">全部类型</option>
          <option v-for="t in RECORD_TYPES" :key="t" :value="t">{{ RECORD_TYPE_LABEL[t] ?? t }}</option>
        </select>
      </div>

      <div class="flex flex-col gap-1.5">
        <label for="rec-from" class="text-[12px] text-text-tertiary">开始日期</label>
        <input
          id="rec-from"
          type="date"
          :value="from"
          :max="to || undefined"
          :class="SELECT_CLS"
          @change="onFromChange"
        />
      </div>

      <div class="flex flex-col gap-1.5">
        <label for="rec-to" class="text-[12px] text-text-tertiary">结束日期</label>
        <input
          id="rec-to"
          type="date"
          :value="to"
          :min="from || undefined"
          :class="SELECT_CLS"
          @change="onToChange"
        />
      </div>

      <button
        class="h-[36px] px-3 rounded-[10px] text-[13px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors flex items-center gap-1.5"
        @click="clearFilters"
      >
        <Eraser :size="14" :stroke-width="1.5" />
        清空筛选
      </button>

      <div class="flex-1" />

      <!-- 导出主按钮（loading 态"正在生成…"） -->
      <button
        :disabled="exporting || timeInverted"
        :class="
          cn(
            'h-[36px] px-4 rounded-[10px] text-[13.5px] font-medium flex items-center gap-1.5 transition-colors',
            'bg-primary text-white hover:bg-primary-600',
            (exporting || timeInverted) && 'opacity-60 cursor-not-allowed',
          )
        "
        @click="handleExport"
      >
        <Download :size="15" :stroke-width="1.5" :class="exporting ? 'animate-pulse' : ''" />
        {{ exporting ? '正在生成…' : '导出 Excel' }}
      </button>
    </div>

    <!-- EX-R3 时间倒置提示（前端拦截） -->
    <div
      v-if="timeInverted"
      class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
      :style="{
        backgroundColor: 'rgba(232,162,61,.08)',
        border: '1px solid rgba(232,162,61,.3)',
        color: 'var(--warning)',
      }"
      role="alert"
    >
      开始时间不能晚于结束时间，请调整时间范围
    </div>

    <!-- 查询失败错误条（06 规范 03 节：danger @ 8% 底 + 图标 + 重试按钮；含 40302 越权等场景） -->
    <div
      v-if="isError && !timeInverted"
      class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px] flex items-center justify-between gap-3"
      :style="{
        backgroundColor: 'rgba(217,83,79,.07)',
        border: '1px solid rgba(217,83,79,.25)',
        color: 'var(--danger)',
      }"
      role="alert"
    >
      <span>{{ listError instanceof ApiError ? listError.message : '台账加载失败，请检查网络后重试' }}</span>
      <button
        class="px-3 py-1 rounded-[8px] text-[12.5px] font-medium shrink-0 transition-colors hover:opacity-80"
        style="background-color: rgba(217,83,79,.12); color: var(--danger)"
        @click="listQuery.refetch()"
      >
        重试
      </button>
    </div>

    <!-- 台账表格（<768 横向滚动，宽表不折行） -->
    <div class="relative bg-card rounded-[12px] overflow-hidden" style="box-shadow: var(--e1)">
      <!-- 表格加载条（03 节：primary-500 顶部 2px 进度条） -->
      <div v-if="isFetching" class="absolute top-0 left-0 right-0 h-[2px] bg-primary overflow-hidden z-10">
        <div class="h-full w-1/3 bg-primary-600 table-loadingbar" />
      </div>

      <!-- 圈舍折叠工具条 -->
      <div class="flex items-center justify-between px-5 py-2.5 border-b border-border">
        <span class="text-[12.5px] text-text-tertiary">
          共 {{ total.toLocaleString() }} 条 · {{ groups.length }} 个圈舍 · 点击圈舍行展开明细
        </span>
        <button
          v-if="groups.length > 0"
          class="px-3 py-1 rounded-[8px] text-[12.5px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
          @click="toggleAllGroups"
        >
          {{ allExpanded ? '全部收起' : '全部展开' }}
        </button>
      </div>

      <div class="overflow-x-auto">
        <table class="w-full min-w-[640px] text-[13.5px]">
          <thead>
            <tr class="text-left text-[12.5px] text-text-tertiary border-b border-border">
              <th class="font-medium px-5 py-3 whitespace-nowrap">时间</th>
              <th class="font-medium px-5 py-3 whitespace-nowrap">圈舍</th>
              <th class="font-medium px-5 py-3 whitespace-nowrap">类型</th>
              <th class="font-medium px-5 py-3 whitespace-nowrap">摘要</th>
              <th v-for="col in extColumns" :key="col.field" class="font-medium px-5 py-3 text-right whitespace-nowrap">
                {{ col.label }}
              </th>
            </tr>
          </thead>
          <tbody>
            <!-- 无斑马纹，行 hover 才现底色（06 规范 4.11 克制感） -->
            <template v-for="g in groups" :key="g.pen_name">
              <!-- 圈舍分组头行：点击展开/收起 -->
              <tr
                class="bg-bg-subtle/60 border-b border-border cursor-pointer select-none hover:bg-bg-subtle transition-colors"
                :aria-expanded="!!expanded[g.pen_name]"
                @click="toggleGroup(g.pen_name)"
              >
                <td :colspan="4 + extColumns.length" class="px-5 py-2.5">
                  <div class="flex items-center gap-2">
                    <ChevronDown
                      :size="15"
                      :stroke-width="1.5"
                      :class="cn('text-text-tertiary transition-transform', expanded[g.pen_name] && 'rotate-180')"
                    />
                    <span class="text-[13.5px] font-medium text-text-primary">{{ g.pen_name }}</span>
                    <span class="text-[12.5px] text-text-tertiary tnum">{{ g.items.length }} 条</span>
                    <span class="text-[12px] text-text-tertiary">{{ g.typeSummary }}</span>
                  </div>
                </td>
              </tr>
              <template v-if="expanded[g.pen_name]">
                <tr
                  v-for="r in g.items"
                  :key="r.record_id"
                  class="border-b border-border last:border-b-0 hover:bg-bg-subtle transition-colors"
                >
                  <td class="pl-10 pr-5 py-3 text-text-secondary whitespace-nowrap" :title="r.create_time">
                    {{ relTime(r.create_time) }}
                  </td>
                  <td class="px-5 py-3 text-text-primary whitespace-nowrap">{{ r.pen_name }}</td>
                  <td class="px-5 py-3 whitespace-nowrap">
                    <span
                      class="px-1.5 py-0.5 rounded-[6px] text-[11.5px] font-medium whitespace-nowrap"
                      :style="{ backgroundColor: tagOf(r.record_type).bg, color: tagOf(r.record_type).color }"
                    >
                      {{ tagOf(r.record_type).label }}
                    </span>
                  </td>
                  <td class="px-5 py-3 text-text-secondary min-w-[240px]">{{ r.summary }}</td>
                  <td
                    v-for="col in extColumns"
                    :key="col.field"
                    class="px-5 py-3 text-right whitespace-nowrap"
                    :style="{ fontVariantNumeric: 'tabular-nums', color: 'var(--text-secondary)' }"
                  >
                    {{ extShown(r, col.field) }}
                  </td>
                </tr>
              </template>
            </template>
          </tbody>
        </table>

        <!-- 空态（EX-R4；查询失败时由上方错误条承接，不叠加空态误导） -->
        <div v-if="!isFetching && !isError && !timeInverted && records.length === 0" class="text-center py-16">
          <NotebookText :size="40" class="mx-auto mb-3" :stroke-width="1.5" :style="{ color: 'var(--text-disabled)' }" />
          <p class="text-[14px]" style="color: var(--text-tertiary)">该条件下暂无台账记录</p>
          <p class="text-[12.5px] mt-1" style="color: var(--text-disabled)">
            台账由存栏变动、告警产生与处理等业务动作自动写入
          </p>
        </div>
      </div>

      <div v-if="overCap" class="flex items-center px-5 py-3 border-t border-border text-[12.5px] text-text-tertiary">
        当前筛选下超过 500 条仅显示前 500 条——请用上方筛选缩小范围，或用「导出 Excel」拿全量
      </div>
    </div>

    <!-- toast（03 节：底部滑入） -->
    <div
      v-if="toast"
      class="fixed bottom-8 left-1/2 -translate-x-1/2 z-[60] px-4 py-2.5 rounded-[10px] text-white text-[13px] animate-slide-in"
      :style="{
        backgroundColor: toast.kind === 'ok' ? '#2A332C' : 'var(--danger)',
        boxShadow: 'var(--e3)',
      }"
      role="status"
    >
      {{ toast.text }}
    </div>
  </div>
</template>
