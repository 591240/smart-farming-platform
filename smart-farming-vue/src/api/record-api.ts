// 台账查询与导出 API — 按《03-API接口设计》10.1/10.2 / F04 PRD FR-01/02 契约封装
// - 列表：GET /record/list（farm/pen/类型/时间筛选 + 03-API 1.4 分页）
// - 导出：GET /record/export（同筛选条件；返回 { filename, mime, content } 由前端包 Blob 下载）

import { apiFetch } from './client';

export interface LedgerExtColumn {
  field: string;
  label: string;
}

export interface LedgerRecordItem {
  record_id: number;
  record_type: string;
  summary: string;
  create_time: string;
  pen_name: string;
  record_ext: Record<string, number | string>;
  /** F10 决策 2：关联复核单状态读侧派生（timeout → 展示「未经兽医复核」，不回写台账） */
  vet_review_id?: number;
  vet_review_status?: 'pending' | 'approved' | 'rejected' | 'timeout';
}

export interface LedgerListResult {
  total: number;
  page: number;
  page_size: number;
  ext_columns: LedgerExtColumn[];
  items: LedgerRecordItem[];
}

export interface LedgerFilterParams {
  farm_id?: number;
  pen_id?: number;
  record_type?: string;
  /** yyyy-MM-dd（含全天，FR-01） */
  from?: string;
  to?: string;
  page?: number;
  page_size?: number;
}

function buildFilterQuery(params: LedgerFilterParams, withPage: boolean): string {
  const q = new URLSearchParams();
  if (params.farm_id) q.set('farm_id', String(params.farm_id));
  if (params.pen_id) q.set('pen_id', String(params.pen_id));
  if (params.record_type) q.set('record_type', params.record_type);
  if (params.from) q.set('from', params.from);
  if (params.to) q.set('to', params.to);
  if (withPage) {
    q.set('page', String(params.page ?? 1));
    q.set('page_size', String(params.page_size ?? 20));
  }
  return q.toString();
}

// FR-01 台账多条件查询（时间倒序；越权 40302 / 非法类型 42201 由服务端拦截）
export function apiListRecords(params: LedgerFilterParams) {
  return apiFetch<LedgerListResult>('GET', `/record/list?${buildFilterQuery(params, true)}`);
}

// FR-02 台账导出：触发浏览器下载，返回文件名（供成功 toast 展示）
// 契约：服务端返回 { filename, mime, content }（CSV + UTF-8 BOM），此处包装 Blob 下载。
// mock 与真后端目前同契约（backend/api/records.py 亦返回 JSON），故不再有文件流分支——
// 原 `USE_MOCK=false` 分支期望 Content-Disposition 文件流，而真后端返回 JSON，
// 全量切真时那条分支会静默拿到 undefined 文件名（01-架构 C-15，已消除）。
// F04 决策 2 若要改 xlsx 文件流，需与本函数、后端生成器三处同批改。
export async function apiExportRecords(params: LedgerFilterParams): Promise<string> {
  const query = buildFilterQuery(params, false);

  const { filename, mime, content } = await apiFetch<{
    filename: string;
    mime: string;
    content: string;
  }>('GET', `/record/export?${query}`);
  triggerDownload(new Blob([content], { type: mime }), filename);
  return filename;
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
