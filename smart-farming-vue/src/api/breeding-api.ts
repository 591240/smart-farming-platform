// 全生命周期管理与出栏休药期 API — 按《03-API接口设计》五节（5.1~5.5、5.3.1）/ F07 PRD FR-01~09 封装
// - 母畜档案：POST/GET/PUT/DELETE /breeding-female（PUT 禁改 breed_status，40003）
// - 生命周期事件：POST /lifecycle/event（表驱动状态机，非法跳变 40003 EX-12）
// - 提醒：GET /lifecycle/reminders?farm_id=（惰性幂等生成）+ POST /reminder/{id}/handle
// - 用药：POST/GET /medicine/usage（withdrawal_until 服务端算；GET 为本文档具体化接口）
// - 出栏：POST /pen/{pen_id}/slaughter（休药期 40001 三要素 → force+force_note 留痕 → 三表联动）

import { apiFetch } from './client';

// ===== 契约类型 =====

export type BreedStatus = 'open' | 'bred' | 'pregnant' | 'nursing' | 'culled';

export interface BreedingFemaleItem {
  female_id: number;
  pen_id: number;
  pen_name: string;
  tag_no: string;
  variety_code: string | null;
  parity: number;
  breed_status: BreedStatus;
  breed_status_label: string;
  breed_date: string | null;
  due_date: string | null;
  remark: string;
  create_time: string;
}

/** POST /lifecycle/event 开放事件类型（slaughter 出栏仅由出栏登记内部触发） */
export type LifecycleEventType =
  | 'breeding'
  | 'pregnancy_check'
  | 'abortion'
  | 'birth'
  | 'weaning'
  | 'castration'
  | 'dehorning'
  | 'stage_change'
  | 'cull';

export interface LifecycleEventResult {
  event_id: number;
  female_status?: BreedStatus;
  due_date?: string | null;
  parity?: number;
}

export type ReminderType = 'due_date' | 'weaning' | 'vaccine';
export type ReminderStatus = 'pending' | 'done' | 'expired';

export interface ReminderItem {
  reminder_id: number;
  reminder_type: ReminderType;
  title: string;
  content: string;
  remind_date: string;
  pen_name: string | null;
  tag_no: string | null;
  status: ReminderStatus;
  handled_note: string | null;
  /** 距应提醒日天数（负=已过期 N 天） */
  days_left: number;
}

export interface MedicineUsageItem {
  usage_id: number;
  med_id: number;
  med_name: string;
  usage_date: string;
  dosage: string;
  withdrawal_until: string;
  withdrawal_period: number | null;
  /** 休药期剩余天数（≤0=已过休药期） */
  withdrawal_left_days: number;
  operator_note: string;
}

export interface SlaughterBlockingMed {
  med_name: string;
  usage_date: string;
  withdrawal_until: string;
  left_days: number;
}

export interface SlaughterResult {
  change_id: number;
  withdrawal_check: 'passed' | 'forced';
  record_id: number;
}

// ===== 母畜档案（FR-01）=====

export function apiCreateBreedingFemale(params: {
  pen_id: number;
  tag_no: string;
  variety_code?: string;
  parity?: number;
  breed_status?: BreedStatus;
  breed_date?: string;
  remark?: string;
}) {
  return apiFetch<{
    female_id: number;
    breed_status: BreedStatus;
    breed_status_label: string;
    due_date: string | null;
  }>('POST', '/breeding-female', params);
}

export function apiListBreedingFemales(params?: { pen_id?: number; farm_id?: number; breed_status?: BreedStatus; page?: number; page_size?: number }) {
  const q = new URLSearchParams();
  if (params?.pen_id) q.set('pen_id', String(params.pen_id));
  if (params?.farm_id) q.set('farm_id', String(params.farm_id));
  if (params?.breed_status) q.set('breed_status', params.breed_status);
  q.set('page', String(params?.page ?? 1));
  q.set('page_size', String(params?.page_size ?? 100));
  return apiFetch<{ total: number; page: number; page_size: number; items: BreedingFemaleItem[] }>(
    'GET',
    `/breeding-female?${q.toString()}`,
  );
}

export function apiUpdateBreedingFemale(
  femaleId: number,
  params: { tag_no?: string; variety_code?: string; parity?: number; remark?: string },
) {
  return apiFetch<{ female_id: number; updated: boolean }>('PUT', `/breeding-female/${femaleId}`, params);
}

export function apiDeleteBreedingFemale(femaleId: number) {
  return apiFetch<{ female_id: number; deleted: boolean }>('DELETE', `/breeding-female/${femaleId}`);
}

// ===== 生命周期事件（FR-02）=====

export function apiCreateLifecycleEvent(params: {
  event_type: LifecycleEventType;
  female_id?: number;
  pen_id?: number;
  event_date?: string;
  detail?: Record<string, number | string>;
  remark?: string;
}) {
  return apiFetch<LifecycleEventResult>('POST', '/lifecycle/event', params);
}

// ===== 提醒（FR-03/FR-04）=====

export function apiListReminders(farmId: number) {
  return apiFetch<{ items: ReminderItem[] }>('GET', `/lifecycle/reminders?farm_id=${farmId}`);
}

export function apiHandleReminder(reminderId: number, params: { action: 'done'; note?: string }) {
  return apiFetch<{ reminder_id: number; status: ReminderStatus }>('POST', `/reminder/${reminderId}/handle`, params);
}

// ===== 用药记录（FR-05）=====

export function apiCreateMedicineUsage(params: {
  pen_id: number;
  med_id: number;
  usage_date?: string;
  dosage?: string;
  operator_note?: string;
}) {
  return apiFetch<{ usage_id: number; withdrawal_until: string }>('POST', '/medicine/usage', params);
}

export function apiListMedicineUsages(penId: number, days = 90) {
  return apiFetch<{ items: MedicineUsageItem[] }>('GET', `/medicine/usage?pen_id=${penId}&days=${days}`);
}

/** 兽药库列表（本文档具体化接口：03-API 无 farmer 侧药库查询；违禁药已过滤） */
export function apiListMedicineLib() {
  return apiFetch<{
    items: { med_id: number; med_name: string; withdrawal_period: number; disease_name: string | null; species_code?: 'pig' | 'cattle' | 'sheep' }[];
  }>('GET', '/medicine/list');
}

// ===== 出栏登记（FR-06）=====

export function apiSlaughter(
  penId: number,
  params: {
    male_count: number;
    female_count: number;
    avg_weight_kg?: number;
    destination?: string;
    force?: boolean;
    force_note?: string;
  },
) {
  return apiFetch<SlaughterResult>('POST', `/pen/${penId}/slaughter`, params);
}
