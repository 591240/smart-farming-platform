"""FastAPI 入口 — 智慧养殖 AI 服务（LLM 网关 + 业务后端 + LangGraph 问诊工作流）。

启动：**必须用 `python run.py`**（Windows 上 psycopg 异步模式要求 SelectorEventLoop，
`python -m uvicorn` 起的是 ProactorEventLoop，checkpoint 连接池会 30s 超时）。
健康检查：GET /api/health（不泄露 Key，仅返回 key_configured / checkpoint 布尔）
业务路由：auth（F01）/ farm（F02+F14）/ individual（F14+F17）/ records（F04）/ species
         / device（F03）/ breeding（F07）/ agent（F05 SSE）/ vet（F10 复核）/ admin
"""

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.agents import close_agent_runtime, init_agent_runtime, prune_checkpoints
from app.api.admin import router as admin_router
from app.api.agent import router as agent_router
from app.api.ai import router as ai_router
from app.api.auth import router as auth_router
from app.api.breeding import router as breeding_router
from app.api.device import ingest_router, offline_scan
from app.api.device import router as device_router
from app.api.feed import admin_router as feed_admin_router, router as feed_router
from app.api.kb import router as kb_router, seed_kb_documents
from app.api.feeding import router as feeding_router
from app.api.farm import router as farm_router
from app.api.individual import router as individual_router
from app.api.notify import router as notify_router
from app.api.records import router as records_router
from app.api.rules import MEDICINE_LIB_SEED
from app.api.scene import router as scene_router
from app.api.species import router as species_router
from app.api.vet import router as vet_router, sweep_timeout_reviews
from app.api.vision import router as vision_router
from app.api.ws import router as ws_router
from app.api.weather import router as weather_router
from app.core import statestore
from app.core.config import settings
from app.core.db import SessionLocal, init_db
from app.core.logging import new_request_id, request_id_var, setup_logging
from app.core.response import (
    ApiError,
    api_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.core.security import hash_password_async
from app.models import MedicineLib, User
from app.services.feed_engine import seed_feed_data
from app.services.llm import close_http
from app.services import mqtt_bridge

logger = logging.getLogger("app")
MAINT_INTERVAL_SECONDS = 600  # 维护周期：超时降级扫描 + checkpoint 清理
OFFLINE_SCAN_INTERVAL_SECONDS = 60  # 设备离线心跳扫描周期（F03 FR-05）
_price_synced_on: str = ""  # 最近一次自动行情采集的本地日期（每日一次）


async def _maybe_sync_prices() -> None:
    """每日行情采集（D1）。默认关——多一项出网与 token 成本，需 PRICE_SYNC_ENABLED=true。"""
    global _price_synced_on
    if not settings.price_sync_enabled:
        return
    from app.services import price_sync as ps

    now = datetime.now()
    today = now.date().isoformat()
    if today == _price_synced_on or now.hour < settings.price_sync_hour:
        return
    _price_synced_on = today  # 先落牌：失败也不在同一小时内重试，避免刷 token
    await ps.run_once()


async def _warm_weather(db) -> int:
    """预热气象缓存（F08 → F06 高温下调链路）。

    `today_max_temp_for_pen` 按 F06 FR-05 口径**只读缓存、不触发外部调用**（保证配方计算
    不被网络拖住），而 mock 时代是 `client.ts` 在请求 /feed-plan 前替它刷新的；切真后
    只剩「有人打开大屏/设置页」才会写缓存 → 当天先开哪个页面会改变配方结果。
    缓存 6h TTL，所以本函数每轮大多直接命中新鲜缓存、零外部调用。
    """
    from app.models import Farm
    from app.services import weather as wx

    farms = (await db.execute(select(Farm).where(Farm.is_deleted == False))).scalars().all()  # noqa: E712
    live = 0
    for farm in farms:
        result = await wx.resolve_weather_for_farm(db, farm)
        if result.get("source") == "live":
            live += 1
    await db.commit()
    return live


async def _maintenance_loop(stop: asyncio.Event) -> None:
    """替代 APScheduler 的轻量周期任务（环境未装 apscheduler，不为此加依赖）。

    装好 APScheduler 后本函数可整体换掉，行为口径不变（EX-05/EX-06 降级语义已在
    smoke 与 v1 惰性扫描里验过）。
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=MAINT_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break
        try:
            async with SessionLocal() as db:
                degraded = await sweep_timeout_reviews(db)
                warmed = await _warm_weather(db)
            pruned = await prune_checkpoints()
            await _maybe_sync_prices()
            if degraded or pruned or warmed:
                logger.info("维护任务：超时降级复核 %s 条，清理 checkpoint %s 行，实时刷新气象 %s 个场", degraded, pruned, warmed)
        except Exception:  # noqa: BLE001 - 周期任务不得因单次异常退出
            logger.exception("维护任务异常（下一周期继续）")


async def _offline_scan_loop(stop: asyncio.Event) -> None:
    """设备离线心跳扫描（F03 FR-05「每分钟扫描」）。

    此前离线判定只由 /alert/list 与 /device/ingest 顺带触发，等于"有人打开页面才做判定"，
    关掉浏览器后设备永远显示 online。全量扫描是系统职责，故不传 farm_ids
    （农户 HTTP 触发的扫描已按各自农场收敛，见 app/api/device.py）。
    """
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=OFFLINE_SCAN_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break
        try:
            async with SessionLocal() as db:
                await offline_scan(db)
                await db.commit()
        except Exception:  # noqa: BLE001 - 周期任务不得因单次异常退出
            logger.exception("离线扫描异常（下一周期继续）")


async def seed_medicine_lib() -> None:
    """兽药库种子（幂等：按 med_code 判重；与 mock 惰性迁移同口径）。"""
    async with SessionLocal() as db:
        known = set((await db.execute(select(MedicineLib.med_code))).scalars())
        for seed in MEDICINE_LIB_SEED:
            if seed["med_code"] not in known:
                db.add(MedicineLib(**seed))
        await db.commit()


async def seed_admin() -> None:
    """管理员种子账号（幂等：按 phone 判重）。

    未配置 ADMIN_SEED_PHONE / ADMIN_SEED_PASSWORD 时跳过——不硬编码默认凭据，
    避免任何环境里出现"已知密码的 admin"。F10 复核链路需要 admin 建兽医账号，
    本地演示在 .env 里配这两个键即可。
    """
    phone, password = settings.admin_seed_phone.strip(), settings.admin_seed_password
    if not phone or not password:
        logger.warning("ADMIN_SEED_PHONE/ADMIN_SEED_PASSWORD 未配置：跳过管理员种子（F10 需 admin 建兽医号）")
        return
    async with SessionLocal() as db:
        exists = (await db.execute(select(User).where(User.phone == phone))).scalar_one_or_none()
        if exists is not None:
            return
        db.add(
            User(
                phone=phone,
                password_hash=await hash_password_async(password),
                nickname="系统管理员",
                role="admin",
                main_species_code=None,
                is_enable=True,
            )
        )
        await db.commit()
        logger.info("已创建管理员种子账号 %s", phone)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    # C-26 跨进程状态：Redis 可达则去重/防抖/合并窗口/限流/广播全进程共享，连不上退回进程
    # 内存（单 worker 语义不变）。探活失败不阻塞启动，所以这一步永远不该向外抛
    await statestore.init()
    await init_db()  # 演示期 create_all + 幂等列补齐；生产换 Alembic（01-架构 G-02）
    await seed_medicine_lib()
    await seed_admin()
    async with SessionLocal() as db:
        await seed_feed_data(db)  # F06 营养标准 + 原料价种子（幂等，按业务键判重）
        await seed_kb_documents(db)  # F16 知识库示范文档（表空时灌入，含附件落盘）
    try:
        await init_agent_runtime()  # checkpoint 连接池 + saver.setup()（schema langgraph）
    except Exception:  # noqa: BLE001 — 工作流不可用不应拖垮业务接口
        logger.exception("LangGraph checkpoint 初始化失败：/agent/chat 将不可用，其余接口正常")
    # B5：Milvus 惰性建索引（集合缺失/为空才灌库，避免重启重复花 embedding 钱；
    # 失败仅告警，检索层自动回退关键词计分）
    try:
        from app.agents.vector_store import ensure_indexed

        indexed = await ensure_indexed()
        if indexed > 0:
            logger.info("B5 向量索引已灌库 %s 条（kb_entries @ Milvus）", indexed)
    except Exception:  # noqa: BLE001
        logger.exception("B5 向量索引初始化异常（检索回退关键词路径）")
    stop = asyncio.Event()
    maint = asyncio.create_task(_maintenance_loop(stop))
    scanner = asyncio.create_task(_offline_scan_loop(stop))
    # 设备上行 MQTT 桥接（MQTT_ENABLED=true 才启动；未装 paho/连不上只告警不阻塞业务）
    mqtt_bridge.start(asyncio.get_running_loop())
    yield
    stop.set()
    maint.cancel()
    scanner.cancel()
    mqtt_bridge.stop()
    await close_agent_runtime()
    await close_http()
    await statestore.close()


app = FastAPI(
    title="智慧养殖 AI 服务（LLM 网关 + 业务后端 + LangGraph 问诊）",
    version="0.3.0",
    lifespan=lifespan,
    # 文档端点默认不存在（EXPOSE_DOCS=true 才开）：公网常驻的 /docs + /openapi.json
    # 等于把 95 个端点、参数名和错误码结构一次性交给扫描器
    docs_url="/docs" if settings.expose_docs else None,
    redoc_url="/redoc" if settings.expose_docs else None,
    openapi_url="/openapi.json" if settings.expose_docs else None,
)

# CORS：来源从环境变量读（CORS_ORIGINS，逗号分隔）。写死 localhost:3015 时换域名/端口即失效。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


@app.middleware("http")
async def request_id_middleware(request: Request, call_next) -> Response:
    """request_id 贯穿 API → Agent → 台账（01-架构十四节）。

    客户端带 X-Request-ID 就沿用它，便于把前端日志与服务端日志对齐。
    SSE 响应体在中间件返回之后才逐帧生成，流内的日志由 api/agent.py 自行回填同一个 id。
    """
    rid = request.headers.get("X-Request-ID") or new_request_id()
    token = request_id_var.set(rid)
    request.state.request_id = rid
    try:
        response = await call_next(request)
    finally:
        request_id_var.reset(token)
    response.headers["X-Request-ID"] = rid
    return response

# 业务错误统一转 HTTP 200 + {code,message,data}（前端 ApiError 处理零改动）
app.add_exception_handler(ApiError, api_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(RequestValidationError, validation_error_handler)  # type: ignore[arg-type]
app.add_exception_handler(Exception, unhandled_error_handler)

# Prometheus 应用指标（2026-11-05 监控运维项目对接）：http_requests_total（请求量）、
# http_request_duration_seconds（延迟分布）、状态码分布（错误率）。/metrics 只含端点
# 计数与延迟分位数，无业务数据；经 nginx 精确匹配直通，供外部 Prometheus 抓取。
from prometheus_fastapi_instrumentator import Instrumentator

Instrumentator(excluded_handlers=["/metrics"]).instrument(app).expose(
    app, include_in_schema=False
)

app.include_router(ai_router)
app.include_router(auth_router)
app.include_router(farm_router)
app.include_router(individual_router)
app.include_router(device_router)
app.include_router(ingest_router)  # C-24：设备上行双轨鉴权，不挂农户 JWT 的 router 级依赖
app.include_router(breeding_router)
app.include_router(records_router)
app.include_router(species_router)
app.include_router(agent_router)
app.include_router(vet_router)
app.include_router(vision_router)
app.include_router(ws_router)
app.include_router(admin_router)
app.include_router(weather_router)
app.include_router(notify_router)
app.include_router(feeding_router)
app.include_router(feed_router)
app.include_router(feed_admin_router)  # /feed/price/sync：会改写全局参考价，仅管理员
app.include_router(kb_router)
app.include_router(scene_router)


@app.get("/api/health")
def health() -> dict:
    from app.agents.graph import _graph  # 局部导入：只读探针状态

    return {
        "code": 0,
        "message": "ok",
        "data": {
            "status": "ok",
            "model": settings.llm_model,
            "key_configured": settings.key_configured,
            # 刻意不回吐 database_url：`split("@")[-1]` 看着无害，实际把内网 host:port/库名
            # 直接给到匿名请求。部署验收要看的两项是 state_backend 与 checkpoint。
            # C-26：跨进程状态实际落在哪。多 worker 部署时这里必须是 redis
            "state_backend": "redis" if statestore.is_redis_backed() else "memory",
            "checkpoint": _graph is not None,
        },
    }
