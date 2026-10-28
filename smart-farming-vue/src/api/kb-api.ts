// F16 知识库管理 API — 02-数据库 3.7 kb_document + 03-API 10.1 /admin/kb（admin 专属 40301）
// 上传必填来源与整理人（PRD 3.5.1 / 附录 E SOP 留痕）；上传即触发"切片重建索引"（chunk_count/indexed_at 回显）。

import { ApiError, API_BASE, apiFetch, getToken, handleUnauthorized } from './client';

export type KbDocType = 'disease' | 'nutrition';

export interface KbDocItem {
  doc_id: number;
  doc_name: string;
  doc_type: KbDocType;
  /** ''=公共法规文档（全物种可见） */
  species_code: 'pig' | 'cattle' | 'sheep' | '';
  retrieval_keywords: string[];
  risk_level: 'low' | 'mid' | 'high';
  med_names: string[];
  feed_advice: string;
  content_md: string;
  source: string;
  organizer: string;
  chunk_count: number;
  indexed_at: string;
  is_enable: boolean;
  create_time: string;
}

export function apiAdminKbList() {
  return apiFetch<{ total: number; items: KbDocItem[] }>('GET', '/admin/kb');
}

export interface KbUploadBody {
  doc_name: string;
  doc_type: KbDocType;
  /** ''=公共法规文档 */
  species_code: 'pig' | 'cattle' | 'sheep' | '';
  retrieval_keywords: string[];
  risk_level?: 'low' | 'mid' | 'high';
  med_names?: string[];
  feed_advice?: string;
  content_md?: string;
  source: string;
  organizer: string;
}

export function apiAdminKbUpload(body: KbUploadBody) {
  return apiFetch<{ doc_id: number; chunk_count: number; indexed_at: string }>('POST', '/admin/kb', body);
}

export function apiAdminKbSetEnable(docId: number, isEnable: boolean) {
  return apiFetch<{ doc_id: number; is_enable: boolean }>('PUT', `/admin/kb/${docId}`, { is_enable: isEnable });
}

export function apiAdminKbDelete(docId: number) {
  return apiFetch<{ doc_id: number; is_deleted: boolean }>('DELETE', `/admin/kb/${docId}`);
}

// ===== 知识库阅读页（全员可读；farmer 按主物种过滤 + 公共文档，与问诊物种隔离同口径）=====

export function apiKbList() {
  return apiFetch<{ total: number; items: KbDocItem[] }>('GET', '/kb/list');
}

// ===== 知识库书架版（2026-10-15：真后端 /kb/*，全员上传 + 各类文件存档）=====

export type KbBookType = 'disease' | 'nutrition' | 'general';

export interface KbBook {
  doc_id: number;
  doc_name: string;
  doc_type: KbBookType;
  /** ''=公共文档（全物种可见） */
  species_code: 'pig' | 'cattle' | 'sheep' | '';
  retrieval_keywords: string[];
  risk_level: 'low' | 'mid' | 'high';
  feed_advice: string;
  content_md: string;
  source: string;
  organizer: string;
  chunk_count: number;
  is_enable: boolean;
  file_name: string | null;
  file_ext: string | null;
  file_size: number | null;
  has_file: boolean;
  create_time: string;
}

export function apiKbBookList(q = '', docType = '') {
  const params = new URLSearchParams();
  if (q.trim()) params.set('q', q.trim());
  if (docType) params.set('doc_type', docType);
  const qs = params.toString();
  return apiFetch<{ total: number; items: KbBook[] }>('GET', `/kb/list${qs ? `?${qs}` : ''}`);
}

export function apiKbBookDetail(docId: number) {
  return apiFetch<KbBook>('GET', `/kb/doc/${docId}`);
}

/** 上传（multipart）：文件可选（20MB 内，白名单类型），无文件时 content_md 必填 */
export async function apiKbBookUpload(form: FormData): Promise<KbBook> {
  const res = await fetch(`${API_BASE}/kb/upload`, {
    method: 'POST',
    headers: { ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) },
    body: form,
  });
  const result = (await res.json()) as { code: number; message: string; data: unknown };
  if (result.code === 40101) handleUnauthorized();
  if (result.code !== 0) throw new ApiError(result.code, result.message);
  return result.data as KbBook;
}

/** 附件下载（带鉴权头，落 blob 触发浏览器保存） */
export async function apiKbBookDownload(docId: number, fileName: string): Promise<void> {
  const res = await fetch(`${API_BASE}/kb/doc/${docId}/file`, {
    headers: { ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}) },
  });
  // 后端业务错误统一是 HTTP 200 + {code,message,data}：只看 res.ok 会把错误信封
  // 当成文件下载下来（用户得到一个打不开的 .json）。按 content-type 分流。
  const ctype = res.headers.get('content-type') ?? '';
  if (!res.ok || ctype.includes('application/json')) {
    let msg = '附件下载失败';
    try {
      const payload = (await res.json()) as { code?: number; message?: string };
      if (payload.code === 40101) handleUnauthorized();
      if (payload.message) msg = payload.message;
    } catch {
      /* 非 JSON：保持默认文案 */
    }
    throw new ApiError(50001, msg);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fileName;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function apiKbBookToggle(docId: number, isEnable: boolean) {
  return apiFetch<{ doc_id: number; is_enable: boolean }>('PUT', `/kb/doc/${docId}/enable`, { is_enable: isEnable });
}

export function apiKbBookDelete(docId: number) {
  return apiFetch<{ doc_id: number; is_deleted: boolean }>('DELETE', `/kb/doc/${docId}`);
}
