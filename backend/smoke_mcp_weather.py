"""气象 MCP 域冒烟 — server 工具清单 + 真实调用链（stdio 传输，无需端口）。

运行：cd backend && python smoke_mcp_weather.py
前置：pip install mcp（requirements.txt 已收录）。无 AMAP key 也能跑——
断言的是"结构化失败而非崩溃"，key 配置后的成功路径请单独人工验一次。
"""

from __future__ import annotations

import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: object = "") -> None:
    global PASS, FAIL
    tag = "PASS" if ok else "FAIL"
    if ok:
        PASS += 1
    else:
        FAIL += 1
    print(f"[{tag}] {name} {detail if not ok else ''}")


async def main() -> int:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_weather.server", "--stdio"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            check("工具清单含 get_weather_forecast", "get_weather_forecast" in names, names)
            check("工具清单含 assess_climate_risk", "assess_climate_risk" in names, names)

            r = await session.call_tool(
                "assess_climate_risk", {"adcode": "431300", "species_code": "sheep"}
            )
            data = json.loads(r.content[0].text)
            # 无 key → 结构化失败（ok=false）；有 key → ok=true 且 risks 为列表
            if data.get("ok"):
                check("assess_climate_risk 成功路径", isinstance(data.get("risks"), list), data)
            else:
                check(
                    "无 key 时返回结构化错误而非崩溃",
                    data.get("ok") is False and bool(data.get("error")),
                    data,
                )

            r2 = await session.call_tool("get_weather_forecast", {"adcode": "431300"})
            d2 = json.loads(r2.content[0].text)
            check(
                "get_weather_forecast 返回 ok 字段",
                isinstance(d2, dict) and "ok" in d2,
                d2 if not isinstance(d2, dict) else list(d2),
            )

            r3 = await session.call_tool(
                "assess_climate_risk", {"adcode": "431300", "species_code": "alien"}
            )
            d3 = json.loads(r3.content[0].text)
            check("未知物种拒绝且不编造", d3.get("ok") is False and "未配置" in str(d3.get("error", "")), d3)

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
