<script setup lang="ts">
// AI 对话问诊页 — F05 FR-02/08/10（06 规范 4.9，mock 切真）
// - 数据源：POST /agent/chat（SSE 流式：intent/token/section/done/error）+ 会话历史接口
// - 布局：左侧会话列表 240px（<768 隐藏，降级单栏）+ 右侧气泡流（用户右 primary-100
//   圆角 16px/右下 4px；AI 左白卡 e1/左下 4px），AI 回复分节渲染（诊断/用药/饲料建议）
// - 结果区固定元素：风险标签（low 绿/mid 橙/high 红"已转交兽医复核"）、依据来源小字、
//   底部固定免责声明条
// - 图像问诊（FR-11）：输入区相机按钮 → 浏览器压至 ≤512px JPEG → POST /agent/chat 带 image，
//   图先经 vision_describe 出客观征象再进检索；气泡与历史回显缩略图（meta.image）
// - F14 个体绑定：输入区 [绑定个体] 选耳标 → 本轮带 individual_id，后端按该个体所在圈定位、
//   结论回写台账（record_ext.tag_no）；结果卡 [记入个体档案] 把本轮结论写为该个体的诊疗事件
// - 台账为只读档案流，页面不提供任何编辑/删除入口

import { computed, onMounted, ref, watch, type Component } from 'vue';
import { useQuery, useQueryClient } from '@tanstack/vue-query';
import {
  Plus, MessageSquare, Send, Stethoscope, Pill, Wheat, ShieldAlert,
  AlertTriangle, RotateCcw, RefreshCw, NotebookPen, Trash2,
  Camera, X, Tag,
} from 'lucide-vue-next';
import { cn } from '@/lib/utils';
import { ApiError } from '@/lib/api-contract';
import { useAuthStore } from '@/stores/auth';
import { fileToImageDataUrl, ImageInputError } from '@/lib/image-input';
import { apiChatStream, apiListSessions, apiSessionMessages, apiDeleteSession, type AgentMessageItem } from '@/api/agent-api';
import PickIndividualDialog from '@/components/individual/PickIndividualDialog.vue';
import RecordToIndividualDialog from '@/components/individual/RecordToIndividualDialog.vue';
import type { PickedIndividual } from '@/components/individual/types';
import RiskBadge from '@/components/aichat/RiskBadge.vue';
import MarkdownText from '@/components/aichat/MarkdownText.vue';
import StreamingCursor from '@/components/aichat/StreamingCursor.vue';
import ThinkingRow from '@/components/aichat/ThinkingRow.vue';

// ===== 本地消息模型 =====

interface UiSection {
  key?: string;
  title: string;
  text: string;
  source?: string;
}

interface UiMessage {
  id: number;
  role: 'user' | 'assistant';
  /** user / 纯文本 AI 回复（引导话术、澄清、拦截） */
  text?: string;
  /** 用户轮的缩略图（压缩后的 data URL，与发给后端的 image 同一份） */
  image?: string;
  /** 分节 AI 回复（诊断/用药/饲料建议） */
  sections?: UiSection[];
  risk?: string;
  /** 本轮未绑定到具体圈舍（先答后问）→ 高危标签不得声称"已转交兽医复核" */
  penUnresolved?: boolean;
  /** 本轮提问原文（"记入个体档案"用它做症状预填） */
  prompt?: string;
  /** F14：本轮绑定的个体（done 帧回填，历史轮由 meta.individual 复原） */
  individual?: PickedIndividual;
  /** 50002 知识库无结果（拒绝卡片，不可重试） */
  code?: number;
  /** 50001 管道/LLM 失败（错误气泡 + 重试，EX-03） */
  failed?: boolean;
  time: string;
  streaming?: boolean;
  /** 流式等待期文案（intent 帧推进），首个 token/section 到达后即被正文取代 */
  stage?: string;
}

const SECTION_ICON: Record<string, Component> = {
  diagnosis: Stethoscope,
  medication: Pill,
  nutrition: Wheat,
};

// 空态引导文案按账号主物种出（注册引导选择的物种；示例话术均可命中对应物种知识库）
const CONSULT_EMPTY: Record<string, { title: string; examples: string[] }> = {
  pig: { title: '描述猪的症状，开始 AI 问诊', examples: ['小猪咳嗽不吃食', '母猪产后站不起来', '猪拉水样稀粪怎么办'] },
  sheep: { title: '描述羊的症状，开始 AI 问诊', examples: ['羊咳嗽不吃食', '羊消瘦贫血被毛粗乱', '羔羊水样腹泻怎么办'] },
  cattle: { title: '描述牛的症状，开始 AI 问诊', examples: ['牛咳嗽流鼻涕', '牛反刍减少不吃食', '牛突然腹胀不安怎么办'] },
};

function nowTime(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 流式等待期文案：intent 帧一到就推进阶段，避免"空气泡干等"被当成没回复
const STAGE_WAIT = '正在连接 AI 助手…';
const INTENT_STAGE: Record<string, string> = {
  consult: '已识别为 AI 问诊，正在检索知识库并生成诊断（约 10~30 秒）…',
  feed: '正在分析饲喂方案…',
  data_query: '正在查询您的场舍数据…',
  weather: '正在获取气象数据…',
};

/** 只有问诊结论卡才有"记入个体"的意义（引导话术/澄清/拦截轮没有诊断节） */
function isConsultResult(m: UiMessage): boolean {
  return !m.streaming && !!m.sections?.some((s) => s.key === 'diagnosis' || s.title === '诊断');
}

function sectionText(m: UiMessage, key: 'diagnosis' | 'medication'): string {
  return m.sections?.find((s) => s.key === key || (key === 'diagnosis' && s.title === '诊断') || (key === 'medication' && s.title === '用药建议'))?.text ?? '';
}

/** 取首行并剥掉列表符号/加粗标记：档案字段 128 字，塞整段建议只会截断成半句话 */
function firstLine(text: string): string {
  return text.split('\n').map((l) => l.replace(/^[-*]\s*/, '').replace(/\*\*/g, '').trim()).find(Boolean) ?? '';
}

/** 历史消息 → 本地模型（meta.sections 重建分节结构） */
function fromHistory(m: AgentMessageItem, id: number, prompt?: string): UiMessage {
  const time = m.create_time.slice(11, 16);
  if (m.role === 'user') return { id, role: 'user', text: m.content, image: m.meta.image ?? undefined, time };
  const sections = m.meta.sections;
  if (sections && sections.length > 0) {
    return {
      id, role: 'assistant', risk: m.meta.risk_level, penUnresolved: m.meta.pen_unresolved,
      prompt, individual: m.meta.individual,
      sections: sections.map((s, i) => ({ key: ['diagnosis', 'medication', 'nutrition'][i] ?? '', title: s.title, text: s.text, source: s.source })),
      time,
    };
  }
  return { id, role: 'assistant', text: m.content, code: m.meta.code, risk: m.meta.risk_level, prompt, time };
}

const queryClient = useQueryClient();
const auth = useAuthStore();
// 空态引导按主物种（默认猪：未选物种/异常兜底）
const empty = computed(() => CONSULT_EMPTY[auth.user?.main_species_code ?? 'pig'] ?? CONSULT_EMPTY.pig);
const activeSessionId = ref<number | null>(null);
// 会话归属同步 ref（2026-10-14 修复）：done 帧 setActiveSessionId 后，同轮排队的续发
// send() 读 state 闭包仍是旧值（null）→ 补位轮被当新会话、上下文断裂。
// 凡改会话归属处（done 帧/收养/切换/新建）必须同步此 ref，ref 为发送时唯一事实源
const activeSessionRef = ref<number | null>(null);
const messages = ref<UiMessage[]>([]);
const inputValue = ref('');
// 待发图片（已压缩的 data URL）；null = 本轮不带图
const pendingImage = ref<string | null>(null);
const imageBusy = ref(false);
const imageErr = ref<string | null>(null);
// F14 个体绑定：粘在输入区——连问几轮通常都是同一头；切会话/新建时才清
const bound = ref<PickedIndividual | null>(null);
const pickOpen = ref(false);
// 待写档的那一轮（点结果卡「记入个体档案」时挂上）
const archiveFor = ref<UiMessage | null>(null);
const archiveTip = ref<string | null>(null);
const fileRef = ref<HTMLInputElement | null>(null);
const streaming = ref(false);
const historyLoading = ref(false);
const lastSentRef = ref<string>('');
const lastImageRef = ref<string | null>(null);
// 流式期间的发送队列（2026-10-14 修复：原实现 streaming 时静默丢弃用户消息，
// 补槽位场景"3号圈"发出去毫无响应）：本轮 done/error 后自动续发。
// streamingRef 与 streaming state 同步维护——续发递归调用时 state 闭包是旧值，必须以 ref 为准
const queueRef = ref<{ text: string; image: string | null; individual: PickedIndividual | null }[]>([]);
const streamingRef = ref(false);
// done 帧自设会话标记：置 true 时切换会话 effect 跳过历史重载（防覆盖在途气泡）
const selfSwitchRef = ref(false);
const idRef = ref(1);
const scrollRef = ref<HTMLDivElement | null>(null);
// 会话删除（FR-08 扩展：列表项悬停删除按钮 + 内联二次确认）
const confirmDeleteId = ref<number | null>(null);

const archivePrefill = computed(() => {
  const m = archiveFor.value;
  if (!m) return { symptom: '', diagnosis: '', medication: '' };
  return {
    symptom: m.prompt ?? '',
    diagnosis: firstLine(sectionText(m, 'diagnosis')),
    medication: firstLine(sectionText(m, 'medication')),
  };
});
const archiveDefaultPick = computed(() => archiveFor.value?.individual);

// ----- 会话列表（FR-08）-----
const { data: sessionData, isFetching: sessionsFetching } = useQuery({
  queryKey: ['agent-sessions'],
  queryFn: apiListSessions,
});
const sessions = computed(() => sessionData.value?.items ?? []);

// ----- 切换会话：加载历史（TC-G15）-----
// selfSwitchRef：done 帧把新会话 ID 落到 activeSessionId 时，本 effect 若照常拉历史，
// 异步回调会用"刚落库的旧轮次"整体覆盖 messages，冲掉排队续发轮已挂出的占位气泡
// （2026-10-14 竞态修复：澄清补位轮落库成功但界面空白）。自设会话跳过重载。
watch(
  activeSessionId,
  (sessionId, _prev, onCleanup) => {
    if (selfSwitchRef.value) {
      selfSwitchRef.value = false;
      activeSessionRef.value = sessionId;
      return;
    }
    queueRef.value = []; // 切会话清空排队，防消息串入他处
    bound.value = null; // 同理：个体绑定不跨会话带过去
    activeSessionRef.value = sessionId; // 归属 ref 同步
    if (sessionId === null) {
      messages.value = [];
      return;
    }
    let cancelled = false;
    historyLoading.value = true;
    apiSessionMessages(sessionId)
      .then(({ items }) => {
        if (cancelled || streamingRef.value) return; // 流式进行中不接受历史覆盖（双保险）
        // 历史 id 必须走同一个自增计数器：早先固定从 1 编号，会和随后发出的气泡撞 key
        messages.value = items.map((m, i) => {
          const prev = i > 0 ? items[i - 1] : undefined;
          // 本轮提问原文：assistant 轮取上一条 user 消息（"记入个体档案"的症状预填）
          return fromHistory(m, idRef.value++, m.role === 'assistant' && prev?.role === 'user' ? prev.content : undefined);
        });
      })
      .catch(() => {
        if (!cancelled && !streamingRef.value) messages.value = [];
      })
      .finally(() => {
        if (!cancelled) historyLoading.value = false;
      });
    onCleanup(() => {
      cancelled = true;
    });
  },
  { immediate: true },
);

watch(
  [messages, streaming],
  () => {
    const el = scrollRef.value;
    if (el) el.scrollTop = el.scrollHeight;
  },
  { flush: 'post' },
);

// ----- 异常跳转预填上下文（06 规范 4.9 快捷入口 / F05 FR-10 预留，喂食中心 [归因分析] 跳入）-----
// 喂食中心侧 sessionStorage 写入 'sf_ai_prefill'，本页挂载时读出填入输入框并清除 key，
// 用户确认/补充后手动发送（不自动发出，保留用户对内容的控制权）
onMounted(() => {
  const prefill = sessionStorage.getItem('sf_ai_prefill');
  if (prefill) {
    sessionStorage.removeItem('sf_ai_prefill');
    inputValue.value = prefill;
  }
});

// archiveTip 自动消失（卸载时必须清理定时器）
watch(archiveTip, (tip, _old, onCleanup) => {
  if (!tip) return;
  const t = window.setTimeout(() => (archiveTip.value = null), 2600);
  onCleanup(() => window.clearTimeout(t));
});

function sectionIcon(key?: string): Component {
  return (key ? SECTION_ICON[key] : undefined) ?? MessageSquare;
}

// ----- 流式发送（FR-02/08）-----
/** 选图即压：失败只提示、不污染输入区（错误在下一行小字里给原文） */
async function pickImage(f: File) {
  imageBusy.value = true;
  imageErr.value = null;
  try {
    pendingImage.value = await fileToImageDataUrl(f);
  } catch (e) {
    imageErr.value = e instanceof ImageInputError ? e.message : '图片处理失败，请换一张';
  } finally {
    imageBusy.value = false;
  }
}

function onFileChange(e: Event) {
  const target = e.target as HTMLInputElement;
  const f = target.files?.[0];
  target.value = ''; // 允许连续选同一张文件也触发 change
  if (f) void pickImage(f);
}

async function send(text: string, opts?: { retry?: boolean; image?: string | null; individual?: PickedIndividual | null }) {
  const content = text.trim();
  const image = opts?.image ?? pendingImage.value;
  const individual = opts?.individual ?? bound.value;
  if (!content && !image) return;
  if (streamingRef.value && !opts?.retry) {
    // 流式中不丢弃：入队，本轮回复结束后自动续发（同会话语义，补槽位关键路径）
    queueRef.value.push({ text: content, image, individual });
    inputValue.value = '';
    pendingImage.value = null;
    return;
  }
  if (streamingRef.value && opts?.retry) return;
  // id 必须在 setMessages 之外自增：写进 updater 里等于把副作用塞给渲染阶段，
  // updater 可能延后甚至重复执行，assistantId 就会指到别的气泡上
  //（2026-10-15 现象：AI 回复被打进用户气泡，占位气泡永远空白）
  const t = nowTime();
  const assistantId = idRef.value + (opts?.retry ? 0 : 1);
  idRef.value += opts?.retry ? 1 : 2;
  if (!opts?.retry) {
    const userId = assistantId - 1;
    lastSentRef.value = content;
    lastImageRef.value = image;
    messages.value = [
      ...messages.value,
      { id: userId, role: 'user', text: content, image: image ?? undefined, time: t },
      { id: assistantId, role: 'assistant', sections: [], time: t, streaming: true, stage: STAGE_WAIT, prompt: content, individual: individual ?? undefined },
    ];
  } else {
    // 重试：替换失败气泡，重新流式（失败轮次不落库，重发安全）
    messages.value = [
      ...messages.value.filter((m) => !(m.role === 'assistant' && m.failed)),
      { id: assistantId, role: 'assistant', sections: [], time: t, streaming: true, stage: STAGE_WAIT, individual: individual ?? undefined },
    ];
  }
  streaming.value = true;
  streamingRef.value = true;
  inputValue.value = '';
  pendingImage.value = null;

  let errCode: number | undefined;
  let errText = 'AI 服务暂时不可用，请稍后重试';

  const patchAssistant = (fn: (m: UiMessage) => UiMessage) => {
    messages.value = messages.value.map((m) => (m.id === assistantId ? fn(m) : m));
  };

  try {
    await apiChatStream(
      {
        session_id: activeSessionRef.value ?? undefined,
        message: content,
        ...(image ? { image } : {}),
        ...(individual ? { individual_id: individual.individual_id } : {}),
      },
      (event, data) => {
        if (event === 'intent') {
          const stage = INTENT_STAGE[String(data.intent ?? '')];
          patchAssistant((m) => ({ ...m, stage: stage ?? '正在分析…' }));
        } else if (event === 'section') {
          patchAssistant((m) => ({
            ...m,
            sections: [
              ...(m.sections ?? []),
              { key: String(data.key ?? ''), title: String(data.title ?? ''), text: '', source: data.source ? String(data.source) : undefined },
            ],
          }));
        } else if (event === 'token') {
          const chunk = String(data.text ?? '');
          patchAssistant((m) => {
            if (m.sections && m.sections.length > 0) {
              const sections = [...m.sections];
              sections[sections.length - 1] = { ...sections[sections.length - 1], text: sections[sections.length - 1].text + chunk };
              return { ...m, sections };
            }
            return { ...m, text: (m.text ?? '') + chunk };
          });
        } else if (event === 'done') {
          const sessionId = Number(data.session_id);
          if (sessionId) {
            // ref 立即同步（不等重渲染）：排队续发的补位轮必须归属本会话
            activeSessionRef.value = sessionId;
            if (sessionId !== activeSessionId.value) {
              selfSwitchRef.value = true; // 自设会话：effect 跳过历史重载，保住排队轮气泡
              activeSessionId.value = sessionId;
            }
            queryClient.invalidateQueries({ queryKey: ['agent-sessions'] });
          }
          if (data.risk_level) {
            patchAssistant((m) => ({ ...m, risk: String(data.risk_level), penUnresolved: !!data.pen_unresolved, prompt: m.prompt ?? content }));
          }
          // 以后端回填为准：绑定的个体被服务端判为不可用时不会进 meta，前端不该继续显示已绑
          const ind = data.individual as { individual_id?: number; ear_tag_no?: string; pen_name?: string } | undefined;
          if (ind?.individual_id) {
            patchAssistant((m) => ({
              ...m,
              individual: { individual_id: ind.individual_id as number, ear_tag_no: ind.ear_tag_no ?? '', pen_name: ind.pen_name ?? '' },
            }));
          }
        } else if (event === 'error') {
          errCode = Number(data.code ?? 50001);
          if (data.message) errText = String(data.message);
        }
      },
    );
  } catch (e) {
    errCode = 50001;
    if (e instanceof ApiError) {
      errText = e.message;
      // 开流前的归属/参数校验（40401/40302/42201）不是"AI 服务不可用"：
      // 给拒绝卡片并原样显示服务端理由，给 [重试] 只会让人反复点同一个错误选择
      if (e.code >= 40000 && e.code < 50000) errCode = e.code;
    }
  }

  // 50001/50003 = 服务侧失败（可重试）；其余 4xxxx = 本轮请求被拒（改正选择才有用）
  const retryable = errCode === undefined ? null : errCode === 50001 || errCode === 50003;
  patchAssistant((m) => ({
    ...m,
    streaming: false,
    stage: undefined,
    failed: retryable === true,
    code: retryable === false ? errCode : undefined,
    text: retryable === false ? errText : m.text,
    time: nowTime(),
  }));
  streaming.value = false;
  streamingRef.value = false;

  // 续发排队消息：先出队再触发渲染（done 帧的 setActiveSessionId 会让切会话 effect 清队列，
  // 新会话首轮流式后 done 带 id 属正常归属切换，不能让清队逻辑误杀已排队补位消息）
  const next = queueRef.value.shift();
  if (next) send(next.text, { image: next.image, individual: next.individual });
}

function retrySend() {
  send(lastSentRef.value, { retry: true, image: lastImageRef.value });
}

function newChat() {
  if (streamingRef.value) return;
  queueRef.value = [];
  activeSessionRef.value = null;
  activeSessionId.value = null;
  messages.value = [];
  bound.value = null; // 换会话=换主体，绑定个体不跨会话延续
}

async function removeSession(sessionId: number) {
  if (streaming.value) return;
  confirmDeleteId.value = null;
  try {
    await apiDeleteSession(sessionId);
  } catch {
    /* mock 层删除失败极罕见；列表刷新兜底 */
  }
  if (activeSessionId.value === sessionId) {
    activeSessionRef.value = null;
    activeSessionId.value = null;
    messages.value = [];
  }
  queryClient.invalidateQueries({ queryKey: ['agent-sessions'] });
}

function selectSession(sessionId: number) {
  if (streaming.value) return;
  confirmDeleteId.value = null;
  activeSessionId.value = sessionId;
}

function sessionKeydown(e: KeyboardEvent, sessionId: number) {
  if (e.key === 'Enter' && !streaming.value) activeSessionId.value = sessionId;
}

function openDeleteConfirm(sessionId: number) {
  if (!streaming.value) confirmDeleteId.value = sessionId;
}

function onInputKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && (inputValue.value.trim() || pendingImage.value) && !inputDisabled.value) send(inputValue.value);
}

function onPickIndividual(v: PickedIndividual) {
  bound.value = v;
  pickOpen.value = false;
}

function onArchived(picked: PickedIndividual) {
  archiveFor.value = null;
  archiveTip.value = `已记入 ${picked.ear_tag_no} 的个体档案`;
}

// 流式中不禁用输入（2026-10-14）：允许提前组织补位信息，回车即排队
const inputDisabled = computed(() => historyLoading.value);
</script>

<template>
  <div class="flex h-full">
    <!-- 左侧会话历史列表（240px；<768 隐藏，对话页降级单栏） -->
    <div class="hidden md:flex w-[240px] bg-card border-r border-border flex-col shrink-0">
      <div class="p-4 border-b border-border">
        <button
          @click="newChat"
          :disabled="streaming"
          class="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-primary text-white rounded-[10px] text-[14px] font-medium hover:bg-primary-600 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
        >
          <Plus :size="16" :stroke-width="1.5" />
          新建对话
        </button>
      </div>
      <div class="flex-1 overflow-y-auto py-2">
        <p v-if="sessionsFetching && sessions.length === 0" class="px-4 py-6 text-[12.5px] text-text-tertiary text-center">
          加载中…
        </p>
        <p v-if="!sessionsFetching && sessions.length === 0" class="px-4 py-6 text-[12.5px] text-text-tertiary text-center leading-[1.8]">
          暂无会话
          <br />
          描述症状开始第一次问诊
        </p>
        <div
          v-for="s in sessions"
          :key="s.session_id"
          role="button"
          tabindex="0"
          @click="selectSession(s.session_id)"
          @keydown="sessionKeydown($event, s.session_id)"
          :class="
            cn(
              'group relative w-full text-left px-4 py-3 transition-colors cursor-pointer',
              activeSessionId === s.session_id ? 'bg-primary-50 border-r-2 border-primary' : 'hover:bg-bg-subtle',
            )
          "
        >
          <div class="flex items-center gap-2 mb-1">
            <MessageSquare :size="14" class="text-text-tertiary shrink-0" :stroke-width="1.5" />
            <span class="text-[14px] font-medium text-text-primary truncate pr-6">{{ s.title }}</span>
          </div>
          <span class="text-[11px] text-text-disabled pl-[22px] mt-0.5 block">
            {{ s.update_time.slice(5, 16).replace('T', ' ') }}
          </span>
          <!-- 删除入口：悬停/选中时显现；点击后内联二次确认（06 规范危险操作模式） -->
          <div
            v-if="confirmDeleteId === s.session_id"
            class="absolute inset-0 z-10 flex items-center justify-end gap-2 px-3"
            style="background-color: var(--card, #fff)"
            @click.stop
          >
            <span class="text-[12px] text-text-secondary mr-auto">删除此会话？</span>
            <button
              @click="removeSession(s.session_id)"
              class="h-[28px] px-3 rounded-[8px] text-[12.5px] font-medium text-white transition-colors"
              style="background-color: var(--danger)"
            >
              删除
            </button>
            <button
              @click="confirmDeleteId = null"
              class="h-[28px] px-3 rounded-[8px] text-[12.5px] bg-bg-subtle text-text-secondary hover:bg-bg-inset transition-colors"
            >
              取消
            </button>
          </div>
          <button
            v-else
            @click.stop="openDeleteConfirm(s.session_id)"
            :class="
              cn(
                'absolute top-2.5 right-2.5 p-1.5 rounded-[8px] text-text-disabled hover:bg-bg-inset hover:text-[var(--danger)] transition-all',
                activeSessionId === s.session_id ? 'opacity-70' : 'opacity-0 group-hover:opacity-100',
              )
            "
            :aria-label="'删除会话 ' + s.title"
            title="删除会话"
          >
            <Trash2 :size="13" :stroke-width="1.5" />
          </button>
        </div>
      </div>
    </div>

    <!-- 右侧对话区 -->
    <div class="flex-1 flex flex-col min-w-0 bg-bg-page">
      <div ref="scrollRef" class="flex-1 overflow-y-auto flex flex-col px-6 md:px-8 py-6">
        <div :class="cn('max-w-[800px] mx-auto space-y-4', messages.length > 0 && 'mt-auto')">
          <!-- 空态（新对话，按主物种出引导） -->
          <div v-if="messages.length === 0 && !historyLoading" class="text-center pt-16">
            <Stethoscope :size="40" class="mx-auto mb-3" style="color: var(--text-disabled)" :stroke-width="1.5" />
            <p class="text-[15px] text-text-secondary">{{ empty.title }}</p>
            <p class="text-[12.5px] mt-1.5" style="color: var(--text-tertiary)">
              例如：咳嗽喘气、腹泻拉稀、不吃食、产后瘫痪…
            </p>
            <div class="flex flex-wrap justify-center gap-2 mt-5">
              <button
                v-for="q in empty.examples"
                :key="q"
                @click="!streaming && send(q)"
                :disabled="streaming"
                class="px-3 py-1.5 rounded-[10px] border border-border bg-card text-[12.5px] text-text-secondary hover:border-primary hover:text-primary transition-colors disabled:opacity-50"
              >
                {{ q }}
              </button>
            </div>
          </div>

          <template v-for="msg in messages" :key="msg.id">
            <div v-if="msg.role === 'user'" class="flex justify-end">
              <div class="max-w-[70%] bg-primary-100 rounded-[16px] rounded-br-[4px] px-4 py-3">
                <img
                  v-if="msg.image"
                  :src="msg.image"
                  alt="患畜照片"
                  class="rounded-[10px] border border-primary-700/20 mb-2 max-h-48 object-contain"
                />
                <p v-if="msg.text" class="text-[14px] text-primary-900 leading-[1.7] whitespace-pre-line">{{ msg.text }}</p>
                <span class="text-[11px] text-primary-700/60 mt-1 block text-right">{{ msg.time }}</span>
              </div>
            </div>
            <div v-else class="max-w-[85%] w-full">
              <RiskBadge v-if="msg.risk" :risk="msg.risk" :unresolved="msg.penUnresolved" />

              <!-- 50001 失败：错误气泡 + 重试（EX-03） -->
              <div
                v-if="msg.failed"
                class="bg-card rounded-[12px] rounded-bl-[4px] p-4 flex items-center justify-between gap-3"
                :style="{ boxShadow: 'var(--e1)', border: '1px solid rgba(220,80,80,.25)' }"
              >
                <div class="flex items-center gap-2 text-[13.5px]" style="color: var(--danger)">
                  <AlertTriangle :size="15" :stroke-width="1.8" />
                  {{ msg.text ?? 'AI 服务暂时不可用，请稍后重试' }}
                </div>
                <button
                  @click="retrySend"
                  :disabled="streaming"
                  class="shrink-0 h-[30px] px-3 rounded-[8px] text-[12.5px] bg-primary text-white hover:bg-primary-600 transition-colors flex items-center gap-1.5 disabled:opacity-50"
                >
                  <RotateCcw :size="13" :stroke-width="1.8" />
                  重试
                </button>
              </div>
              <!-- 4xxxx 拒绝卡片（EX-04 知识库无结果 / 个体或圈舍归属不符），明确不臆测、不重试 -->
              <div
                v-else-if="msg.code"
                class="bg-card rounded-[12px] rounded-bl-[4px] p-4 flex items-start gap-2.5"
                :style="{ boxShadow: 'var(--e1)', border: '1px solid rgba(232,162,61,.3)' }"
              >
                <ShieldAlert :size="16" :stroke-width="1.8" class="mt-0.5 shrink-0" style="color: var(--warning)" />
                <p class="text-[13.5px] text-text-secondary leading-[1.7]">{{ msg.text }}</p>
              </div>
              <!-- 分节结构化报告（诊断/用药/饲料建议） -->
              <div v-else-if="msg.sections && msg.sections.length > 0" class="space-y-3">
                <div
                  v-for="(sec, i) in msg.sections"
                  :key="i"
                  class="bg-card rounded-[12px] p-5"
                  style="box-shadow: var(--e1)"
                >
                  <div class="flex items-center gap-2 mb-3">
                    <div class="w-6 h-6 rounded-[6px] bg-primary-50 flex items-center justify-center">
                      <component :is="sectionIcon(sec.key)" :size="14" class="text-primary" :stroke-width="1.5" />
                    </div>
                    <h4 class="text-[14px] font-semibold text-text-primary">{{ sec.title }}</h4>
                  </div>
                  <div class="text-[14px] text-text-secondary leading-[1.7] whitespace-pre-line">
                    <ThinkingRow
                      v-if="msg.streaming && i === msg.sections.length - 1 && !sec.text"
                      :stage="sec.title ? `正在生成「${sec.title}」…` : undefined"
                    />
                    <template v-else>
                      <MarkdownText :text="sec.text" />
                      <StreamingCursor v-if="msg.streaming && i === msg.sections.length - 1" />
                    </template>
                  </div>
                  <p
                    v-if="sec.source && !msg.streaming"
                    class="text-[11.5px] mt-3 pt-2.5 border-t border-border"
                    style="color: var(--text-tertiary)"
                  >
                    依据来源：{{ sec.source }}
                  </p>
                </div>
              </div>
              <!-- 纯文本回复（引导话术/澄清/拦截） -->
              <div v-else class="bg-card rounded-[12px] rounded-bl-[4px] p-4" style="box-shadow: var(--e1)">
                <ThinkingRow v-if="msg.streaming && !msg.text" :stage="msg.stage" />
                <p v-else class="text-[14px] text-text-secondary leading-[1.7] whitespace-pre-line">
                  {{ msg.text ?? '' }}<StreamingCursor v-if="msg.streaming" />
                </p>
              </div>
              <div v-if="!msg.streaming && !msg.failed" class="flex items-center gap-2 mt-1.5 flex-wrap">
                <span class="text-[11px] text-text-disabled">AI 助手 · {{ msg.time }}</span>
                <span
                  v-if="msg.individual"
                  class="inline-flex items-center gap-1 px-2 py-0.5 rounded-[6px] bg-primary-50 text-primary-700 text-[11.5px] font-medium"
                >
                  <Tag :size="11" :stroke-width="1.8" />
                  {{ msg.individual.ear_tag_no }}
                </span>
                <button
                  v-if="isConsultResult(msg)"
                  @click="archiveFor = msg"
                  class="inline-flex items-center gap-1 px-2 py-0.5 rounded-[6px] text-[11.5px] text-text-secondary bg-bg-subtle hover:bg-bg-inset transition-colors"
                >
                  <NotebookPen :size="11" :stroke-width="1.8" />
                  记入个体档案
                </button>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- 底部固定免责声明条（FR-08 三处常驻之一） -->
      <div
        class="px-8 py-2 border-t"
        :style="{ backgroundColor: 'rgba(232,162,61,0.05)', borderColor: 'rgba(232,162,61,0.2)' }"
      >
        <p class="text-[12px] text-text-tertiary text-center flex items-center justify-center gap-1.5">
          <ShieldAlert :size="12" style="color: var(--warning)" :stroke-width="1.5" />
          本系统 AI 建议仅作辅助参考，不能替代执业兽医诊断与处方。
        </p>
      </div>

      <!-- 输入区（FR-11 图像问诊：相机按钮 + 待发缩略图，图片在浏览器压缩后才进请求体） -->
      <div class="px-6 md:px-8 py-4 bg-card border-t border-border">
        <div class="max-w-[800px] mx-auto">
          <p v-if="imageErr" class="text-[12px] mb-2" style="color: var(--danger)">{{ imageErr }}</p>
          <div v-if="pendingImage" class="relative inline-block mb-2">
            <img
              :src="pendingImage"
              alt="待发送图片"
              class="w-20 h-20 object-cover rounded-[10px] border border-border-strong"
            />
            <button
              type="button"
              @click="pendingImage = null"
              class="absolute -top-1.5 -right-1.5 w-5 h-5 rounded-full bg-bg-subtle border border-border flex items-center justify-center text-text-secondary hover:text-text-primary"
              aria-label="移除图片"
            >
              <X :size="11" :stroke-width="2" />
            </button>
          </div>
          <span
            v-if="bound"
            class="inline-flex items-center gap-1.5 pl-2.5 pr-1.5 py-1 mb-2 rounded-[8px] bg-primary-50 text-primary-700 text-[12.5px] font-medium"
          >
            <Tag :size="12" :stroke-width="1.8" />
            本轮绑定 {{ bound.ear_tag_no }} · {{ bound.pen_name }}
            <button
              type="button"
              @click="bound = null"
              class="p-0.5 rounded-[4px] hover:bg-primary-100"
              aria-label="解除个体绑定"
            >
              <X :size="11" :stroke-width="2" />
            </button>
          </span>
          <div class="flex items-center gap-3">
            <input ref="fileRef" type="file" accept="image/*" class="hidden" @change="onFileChange" />
            <button
              type="button"
              @click="pickOpen = true"
              :disabled="inputDisabled"
              :class="
                cn(
                  'h-10 shrink-0 px-3 rounded-[10px] border flex items-center gap-1.5 text-[13px] transition-colors disabled:opacity-50 disabled:cursor-not-allowed',
                  bound
                    ? 'border-primary text-primary-700 bg-primary-50'
                    : 'border-border-strong text-text-secondary hover:text-primary hover:border-primary',
                )
              "
              :title="bound ? '改绑其他个体' : '绑定到具体个体（耳标）后再问诊'"
            >
              <Tag :size="14" :stroke-width="1.5" />
              {{ bound ? bound.ear_tag_no : '绑定个体' }}
            </button>
            <button
              type="button"
              @click="fileRef?.click()"
              :disabled="inputDisabled || imageBusy || !!pendingImage"
              class="w-10 h-10 shrink-0 rounded-[10px] border border-border-strong flex items-center justify-center text-text-secondary hover:text-primary hover:border-primary transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              aria-label="添加图片"
              title="上传患畜照片辅助问诊（自动压缩至 512px 内）"
            >
              <RefreshCw v-if="imageBusy" :size="16" :stroke-width="1.5" class="animate-spin" />
              <Camera v-else :size="17" :stroke-width="1.5" />
            </button>
            <input
              type="text"
              v-model="inputValue"
              maxlength="500"
              @keydown="onInputKeydown"
              :disabled="inputDisabled"
              :placeholder="
                streaming ? 'AI 回复中，可继续输入，回车排队发送…' : '描述症状、输入问题，或点左侧相机上传照片…'
              "
              class="flex-1 px-4 py-2.5 rounded-[10px] border border-border-strong bg-bg-page text-[14px] text-text-primary placeholder:text-text-tertiary outline-none focus:border-primary focus:ring-1 focus:ring-primary/20 transition-colors disabled:opacity-60"
            />
            <button
              @click="send(inputValue)"
              :disabled="(!inputValue.trim() && !pendingImage) || inputDisabled"
              :class="
                cn(
                  'w-10 h-10 rounded-[10px] flex items-center justify-center transition-colors',
                  (inputValue.trim() || pendingImage) && !inputDisabled
                    ? 'bg-primary text-white hover:bg-primary-600'
                    : 'bg-bg-subtle text-text-disabled cursor-not-allowed',
                )
              "
              aria-label="发送"
            >
              <RefreshCw v-if="streaming" :size="16" :stroke-width="1.5" class="animate-spin" />
              <Send v-else :size="18" :stroke-width="1.5" />
            </button>
          </div>
        </div>
      </div>
    </div>

    <PickIndividualDialog v-if="pickOpen" @close="pickOpen = false" @pick="onPickIndividual" />
    <RecordToIndividualDialog
      v-if="archiveFor"
      :default-pick="archiveDefaultPick"
      :prefill="archivePrefill"
      @close="archiveFor = null"
      @saved="onArchived"
    />
    <div
      v-if="archiveTip"
      class="fixed bottom-24 left-1/2 -translate-x-1/2 z-[60] px-4 py-2.5 rounded-[10px] bg-[#2A332C] text-white text-[13px]"
      :style="{ boxShadow: 'var(--e3)' }"
      role="status"
    >
      {{ archiveTip }}
    </div>
  </div>
</template>
