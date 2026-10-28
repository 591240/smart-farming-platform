// 场舍存栏 API — 按《03-API接口设计》4.1~4.4 / F02 PRD 七节契约封装
// F02 范围：farm/pen 增删改、存栏变动（五类）、转群（两笔联动）

import { apiFetch } from './client';
import type { UserFarm, UserPen } from './auth-api';
export type { UserFarm, UserPen };

export interface FarmPayload {
  farm_name: string;
  province: string;
  city: string;
  county: string;
  adcode: string;
  address?: string;
  contact_person?: string;
  phone?: string;
}

// FR-01 养殖场新增 / 编辑（编辑仅提交有变化字段即可）
export function apiCreateFarm(body: FarmPayload) {
  return apiFetch<{ farm_id: number; guide_done: boolean }>('POST', '/farm', body);
}

export function apiUpdateFarm(farmId: number, body: Partial<FarmPayload>) {
  return apiFetch<{ farm_id: number; updated: boolean }>('PUT', `/farm/${farmId}`, body);
}

// FR-02 养殖场逻辑删除（圈舍/流水/台账保留；guide_done 计算判定自动回退）
export function apiDeleteFarm(farmId: number) {
  return apiFetch<{ farm_id: number; deleted: boolean; guide_done: boolean }>(
    'DELETE',
    `/farm/${farmId}`,
  );
}

export interface PenPayload {
  farm_id: number;
  pen_name: string;
  variety_code: string | null;
  male_count: number;
  female_count: number;
  batch_stage: string | null;
}

// FR-03 圈舍新增（初始存栏 > 0 时服务端自动补 purchase 流水，FR-03 初始流水约定）
export function apiCreatePen(body: PenPayload) {
  return apiFetch<{ pen_id: number; change_id: number | null }>('POST', '/pen', body);
}

// FR-03 圈舍编辑（数量不可经编辑修改，快照只能由流水驱动）
export function apiUpdatePen(
  penId: number,
  body: { pen_name?: string; variety_code?: string | null; batch_stage?: string | null },
) {
  return apiFetch<{ pen_id: number; updated: boolean }>('PUT', `/pen/${penId}`, body);
}

// FR-04 圈舍逻辑删除（流水与台账保留）
export function apiDeletePen(penId: number) {
  return apiFetch<{ pen_id: number; deleted: boolean }>('DELETE', `/pen/${penId}`);
}

export type StockChangeType = 'birth' | 'death' | 'sale' | 'purchase' | 'manual';

// FR-06 存栏变动（birth/purchase 正录正存；death/sale 正录负存；manual ±，备注必填）
// F14 强关联耳标：入栏类必带 male_tags/female_tags（数量与 *_delta 一致）；
// 离栏类必带 exit_tag_nos（本圈在栏、公母分布与数量一致）。返回变动后的快照。
export function apiStockChange(
  penId: number,
  body: {
    change_type: StockChangeType;
    male_delta: number;
    female_delta: number;
    remark?: string;
    male_tags?: string[];
    female_tags?: string[];
    exit_tag_nos?: string[];
  },
) {
  return apiFetch<{ change_id: number; male_count: number; female_count: number }>(
    'POST',
    `/pen/${penId}/stock-change`,
    body,
  );
}

// FR-07 转群（同场、同物种；字段名为 male_count/female_count，与存栏变动的 *_delta 不同）
// F14 强关联耳标：tag_nos 必填（源圈在栏个体，公母分布与数量一致）
export function apiTransfer(
  penId: number,
  body: { target_pen_id: number; male_count: number; female_count: number; remark?: string; tag_nos: string[] },
) {
  return apiFetch<{
    out_change_id: number;
    in_change_id: number;
    source_pen: { male_count: number; female_count: number };
    target_pen: { male_count: number; female_count: number };
  }>('POST', `/pen/${penId}/transfer`, body);
}
