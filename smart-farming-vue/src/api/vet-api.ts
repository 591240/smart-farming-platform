// F10 HITL 兽医复核 API — 03-API 11.1/11.2 + 十二节 /admin/user（F10 PRD FR-01~05 具体化）
// - 角色边界（接口层 40301 是唯一安全边墙，前端过滤仅体验层）：
//   vet：GET /vet/pending-cases + POST /vet/review；
//   admin：pending-cases 只读放宽（决策 5 待确认③默认采纳：徽标计数/督办下钻，无复核权）
//          + /admin/user CRUD（用户管理）；farmer：两者均不可见。
// - 响应最小字段集白名单（RK-2：新增字段须重过安全评审），不含养殖户 phone/user_id。

import { apiFetch } from './client';

export interface PendingCaseItem {
  review_id: number;
  alert_id: number;
  session_id: number;
  /** 等待时长（小时，1 位小数，读取实时计算） */
  waiting_hours: number;
  /** ≥24h warn（置顶标红，EX-05）；≥48h 已惰性降级不再出现（EX-06） */
  timeout_level: 'normal' | 'warn';
  original_diagnosis: string;
  risk_level: 'low' | 'mid' | 'high';
  /** 服务端派生（vet 无租户上下文，不能让前端反查） */
  farm_name: string;
  pen_name: string;
  create_time: string;
}

export function apiVetPendingCases() {
  return apiFetch<{ items: PendingCaseItem[] }>('GET', '/vet/pending-cases');
}

export interface VetReviewResult {
  review_id: number;
  review_status: 'approved' | 'rejected';
  ledger_record_id: number;
}

export function apiVetReview(body: {
  review_id: number;
  action: 'approve' | 'reject';
  review_opinion?: string;
  revised_diagnosis?: string;
  revised_med?: string;
}) {
  return apiFetch<VetReviewResult>('POST', '/vet/review', body);
}

export interface AdminUserItem {
  user_id: number;
  phone: string; // 脱敏
  nickname: string;
  role: 'farmer' | 'vet' | 'admin';
  is_enable: boolean;
  create_time: string;
}

export function apiAdminListUsers() {
  return apiFetch<{ total: number; items: AdminUserItem[] }>('GET', '/admin/user');
}

export function apiAdminCreateUser(body: {
  phone: string;
  nickname?: string;
  role: 'vet' | 'admin';
  initial_password: string;
}) {
  return apiFetch<AdminUserItem>('POST', '/admin/user', body);
}

export function apiAdminSetUserEnable(userId: number, isEnable: boolean) {
  return apiFetch<{ user_id: number; is_enable: boolean }>('PUT', `/admin/user/${userId}`, {
    is_enable: isEnable,
  });
}
