"""问诊检索评测 —— 用黄金集量化"检索得好不好"，而不是靠人眼看几条。

冒烟测的是契约（状态码/字段/权限），本脚本测的是**效果**，两件事不能互相替代：
C-19 那个 bug（无意义主诉也被语义检索命中 → EX-04 不触发、中危被判高危）
在 225 条冒烟里全绿，只有评测集能抓到它。

三个指标 + 一个阈值建议：

- `recall@k`：正例的 `expected_any`（同物种下包含所用全部关键词的疾病集合）命中 top-k 的比例。
  期望是集合，所以不会因为"咳嗽"同时属于多条疾病而误判；
- `false_recall`：负例（无证据主诉）本应空手而归，却被检索到结果的比例——这正是 EX-04 失效率；
- `risk_accuracy`：命中时 `max_risk(命中)` 与知识库 `risk_level` 一致的比例（风险不由模型判定，所以这个数必须能测）；
- **阈值分布**：把所有正例的 top 分数与所有负例的 top 分数摆出来，看能不能分开。
  分不开就是"cosine 本身不够"的证据，此时结论不是"调个阈值"而是"必须加关键词交集门"。

模式：
    --mode keyword  只走关键词计分（不花钱、不调 embedding，可当免费回归门）
    --mode vector   只走向量语义检索（默认，需要 Milvus + embedding key）
    --mode both     两条都跑并对比——C-19 的定性证据就是这个对比

用法：
    DATABASE_URL=...smart_farm_test python scripts/eval_consult.py --mode both
    ... --min-recall 0.9 --max-false-recall 0.05   # 低于门槛退出码 1（CI 阻断）
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.agents import kb  # noqa: E402
from app.agents.kb import search_diseases, search_diseases_smart  # noqa: E402

CASES = BACKEND / "golden_consult_cases.json"
TOP_K = 5


async def retrieve(message: str, species: str, has_female: bool, mode: str, gate: bool = False) -> tuple[list[dict], str]:
    """返回 (命中列表, 实际走的通路)。keyword 模式直调关键词路径，绕开 embedding 调用。

    gate=True 时叠加"关键词交集门"候选修法：向量命中必须至少有一个 `symptom_keywords`
    字面出现在主诉里才算数——用来在改 `kb.py` 之前先量化这个门的效果。
    """
    if mode == "keyword":
        matched, _ = search_diseases(message, species, has_female, TOP_K)
        return matched, "keyword"
    matched, _ = await search_diseases_smart(message, species, has_female, TOP_K)
    path = "vector" if (matched and matched[0].get("vector_score") is not None) else "keyword-fallback"
    if gate and path == "vector":
        kept = [m for m in matched if any(k in message for k in m.get("symptom_keywords") or [])]
        if not kept:  # 门内全部滤掉 → 与 mock 同款回退关键词通路
            matched, _ = search_diseases(message, species, has_female, TOP_K)
            path = "vector+gate→keyword"
        else:
            matched = kept
            path = "vector+gate"
    return matched, path


def expected_set(c: dict) -> set[str]:
    """期望命中集合：id 与名字都收——向量通路返回的条目不带 disease_id（实测），只比 id 会误判 0 召回。"""
    return set(c.get("expected_any") or []) | set(c.get("expected_names") or [])


def hit_keys(m: dict) -> set[str]:
    return {str(m.get("disease_id") or ""), str(m.get("disease_name") or "")} - {""}


async def run(cases: list[dict], mode: str, gate: bool = False, risk_from: str = "set") -> dict[str, Any]:
    pos = [c for c in cases if c["kind"] == "positive"]
    neg = [c for c in cases if c["kind"] == "negative"]
    hits = risk_ok = risk_total = false_recall = 0
    # 三种判级口径同批测量（一次 embedding 都不多花）：
    #   set   = top5 候选取 max（曾为生产口径）
    #   top1  = 主疑似自己的 risk_level
    #   shown = 主疑似 + 实际展示给用户的鉴别（matched[:2]）取 max
    risk_ok_by = {"set": 0, "top1": 0, "shown": 0}
    pos_scores: list[float] = []
    neg_scores: list[float] = []
    paths: dict[str, int] = {}
    failures: list[str] = []

    for c in pos:
        matched, path = await retrieve(c["message"], c["species_code"], c["has_female"], mode, gate)
        paths[path] = paths.get(path, 0) + 1
        want = expected_set(c)
        got = {k for m in matched[:TOP_K] for k in hit_keys(m)}
        scores = [float(m["vector_score"]) for m in matched[:TOP_K] if m.get("vector_score") is not None]
        if scores:
            pos_scores.append(max(scores))
        if got & want:
            hits += 1
            expected_risk = kb.max_risk([m for m in matched if hit_keys(m) & want])
            by_mode = {
                "set": kb.max_risk(matched[:TOP_K]),
                "top1": matched[0]["risk_level"] if matched else "low",
                "shown": kb.max_risk(matched[:2]) if matched else "low",
            }
            risk_total += 1
            for k, v in by_mode.items():
                if v == expected_risk or c["expected_risk"] == v:
                    risk_ok_by[k] += 1
            got_risk = by_mode[risk_from]
            if got_risk == expected_risk or c["expected_risk"] == got_risk:
                risk_ok += 1
            else:
                failures.append(f"[风险] {c['message'][:34]} 期望={c['expected_risk']} 实得={got_risk}")
        else:
            failures.append(f"[漏召] {c['message'][:34]} 期望={sorted(want)[:2]} 实得={[m.get('disease_name') for m in matched[:3]]}")

    for c in neg:
        matched, path = await retrieve(c["message"], c["species_code"], c["has_female"], mode, gate)
        paths[path] = paths.get(path, 0) + 1
        scores = [float(m["vector_score"]) for m in matched if m.get("vector_score") is not None]
        if scores:
            neg_scores.append(max(scores))
        if matched:
            false_recall += 1

    return {
        "mode": mode,
        "positive": len(pos),
        "negative": len(neg),
        "recall_at_k": round(hits / len(pos), 4) if pos else None,
        "false_recall_rate": round(false_recall / len(neg), 4) if neg else None,
        "risk_accuracy": round(risk_ok / risk_total, 4) if risk_total else None,
        "risk_accuracy_by": {k: (round(v / risk_total, 4) if risk_total else None) for k, v in risk_ok_by.items()},
        "paths": paths,
        "pos_scores": pos_scores,
        "neg_scores": neg_scores,
        "failures": failures[:8],
    }


def threshold_verdict(res: dict[str, Any]) -> None:
    pos, neg = res["pos_scores"], res["neg_scores"]
    if not pos or not neg:
        print("  （无向量分数，阈值分析跳过——当前走的是关键词通路）")
        return
    lo_p, hi_p = min(pos), max(pos)
    lo_n, hi_n = min(neg), max(neg)
    print(f"  正例 top 分数分布：{lo_p:.3f} ~ {hi_p:.3f}（n={len(pos)}）")
    print(f"  负例 top 分数分布：{lo_n:.3f} ~ {hi_n:.3f}（n={len(neg)}）")
    if lo_p > hi_n:
        mid = (lo_p + hi_n) / 2
        print(f"  ✓ 可分：阈值定在 {mid:.3f} 以上即可同时保证不漏召、不误召（仅需调 vector_min_score）")
    else:
        overlap = lo_p - hi_n
        print(f"  ✗ 不可分：负例最高 {hi_n:.3f} ≥ 正例最低 {lo_p:.3f}（重叠 {abs(overlap):.3f}）")
        print("    → 结论不是「调阈值」，而是 cosine 单独不足以判定相关性：需要关键词交集门（见 docs/08 C-19）")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["vector", "keyword", "both"], default="vector")
    parser.add_argument("--risk-from", choices=["set", "top1", "shown"], default="set",
                        help="风险口径：set=top5 取 max；top1=主疑似自身；shown=主疑似+展示鉴别取 max")
    parser.add_argument("--gate", action="store_true", help="叠加「关键词交集门」候选修法后重测")
    parser.add_argument("--min-recall", type=float, default=0.9)
    parser.add_argument("--max-false-recall", type=float, default=0.05)
    args = parser.parse_args()

    if not CASES.exists():
        print("缺 golden_consult_cases.json，先跑 scripts/gen_golden_set.py")
        return 2
    cases = json.loads(CASES.read_text(encoding="utf-8"))["cases"]
    modes = ["vector", "keyword"] if args.mode == "both" else [args.mode]

    rc = 0
    for mode in modes:
        res = asyncio.run(run(cases, mode, args.gate, args.risk_from))
        print(f"\n=== 通路：{mode}（正例 {res['positive']} / 负例 {res['negative']}）===")
        print(f"  recall@{TOP_K}      = {res['recall_at_k']}")
        print(f"  误召回率(负例)      = {res['false_recall_rate']}")
        print(f"  风险分级准确率      = {res['risk_accuracy']}（当前口径 {args.risk_from}）")
        by = res.get("risk_accuracy_by") or {}
        if by:
            print(f"    三口径对照：set={by.get('set')}（top5 取 max）/ top1={by.get('top1')}（主疑似自身）/ shown={by.get('shown')}（主疑似+展示鉴别取 max）")
        print(f"  实际走的通路        = {res['paths']}")
        threshold_verdict(res)
        for f in res["failures"]:
            print("   ", f)
        if (res["recall_at_k"] or 0) < args.min_recall:
            print(f"  ✗ 低于召回门槛 {args.min_recall}")
            rc = 1
        if (res["false_recall_rate"] or 0) > args.max_false_recall:
            print(f"  ✗ 误召回率高于门槛 {args.max_false_recall}（EX-04 正在被绕过）")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
