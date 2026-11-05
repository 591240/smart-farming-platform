<script setup lang="ts">
// 待复核病例页 — F10 FR-01/02/03（06 规范 4.10，仅 vet 角色可见）
// - 数据源：GET /vet/pending-cases（入口惰性 sweep ≥48h 降级；waiting_hours 实时计算；warn 恒置顶）
// - 环形进度微件（4.10）：48h 复核窗口剩余弧长，≥24h 变红；等待时长数字中心
// - 复核弹窗（S1 主流程）：original_diagnosis 全文（F05 快照含症状描述）+ approve 意见选填 /
//   reject 三必填（修改后用药实时过兽药库校验，违禁/库外 42201 逐字段提示 EX-R6）
// - 跨租户最小字段集：本页不展示养殖户手机号/user_id（服务端白名单，前端无从取得）

import { computed, ref } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import { CheckCircle2, XCircle, ClipboardCheck, Stethoscope } from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { ApiError } from '@/api/client';
import { apiVetPendingCases, apiVetReview, type PendingCaseItem } from '@/api/vet-api';

const TIMEOUT_WINDOW_H = 48; // 复核窗口（EX-06 降级线）
const WARN_H = 24; // 超时提醒线（EX-05）

function relNote(iso: string): string {
  const d = new Date(iso);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/** 环形进度微件（06 规范 4.10）：24h 一圈口径落地为 48h 窗口剩余比例，≥24h 变红 */
const RING_SIZE = 40;
const RING_R = 16;
const RING_C = 2 * Math.PI * RING_R;

function ringRemain(hours: number): number {
  return Math.max(0, Math.min(1, 1 - hours / TIMEOUT_WINDOW_H));
}

function ringWarn(hours: number): boolean {
  return hours >= WARN_H;
}

function ringColor(hours: number): string {
  return ringWarn(hours) ? 'var(--danger)' : 'var(--primary)';
}

function ringDash(hours: number): string {
  const remain = ringRemain(hours);
  return `${RING_C * remain} ${RING_C * (1 - remain)}`;
}

function ringLabel(hours: number): string {
  return `已等待 ${hours} 小时${ringWarn(hours) ? '，已超时' : ''}`;
}

const queryClient = useQueryClient();
const reviewTarget = ref<PendingCaseItem | null>(null);
const { data, isLoading } = useQuery({
  queryKey: computed(() => ['vet-pending']),
  queryFn: apiVetPendingCases,
  refetchInterval: 15_000, // 轮询近似推送（实时推流随切真，范围外）
});
const items = computed(() => data.value?.items ?? []);
const warnCount = computed(() => items.value.filter((i) => i.timeout_level === 'warn').length);

function refresh() {
  queryClient.invalidateQueries({ queryKey: ['vet-pending'] });
  queryClient.invalidateQueries({ queryKey: ['alerts'] });
  queryClient.invalidateQueries({ queryKey: ['records'] });
}

// ===== 复核弹窗（approve 意见选填 / reject 三必填 + 药库校验 42201 逐字段回显）=====
// 打开复核弹窗时重置全部表单态，避免残留上一条记录的内容。
const action = ref<'approve' | 'reject'>('approve');
const opinion = ref('');
const revisedDiagnosis = ref('');
const revisedMed = ref('');
const err = ref<string | null>(null);
const submitting = ref(false);

function openReview(target: PendingCaseItem) {
  reviewTarget.value = target;
  action.value = 'approve';
  opinion.value = '';
  revisedDiagnosis.value = '';
  revisedMed.value = '';
  err.value = null;
  submitting.value = false;
}

function closeReview() {
  reviewTarget.value = null;
}

async function submitReview() {
  const target = reviewTarget.value;
  if (!target) return;
  submitting.value = true;
  err.value = null;
  try {
    await apiVetReview({
      review_id: target.review_id,
      action: action.value,
      review_opinion: opinion.value.trim() || undefined,
      ...(action.value === 'reject'
        ? { revised_diagnosis: revisedDiagnosis.value.trim(), revised_med: revisedMed.value.trim() }
        : {}),
    });
    refresh();
    closeReview();
  } catch (e) {
    // 校验失败零部分状态（FR-03 服务端保证）；错误文案含字段名直接回显
    err.value = e instanceof ApiError ? e.message : '提交失败，请稍后重试';
  } finally {
    submitting.value = false;
  }
}
</script>

<template>
  <div class="px-8 py-6 max-w-[1100px]">
    <div class="mb-6">
      <h1 class="text-[20px] font-semibold text-text-primary leading-[1.4] flex items-center gap-2">
        <ClipboardCheck :size="19" :stroke-width="1.5" class="text-primary" />
        待复核病例
      </h1>
      <p class="text-[14px] text-text-secondary mt-0.5">
        高危疫病 AI 诊断需执业兽医复核后方可落地
        <span v-if="warnCount > 0" class="ml-2 font-medium" :style="{ color: 'var(--danger)' }">
          {{ warnCount }} 例已超过 24 小时未复核
        </span>
      </p>
    </div>

    <!-- 空态 -->
    <div
      v-if="!isLoading && items.length === 0"
      class="bg-card rounded-[12px] p-14 card-highlight flex flex-col items-center text-center"
    >
      <div class="w-16 h-16 rounded-full bg-primary-50 flex items-center justify-center mb-5">
        <CheckCircle2 :size="28" :stroke-width="1.5" class="text-success" />
      </div>
      <h3 class="text-[16px] font-semibold text-text-primary mb-2">没有待复核病例</h3>
      <p class="text-[13.5px] text-text-secondary max-w-[420px] leading-[1.7]">
        养殖户问诊判定为高风险时，病例会自动进入这里；
        超过 48 小时未复核的病例将按原诊断自动降级收尾。
        高危病例可在 AI 问诊就高危主诉自然产生（hitl_gate 节点）。
      </p>
    </div>

    <div v-if="isLoading" class="bg-card rounded-[12px] p-6 card-highlight skeleton-shimmer h-[160px]" />

    <!-- 列表：warn 恒前 + waiting_hours 倒序（服务端已排） -->
    <div class="space-y-3">
      <div
        v-for="(c, idx) in items"
        :key="c.review_id"
        :class="
          cn(
            'bg-card rounded-[12px] p-5 card-highlight transition-all',
            idx === 0 && c.timeout_level === 'warn' ? 'animate-slide-in' : '',
          )
        "
        :style="c.timeout_level === 'warn' ? { borderLeft: '4px solid var(--danger)' } : undefined"
      >
        <div class="flex items-start gap-4">
          <svg
            :width="RING_SIZE"
            :height="RING_SIZE"
            viewBox="0 0 40 40"
            role="img"
            :aria-label="ringLabel(c.waiting_hours)"
          >
            <circle cx="20" cy="20" :r="RING_R" fill="none" stroke="rgba(20,30,24,.08)" stroke-width="3.5" />
            <circle
              cx="20"
              cy="20"
              :r="RING_R"
              fill="none"
              :stroke="ringColor(c.waiting_hours)"
              stroke-width="3.5"
              stroke-linecap="round"
              :stroke-dasharray="ringDash(c.waiting_hours)"
              transform="rotate(-90 20 20)"
              style="transition: stroke-dasharray .3s"
            />
            <text
              x="20"
              y="24"
              text-anchor="middle"
              font-size="10.5"
              class="tnum"
              :fill="ringWarn(c.waiting_hours) ? 'var(--danger)' : 'var(--text-primary)'"
            >{{ Math.floor(c.waiting_hours) }}h</text>
          </svg>
          <div class="flex-1 min-w-0">
            <div class="flex items-center gap-2 mb-1 flex-wrap">
              <span class="text-[14px] font-semibold text-text-primary">{{ c.farm_name }} · {{ c.pen_name }}</span>
              <span
                class="px-1.5 py-0.5 rounded-[4px] text-[11px] font-medium"
                :style="{ backgroundColor: 'rgba(217,83,79,.06)', color: 'var(--danger)' }"
              >
                高风险
              </span>
              <span
                v-if="c.timeout_level === 'warn'"
                class="px-1.5 py-0.5 rounded-[4px] text-[11px] font-medium"
                :style="{ backgroundColor: 'rgba(217,83,79,.08)', color: 'var(--danger)' }"
              >
                已等待 {{ c.waiting_hours }}h，请尽快复核
              </span>
              <span v-if="c.alert_id > 0" class="px-1.5 py-0.5 rounded-[4px] text-[11px] bg-bg-subtle text-text-tertiary">
                来源：告警 #{{ c.alert_id }}
              </span>
            </div>
            <p class="text-[13.5px] text-text-secondary leading-[1.7]">{{ c.original_diagnosis }}</p>
            <div class="text-[12px] text-text-tertiary mt-1.5">提交时间 {{ relNote(c.create_time) }} · 会话 #{{ c.session_id }}</div>
          </div>
          <button
            @click="openReview(c)"
            class="flex items-center gap-2 px-4 py-2 rounded-[10px] text-[14px] font-medium bg-primary text-white hover:bg-primary-600 transition-colors shrink-0"
          >
            <Stethoscope :size="15" :stroke-width="1.5" />
            复核
          </button>
        </div>
      </div>
    </div>

    <div
      v-if="reviewTarget"
      class="fixed inset-0 z-50 flex items-center justify-center"
      @click="closeReview"
    >
      <div class="absolute inset-0 bg-[rgba(20,30,24,0.45)]" />
      <div
        class="relative bg-card rounded-[12px] w-[560px] max-w-[94vw] max-h-[88vh] overflow-y-auto p-6 animate-modal-in"
        :style="{ boxShadow: 'var(--e3)' }"
        @click.stop
        role="dialog"
        aria-modal="true"
        aria-label="病例复核"
      >
        <h2 class="text-[16px] font-semibold text-text-primary mb-1">病例复核 #{{ reviewTarget.review_id }}</h2>
        <div class="text-[12.5px] text-text-tertiary mb-4">
          {{ reviewTarget.farm_name }} · {{ reviewTarget.pen_name }} · 已等待 {{ reviewTarget.waiting_hours }}h
          <span v-if="reviewTarget.timeout_level === 'warn'" class="ml-1.5 font-medium" :style="{ color: 'var(--danger)' }">（超 24h 未复核）</span>
        </div>

        <!-- 原诊断全文（F05 快照含症状；跨租户最小集不含养殖户身份信息） -->
        <div
          class="px-3.5 py-3 rounded-[10px] mb-4"
          :style="{ backgroundColor: 'rgba(217,83,79,.04)', border: '1px solid rgba(217,83,79,.12)' }"
        >
          <div class="text-[12px] font-medium text-text-tertiary mb-1">AI 原诊断（高风险，建议复核）</div>
          <p class="text-[13.5px] text-text-primary leading-[1.7]">{{ reviewTarget.original_diagnosis }}</p>
        </div>

        <!-- 动作切换 -->
        <div class="flex gap-2 mb-4">
          <button
            @click="action = 'approve'"
            :class="
              cn(
                'flex-1 px-3 py-2.5 rounded-[10px] text-[13.5px] font-medium border transition-colors flex items-center justify-center gap-1.5',
                action === 'approve'
                  ? 'bg-primary-50 text-primary-700 border-primary-300'
                  : 'bg-bg-subtle text-text-secondary border-transparent hover:bg-bg-inset',
              )
            "
          >
            <CheckCircle2 :size="15" :stroke-width="1.5" /> 确认原诊断
          </button>
          <button
            @click="action = 'reject'"
            :class="
              cn(
                'flex-1 px-3 py-2.5 rounded-[10px] text-[13.5px] font-medium border transition-colors flex items-center justify-center gap-1.5',
                action === 'reject' ? 'text-white' : 'bg-bg-subtle text-text-secondary border-transparent hover:bg-bg-inset',
              )
            "
            :style="action === 'reject' ? { backgroundColor: 'var(--warning)', borderColor: 'var(--warning)' } : undefined"
          >
            <XCircle :size="15" :stroke-width="1.5" /> 驳回修改
          </button>
        </div>

        <div class="mb-3">
          <label for="vet-opinion" class="text-[13px] text-text-secondary mb-1.5 block">
            复核意见{{ action === 'reject' ? '（驳回必填）' : '（选填）' }}
          </label>
          <textarea
            id="vet-opinion"
            v-model="opinion"
            maxlength="500"
            rows="2"
            :placeholder="action === 'approve' ? '可补充观察要点（≤500 字）' : '请说明驳回理由'"
            class="w-full px-3 py-2 rounded-[10px] bg-card border border-border-strong text-[13.5px] text-text-primary outline-none focus:border-primary transition-colors resize-none"
          />
        </div>

        <template v-if="action === 'reject'">
          <div class="mb-3">
            <label for="vet-rd" class="text-[13px] text-text-secondary mb-1.5 block">修改后诊断（必填）</label>
            <input
              id="vet-rd"
              v-model="revisedDiagnosis"
              placeholder="如：猪肺疫（巴氏杆菌病）"
              class="w-full h-10 px-3 rounded-[10px] bg-card border border-border-strong text-[14px] text-text-primary outline-none focus:border-primary transition-colors"
            />
          </div>
          <div class="mb-3">
            <label for="vet-rm" class="text-[13px] text-text-secondary mb-1.5 block">修改后用药（必填，需兽药库在库且非违禁）</label>
            <input
              id="vet-rm"
              v-model="revisedMed"
              placeholder="如：双黄连可溶性粉拌料"
              class="w-full h-10 px-3 rounded-[10px] bg-card border border-border-strong text-[14px] text-text-primary outline-none focus:border-primary transition-colors"
            />
            <p class="text-[11.5px] text-text-tertiary mt-1.5">违禁药品不可用于复核方案——底线对人和 AI 一视同仁。</p>
          </div>
        </template>

        <div
          v-if="err"
          class="mb-4 px-3.5 py-2.5 rounded-[10px] text-[13px]"
          :style="{ backgroundColor: 'rgba(217,83,79,.07)', border: '1px solid rgba(217,83,79,.25)', color: 'var(--danger)' }"
          role="alert"
        >
          {{ err }}
        </div>

        <div class="text-[11.5px] text-text-tertiary leading-[1.7] mb-4">
          复核操作将全程留痕（复核人/意见/耗时），并生成独立「兽医复核」台账记录；原 AI 诊断台账保持不可变，供追溯对照。
        </div>

        <div class="flex justify-end gap-3">
          <button @click="closeReview" class="px-4 py-2 rounded-[10px] text-[14px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors">
            取消
          </button>
          <button
            @click="submitReview"
            :disabled="submitting"
            :class="
              cn(
                'px-5 py-2 rounded-[10px] text-[14px] text-white font-medium transition-colors',
                submitting ? 'opacity-70 cursor-not-allowed' : 'hover:opacity-90',
              )
            "
            :style="{ backgroundColor: action === 'reject' ? 'var(--warning)' : 'var(--primary)' }"
          >
            {{ submitting ? '提交中…' : action === 'approve' ? '确认并提交' : '驳回并提交' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
