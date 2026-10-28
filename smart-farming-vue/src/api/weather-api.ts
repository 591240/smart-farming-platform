// F08 气象 API — 03-API 十五节 GET /weather 契约 + F08 PRD FR-01 具体化（V1.1 高德数据源定稿）
// - 响应契约零改动：高德字段映射为 forecast/warnings/risk 形状（weather-service.ts mapAmapResponse）；
// - source=live|cache 角标 + data_time 取数时间 + degraded 降级标注（EX-02）；
// - available=false 为引导态（no_farm/no_adcode，EX-W1 不报错）；50003 由 ApiError 抛出（EX-W2）。

import { apiFetch } from './client';

export interface WeatherForecastDay {
  date: string; // yyyy-MM-dd
  temp_min: number;
  temp_max: number;
  text_day: string;
  /** 降水概率 %（高德无此字段，由天气现象推导估算，F08 V1.1） */
  precip_prob: number;
  /** 内部扩展字段：24h 降水估算 mm（暴雨规则匹配用） */
  precip_mm: number;
  /** 内部扩展字段：风力等级（大风规则匹配用） */
  wind_level: number;
}

export interface WeatherWarning {
  type: string;
  level: 'blue' | 'yellow' | 'orange' | 'red';
  title: string;
}

export interface WeatherRiskItem {
  risk_type: string;
  level: 'low' | 'mid' | 'high';
  title: string;
  pen_name: string;
  data_hint: string;
}

export interface WeatherRiskInfo {
  level: 'low' | 'mid' | 'high';
  species: string;
  risk_type: string;
  title: string;
  impact: string;
  advice: string[];
  data_hint: string;
  items: WeatherRiskItem[];
}

export interface WeatherNoneRisk {
  level: 'none';
}

export interface WeatherResponse {
  available: boolean;
  /** available=false 时：no_farm 未建场 / no_adcode 地址不完整（EX-W1 引导态） */
  reason?: 'no_farm' | 'no_adcode';
  farm_id?: number;
  adcode?: string;
  /** 取数时间（ISO；降级时为缓存时刻，前端标注「数据更新于 X 小时前」） */
  data_time?: string;
  source?: 'live' | 'cache';
  /** EX-02 降级返回缓存时置 true */
  degraded?: boolean;
  forecast?: WeatherForecastDay[];
  warnings?: WeatherWarning[];
  risk?: WeatherRiskInfo | WeatherNoneRisk;
}

/** GET /weather?farm_id=（farm_id 缺省取主养殖场；归属越权 40302/不存在 40401 由服务端拦截） */
export function apiGetWeather(farmId?: number) {
  return apiFetch<WeatherResponse>('GET', `/weather${farmId ? `?farm_id=${farmId}` : ''}`);
}
