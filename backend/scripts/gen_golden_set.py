"""从 kb_seed.json 机械生成问诊检索黄金集（不手标、不抄中文领域数据）。

为什么能机械生成：`kb_seed.json` 的每条疾病本来就带 `symptom_keywords` 和 `risk_level`
（后者是 F05 决策"风险不由模型判定"的依据），所以"症状描述 → 期望命中疾病 → 期望风险"
这三元组的原料都在库里，脚本只是把它们拼成自然语句。

三条防止"评测自己骗自己"的设计：

1. **期望值是集合不是单值**：`咳嗽/减食` 这类关键词在多条疾病里共用，要求 top-k 必中某一条
   具体疾病是错的。故 `expected_any` = 同物种下**包含了所用全部关键词**的所有疾病，命中其中任一条即算召回。
2. **负例可验证**：无证据样本的措辞必须与该物种全部关键词**零交集**，生成时断言检查；
   将来有人给某条疾病加了个关键词恰好撞上这些措辞，生成阶段就报错，而不是让评测悄悄变绿。
3. **阶段/体重只用于文案**，不进期望判定——营养阶段词与检索无关，写进期望会把评测绑死在措辞上。

用法：
    python scripts/gen_golden_set.py                 # 每条疾病 1 个正例（默认）
    python scripts/gen_golden_set.py --per-disease 2 # 每条 2 个不同关键词组合
输出：backend/golden_consult_cases.json
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SEED = BACKEND / "app" / "agents" / "kb_seed.json"
OUT = BACKEND / "golden_consult_cases.json"

SPECIES_WORD = {"pig": "猪", "sheep": "羊", "cattle": "牛"}

# 与任何症状词都不该有交集的"无证据主诉"（EX-04 负例）
NO_EVIDENCE_SIGNS = [
    "尾巴一直摇摆不停",
    "在圈里转了三圈就趴下了",
    "今天喂料时它看了我一眼",
    "鼻镜上停了一只蚊子",
    "睡觉时后腿蹬到了栏位",
    "吃的时候把料槽顶歪了",
    "耳朵一直在抖",
    "走路时尾巴竖得笔直",
    "对着料槽叫了两声",
    "趴着的时候头朝着北边",
]


def build_cases(entries: list[dict], per_disease: int, seed_no: int) -> list[dict]:
    rng = random.Random(seed_no)
    cases: list[dict] = []

    by_species: dict[str, list[dict]] = {}
    for e in entries:
        by_species.setdefault(e["species_code"], []).append(e)

    for species, group in by_species.items():
        word = SPECIES_WORD.get(species, species)
        union = {kw for e in group for kw in e["symptom_keywords"]}
        for e in group:
            kws = list(e["symptom_keywords"])
            for idx in range(min(per_disease, max(1, len(kws) // 2))):
                n = 3 if len(kws) >= 3 else 2
                used = kws[idx: idx + n] if idx + n <= len(kws) else kws[:n]
                used = [k for k in used if k]
                if len(used) < 2:
                    continue
                message = f"{word}{'、'.join(used)}，精神不太好"
                matched_group = [x for x in group if all(k in x["symptom_keywords"] for k in used)]
                expected_any = sorted({x["disease_id"] for x in matched_group})
                # 向量通路返回的条目不带 disease_id（只有 disease_name，实测 2026-10-15），
                # 所以期望值必须同时给名字，否则评测会把向量通路误算成 0 召回
                expected_names = sorted({x["disease_name"] for x in matched_group})
                cases.append({
                    "kind": "positive",
                    "species_code": species,
                    "has_female": bool(e.get("female_only")),
                    "message": message,
                    "used_keywords": used,
                    "expected_any": expected_any,
                    "expected_names": expected_names,
                    "expected_risk": e["risk_level"],
                    "source_disease": e["disease_name"],
                })

        # 负例：措辞与该物种全部关键词零交集
        for sign in NO_EVIDENCE_SIGNS:
            overlap = [k for k in union if k in sign]
            if overlap:
                raise SystemExit(
                    f"负例措辞「{sign}」与关键词 {'、'.join(overlap)} 撞上，"
                    "知识库扩充后需要换措辞——否则 EX-04 评测会假绿"
                )
            cases.append({
                "kind": "negative",
                "species_code": species,
                "has_female": False,
                "message": f"{word}生病了，症状是{sign}",
                "expected_any": [],
                "expected_risk": None,
                "source_disease": None,
            })
    rng.shuffle(cases)  # 打散正负顺序，避免评测实现依赖排列
    return cases


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--per-disease", type=int, default=1, help="每条疾病生成几个正例（默认 1）")
    parser.add_argument("--seed", type=int, default=20261015, help="固定随机种子，保证可复现")
    args = parser.parse_args()

    entries = json.loads(SEED.read_text(encoding="utf-8"))["entries"]
    cases = build_cases(entries, args.per_disease, args.seed)
    positives = [c for c in cases if c["kind"] == "positive"]
    negatives = [c for c in cases if c["kind"] == "negative"]

    payload = {
        "version": 1,
        "generated_from": "app/agents/kb_seed.json",
        "note": "期望值由知识库自身字段机械推导；expected_any 为集合，命中任一即算召回",
        "counts": {"positive": len(positives), "negative": len(negatives), "total": len(cases)},
        "cases": cases,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"黄金集写入 {OUT.name}：正例 {len(positives)}、负例 {len(negatives)}，共 {len(cases)} 条")
    print(f"覆盖疾病条目：{len({p['source_disease'] for p in positives})} / {len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
