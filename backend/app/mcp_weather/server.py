"""气象 MCP Server — FastMCP 封装（工具只读、无状态、不连库）。

启动：
    python -m app.mcp_weather.server            # streamable-http，监听 127.0.0.1:8765/mcp
    python -m app.mcp_weather.server --stdio    # 标准输入输出传输（冒烟脚本 / IDE 调试用）

工具清单（MCP 客户端 list_tools 可自动发现）：
- get_weather_forecast(adcode)：高德 3 天预报（降水概率/风力由文案估算，口径同 F08）
- assess_climate_risk(adcode, species_code)：按物种气候规则评估高温/暴雨/霜冻/大风风险

上游取数失败不抛异常：返回 {"ok": false, "error": ...} 结构化结果，
由调用方（Agent 节点）决定降级策略——MCP 工具不做静默兜底，不编造天气。
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from app.api.rules import climate_risk
from app.services import weather as wx

mcp = FastMCP("sf-weather", host="127.0.0.1", port=8765)


@mcp.tool()
async def get_weather_forecast(adcode: str) -> dict[str, Any]:
    """查询指定行政区划（adcode）未来 3 天天气预报。

    Args:
        adcode: 高德行政区划码（区/县级，如 431300），取自养殖场档案的 adcode 字段。

    Returns:
        ok=true 时含 forecast（date/temp_min/temp_max/text_day/precip_prob/precip_mm/wind_level）
        与 warnings（当前恒为空：高德无官方预警接口）；ok=false 时含 error 原因。
    """
    try:
        payload = await wx.fetch_live_from_amap(adcode)
    except wx.WeatherUnavailable as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, **payload}


@mcp.tool()
async def assess_climate_risk(adcode: str, species_code: str = "pig") -> dict[str, Any]:
    """按养殖物种的气候风险阈值评估未来 3 天养殖风险（高温热应激/暴雨/霜冻/大风）。

    Args:
        adcode: 高德行政区划码（区/县级）。
        species_code: 物种编码，pig / sheep / cattle，决定阈值与文案前缀。

    Returns:
        ok=true 时含 risks 列表（risk_type/level/title/impact/advice/data_hint，按风险等级降序）；
        ok=false 时含 error 原因（取数失败或物种无规则时不编造结果）。
    """
    rule = climate_risk(species_code)
    if not rule:
        return {"ok": False, "error": f"物种 {species_code} 未配置气候风险规则"}
    try:
        payload = await wx.fetch_live_from_amap(adcode)
    except wx.WeatherUnavailable as e:
        return {"ok": False, "error": str(e)}
    risks = wx._match_risks(
        rule,
        payload.get("forecast") or [],
        payload.get("warnings") or [],
        wx.species_name(species_code),
    )
    return {
        "ok": True,
        "species": wx.species_name(species_code),
        "risks": risks,
        "fetched_at": payload.get("fetched_at"),
    }


if __name__ == "__main__":
    import sys

    mcp.run(transport="stdio" if "--stdio" in sys.argv else "streamable-http")
