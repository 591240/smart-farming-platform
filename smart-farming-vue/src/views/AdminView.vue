<script setup lang="ts">
// 后台管理页 — F10 FR-05（06 规范 4.12）+ F16 知识库文档管理
// - Tab：用户 / 知识库文档（F16 提前交付）；物种/兽药库/饲料库配置仍规划于阶段 12
// - 待办徽标（决策 5 / RK-3）：GET /vet/pending-cases 只读放宽（admin 计数，无复核权），
//   ≥24h 未复核 N 例常驻头部，点击展开超时病例只读面板（不跳兽医页——授权面最小化，V1.0 形态本档具体化）
// - 用户管理：创建兽医/管理员（不开放注册，主 PRD 2.2）、禁用/启用即时生效（登录+请求双点拦截）；
//   不提供删除（复核溯源审计要求账号不物理删除）；手机号脱敏展示。
// - 知识库管理（F16）：上传必填来源与整理人（PRD 3.5.1 / 附录 E SOP 留痕），
//   上传即触发切片重建索引；停用即退出问诊检索池（不删档可恢复）；删除为软删除。

import { computed, ref } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { BookOpen, Plus, ShieldCheck, Trash2, Upload, UserPlus, X } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import {
  apiAdminCreateUser,
  apiAdminListUsers,
  apiAdminSetUserEnable,
  apiVetPendingCases,
  type AdminUserItem,
} from '@/api/vet-api';
import {
  apiAdminKbDelete,
  apiAdminKbList,
  apiAdminKbSetEnable,
  apiAdminKbUpload,
  type KbDocItem,
  type KbDocType,
} from '@/api/kb-api';
import { apiListMedicineLib } from '@/api/breeding-api';

const inputCls =
  'w-full h-10 px-3 rounded-[10px] bg-card border border-border-strong text-[14px] text-text-primary outline-none focus:border-primary focus:ring-2 focus:ring-primary/20 transition-colors';

const ROLE_LABEL: Record<AdminUserItem['role'], { label: string; color: string }> = {
  farmer: { label: '养殖户', color: 'var(--text-secondary)' },
  vet: { label: '执业兽医', color: 'var(--primary)' },
  admin: { label: '管理员', color: 'var(--warning)' },
};

// ===== F16 知识库文档面板（02-数据库 3.7 / 03-API 10.1；admin 专属）=====
const KB_SPECIES_LABEL: Record<KbDocItem['species_code'], string> = {
  pig: '猪',
  cattle: '牛',
  sheep: '羊',
  '': '公共（全物种）',
};

const TABS = [
  { v: 'users', label: '用户' },
  { v: 'kb', label: '知识库文档' },
] as const;

const DOC_TYPE_OPTIONS = [
  { v: 'disease', label: '疫病诊疗', desc: '症状关键词 → 问诊诊断候选' },
  { v: 'nutrition', label: '饲养营养', desc: '命中时补充进饲料建议' },
] as const;

// ===== 主页面 =====
const queryClient = useQueryClient();
const tab = ref<'users' | 'kb'>('users');
const createOpen = ref(false);
const kbUploadOpen = ref(false);
const toast = ref<string | null>(null);
const showOverdue = ref(false);

const { data: userData } = useQuery({
  queryKey: computed(() => ['admin-users']),
  queryFn: apiAdminListUsers,
});
const users = computed(() => userData.value?.items ?? []);

// 待办徽标：pending 列表（admin 只读放宽）——warn 计数
const { data: pendingData } = useQuery({
  queryKey: computed(() => ['vet-pending-admin']),
  queryFn: apiVetPendingCases,
  refetchInterval: 30_000,
});
const pendingItems = computed(() => pendingData.value?.items ?? []);
const warnCases = computed(() => pendingItems.value.filter((p) => p.timeout_level === 'warn'));

function showToast(msg: string) {
  toast.value = msg;
  window.setTimeout(() => (toast.value = null), 2800);
}

async function toggleEnable(u: AdminUserItem) {
  try {
    await apiAdminSetUserEnable(u.user_id, !u.is_enable);
    showToast(u.is_enable ? `已禁用「${u.nickname}」，该账号登录与请求即时失效` : `已启用「${u.nickname}」`);
    queryClient.invalidateQueries({ queryKey: ['admin-users'] });
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '操作失败');
  }
}

// ===== F16 知识库文档面板 =====
const { data: kbData } = useQuery({
  queryKey: computed(() => ['admin-kb']),
  queryFn: apiAdminKbList,
  enabled: computed(() => tab.value === 'kb'),
});
const kbDocs = computed(() => kbData.value?.items ?? []);

async function toggleKbEnable(d: KbDocItem) {
  try {
    await apiAdminKbSetEnable(d.doc_id, !d.is_enable);
    showToast(d.is_enable ? `已停用《${d.doc_name}》，问诊检索池即时退出（可恢复）` : `已启用《${d.doc_name}》，已重新入池并刷新索引`);
    queryClient.invalidateQueries({ queryKey: ['admin-kb'] });
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '操作失败');
  }
}

async function removeKb(d: KbDocItem) {
  if (!window.confirm(`确认删除《${d.doc_name}》？删除后退出检索池且不可在列表恢复（软删除）。`)) return;
  try {
    await apiAdminKbDelete(d.doc_id);
    showToast(`已删除《${d.doc_name}》`);
    queryClient.invalidateQueries({ queryKey: ['admin-kb'] });
  } catch (e) {
    showToast(e instanceof ApiError ? e.message : '删除失败');
  }
}

// ===== F16 上传知识文档弹窗（来源/整理人必填留痕；disease 型推荐药须库内非违禁）=====
const kbDocType = ref<KbDocType>('disease');
const kbDocName = ref('');
const kbSpecies = ref<KbDocItem['species_code']>('pig');
const kbKeywords = ref('');
const kbRiskLevel = ref<'low' | 'mid' | 'high'>('mid');
const kbMedNames = ref<string[]>([]);
const kbFeedAdvice = ref('');
const kbSource = ref('');
const kbOrganizer = ref('');
const kbContentMd = ref('');
const kbErr = ref<string | null>(null);
const kbLoading = ref(false);

const { data: medData } = useQuery({
  queryKey: computed(() => ['medicine-lib']),
  queryFn: apiListMedicineLib,
  enabled: computed(() => kbUploadOpen.value),
});
const medOptions = computed(() => (medData.value?.items ?? []).filter((m) => !kbSpecies.value || m.species_code === kbSpecies.value));

function openKbUpload() {
  kbDocType.value = 'disease';
  kbDocName.value = '';
  kbSpecies.value = 'pig';
  kbKeywords.value = '';
  kbRiskLevel.value = 'mid';
  kbMedNames.value = [];
  kbFeedAdvice.value = '';
  kbSource.value = '';
  kbOrganizer.value = '';
  kbContentMd.value = '';
  kbErr.value = null;
  kbLoading.value = false;
  kbUploadOpen.value = true;
}

function toggleMed(name: string) {
  kbMedNames.value = kbMedNames.value.includes(name)
    ? kbMedNames.value.filter((m) => m !== name)
    : [...kbMedNames.value, name];
}

function onKbSpeciesChange(e: Event) {
  kbSpecies.value = (e.target as HTMLSelectElement).value as KbDocItem['species_code'];
  kbMedNames.value = [];
}

function onKbRiskChange(e: Event) {
  kbRiskLevel.value = (e.target as HTMLSelectElement).value as 'low' | 'mid' | 'high';
}

function handleKbUploaded(msg: string) {
  kbUploadOpen.value = false;
  showToast(msg);
  queryClient.invalidateQueries({ queryKey: ['admin-kb'] });
}

async function submitKbUpload() {
  const docName = kbDocName.value.trim();
  const source = kbSource.value.trim();
  const organizer = kbOrganizer.value.trim();
  const docType = kbDocType.value;
  const riskLevel = kbRiskLevel.value;
  const medNames = kbMedNames.value;
  if (!docName || !source || !organizer) {
    kbErr.value = '请填写文档名称、知识来源与整理人（来源与整理人为必填留痕项）';
    return;
  }
  const kwList = kbKeywords.value.split(/[,，、;；]/).map((k) => k.trim()).filter(Boolean);
  if (kwList.length < 2) {
    kbErr.value = '请至少填写 2 个症状/主题关键词（AI 问诊检索的计分依据）';
    return;
  }
  kbLoading.value = true;
  kbErr.value = null;
  try {
    const r = await apiAdminKbUpload({
      doc_name: docName,
      doc_type: docType,
      species_code: kbSpecies.value,
      retrieval_keywords: kwList,
      ...(docType === 'disease' ? { risk_level: riskLevel, med_names: medNames } : {}),
      feed_advice: kbFeedAdvice.value.trim() || undefined,
      content_md: kbContentMd.value.trim() || undefined,
      source,
      organizer,
    });
    handleKbUploaded(`《${docName}》已上传并完成索引（${r.chunk_count} 片），问诊检索池即时生效`);
  } catch (e) {
    kbErr.value = e instanceof ApiError ? e.message : '上传失败，请稍后重试';
  } finally {
    kbLoading.value = false;
  }
}

// ===== 新增用户弹窗（角色限 vet/admin：farmer 走注册链路；初始密码不回显不传输列表，提交后丢弃）=====
const cuPhone = ref('');
const cuNickname = ref('');
const cuRole = ref<'vet' | 'admin'>('vet');
const cuPassword = ref('');
const cuErr = ref<string | null>(null);
const cuLoading = ref(false);
const ROLE_OPTIONS = ['vet', 'admin'] as const;

function openCreate() {
  cuPhone.value = '';
  cuNickname.value = '';
  cuRole.value = 'vet';
  cuPassword.value = '';
  cuErr.value = null;
  cuLoading.value = false;
  createOpen.value = true;
}

async function submitCreateUser() {
  const phone = cuPhone.value.trim();
  const nickname = cuNickname.value.trim();
  const role = cuRole.value;
  const password = cuPassword.value;
  if (!phone || !password) {
    cuErr.value = '请填写手机号与初始密码';
    return;
  }
  cuLoading.value = true;
  cuErr.value = null;
  try {
    const u = await apiAdminCreateUser({ phone, nickname: nickname || undefined, role, initial_password: password });
    createOpen.value = false;
    showToast(`已创建${role === 'vet' ? '兽医' : '管理员'}账号「${u.nickname}」，可交付使用`);
    queryClient.invalidateQueries({ queryKey: ['admin-users'] });
  } catch (e) {
    cuErr.value = e instanceof ApiError ? e.message : '创建失败，请稍后重试';
  } finally {
    cuLoading.value = false;
  }
}
</script>

<template>
  <div class="px-8 py-6 max-w-[1100px]">
    <div class="mb-6">
      <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4] flex items-center gap-2">
        <ShieldCheck :size="19" :stroke-width="1.5" class="text-primary" />后台管理
      </h1>
      <p class="text-[14px] text-text-secondary mt-0.5">
        用户管理 · 知识库文档管理 · 物种/兽药库/饲料库配置规划于阶段 12
      </p>
    </div>

    <!-- 一级 Tab：用户 / 知识库文档（F16） -->
    <div class="flex gap-1 mb-4 bg-bg-subtle rounded-[10px] p-1 w-fit">
      <button
        v-for="t in TABS"
        :key="t.v"
        :class="
          cn(
            'px-4 py-1.5 rounded-[8px] text-[13.5px] font-medium transition-colors',
            tab === t.v ? 'bg-card text-text-primary' : 'text-text-secondary hover:text-text-primary',
          )
        "
        @click="tab = t.v"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- 超时待办徽标（RK-3：常驻，不依赖用户主动进列表） -->
    <button
      v-if="warnCases.length > 0"
      class="mb-4 w-full text-left flex items-center gap-3 px-4 py-3 rounded-[12px] border-l-4 transition-colors hover:opacity-90"
      :style="{ backgroundColor: 'rgba(217,83,79,0.07)', borderLeftColor: 'var(--danger)' }"
      @click="showOverdue = !showOverdue"
    >
      <span class="w-2.5 h-2.5 rounded-full shrink-0 animate-breathe" :style="{ backgroundColor: 'var(--danger)' }" />
      <span class="text-[14px] text-text-primary flex-1">
        <span class="font-medium">{{ warnCases.length }}</span> 个病例已超过 24 小时未复核，请督促兽医处理
      </span>
      <span class="text-[13px] text-primary font-medium">{{ showOverdue ? '收起 ∧' : '查看 ∨' }}</span>
    </button>
    <div v-if="showOverdue && warnCases.length > 0" class="mb-4 bg-card rounded-[12px] card-highlight divide-y divide-border">
      <div v-for="c in warnCases" :key="c.review_id" class="flex items-center gap-3 px-4 py-3">
        <span class="px-1.5 py-0.5 rounded-[4px] text-[11px] font-medium shrink-0" style="background-color: rgba(217, 83, 79, 0.06); color: var(--danger)">
          等待 {{ c.waiting_hours }}h
        </span>
        <span class="text-[13px] text-text-primary shrink-0">{{ c.farm_name }} · {{ c.pen_name }}</span>
        <span class="text-[12.5px] text-text-secondary truncate flex-1">{{ c.original_diagnosis }}</span>
      </div>
      <div class="px-4 py-2.5 text-[11.5px] text-text-tertiary">只读督办视图（管理员无复核权限，复核由执业兽医在「待复核病例」完成）</div>
    </div>

    <!-- 用户 Tab -->
    <div v-if="tab === 'users'" class="bg-card rounded-[12px] card-highlight">
      <div class="flex items-center justify-between px-5 py-4 border-b border-border">
        <div class="flex items-center gap-2">
          <span class="text-[14px] font-semibold text-text-primary">用户</span>
          <span class="text-[12px] text-text-tertiary">共 {{ users.length }} 个账号（禁用不删档，复核记录溯源保留）</span>
        </div>
        <button
          class="flex items-center gap-2 px-3.5 py-2 bg-primary text-white rounded-[10px] text-[13.5px] font-medium hover:bg-primary-600 transition-colors"
          @click="openCreate"
        >
          <Plus :size="15" :stroke-width="1.5" />新增用户
        </button>
      </div>
      <table class="w-full text-[13.5px]">
        <thead>
          <tr class="text-left text-[12px] text-text-tertiary border-b border-border">
            <th class="px-5 py-2.5 font-normal">昵称</th>
            <th class="px-3 py-2.5 font-normal">手机号（脱敏）</th>
            <th class="px-3 py-2.5 font-normal">角色</th>
            <th class="px-3 py-2.5 font-normal">状态</th>
            <th class="px-3 py-2.5 font-normal">创建时间</th>
            <th class="px-5 py-2.5 font-normal text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="u in users"
            :key="u.user_id"
            class="border-b border-border/60 last:border-0 hover:bg-bg-subtle/60 transition-colors"
          >
            <td class="px-5 py-3 text-text-primary font-medium">{{ u.nickname }}</td>
            <td class="px-3 py-3 text-text-secondary tnum">{{ u.phone }}</td>
            <td class="px-3 py-3" :style="{ color: ROLE_LABEL[u.role]?.color }">{{ ROLE_LABEL[u.role]?.label ?? u.role }}</td>
            <td class="px-3 py-3">
              <span v-if="u.is_enable" class="inline-flex items-center gap-1.5 text-[12.5px]" style="color: var(--success)">
                <span class="w-1.5 h-1.5 rounded-full" style="background-color: var(--success)" />启用
              </span>
              <span v-else class="inline-flex items-center gap-1.5 text-[12.5px]" style="color: var(--text-tertiary)">
                <span class="w-1.5 h-1.5 rounded-full bg-text-disabled" />已禁用
              </span>
            </td>
            <td class="px-3 py-3 text-text-tertiary tnum">{{ u.create_time.slice(0, 10) }}</td>
            <td class="px-5 py-3 text-right">
              <button
                :class="
                  cn(
                    'px-3 py-1.5 rounded-[8px] text-[12.5px] transition-colors',
                    u.is_enable ? 'text-text-secondary bg-bg-subtle hover:bg-bg-inset' : 'text-primary-700 bg-primary-50 hover:bg-primary-100',
                  )
                "
                @click="toggleEnable(u)"
              >
                {{ u.is_enable ? '禁用' : '启用' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 知识库文档 Tab（F16 面板） -->
    <div v-if="tab === 'kb'" class="bg-card rounded-[12px] card-highlight">
      <div class="flex items-center justify-between px-5 py-4 border-b border-border">
        <div class="flex items-center gap-2">
          <BookOpen :size="16" :stroke-width="1.5" class="text-primary" />
          <span class="text-[14px] font-semibold text-text-primary">知识库文档</span>
          <span class="text-[12px] text-text-tertiary">
            共 {{ kbDocs.length }} 篇 · 启用中的文档参与 AI 问诊检索；上传必填来源与整理人（知识留痕规范）
          </span>
        </div>
        <button
          class="flex items-center gap-2 px-3.5 py-2 bg-primary text-white rounded-[10px] text-[13.5px] font-medium hover:bg-primary-600 transition-colors"
          @click="openKbUpload"
        >
          <Upload :size="15" :stroke-width="1.5" />上传文档
        </button>
      </div>
      <div v-if="kbDocs.length === 0" class="px-5 py-10 text-center text-[13px] text-text-tertiary">
        暂无上传文档。内置猪病知识库仍参与问诊；上传后的文档会以「本场知识库」身份进入检索池。
      </div>
      <table v-else class="w-full text-[13.5px]">
        <thead>
          <tr class="text-left text-[12px] text-text-tertiary border-b border-border">
            <th class="px-5 py-2.5 font-normal">文档名称</th>
            <th class="px-3 py-2.5 font-normal">类型</th>
            <th class="px-3 py-2.5 font-normal">物种</th>
            <th class="px-3 py-2.5 font-normal">检索关键词</th>
            <th class="px-3 py-2.5 font-normal">来源 / 整理人</th>
            <th class="px-3 py-2.5 font-normal">切片</th>
            <th class="px-3 py-2.5 font-normal">状态</th>
            <th class="px-5 py-2.5 font-normal text-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="d in kbDocs"
            :key="d.doc_id"
            class="border-b border-border/60 last:border-0 hover:bg-bg-subtle/60 transition-colors"
          >
            <td class="px-5 py-3 text-text-primary font-medium max-w-[200px] truncate" :title="d.doc_name">{{ d.doc_name }}</td>
            <td class="px-3 py-3 text-text-secondary">{{ d.doc_type === 'disease' ? '疫病诊疗' : '饲养营养' }}</td>
            <td class="px-3 py-3 text-text-secondary">{{ KB_SPECIES_LABEL[d.species_code] }}</td>
            <td class="px-3 py-3 text-text-secondary max-w-[160px] truncate" :title="d.retrieval_keywords.join('、')">{{ d.retrieval_keywords.join('、') }}</td>
            <td class="px-3 py-3 text-text-tertiary max-w-[180px] truncate" :title="d.source + ' · ' + d.organizer">{{ d.source }} · {{ d.organizer }}</td>
            <td class="px-3 py-3 text-text-tertiary tnum">{{ d.chunk_count }} 片</td>
            <td class="px-3 py-3">
              <span v-if="d.is_enable" class="inline-flex items-center gap-1.5 text-[12.5px]" style="color: var(--success)">
                <span class="w-1.5 h-1.5 rounded-full" style="background-color: var(--success)" />检索中
              </span>
              <span v-else class="inline-flex items-center gap-1.5 text-[12.5px]" style="color: var(--text-tertiary)">
                <span class="w-1.5 h-1.5 rounded-full bg-text-disabled" />已停用
              </span>
            </td>
            <td class="px-5 py-3 text-right whitespace-nowrap">
              <button
                :class="
                  cn(
                    'px-3 py-1.5 rounded-[8px] text-[12.5px] transition-colors mr-2',
                    d.is_enable ? 'text-text-secondary bg-bg-subtle hover:bg-bg-inset' : 'text-primary-700 bg-primary-50 hover:bg-primary-100',
                  )
                "
                @click="toggleKbEnable(d)"
              >
                {{ d.is_enable ? '停用' : '启用' }}
              </button>
              <button
                class="px-2.5 py-1.5 rounded-[8px] text-[12.5px] text-text-tertiary hover:bg-bg-subtle transition-colors inline-flex items-center gap-1"
                :aria-label="`删除 ${d.doc_name}`"
                @click="removeKb(d)"
              >
                <Trash2 :size="13" :stroke-width="1.5" />
              </button>
            </td>
          </tr>
        </tbody>
      </table>
      <div class="px-5 py-2.5 text-[11.5px] text-text-tertiary border-t border-border">
        上传即按 300 字/片重建索引；停用的文档不参与问诊检索但不删档，删除为软删除（切真后端同步 Milvus 删除）。
      </div>
    </div>

    <!-- ===== 新增用户弹窗 ===== -->
    <div v-if="createOpen" class="fixed inset-0 z-50 flex items-center justify-center" @click="createOpen = false">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[420px] max-w-[92vw] p-6 animate-modal-in"
        style="box-shadow: var(--e3)"
        role="dialog"
        aria-modal="true"
        aria-label="新增用户"
        @click.stop
      >
        <div class="flex items-center justify-between mb-4">
          <h2 class="text-[16px] font-semibold text-text-primary flex items-center gap-2">
            <UserPlus :size="16" :stroke-width="1.5" class="text-primary" />新增用户
          </h2>
          <button class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle transition-colors" aria-label="关闭" @click="createOpen = false">
            <X :size="16" :stroke-width="1.5" />
          </button>
        </div>

        <div class="text-[13px] text-text-secondary mb-1.5">角色</div>
        <div class="flex gap-2 mb-4">
          <button
            v-for="r in ROLE_OPTIONS"
            :key="r"
            :class="
              cn(
                'flex-1 px-3 py-2 rounded-[10px] text-[13.5px] font-medium border transition-colors',
                cuRole === r ? 'bg-primary-50 text-primary-700 border-primary-300' : 'bg-bg-subtle text-text-secondary border-transparent hover:bg-bg-inset',
              )
            "
            @click="cuRole = r"
          >
            {{ r === 'vet' ? '执业兽医（复核病例）' : '管理员（后台管理）' }}
          </button>
        </div>

        <label for="au-phone" class="text-[13px] text-text-secondary mb-1.5 block">手机号</label>
        <input id="au-phone" v-model="cuPhone" maxlength="11" placeholder="11 位手机号" :class="cn(inputCls, 'mb-3')" />

        <label for="au-nick" class="text-[13px] text-text-secondary mb-1.5 block">昵称（选填）</label>
        <input id="au-nick" v-model="cuNickname" maxlength="24" placeholder="留空自动用脱敏手机号" :class="cn(inputCls, 'mb-3')" />

        <label for="au-pwd" class="text-[13px] text-text-secondary mb-1.5 block">初始密码（≥8 位，含字母与数字）</label>
        <input
          id="au-pwd"
          v-model="cuPassword"
          type="password"
          autocomplete="new-password"
          :class="cn(inputCls, 'mb-4')"
        />

        <div
          v-if="cuErr"
          class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
          style="background-color: rgba(217, 83, 79, 0.07); border: 1px solid rgba(217, 83, 79, 0.25); color: var(--danger)"
          role="alert"
        >
          {{ cuErr }}
        </div>

        <div class="flex justify-end gap-3">
          <button class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors" @click="createOpen = false">
            取消
          </button>
          <button
            :disabled="cuLoading"
            :class="
              cn(
                'px-5 py-2 rounded-[10px] text-[14px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors flex items-center gap-2',
                cuLoading && 'opacity-70 cursor-not-allowed',
              )
            "
            @click="submitCreateUser"
          >
            <span v-if="cuLoading" class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin" />
            {{ cuLoading ? '创建中…' : '确认创建' }}
          </button>
        </div>
      </div>
    </div>

    <!-- ===== F16 上传知识文档弹窗 ===== -->
    <div v-if="kbUploadOpen" class="fixed inset-0 z-50 flex items-center justify-center" @click="kbUploadOpen = false">
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[16px] w-[560px] max-w-[92vw] p-6 animate-modal-in max-h-[90vh] overflow-y-auto"
        style="box-shadow: var(--e3)"
        role="dialog"
        aria-modal="true"
        aria-label="上传知识文档"
        @click.stop
      >
        <div class="flex items-center justify-between mb-1.5">
          <h2 class="text-[16px] font-semibold text-text-primary flex items-center gap-2">
            <Upload :size="16" :stroke-width="1.5" class="text-primary" />上传知识文档
          </h2>
          <button class="p-1.5 rounded-[6px] text-text-tertiary hover:bg-bg-subtle transition-colors" aria-label="关闭" @click="kbUploadOpen = false">
            <X :size="16" :stroke-width="1.5" />
          </button>
        </div>
        <p class="text-[12.5px] text-text-tertiary mb-4">
          建议整理为 markdown 结构化文档后上传（附录 E SOP：LLM 辅助整理 + 人工校对）；上传成功后立即参与 AI 问诊检索。
        </p>

        <label class="text-[13px] text-text-secondary mb-1.5 block">文档类型</label>
        <div class="grid grid-cols-2 gap-2 mb-4">
          <button
            v-for="o in DOC_TYPE_OPTIONS"
            :key="o.v"
            type="button"
            :class="
              cn(
                'px-3.5 py-2.5 rounded-[10px] text-left border transition-all',
                kbDocType === o.v ? 'bg-primary-50 border-primary' : 'bg-bg-subtle border-transparent hover:bg-bg-inset',
              )
            "
            @click="kbDocType = o.v"
          >
            <div :class="cn('text-[13.5px] font-medium', kbDocType === o.v ? 'text-primary-700' : 'text-text-primary')">{{ o.label }}</div>
            <div class="text-[12px] text-text-tertiary mt-0.5">{{ o.desc }}</div>
          </button>
        </div>

        <label for="kb-name" class="text-[13px] text-text-secondary mb-1.5 block">
          文档名称 <span style="color: var(--danger)">*</span>
        </label>
        <input id="kb-name" v-model="kbDocName" maxlength="128" placeholder="如：猪流行性腹泻防控要点" :class="cn(inputCls, 'mb-3')" />

        <div class="grid grid-cols-2 gap-3 mb-3">
          <div>
            <label for="kb-species" class="text-[13px] text-text-secondary mb-1.5 block">适用物种</label>
            <select id="kb-species" :value="kbSpecies" :class="inputCls" @change="onKbSpeciesChange">
              <option value="pig">猪</option>
              <option value="cattle">牛</option>
              <option value="sheep">羊</option>
              <option value="">全部物种（公共法规）</option>
            </select>
          </div>
          <div v-if="kbDocType === 'disease'">
            <label for="kb-risk" class="text-[13px] text-text-secondary mb-1.5 block">风险等级</label>
            <select id="kb-risk" :value="kbRiskLevel" :class="inputCls" @change="onKbRiskChange">
              <option value="low">low（低风险）</option>
              <option value="mid">mid（中度风险）</option>
              <option value="high">high（高风险，转兽医复核）</option>
            </select>
          </div>
        </div>

        <label for="kb-kw" class="text-[13px] text-text-secondary mb-1.5 block">
          症状/主题关键词（逗号分隔，≥2 个）<span style="color: var(--danger)">*</span>
        </label>
        <input
          id="kb-kw"
          v-model="kbKeywords"
          :placeholder="kbDocType === 'disease' ? '如：水样腹泻, 呕吐, 快速脱水' : '如：饲料霉变, 采食下降'"
          :class="cn(inputCls, 'mb-3')"
        />

        <template v-if="kbDocType === 'disease' && medOptions.length > 0">
          <label class="text-[13px] text-text-secondary mb-1.5 block">推荐用药（选填，仅库内非违禁药）</label>
          <div class="flex flex-wrap gap-1.5 mb-3">
            <button
              v-for="m in medOptions"
              :key="m.med_id"
              type="button"
              :class="
                cn(
                  'px-2.5 py-1 rounded-[8px] text-[12px] border transition-colors',
                  kbMedNames.includes(m.med_name) ? 'bg-primary-50 border-primary text-primary-700' : 'bg-bg-subtle border-transparent text-text-secondary hover:bg-bg-inset',
                )
              "
              @click="toggleMed(m.med_name)"
            >
              {{ m.med_name }}
            </button>
          </div>
        </template>

        <label for="kb-advice" class="text-[13px] text-text-secondary mb-1.5 block">饲养/饲喂管理建议（选填）</label>
        <input
          id="kb-advice"
          v-model="kbFeedAdvice"
          maxlength="500"
          placeholder="如：重点是补液防脱水、保温；停料减料减轻肠道负担"
          :class="cn(inputCls, 'mb-3')"
        />

        <label for="kb-source" class="text-[13px] text-text-secondary mb-1.5 block">
          知识来源（书名/规范名）<span style="color: var(--danger)">*</span>
        </label>
        <input
          id="kb-source"
          v-model="kbSource"
          maxlength="256"
          placeholder="如：《猪病学》消化系统疾病章节（公开出版物合规整理）"
          :class="cn(inputCls, 'mb-3')"
        />

        <label for="kb-organizer" class="text-[13px] text-text-secondary mb-1.5 block">
          整理人 <span style="color: var(--danger)">*</span>
        </label>
        <input id="kb-organizer" v-model="kbOrganizer" maxlength="64" placeholder="知识整理留痕（附录 E SOP）" :class="cn(inputCls, 'mb-3')" />

        <label for="kb-content" class="text-[13px] text-text-secondary mb-1.5 block">正文内容（markdown，选填，每 300 字计 1 片）</label>
        <textarea
          id="kb-content"
          v-model="kbContentMd"
          maxlength="20000"
          rows="5"
          placeholder="粘贴整理好的 markdown 正文（存档与溯源用）"
          :class="cn(inputCls, 'mb-4 h-auto py-2')"
        />

        <div
          v-if="kbErr"
          class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
          style="background-color: rgba(217, 83, 79, 0.07); border: 1px solid rgba(217, 83, 79, 0.25); color: var(--danger)"
          role="alert"
        >
          {{ kbErr }}
        </div>

        <div class="flex justify-end gap-3">
          <button class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors" @click="kbUploadOpen = false">
            取消
          </button>
          <button
            :disabled="kbLoading"
            :class="
              cn(
                'px-5 py-2 rounded-[10px] text-[14px] text-white bg-primary hover:bg-primary-600 font-medium transition-colors flex items-center gap-2',
                kbLoading && 'opacity-70 cursor-not-allowed',
              )
            "
            @click="submitKbUpload"
          >
            <span v-if="kbLoading" class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin" />
            {{ kbLoading ? '上传中…' : '上传并入检索池' }}
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
