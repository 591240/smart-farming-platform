// 告警微信推送（WxPusher）状态 — 设置页「微信告警通知」卡用
import { apiFetch } from './client';

export interface WxNotifyStatus {
  /** true = 服务端已配置 appToken，推送通道开启 */
  enabled: boolean;
  /** 订阅二维码页链接（农户微信扫码加入主题即订阅，平台不存设备标识） */
  subscribe_url: string;
}

export function apiWxNotifyStatus() {
  return apiFetch<WxNotifyStatus>('GET', '/notify/wx-status');
}
