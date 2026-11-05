"""一次性诊断脚本：复现图片问诊的两条检索路径。用后即删。"""
import asyncio

from app.agents.kb import search_diseases, search_diseases_smart

Q = "从图片识别到以下可见征象：皮肤发红、局部脱毛、皮肤有红斑结痂、被毛粗乱。你看图片这是什么病"

m2 = search_diseases(Q, "sheep", True)
print("关键词路径返回类型:", type(m2), type(m2[0]) if m2 else "空")
print("关键词路径命中:", m2[:5] if m2 else [])


async def main() -> None:
    m1, na = await search_diseases_smart(Q, "sheep", True)
    print("语义路径命中:", [d if isinstance(d, str) else d.get("name") for d in m1][:5], "| 不适用数:", len(na))


asyncio.run(main())
