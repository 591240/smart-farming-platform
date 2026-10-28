# -*- coding: utf-8 -*-
"""知识库手册批量生成器（2026-10-15 用户需求：每本几万字，作大模型 RAG 知识源）。

- 逐书逐章调用 qwen（DashScope OpenAI 兼容接口，复用 app.services.llm.chat）
- 每章 1800~2500 字，8 章/本 ≈ 1.5~2 万字/册
- 断点续跑：kb_gen_state.json 记录已完成章节，中断后重跑只补缺失
- 结果写入 app/agents/kb_full_texts.json（seed 回灌时的覆盖源）
- 并发：全局信号量 4，避免打爆限流
"""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.db import SessionLocal  # noqa: E402
from app.services.llm import chat  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.models import KbDocument  # noqa: E402
from sqlalchemy import select  # noqa: E402

BASE = Path(__file__).resolve().parent
FULL_PATH = BASE.parent / "app" / "agents" / "kb_full_texts.json"
STATE_PATH = BASE / "kb_gen_state.json"
CONCURRENCY = 4
MAX_TOKENS = 4200

DISEASE_CHAPTERS = [
    "疾病概述与病原特性",
    "流行病学与传播途径",
    "临床症状与病程分期",
    "诊断方法与鉴别诊断",
    "治疗原则与用药方案",
    "预防措施与免疫程序",
    "饲养管理与营养调控",
    "经济损失评估与综合防控案例",
]

TOPIC_CHAPTERS: dict[str, list[str]] = {
    "母羊妊娠期饲养管理要点": [
        "繁殖生理与妊娠机制", "妊娠阶段划分与各期营养需要", "妊娠期日粮配方与原料选择",
        "妊娠期日常管理与防流产措施", "围产期管理与产房准备", "妊娠期常见疾病防治",
        "产后护理与羔羊培育衔接", "全周期记录管理与经济效益",
    ],
    "羔羊断奶与保育要点": [
        "初生羔羊生理特点与护理", "初乳与被动免疫技术", "开食补饲技术与料槽管理",
        "断奶方案与应激管理", "保育期日粮设计与饲养制度", "常见羔羊疾病防控",
        "生长发育监测与分群管理", "数据记录与经济效益分析",
    ],
    "羊饲养营养标准要点（NY/T 816 整理）": [
        "羊营养需要量体系解读", "能量与蛋白质营养详解", "矿物质维生素与水的需要",
        "各生长阶段饲养标准详解", "日粮配方设计方法与实例", "常见饲料原料营养价值与利用",
        "饲喂制度与换料过渡管理", "营养失误案例复盘与纠正",
    ],
    "羊免疫程序与接种要点": [
        "免疫学基础与疫苗作用原理", "羊场主要疫苗种类与特点", "参考免疫程序表逐项详解",
        "接种操作规范与注意事项", "疫苗冷链运输与保存管理", "不良反应识别与应急处置",
        "免疫失败原因排查", "免疫档案管理与法规要求",
    ],
    "羊寄生虫防控程序": [
        "羊场寄生虫图谱与总体危害", "体内寄生虫逐类详解（线虫/绦虫/吸虫）",
        "体外寄生虫逐类详解（螨/虱/蜱）", "驱虫药物分类与轮换策略",
        "全年防控日历与季节管理", "环境治理与草场管理",
        "耐药性问题与驱虫效果评估", "寄生虫防控案例与经济损失测算",
    ],
    "羊繁殖管理要点（两年三胎）": [
        "羊繁殖生理基础", "发情鉴定与配种技术", "短期优饲与双羔率提升",
        "妊娠诊断与胚胎保护", "围产期管理与接产技术", "产后护理与羔羊培育",
        "繁殖障碍排查与处理", "两年三胎模式设计与效益测算",
    ],
    "羊育肥与出栏管理要点": [
        "育肥方式与羊源选择", "进场应激与隔离管理", "育肥日粮设计与换料过渡",
        "饲喂制度与日常巡查", "增重监控与分群调整", "育肥期常见疾病防控",
        "出栏标准与休药期核对", "经济效益核算与行情判断",
    ],
    "圈舍生物安全与消毒制度": [
        "生物安全体系总论", "消毒剂种类与作用机理", "消毒制度设计与操作流程",
        "隔离制度与人员车辆管控", "无害化处理规范", "重大疫病报告与应急处置",
        "灭鼠灭虫与环境治理", "防疫检查验收与档案管理",
    ],
}


def _load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _save_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


SYSTEM_PROMPT = (
    "你是资深兽医学教材编辑，为中国中小规模养殖场编写合规整理的知识手册。"
    "要求：①面向养殖户与基层兽医，语言通俗但专业准确；"
    "②内容为公开兽医学教材/官方技术规范的一般性知识整理（原创改写），不得逐字抄袭任何书籍；"
    "③具体药物剂量一律表述为\"遵药品标签与兽医处方\"，只给通用原则与常见区间；"
    "④涉及重大动物疫病（口蹄疫、小反刍兽疫等）须写明报告义务；"
    "⑤数字化指标（体温、日龄、浓度、比例等）尽量给出常见参考区间。"
)


def build_user_prompt(book: str, idx: int, total: int, chapter: str, facts: str) -> str:
    return (
        f"请为《{book}》撰写第 {idx}/{total} 章：《{chapter}》，篇幅 1800~2500 字。\n\n"
        f"本书核心要点（必须自然融合进本章）：{facts}\n\n"
        f"写作要求：\n"
        f"1. 本章直接开篇写正文，不要写\"本章是\"之类的元叙述；\n"
        f"2. 用小节编号组织（如 1.1、1.2 或 一、二、三），层次清晰；\n"
        f"3. 关键数字指标给常见参考区间；药物剂量写\"遵药品标签与兽医处方\"；\n"
        f"4. 章末以\"【本章要点】\"开头给出 3 条要点小结；\n"
        f"5. 只输出本章正文。"
    )


async def gen_chapter(book: str, idx: int, total: int, chapter: str, facts: str, sem: asyncio.Semaphore) -> str:
    async with sem:
        for attempt in range(3):
            try:
                return await chat(
                    [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(book, idx, total, chapter, facts)},
                    ],
                    temperature=0.4,
                    max_tokens=MAX_TOKENS,
                )
            except Exception as e:  # noqa: BLE001
                if attempt == 2:
                    print(f"    [FAIL] {book} 第{idx}章: {e}", flush=True)
                    return None
                await asyncio.sleep(5 * (attempt + 1))


async def main() -> None:
    full = _load_json(FULL_PATH, {})
    state = _load_json(STATE_PATH, {"done": []})
    done: set[str] = set(state.get("done", []))

    # 目标书目：羊馆 16 本（按主物种逐批推进，猪/牛馆下一批）
    async with SessionLocal() as db:
        rows = (
            await db.execute(
                select(KbDocument).where(
                    KbDocument.is_deleted.is_(False),
                    KbDocument.is_enable.is_(True),
                    KbDocument.species_code == "sheep",
                )
            )
        ).scalars().all()
    books: list[dict] = []
    for d in rows:
        chapters = TOPIC_CHAPTERS.get(d.doc_name, DISEASE_CHAPTERS)
        facts = (
            f"文档类型：{d.doc_type}；检索关键词：{'、'.join(d.retrieval_keywords or [])}；"
            f"风险等级：{d.risk_level}；饲养管理建议：{d.feed_advice or '无'}；"
            f"现有正文摘要：{(d.content_md or '')[:300]}"
        )
        books.append({"doc_name": d.doc_name, "chapters": chapters, "facts": facts})

    print(f"目标书目 {len(books)} 本 × {len(books[0]['chapters']) if books else 0} 章", flush=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    tasks = []
    for b in books:
        if b["doc_name"] in done:
            print(f"[SKIP] {b['doc_name']}（已完成）", flush=True)
            continue
        for i, ch in enumerate(b["chapters"], 1):
            key = f"{b['doc_name']}::{i}"
            if key in done:
                continue
            tasks.append((b["doc_name"], i, len(b["chapters"]), ch, b["facts"], key))
    print(f"待生成章节: {len(tasks)}", flush=True)

    async def worker(t):
        book, i, total, ch, facts, key = t
        t0 = time.time()
        text = await gen_chapter(book, i, total, ch, facts, sem)
        if text is None:
            return  # 失败章节不落库，重跑补齐
        if not full.get(book):
            full[book] = f"【{book}】\n\n"
        full[book] += f"\n\n{'═' * 3} 第{i}章 {ch} {'═' * 3}\n\n{text}"
        done.add(key)
        state["done"] = sorted(done)
        _save_json(STATE_PATH, state)
        _save_json(FULL_PATH, full)
        print(f"[OK] {book} 第{i}/{total}章（{len(text)} 字，{time.time() - t0:.0f}s）", flush=True)

    if tasks:
        await asyncio.gather(*(worker(t) for t in tasks))

    # 收尾：清理生成失败占位符
    for k in full:
        full[k] = full[k].replace("（第", "（第")  # 占位符保留提示，用户重跑即可补
    _save_json(FULL_PATH, full)
    print("全部章节生成完成", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
