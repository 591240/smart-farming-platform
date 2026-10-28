// 喂食中心静态种子数据 — F06 FR-01（营养标准库 feed_nutrition_lib）+ 02-数据库 5.3（价格种子）
//
// 数值来源：NY/T 65《猪饲养标准》(2004) 与《湖南省生猪健康养殖技术规程》等
// 公开资料的合规整理估算值（F06 PRD 决策 2 / RK-2：逐条标 source + "估算"，
// 页面显著标注"参考值，不替代兽医与营养师建议"，15.5 合规声明）。
// 配方生成（mock 规则模板引擎，F06 PRD 决策 1）与计划兜底的 basis 引用本库 source（TC-F01）。
// 猪先行（F06 PRD 四节）：育肥/母猪妊娠哺乳/仔猪保育分档，牛羊随阶段 10 admin 后台补录。
// 2026-10-14 配食分类完善（联网核实 NY/T 816-2021 等公开饲养标准后补齐）：羊增加
// weaning 刚断奶羔羊 / growing 成长期 / gestation 怀孕母羊 / lactation 哺乳母羊档，
// 牛增加 weaning 断奶犊牛 / gestation 怀孕母牛 / lactation 哺乳母牛档（两羊品种各 6 档、
// 肉牛 5 档、奶牛 3 档）；所有新增档 formula 已按 INGREDIENT_NUTRITION 加权自校验达标。

// ===== 原料构成模板 =====

export interface FormulaItem {
  name: string;
  ratio_pct: number;
}

// ===== 营养指标（required 为展示文案；min/max 供校验，闭区间，单边用 null）=====

export interface NutritionIndicator {
  name: string;
  /** 展示文案，如 "13~15%" / "≥0.70%" / "≥13.0 MJ/kg" */
  required: string;
  min: number | null;
  max: number | null;
  unit: string;
}

export interface NutritionStandard {
  species_code: string;
  variety_code: string;
  /** 阶段（对齐 pen.batch_stage 枚举）；母猪按 sex_type 分档（妊娠细分推导随阶段 7，F06 PRD 口径 457） */
  stage: string | null;
  sex_type: 'all' | 'female';
  /**
   * 母猪分档标记（F07 FR-07 新增）：pig_sow 两条种子分别标记 gestation 妊娠 / lactation 哺乳，
   * 由 matchNutritionStandard 按在栏母畜状态选取（收口 F06 已知可接受项①：哺乳档不可达）。
   * 妊娠前后期为应用层推导值（口径 457），猪为单档，early/late 均回落 gestation 档。
   */
  sow_phase?: 'gestation' | 'lactation';
  min_weight: number;
  max_weight: number;
  nutrition: {
    /** 每头每日采食总量（kg）——配方原料 daily_kg_per_head 的核算基数 */
    daily_feed_kg_per_head: number;
    feeds_per_day: number;
    indicators: NutritionIndicator[];
  };
  formula: FormulaItem[];
  /** 气象调整规则（F06 PRD 决策 4：本期字段保留、计算钩子预留不生效，随阶段 8 气象模块接入） */
  weather_adjust: Record<string, unknown> | null;
  source: string;
}

const PIG_SOURCE_NY = 'NY/T 65《猪饲养标准》(2004)';
const PIG_SOURCE_HUN = '《湖南省生猪健康养殖技术规程》';

export const FEED_NUTRITION_LIB_SEED: NutritionStandard[] = [
  {
    species_code: 'pig',
    variety_code: 'pig_fattening',
    stage: 'weaning',
    sex_type: 'all',
    min_weight: 20,
    max_weight: 35,
    nutrition: {
      daily_feed_kg_per_head: 1.4,
      feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '16~18%', min: 16, max: 18, unit: '%' },
        { name: '赖氨酸', required: '≥1.00%', min: 1.0, max: null, unit: '%' },
        { name: '消化能', required: '≥13.5 MJ/kg', min: 13.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 65 },
      { name: '麸皮', ratio_pct: 6 },
      { name: '豆粕', ratio_pct: 24 },
      { name: '预混料', ratio_pct: 5 },
    ],
    weather_adjust: { high_temp: { temp_gt: 32, reduce_concentrate_pct: 10 } },
    source: `${PIG_SOURCE_NY} 保育期段 · ${PIG_SOURCE_HUN}（估算整理）`,
  },
  {
    species_code: 'pig',
    variety_code: 'pig_fattening',
    stage: 'fattening',
    sex_type: 'all',
    min_weight: 35,
    max_weight: 110,
    nutrition: {
      daily_feed_kg_per_head: 2.0,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '13~15%', min: 13, max: 15, unit: '%' },
        { name: '赖氨酸', required: '≥0.70%', min: 0.7, max: null, unit: '%' },
        { name: '消化能', required: '≥13.0 MJ/kg', min: 13.0, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.9%', min: 0.5, max: 0.9, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 65 },
      { name: '麸皮', ratio_pct: 12 },
      { name: '豆粕', ratio_pct: 18 },
      { name: '预混料', ratio_pct: 5 },
    ],
    weather_adjust: { high_temp: { temp_gt: 32, reduce_concentrate_pct: 10 } },
    source: `${PIG_SOURCE_NY} 育肥期段（35~110kg） · ${PIG_SOURCE_HUN}（估算整理）`,
  },
  {
    // 母猪妊娠档（F07 FR-07：sow_phase 标记，按在栏母畜状态选取；妊娠细分推导随阶段 7 实装）
    species_code: 'pig',
    variety_code: 'pig_sow',
    stage: null,
    sex_type: 'female',
    sow_phase: 'gestation',
    min_weight: 120,
    max_weight: 250,
    nutrition: {
      daily_feed_kg_per_head: 2.2,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '12~14%', min: 12, max: 14, unit: '%' },
        { name: '赖氨酸', required: '≥0.55%', min: 0.55, max: null, unit: '%' },
        { name: '消化能', required: '≥12.5 MJ/kg', min: 12.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.6~0.9%', min: 0.6, max: 0.9, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 60 },
      { name: '麸皮', ratio_pct: 22 },
      { name: '豆粕', ratio_pct: 13 },
      { name: '预混料', ratio_pct: 5 },
    ],
    weather_adjust: { high_temp: { temp_gt: 30, reduce_concentrate_pct: 8 } },
    source: `${PIG_SOURCE_NY} 妊娠母猪段（妊娠单档；前期/后期为应用层推导值，F07 实装）· 估算整理`,
  },
  {
    // 母猪哺乳档（F07 FR-07：sow_phase 标记；F06 期兜底恒取妊娠档不可达，本期收口）
    species_code: 'pig',
    variety_code: 'pig_sow',
    stage: null,
    sex_type: 'female',
    sow_phase: 'lactation',
    min_weight: 140,
    max_weight: 260,
    nutrition: {
      daily_feed_kg_per_head: 5.0,
      feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '16~18%', min: 16, max: 18, unit: '%' },
        { name: '赖氨酸', required: '≥0.90%', min: 0.9, max: null, unit: '%' },
        { name: '消化能', required: '≥13.5 MJ/kg', min: 13.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.6~1.0%', min: 0.6, max: 1.0, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 65 },
      { name: '麸皮', ratio_pct: 6 },
      { name: '豆粕', ratio_pct: 24 },
      { name: '预混料', ratio_pct: 5 },
    ],
    weather_adjust: { high_temp: { temp_gt: 30, reduce_concentrate_pct: 8 } },
    source: `${PIG_SOURCE_NY} 哺乳母猪段 · 估算整理`,
  },
  {
    species_code: 'pig',
    variety_code: 'pig_piglet',
    stage: 'weaning',
    sex_type: 'all',
    min_weight: 15,
    max_weight: 25,
    nutrition: {
      daily_feed_kg_per_head: 0.9,
      feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '18~20%', min: 18, max: 20, unit: '%' },
        { name: '赖氨酸', required: '≥1.15%', min: 1.15, max: null, unit: '%' },
        { name: '消化能', required: '≥13.5 MJ/kg', min: 13.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 60 },
      { name: '麸皮', ratio_pct: 4 },
      { name: '豆粕', ratio_pct: 31 },
      { name: '预混料', ratio_pct: 5 },
    ],
    weather_adjust: null,
    source: `${PIG_SOURCE_NY} 仔猪保育段（15~25kg） · 估算整理`,
  },
  // —— 羊（F12 FR-05 横向复制 + 2026-10-14 配食分类完善：
  //    阶段档位依据 NY/T 816-2021《肉羊饲养标准》、舍饲肉羊/滩羊技术规程、
  //    《羊的饲养标准》（怀孕/哺乳母羊分段表）等公开资料整理的演示估算值，
  //    风干料口径；妊娠/泌乳为全期均值档，前期/后期细分随阶段 7 繁殖推导实装）——
  {
    // 刚断奶羔羊（保育期）：断奶至 4 月龄，促瘤胃发育，日增重 200~250g
    species_code: 'sheep',
    variety_code: 'sheep_hu',
    stage: 'weaning',
    sex_type: 'all',
    min_weight: 15,
    max_weight: 30,
    nutrition: {
      daily_feed_kg_per_head: 0.9,
      feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '16~18%', min: 16, max: 18, unit: '%' },
        { name: '消化能', required: '≥10.0 MJ/kg', min: 10.0, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 45 },
      { name: '豆粕', ratio_pct: 24 },
      { name: '麸皮', ratio_pct: 8 },
      { name: '干草粉', ratio_pct: 20 },
      { name: '预混料', ratio_pct: 3 },
    ],
    weather_adjust: null,
    source: 'NY/T 816-2021《肉羊饲养标准》羔羊段（CP 16~18%、DE 9.5~10.5）· 舍饲肉羊技术规程羔羊期配方（估算整理）',
  },
  {
    // 成长期羊只（育成期）：4~6 月龄，骨架发育为主，日增重 250~300g
    species_code: 'sheep',
    variety_code: 'sheep_hu',
    stage: 'growing',
    sex_type: 'all',
    min_weight: 30,
    max_weight: 45,
    nutrition: {
      daily_feed_kg_per_head: 1.4,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '14~16%', min: 14, max: 16, unit: '%' },
        { name: '消化能', required: '≥9.5 MJ/kg', min: 9.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 50 },
      { name: '豆粕', ratio_pct: 18 },
      { name: '麸皮', ratio_pct: 10 },
      { name: '干草粉', ratio_pct: 19 },
      { name: '预混料', ratio_pct: 3 },
    ],
    weather_adjust: null,
    source: 'NY/T 816-2021《肉羊饲养标准》育成羊段（CP 14~16%、DE 9.0~10.0）· 舍饲肉羊技术规程（估算整理）',
  },
  {
    // 待出栏育肥羊：精粗比逐步提至 6:4~7:3，日增重 300g+
    species_code: 'sheep',
    variety_code: 'sheep_hu',
    stage: 'fattening',
    sex_type: 'all',
    min_weight: 25,
    max_weight: 55,
    nutrition: {
      daily_feed_kg_per_head: 1.6,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '12~14%', min: 12, max: 14, unit: '%' },
        { name: '消化能', required: '≥10.0 MJ/kg', min: 10.0, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.4~0.8%', min: 0.4, max: 0.8, unit: '%' },
      ],
    },
    formula: [{ name: '玉米', ratio_pct: 58 }, { name: '豆粕', ratio_pct: 13 }, { name: '麸皮', ratio_pct: 14 }, { name: '干草粉', ratio_pct: 12 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: 'NY/T 816-2021《肉羊饲养标准》育肥羊段（CP 12~14%、DE 10.0~10.8）· 内蒙古农牧厅育肥配方（估算整理）',
  },
  {
    // 怀孕母羊（妊娠期全期均值档）：妊娠前期维持+胎儿慢增，后期（≥91 天）能量钙磷显著上调；
    // 精料由 0.4 提至 0.6~0.7 kg/d（滩羊规程），此为风干料全期均值
    species_code: 'sheep',
    variety_code: 'sheep_hu',
    stage: 'gestation',
    sex_type: 'female',
    min_weight: 40,
    max_weight: 75,
    nutrition: {
      daily_feed_kg_per_head: 1.8,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '13~15%', min: 13, max: 15, unit: '%' },
        { name: '消化能', required: '≥10.0 MJ/kg', min: 10.0, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.7~1.0%', min: 0.7, max: 1.0, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 47 },
      { name: '豆粕', ratio_pct: 16 },
      { name: '麸皮', ratio_pct: 12 },
      { name: '干草粉', ratio_pct: 20 },
      { name: '预混料', ratio_pct: 5 },
    ],
    weather_adjust: null,
    source: '《羊的饲养标准》怀孕母羊段（后期 50kg：风干料 2.0kg、DCP 90~120g/d）· 滩羊/舍饲规程妊娠后期精料 0.5~0.7kg/d（估算整理）',
  },
  {
    // 哺乳母羊（泌乳期全期均值档）：泌乳前 8 周需求峰值，双羔母羊在此基础上再加 20~30%
    species_code: 'sheep',
    variety_code: 'sheep_hu',
    stage: 'lactation',
    sex_type: 'female',
    min_weight: 40,
    max_weight: 75,
    nutrition: {
      daily_feed_kg_per_head: 2.4,
      feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '14~16%', min: 14, max: 16, unit: '%' },
        { name: '消化能', required: '≥10.5 MJ/kg', min: 10.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.7~1.0%', min: 0.7, max: 1.0, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 50 },
      { name: '豆粕', ratio_pct: 20 },
      { name: '麸皮', ratio_pct: 10 },
      { name: '干草粉', ratio_pct: 15 },
      { name: '预混料', ratio_pct: 5 },
    ],
    weather_adjust: null,
    source: '《羊的饲养标准》哺乳母羊段（单羔 50kg：风干料 2.2kg、DCP 110~190g/d；双羔 3.0kg）· 澳洲白绵羊规程哺乳期（估算整理）',
  },
  {
    // 空怀/未设阶段母羊兜底档（配种前 15 天建议短期优饲 +30%）
    species_code: 'sheep',
    variety_code: 'sheep_hu',
    stage: null,
    sex_type: 'female',
    min_weight: 40,
    max_weight: 70,
    nutrition: {
      daily_feed_kg_per_head: 1.8,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '11~13%', min: 11, max: 13, unit: '%' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ],
    },
    formula: [{ name: '玉米', ratio_pct: 50 }, { name: '豆粕', ratio_pct: 14 }, { name: '麸皮', ratio_pct: 16 }, { name: '干草粉', ratio_pct: 17 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: '《羊的饲养标准》空怀母羊段（空怀期兜底；妊娠/泌乳请在圈舍设置对应阶段）（估算整理）',
  },
  { species_code: 'sheep', variety_code: 'sheep_small_tailed_han', stage: 'weaning', sex_type: 'all', min_weight: 15, max_weight: 30,
    nutrition: { daily_feed_kg_per_head: 0.95, feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '16~18%', min: 16, max: 18, unit: '%' },
        { name: '消化能', required: '≥10.0 MJ/kg', min: 10.0, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ] },
    formula: [{ name: '玉米', ratio_pct: 45 }, { name: '豆粕', ratio_pct: 24 }, { name: '麸皮', ratio_pct: 8 }, { name: '干草粉', ratio_pct: 20 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: 'NY/T 816-2021《肉羊饲养标准》羔羊段（多胎品种采食量略高，估算整理）' },
  { species_code: 'sheep', variety_code: 'sheep_small_tailed_han', stage: 'growing', sex_type: 'all', min_weight: 30, max_weight: 45,
    nutrition: { daily_feed_kg_per_head: 1.5, feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '14~16%', min: 14, max: 16, unit: '%' },
        { name: '消化能', required: '≥9.5 MJ/kg', min: 9.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ] },
    formula: [{ name: '玉米', ratio_pct: 50 }, { name: '豆粕', ratio_pct: 18 }, { name: '麸皮', ratio_pct: 10 }, { name: '干草粉', ratio_pct: 19 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: 'NY/T 816-2021《肉羊饲养标准》育成羊段（多胎品种采食量略高，估算整理）' },
  { species_code: 'sheep', variety_code: 'sheep_small_tailed_han', stage: 'fattening', sex_type: 'all', min_weight: 25, max_weight: 55,
    nutrition: { daily_feed_kg_per_head: 1.7, feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '12~14%', min: 12, max: 14, unit: '%' },
        { name: '消化能', required: '≥10.0 MJ/kg', min: 10.0, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.4~0.8%', min: 0.4, max: 0.8, unit: '%' },
      ] },
    formula: [{ name: '玉米', ratio_pct: 58 }, { name: '豆粕', ratio_pct: 13 }, { name: '麸皮', ratio_pct: 14 }, { name: '干草粉', ratio_pct: 12 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: 'NY/T 816-2021《肉羊饲养标准》育肥羊段（CP 12~14%、DE 10.0~10.8；估算整理）' },
  { species_code: 'sheep', variety_code: 'sheep_small_tailed_han', stage: 'gestation', sex_type: 'female', min_weight: 40, max_weight: 80,
    nutrition: { daily_feed_kg_per_head: 1.9, feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '13~15%', min: 13, max: 15, unit: '%' },
        { name: '消化能', required: '≥10.0 MJ/kg', min: 10.0, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.7~1.0%', min: 0.7, max: 1.0, unit: '%' },
      ] },
    formula: [{ name: '玉米', ratio_pct: 47 }, { name: '豆粕', ratio_pct: 16 }, { name: '麸皮', ratio_pct: 12 }, { name: '干草粉', ratio_pct: 20 }, { name: '预混料', ratio_pct: 5 }],
    weather_adjust: null,
    source: '《羊的饲养标准》怀孕母羊段（多胎品种双羔率高，妊娠后期需求更高，估算整理）' },
  { species_code: 'sheep', variety_code: 'sheep_small_tailed_han', stage: 'lactation', sex_type: 'female', min_weight: 40, max_weight: 80,
    nutrition: { daily_feed_kg_per_head: 2.6, feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '14~16%', min: 14, max: 16, unit: '%' },
        { name: '消化能', required: '≥10.5 MJ/kg', min: 10.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.7~1.0%', min: 0.7, max: 1.0, unit: '%' },
      ] },
    formula: [{ name: '玉米', ratio_pct: 50 }, { name: '豆粕', ratio_pct: 20 }, { name: '麸皮', ratio_pct: 10 }, { name: '干草粉', ratio_pct: 15 }, { name: '预混料', ratio_pct: 5 }],
    weather_adjust: null,
    source: '《羊的饲养标准》哺乳母羊段（双羔档 3.0kg 风干料折均值，估算整理）' },
  { species_code: 'sheep', variety_code: 'sheep_small_tailed_han', stage: null, sex_type: 'female', min_weight: 40, max_weight: 75,
    nutrition: { daily_feed_kg_per_head: 1.9, feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '11~13%', min: 11, max: 13, unit: '%' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ] },
    formula: [{ name: '玉米', ratio_pct: 50 }, { name: '豆粕', ratio_pct: 14 }, { name: '麸皮', ratio_pct: 16 }, { name: '干草粉', ratio_pct: 17 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: '《羊的饲养标准》空怀母羊段（空怀期兜底；妊娠/泌乳请在圈舍设置对应阶段）（估算整理）' },
  // —— 牛（F12 FR-05 横向复制 + 2026-10-14 配食分类完善：
  //    依据 NY/T 815《肉牛饲养标准》、肉牛饲养管理行标（泌乳/育成分段）、
  //    优质肉牛与皖东牛饲养管理技术规程（母牛妊娠/哺乳精料量）整理的演示估算值）——
  {
    // 断奶犊牛（保育期）：断奶至育成前，精料由补饲栏自由采食过渡到定时饲喂
    species_code: 'cattle',
    variety_code: 'cattle_beef',
    stage: 'weaning',
    sex_type: 'all',
    min_weight: 120,
    max_weight: 200,
    nutrition: {
      daily_feed_kg_per_head: 4.0,
      feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '15~17%', min: 15, max: 17, unit: '%' },
        { name: '消化能', required: '≥12.0 MJ/kg', min: 12.0, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 52 },
      { name: '豆粕', ratio_pct: 20 },
      { name: '麸皮', ratio_pct: 12 },
      { name: '干草粉', ratio_pct: 13 },
      { name: '预混料', ratio_pct: 3 },
    ],
    weather_adjust: null,
    source: '肉牛饲养管理技术行标断奶犊牛段 · 优质肉牛饲养管理规程犊牛补饲（估算整理）',
  },
  {
    // 待出栏育肥牛（架子牛育肥后期）：精补料占体重 1.2%~1.7%
    species_code: 'cattle',
    variety_code: 'cattle_beef',
    stage: 'fattening',
    sex_type: 'all',
    min_weight: 250,
    max_weight: 550,
    nutrition: {
      daily_feed_kg_per_head: 9.5,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '11~13%', min: 11, max: 13, unit: '%' },
        { name: '消化能', required: '≥12.5 MJ/kg', min: 12.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.4~0.8%', min: 0.4, max: 0.8, unit: '%' },
      ],
    },
    formula: [{ name: '玉米', ratio_pct: 60 }, { name: '豆粕', ratio_pct: 14 }, { name: '麸皮', ratio_pct: 12 }, { name: '干草粉', ratio_pct: 11 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: 'NY/T 815《肉牛饲养标准》育肥牛段 · 皖东牛规程架子牛育肥（精补料 1.2%~1.4% 体重）（估算整理）',
  },
  {
    // 成长期牛只（育成期）：CP 由 14% 渐降至 13%，日增重 1.1~1.3kg
    species_code: 'cattle',
    variety_code: 'cattle_beef',
    stage: 'growing',
    sex_type: 'all',
    min_weight: 150,
    max_weight: 250,
    nutrition: {
      daily_feed_kg_per_head: 7.0,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '13~15%', min: 13, max: 15, unit: '%' },
        { name: '消化能', required: '≥12.0 MJ/kg', min: 12.0, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.4~0.8%', min: 0.4, max: 0.8, unit: '%' },
      ],
    },
    formula: [{ name: '玉米', ratio_pct: 55 }, { name: '豆粕', ratio_pct: 15 }, { name: '麸皮', ratio_pct: 14 }, { name: '干草粉', ratio_pct: 13 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: 'NY/T 815《肉牛饲养标准》育成牛段（12 月龄 DMI 8.5~11kg、CP 14%→13%）（估算整理）',
  },
  {
    // 怀孕母牛（妊娠期全期均值档）：前期以优质粗料为主，后期（≥180 天）精料增至 2~3kg/d
    species_code: 'cattle',
    variety_code: 'cattle_beef',
    stage: 'gestation',
    sex_type: 'female',
    min_weight: 350,
    max_weight: 600,
    nutrition: {
      daily_feed_kg_per_head: 9.0,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '11~13%', min: 11, max: 13, unit: '%' },
        { name: '消化能', required: '≥10.5 MJ/kg', min: 10.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.8%', min: 0.5, max: 0.8, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 40 },
      { name: '豆粕', ratio_pct: 10 },
      { name: '麸皮', ratio_pct: 12 },
      { name: '干草粉', ratio_pct: 30 },
      { name: '青贮玉米', ratio_pct: 5 },
      { name: '预混料', ratio_pct: 3 },
    ],
    weather_adjust: null,
    source: '优质肉牛饲养管理规程妊娠母牛段（前期精料 1.5~2kg、后期 2~3kg + 优质粗料）· 肉牛养殖技术要点（估算整理）',
  },
  {
    // 哺乳母牛（泌乳期）：产犊前 30 天~产后 70 天为营养关键期，增料促乳
    species_code: 'cattle',
    variety_code: 'cattle_beef',
    stage: 'lactation',
    sex_type: 'female',
    min_weight: 350,
    max_weight: 600,
    nutrition: {
      daily_feed_kg_per_head: 10.0,
      feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '11~13%', min: 11, max: 13, unit: '%' },
        { name: '消化能', required: '≥10.5 MJ/kg', min: 10.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.5~0.9%', min: 0.5, max: 0.9, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 42 },
      { name: '豆粕', ratio_pct: 12 },
      { name: '麸皮', ratio_pct: 12 },
      { name: '干草粉', ratio_pct: 26 },
      { name: '青贮玉米', ratio_pct: 5 },
      { name: '预混料', ratio_pct: 3 },
    ],
    weather_adjust: null,
    source: '呼伦贝尔市农牧局肉牛技术要点（哺乳母牛 DMI 9~11kg、CP 10%~11%，取上限整理）· 优质肉牛规程哺乳期精料 CP≥16%（估算整理）',
  },
  {
    // 育成牛（成长期）
    species_code: 'cattle',
    variety_code: 'cattle_dairy',
    stage: 'growing',
    sex_type: 'all',
    min_weight: 200,
    max_weight: 350,
    nutrition: {
      daily_feed_kg_per_head: 8.0,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '13~15%', min: 13, max: 15, unit: '%' },
        { name: '消化能', required: '≥12.0 MJ/kg', min: 12.0, max: null, unit: 'MJ/kg' },
      ],
    },
    formula: [{ name: '玉米', ratio_pct: 55 }, { name: '豆粕', ratio_pct: 15 }, { name: '麸皮', ratio_pct: 14 }, { name: '干草粉', ratio_pct: 13 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: 'NY/T 815《肉牛饲养标准》育成牛段（估算整理）',
  },
  {
    // 泌乳牛（未设阶段时的母牛兜底档；泌乳期精料按产奶量浮动）
    species_code: 'cattle',
    variety_code: 'cattle_dairy',
    stage: null,
    sex_type: 'female',
    min_weight: 450,
    max_weight: 650,
    nutrition: {
      daily_feed_kg_per_head: 12.0,
      feeds_per_day: 3,
      indicators: [
        { name: '粗蛋白', required: '14~16%', min: 14, max: 16, unit: '%' },
        { name: '钙', required: '0.6~0.9%', min: 0.6, max: 0.9, unit: '%' },
      ],
    },
    formula: [{ name: '玉米', ratio_pct: 52 }, { name: '豆粕', ratio_pct: 18 }, { name: '麸皮', ratio_pct: 14 }, { name: '干草粉', ratio_pct: 13 }, { name: '预混料', ratio_pct: 3 }],
    weather_adjust: null,
    source: 'NY/T 815《牛饲养标准》泌乳牛段 · 奶牛饲养管理行标泌乳前期段（CP 14%~15%、DMI 体重 3.0%~3.2%）（估算整理）',
  },
  {
    // 怀孕母牛（奶牛干奶/妊娠后期）：CP 12~14%，围产期前置钙调控随阶段 12 精细化
    // （置于兜底档之后：matchNutritionStandard 的 bySex 兜底取池内首条 female 档）
    species_code: 'cattle',
    variety_code: 'cattle_dairy',
    stage: 'gestation',
    sex_type: 'female',
    min_weight: 450,
    max_weight: 650,
    nutrition: {
      daily_feed_kg_per_head: 9.5,
      feeds_per_day: 2,
      indicators: [
        { name: '粗蛋白', required: '12~14%', min: 12, max: 14, unit: '%' },
        { name: '消化能', required: '≥10.5 MJ/kg', min: 10.5, max: null, unit: 'MJ/kg' },
        { name: '钙', required: '0.6~0.9%', min: 0.6, max: 0.9, unit: '%' },
      ],
    },
    formula: [
      { name: '玉米', ratio_pct: 45 },
      { name: '豆粕', ratio_pct: 12 },
      { name: '麸皮', ratio_pct: 14 },
      { name: '干草粉', ratio_pct: 25 },
      { name: '预混料', ratio_pct: 4 },
    ],
    weather_adjust: null,
    source: '奶牛饲养管理行标干奶/围产前期段（CP 12%~14%）· 优质肉牛规程妊娠后期（估算整理）',
  },
];

// ===== 替代配方变体模板（F06 PRD FR-03：方案间原料替代关系在 label 标注）=====
// key = `${variety_code}|${stage ?? ''}`；无变体的标准走 SUBSTITUTE_MAP 等比替换兜底。

export interface AltTemplate {
  /** label 后缀（如"稻谷替代·成本最低"），主模板变体由替换逻辑命名 */
  label: string;
  formula: FormulaItem[];
  /**
   * 仅当排除这些原料之一时该变体才参与候选（语义化替代方案：
   * 如"菜籽粕替代·营养平衡"是豆粕缺货的专属预案，无约束时不与基础配方重复出现）
   */
  requiresExclude?: string[];
}

export const ALT_TEMPLATES: Record<string, AltTemplate[]> = {
  'pig_fattening|fattening': [
    {
      label: '菜籽粕替代·营养平衡',
      requiresExclude: ['豆粕'],
      formula: [
        { name: '玉米', ratio_pct: 68 },
        { name: '麸皮', ratio_pct: 9 },
        { name: '菜籽粕', ratio_pct: 18 },
        { name: '预混料', ratio_pct: 5 },
      ],
    },
    {
      label: '稻谷替代·成本最低',
      formula: [
        { name: '玉米', ratio_pct: 56 },
        { name: '稻谷', ratio_pct: 10 },
        { name: '麸皮', ratio_pct: 12 },
        { name: '菜籽粕', ratio_pct: 17 },
        { name: '预混料', ratio_pct: 5 },
      ],
    },
    {
      label: '鱼粉替代·营养更优',
      formula: [
        { name: '玉米', ratio_pct: 68 },
        { name: '麸皮', ratio_pct: 10 },
        { name: '鱼粉', ratio_pct: 3 },
        { name: '菜籽粕', ratio_pct: 14 },
        { name: '预混料', ratio_pct: 5 },
      ],
    },
  ],
};

// ===== 原料替换映射（排除原料约束的兜底等比替换，FR-02：替换原料须在价格库内）=====

export const SUBSTITUTE_MAP: Record<string, string[]> = {
  豆粕: ['菜籽粕', '鱼粉'],
  玉米: ['稻谷', '麸皮'],
  麸皮: ['稻谷'],
  菜籽粕: ['豆粕'],
  稻谷: ['玉米'],
  鱼粉: ['豆粕'],
  预混料: [],
  青贮玉米: [],
  干草粉: ['青贮玉米'],
};

// ===== 原料营养表（估算，公开饲养标准整理；营养校验按配比加权逐项比对）=====
// cp_pct 粗蛋白 / lys_pct 赖氨酸 / de_mj 消化能 MJ/kg / ca_pct 钙

export interface IngredientNutrition {
  cp_pct: number;
  lys_pct: number;
  de_mj: number;
  ca_pct: number;
}

export const INGREDIENT_NUTRITION: Record<string, IngredientNutrition> = {
  玉米: { cp_pct: 8.0, lys_pct: 0.24, de_mj: 14.5, ca_pct: 0.02 },
  麸皮: { cp_pct: 15.7, lys_pct: 0.58, de_mj: 11.0, ca_pct: 0.11 },
  豆粕: { cp_pct: 43.0, lys_pct: 2.45, de_mj: 14.3, ca_pct: 0.3 },
  菜籽粕: { cp_pct: 36.0, lys_pct: 1.3, de_mj: 12.0, ca_pct: 0.65 },
  稻谷: { cp_pct: 7.8, lys_pct: 0.29, de_mj: 12.0, ca_pct: 0.07 },
  鱼粉: { cp_pct: 62.0, lys_pct: 4.5, de_mj: 12.5, ca_pct: 4.5 },
  预混料: { cp_pct: 0, lys_pct: 6.0, de_mj: 0, ca_pct: 12.0 },
  青贮玉米: { cp_pct: 2.1, lys_pct: 0.05, de_mj: 2.6, ca_pct: 0.1 },
  干草粉: { cp_pct: 8.8, lys_pct: 0.28, de_mj: 7.0, ca_pct: 0.57 },
};

// ===== 原料价格种子（02-数据库 5.3 内置参考价 + 替代原料扩展；用户可改价）=====
// days_ago 相对建库日；稻谷 45 天未更新用于 EX-17 灰标演示（TC-F06）
//
// 【2026-10-14 按真实行情校准】原种子为开发期估算值，经 2026-10-13/14 多源交叉核对修正
// （口径：全国主要市场批发/收购均价，元/kg = 元/吨 ÷ 1000；实际采购价随地区波动，以改价为准）：
// - 玉米 2.20：农业农村部 500 县集贸监测 2.43（10-08）、行情宝 2.392（10-14）、
//   生意社 2.171（10-13）、Mysteel 指数 2.098（10-13）——取主流批发中枢，原 2.60 偏高 18%
// - 豆粕 3.35：生意社 3.408（10-13）、行情宝 3.223（10-14）、沿海现货 3.30~3.36——原 3.80 偏高 13%
// - 麸皮 1.50：Mysteel 全国粉企汇总（10-14）河北 1.32~1.48、河南 1.41~1.54——原 2.00 偏高 33%
// - 菜籽粕 2.48：生意社 2482 元/吨（10-13）——原 2.90 偏高 17%
// - 稻谷 2.50：湖北省粮局中晚籼收购均价 2.478、早籼 2.511（10-14），国家托市底价 1.29 元/斤=2.58
//   ——原 2.40 略低于托市价，上调
// - 鱼粉 19.00：卓创（10-13）国产 65%+ 蛋白 18.5~19.0、进口超级蒸汽 23.6~23.9（秘鲁 B 季
//   减产预期下急涨）——原 9.00 严重失真（低 2 倍多），按国产高光蛋白主流价修正
// - 预混料 6.50：4% 猪用预混料市面 6~15 元/kg 区间，取入门档；不同配方商差异大，演示参考值
// - 青贮玉米 0.80：全株青贮地头收储常见 0.6~0.9 元/kg，维持
// - 干草粉 1.30：无全国统一行情（苜蓿 1.5~2.5、禾草/秸秆粉 0.6~1.0），取中间参考值

export interface FeedPriceSeed {
  ingredient_name: string;
  unit_price: number;
  days_ago: number;
}

/** 价格种子数据口径标定日（mock 库惰性迁移按此判定是否刷新内置价，用户改价不受影响） */
export const FEED_PRICE_CALIBRATION = '2026-10-14';

export const FEED_PRICE_SEED: FeedPriceSeed[] = [
  { ingredient_name: '玉米', unit_price: 2.2, days_ago: 5 },
  { ingredient_name: '豆粕', unit_price: 3.35, days_ago: 5 },
  { ingredient_name: '麸皮', unit_price: 1.5, days_ago: 5 },
  { ingredient_name: '菜籽粕', unit_price: 2.48, days_ago: 5 },
  { ingredient_name: '稻谷', unit_price: 2.5, days_ago: 45 },
  { ingredient_name: '鱼粉', unit_price: 19.0, days_ago: 3 },
  { ingredient_name: '预混料', unit_price: 6.5, days_ago: 5 },
  { ingredient_name: '青贮玉米', unit_price: 0.8, days_ago: 12 },
  { ingredient_name: '干草粉', unit_price: 1.3, days_ago: 7 },
];

// ===== 品种饲喂参数（02-数据库 5.2 variety.feed_param 种子）=====
// standard_daily_gain_kg：日增重 vs 标准判定的基准（FR-08）；null = 无统一标准（前端不画标准线）

export const STANDARD_DAILY_GAIN: Record<string, number | null> = {
  pig_fattening: 0.75, // 30~110kg 阶段均值（02-数据库 5.2 原文）
  pig_piglet: 0.45, // 保育期估算
  pig_sow: null, // 母猪分阶段差异大，V1.0 不设统一标准
  // 羊/牛（2026-10-14 配食分类完善补齐；育肥/育成期估算值，母畜生理阶段由 stage 参数置 null）
  sheep_hu: 0.25, // 育肥期日增重 200~250g（NY/T 816 / 舍饲肉羊技术规程整理估算）
  sheep_small_tailed_han: 0.28, // 多胎肉羊育肥日增重偏高（300g 档下沿，估算）
  cattle_beef: 1.2, // 育成~育肥期日增重 1.1~1.3kg（肉牛饲养管理行标整理估算）
  cattle_dairy: null,
  common: null,
};

/**
 * 取品种日增重标准线（FR-08）。
 * stage 传妊娠/泌乳时返回 null——母畜生理阶段以维持+繁殖为目标，不适用育肥日增重标准线
 * （前端不画标准线，GainChart 已兼容 null）。
 */
export function getStandardDailyGain(varietyCode: string | null, stage?: string | null): number | null {
  if (stage === 'gestation' || stage === 'lactation') return null;
  return STANDARD_DAILY_GAIN[varietyCode ?? ''] ?? null;
}
