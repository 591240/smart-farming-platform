export type Species = 'pig' | 'cattle' | 'sheep';

/** 3D 场景天气模式：与 F08 气象模块联动（text_day 关键词映射） */
export type WeatherMode = 'sunny' | 'cloudy' | 'rain';

/** 告警定位锚点类型：环境/设备/监控/栏位 */
export type HotspotKey = 'env' | 'device' | 'cam' | 'pen';

export interface BarnFocus {
  species: Species;
  hotspot: HotspotKey;
  penName?: string;
}

export interface BarnInstance {
  name: string;
  species: Species;
  /** 真实存栏数（公+母），用于按数量渲染动物 */
  count?: number;
  /** 公畜数量（统计面板用） */
  male?: number;
  /** 母畜数量（统计面板用） */
  female?: number;
}
