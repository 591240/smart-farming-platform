"""一次性诊断：取库里那轮带图消息，原样跑 describe_vision + 两条检索。用后即删。"""
import asyncio
import json

from app.core.config import settings
from app.agents import prompts
from app.agents.kb import search_diseases, search_diseases_smart


async def main() -> None:
    print("vision_model =", settings.llm_vision_model)

    # 1) 从库里取用户那轮的原图（message_id=7）
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    engine = create_async_engine(settings.database_url)
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text("SELECT meta->>'image' FROM agent_messages WHERE message_id = 7")
            )
        ).scalar()
    await engine.dispose()
    if not row:
        print("!! 库里没取到图")
        return
    print("图片 data URL 长度:", len(row))

    # 2) 原样跑视觉描述
    try:
        res = await prompts.describe_vision(image=row, species_code="sheep", message="这是咋回事啊")
        print("VLM observation:", res.get("observation"))
        print("VLM signs:", res.get("signs"))
    except Exception as e:  # noqa: BLE001
        print("VLM 调用异常:", type(e).__name__, str(e)[:300])
        return

    # 3) 用真实征象跑两条检索
    signs = res.get("signs") or []
    q = f"从图片识别到以下可见征象：{'、'.join(signs)}。这是咋回事啊"
    m_kw = search_diseases(q, "sheep", True)
    print("关键词路径:", [d.get("disease_name") for d in m_kw][:4])
    m_sem, _ = await search_diseases_smart(q, "sheep", True)
    print("语义路径:", [d.get("disease_name") or d.get("name") for d in m_sem][:4])


asyncio.run(main())
