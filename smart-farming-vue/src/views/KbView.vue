<script setup lang="ts">
// 知识库页 — F16 书架版（2026-10-15：真后端 /kb/*，各类文件存档）
// - 视觉：暖木书架 + 书脊竖排书名（分类三色：疫病红/营养绿/通用褐），hover 抽书效果
// - 数据：GET /kb/list（farmer 按主物种过滤 + 公共文档，vet/admin 全量）
// - 上传：POST /kb/upload（multipart，20MB 白名单类型；纯文本整理稿亦可）——来源与整理人必填留痕
//   2026-10-23 收敛为 **admin 专属**：kb_document 是全局检索池（无租户列），农户上传的文档会
//   进入他人问诊的候选集并影响风险分级/用药建议，与"知识库是安全护栏"的定位冲突。
// - 详情：正文预览（txt/md/整理稿）或附件下载（pdf/office/图片等二进制）
// - 管理：admin 可停用/删除（停用退出问诊检索池）

import { computed, ref, watch } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { BookOpen, CloudUpload, Download, EyeOff, FileText, Search, Trash2, Upload, X } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { readAuthSnapshot } from '@/api/client';
import { ApiError } from '@/lib/api-contract';
import { useAuthStore } from '@/stores/auth';
import { speciesName } from '@/lib/species-data';
import {
  apiKbBookDelete,
  apiKbBookDetail,
  apiKbBookDownload,
  apiKbBookList,
  apiKbBookToggle,
  apiKbBookUpload,
  type KbBook,
} from '@/api/kb-api';

const inputCls =
  'w-full h-10 px-3 rounded-[10px] bg-card border border-border-strong text-[14px] text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-colors';

const TYPE_META: Record<KbBook['doc_type'], { label: string; color: string; deep: string }> = {
  disease: { label: '疫病诊疗', color: '#A84B42', deep: '#8A3B34' },
  nutrition: { label: '饲养营养', color: '#6E8B3D', deep: '#597231' },
  general: { label: '通用资料', color: '#8A6D3B', deep: '#715830' },
};

const SPECIES_LABEL: Record<string, string> = { pig: '猪', cattle: '牛', sheep: '羊', '': '公共' };

/** 每本书的伪随机形变（同文档恒定）：高度/宽度/微倾角，避免书架过于整齐的塑料感 */
function bookShape(docId: number): { h: number; w: number; lean: number } {
  let seed = docId;
  const next = () => {
    seed = (seed * 9301 + 49297) % 233280;
    return seed / 233280;
  };
  return { h: 150 + Math.round(next() * 44), w: 60 + Math.round(next() * 20), lean: next() > 0.72 ? Math.round(next() * 5) : 0 };
}

const TYPE_TABS = [
  { v: 'all', label: '全部' },
  { v: 'disease', label: '疫病诊疗' },
  { v: 'nutrition', label: '饲养营养' },
  { v: 'general', label: '通用资料' },
] as const;

const role = readAuthSnapshot()?.role ?? 'farmer';
const userId = readAuthSnapshot()?.userId ?? 0;
const auth = useAuthStore();
const mainSpecies = computed(() => auth.user?.main_species_code ?? '');
const queryClient = useQueryClient();
const keyword = ref('');
const typeFilter = ref<'all' | KbBook['doc_type']>('all');
const detailId = ref<number | null>(null);
const uploadOpen = ref(false);
const toast = ref<{ kind: 'ok' | 'err'; text: string } | null>(null);
let toastTimer = 0;

function showToast(kind: 'ok' | 'err', text: string) {
  window.clearTimeout(toastTimer);
  toast.value = { kind, text };
  toastTimer = window.setTimeout(() => (toast.value = null), 2600);
}

const { data, isLoading } = useQuery({ queryKey: computed(() => ['kb-books']), queryFn: () => apiKbBookList() });
const docs = computed(() => data.value?.items ?? []);

const filtered = computed(() =>
  docs.value.filter((d) => {
    if (typeFilter.value !== 'all' && d.doc_type !== typeFilter.value) return false;
    const kw = keyword.value.trim().toLowerCase();
    if (!kw) return true;
    return (
      d.doc_name.toLowerCase().includes(kw)
      || d.retrieval_keywords.some((k) => k.toLowerCase().includes(kw))
      || d.content_md.toLowerCase().includes(kw)
      || d.source.toLowerCase().includes(kw)
    );
  }),
);

// 书架分行：每排 5 本
const rows = computed(() => {
  const groups: KbBook[][] = [];
  for (let i = 0; i < filtered.value.length; i += 5) groups.push(filtered.value.slice(i, i + 5));
  return groups;
});

function metaFor(d: KbBook) {
  return TYPE_META[d.doc_type] ?? TYPE_META.general;
}

function bookStyle(d: KbBook) {
  const shape = bookShape(d.doc_id);
  const meta = metaFor(d);
  return {
    width: `${shape.w}px`,
    height: `${shape.h}px`,
    transform: shape.lean ? `rotate(${shape.lean * 0.6}deg)` : undefined,
    background: `linear-gradient(105deg, ${meta.color} 0%, ${meta.color} 72%, ${meta.deep} 72%, ${meta.deep} 100%)`,
    boxShadow: 'inset 3px 0 6px rgba(0,0,0,.18), inset -1px 0 2px rgba(255,255,255,.12), 2px 3px 5px rgba(0,0,0,.14)',
  };
}

// ===== 详情弹窗（原 KbDetailDialog）=====

const detailDoc = ref<KbBook | null>(null);
const detailErr = ref<string | null>(null);
const detailDownloading = ref(false);

watch(
  detailId,
  (id, _prev, onCleanup) => {
    detailDoc.value = null;
    detailErr.value = null;
    detailDownloading.value = false;
    if (id === null) return;
    let cancelled = false;
    onCleanup(() => {
      cancelled = true;
    });
    apiKbBookDetail(id)
      .then((d) => {
        if (!cancelled) detailDoc.value = d;
      })
      .catch((e) => {
        if (!cancelled) detailErr.value = e instanceof ApiError ? e.message : '加载失败';
      });
  },
  { immediate: true },
);

const detailMeta = computed(() => (detailDoc.value ? TYPE_META[detailDoc.value.doc_type] ?? TYPE_META.general : null));

function handleDetailChanged(kind: 'ok' | 'err', text: string) {
  showToast(kind, text);
  queryClient.invalidateQueries({ queryKey: ['kb-books'] });
}

function downloadLabel(doc: KbBook): string {
  return `下载附件：${doc.file_name ?? ''}（${((doc.file_size ?? 0) / 1024).toFixed(1)} KB）`;
}

async function handleDetailDownload() {
  const doc = detailDoc.value;
  if (!doc?.has_file) return;
  detailDownloading.value = true;
  try {
    await apiKbBookDownload(doc.doc_id, doc.file_name ?? `doc-${doc.doc_id}`);
  } catch {
    handleDetailChanged('err', '附件下载失败');
  } finally {
    detailDownloading.value = false;
  }
}

async function handleDetailToggle() {
  const doc = detailDoc.value;
  if (!doc) return;
  try {
    await apiKbBookToggle(doc.doc_id, !doc.is_enable);
    handleDetailChanged('ok', doc.is_enable ? '已停用（退出问诊检索池）' : '已启用（回到问诊检索池）');
    detailDoc.value = { ...doc, is_enable: !doc.is_enable };
  } catch (e) {
    handleDetailChanged('err', e instanceof ApiError ? e.message : '操作失败');
  }
}

async function handleDetailDelete() {
  const doc = detailDoc.value;
  if (!doc || !window.confirm(`确认删除《${doc.doc_name}》？删除后不可恢复。`)) return;
  try {
    await apiKbBookDelete(doc.doc_id);
    handleDetailChanged('ok', '已删除');
    detailId.value = null;
  } catch (e) {
    handleDetailChanged('err', e instanceof ApiError ? e.message : '操作失败');
  }
}

// ===== 上传弹窗（原 KbUploadDialog）=====

const MAX_FILE_BYTES = 20 * 1024 * 1024;

const file = ref<File | null>(null);
const docName = ref('');
const docType = ref<KbBook['doc_type']>('disease');
const species = ref('');
const keywords = ref('');
const source = ref('');
const organizer = ref('');
const contentMd = ref('');
const uploadDragging = ref(false);
const uploadLoading = ref(false);
const uploadErr = ref<string | null>(null);
const inputRef = ref<HTMLInputElement | null>(null);

function openUpload() {
  file.value = null;
  docName.value = '';
  docType.value = 'disease';
  species.value = '';
  keywords.value = '';
  source.value = '';
  organizer.value = '';
  contentMd.value = '';
  uploadDragging.value = false;
  uploadLoading.value = false;
  uploadErr.value = null;
  uploadOpen.value = true;
}

function pickFile(f: File | null | undefined) {
  if (!f) return;
  if (f.size > MAX_FILE_BYTES) {
    uploadErr.value = '文件大小不能超过 20MB';
    return;
  }
  file.value = f;
  if (!docName.value.trim()) docName.value = f.name.replace(/\.[^.]+$/, '');
  uploadErr.value = null;
}

function openFilePicker() {
  inputRef.value?.click();
}

function removeFile() {
  file.value = null;
  if (inputRef.value) inputRef.value.value = '';
}

function onUploadDrop(e: DragEvent) {
  uploadDragging.value = false;
  pickFile(e.dataTransfer?.files?.[0]);
}

function handleUploaded(name: string) {
  uploadOpen.value = false;
  showToast('ok', `《${name}》已上架`);
  queryClient.invalidateQueries({ queryKey: ['kb-books'] });
}

async function handleSubmit() {
  uploadErr.value = null;
  if (!docName.value.trim()) {
    uploadErr.value = '请填写文档名称';
    return;
  }
  if (!file.value && !contentMd.value.trim()) {
    uploadErr.value = '请选择文件，或直接粘贴整理稿正文';
    return;
  }
  if (!source.value.trim()) {
    uploadErr.value = '来源必填（出处留痕，如书名/规范名/自整资料）';
    return;
  }
  if (!organizer.value.trim()) {
    uploadErr.value = '整理人必填（留痕）';
    return;
  }
  uploadLoading.value = true;
  try {
    const form = new FormData();
    form.set('doc_name', docName.value.trim());
    form.set('doc_type', docType.value);
    form.set('species_code', species.value);
    form.set('retrieval_keywords', keywords.value);
    form.set('risk_level', 'mid');
    form.set('feed_advice', '');
    form.set('source', source.value.trim());
    form.set('organizer', organizer.value.trim());
    if (contentMd.value.trim()) form.set('content_md', contentMd.value.trim());
    if (file.value) form.set('file', file.value);
    const doc = await apiKbBookUpload(form);
    handleUploaded(doc.doc_name);
  } catch (e) {
    uploadErr.value = e instanceof ApiError ? e.message : '上传失败，请重试';
  } finally {
    uploadLoading.value = false;
  }
}
</script>

<template>
  <div class="px-8 py-6 max-w-[1100px]">
    <div class="mb-5 flex items-start justify-between gap-4 flex-wrap">
      <div>
        <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4] flex items-center gap-2 flex-wrap">
          <BookOpen :size="19" :stroke-width="1.5" class="text-primary" />知识库
          <span class="text-[12.5px] font-medium px-2.5 py-1 rounded-full bg-primary-50 text-primary-700">
            {{ mainSpecies ? `${speciesName(mainSpecies)}知识馆` : '公共知识馆' }}
          </span>
        </h1>
        <p class="text-[14px] text-text-secondary mt-0.5">
          {{ mainSpecies ? `按你养殖的物种（${speciesName(mainSpecies)}）自动展示对应知识 + 全物种通用文档 · AI 问诊的检索来源` : '公共养殖知识文档 · AI 问诊的检索来源 · 设置主物种后自动过滤出你需要的知识' }}
        </p>
      </div>
      <!-- 上传收敛到 admin：kb_document 是全局检索池（无租户列），农户上传会进他人问诊候选集 -->
      <button
        v-if="role === 'admin'"
        class="flex items-center gap-2 px-4 py-2.5 rounded-[10px] bg-primary text-white text-[13.5px] font-medium hover:bg-primary-600 transition-colors shrink-0"
        @click="openUpload"
      >
        <Upload :size="15" :stroke-width="1.6" />上传知识文档
      </button>
    </div>

    <div class="flex items-center gap-3 mb-5">
      <div class="relative flex-1 max-w-[360px]">
        <Search :size="15" :stroke-width="1.5" class="absolute left-3 top-1/2 -translate-y-1/2 text-text-tertiary" />
        <input
          v-model="keyword"
          placeholder="搜索文档名、症状关键词或正文"
          :class="cn(inputCls, 'pl-8.5')"
          :style="{ paddingLeft: 32 }"
        />
      </div>
      <div class="flex gap-1 bg-bg-subtle rounded-[10px] p-1">
        <button
          v-for="t in TYPE_TABS"
          :key="t.v"
          :class="
            cn(
              'px-3 py-1.5 rounded-[8px] text-[12.5px] font-medium transition-colors',
              typeFilter === t.v ? 'bg-card text-text-primary' : 'text-text-secondary hover:text-text-primary',
            )
          "
          @click="typeFilter = t.v"
        >
          {{ t.label }}
        </button>
      </div>
      <span class="ml-auto text-[12.5px] text-text-tertiary shrink-0">共 {{ filtered.length }} 本</span>
    </div>

    <div v-if="isLoading" class="py-16 text-center text-[13px] text-text-tertiary">加载中…</div>
    <div v-else-if="filtered.length === 0" class="py-14 text-center">
      <CloudUpload :size="30" :stroke-width="1.2" class="mx-auto text-text-tertiary mb-3" />
      <div class="text-[13.5px] text-text-secondary mb-1">
        {{
          docs.length === 0
            ? role === 'admin'
              ? '书架还是空的——上传第一份知识文档吧'
              : '书架还是空的——等管理员上架与你物种相关的知识文档'
            : '没有符合条件的文档，换个关键词试试'
        }}
      </div>
      <div class="text-[12px] text-text-tertiary">
        支持 pdf / word / txt / 图片 / 表格等常见格式（20MB 内）；txt/md 会自动进 AI 问诊检索池
      </div>
    </div>
    <div v-else class="space-y-7">
      <div v-for="(row, ri) in rows" :key="ri">
        <!-- 书排 -->
        <div class="flex items-end gap-1.5 px-4 overflow-x-auto pb-0.5 min-h-[150px]">
          <button
            v-for="d in row"
            :key="d.doc_id"
            :title="d.doc_name"
            class="relative shrink-0 rounded-t-[6px] rounded-b-[3px] group cursor-pointer transition-transform duration-200 hover:-translate-y-1.5 origin-bottom"
            :style="bookStyle(d)"
            @click="detailId = d.doc_id"
          >
            <!-- 书脊装订线 -->
            <span
              class="absolute left-[7px] top-0 bottom-0 w-[2px] opacity-40"
              :style="{ background: 'repeating-linear-gradient(to bottom, rgba(255,255,255,.55) 0 2px, transparent 2px 9px)' }"
            />
            <!-- 书脊文字（竖排） -->
            <span
              class="absolute inset-0 flex items-center justify-center px-1.5"
              :style="{ writingMode: 'vertical-rl', textOrientation: 'upright' }"
            >
              <span class="text-[12px] font-medium text-white/95 tracking-[2px] leading-none line-clamp-3 overflow-hidden max-h-full py-2">
                {{ d.doc_name.slice(0, 10) }}
              </span>
            </span>
            <!-- 分类色钉 -->
            <span
              class="absolute top-2 right-1.5 w-2 h-2 rounded-full border border-white/40"
              :style="{ background: metaFor(d).deep }"
            />
            <span v-if="!d.is_enable" class="absolute inset-0 rounded-t-[6px] bg-black/45 flex items-center justify-center">
              <EyeOff :size="14" class="text-white/85" :stroke-width="1.6" />
            </span>
          </button>
        </div>
        <!-- 木板 -->
        <div
          class="h-[9px] rounded-[3px]"
          style="
            background: linear-gradient(to bottom, #D8B98F 0%, #C9A06B 55%, #B98F5C 100%);
            box-shadow: 0 3px 5px rgba(120, 80, 40, 0.25), inset 0 1px 0 rgba(255, 255, 255, 0.5);
          "
        />
        <div class="h-2.5" style="background: linear-gradient(to bottom, rgba(150, 110, 70, 0.18), transparent)" />
        <!-- 书名图例（书脊字看不全时的兜底） -->
        <div class="flex flex-wrap gap-x-4 gap-y-1 mt-1.5 px-1">
          <button
            v-for="d in row"
            :key="`t-${d.doc_id}`"
            class="text-[11.5px] text-text-tertiary hover:text-primary transition-colors truncate max-w-[220px]"
            @click="detailId = d.doc_id"
          >
            <span class="inline-block w-1.5 h-1.5 rounded-full mr-1 align-middle" :style="{ background: metaFor(d).color }" />《{{ d.doc_name }}》<span class="text-text-tertiary/80">（{{ SPECIES_LABEL[d.species_code] ?? '公共' }}）</span>
          </button>
        </div>
      </div>
    </div>

    <!-- ===== 详情弹窗 ===== -->
    <div v-if="detailId !== null" class="fixed inset-0 z-50 flex items-center justify-center" @click="detailId = null">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[640px] max-w-[92vw] p-6 animate-modal-in max-h-[86vh] overflow-y-auto"
        style="box-shadow: var(--e3)"
        role="dialog"
        aria-modal="true"
        @click.stop
      >
        <div v-if="detailErr" class="py-10 text-center text-[13.5px] text-text-secondary">
          {{ detailErr }}
          <div class="mt-4">
            <button class="px-4 py-2 rounded-[10px] bg-bg-subtle text-[13px] text-text-secondary hover:bg-primary-50" @click="detailId = null">
              关闭
            </button>
          </div>
        </div>
        <div v-else-if="!detailDoc" class="py-10 text-center text-[13px] text-text-tertiary">加载中…</div>
        <template v-else-if="detailDoc">
          <div class="flex items-start justify-between mb-1.5 gap-3">
            <h2 class="text-[16px] font-semibold text-text-primary leading-snug">{{ detailDoc.doc_name }}</h2>
            <button class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle transition-colors shrink-0" aria-label="关闭" @click="detailId = null">
              <X :size="16" :stroke-width="1.5" />
            </button>
          </div>
          <div class="text-[12px] text-text-tertiary mb-4">
            {{ detailMeta?.label }} · {{ SPECIES_LABEL[detailDoc.species_code] ?? '公共' }} · 来源：{{ detailDoc.source }} · 整理人：{{ detailDoc.organizer }}{{ detailDoc.create_time ? ' · ' + detailDoc.create_time.slice(0, 10) : '' }}
          </div>

          <div v-if="!detailDoc.is_enable" class="mb-4 px-3.5 py-2.5 rounded-[10px] bg-bg-subtle text-[12.5px] text-text-secondary flex items-center gap-2">
            <EyeOff :size="14" :stroke-width="1.5" />本文档已停用，暂不参与 AI 问诊检索
          </div>

          <div v-if="detailDoc.retrieval_keywords.length > 0" class="mb-4 px-3.5 py-3 rounded-[10px] bg-bg-subtle">
            <div class="text-[12.5px] text-text-tertiary mb-1">检索关键词（问诊命中依据）</div>
            <div class="flex flex-wrap gap-1.5">
              <span
                v-for="k in detailDoc.retrieval_keywords"
                :key="k"
                class="px-2 py-0.5 rounded-[6px] bg-card border border-border text-[12px] text-text-secondary"
              >
                {{ k }}
              </span>
            </div>
          </div>

          <div v-if="detailDoc.feed_advice" class="mb-4">
            <div class="text-[13px] font-medium text-text-primary mb-1.5">饲养/饲喂管理建议</div>
            <p class="text-[13px] text-text-secondary leading-relaxed">{{ detailDoc.feed_advice }}</p>
          </div>

          <div v-if="detailDoc.content_md" class="mb-4">
            <div class="text-[13px] font-medium text-text-primary mb-1.5">文档正文</div>
            <pre class="whitespace-pre-wrap text-[13px] text-text-secondary leading-relaxed font-sans bg-bg-subtle rounded-[10px] p-4 max-h-[36vh] overflow-y-auto">{{ detailDoc.content_md }}</pre>
          </div>
          <div v-else-if="detailDoc.has_file" class="text-[12.5px] text-text-tertiary mb-4">（该类型文件暂不支持站内预览，可下载后查看）</div>

          <button
            v-if="detailDoc.has_file"
            :disabled="detailDownloading"
            class="inline-flex items-center gap-2 px-4 py-2 rounded-[10px] border border-border text-[13px] text-text-secondary hover:border-primary hover:text-primary transition-colors disabled:opacity-60"
            @click="handleDetailDownload"
          >
            <Download :size="14" :stroke-width="1.6" />{{ detailDownloading ? '下载中…' : downloadLabel(detailDoc) }}
          </button>

          <p class="text-[11.5px] text-text-tertiary mt-4">
            知识内容仅作辅助参考，不能替代执业兽医现场诊断。
          </p>

          <div v-if="role === 'admin'" class="mt-4 pt-4 border-t border-border flex items-center gap-2">
            <button
              class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-[8px] bg-bg-subtle text-[12.5px] text-text-secondary hover:text-text-primary transition-colors"
              @click="handleDetailToggle"
            >
              <EyeOff :size="13" :stroke-width="1.5" />{{ detailDoc.is_enable ? '停用（退出检索池）' : '启用（回到检索池）' }}
            </button>
            <button
              class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-[8px] text-[12.5px] hover:bg-red-50 transition-colors"
              style="color: var(--danger)"
              @click="handleDetailDelete"
            >
              <Trash2 :size="13" :stroke-width="1.5" />删除
            </button>
          </div>
        </template>
      </div>
    </div>

    <!-- ===== 上传弹窗 ===== -->
    <div v-if="uploadOpen" class="fixed inset-0 z-50 flex items-center justify-center" @click="uploadOpen = false">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[560px] max-w-[92vw] p-6 animate-modal-in max-h-[88vh] overflow-y-auto"
        style="box-shadow: var(--e3)"
        role="dialog"
        aria-modal="true"
        aria-label="上传知识文档"
        @click.stop
      >
        <div class="flex items-start justify-between mb-4">
          <h2 class="text-[16px] font-semibold text-text-primary">上传知识文档</h2>
          <button class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle transition-colors" aria-label="关闭" @click="uploadOpen = false">
            <X :size="16" :stroke-width="1.5" />
          </button>
        </div>

        <!-- 拖拽/选择文件 -->
        <div
          :class="
            cn(
              'mb-4 rounded-[12px] border-2 border-dashed px-4 py-6 text-center cursor-pointer transition-colors',
              uploadDragging ? 'border-primary bg-primary-50' : 'border-border-strong bg-bg-subtle hover:border-primary',
            )
          "
          @click="openFilePicker"
          @dragover.prevent="uploadDragging = true"
          @dragleave="uploadDragging = false"
          @drop.prevent="onUploadDrop"
        >
          <input
            ref="inputRef"
            type="file"
            class="hidden"
            accept=".pdf,.doc,.docx,.txt,.md,.csv,.xls,.xlsx,.ppt,.pptx,.jpg,.jpeg,.png,.webp,.gif,.bmp,.mp4,.mp3,.zip,.rar"
            @change="pickFile(($event.target as HTMLInputElement).files?.[0])"
          />
          <div v-if="file" class="flex items-center justify-center gap-2 text-[13px] text-text-primary">
            <FileText :size="16" :stroke-width="1.5" class="text-primary" />
            <span class="font-medium">{{ file.name }}</span>
            <span class="text-text-tertiary">（{{ (file.size / 1024).toFixed(1) }} KB）</span>
            <span class="text-[12px] text-text-tertiary underline hover:text-primary ml-1" @click.stop="removeFile">移除</span>
          </div>
          <template v-else>
            <CloudUpload :size="22" :stroke-width="1.4" class="mx-auto text-text-tertiary mb-1.5" />
            <div class="text-[13px] text-text-secondary">拖拽文件到此处，或点击选择</div>
            <div class="text-[11.5px] text-text-tertiary mt-0.5">
              pdf / word / txt / md / 图片 / 表格 / 音视频 / 压缩包，20MB 内；txt·md·csv 自动进 AI 问诊检索池
            </div>
          </template>
        </div>

        <div class="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label class="block text-[12.5px] text-text-secondary mb-1">
              文档名称 <span class="text-[var(--danger)]">*</span>
            </label>
            <input v-model="docName" maxlength="60" placeholder="如：育肥羊驱虫计划" :class="inputCls" />
          </div>
          <div>
            <label class="block text-[12.5px] text-text-secondary mb-1">分类</label>
            <select
              :value="docType"
              :class="inputCls"
              @change="docType = ($event.target as HTMLSelectElement).value as KbBook['doc_type']"
            >
              <option value="disease">疫病诊疗</option>
              <option value="nutrition">饲养营养</option>
              <option value="general">通用资料</option>
            </select>
          </div>
          <div>
            <label class="block text-[12.5px] text-text-secondary mb-1">物种归属</label>
            <select :value="species" :class="inputCls" @change="species = ($event.target as HTMLSelectElement).value">
              <option value="">公共（全物种可见）</option>
              <option value="pig">猪</option>
              <option value="cattle">牛</option>
              <option value="sheep">羊</option>
            </select>
          </div>
          <div>
            <label class="block text-[12.5px] text-text-secondary mb-1">检索关键词（逗号分隔，≥2 个）</label>
            <input v-model="keywords" placeholder="如：驱虫, 阿苯达唑" :class="inputCls" />
          </div>
        </div>

        <div class="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label class="block text-[12.5px] text-text-secondary mb-1">
              来源 <span class="text-[var(--danger)]">*</span>
            </label>
            <input v-model="source" maxlength="120" placeholder="出处留痕：书名/规范名/自整资料" :class="inputCls" />
          </div>
          <div>
            <label class="block text-[12.5px] text-text-secondary mb-1">
              整理人 <span class="text-[var(--danger)]">*</span>
            </label>
            <input v-model="organizer" maxlength="30" placeholder="留痕" :class="inputCls" />
          </div>
        </div>

        <div v-if="!file" class="mb-3">
          <label class="block text-[12.5px] text-text-secondary mb-1">
            整理稿正文（未选文件时必填；将进 AI 问诊检索池）
          </label>
          <textarea
            v-model="contentMd"
            rows="4"
            maxlength="20000"
            placeholder="直接粘贴整理好的知识正文……"
            :class="cn(inputCls, 'h-auto py-2.5 resize-y leading-relaxed')"
          />
        </div>

        <div v-if="uploadErr" class="mb-3 px-3 py-2 rounded-[8px] text-[12.5px]" style="background: rgba(217, 83, 79, 0.08); color: var(--danger)">
          {{ uploadErr }}
        </div>

        <div class="flex items-center justify-end gap-2">
          <button class="px-4 py-2 rounded-[10px] bg-bg-subtle text-[13px] text-text-secondary hover:text-text-primary transition-colors" @click="uploadOpen = false">
            取消
          </button>
          <button
            :disabled="uploadLoading"
            class="px-5 py-2 rounded-[10px] bg-primary text-white text-[13.5px] font-medium hover:bg-primary-600 transition-colors disabled:opacity-60 inline-flex items-center gap-1.5"
            @click="handleSubmit"
          >
            <Upload :size="14" :stroke-width="1.6" />{{ uploadLoading ? '上传中…' : '上架' }}
          </button>
        </div>

        <p class="text-[11.5px] text-text-tertiary mt-3">
          上传即代表内容来源合法；停用与删除由管理员操作，整理稿正文将参与 AI 问诊检索（AI 建议仅作辅助，不替代执业兽医）。
        </p>
      </div>
    </div>

    <div
      v-if="toast"
      class="fixed bottom-8 left-1/2 -translate-x-1/2 z-[60] px-5 py-2.5 rounded-[10px] text-[13px] text-white shadow-lg"
      :style="{ background: toast.kind === 'ok' ? 'var(--success, #3E7C4F)' : 'var(--danger)' }"
    >
      {{ toast.text }}
    </div>
  </div>
</template>
