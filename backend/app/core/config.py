"""全局配置 — 读取项目根 .env（架构文档三节预留结构：backend/app/core/config.py）。

.env 键（2026-10-12 用户拍板）：
- LLM_API_KEY / LLM_BASE_URL / LLM_MODEL   主对话+多模态（qwen3.8-flash）
- LLM_VISION_MODEL                          图像问诊视觉模型（FR-11，默认 qwen-vl-max-latest）
- EMBEDDING_MODEL / DASHSCOPE_API_KEY      向量/Embedding（知识库 RAG，同一 Key）
敏感信息只存在于服务端进程内存，禁止下发前端 bundle 或写入日志。
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py → parents[3] = 项目根（.env 所在）
_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ROOT_ENV, env_file_encoding="utf-8", extra="ignore")

    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen3.8-flash"
    # 图像问诊（FR-11）：病症图片可见体征识别走 DashScope 视觉模型，
    # 与主模型共用 base_url/api_key；可用 LLM_VISION_MODEL 环境变量覆盖。
    llm_vision_model: str = "qwen-vl-max-latest"
    embedding_model: str = "qwen3.7-text-embedding"
    dashscope_api_key: str = ""
    # B5 向量检索（Milvus standalone，docker-compose sf-milvus，仅绑定回环地址）
    milvus_uri: str = "http://127.0.0.1:19530"
    # 向量检索最低相似度（COSINE）：低于该分数视为不相关，回退关键词检索（防弱相关误召）
    vector_min_score: float = 0.30

    # 本服务监听（开发期仅绑定回环地址）
    ai_host: str = "127.0.0.1"
    ai_port: int = 8000

    # API 文档（/docs、/redoc、/openapi.json）默认关：公网常驻等于递一份现成的端点清单。
    # 本地调试要开就在 .env 写 EXPOSE_DOCS=true。
    expose_docs: bool = False

    # CORS 白名单（逗号分隔）。开发期是 3015 dev server；换域名/端口只改环境变量，不改代码。
    cors_origins: str = "http://localhost:3015,http://127.0.0.1:3015"

    # ----- C-26 跨进程状态（去重/防抖/合并窗口/心跳/限流/告警广播）-----
    # 默认值与 docker-compose 的 sf-redis 对齐（宿主侧端口 16379，6379 被 vmnat 占用）。
    # 留空或连不上 → 退回进程内存：单 worker 语义仍正确，多 worker 才会各算各的。
    redis_url: str = "redis://127.0.0.1:16379/0"

    # ----- 设备上行 MQTT 桥接（app/services/mqtt_bridge.py）-----
    # 开启后后端订阅 EMQX，把设备 data 帧转成 /device/ingest 的内部调用。
    # 未配置时整条桥接不启动，设备只能走 HTTP 直连（浏览器/网关）。
    mqtt_enabled: bool = False
    mqtt_host: str = "127.0.0.1"
    mqtt_port: int = 1883
    mqtt_username: str = ""
    mqtt_password: str = ""
    mqtt_topic: str = "farm/+/pen/+/+/data"

    # ----- F02/F04/F14 业务后端（切真主链路，docker-compose sf-postgres）-----
    database_url: str = "postgresql+asyncpg://smartfarm:smartfarm123@127.0.0.1:5432/smart_farm"
    # JWT 签名密钥。**刻意不给默认值**：带默认密钥上线等于把全平台身份交给任何读过源码的人。
    # 缺失时在 import 阶段就拒绝启动（见文件末尾 settings 装配断言），不允许静默跑起来。
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    # token 有效期秒数（03-API 1.1：86400 = 24h）
    jwt_ttl_seconds: int = 86400
    # 登录限流（F01 FR-02：连续 5 次失败锁 15 分钟）
    lock_threshold: int = 5
    lock_minutes: int = 15
    # ----- 未认证入口的按 IP 闸（core/ratelimit.py）-----
    # 阈值刻意可配：一个养殖场常是多人共用一条出口 IP，设备侧又常由网关代报多路，
    # 写死任何一个数都会在某种部署形态下误伤真实用户。
    login_rate_per_minute: int = 30
    register_rate_per_hour: int = 20
    ingest_rate_per_minute: int = 600  # 按 IP 而非按设备：网关一次代报几十个设备不该被挡
    # F03 离线判定阈值（07 协议真实口径 300s；演示与冒烟按此加速）
    offline_after_seconds: int = 90

    # ----- F08 气象（01-架构 G-03：key 从前端 VITE_ 变量移到这里，不再打进产物）-----
    amap_weather_key: str = ""
    # 气象 MCP Server（app/mcp_weather/server.py）。留空 = 不走 MCP、直调链路照旧；
    # 配置后 weather_answer 节点优先经 MCP 取数，失败自动降级直调（client.py 纪律）
    weather_mcp_url: str = ""

    # ----- F05/F10 LangGraph checkpoint（01-架构 10.1：独立 schema，勿手工操作）-----
    checkpoint_schema: str = "langgraph"
    checkpoint_pool_size: int = 5

    # ----- 管理员种子账号（F10 复核链路前提：兽医由管理员后台创建）-----
    # 刻意不给默认值：未配置即不建号，避免把默认凭据带进任何环境（01-架构 G-06 同源纪律）
    admin_seed_phone: str = ""
    admin_seed_password: str = ""

    # ----- D1 原料行情采集（LLM 联网检索，单次 30~190s + 约 1.5 万 token）-----
    # 默认关：多一个出网与 token 成本的副作用，显式打开才自动跑
    price_sync_enabled: bool = False
    price_sync_hour: int = 9  # 每日该时刻后触发一次（行情站点当日报价一般上午发布）

    @property
    def api_key(self) -> str:
        return self.llm_api_key or self.dashscope_api_key

    @property
    def key_configured(self) -> bool:
        return bool(self.api_key)


settings = Settings()

# 装配即校验：JWT 密钥缺失就拒绝导入，而不是带着空密钥把服务跑起来。
# 这一条同时堵住了 `.env` 没被找到（parents[3] 指错目录、部署只拷了 backend/）的情况——
# 那种场合 jwt_secret 是空串，服务在 import 阶段就起不来，而不是"看起来一切正常，
# 但任何读过源码的人都能自签管理员令牌"。
if not settings.jwt_secret:
    raise RuntimeError(
        "JWT_SECRET 未配置：请在项目根 .env 写入强随机值"
        "（生成：python -c 'import secrets; print(secrets.token_hex(32))'）"
    )
