// F14 个体耳标档案 API — 按《03-API接口设计》十六节 16.1~16.5 契约封装
// 强关联对账：在栏个体数 == 圈舍存栏快照；入栏/离栏/转群走存栏流水接口并携带耳标

import { apiFetch } from './client';

export type IndividualStatus = 'active' | 'sold' | 'dead' | 'removed';
export type IndividualGender = 'male' | 'female';

export const INDIVIDUAL_STATUS_LABEL: Record<IndividualStatus, string> = {
  active: '在栏',
  sold: '已出栏',
  dead: '已死淘',
  removed: '已调减',
};

export interface IndividualItem {
  individual_id: number;
  ear_tag_no: string;
  pen_id: number;
  pen_name: string;
  gender: IndividualGender;
  gender_label: string;
  variety_code: string | null;
  variety_name: string | null;
  birth_date: string | null;
  source: string;
  status: IndividualStatus;
  status_label: string;
  exit_note: string;
  exit_time: string | null;
  remark: string;
  traits: string; // F17：外貌/毛色特征
  lineage: string; // F17：血缘备注
  create_time: string;
}

// ----- F17 个体档案详情（一畜一档）-----

export type IndividualEventType = 'register' | 'rename' | 'transfer' | 'exit' | 'weight' | 'immunization' | 'treatment' | 'note';
export const MANUAL_EVENT_TYPES: IndividualEventType[] = ['weight', 'immunization', 'treatment', 'note'];

export interface IndividualEventItem {
  event_id: number;
  event_type: IndividualEventType;
  event_type_label: string;
  event_date: string;
  payload: Record<string, string | number>;
  manual: boolean; // 手动记录可纠错删除；流水联动档案不可删
  create_time: string;
}

export interface WeightPoint {
  date: string;
  weight_kg: number;
  daily_gain: number | null; // 相邻两点日增重（kg/日）
}

export interface IndividualDetail {
  individual: IndividualItem;
  events: IndividualEventItem[];
  weight_series: WeightPoint[];
}

/** 档案详情聚合（信息卡 + 时间线 ≤200 + 体重曲线） */
export function apiIndividualDetail(individualId: number) {
  return apiFetch<IndividualDetail>('GET', `/individual/${individualId}/detail`);
}

export type IndividualEventPayload = {
  event_type: Extract<IndividualEventType, 'weight' | 'immunization' | 'treatment' | 'note'>;
  event_date: string;
  weight_kg?: number;
  vaccine_name?: string;
  dose?: string;
  next_date?: string;
  symptom?: string;
  diagnosis?: string;
  medication?: string;
  outcome?: string;
  vet_name?: string;
  content?: string;
  note?: string;
};

/** 记一笔（手动事件；payload 按类型校验在服务端） */
export function apiCreateIndividualEvent(individualId: number, body: IndividualEventPayload) {
  return apiFetch<{ event_id: number; event_type: string; event_date: string }>('POST', `/individual/${individualId}/event`, body);
}

/** 纠错删除手动事件（流水联动档案事件服务端拒绝） */
export function apiDeleteIndividualEvent(eventId: number) {
  return apiFetch<{ event_id: number; deleted: boolean }>('DELETE', `/individual/event/${eventId}`);
}

/** 逐圈对账（diff = 存栏快照 − 在栏个体；>0 需补齐） */
export interface PenReconcileItem {
  pen_id: number;
  pen_name: string;
  stock_total: number;
  active_total: number;
  diff: number;
}

export interface IndividualListResult {
  summary: { stock_total: number; active_total: number; diff: number };
  pens: PenReconcileItem[];
  total: number;
  page: number;
  page_size: number;
  items: IndividualItem[];
}

export function apiListIndividuals(params: {
  pen_id?: number;
  status?: IndividualStatus | 'all';
  keyword?: string;
  page?: number;
  page_size?: number;
}) {
  const q = new URLSearchParams();
  if (params.pen_id) q.set('pen_id', String(params.pen_id));
  if (params.status) q.set('status', params.status);
  if (params.keyword) q.set('keyword', params.keyword);
  if (params.page) q.set('page', String(params.page));
  if (params.page_size) q.set('page_size', String(params.page_size));
  return apiFetch<IndividualListResult>('GET', `/individual/list?${q.toString()}`);
}

/** 单个建档（差额头寸内补录；新入栏动物请走「调整存栏」流水） */
export function apiCreateIndividual(body: {
  pen_id: number;
  ear_tag_no: string;
  gender: IndividualGender;
  birth_date?: string;
  source?: string;
  remark?: string;
  traits?: string;
  lineage?: string;
}) {
  return apiFetch<{ individual_id: number; ear_tag_no: string }>('POST', '/individual', body);
}

export function apiUpdateIndividual(
  individualId: number,
  body: { ear_tag_no?: string; birth_date?: string | null; source?: string; remark?: string; traits?: string; lineage?: string },
) {
  return apiFetch<{ individual_id: number; updated: boolean }>('PUT', `/individual/${individualId}`, body);
}

/** 纠错删档（仅限在栏个体） */
export function apiDeleteIndividual(individualId: number) {
  return apiFetch<{ individual_id: number; deleted: boolean }>('DELETE', `/individual/${individualId}`);
}

/** 一键补齐（历史差额收敛；只建个体档案，存栏不变） */
export function apiAutoFillIndividuals(penId: number, prefix?: string) {
  return apiFetch<{
    created_male: number;
    created_female: number;
    active_total: number;
    stock_total: number;
    message: string;
  }>('POST', `/pen/${penId}/individuals/auto-fill`, prefix ? { prefix } : {});
}

/** 批量建档：同圈同性别连号循环单个建档（强关联头寸逐个校验，任一失败即止） */
export async function apiBatchCreateIndividuals(body: {
  pen_id: number;
  gender: IndividualGender;
  prefix: string;
  start_no: number;
  count: number;
  birth_date?: string;
  source?: string;
  remark?: string;
}) {
  const created: { individual_id: number; ear_tag_no: string }[] = [];
  for (let i = 0; i < body.count; i++) {
    const tagNo = `${body.prefix}-${body.gender === 'male' ? 'M' : 'F'}${String(body.start_no + i).padStart(3, '0')}`;
    created.push(
      await apiCreateIndividual({
        pen_id: body.pen_id,
        ear_tag_no: tagNo,
        gender: body.gender,
        birth_date: body.birth_date,
        source: body.source || '批量建档',
        remark: body.remark,
      }),
    );
  }
  return { created, tag_nos: created.map((c) => c.ear_tag_no) };
}
