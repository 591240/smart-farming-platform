"""F16 知识库文档 — 全员上传版（2026-10-15 书架改版）。

- GET  /kb/list            全员可读（farmer 按主物种过滤 + 公共文档，vet/admin 全量）
- POST /kb/upload          全员可上传（multipart：文件可选，纯文本整理稿亦可）；
                           source + organizer 必填（附录 E SOP 留痕）
- GET  /kb/doc/{id}        详情（含 content_md 正文）
- GET  /kb/doc/{id}/file   附件下载/预览（FileResponse）
- PUT  /kb/doc/{id}/enable 管理员停用/启用（停用退出问诊检索池，可恢复）
- DELETE /kb/doc/{id}      管理员软删（纪律：不物理删除）

文件落盘 backend/app/uploads/kb/（不入库，库里存元数据）；txt/md 自动抽取正文进
content_md 参与问诊检索；其余类型只存档可下载（检索依赖检索关键词）。
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal, get_db
from app.core.response import E_FORBIDDEN, E_NOT_FOUND, E_ROLE_FORBIDDEN, E_VALIDATION, ApiError, ok
from app.core.security import get_current_user
from app.models import KbDocument, User
from app.agents.kb import doc_to_kb_entry
from app.agents.vector_store import delete_doc as vec_delete_doc
from app.agents.vector_store import upsert_doc as vec_upsert_doc
from app.services.embedding import embed_texts

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "uploads" / "kb"
_FULL_TEXTS_PATH = Path(__file__).resolve().parent.parent / "agents" / "kb_full_texts.json"
ALLOWED_EXTS = {
    "pdf", "doc", "docx", "txt", "md", "csv", "xls", "xlsx", "ppt", "pptx",
    "jpg", "jpeg", "png", "webp", "gif", "bmp", "mp4", "mp3", "zip", "rar",
}
TEXT_EXTS = {"txt", "md", "csv"}
MAX_FILE_BYTES = 20 * 1024 * 1024  # 20MB
KB_CONTENT_MAX = 20000  # 整理稿正文上限（与 admin.py 同口径，超长要求拆分多篇）
DOC_TYPES = {"disease", "nutrition", "general"}
SPECIES_CODES = {"", "pig", "cattle", "sheep"}


def _load_full_texts() -> dict[str, str]:
    """完整版正文覆盖表（doc_name → content_md）：书架全文化数据源。"""
    try:
        return json.loads(_FULL_TEXTS_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 数据文件缺失/损坏时回落标准模板
        return {}


def _ensure_upload_dir() -> None:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


async def _index_single(doc_id: int) -> None:
    """知识回灌（2026-10-15 接通）：上传/启用后单文档增量进问诊检索池。

    优先向量库（embedding 仅本文档一条，费用可控）；Milvus 不可用时静默降级——
    检索层有关键词计分兜底（graph load_context_retrieve），文档已在 kb_document 表，
    下次 reindex_all / 启动建索引会补上。任何失败不拖垮上传主流程。
    """
    try:
        from app.agents import vector_store as vs

        async with SessionLocal() as db:
            d = await db.get(KbDocument, doc_id)
            if d is None or d.is_deleted or not d.is_enable:
                await vec_delete_doc(doc_id)
                return
            entry = doc_to_kb_entry(d)
        vecs = await embed_texts([vs._entry_text(entry)])
        await vec_upsert_doc(doc_id, entry, vecs[0])
        async with SessionLocal() as db:
            row = await db.get(KbDocument, doc_id)
            if row:
                row.indexed_at = datetime.now(timezone.utc)
                await db.commit()
    except Exception:  # noqa: BLE001 — 回灌失败不拖垮上传（检索层有降级）
        import logging

        logging.getLogger("sf.kb").warning("文档 %s 回灌索引失败（关键词路径兜底）", doc_id, exc_info=True)


def _to_item(d: KbDocument, *, with_content: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "doc_id": d.doc_id,
        "doc_name": d.doc_name,
        "doc_type": d.doc_type,
        "species_code": d.species_code or "",
        "retrieval_keywords": d.retrieval_keywords or [],
        "risk_level": d.risk_level,
        "med_names": d.med_names or [],
        "feed_advice": d.feed_advice or "",
        "source": d.source,
        "organizer": d.organizer,
        "chunk_count": d.chunk_count,
        "is_enable": d.is_enable,
        "file_name": d.file_name,
        "file_ext": d.file_ext,
        "file_size": d.file_size,
        "has_file": bool(d.storage_path),
        "content_md": d.content_md or "" if with_content else (d.content_md or "")[:120],
        "create_time": d.create_time.isoformat() if d.create_time else "",
    }
    return out


def _chunk_count(text: str) -> int:
    return max(1, (len(text) + 299) // 300)


@router.post("/kb/upload")
async def upload_doc(
    file: UploadFile | None = File(default=None),
    doc_name: str = Form(default=""),
    doc_type: str = Form(default="general"),
    species_code: str = Form(default=""),
    retrieval_keywords: str = Form(default=""),
    risk_level: str = Form(default="low"),
    feed_advice: str = Form(default=""),
    source: str = Form(default=""),
    organizer: str = Form(default=""),
    content_md: str = Form(default=""),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    # 知识库是全局检索池（kb_document 无租户列），写入权必须收敛到 admin：
    # 否则任一农户上传的"疾病文档"会进入他人问诊的候选集，连带影响风险分级与用药建议。
    # 与 /admin/kb 同一纪律，只是入口不同（这里是 multipart 上传原始文件）。
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可上传知识库文档")

    name = doc_name.strip() or (file.filename if file else "") or ""
    name = name.strip()
    if not name:
        raise ApiError(E_VALIDATION, "请填写文档名称", {"field": "doc_name"})
    if doc_type not in DOC_TYPES:
        raise ApiError(E_VALIDATION, "无效的文档类型", {"field": "doc_type"})
    if species_code not in SPECIES_CODES:
        raise ApiError(E_VALIDATION, "无效的物种归属", {"field": "species_code"})
    if not source.strip():
        raise ApiError(E_VALIDATION, "来源必填（出处留痕，附录 E SOP）", {"field": "source"})
    if not organizer.strip():
        raise ApiError(E_VALIDATION, "整理人必填（留痕，附录 E SOP）", {"field": "organizer"})
    keywords = [k.strip() for k in re.split(r"[,，;；\s]+", retrieval_keywords.strip()) if k.strip()]
    if keywords and len(keywords) < 2:
        raise ApiError(E_VALIDATION, "检索关键词至少 2 个（问诊命中依据）", {"field": "retrieval_keywords"})
    if doc_type == "disease" and risk_level not in ("low", "mid", "high"):
        raise ApiError(E_VALIDATION, "无效的风险等级", {"field": "risk_level"})

    file_name = file_ext = None
    file_size: int | None = None
    storage_path: str | None = None
    text_content = content_md.strip()

    if file is not None and file.filename:
        # 边读边累加：此前是先 await file.read() 全量入内存再判 20MB，
        # 一个 2GB 的上传会先把进程内存打满再被拒。
        raw = bytearray()
        while chunk := await file.read(1024 * 1024):
            raw.extend(chunk)
            if len(raw) > MAX_FILE_BYTES:
                raise ApiError(E_VALIDATION, "文件大小不能超过 20MB", {"field": "file"})
        raw = bytes(raw)
        original = Path(file.filename)
        ext = original.suffix.lstrip(".").lower()
        if ext not in ALLOWED_EXTS:
            raise ApiError(E_VALIDATION, f"暂不支持 .{ext} 类型文件", {"field": "file"})
        _ensure_upload_dir()
        stored_name = f"{uuid.uuid4().hex[:12]}_{original.name}"
        dest = UPLOAD_DIR / stored_name
        dest.write_bytes(raw)
        file_name = original.name
        file_ext = ext
        file_size = len(raw)
        storage_path = f"uploads/kb/{stored_name}"
        if not text_content and ext in TEXT_EXTS:
            try:
                text_content = raw.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    text_content = raw.decode("gbk")
                except UnicodeDecodeError:
                    text_content = ""  # 编码不可识别 → 只存档不进检索

    if not text_content and file is None:
        raise ApiError(E_VALIDATION, "请上传文件或粘贴整理稿正文", {"field": "content_md"})
    if len(text_content) > KB_CONTENT_MAX:
        # 与 /admin/kb 同口径：超长拒收而非静默截断（截断后检索命中的是残缺正文，比报错更坏）
        raise ApiError(E_VALIDATION, f"正文不超过 {KB_CONTENT_MAX} 字（超长请拆分多篇上传）", {"field": "content_md"})

    row = KbDocument(
        doc_name=name[:128],
        doc_type=doc_type,
        species_code=species_code,
        retrieval_keywords=keywords,
        risk_level=risk_level if doc_type == "disease" else "mid",
        med_names=[],
        feed_advice=feed_advice.strip(),
        content_md=text_content,
        source=source.strip()[:256],
        organizer=organizer.strip()[:64],
        chunk_count=_chunk_count(text_content),
        is_enable=True,
        file_name=file_name,
        file_ext=file_ext,
        file_size=file_size,
        storage_path=storage_path,
        uploader_id=user.user_id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    asyncio.create_task(_index_single(row.doc_id))  # 知识回灌：后台增量进检索池
    return ok(_to_item(row, with_content=True))


async def _get_visible_doc(db: AsyncSession, doc_id: int, user: User) -> KbDocument:
    d = await db.get(KbDocument, doc_id)
    if d is None or d.is_deleted:
        raise ApiError(E_NOT_FOUND, "该文档不存在或已删除")
    if user.role == "farmer" and (d.species_code or "") not in ("", user.main_species_code or ""):
        raise ApiError(E_FORBIDDEN, "无权限访问该文档")
    return d


@router.get("/kb/doc/{doc_id}")
async def get_doc(
    doc_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    d = await _get_visible_doc(db, doc_id, user)
    return ok(_to_item(d, with_content=True))


@router.get("/kb/doc/{doc_id}/file")
async def download_doc_file(
    doc_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> FileResponse:
    d = await _get_visible_doc(db, doc_id, user)
    if not d.storage_path:
        raise ApiError(E_NOT_FOUND, "该文档没有附件")
    path = Path(__file__).resolve().parent.parent / d.storage_path
    if not path.exists():
        raise ApiError(E_NOT_FOUND, "附件文件丢失，请联系管理员")
    return FileResponse(path, filename=d.file_name or path.name)


@router.put("/kb/doc/{doc_id}/enable")
async def toggle_doc(
    doc_id: int,
    body: dict[str, Any],
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    if user.role != "admin":
        raise ApiError(E_FORBIDDEN, "仅管理员可停用/启用文档")
    d = await db.get(KbDocument, doc_id)
    if d is None or d.is_deleted:
        raise ApiError(E_NOT_FOUND, "该文档不存在或已删除")
    d.is_enable = bool(body.get("is_enable", True))
    d.indexed_at = datetime.now(timezone.utc)
    await db.commit()
    asyncio.create_task(_index_single(d.doc_id))  # 启用→增量回灌；停用→从检索池摘除
    return ok({"doc_id": d.doc_id, "is_enable": d.is_enable})


@router.delete("/kb/doc/{doc_id}")
async def delete_doc(
    doc_id: int, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    if user.role != "admin":
        raise ApiError(E_FORBIDDEN, "仅管理员可删除文档")
    d = await db.get(KbDocument, doc_id)
    if d is None or d.is_deleted:
        raise ApiError(E_NOT_FOUND, "该文档不存在或已删除")
    d.is_deleted = True  # 软删（纪律：不物理删除，停用/删除均可追溯）
    await db.commit()
    asyncio.create_task(_index_single(d.doc_id))  # 已删/停用文档从检索池摘除
    return ok({"doc_id": d.doc_id, "is_deleted": True})


# ===== 示范文档种子（表空时灌入，与主物种无关的公共/羊文档，自写要点整理）=====

_SAMPLE_DOCS: list[dict[str, Any]] = [
    {
        "doc_name": "母羊妊娠期饲养管理要点",
        "doc_type": "nutrition",
        "species_code": "sheep",
        "retrieval_keywords": ["怀孕母羊", "妊娠", "补饲", "保胎"],
        "feed_advice": "妊娠后期（产前 2 个月）精料补饲提至每日 0.5~0.7kg/d，注意钙磷平衡与维生素 E/硒补充。",
        "source": "云上牧场知识库示范稿（舍饲肉羊技术规程要点整理）",
        "content_md": (
            "【母羊妊娠期饲养管理要点（示范文档）】\n\n"
            "一、妊娠前期（配种~90 天）：胎儿发育慢，按空怀水平略增 5~10%，"
            "优质粗料为主，精料 0.3~0.4kg/d。\n\n"
            "二、妊娠后期（91~150 天）：胎儿 70~80% 增重在此时完成——\n"
            "1. 精料补饲 0.5~0.7kg/d，分 2 次投喂；\n"
            "2. 钙磷比 1.5~2:1，注意维生素 E 与硒（防羔羊白肌病）；\n"
            "3. 避免惊赶、滑跌，出产前 7~10 天转入产房。\n\n"
            "三、管理红线：不喂霉变饲料与冰冻饮水；疫苗（如羔羊痢疾苗）产前 20~30 天免疫。"
        ),
    },
    {
        "doc_name": "羔羊断奶与保育要点",
        "doc_type": "nutrition",
        "species_code": "sheep",
        "retrieval_keywords": ["羔羊", "断奶", "保育", "开食料"],
        "feed_advice": "7~10 日龄开始训练采食开食料；60 日龄断奶，断奶后减少应激、饲料过渡 5~7 天。",
        "source": "云上牧场知识库示范稿（羔羊培育技术资料要点整理）",
        "content_md": (
            "【羔羊断奶与保育要点（示范文档）】\n\n"
            "一、初乳：出生 1 小时内吃足初乳，弱羔人工辅助哺乳。\n\n"
            "二、补饲：7~10 日龄设补饲栏训练采食开食料（CP 16~18%），"
            "自由采食优质干草，30 日龄起补饲量逐步增加。\n\n"
            "三、断奶：60 日龄左右一次断奶，断奶前后 5~7 天饲料过渡；"
            "断奶后按体况分群，日粮 CP 14~16%，保证清洁温水。"
        ),
    },
    {
        "doc_name": "羊饲养营养标准要点（NY/T 816 整理）",
        "doc_type": "nutrition",
        "species_code": "sheep",
        "retrieval_keywords": ["饲养标准", "粗蛋白", "补饲", "育肥羊", "育成羊"],
        "feed_advice": "各阶段营养见正文；精料比例不超过日粮 50%，防瘤胃酸中毒；换料过渡 5~7 天。",
        "source": "NY/T 816-2021《肉羊饲养标准》及舍饲肉羊技术规程（要点整理）",
        "content_md": (
            "【羊饲养营养标准要点（示范文档）】\n\n"
            "一、刚断奶羔羊（保育期）：CP 16~18%，消化能 ≥10.0 MJ/kg，日采食约 0.9kg/头，每日 3 顿。\n"
            "二、成长期羊只（育成期）：CP 14~16%，消化能 ≥9.5 MJ/kg，日采食约 1.4kg/头。\n"
            "三、待出栏育肥羊：CP 12~14%，消化能 ≥10.0 MJ/kg，日采食约 1.6kg/头，精粗比逐步提至 6:4~7:3。\n"
            "四、怀孕母羊：CP 13~15%，钙 0.7~1.0%（妊娠后期显著上调），日采食约 1.8kg/头。\n"
            "五、哺乳母羊：CP 14~16%，消化能 ≥10.5 MJ/kg，日采食约 2.4kg/头（双羔再增 20~30%）。\n\n"
            "以上为风干料口径的整理估算值，实际以饲养实验与兽医/营养师方案为准。"
        ),
    },
    {
        "doc_name": "畜禽养殖档案管理办法（公共法规摘录）",
        "doc_type": "general",
        "species_code": "",
        "retrieval_keywords": ["养殖档案", "留痕", "免疫", "检疫"],
        "feed_advice": "",
        "source": "农业农村部《畜禽标识和养殖档案管理办法》（公开法规摘录整理）",
        "content_md": (
            "【养殖档案管理要点（示范文档）】\n\n"
            "一、养殖档案应当记载：畜禽品种、数量、繁殖记录、标识情况、"
            "来源和进出栏日期；饲料、饲料添加剂、兽药等投入品的名称、"
            "使用对象、时间和用量；检疫、免疫、消毒情况；发病、诊疗、死亡和无害化处理情况。\n\n"
            "二、档案保存期限：商品畜禽 2 年以上，种畜禽长期保存。\n\n"
            "三、兽药使用记录：须留存药品名称、批号、用药对象与日龄、剂量、"
            "疗程及休药期执行情况，休药期未满的动物不得屠宰上市。"
        ),
    },
    # —— 养殖全流程专题册 ——
    {
        "doc_name": "羊免疫程序与接种要点",
        "doc_type": "disease",
        "species_code": "sheep",
        "retrieval_keywords": ["免疫", "疫苗", "接种", "羔羊痢疾苗", "羊痘苗"],
        "risk_level": "mid",
        "feed_advice": "免疫前后 3 天减少应激、避免驱虫与转群同期进行；接种后观察 30 分钟再离场。",
        "source": "云上牧场知识库专题册（公开免疫技术资料要点整理）",
        "content_md": (
            "【羊免疫程序与接种要点（示范文档）】\n\n"
            "一、繁殖母羊：\n"
            "1. 羊痘弱毒苗：每年春季全群接种一次；\n"
            "2. 羔羊痢疾苗（三联四防）：妊娠母羊产前 20~30 天免疫，母源抗体保护羔羊；\n"
            "3. 口蹄疫灭活苗：按当地免疫计划春秋两季各一次。\n\n"
            "二、羔羊：\n"
            "1. 出生后随母获得母源抗体；\n"
            "2. 断奶前后（约 60 日龄）按当地计划补免口蹄疫与羊痘。\n\n"
            "三、接种操作要点：\n"
            "1. 疫苗现配现用、冷藏运输，避免阳光直射；\n"
            "2. 剂量按标签，一羊一针头；\n"
            "3. 接种后个别羊可能出现短暂减食或低烧，属正常反应；\n"
            "4. 病弱羊、妊娠后期母羊缓种并登记补免日期。\n\n"
            "免疫记录须留痕（疫苗名称/批号/日期/剂量），未在免疫期的羊按本系统告警提醒补免。"
        ),
    },
    {
        "doc_name": "羊寄生虫防控程序",
        "doc_type": "disease",
        "species_code": "sheep",
        "retrieval_keywords": ["驱虫", "寄生虫", "痒螨", "线虫", "药浴"],
        "risk_level": "mid",
        "feed_advice": "驱虫后粪便集中堆肥发酵，杀灭虫卵；放牧草场轮牧减少感染源。",
        "source": "云上牧场知识库专题册（公开寄生虫防控技术资料要点整理）",
        "content_md": (
            "【羊寄生虫防控程序（示范文档）】\n\n"
            "一、防控节奏：\n"
            "1. 春秋两季各全群驱虫一次（阿苯达唑/伊维菌素类，轮换用药防耐药）；\n"
            "2. 外寄生虫（螨虫、虱）注射伊维菌素后，间隔 7~10 天重复一次；\n"
            "3. 新购羊进场驱虫并隔离观察后再混群。\n\n"
            "二、环境同步：\n"
            "1. 圈舍喷淋杀虫与粪污堆积发酵同步进行；\n"
            "2. 潮湿草场排水改良，避免在低洼湿地放牧（肝片吸虫中间宿主）。"
            "\n\n"
            "三、效果判定：驱虫 2~3 周观察被毛光泽度、体况恢复；有条件做粪便虫卵计数对比。\n\n"
            "四、用药红线：孕畜选用孕畜可用剂量（阿苯达唑孕畜禁用，改用伊维菌素类并遵兽医方案）；"
            "严格休药期，未满休药期不得出栏屠宰。"
        ),
    },
    {
        "doc_name": "羊繁殖管理要点（两年三胎）",
        "doc_type": "nutrition",
        "species_code": "sheep",
        "retrieval_keywords": ["繁殖", "配种", "两年三胎", "发情", "产羔"],
        "feed_advice": "配种前 15~20 天对母羊短期优饲（营养上调 30%），可显著提高双羔率。",
        "source": "云上牧场知识库专题册（肉羊高频繁殖技术资料要点整理）",
        "content_md": (
            "【羊繁殖管理要点（示范文档）】\n\n"
            "一、繁殖节律：母羊妊娠约 150 天、哺乳约 60 天，通过羔羊早断奶（60 日龄）"
            "与配种衔接可实现两年三胎。\n\n"
            "二、配种管理：\n"
            "1. 发情周期约 17 天，发情持续 24~36 小时，采用人工授精或本交均需记录配种日期；\n"
            "2. 配种前 15~20 天短期优饲（营养上调 30%）；\n"
            "3. 预产期 = 配种日 + 150 天，本系统繁殖管理页自动计算并生成提醒。\n\n"
            "三、产羔管理：\n"
            "1. 产前 7~10 天转入产房，一栏一羊；\n"
            "2. 产后 1 小时内吃足初乳；\n"
            "3. 产后 20~30 天内完成羔羊痢疾苗免疫（经母源抗体保护羔羊）。\n\n"
            "四、空怀管理：断奶后观察发情，两个情期未返情即确认妊娠；连续空怀的母羊查明原因后决定去留。"
        ),
    },
    {
        "doc_name": "羊育肥与出栏管理要点",
        "doc_type": "nutrition",
        "species_code": "sheep",
        "retrieval_keywords": ["育肥", "出栏", "日增重", "称重", "料肉比"],
        "feed_advice": "育肥期按体重分群、日增重月度盘点；出栏前核对休药期，未满不得屠宰。",
        "source": "云上牧场知识库专题册（肉羊育肥技术资料要点整理）",
        "content_md": (
            "【羊育肥与出栏管理要点（示范文档）】\n\n"
            "一、育肥节奏：\n"
            "1. 育成期（4~6 月龄）：骨架生长为主，CP 14~16%，日增重目标 250~300g；\n"
            "2. 育肥期（7~10 月龄）：精粗比前粗后精（5:5 → 6:4~7:3），日增重目标 300g+；\n"
            "3. 出栏体重参考 45~55kg（按品种与市场行情）。\n\n"
            "二、过程管理：\n"
            "1. 每月随机抽 10% 称重，记录日增重——本系统「采食与增重」页自动对比品种标准线；\n"
            "2. 日增重连续两次低于标准 80% 时排查饲料、驱虫与疫病三方面；\n"
            "3. 换料过渡 5~7 天，防止应激性腹泻。\n\n"
            "三、出栏检查：\n"
            "1. 休药期核对（用药记录在台账可查，未满期系统拦截出栏）；\n"
            "2. 检疫合格证明齐备；\n"
            "3. 出栏前 2~3 小时停喂但保证饮水，减少运输应激。"
        ),
    },
    {
        "doc_name": "圈舍生物安全与消毒制度",
        "doc_type": "disease",
        "species_code": "sheep",
        "retrieval_keywords": ["消毒", "生物安全", "隔离", "无害化", "防疫"],
        "risk_level": "mid",
        "feed_advice": "消毒剂轮换使用（碱性/酸性/氧化型），避免病原产生耐受。",
        "source": "云上牧场知识库专题册（GB/T 39915 及公开防疫技术资料要点整理）",
        "content_md": (
            "【圈舍生物安全与消毒制度（示范文档）】\n\n"
            "一、日常消毒：\n"
            "1. 圈舍通道每周消毒 1~2 次；产房、保育圈每次转群后彻底消毒；\n"
            "2. 常用消毒剂：生石灰（栏面）、氢氧化钠 2%（通道）、过硫酸氢钾（带畜消毒），轮换使用。\n\n"
            "二、隔离制度：\n"
            "1. 新购羊隔离观察 2~3 周，驱虫免疫后再混群；\n"
            "2. 病畜立即转入隔离栏，专人专具饲养；\n"
            "3. 外来人员车辆入场消毒，饲具不串圈使用。\n\n"
            "三、无害化处理：病死畜按当地规定深埋或交无害化处理收集点，严禁出售、食用、丢弃；"
            "处理场地消毒。\n\n"
            "四、疫病报告：疑似重大动物疫病（口蹄疫、小反刍兽疫等）立即报告当地动物疫病预防控制机构。"
        ),
    },
]

CATEGORY_LABEL = {
    "respiratory": "呼吸系统疾病",
    "digestive": "消化系统疾病",
    "reproductive": "繁殖障碍与产科疾病",
    "neurological": "神经系统疾病",
    "skin": "皮肤与寄生虫疾病",
    "general": "综合防治",
}

CATEGORY_DETAIL = {
    "respiratory": {
        "overview": "多与圈舍通风不良、氨气浓度高、饲养密度大及气温骤变等应激因素相关，冬春季节高发。",
        "symptoms": "辨认要点：观察呼吸频率与方式（是否腹式呼吸、犬坐）、有无咳嗽流涕、体温变化及群体发病率；"
                    "听诊可请兽医辅助。出现高热不退、皮肤发绀、呼吸困难加重时按重症处理。",
        "spread": "呼吸道病原多经飞沫、粉尘传播，运输、混群、气温骤变等应激是常见诱因；"
                  "圈舍潮湿闷热、氨气超标会显著加重病情。",
        "prevent": "通风与保温兼顾，控制饲养密度；按免疫程序接种相关疫苗（如猪瘟、羊痘、巴氏杆菌苗等）；"
                   "减少运输与混群应激，新购畜禽隔离观察 2~3 周。",
    },
    "digestive": {
        "overview": "与饲料品质、饲料突变、饮水卫生及环境温度密切相关，幼龄畜禽发病率高于成年畜禽。",
        "symptoms": "辨认要点：观察粪便形态（稀粪/血痢/水样）、脱水程度（眼窝凹陷、皮肤回弹慢）、"
                    "食欲与反刍情况；腹泻呈群体暴发时优先怀疑传染性病因或饲料问题。",
        "spread": "经粪-口途径传播：污染的饲料、饮水、栏面及母畜乳头是主要媒介；"
                  "霉变饲料与卫生不良是两大主因。",
        "prevent": "饲料过渡 5~7 天防突变；料槽水槽定期清洁；防霉变、防冻防冰饮；"
                   "母畜围产期加强饲养以提升幼畜初乳质量；腹泻期补液防脱水优先于止泻。",
    },
    "reproductive": {
        "overview": "多发于围产期与配种繁殖期，营养失衡（钙磷、维生素 E/硒）、产道损伤与传染性病原是三大主因。",
        "symptoms": "辨认要点：产后瘫痪多见站不起、后肢无力；乳房炎见乳房红肿热痛、乳汁异常；"
                    "流产/死胎需记录胎次日龄并保留样本供兽医排查（蓝耳、伪狂犬、布病等）。",
        "spread": "传染性繁殖障碍病原可经配种、胎盘垂直传播；非传染性因素多为营养与管理失误。",
        "prevent": "妊娠后期精准补饲（钙磷平衡、维生素 E/硒）；围产期环境干燥清洁；"
                   "配种前完成布病等检疫；流产后对污染环境彻底消毒并对流产物无害化处理。",
    },
    "neurological": {
        "overview": "神经症状多起病急、进展快，常见于幼龄畜禽（如仔猪水肿病、脑膜炎型链球菌）与中毒 cases，属高风险表现。",
        "symptoms": "辨认要点：转圈、抽搐、倒地划水、眼球震颤、共济失调、眼睑/面部水肿；"
                    "与中暑、中毒（饲料霉变、药物过量）鉴别——同栏多发疑中毒，散发疑感染。",
        "spread": "经外伤感染（剪牙断尾、脐带消毒不严）或继发于其他系统感染；中毒型无传染性。",
        "prevent": "规范产房操作（剪牙、断尾、脐带碘酒消毒）；地面防滑防尖锐物；"
                   "断奶饲料过渡防水肿病；发现神经症状立即单独护理并请兽医处置。",
    },
    "skin": {
        "overview": "皮肤与寄生虫性疾病多为慢性经过，虽死亡率低但显著影响生长速度与皮毛品质，且具接触传染性。",
        "symptoms": "辨认要点：蹭痒蹭墙、脱毛斑秃、皮肤结痂增厚、疹块形态（菱形疹块提示丹毒）；"
                    "注意与湿疹（无热、痒感轻）鉴别。",
        "spread": "螨虫等外寄生虫经直接接触与共用栏面传播；湿度大、卫生差的圈舍高发。",
        "prevent": "全群同步驱虫（间隔 7~10 天重复一次）；药浴/喷淋结合环境杀虫；"
                   "保持圈舍干燥；新购畜禽驱虫后再混群。",
    },
}


def _disease_doc_from_entry(e: dict[str, Any]) -> dict[str, Any] | None:
    """内置问诊知识条目（kb_seed.json，公开教材合规整理稿）→ 知识库文档（完整版正文）。"""
    if not e.get("disease_name"):
        return None
    species = e.get("species_code") or ""
    species_label = {"pig": "猪", "cattle": "牛", "sheep": "羊", "": "畜禽"}.get(species, "畜禽")
    cat = CATEGORY_LABEL.get(e.get("category", ""), "综合防治")
    cat_detail = CATEGORY_DETAIL.get(e.get("category", ""), {})
    meds = e.get("med_names") or []
    med_lines = "\n".join(f"- {m}" for m in meds) if meds else "- 无特效治疗药物，重点在预防与上报（详见正文）"
    risk = e.get("risk_level", "mid")
    risk_label = {"low": "低", "mid": "中", "high": "高"}.get(risk, "中")
    kw_text = "、".join(e.get("symptom_keywords") or [])
    content = (
        f"【{e['disease_name']}——{species_label}常见{cat}知识（内置整理稿）】\n\n"
        f"一、疾病概述\n"
        f"{e['disease_name']}是{species_label}的常见{cat}，{cat_detail.get('overview', '在规模化养殖条件下多有发生。')}"
        f"本条目风险等级为{risk_label}（{'发现后请立即转交执业兽医处理' if risk == 'high' else '请结合实际症状请兽医确诊'}），"
        f"{'属母畜专属疾病，仅发生于母畜。' if e.get('female_only') else '公母畜禽均可发病。'}\n\n"
        f"二、典型症状与临床辨认\n"
        f"问诊检索关键词：{kw_text}。\n"
        f"{cat_detail.get('symptoms', '注意观察采食、精神、体温与排泄物变化，发现异常及时隔离观察。')}\n\n"
        f"三、流行与传播特点\n{cat_detail.get('spread', '病原多经病畜排泄物、分泌物污染环境传播，饲养密度高、卫生条件差时传播加快。')}\n\n"
        f"四、处置原则\n"
        f"1. 立即将病畜（群）隔离，避免与健康畜交叉；\n"
        f"2. 记录发病数量、日龄、症状发展与体温变化，供兽医诊断参考；\n"
        f"3. 疑似重大动物疫病（如猪瘟、小反刍兽疫等）须立即报告当地动物疫病预防控制机构，不得自行用药掩盖症状；\n"
        f"4. 病死畜无害化处理，严禁出售、食用或随意丢弃。\n\n"
        f"五、推荐用药（须过兽药库校验与兽医处方）\n{med_lines}\n\n"
        f"六、饲养/饲喂管理建议\n{e.get('feed_advice') or '保持圈舍清洁干燥、通风良好；保证饲料新鲜与饮水清洁；减少各类应激。'}\n\n"
        f"七、预防措施\n{cat_detail.get('prevent', '按免疫程序接种相关疫苗；坚持全进全出与定期消毒；新购畜禽隔离观察 2~3 周后再混群。')}\n\n"
        f"八、出处\n{e.get('source', '')}\n\n"
        f"——本条目为公开兽医学教材/官方技术规范的一般性知识整理（原创改写），"
        f"仅作辅助参考，不能替代执业兽医现场诊断与处方。"
    )
    return {
        "doc_name": e["disease_name"],
        "doc_type": "disease",
        "species_code": species,
        "retrieval_keywords": list(e.get("symptom_keywords") or []),
        "risk_level": risk,
        "med_names": list(meds),
        "feed_advice": e.get("feed_advice") or "",
        "source": e.get("source", "") or "公开兽医学教材合规整理",
        "content_md": content,
    }


async def seed_kb_documents(db: AsyncSession) -> None:
    """知识库回灌（幂等，按 doc_name 判重，支持增量）：
    ① 内置问诊知识条目（kb_seed.json 32 条，公开教材合规整理稿）逐条成册；
    ② 营养/法规示范文档；③ 附件 txt 落盘。已存在的书名跳过，用户上传不受影响。"""
    _ensure_upload_dir()
    existing = set(
        (await db.execute(select(KbDocument.doc_name).where(KbDocument.is_deleted.is_(False)))).scalars()
    )

    def add_doc(spec: dict[str, Any], organizer: str) -> None:
        if spec["doc_name"] in existing:
            return
        # 2026-10-15 全文化：kb_full_texts.json 中有同名完整版正文时优先使用
        spec = dict(spec)
        spec["content_md"] = _load_full_texts().get(spec["doc_name"], spec["content_md"])
        raw = spec["content_md"].encode("utf-8")
        safe_name = re.sub(r'[\\/:*?"<>|]', '_', spec["doc_name"])  # 病名可能含 / 等路径非法字符
        stored_name = f"seed_{uuid.uuid4().hex[:10]}_{safe_name}.txt"
        (UPLOAD_DIR / stored_name).write_bytes(raw)
        db.add(
            KbDocument(
                doc_name=spec["doc_name"][:128],
                doc_type=spec["doc_type"],
                species_code=spec.get("species_code", ""),
                retrieval_keywords=spec.get("retrieval_keywords", []),
                risk_level=spec.get("risk_level", "mid"),
                med_names=spec.get("med_names", []),
                feed_advice=spec.get("feed_advice", ""),
                content_md=spec["content_md"],
                source=spec.get("source", ""),
                organizer=organizer,
                chunk_count=max(1, (len(spec["content_md"]) + 299) // 300),
                is_enable=True,
                file_name=f"{spec['doc_name']}.txt",
                file_ext="txt",
                file_size=len(raw),
                storage_path=f"uploads/kb/{stored_name}",
                uploader_id=None,
            )
        )
        existing.add(spec["doc_name"])

    # ① 内置问诊知识条目 → 病知识册（32 条：猪 18 / 羊 8 / 牛 6）
    from app.agents.kb import load_entries

    for e in load_entries():
        spec = _disease_doc_from_entry(e)
        if spec:
            add_doc(spec, "云上牧场内置")

    # ② 营养/法规示范文档
    for s in _SAMPLE_DOCS:
        add_doc(s, "云上牧场内置")

    await db.commit()
