/**
 * 喂食域种子导出器 — 把前端 feed-data.ts 的配方引擎数据机械导出为后端 JSON。
 *
 * 为什么不手抄：26 行营养标准 + 9 种原料成分 + 替代模板 + 溯源字符串，
 * 手工转录必错；且 `basis`/`source` 文案是冒烟断言的字面对象。
 * 与 export_kb_seed.ts 同一纪律：import 源模块 → dump JSON。
 *
 * 运行（在 smart-farming-vue 目录下）：
 *   npx esbuild ../backend/scripts/export_feed_seed.ts --bundle --format=esm \
 *     --outfile=../backend/scripts/_export_feed.mjs --platform=node --log-level=warning
 *   node ../backend/scripts/_export_feed.mjs
 *
 * 产物：backend/app/agents/feed_seed.json（切真后落 feed_nutrition_lib / feed_price 表）
 */
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import {
  ALT_TEMPLATES,
  FEED_NUTRITION_LIB_SEED,
  FEED_PRICE_CALIBRATION,
  FEED_PRICE_SEED,
  INGREDIENT_NUTRITION,
  STANDARD_DAILY_GAIN,
  SUBSTITUTE_MAP,
} from '../../smart-farming-vue/src/lib/feed-data';

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, '../app/agents/feed_seed.json');

// 自检：配方比例合计必须 ≈100，原料名必须既有价格又有营养成分
const priceByName = new Map(FEED_PRICE_SEED.map((p) => [p.ingredient_name, p]));
const problems: string[] = [];
for (const std of FEED_NUTRITION_LIB_SEED) {
  const sum = std.formula.reduce((a, i) => a + i.ratio_pct, 0);
  if (sum < 97 || sum > 101) problems.push(`${std.variety_code}/${std.stage ?? '-'}/${std.sex_type}: 合计 ${sum}%`);
  for (const item of std.formula) {
    if (!priceByName.has(item.name)) problems.push(`${std.variety_code}: 原料「${item.name}」无价格种子`);
    if (!INGREDIENT_NUTRITION[item.name]) problems.push(`${std.variety_code}: 原料「${item.name}」无营养成分`);
  }
}
for (const [name, subs] of Object.entries(SUBSTITUTE_MAP)) {
  if (!priceByName.has(name)) problems.push(`替代映射源「${name}」无价格种子`);
  for (const s of subs) if (!priceByName.has(s)) problems.push(`替代映射目标「${s}」无价格种子`);
}

writeFileSync(
  out,
  JSON.stringify(
    {
      version: 1,
      price_calibration: FEED_PRICE_CALIBRATION,
      generated_at: new Date().toISOString(),
      nutrition_lib: FEED_NUTRITION_LIB_SEED,
      ingredient_nutrition: INGREDIENT_NUTRITION,
      price_seed: FEED_PRICE_SEED,
      alt_templates: ALT_TEMPLATES,
      substitute_map: SUBSTITUTE_MAP,
      standard_daily_gain: STANDARD_DAILY_GAIN,
    },
    null,
    2,
  ),
  'utf8',
);
console.log(`[ok] ${FEED_NUTRITION_LIB_SEED.length} 行营养标准 + ${FEED_PRICE_SEED.length} 原料写入 ${out}`);
if (problems.length) {
  console.warn(`[warn] ${problems.length} 项数据问题：\n  ${problems.join('\n  ')}`);
  process.exitCode = 1;
} else {
  console.log('[ok] 配方比例合计与原料完备性自检通过');
}
