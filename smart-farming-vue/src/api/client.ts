// API Client — 唯一取数通道：真后端 FastAPI。
//
// 浏览器内核 `lib/mock-backend.ts` 已于 2026-10-16 下线（存档在 `_archive/mock-retired-20261016/`）。
// 此前这里是"按路径前缀决定走真后端还是走 localStorage 内核"的双路门控；门控存在的理由
// （后端尚未实装完）已消失，故整条 mock 分支、模拟网络延迟、`sf_token_mode` 双凭据标记一并删除。
//
// 统一约定（03-API 1.3）：
// - 成功 code=0；业务失败抛 ApiError（code/message/data 与后端信封同构）
// - 除 AUTH_FREE_PATHS 外一律附 Authorization: Bearer <token>
// - 收到 40101：清凭证并跳 /login（FR-03）
// - 登录态判定只认后端签发的 HS256 JWT（客户端只解 payload 读 exp，签名校验在服务端每个请求做）

import { ApiError } from '@/lib/api-contract';

export { ApiError };
export type { ApiError as ApiErrorType } from '@/lib/api-contract';

/**
 * 后端基址解析顺序：
 * 1. `VITE_REAL_API_BASE`（显式配置优先）；
 * 2. dev server（3015 端口）→ 本机 8000，跨源但开发期固定；
 * 3. 其余一律相对路径 `/api`（打包后由 nginx 或同域后端托管）。
 *
 * 为什么不再写死 127.0.0.1:8000 作默认值：那个默认值会被 Vite 编译进 dist 产物，
 * 部署到任何一台别的机器上，浏览器都会去连**访问者自己**的 8000 端口。
 */
function resolveApiBase(): string {
  const configured = (import.meta.env.VITE_REAL_API_BASE as string | undefined)?.trim();
  if (configured) return configured.replace(/\/$/, '');
  if (typeof window !== 'undefined' && window.location.port === '3015') {
    // 用 hostname 而不是写死 127.0.0.1：局域网/远程开发时前端和后端在同一台机器上
    return `http://${window.location.hostname}:8000/api`;
  }
  return '/api';
}

export const API_BASE = resolveApiBase();

/** 需要 token 的请求才带的本地凭证 key（跨标签页同步监听用） */
export const TOKEN_KEY = 'sf_token';

// 免鉴权路径（F01 PRD FR-03）
const AUTH_FREE_PATHS = new Set(['/auth/register', '/auth/login']);

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/** 只解 payload 读 exp（毫秒）；不验签。非法/过期 token 返回 null。 */
export function getTokenExpMs(): number | null {
  const parts = getToken()?.split('.');
  if (!parts || parts.length !== 3) return null;
  const payload = decodeJwtPayload(parts[1]);
  return typeof payload?.exp === 'number' ? payload.exp * 1000 : null;
}

function decodeJwtPayload(segment: string): { user_id?: number; role?: string; exp?: number } | null {
  try {
    const b64 = segment.replace(/-/g, '+').replace(/_/g, '/');
    return JSON.parse(decodeURIComponent(escape(atob(b64)))) as { user_id?: number; role?: string; exp?: number };
  } catch {
    return null; // 非 JWT（旧 mock 自签凭据）一律视为无效 → 重新登录
  }
}

/** 同步解析当前登录态（供路由守卫 beforeLoad 使用） */
export function readAuthSnapshot(): { userId: number; role: string } | null {
  const tk = getToken();
  if (!tk) return null;
  const parts = tk.split('.');
  if (parts.length !== 3) return null;
  const payload = decodeJwtPayload(parts[1]);
  if (!payload || typeof payload.user_id !== 'number') return null;
  if (payload.exp && payload.exp * 1000 <= Date.now()) return null;
  return { userId: payload.user_id, role: String(payload.role ?? 'farmer') };
}

type Method = 'GET' | 'POST' | 'PUT' | 'DELETE';

export async function apiFetch<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const token = AUTH_FREE_PATHS.has(path.split('?')[0]) ? null : getToken();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    // 后端没起 / 网络断：给一句能行动的提示，而不是让页面把"连不上"渲染成"没有数据"
    throw new ApiError(50001, '无法连接服务器，请确认后端已启动（backend/ 内 python run.py）');
  }

  let result: { code: number; message: string; data: unknown };
  try {
    result = (await res.json()) as { code: number; message: string; data: unknown };
  } catch {
    throw new ApiError(50001, `服务响应异常（HTTP ${res.status}）`);
  }
  if (result.code === 40101 && !AUTH_FREE_PATHS.has(path.split('?')[0])) handleUnauthorized();
  if (result.code !== 0) throw new ApiError(result.code, result.message, result.data);
  return result.data as T;
}

export function handleUnauthorized(): void {
  clearToken();
  if (!window.location.pathname.startsWith('/login')) {
    window.location.assign('/login');
  }
}
