// 喂食中心 API — 按《03-API接口设计》六节（6.1~6.9）+ 十四节 overview / F06 PRD FR-02~10 封装
// - 配方：generate（6.3）/ alternatives（6.4）/ activate（6.5）——mock 期为规则模板引擎，
//   切真后走 LangGraph feed 子图，接口契约零改动（F06 PRD 决策 1）
// - 计划：GET /pen/{pen_id}/feed-plan（6.1 三级优先级；响应附 active_formula 为具体化扩展）
// - 登记：POST/GET /feeding/record（6.2，planned_kg 快照 + 40005 feed_over 确认）
// - 称重：POST /feeding/weight（6.7，40005 weight_drop 确认）+ daily-gain 曲线（6.8）
// - 结算：feed-conversion（6.9，无称重/未出栏 fcr=null+reason，禁止 0）
// - 价格：GET/POST /feed/price（6.6，改价同步重算生效配方）

import type { NutritionStandard } from '@/lib/feed-data';
import { apiFetch } from './client';

// ===== 契约类型 =====

export interface FormulaIngredient {
  name: string;
  ratio_pct: number;
  daily_kg_per_head: number;
}

export interface NutritionCheckItem {
  required: string;
  actual: string;
  pass: boolean;
}

export interface FormulaData {
  formula_id: number;
  pen_id: number;
  label: string;
  ingredients: FormulaIngredient[];
  feeds_per_day: number;
  cost_per_head_day: number;
  nutrition_check: Record<string, NutritionCheckItem>;
  basis: string;
  disclaimer: string;
  is_active: boolean;
  activate_time: string | null;
}

export interface FeedPlanData {
  pen_id: number;
  stage: string;
  stock: number;
  daily_plan_kg: number | null;
  meals_per_day: number | null;
  per_meal_kg: number | null;
  basis: string;
  /** 计划来源（F06 具体化扩展）：formula 生效配方 / standard 标准兜底 / none 请先生成配方 */
  plan_source: 'formula' | 'standard' | 'none';
  active_formula: FormulaData | null;
}

export interface AlternativeOption {
  formula_id: number;
  label: string;
  cost_per_head_day: number;
  nutrition_pass: boolean;
  /** 不达标营养项（nutrition_pass=false 时存在，EX-16） */
  miss?: string[];
  /** 完整配方对象（F06 具体化扩展：方案卡片展开详情/激活所需，避免二次请求） */
  formula: FormulaData;
}

export interface FeedPriceItem {
  ingredient_name: string;
  unit_price: number;
  price_date: string;
  is_builtin: boolean;
  price_age_days: number;
  price_source?: 'builtin' | 'manual' | 'llm_search' | string;
  source_site?: string;
  confidence?: string;
  /** C-18：own=本人改价覆盖（只影响自己的配方成本）；global=全平台共享参考价 */
  scope?: 'own' | 'global';
  /** 被本人覆盖时的全局参考价，用于如实对照显示 */
  global_unit_price?: number | null;
}

export interface FeedPriceUpdateResult {
  updated: boolean;
  /** 受改价影响并已重算的生效配方（TC-F05） */
  affected: { formula_id: number; pen_id: number; pen_name: string; cost_per_head_day: number }[];
}

export type FeedType = 'concentrate' | 'roughage' | 'silage' | 'other';

export interface FeedingRecordItem {
  feed_record_id: number;
  pen_id: number;
  feed_date: string;
  feed_type: FeedType;
  amount_kg: number;
  /** 登记时刻计划量快照（口径 458）；null = 无配方且无标准的手工登记 */
  planned_kg: number | null;
  as_planned: boolean;
  remark: string;
  create_time: string;
}

export interface OverviewItem {
  pen_id: number;
  pen_name: string;
  planned_kg: number | null;
  actual_kg: number | null;
  registered: boolean;
  plan_source: 'formula' | 'standard' | 'none';
  basis: string;
}

export interface FeedingRecordPostResult {
  feed_record_id: number;
  planned_kg: number | null;
  amount_kg: number;
  as_planned: boolean;
}

export interface VsStandard {
  standard: number;
  ratio: number;
  level: 'normal' | 'low';
}

export interface WeightRecordPostResult {
  weight_id: number;
  daily_gain_kg: number | null;
  vs_standard: VsStandard | null;
}

export interface DailyGainData {
  points: { date: string; avg_weight: number }[];
  daily_gain: { date: string; gain: number }[];
  standard_line: number | null;
}

export interface FeedConversionData {
  total_feed_kg: number | null;
  total_gain_kg: number | null;
  fcr: number | null;
  reason?: string;
  avg_cost_per_head_day: number | null;
  slaughter_date?: string;
  head_count?: number;
  slaughter_total_weight_kg?: number;
  first_weigh_date?: string;
  first_avg_weight_kg?: number;
}

export interface FormulaConstraints {
  exclude_ingredients?: string[];
  reason?: string;
}

// ===== 配方（FR-02~04）=====

/** FR-02 配方生成（生成结果落库为候选态，需 activate 生效） */
export function apiGenerateFormula(params: { pen_id: number; constraints?: FormulaConstraints }) {
  return apiFetch<FormulaData>('POST', '/feed/formula/generate', params);
}

/** FR-03 替代配方方案（2~3 套，横排卡片 + 成本对比） */
export function apiFormulaAlternatives(params: { pen_id: number; constraints?: FormulaConstraints }) {
  return apiFetch<{ options: AlternativeOption[] }>('POST', '/feed/formula/alternatives', params);
}

/** FR-04 激活配方（同圈旧配方失效 + 落 feed 台账） */
export function apiActivateFormula(formulaId: number) {
  return apiFetch<{ formula_id: number; is_active: boolean; ledger_record_id: number }>(
    'POST',
    `/feed/formula/${formulaId}/activate`,
  );
}

// ===== 价格（FR-05）=====

export function apiListFeedPrices() {
  return apiFetch<{ items: FeedPriceItem[] }>('GET', '/feed/price');
}

export function apiUpdateFeedPrice(params: { ingredient_name: string; unit_price: number; price_date?: string }) {
  return apiFetch<FeedPriceUpdateResult>('POST', '/feed/price', params);
}

// ===== 计划与登记（FR-06/07）=====

export function apiGetFeedPlan(penId: number) {
  return apiFetch<FeedPlanData>('GET', `/pen/${penId}/feed-plan`);
}

export function apiCreateFeedingRecord(params: {
  pen_id: number;
  feed_date?: string;
  feed_type: FeedType;
  amount_kg?: number;
  as_planned?: boolean;
  confirm?: boolean;
  remark?: string;
}) {
  return apiFetch<FeedingRecordPostResult>('POST', '/feeding/record', params);
}

export function apiListFeedingRecords(params: {
  pen_id: number;
  from?: string;
  to?: string;
  page?: number;
  page_size?: number;
}) {
  const q = new URLSearchParams();
  q.set('pen_id', String(params.pen_id));
  if (params.from) q.set('from', params.from);
  if (params.to) q.set('to', params.to);
  q.set('page', String(params.page ?? 1));
  q.set('page_size', String(params.page_size ?? 31));
  return apiFetch<{ total: number; page: number; page_size: number; items: FeedingRecordItem[] }>(
    'GET',
    `/feeding/record?${q.toString()}`,
  );
}

/** 喂食日历按天汇总（03-API 十四节）；farm_id 传入时服务端按场收敛（大屏单场视图） */
export function apiFeedingOverview(date?: string, farmId?: number) {
  const q = new URLSearchParams();
  if (date) q.set('date', date);
  if (farmId) q.set('farm_id', String(farmId));
  const s = q.toString();
  return apiFetch<{ date: string; items: OverviewItem[] }>('GET', `/feeding/overview${s ? `?${s}` : ''}`);
}

/** 分阶段饲喂标准（F06 展示口径，03-API 未编号）：读库表 feed_nutrition_lib，与配方计算同一份数据 */
export function apiFeedStandards(params?: { species_code?: string; variety_code?: string }) {
  const q = new URLSearchParams();
  if (params?.species_code) q.set('species_code', params.species_code);
  if (params?.variety_code) q.set('variety_code', params.variety_code);
  const s = q.toString();
  return apiFetch<{ total: number; items: NutritionStandard[] }>('GET', `/feed/standards${s ? `?${s}` : ''}`);
}

// ===== 称重与结算（FR-08~10）=====

export function apiCreateWeightRecord(params: {
  pen_id: number;
  weigh_date?: string;
  avg_weight_kg: number;
  sample_count: number;
  confirm?: boolean;
  remark?: string;
}) {
  return apiFetch<WeightRecordPostResult>('POST', '/feeding/weight', params);
}

export function apiGetDailyGain(penId: number) {
  return apiFetch<DailyGainData>('GET', `/pen/${penId}/daily-gain`);
}

export function apiGetFeedConversion(penId: number) {
  return apiFetch<FeedConversionData>('GET', `/pen/${penId}/feed-conversion`);
}
