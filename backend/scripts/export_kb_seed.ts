/**
 * 知识库种子导出器 — 把前端 knowledge-data.ts 的 DISEASE_KB 机械导出为后端 JSON 种子。
 *
 * 为什么不手抄：32 条条目含中文症状关键词、休药期与溯源出处，手工转录必然出错；
 * 本脚本用 esbuild 打包后直接 import 源模块，保证前后端同源逐值一致（rules.py 的
 * MEDICINE_LIB_SEED 也是同一纪律的产物）。
 *
 * 运行（在 smart-farming-vue 目录下，需已 npm install）：
 *   npx esbuild ../backend/scripts/export_kb_seed.ts --bundle --format=esm \
 *     --outfile=../backend/scripts/_export_kb.mjs --platform=node --log-level=warning
 *   node ../backend/scripts/_export_kb.mjs
 *
 * 产物：backend/app/agents/kb_seed.json（B5 灌 Milvus kb_disease 时消费同一份 JSON）
 */
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

import { DISEASE_KB, MEDICINE_LIB_SEED } from '../../smart-farming-vue/src/lib/knowledge-data';

const here = dirname(fileURLToPath(import.meta.url));
const out = resolve(here, '../app/agents/kb_seed.json');

const entries = DISEASE_KB.map((e) => ({
  species_code: e.speciesCode,
  disease_id: e.diseaseId,
  disease_name: e.diseaseName,
  category: e.category,
  symptom_keywords: e.symptomKeywords,
  risk_level: e.riskLevel,
  female_only: e.femaleOnly ?? false,
  med_names: e.medNames,
  feed_advice: e.feedAdvice,
  source: e.source,
}));

// 自检：每条推荐药都必须能在兽药库种子里找到且非违禁（F05 FR-06 库内校验前提）
const byName = new Map(MEDICINE_LIB_SEED.filter((m) => !m.isForbidden).map((m) => [m.medName, m]));
const dangling: string[] = [];
for (const e of entries) {
  for (const name of e.med_names) {
    const med = byName.get(name);
    if (!med || med.speciesCode !== e.species_code) dangling.push(`${e.disease_name} → ${name}`);
  }
}

writeFileSync(out, JSON.stringify({ version: 1, generated_at: new Date().toISOString(), entries }, null, 2), 'utf8');
console.log(`[ok] ${entries.length} 条写入 ${out}`);
if (dangling.length) console.warn(`[warn] 药名对不上兽药库（切真后会被用药节点过滤，需人工核对）：\n  ${dangling.join('\n  ')}`);
else console.log('[ok] 全部推荐药在兽药库种子内且物种一致');
