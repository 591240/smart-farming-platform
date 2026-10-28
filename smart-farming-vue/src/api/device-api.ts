// 设备与告警 API — 按《03-API接口设计》7/8 节 / F03 PRD 契约封装

import { apiFetch } from './client';

// ---- 设备（FR-01/02）----

export interface DeviceItem {
  device_id: number;
  device_name: string;
  device_type: 'sensor' | 'camera';
  device_sn: string;
  source: 'real' | 'sim';
  farm_id: number;
  pen_id: number;
  pen_name: string;
  mqtt_topic: string;
  rtsp_url: string;
  online_status: 'online' | 'offline';
  last_heartbeat: string | null;
  is_enable: boolean;
  install_time: string;
}

export function apiListDevices(farmId?: number) {
  const q = farmId ? `?farm_id=${farmId}` : '';
  return apiFetch<{ items: DeviceItem[] }>('GET', `/device/list${q}`);
}

export function apiCreateDevice(body: {
  farm_id: number;
  pen_id: number;
  device_type: 'sensor' | 'camera';
  device_name: string;
  device_sn: string;
  rtsp_url?: string;
}) {
  // device_secret 是一次性明文（库里只存 sha256）：真实设备上报要用它做 X-Device-Key（C-24）
  return apiFetch<{ device_id: number; mqtt_topic: string; device_secret?: string }>('POST', '/device', body);
}

/** 重签发设备密钥：旧值立即失效，明文只回传这一次（03-API 7.4） */
export function apiRotateDeviceSecret(deviceId: number) {
  return apiFetch<{ device_id: number; device_secret: string }>('POST', `/device/${deviceId}/rotate-secret`, {});
}

export function apiUpdateDevice(
  deviceId: number,
  body: { device_name?: string; is_enable?: boolean; rtsp_url?: string },
) {
  return apiFetch<{ device_id: number; updated: boolean }>('PUT', `/device/${deviceId}`, body);
}

// 逻辑删除：历史采集数据与告警保留（主 PRD 3.3.1）
export function apiDeleteDevice(deviceId: number) {
  return apiFetch<{ device_id: number; deleted: boolean }>('DELETE', `/device/${deviceId}`);
}

// ---- 告警（FR-06）----

export type AlertType = 'sensor' | 'climate' | 'vision' | 'disease' | 'device_offline';
export type AlertStatus = 'pending' | 'acknowledged' | 'resolved' | 'false_alarm';

export interface AlertItem {
  alert_id: number;
  alert_type: AlertType;
  risk_level: 'low' | 'mid' | 'high';
  alert_content: string;
  status: AlertStatus;
  handled_note: string;
  handled_time: string | null;
  create_time: string;
  pen_name: string;
}

export function apiListAlerts(params?: {
  status?: AlertStatus;
  type?: AlertType;
  farm_id?: number;
  /** yyyy-MM-dd，东八区自然日起点（大屏「今日新增」服务端计数） */
  from?: string;
  page?: number;
  page_size?: number;
}) {
  const q = new URLSearchParams();
  if (params?.status) q.set('status', params.status);
  if (params?.type) q.set('type', params.type);
  if (params?.farm_id) q.set('farm_id', String(params.farm_id));
  if (params?.from) q.set('from', params.from);
  q.set('page', String(params?.page ?? 1));
  q.set('page_size', String(params?.page_size ?? 50));
  return apiFetch<{ total: number; page: number; page_size: number; items: AlertItem[] }>(
    'GET',
    `/alert/list?${q.toString()}`,
  );
}

// resolve 无备注返回 42201（TC-D06）；处理动作落台账。
// 请求体字段名是 `note`（后端 AlertActionBody），响应里的 `handled_note` 是库列名——两者不同名，
// 曾按 handled_note 提交导致真后端恒定 42201（切真契约断裂，docs/08）。
export function apiHandleAlert(
  alertId: number,
  body: { action: 'acknowledge' } | { action: 'resolve'; note: string } | { action: 'false_alarm'; note: string },
) {
  return apiFetch<{ alert_id: number; status: AlertStatus }>('POST', `/alert/${alertId}/handle`, body);
}

// ---- 传感器历史（FR-08）----

export function apiSensorHistory(params: { pen_id: number; metric: string; from?: string; to?: string }) {
  const q = new URLSearchParams({ pen_id: String(params.pen_id), metric: params.metric });
  if (params.from) q.set('from', params.from);
  if (params.to) q.set('to', params.to);
  return apiFetch<{ metric: string; points: { time: string; value: number }[] }>(
    'GET',
    `/sensor/history?${q.toString()}`,
  );
}
