"""LLM Client — OpenAI 兼容接口统一封装（01-架构二节：环境变量配置 base_url / api_key / model）。

qwen3.8-flash 经 DashScope compatible-mode 调用；失败统一抛 LLMError，
由路由层捕获转为 {code:50003} 响应（前端静默回退规则管道，EX-02 同款降级纪律）。
"""

import json
import re
from typing import Any

import httpx

from app.core.config import settings


class LLMError(Exception):
    """上游 LLM 调用失败（网络/鉴权/结构异常）。"""


# 进程内复用连接池：此前每次调用都 `async with AsyncClient()` 新建客户端，
# 等于每个请求都重做一次 TCP + TLS 握手。单例 + 每次请求传 timeout 是等价且更省的写法。
_client: httpx.AsyncClient | None = None


def _http() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
            timeout=httpx.Timeout(60.0),
        )
    return _client


async def close_http() -> None:
    """进程退出时关池（lifespan 调用）。"""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def chat(
    messages: list[dict[str, Any]],
    *,
    temperature: float = 0.3,
    max_tokens: int = 900,
    timeout: float = 60.0,
    model: str | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    """OpenAI 兼容 chat/completions 统一封装。

    messages 中 message["content"] 支持两种形态（OpenAI 兼容协议同构）：
    - str：纯文本对话（意图分类/诊断增强等文本模型场景）；
    - list：多模态分片 [{"type":"text",...},{"type":"image_url","image_url":{"url":"data:..."}}]
      （FR-11 图像问诊，走 settings.llm_vision_model；模型名经 model 参数覆盖）。
    extra：上游私有参数透传（如 DashScope 的 enable_search / search_options）。
    """
    if not settings.key_configured:
        raise LLMError("LLM_API_KEY 未配置")
    # enable_thinking=False：qwen3 系关闭思考模式，分类/增强类短输出延迟从 ~30s 降至秒级；
    # 不支持该参数的模型返回 400 时自动去参重试一次。
    payload: dict[str, Any] = {
        "model": model or settings.llm_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "enable_thinking": False,
        **(extra or {}),
    }
    try:
        client = _http()
        resp = await client.post(
            f"{settings.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.api_key}"},
            json=payload,
            timeout=timeout,
        )
        if resp.status_code == 400 and "enable_thinking" in resp.text:
            payload.pop("enable_thinking")
            resp = await client.post(
                f"{settings.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.api_key}"},
                json=payload,
                timeout=timeout,
            )
    except httpx.HTTPError as e:
        raise LLMError(f"LLM 网络异常: {type(e).__name__}") from e
    if resp.status_code != 200:
        # 不回传响应体全文，避免泄钥/泄内部信息
        raise LLMError(f"LLM 上游 HTTP {resp.status_code}")
    try:
        content = resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, ValueError) as e:
        raise LLMError("LLM 响应结构异常") from e
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM 返回空内容")
    return content.strip()


def extract_json(text: str) -> dict[str, Any]:
    """容错解析模型 JSON：剥 markdown 代码围栏，取首个 { 到最后一个 }。"""
    t = re.sub(r"```(?:json)?", "", text.strip())
    start, end = t.find("{"), t.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("LLM 未返回 JSON")
    try:
        obj = json.loads(t[start : end + 1])
    except json.JSONDecodeError as e:
        raise LLMError("LLM JSON 解析失败") from e
    if not isinstance(obj, dict):
        raise LLMError("LLM JSON 非对象")
    return obj
