"""气象 MCP 域 — 把 F08 气象能力封装为标准 MCP 工具，供任意 MCP 客户端动态发现与调用。

两个组成：
- server.py：FastMCP 服务（get_weather_forecast / assess_climate_risk 两个只读工具），
  `python -m app.mcp_weather.server` 启动（默认 streamable-http :8765/mcp，--stdio 走标准输入输出）。
- client.py：LangGraph weather_answer 节点的 MCP 客户端封装（失败返回 None，调用方降级直调）。

设计口径：
- 工具**不连数据库**（纯取数 + 纯规则），缓存仍由主应用 weather_cache 表负责——
  MCP 进程保持无状态，Agent 拿到数据后照旧走 save_cache 落库，两边职责不混。
- AMAP key 只在本进程（server 侧）读取，Agent 侧无需接触密钥。
"""
