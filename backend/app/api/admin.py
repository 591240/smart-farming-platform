"""后台账号管理 — F10 FR-05（03-API 十二节）+ F16 知识库文档管理。

主 PRD 1.5：兽医由管理员在后台创建、不开放注册；注册链路恒为 farmer（见 auth.py）。
本模块是"能建出兽医账号"的唯一入口，也是 HITL 复核链路可测的前提。

F16 追加：知识库文档 CRUD（admin 专属上传，上传即进问诊检索池——
load_context_retrieve 拉启用文档与内置种子同池计分）。纪律：不物理删除
（停用退出检索池可恢复）；上传必填来源 + 整理人（附录 E 留痕）；推荐药
仅 disease 文档可挂且必须在本物种兽药库内非违禁（TC-G07 底线在上传口前置）。

纪律：手机号脱敏返回；不提供删除（禁用不删档，复核溯源审计要求）；
初始密码走 bcrypt，响应与日志均不回传明文。
"""

from __future__ import annotations

import logging
import math
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.kb import doc_to_kb_entry
from app.api.auth import is_valid_phone, mask_phone, validate_password
from app.core.db import get_db
from app.core.response import (
    E_NOT_FOUND,
    E_PHONE_REGISTERED,
    E_ROLE_FORBIDDEN,
    E_VALIDATION,
    ApiError,
    ok,
)
from app.core.security import get_current_user, hash_password_async
from app.models import KbDocument, MedicineLib, User
from app.services.embedding import embed_texts

logger = logging.getLogger("app")

router = APIRouter(prefix="/api", dependencies=[Depends(get_current_user)])

ASSIGNABLE_ROLES = ("vet", "admin")
KB_DOC_TYPES = ("disease", "nutrition")
KB_SPECIES = ("pig", "cattle", "sheep", "")  # 空=公共法规文档（全物种适用）
KB_RISK_LEVELS = ("low", "mid", "high")
KB_CHUNK_CHARS = 300  # mock 切片口径：正文每 300 字一片
KB_CONTENT_MAX = 20000  # 正文上限（与 mock 同口径，超长要求拆分多篇）


def _to_item(u: User) -> dict[str, Any]:
    return {
        "user_id": u.user_id,
        "phone": mask_phone(u.phone),
        "nickname": u.nickname,
        "role": u.role,
        "is_enable": u.is_enable is not False,
        "create_time": u.create_time.isoformat() if u.create_time else "",
    }


@router.get("/admin/user")
async def list_users(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)) -> dict:
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可访问后台管理")
    rows = (await db.execute(select(User).order_by(User.user_id))).scalars().all()
    return ok({"total": len(rows), "items": [_to_item(u) for u in rows]})


class CreateUserBody(BaseModel):
    phone: str = Field(default="")
    nickname: str = Field(default="", max_length=64)
    role: str = Field(default="vet")
    initial_password: str = Field(default="")


@router.post("/admin/user")
async def create_user(
    body: CreateUserBody, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可创建账号")
    if body.role not in ASSIGNABLE_ROLES:
        raise ApiError(E_VALIDATION, "仅可创建兽医或管理员账号", {"field": "role"})
    if not is_valid_phone(body.phone):
        raise ApiError(E_VALIDATION, "手机号格式不正确", {"field": "phone"})
    if err := validate_password(body.initial_password):
        raise ApiError(E_VALIDATION, err, {"field": "initial_password"})
    exists = (await db.execute(select(User).where(User.phone == body.phone))).scalar_one_or_none()
    if exists:
        raise ApiError(E_PHONE_REGISTERED, "该手机号已注册", {"field": "phone"})

    created = User(
        phone=body.phone,
        password_hash=await hash_password_async(body.initial_password),
        nickname=body.nickname.strip() or mask_phone(body.phone),
        role=body.role,
        main_species_code=None,
        is_enable=True,
    )
    db.add(created)
    await db.commit()
    return ok(_to_item(created))


class SetEnableBody(BaseModel):
    is_enable: bool


@router.put("/admin/user/{user_id}")
async def set_user_enable(
    user_id: int,
    body: SetEnableBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可管理账号")
    target = await db.get(User, user_id)
    if target is None:
        raise ApiError(E_VALIDATION, "账号不存在", {"field": "user_id"})
    if target.user_id == user.user_id and body.is_enable is False:
        raise ApiError(E_VALIDATION, "不能禁用当前登录的管理员账号", {"field": "is_enable"})
    target.is_enable = body.is_enable
    await db.commit()
    return ok({"user_id": target.user_id, "is_enable": target.is_enable})


# =====================================================================
# F16 知识库文档管理（admin 专属上传；/kb/list 全员可读）
# =====================================================================

def _kb_to_item(d: KbDocument, with_content: bool = False) -> dict[str, Any]:
    item: dict[str, Any] = {
        "doc_id": d.doc_id,
        "doc_name": d.doc_name,
        "doc_type": d.doc_type,
        "species_code": d.species_code,
        "retrieval_keywords": d.retrieval_keywords or [],
        "risk_level": d.risk_level,
        "med_names": d.med_names or [],
        "feed_advice": d.feed_advice,
        "source": d.source,
        "organizer": d.organizer,
        "chunk_count": d.chunk_count,
        "indexed_at": d.indexed_at.isoformat() if d.indexed_at else "",
        "is_enable": d.is_enable is not False,
        "create_time": d.create_time.isoformat() if d.create_time else "",
    }
    if with_content:
        item["content_md"] = d.content_md
    return item


def _kb_text_list(value: Any) -> list[str]:
    """关键词/推荐药入参归一：数组或逗号顿号分号分隔的字符串都接受（mock 同款宽容）。

    去空、去重，保持首次出现顺序。不再"静默截断到 10 个 / 6 个"——那会让用户
    以为存进去的和看到的不一样，长度合法性改由调用方显式校验并报错。
    """
    if isinstance(value, list):
        raw = [str(v) for v in value]
    else:
        raw = re.split(r"[,，、;；]", str(value or ""))
    out: list[str] = []
    for item in raw:
        text = item.strip()
        if text and text not in out:
            out.append(text)
    return out


async def _validate_kb_meds(db: AsyncSession, med_names: list[str], species_code: str) -> None:
    """推荐药上传口校验：必须在兽药库内且非违禁（TC-G07 前置到入口）。

    公共文档（species 为空）按 mock 口径放行"任一物种库内非违禁药"，
    否则同一份跨物种规范文档根本挂不上药。
    """
    if not med_names:
        return
    query = select(MedicineLib.med_name, MedicineLib.species_code).where(MedicineLib.is_forbidden.is_(False))
    if species_code:
        query = query.where(MedicineLib.species_code == species_code)
    rows = (await db.execute(query)).all()
    lib_names = {r[0] for r in rows}
    for med_name in med_names:
        if med_name not in lib_names:
            raise ApiError(
                E_VALIDATION,
                f"推荐用药「{med_name}」不在兽药库内或为违禁药（仅库内非违禁药可推荐）",
                {"field": "med_names"},
            )


class KbUploadBody(BaseModel):
    doc_name: str = Field(min_length=1, max_length=128)
    doc_type: str = Field(default="disease")
    species_code: str = Field(default="")  # 空=公共文档
    retrieval_keywords: Any = Field(default_factory=list)  # 数组或逗号/顿号/分号分隔串均可
    risk_level: str = Field(default="mid")
    med_names: Any = Field(default_factory=list)
    feed_advice: str = Field(default="")
    content_md: str = Field(default="")
    source: str = Field(min_length=1, max_length=256)
    organizer: str = Field(min_length=1, max_length=64)


@router.get("/admin/kb")
async def list_kb_docs(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可管理知识库")
    rows = (
        await db.execute(
            select(KbDocument).where(KbDocument.is_deleted.is_(False)).order_by(KbDocument.doc_id.desc())
        )
    ).scalars().all()
    return ok({"total": len(rows), "items": [_kb_to_item(d, with_content=True) for d in rows]})


@router.post("/admin/kb")
async def upload_kb_doc(
    body: KbUploadBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可上传知识库文档")
    if body.doc_type not in KB_DOC_TYPES:
        raise ApiError(E_VALIDATION, "文档类型仅支持 disease（疫病诊疗）/ nutrition（饲养营养）", {"field": "doc_type"})
    if body.species_code not in KB_SPECIES:
        raise ApiError(E_VALIDATION, "适用物种无效（可为空=公共法规文档）", {"field": "species_code"})
    keywords = _kb_text_list(body.retrieval_keywords)
    if len(keywords) < 2:
        raise ApiError(E_VALIDATION, "请至少填写 2 个症状/主题关键词（检索计分依据）", {"field": "retrieval_keywords"})
    if any(len(k) > 20 for k in keywords):
        raise ApiError(E_VALIDATION, "单个关键词不超过 20 字", {"field": "retrieval_keywords"})
    if body.risk_level not in KB_RISK_LEVELS:
        raise ApiError(E_VALIDATION, "风险等级仅支持 low/mid/high", {"field": "risk_level"})
    med_names = _kb_text_list(body.med_names)
    if body.doc_type == "nutrition" and med_names:
        raise ApiError(E_VALIDATION, "营养类文档不支持推荐用药", {"field": "med_names"})
    await _validate_kb_meds(db, med_names, body.species_code)
    if len(body.feed_advice.strip()) > 500:
        raise ApiError(E_VALIDATION, "饲养建议不超过 500 字", {"field": "feed_advice"})
    content_md = body.content_md.strip()
    if len(content_md) > KB_CONTENT_MAX:
        raise ApiError(E_VALIDATION, f"正文不超过 {KB_CONTENT_MAX} 字（超长请拆分多篇上传）", {"field": "content_md"})

    doc = KbDocument(
        doc_name=body.doc_name.strip(),
        doc_type=body.doc_type,
        species_code=body.species_code,
        retrieval_keywords=keywords,
        # 营养类不承载医疗风险：写入侧就归 low 并清空推荐药（mock 同款）。否则管理员列表
        # 回显一个"看起来会致病"的 high，而检索层实际根本不把它当风险来源
        risk_level="low" if body.doc_type == "nutrition" else body.risk_level,
        med_names=med_names if body.doc_type == "disease" else [],
        feed_advice=body.feed_advice.strip(),
        content_md=content_md,
        source=body.source.strip(),
        organizer=body.organizer.strip(),
        chunk_count=max(1, math.ceil(len(content_md) / KB_CHUNK_CHARS)),
        indexed_at=datetime.now(timezone.utc),
        is_enable=True,
    )
    db.add(doc)
    await db.commit()
    item = _kb_to_item(doc, with_content=True)
    item["vector_indexed"] = await _index_doc_safe(doc)  # B5：上传即灌 Milvus（失败不阻塞，可手动重建）
    return ok(item)


async def _index_doc_safe(doc: KbDocument) -> bool:
    """单文档灌 Milvus：向量库不可用时 False（检索层会回退关键词，文档已保存不丢）。"""
    try:
        from app.agents import vector_store

        entry = doc_to_kb_entry(doc)
        entry["entry_kind"] = "kb_doc"
        text = vector_store._entry_text(entry)
        vectors = await embed_texts([text])
        await vector_store.upsert_doc(doc.doc_id, entry, vectors[0])
        return True
    except Exception:  # noqa: BLE001 — 索引失败不影响文档保存与关键词检索兜底，但必须留痕
        # 此前这里是裸 `return False`：一次 NameError（doc_to_kb_entry 未导入）被吞掉，
        # 所有 admin 上传文档恒报"未进向量索引"，界面看起来只是"索引没成功"。
        logger.warning("知识库文档向量索引失败 doc_id=%s（检索回退关键词路径）", getattr(doc, "doc_id", None), exc_info=True)
        return False


@router.get("/admin/kb/{doc_id}")
async def get_kb_doc(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可管理知识库")
    doc = await db.get(KbDocument, doc_id)
    if doc is None or doc.is_deleted:
        raise ApiError(E_NOT_FOUND, "知识库文档不存在", {"field": "doc_id"})
    return ok(_kb_to_item(doc, with_content=True))


class KbEnableBody(BaseModel):
    is_enable: Any


@router.put("/admin/kb/{doc_id}")
async def set_kb_doc_enable(
    doc_id: int,
    body: KbEnableBody,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """启用/停用（mock 契约：路径就是 `PUT /admin/kb/{id}`，不带 /enable 后缀）。

    `is_enable` 手工校验而非交给 pydantic —— 框架级 422 返回的是 FastAPI 默认错误体，
    前端 `apiFetch` 拿不到 `{code,message}` 会当成网络异常。
    """
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可管理知识库")
    if not isinstance(body.is_enable, bool):
        raise ApiError(E_VALIDATION, "is_enable 必须为布尔值", {"field": "is_enable"})
    doc = await db.get(KbDocument, doc_id)
    if doc is None or doc.is_deleted:
        raise ApiError(E_NOT_FOUND, "知识库文档不存在", {"field": "doc_id"})
    doc.is_enable = body.is_enable
    if body.is_enable:
        doc.indexed_at = datetime.now(timezone.utc)  # 启用即重建索引
    await db.commit()
    result: dict[str, Any] = {"doc_id": doc.doc_id, "is_enable": doc.is_enable}
    # B5 联动：启用 → 灌 Milvus；停用 → 从向量库摘除（Milvus 无 is_enable 字段，
    # 停用必须即刻摘除，否则语义检索仍会召回已停用文档）
    try:
        from app.agents import vector_store

        if body.is_enable:
            result["vector_indexed"] = await _index_doc_safe(doc)
        else:
            await vector_store.delete_doc(doc.doc_id)
            result["vector_removed"] = True
    except Exception:  # noqa: BLE001 — 向量库不可用时启停仍生效（全量重建会对齐）
        pass
    return ok(result)


@router.delete("/admin/kb/{doc_id}")
async def delete_kb_doc(
    doc_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """软删档 + 立即摘出向量库（mock 契约：`{doc_id, is_deleted:true}`）。

    只置 is_deleted 不物理删行——台账与问诊消息里可能已引用该文档，删行会留下悬空出处。
    """
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可删除知识库文档")
    doc = await db.get(KbDocument, doc_id)
    if doc is None or doc.is_deleted:
        raise ApiError(E_NOT_FOUND, "知识库文档不存在", {"field": "doc_id"})
    doc.is_deleted = True
    doc.is_enable = False
    await db.commit()
    result: dict[str, Any] = {"doc_id": doc.doc_id, "is_deleted": True}
    try:
        from app.agents import vector_store

        await vector_store.delete_doc(doc.doc_id)
        result["vector_removed"] = True
    except Exception:  # noqa: BLE001 — 向量库不可用时删除仍生效（全量重建会摘除）
        pass
    return ok(result)


@router.post("/admin/kb/reindex")
async def reindex_kb(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """B5 全量重建向量索引：内置种子 + 启用文档（维度变更/手动修复用）。"""
    if user.role != "admin":
        raise ApiError(E_ROLE_FORBIDDEN, "仅管理员可重建知识库索引")
    from app.agents import vector_store

    count = await vector_store.reindex_all()
    return ok({"indexed": count})


@router.get("/kb/list")
async def list_kb_for_reading(
    user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
) -> dict:
    """全员阅读列表（F16：不用管理员也能用知识库）。

    farmer：主物种文档 + 公共文档；vet/admin：全量。仅返回启用中的文档。
    """
    query = select(KbDocument).where(KbDocument.is_enable.is_(True), KbDocument.is_deleted.is_(False))
    if user.role == "farmer":
        main = user.main_species_code or ""
        query = query.where((KbDocument.species_code == main) | (KbDocument.species_code == ""))
    rows = (await db.execute(query.order_by(KbDocument.doc_id.desc()))).scalars().all()
    return ok({"total": len(rows), "items": [_kb_to_item(d, with_content=True) for d in rows]})
