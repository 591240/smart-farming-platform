"""跨进程状态存储 — docs/08 C-26「进程内状态」的落地。

去重、防抖计数、合并窗口、心跳、限流、告警广播原本各存一份进程内 dict，`uvicorn
--workers N` 时按进程分裂（同一设备在 A 进程超标 2 次、B 进程 1 次，永远凑不满 3 次）。
走 Redis 后这些状态全进程共享；Redis 不可达时**一次性**降级回进程内存并打 warning——
演示与冒烟不必依赖容器，多进程部署配上 REDIS_URL 才是真分布式。

两条契约，两个实现都必须满足，调用方不需要知道自己走的哪条：

- `claim` 是原子占位：同一 key 在 TTL 内只有一次返回 True（合并窗口/去重靠它）；
- `publish` 保证本进程的订阅回调恰好被调用一次——Redis 模式下是自己那条订阅连接
  收回来的，所以发布方永远不会和本地直推撞出重复推送。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from app.core.config import settings

logger = logging.getLogger("app.state")

NS = "sf"  # 键命名空间：Redis 里 sf:merge:* / sf:dedup:* 一眼可辨，也便于按前缀清理
_MAX_KEYS = 5000  # 内存实现的兜底上界：宁可淘汰过期项，也不让 dict 随请求无界增长

Handler = Callable[[dict[str, Any]], Awaitable[None]]


def now_ms() -> int:
    return int(time.time() * 1000)


def _k(key: str) -> str:
    return f"{NS}:{key}"


class _Base:
    """两个实现共享的订阅注册表——降级时回调跟着走，调用方无需重新注册。"""

    def __init__(self) -> None:
        self.handlers: dict[str, list[Handler]] = {}
        self.redis_backed = False

    def subscribe(self, channel: str, handler: Handler) -> None:
        self.handlers.setdefault(channel, []).append(handler)

    async def _deliver(self, channel: str, message: dict[str, Any]) -> None:
        for handler in self.handlers.get(channel, ()):
            try:
                await handler(message)
            except Exception:  # noqa: BLE001 — 推送是旁路，异常绝不冒回业务事务
                logger.exception("订阅回调异常 channel=%s", channel)


class _Memory(_Base):
    """进程内存实现（单进程演示 / Redis 不可达时的降级形态）。"""

    def __init__(self) -> None:
        super().__init__()
        self._kv: dict[str, tuple[Any, int]] = {}  # key -> (value, 过期 ms)

    def _fresh(self, key: str) -> Any:
        value, expires_at = self._kv.get(key, (None, 0))
        if value is None:
            return None
        if now_ms() >= expires_at:
            self._kv.pop(key, None)
            return None
        return value

    def _put(self, key: str, value: Any, ttl_ms: int) -> None:
        self._kv[key] = (value, now_ms() + ttl_ms)
        if len(self._kv) > _MAX_KEYS:
            cutoff = now_ms()
            for stale in [k for k, (_v, exp) in self._kv.items() if exp <= cutoff]:
                self._kv.pop(stale, None)

    async def claim(self, key: str, ttl_ms: int) -> bool:
        if self._fresh(key) is not None:
            return False
        self._put(key, 1, ttl_ms)
        return True

    async def bump(self, key: str, ttl_ms: int) -> int:
        count = int(self._fresh(key) or 0) + 1
        self._put(key, count, ttl_ms)
        return count

    async def reset_count(self, key: str, ttl_ms: int) -> None:
        self._put(key, 0, ttl_ms)

    async def stamp(self, key: str, value_ms: int, ttl_ms: int) -> None:
        self._put(key, value_ms, ttl_ms)

    async def last_stamp(self, key: str) -> int | None:
        value = self._fresh(key)
        return None if value is None else int(value)

    async def drop(self, key: str) -> None:
        self._kv.pop(key, None)

    async def allow_within(self, key: str, limit: int, window_ms: int) -> bool:
        hits = self._fresh(key)
        if hits is None:
            hits = []
        cutoff = now_ms() - window_ms
        while hits and hits[0] <= cutoff:
            hits.pop(0)
        if len(hits) >= limit:
            self._put(key, hits, window_ms)
            return False
        hits.append(now_ms())
        self._put(key, hits, window_ms)  # 每次命中都续期，与 Redis 侧 PEXPIRE 同口径
        return True

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        await self._deliver(channel, message)

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


# 清过期 → 判上限 → 占名额 → 续期，四步不能在中间被并发请求插进来，所以走 Lua
_LUA_SLIDING = """
local now = tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - tonumber(ARGV[2]))
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[3]) then
  return 0
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""


class _Redis(_Base):
    """Redis 实现：多 worker / 多机部署下共享同一份状态。"""

    def __init__(self, client: Any) -> None:
        super().__init__()
        self.redis_backed = True
        self.r = client
        self._sliding = client.register_script(_LUA_SLIDING)
        self._pubsub: Any = None
        self._task: asyncio.Task | None = None

    async def claim(self, key: str, ttl_ms: int) -> bool:
        return bool(await self.r.set(_k(key), 1, nx=True, px=ttl_ms))

    async def bump(self, key: str, ttl_ms: int) -> int:
        async with self.r.pipeline(transaction=True) as pipe:
            pipe.incr(_k(key))
            pipe.pexpire(_k(key), ttl_ms)
            count, _ = await pipe.execute()
        return int(count)

    async def reset_count(self, key: str, ttl_ms: int) -> None:
        await self.r.set(_k(key), 0, px=ttl_ms)

    async def stamp(self, key: str, value_ms: int, ttl_ms: int) -> None:
        await self.r.set(_k(key), value_ms, px=ttl_ms)

    async def last_stamp(self, key: str) -> int | None:
        value = await self.r.get(_k(key))
        return None if value is None else int(value)

    async def drop(self, key: str) -> None:
        await self.r.delete(_k(key))

    async def allow_within(self, key: str, limit: int, window_ms: int) -> bool:
        return bool(await self._sliding(keys=[_k(key)], args=[now_ms(), window_ms, limit, uuid.uuid4().hex]))

    async def publish(self, channel: str, message: dict[str, Any]) -> None:
        await self.r.publish(_k(channel), json.dumps(message, ensure_ascii=False))

    async def start(self) -> None:
        self._pubsub = self.r.pubsub()
        await self._pubsub.psubscribe(f"{NS}:*")
        self._task = asyncio.create_task(self._pump(), name="statestore-pubsub")

    async def _pump(self) -> None:
        while True:
            try:
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            except asyncio.CancelledError:
                return
            except Exception as exc:  # noqa: BLE001 — 订阅断了没有重连价值：直接降级，本进程仍能直推
                _degrade(f"Redis 订阅中断（{exc.__class__.__name__}）")
                return
            if message is None:
                continue
            channel = str(message["channel"]).removeprefix(f"{NS}:")
            try:
                await self._deliver(channel, json.loads(message["data"]))
            except Exception:  # noqa: BLE001
                logger.exception("广播分发异常 channel=%s", channel)

    async def close(self) -> None:
        task, self._task = self._task, None
        # 订阅泵自己发现断线时会走到这里：那种场合不能 cancel/await 当前任务，否则自锁
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._pubsub is not None:
            await self._pubsub.aclose()
            self._pubsub = None


_backend: _Base = _Memory()
_client: Any = None


def _degrade(reason: str) -> None:
    """Redis 失联：换内存实现，已注册的订阅回调一起带走，调用方无感。

    刻意**不回切**：探活留在进程启动时做一次。降级后每个请求都试 Redis，等于让业务为
    一个已经确认不可用的依赖反复付超时时间。Redis 恢复后重启进程即可重新接管。
    """
    global _backend
    if not _backend.redis_backed:
        return
    previous = _backend
    _backend = _Memory()
    for channel, handlers in previous.handlers.items():
        for handler in handlers:
            _backend.subscribe(channel, handler)
    asyncio.get_running_loop().create_task(previous.close())
    logger.warning("%s — 跨进程状态退回进程内存（单 worker 语义仍正确，多 worker 会各算各的）", reason)


async def _op(name: str, *args: Any) -> Any:
    """所有读写的唯一入口：Redis 路径上任何异常都降级重试一次，业务永不因状态层而 500。"""
    backend = _backend
    if not backend.redis_backed:
        return await getattr(backend, name)(*args)
    try:
        return await getattr(backend, name)(*args)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        _degrade(f"Redis {name} 失败（{exc.__class__.__name__}）")
        return await getattr(_backend, name)(*args)


async def claim(key: str, ttl_ms: int) -> bool:
    """原子占位。True=占到（不在合并窗口内 / 消息未见过）；False=已被占，本次应跳过。"""
    return await _op("claim", key, ttl_ms)


async def bump(key: str, ttl_ms: int) -> int:
    """计数 +1 并把 TTL 续到 ttl_ms（防抖：每次超标都刷新，静默超时后自动归零）。"""
    return await _op("bump", key, ttl_ms)


async def reset_count(key: str, ttl_ms: int) -> None:
    await _op("reset_count", key, ttl_ms)


async def stamp(key: str, value_ms: int, ttl_ms: int) -> None:
    await _op("stamp", key, value_ms, ttl_ms)


async def last_stamp(key: str) -> int | None:
    return await _op("last_stamp", key)


async def drop(key: str) -> None:
    await _op("drop", key)


async def allow_within(key: str, limit: int, window_ms: int) -> bool:
    """窗口内未达 limit 则放行并占一个名额（滑动窗口，不是固定桶）。"""
    return await _op("allow_within", key, limit, window_ms)


def subscribe(channel: str, handler: Handler) -> None:
    _backend.subscribe(channel, handler)


async def publish(channel: str, message: dict[str, Any]) -> None:
    await _op("publish", channel, message)


def is_redis_backed() -> bool:
    return _backend.redis_backed


async def init() -> bool:
    """lifespan 调用：探活 Redis，可达则接管状态并启动订阅泵，否则保持内存实现。"""
    global _backend, _client
    if not settings.redis_url:
        logger.info("未配置 REDIS_URL — 跨进程状态用进程内存（docs/08 C-26）")
        return False
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await client.ping()
    except Exception as exc:  # noqa: BLE001 — 连不上就降级，不阻塞业务接口启动
        logger.warning("Redis 不可用（%s）— 跨进程状态用进程内存", exc.__class__.__name__)
        return False
    _client = client
    previous = _backend
    _backend = _Redis(client)
    for channel, handlers in previous.handlers.items():
        for handler in handlers:
            _backend.subscribe(channel, handler)
    await _backend.start()
    logger.info("跨进程状态已接 Redis %s（去重/防抖/合并窗口/限流/告警广播）", settings.redis_url)
    return True


async def close() -> None:
    """lifespan 退出：先停订阅泵，再关连接池，实现换回内存以备重启复用。"""
    global _backend, _client
    if _backend.redis_backed:
        await _backend.close()
    if _client is not None:
        await _client.aclose()
        _client = None
    _backend = _Memory()
