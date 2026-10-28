// AI 问诊 API — 按《03-API接口设计》9.1~9.3 / F05 PRD FR-02/08 契约封装
// - 流式对话：POST /agent/chat（text/event-stream，事件 intent/token/section/done/error）
//   走 fetch ReadableStream 解析，事件回调签名由页面消费，传输层不再有二选择。
// - 会话历史：GET /agent/sessions、GET /agent/sessions/{id}/messages

import { API_BASE, apiFetch, getToken, ApiError } from './client';

/** 问诊请求契约（03-API 9.1）。`pen_id` 为圈舍定位（EX-C1 澄清链路用），`individual_id` 为 F14 个体绑定 */
export interface AgentChatParams {
  session_id?: number;
  message: string;
  pen_id?: number;
  /** F14：绑定到具体个体（耳标）。后端会按该个体所在圈定位并把结论回写台账 */
  individual_id?: number;
  /** FR-11 图像问诊：data URL 图片（前端已压缩 ≤512px JPEG），用于视觉体征识别 */
  image?: string;
}

export interface AgentSessionItem {
  session_id: number;
  title: string;
  update_time: string;
}

export interface AgentMessageItem {
  role: 'user' | 'assistant';
  content: string;
  meta: {
    risk_level?: string;
    intent?: string;
    clarify?: boolean;
    /** 本轮按物种通用口径作答、未绑定圈舍（先答后问） */
    pen_unresolved?: boolean;
    /** F14：本轮绑定的个体（历史回看用它复原"记入档案"的预选） */
    individual?: { individual_id: number; ear_tag_no: string; pen_name: string };
    code?: number;
    /** FR-11：用户轮的压缩图 data URL（≤512px JPEG，≤300KB），用于气泡回显 */
    image?: string;
    sections?: { title: string; text: string; source?: string }[];
  };
  create_time: string;
}

/** 流式事件回调：event ∈ intent | token | section | done | error（01-架构六节） */
export type ChatEventHandler = (event: string, data: Record<string, unknown>) => void;

/** 解析单个 SSE 帧（event:/data: 两行结构） */
function parseSseFrame(block: string): { event: string; data: Record<string, unknown> } | null {
  let event = '';
  let data = '';
  for (const line of block.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim();
    else if (line.startsWith('data:')) data += line.slice(5).trim();
  }
  if (!event && !data) return null;
  let parsed: Record<string, unknown> = {};
  try {
    parsed = data ? (JSON.parse(data) as Record<string, unknown>) : {};
  } catch {
    parsed = { text: data };
  }
  return { event: event || 'message', data: parsed };
}

/**
 * FR-08 流式问诊：POST /agent/chat 读响应流，按空行分帧逐帧回调直至 done/error。
 * 会话/圈舍/个体的归属校验在开流前完成，失败返回的是 JSON 信封（HTTP 200），
 * 这里按 content-type 区分：不是 event-stream 就当业务错误抛出，否则会渲染成空气泡。
 */
export async function apiChatStream(params: AgentChatParams, onEvent: ChatEventHandler): Promise<void> {
  const dispatch = (frameText: string) => {
    const parsed = parseSseFrame(frameText);
    if (parsed) onEvent(parsed.event, parsed.data);
  };

  const res = await fetch(`${API_BASE}/agent/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
    },
    body: JSON.stringify(params),
  });
  const ctype = res.headers.get('content-type') ?? '';
  if (!ctype.includes('text/event-stream')) {
    if (!res.ok) throw new ApiError(50001, `服务响应异常（HTTP ${res.status}）`);
    let payload: { code?: number; message?: string } = {};
    try {
      payload = (await res.json()) as { code?: number; message?: string };
    } catch {
      throw new ApiError(50001, '服务响应格式异常，请稍后重试');
    }
    throw new ApiError(Number(payload.code) || 50001, payload.message || '问诊请求被拒绝');
  }
  if (!res.body) throw new ApiError(50001, '连接失败：响应无数据流');

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      dispatch(block);
    }
  }
  if (buffer.trim()) dispatch(buffer);
}

/** FR-08 会话列表（时间倒序） */
export function apiListSessions() {
  return apiFetch<{ items: AgentSessionItem[] }>('GET', '/agent/sessions');
}

/** FR-08 会话消息（越权 40302 由服务端拦截） */
export function apiSessionMessages(sessionId: number) {
  return apiFetch<{ items: AgentMessageItem[] }>('GET', `/agent/sessions/${sessionId}/messages`);
}

/** 会话删除（连同轮次消息；不存在 40401 / 越权 40302 由服务端拦截） */
export function apiDeleteSession(sessionId: number) {
  return apiFetch<{ deleted: boolean }>('DELETE', `/agent/sessions/${sessionId}`);
}
