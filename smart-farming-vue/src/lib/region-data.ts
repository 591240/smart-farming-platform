// 省市区静态行政区划数据（引导第 2 步 / 建场表单用）
// 全量国标数据见 region-data.gen.ts（由 china-division 民政部口径生成，直辖市的"市辖区"已替换为市名）
// adcode 采用国标 6 位行政区划码（高德气象 API 直接可用）

import { PROVINCES, type RegionNode } from './region-data.gen';

export type { RegionNode };
export { PROVINCES };

export interface RegionSelection {
  province: string;
  city: string;
  county: string;
  adcode: string;
}

/** 由三级 code 组装名称与 adcode（adcode 取县级编码） */
export function resolveRegion(
  provinceCode: string,
  cityCode: string,
  countyCode: string,
): RegionSelection | null {
  const province = PROVINCES.find((p) => p.code === provinceCode);
  if (!province) return null;
  const city = province.children?.find((c) => c.code === cityCode);
  if (!city) return null;
  const county = city.children?.find((d) => d.code === countyCode);
  if (!county) return null;
  return {
    province: province.name,
    city: city.name,
    county: county.name,
    adcode: county.code,
  };
}
