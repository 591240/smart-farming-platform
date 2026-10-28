<script setup lang="ts">
// 个体耳标档案页 — F14 个体耳标档案（06 规范 4.x 表格页）
// - 对账横幅：在栏个体数 vs 圈舍存栏快照，差额圈舍一键补齐（强关联对账收敛入口）
// - 档案表：耳标号/性别/圈舍/品种/出生日期/来源/状态，支持圈舍/状态/关键词筛选
// - 录入耳标弹窗：单个建档（差额头寸内补录）+ 批量建档（连号生成）
// - 编辑/纠错删档：仅档案信息可改；在栏个体可纠错删除，离栏个体为历史档案不可删

import { computed, onBeforeUnmount, ref, watch } from 'vue';
import {
  AlertTriangle,
  CheckCircle2,
  Layers,
  Pencil,
  Plus,
  Search,
  StickyNote,
  Tag,
  Trash2,
  Wand2,
} from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import {
  apiAutoFillIndividuals,
  apiDeleteIndividual,
  apiListIndividuals,
  type IndividualItem,
  type IndividualListResult,
  type IndividualStatus,
  type PenReconcileItem,
} from '@/api/individual-api';
import { ApiError } from '@/lib/api-contract';
import { inputCls } from '@/components/individual/styles';
import StatusBadge from '@/components/individual/StatusBadge.vue';
import CreateIndividualDialog from '@/components/individual/CreateIndividualDialog.vue';
import EditIndividualDialog from '@/components/individual/EditIndividualDialog.vue';
import IndividualDrawer from '@/components/individual/IndividualDrawer.vue';
import ConfirmDialog from '@/components/individual/ConfirmDialog.vue';

const STATUS_TABS: { value: IndividualStatus | 'all'; label: string }[] = [
  { value: 'active', label: '在栏' },
  { value: 'sold', label: '已出栏' },
  { value: 'dead', label: '已死淘' },
  { value: 'removed', label: '已调减' },
  { value: 'all', label: '全部' },
];

const penFilter = ref<number>(0); // 0 = 全部圈舍
const status = ref<IndividualStatus | 'all'>('active');
const keyword = ref('');
const page = ref(1);

// 档案列表（含对账 summary / 逐圈差额）
const data = ref<IndividualListResult | null>(null);
const loading = ref(true);
// 圈舍下拉数据（恒取全部圈口径，不受筛选影响）
const allPens = ref<PenReconcileItem[]>([]);
const toast = ref<string | null>(null);
const fillingPen = ref<number | null>(null);

const createOpen = ref(false);
const editItem = ref<IndividualItem | null>(null);
const deleteItem = ref<IndividualItem | null>(null);
const detailItem = ref<IndividualItem | null>(null); // F17：档案抽屉

let toastTimer: number | null = null;
function showToast(msg: string) {
  toast.value = msg;
  toastTimer = window.setTimeout(() => (toast.value = null), 2600);
}

onBeforeUnmount(() => {
  if (toastTimer) window.clearTimeout(toastTimer);
});

async function load() {
  loading.value = true;
  try {
    const r = await apiListIndividuals({
      pen_id: penFilter.value || undefined,
      status: status.value,
      keyword: keyword.value.trim() || undefined,
      page: page.value,
      page_size: 20,
    });
    data.value = r;
    if (!penFilter.value) allPens.value = r.pens;
  } catch (e) {
    // 失败必须可见：静默 catch 会把"接口挂了"渲染成"这个场没有个体"（写操作早已强提示，读侧漏了）
    showToast(e instanceof ApiError ? e.message : '个体列表加载失败，请重试');
  } finally {
    loading.value = false;
  }
}

watch(
  [penFilter, status, keyword, page],
  () => {
    void load();
  },
  { immediate: true },
);

async function handleAutoFill(penId: number, penName: string) {
  fillingPen.value = penId;
  try {
    const r = await apiAutoFillIndividuals(penId);
    showToast(`「${penName}」${r.message}`);
    await load();
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '补齐失败，请重试');
  } finally {
    fillingPen.value = null;
  }
}

const summary = computed(() => data.value?.summary ?? { stock_total: 0, active_total: 0, diff: 0 });
const diffPens = computed(() => allPens.value.filter((p) => p.diff > 0));
const total = computed(() => data.value?.total ?? 0);
const currentPage = computed(() => data.value?.page ?? 1);
const pageCount = computed(() => (data.value ? Math.ceil(data.value.total / data.value.page_size) : 1));

function handlePenChange(e: Event) {
  penFilter.value = Number((e.target as HTMLSelectElement).value);
  page.value = 1;
}

function handleKeywordInput(e: Event) {
  keyword.value = (e.target as HTMLInputElement).value;
  page.value = 1;
}

function selectStatus(s: IndividualStatus | 'all') {
  status.value = s;
  page.value = 1;
}

function handleDeleteEnter(e: MouseEvent) {
  (e.currentTarget as HTMLElement).style.color = 'var(--danger)';
}

function handleDeleteLeave(e: MouseEvent) {
  (e.currentTarget as HTMLElement).style.color = '';
}

async function handleCreateSaved(msg: string) {
  createOpen.value = false;
  showToast(msg);
  await load();
}

async function handleEditSaved() {
  editItem.value = null;
  showToast('档案已更新');
  await load();
}

async function handleDrawerClose() {
  detailItem.value = null;
  await load();
}

function handleDrawerEdit(item: IndividualItem) {
  detailItem.value = null;
  editItem.value = item;
}

async function handleDeleteConfirm() {
  const item = deleteItem.value;
  if (!item) return;
  try {
    await apiDeleteIndividual(item.individual_id);
    deleteItem.value = null;
    showToast('档案已删除');
    await load();
  } catch (e) {
    deleteItem.value = null;
    showToast(e instanceof ApiError ? e.message : '删除失败，请重试');
  }
}
</script>

<template>
  <div class="px-8 py-6 max-w-[1400px]">
    <div class="flex items-center justify-between mb-5">
      <div>
        <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4]">个体耳标</h1>
        <p class="text-[14px] text-text-secondary mt-0.5">每头动物一个耳标号，精确到个体的档案与流转记录</p>
      </div>
      <button
        class="flex items-center gap-2 px-4 py-2 bg-primary text-white rounded-[10px] text-[14px] font-medium hover:bg-primary-600 transition-colors duration-[var(--duration-fast)]"
        @click="createOpen = true"
      >
        <Plus :size="16" :stroke-width="1.5" />录入耳标
      </button>
    </div>

    <!-- 对账横幅（FR-05 强关联对账） -->
    <div class="bg-card rounded-[12px] p-4 card-highlight mb-4">
      <div class="flex flex-wrap items-center gap-x-6 gap-y-2">
        <div class="flex items-center gap-2">
          <Layers :size="16" :stroke-width="1.5" class="text-primary" />
          <span class="text-[13px] text-text-secondary">对账</span>
        </div>
        <div class="flex items-center gap-4 text-[13px] tnum">
          <span class="text-text-secondary">
            存栏快照 <b class="text-text-primary text-[15px]">{{ summary.stock_total }}</b>
          </span>
          <span class="w-px h-3.5 bg-border" />
          <span class="text-text-secondary">
            在栏耳标 <b class="text-text-primary text-[15px]">{{ summary.active_total }}</b>
          </span>
        </div>
        <span
          v-if="summary.diff === 0"
          class="flex items-center gap-1.5 text-[12.5px] font-medium"
          style="color: var(--success, #3a8f5f)"
        >
          <CheckCircle2 :size="14" :stroke-width="1.5" /> 账实一致
        </span>
        <span
          v-else
          class="flex items-center gap-1.5 text-[12.5px] font-medium"
          style="color: var(--warning, #c07f2d)"
        >
          <AlertTriangle :size="14" :stroke-width="1.5" /> 差额 {{ summary.diff }} 头（历史未建档）
        </span>
      </div>
      <div v-if="diffPens.length > 0" class="flex flex-wrap items-center gap-2 mt-3 pt-3 border-t border-border">
        <span class="text-[12px] text-text-tertiary">待补齐圈舍：</span>
        <button
          v-for="p in diffPens"
          :key="p.pen_id"
          :disabled="fillingPen !== null"
          class="flex items-center gap-1.5 px-2.5 py-1 rounded-[8px] text-[12.5px] border transition-colors disabled:opacity-50"
          style="border-color: rgba(192,127,45,.4); color: var(--warning, #c07f2d); background-color: rgba(192,127,45,.06)"
          @click="handleAutoFill(p.pen_id, p.pen_name)"
        >
          <Wand2 :size="12" :stroke-width="1.5" />{{ p.pen_name }} 差 {{ p.diff }} · 一键补齐
        </button>
      </div>
    </div>

    <!-- 筛选行 -->
    <div class="flex flex-wrap items-center gap-2.5 mb-4">
      <select
        aria-label="圈舍筛选"
        :value="penFilter"
        :class="cn(inputCls, 'w-[180px]')"
        @change="handlePenChange"
      >
        <option :value="0">全部圈舍</option>
        <option v-for="p in allPens" :key="p.pen_id" :value="p.pen_id">{{ p.pen_name }}</option>
      </select>
      <div class="flex rounded-[10px] overflow-hidden border border-border">
        <button
          v-for="t in STATUS_TABS"
          :key="t.value"
          type="button"
          :class="
            cn(
              'px-3 py-1.5 text-[13px] font-medium transition-colors',
              status === t.value ? 'bg-primary text-white' : 'bg-card text-text-secondary hover:bg-bg-subtle',
            )
          "
          @click="selectStatus(t.value)"
        >
          {{ t.label }}
        </button>
      </div>
      <div class="relative flex-1 min-w-[160px] max-w-[280px]">
        <Search
          :size="15"
          class="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary"
          :stroke-width="1.5"
        />
        <input
          :value="keyword"
          placeholder="搜耳标号 / 备注"
          :class="cn(inputCls, 'pl-9')"
          @input="handleKeywordInput"
        />
      </div>
    </div>

    <!-- 档案表 -->
    <div class="bg-card rounded-[12px] card-highlight overflow-hidden">
      <table class="w-full text-[13.5px]">
        <thead>
          <tr class="text-left text-[12.5px] text-text-tertiary border-b border-border">
            <th class="px-5 py-3 font-medium">耳标号</th>
            <th class="px-3 py-3 font-medium">性别</th>
            <th class="px-3 py-3 font-medium">圈舍</th>
            <th class="px-3 py-3 font-medium">品种</th>
            <th class="px-3 py-3 font-medium">出生日期</th>
            <th class="px-3 py-3 font-medium">来源</th>
            <th class="px-3 py-3 font-medium">状态</th>
            <th class="px-3 py-3 font-medium">建档时间</th>
            <th class="px-5 py-3 font-medium text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="loading">
            <td colspan="9" class="px-5 py-10 text-center text-text-tertiary">加载中…</td>
          </tr>
          <tr v-else-if="!data || data.items.length === 0">
            <td colspan="9" class="px-5 py-12 text-center">
              <Tag :size="24" :stroke-width="1.5" class="text-primary mx-auto mb-2.5" />
              <p class="text-[13px] text-text-secondary mb-1">还没有个体耳标档案</p>
              <p class="text-[12px] text-text-tertiary">新入栏动物在「场舍管理 → 调整存栏」时录入耳标；历史存栏可一键补齐</p>
            </td>
          </tr>
          <template v-else>
            <tr
              v-for="it in data.items"
              :key="it.individual_id"
              class="border-b border-border last:border-0 hover:bg-bg-subtle/50 transition-colors cursor-pointer"
              @click="detailItem = it"
            >
              <td class="px-5 py-3 font-medium text-text-primary tnum">{{ it.ear_tag_no }}</td>
              <td class="px-3 py-3 text-text-secondary">{{ it.gender_label }}</td>
              <td class="px-3 py-3 text-text-secondary">{{ it.pen_name }}</td>
              <td class="px-3 py-3 text-text-secondary">{{ it.variety_name ?? '—' }}</td>
              <td class="px-3 py-3 text-text-secondary tnum">{{ it.birth_date ?? '—' }}</td>
              <td class="px-3 py-3 text-text-secondary">{{ it.source }}</td>
              <td class="px-3 py-3">
                <StatusBadge :status="it.status" :label="it.status_label" />
                <p
                  v-if="it.status !== 'active' && it.exit_note"
                  class="text-[11.5px] text-text-tertiary mt-0.5 max-w-[220px] truncate"
                  :title="it.exit_note"
                >
                  {{ it.exit_note }}
                </p>
              </td>
              <td class="px-3 py-3 text-text-tertiary tnum">{{ it.create_time.slice(0, 10) }}</td>
              <td class="px-5 py-3 text-right whitespace-nowrap">
                <button
                  class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle hover:text-text-secondary transition-colors"
                  :aria-label="`查看档案 ${it.ear_tag_no}`"
                  title="查看档案"
                  @click.stop="detailItem = it"
                >
                  <StickyNote :size="14" :stroke-width="1.5" />
                </button>
                <button
                  class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle hover:text-text-secondary transition-colors"
                  :aria-label="`编辑 ${it.ear_tag_no}`"
                  @click.stop="editItem = it"
                >
                  <Pencil :size="14" :stroke-width="1.5" />
                </button>
                <button
                  v-if="it.status === 'active'"
                  class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle transition-colors ml-1"
                  :aria-label="`删除 ${it.ear_tag_no}`"
                  @click.stop="deleteItem = it"
                  @mouseenter="handleDeleteEnter"
                  @mouseleave="handleDeleteLeave"
                >
                  <Trash2 :size="14" :stroke-width="1.5" />
                </button>
              </td>
            </tr>
          </template>
        </tbody>
      </table>
      <div
        v-if="data && data.total > data.page_size"
        class="flex items-center justify-end gap-3 px-5 py-3 border-t border-border text-[13px] text-text-secondary"
      >
        <span class="tnum">共 {{ total }} 条 · 第 {{ currentPage }} / {{ pageCount }} 页</span>
        <button
          :disabled="currentPage <= 1"
          class="px-3 py-1 rounded-[8px] bg-bg-subtle hover:bg-bg-inset transition-colors disabled:opacity-40"
          @click="page = Math.max(1, page - 1)"
        >
          上一页
        </button>
        <button
          :disabled="currentPage >= pageCount"
          class="px-3 py-1 rounded-[8px] bg-bg-subtle hover:bg-bg-inset transition-colors disabled:opacity-40"
          @click="page = Math.min(pageCount, page + 1)"
        >
          下一页
        </button>
      </div>
    </div>

    <!-- 弹窗层 -->
    <CreateIndividualDialog
      v-if="createOpen"
      :pens="allPens"
      :default-pen-id="penFilter || undefined"
      @close="createOpen = false"
      @saved="handleCreateSaved"
    />
    <EditIndividualDialog
      v-if="editItem"
      :item="editItem"
      @close="editItem = null"
      @saved="handleEditSaved"
    />
    <IndividualDrawer
      v-if="detailItem"
      :individual="detailItem"
      @close="handleDrawerClose"
      @edit="handleDrawerEdit"
      @toast="showToast"
    />
    <ConfirmDialog
      v-if="deleteItem"
      title="删除个体档案"
      confirm-text="确认删除"
      :action="handleDeleteConfirm"
      @cancel="deleteItem = null"
    >
      确定删除耳标「{{ deleteItem?.ear_tag_no }}」吗？仅用于纠错（如录错号），删除后可重新录入。
    </ConfirmDialog>

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
