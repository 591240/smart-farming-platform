// 物种与细分品种静态数据
// 对齐《03-API接口设计》3.1 / 3.2 契约（GET /species/list、GET /variety/list?species_code=）
// V1.0 三物种全建（猪/牛/羊），品种含"通用"兜底项（F01 PRD FR-06 验收）

export interface SpeciesItem {
  species_code: 'pig' | 'cattle' | 'sheep';
  species_name: string;
  category: '畜禽';
  desc: string;
}

export interface VarietyItem {
  variety_code: string;
  variety_name: string;
}

export const SPECIES_LIST: SpeciesItem[] = [
  // 猪先行（首发市场湖南生猪，卡片排序第一）
  { species_code: 'pig', species_name: '猪', category: '畜禽', desc: '妊娠期 114 天 · 年产约 2.2 窝' },
  { species_code: 'cattle', species_name: '牛', category: '畜禽', desc: '妊娠期 280 天 · 一年一犊' },
  { species_code: 'sheep', species_name: '羊', category: '畜禽', desc: '妊娠期 150 天 · 两年三胎' },
];

const VARIETY_MAP: Record<string, VarietyItem[]> = {
  pig: [
    { variety_code: 'pig_fattening', variety_name: '育肥猪' },
    { variety_code: 'pig_sow', variety_name: '母猪' },
    { variety_code: 'pig_piglet', variety_name: '仔猪' },
    { variety_code: 'common', variety_name: '通用' },
  ],
  cattle: [
    { variety_code: 'cattle_beef', variety_name: '肉牛' },
    { variety_code: 'cattle_dairy', variety_name: '奶牛' },
    { variety_code: 'common', variety_name: '通用' },
  ],
  sheep: [
    { variety_code: 'sheep_hu', variety_name: '湖羊' },
    { variety_code: 'sheep_small_tailed_han', variety_name: '小尾寒羊' },
    { variety_code: 'common', variety_name: '通用' },
  ],
};

export function listVarieties(speciesCode: string): VarietyItem[] {
  return VARIETY_MAP[speciesCode] ?? [{ variety_code: 'common', variety_name: '通用' }];
}

// ===== 台账扩展字段定义（02-数据库 3.1 variety.record_ext_fields 种子数据）=====
// F04 PRD 十节文档同步项②：存栏变动类台账 record_ext 使用 male_delta/female_delta
// （与 03-API 4.3 存栏变动请求字段同名同义），label"公变动/母变动"；
// 台账导出动态列序按本定义顺序排列（03-API 10.2 / F04 TC-R03），未入定义字段走首现兜底。
// admin 后台维护本定义随阶段 10 实装（F04 PRD 四节不包含项）。

export interface RecordExtFieldDef {
  field: string;
  label: string;
}

const RECORD_EXT_FIELDS: Record<string, RecordExtFieldDef[]> = {
  pig: [
    { field: 'male_delta', label: '公变动' },
    { field: 'female_delta', label: '母变动' },
  ],
  cattle: [],
  sheep: [],
};

/** 按物种返回台账扩展字段定义（mock 期静态种子，真实后端读 variety.record_ext_fields） */
export function getRecordExtFields(speciesCode: string | null): RecordExtFieldDef[] {
  return RECORD_EXT_FIELDS[speciesCode ?? 'pig'] ?? [];
}

/**
 * 批次生长阶段（按物种，对齐 PRD 3.1.4 / pen.batch_stage 枚举）。
 * 2026-10-14 配食分类完善（用户需求：按生长阶段+生理状态细分）：
 * - 羊/牛新增 gestation 妊娠期 / lactation 泌乳期（母畜圈生理状态档，
 *   营养库对应档位见 feed-data.ts；标签带场景注释便于养殖户选择）；
 * - 母猪仍走 pig_sow 品种 + 繁殖状态自动推导（F07 口径），不重复给猪列生理档。
 */
export function stageOptions(speciesCode: string): { value: string; label: string }[] {
  if (speciesCode === 'cattle') {
    return [
      { value: 'nursing', label: '哺乳期（哺乳犊牛）' },
      { value: 'weaning', label: '保育期（断奶犊牛）' },
      { value: 'growing', label: '育成期（成长期牛只）' },
      { value: 'fattening', label: '育肥期（待出栏肉牛）' },
      { value: 'gestation', label: '妊娠期（怀孕母牛）' },
      { value: 'lactation', label: '泌乳期（哺乳母牛）' },
    ];
  }
  if (speciesCode === 'sheep') {
    return [
      { value: 'nursing', label: '哺乳期（哺乳羔羊）' },
      { value: 'weaning', label: '保育期（刚断奶羔羊）' },
      { value: 'growing', label: '育成期（成长期羊只）' },
      { value: 'fattening', label: '育肥期（待出栏育肥羊）' },
      { value: 'gestation', label: '妊娠期（怀孕母羊）' },
      { value: 'lactation', label: '泌乳期（哺乳母羊）' },
    ];
  }
  return [
    { value: 'nursing', label: '哺乳期' },
    { value: 'weaning', label: '保育期' },
    { value: 'fattening', label: '育肥期' },
  ];
}

export function speciesName(code: string | null): string {
  return SPECIES_LIST.find((s) => s.species_code === code)?.species_name ?? '未设置';
}

// ===== 物种生命周期参数（02-数据库 species.lifecycle_params 种子，V1.7 定稿结构）=====
// F07 全生命周期管理：预产期计算（配种日 + gestation_days）、断奶提醒（分娩日 + lactation_days）、
// 免疫提醒（vaccination_program，anchor=birth 按日龄 / anchor=due_date 按距预产期天数，负数=提前）。
// 数值来源：PRD 附录 D 三物种生命周期参数表 + 02-数据库 5.1 猪种子（公开资料整理估算值，
// 页面标注"参考值，以兽医建议为准"；牛羊参数已按附录 D 同格式预置，知识库/营养库随横向复制阶段补）。
// pregnancy_late_after_days：妊娠前后期细分推导阈值（口径 457，应用层推导值，不进状态机；
// 猪营养库当前为妊娠单档（F06 种子），early/late 均回落该档，分档收益场景为羊/牛）。

export interface VaccineProgramItem {
  vaccine: string;
  timing_day: number;
  target: string;
  anchor: 'birth' | 'due_date';
  note: string;
}

export interface LifecycleParams {
  gestation_days: number;
  lactation_days: number;
  market_weight_kg: number;
  estrus_cycle_days: number;
  castration_age: string;
  /** 妊娠后期起始天数（配种后第 N 天起为后期；猪为单档，阈值仅用于推导标注） */
  pregnancy_late_after_days: number;
  vaccination_program: VaccineProgramItem[];
}

export const LIFECYCLE_PARAMS: Record<string, LifecycleParams> = {
  // 猪（02-数据库 5.1 种子原文；妊娠单档，附录 D 口径）
  pig: {
    gestation_days: 114,
    lactation_days: 28,
    market_weight_kg: 120,
    estrus_cycle_days: 21,
    castration_age: '7~15日龄',
    pregnancy_late_after_days: 91,
    vaccination_program: [
      { vaccine: '猪瘟兔化弱毒苗', timing_day: 30, target: '仔猪', anchor: 'birth', note: '30 日龄首免' },
      { vaccine: '口蹄疫O型灭活苗', timing_day: 55, target: '仔猪', anchor: 'birth', note: '55 日龄二免' },
      {
        vaccine: '大肠杆菌K88/K99',
        timing_day: -21,
        target: '妊娠母猪',
        anchor: 'due_date',
        note: '产前 21 天免疫（母源抗体）',
      },
    ],
  },
  // 羊（附录 D：妊娠约 150 天，60 天左右断奶；妊娠后期 = 第 91~150 天）
  sheep: {
    gestation_days: 150,
    lactation_days: 60,
    market_weight_kg: 45,
    estrus_cycle_days: 17,
    castration_age: '2周~2月龄',
    pregnancy_late_after_days: 91,
    vaccination_program: [
      {
        vaccine: '羔羊痢疾苗',
        timing_day: -25,
        target: '妊娠母羊',
        anchor: 'due_date',
        note: '产前 20~30 天免疫（附录 D 关键管理提醒）',
      },
    ],
  },
  // 肉牛（附录 D：妊娠 270~285，取 280 估算；约 6 月龄断奶；妊娠后期 = 后 3 月）
  cattle: {
    gestation_days: 280,
    lactation_days: 180,
    market_weight_kg: 500,
    estrus_cycle_days: 21,
    castration_age: '育肥进场时',
    pregnancy_late_after_days: 180,
    vaccination_program: [],
  },
};

/** 取物种生命周期参数（缺省回落猪——猪先行，F07 本期仅猪验收） */
export function getLifecycleParams(speciesCode: string | null): LifecycleParams {
  return LIFECYCLE_PARAMS[speciesCode ?? 'pig'] ?? LIFECYCLE_PARAMS.pig;
}

// ===== 物种气候风险规则（02-数据库 species.climate_risk 结构种子，F08 FR-03）=====
// 四类风险阈值（F08 PRD 待确认③拟定值，来源为公开气象预警等级标准整理，标「估算」）：
// 高温 heat_stress temp_gt 35℃ / 暴雨 rainstorm 24h 降水 >50mm / 霜冻 frost 最低温 <2℃ / 大风 gale 风力 ≥6 级。
// 分工：climate_risk 管告警阈值，weather_adjust（feed-data.ts）管喂食计划下调阈值，两者分别配置（F08 术语表，防双阈值混淆）。
// 羊/牛随横向复制阶段补（缺规则的物种不生成 climate 规则告警，EX-W7；官方预警透传不受影响，FR-07）。

export type ClimateRiskSeed = {
  heat_stress?: { temp_gt: number };
  rainstorm?: { precip_mm_gt: number };
  frost?: { temp_lt: number };
  gale?: { wind_level_gte: number };
};

export const CLIMATE_RISK_SEED: Record<string, ClimateRiskSeed> = {
  pig: {
    heat_stress: { temp_gt: 35 },
    rainstorm: { precip_mm_gt: 50 },
    frost: { temp_lt: 2 },
    gale: { wind_level_gte: 6 },
  },
  // F12 FR-05：羊牛阈值承接 F08 遗留"随横向复制补"（公开预警标准整理的估算值）
  sheep: {
    heat_stress: { temp_gt: 30 },
    rainstorm: { precip_mm_gt: 40 },
    frost: { temp_lt: 0 },
    gale: { wind_level_gte: 6 },
  },
  cattle: {
    heat_stress: { temp_gt: 30 },
    rainstorm: { precip_mm_gt: 50 },
    frost: { temp_lt: 0 },
    gale: { wind_level_gte: 7 },
  },
};

/** 取物种气候风险规则（无种子物种返回 null → 不生成 climate 规则告警，EX-W7） */
export function getClimateRisk(speciesCode: string | null): ClimateRiskSeed | null {
  return CLIMATE_RISK_SEED[speciesCode ?? ''] ?? null;
}
