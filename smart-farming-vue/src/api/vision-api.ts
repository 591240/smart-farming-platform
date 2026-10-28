// F09 视觉告警 API — 03-API 7.3 GET /vision/events 契约 + POST /vision/simulate（mock 演示注入）
// - 事件 payload 与边缘网关完全同构（07-协议第四节），注入走同一消费链（F09 PRD FR-04）；
// - 免责固定文案 VISION_DISCLAIMER 从引擎层导出，三处展示共用同一常量（逐字一致硬约束，RK-4）；
// - /vision/simulate 为模拟器专属路由（vision_sim.py CLI 等价能力），切真后随真实网关注入下线删除。

import { apiFetch } from './client';

export interface VisionEventItem {
  result_id: number;
  event_type: string;
  event_label: string;
  confidence: number;
  /** 应用层分级（07-协议：[0.5,0.7) low / ≥0.7 mid / death ≥0.5 high） */
  level: 'low' | 'mid' | 'high';
  collect_time: string;
  pen_name: string;
  /** 扩展字段：弹窗按设备过滤时间线用（文档同步项①展开时注明） */
  device_sn: string;
}

export interface VisionEventsParams {
  pen_id?: number;
  farm_id?: number;
  from?: string; // ISO 或 yyyy-MM-dd
  to?: string;
  page?: number;
  page_size?: number;
}

export function apiListVisionEvents(params?: VisionEventsParams) {
  const q = new URLSearchParams();
  if (params?.pen_id) q.set('pen_id', String(params.pen_id));
  if (params?.farm_id) q.set('farm_id', String(params.farm_id));
  if (params?.from) q.set('from', params.from);
  if (params?.to) q.set('to', params.to);
  q.set('page', String(params?.page ?? 1));
  q.set('page_size', String(params?.page_size ?? 20));
  return apiFetch<{ total: number; page: number; page_size: number; items: VisionEventItem[] }>(
    'GET',
    `/vision/events?${q.toString()}`,
  );
}

export type VisionAnomaly = 'none' | 'low_confidence' | 'duplicate' | 'bad_payload';

export interface VisionSimulateResult {
  ok: boolean;
  dropped?: boolean;
  reason?: string;
  result?: { result_id: number; level: 'low' | 'mid' | 'high'; alert_id: number | null };
  dedup?: { msg_id_replay_dropped: boolean };
  message: string;
}

/** FR-04 演示注入（走 FR-01 同一校验链：去重/回表/枚举/低置信丢弃，非旁路直写） */
export function apiInjectVisionEvent(body: {
  device_id: number;
  event_type: string;
  confidence: number;
  anomaly?: VisionAnomaly;
}) {
  return apiFetch<VisionSimulateResult>('POST', '/vision/simulate', body);
}

// ===== 常量与枚举（与引擎消费链同源，见 lib/vision-constants.ts）=====
export { VISION_EVENT_TYPES, VISION_EVENT_META, VISION_DISCLAIMER } from '@/lib/vision-constants';
export type { VisionEventType } from '@/lib/vision-constants';
