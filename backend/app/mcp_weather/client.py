"""气象 MCP 客户端 — weather_answer 节点经 MCP 协议调用天气工具的薄封装。

纪律：**任何失败（连接 refused / 超时 / 结构异常）都返回 None 并吞掉异常**，
由调用方降级走原直调链路（resolve_weather_for_farm 的缓存/取数/50003 三态）。
MCP 是加速路径不是依赖路径：weather-mcp 服务没启动时系统行为与之前完全一致。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

TOOL_NAME = "get_weather_forecast"


async def fetch_weather_via_mcp(adcode: str, url: str, timeout_s: float = 8.0) -> dict[str, Any] | None:
    """经 MCP 调用 get_weather_forecast。成功返回 {forecast, warnings, fetched_at}，失败返回 None。"""
    try:
        async with streamablehttp_client(url, timeout=timeout_s) as (read, write, _):
            async with ClientSession(read, write) as session:
                await asyncio.wait_for(session.initialize(), timeout_s)
                result = await asyncio.wait_for(
                    session.call_tool(TOOL_NAME, {"adcode": adcode}), timeout_s
                )
    except Exception:  # noqa: BLE001 — 降级路径：连接/超时/协议异常一律静默回退
        return None
    if not result.content:
        return None
    try:
        data = json.loads(result.content[0].text)
    except (ValueError, IndexError, AttributeError):
        return None
    if not isinstance(data, dict) or not data.get("ok"):
        return None  # 工具返回结构化失败（如 key 未配）：交给直调链路给出 50003/缓存降级
    return {
        "forecast": data.get("forecast") or [],
        "warnings": data.get("warnings") or [],
        "fetched_at": data.get("fetched_at"),
    }
