/**
 * 告警订阅统一入口 —— 把"进程内 pub/sub"和"真后端 /ws/alerts"的切换收在这一个文件里。
 *
 * 调用方（TopBar 红点、告警中心页）保持原签名 `subscribeAlerts(cb) => unsubscribe`，
 * 这也是 mock 时代注释里写的"切真后调用方零改动"的兑现处。
 *
 * 三处不显然但必要的行为：
 * 1. **凭据走子协议头**：`new WebSocket(url, [wsToken])`。握手 URL 会原样进反代与
 *    访问日志，业务 token（24h）拼在 query string 上等于长期凭据外泄（08 文档 C-03）。
 *    所以先 `POST /auth/ws-token` 换 5 分钟短令牌；服务端必须回显该子协议，否则浏览器判定握手失败。
 * 2. **每次重连前重新签发**：短令牌 5 分钟就过期，复用旧令牌重连必然 4401。
 * 3. **连上即补一次**：断线期间的告警服务端不补发，靠回调触发一次列表重取。
 *
 * cb 不接参数：两个消费方都只做 invalidateQueries。真后端帧里确实带 alert_id 等字段，
 * 但把它当权威会让"帧丢失 = 告警丢失"，读侧仍以服务端列表为准。
 */

import { API_BASE, apiFetch, getToken } from '@/api/client';

type Handler = () => void;

const HEARTBEAT_MS = 30_000;
const BACKOFF_MIN_MS = 1_000;
const BACKOFF_MAX_MS = 30_000;

function wsUrl(): string {
  const base = API_BASE.replace(/^http/, 'ws');
  // 生产构建下 API_BASE 是相对路径 `/api`，而 WebSocket 构造器只接受绝对 URL——
  // 相对路径会直接抛 SyntaxError。开发环境是 http://host:8000/api，这个坑永远暴露不出来。
  const absolute = /^wss?:\/\//.test(base)
    ? base
    : `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}${base}`;
  return `${absolute}/ws/alerts`;
}

const PUSH_PREF_KEY = 'sf_alert_push';

/**
 * 「告警实时推送」偏好（系统设置页的那个开关此前无人消费，WS 是无条件连的）。
 * 关掉即不建连，页面仍靠各自轮询兜底；订阅发生在挂载时，所以切换后下次进入相关页面生效。
 */
export function isAlertPushEnabled(): boolean {
  return localStorage.getItem(PUSH_PREF_KEY) !== '0';
}

export function setAlertPushEnabled(on: boolean): void {
  localStorage.setItem(PUSH_PREF_KEY, on ? '1' : '0');
}

/** 真后端传输层：自持重连状态机，返回的 dispose 必须能彻底掐掉定时器与 socket。 */
function startRealStream(cb: Handler): () => void {
  let disposed = false;
  let socket: WebSocket | null = null;
  let heartbeat: ReturnType<typeof setInterval> | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;
  let backoff = BACKOFF_MIN_MS;

  async function open() {
    if (disposed) return;
    let token: string;
    try {
      token = (await apiFetch<{ ws_token: string }>('POST', '/auth/ws-token')).ws_token;
    } catch {
      return; // 拿不到短令牌通常是登录态失效：交给既有 60s 轮询兜底，不无限重试打后端
    }
    if (disposed) return; // 等令牌期间已被卸载

    socket = new WebSocket(wsUrl(), [token]);
    socket.onopen = () => {
      backoff = BACKOFF_MIN_MS;
      cb();
      heartbeat = setInterval(() => {
        if (socket?.readyState === WebSocket.OPEN) socket.send('ping');
      }, HEARTBEAT_MS);
    };
    socket.onmessage = () => cb();
    socket.onclose = () => {
      if (heartbeat) clearInterval(heartbeat);
      heartbeat = null;
      if (disposed) return;
      backoff = Math.min(backoff * 2, BACKOFF_MAX_MS);
      retry = setTimeout(open, backoff);
    };
    socket.onerror = () => socket?.close();
  }

  void open();

  return () => {
    disposed = true;
    if (heartbeat) clearInterval(heartbeat);
    if (retry) clearTimeout(retry);
    socket?.close();
    socket = null;
  };
}

/**
 * 订阅新告警：真后端 `/ws/alerts`（未登录或关掉推送时不建连，返回空清理函数）。
 */
export function subscribeAlerts(cb: Handler): () => void {
  if (!isAlertPushEnabled() || !getToken()) return () => {};
  return startRealStream(cb);
}
