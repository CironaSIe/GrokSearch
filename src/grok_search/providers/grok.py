import httpx
import json
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_random_exponential
from tenacity.wait import wait_base
from zoneinfo import ZoneInfo
from .base import BaseSearchProvider, SearchResult
from ..prompts import search_prompt, fetch_prompt, url_describe_prompt, rank_sources_prompt
from ..utils import redact_sensitive_text
from ..logger import log_info
from ..config import config


def get_local_time_info() -> str:
    """获取本地时间信息，用于注入到搜索查询中"""
    try:
        # 尝试获取系统本地时区
        local_tz = datetime.now().astimezone().tzinfo
        local_now = datetime.now(local_tz)
    except Exception:
        # 降级使用 UTC
        local_now = datetime.now(timezone.utc)

    # 格式化时间信息
    weekdays_cn = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    weekday = weekdays_cn[local_now.weekday()]

    return (
        f"[Current Time Context]\n"
        f"- Date: {local_now.strftime('%Y-%m-%d')} ({weekday})\n"
        f"- Time: {local_now.strftime('%H:%M:%S')}\n"
        f"- Timezone: {local_now.tzname() or 'Local'}\n"
    )


def _split_csv_values(value: str) -> list[str]:
    return [item.strip() for item in (value or "").split(",") if item.strip()]


def _valid_reasoning_effort(effort: str) -> str:
    normalized = (effort or "").strip().lower()
    return normalized if normalized in {"low", "medium", "high", "xhigh"} else ""


def _build_reasoning_prompt(effort: str) -> str:
    effort = effort.strip().lower()
    if effort == "low":
        return "Be concise; minimal analysis."
    elif effort == "high":
        return "Think step by step; show your reasoning."
    elif effort == "xhigh":
        return "Very detailed reasoning; explore multiple angles."
    return ""


def _is_x_platform(platform: str) -> bool:
    normalized = (platform or "").strip().lower()
    return normalized in {"x", "twitter", "x/twitter", "twitter/x", "推特", "x平台"}


def _needs_time_context(query: str) -> bool:
    """检查查询是否需要时间上下文"""
    # 中文时间相关关键词
    cn_keywords = [
        "当前", "现在", "今天", "明天", "昨天",
        "本周", "上周", "下周", "这周",
        "本月", "上月", "下月", "这个月",
        "今年", "去年", "明年",
        "最新", "最近", "近期", "刚刚", "刚才",
        "实时", "即时", "目前",
    ]
    # 英文时间相关关键词
    en_keywords = [
        "current", "now", "today", "tomorrow", "yesterday",
        "this week", "last week", "next week",
        "this month", "last month", "next month",
        "this year", "last year", "next year",
        "latest", "recent", "recently", "just now",
        "real-time", "realtime", "up-to-date",
    ]

    query_lower = query.lower()

    for keyword in cn_keywords:
        if keyword in query:
            return True

    for keyword in en_keywords:
        if keyword in query_lower:
            return True

    return False

RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _is_retryable_exception(exc) -> bool:
    """检查异常是否可重试"""
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.ConnectError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in RETRYABLE_STATUS_CODES
    return False


class _WaitWithRetryAfter(wait_base):
    """等待策略：优先使用 Retry-After 头，否则使用指数退避"""

    def __init__(self, multiplier: float, max_wait: int):
        self._base_wait = wait_random_exponential(multiplier=multiplier, max=max_wait)
        self._protocol_error_base = 3.0

    def __call__(self, retry_state):
        if retry_state.outcome and retry_state.outcome.failed:
            exc = retry_state.outcome.exception()
            if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429:
                retry_after = self._parse_retry_after(exc.response)
                if retry_after is not None:
                    return retry_after
            if isinstance(exc, httpx.RemoteProtocolError):
                return self._base_wait(retry_state) + self._protocol_error_base
        return self._base_wait(retry_state)

    def _parse_retry_after(self, response: httpx.Response) -> Optional[float]:
        """解析 Retry-After 头（支持秒数或 HTTP 日期格式）"""
        header = response.headers.get("Retry-After")
        if not header:
            return None
        header = header.strip()

        if header.isdigit():
            return float(header)

        try:
            retry_dt = parsedate_to_datetime(header)
            if retry_dt.tzinfo is None:
                retry_dt = retry_dt.replace(tzinfo=timezone.utc)
            delay = (retry_dt - datetime.now(timezone.utc)).total_seconds()
            return max(0.0, delay)
        except (TypeError, ValueError):
            return None


class GrokSearchProvider(BaseSearchProvider):
    def __init__(self, api_url: str, api_key: str, model: str = "grok-4-fast", reasoning_effort: str = "", timeout: int | None = None):
        super().__init__(api_url, api_key)
        self.model = model
        self.reasoning_effort = _valid_reasoning_effort(reasoning_effort)
        self.timeout = timeout

    def get_provider_name(self) -> str:
        return "Grok"

    @property
    def _use_responses_api(self) -> bool:
        return config.force_responses_api or "multi-agent" in self.model.lower()

    @property
    def _api_endpoint(self) -> str:
        return "responses" if self._use_responses_api else "chat/completions"

    def _build_chat_payload(self, system: str, user: str, *, stream: bool = True, tools: list | None = None) -> dict:
        payload: dict = {"model": self.model, "stream": stream}
        if tools:
            payload["tools"] = tools
        if self._use_responses_api:
            payload["input"] = user
            payload["instructions"] = system
        else:
            payload["messages"] = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
        return payload

    def _build_search_payload(
        self,
        query: str,
        platform: str = "",
        from_date: str = "",
        to_date: str = "",
        allowed_domains: str = "",
        max_search_results: int = 0,
        reasoning_effort: str = "",
        direction: str = "",
    ) -> dict:
        prompt_lines: list[str] = []
        payload: dict = {
            "model": self.model,
            "stream": True,
        }

        tools: list[dict] = []
        if platform:
            if _is_x_platform(platform):
                tools.append({"type": "x_search"})
                prompt_lines.append("Search both web and X/Twitter for this query.")
            else:
                prompt_lines.append(f"Focus the search on this platform or source type: {platform}.")

        if config.web_search_tool_enabled:
            tools.insert(0, {"type": "web_search"})

        if tools:
            payload["tools"] = tools

        search_parameters: dict = {}
        if from_date:
            search_parameters["from_date"] = from_date
            prompt_lines.append(f"Only use results dated on or after {from_date}.")
        if to_date:
            search_parameters["to_date"] = to_date
            prompt_lines.append(f"Only use results dated on or before {to_date}.")
        if search_parameters:
            search_parameters["mode"] = "on"
            payload["search_parameters"] = search_parameters

        domains = _split_csv_values(allowed_domains)
        if domains:
            prompt_lines.append(
                "Only search and cite these domains: " + ", ".join(domains) + "."
            )

        if max_search_results > 0:
            prompt_lines.append(
                f"Use no more than {max_search_results} high-quality search results or citations in the final answer."
            )

        effort = _valid_reasoning_effort(reasoning_effort) or self.reasoning_effort

        dir_prompt = ""
        if direction:
            dir_lower = direction.lower().strip()
            dir_map = {
                "mainstream": "Prioritize official, institutional, and widely-cited authoritative sources belonging to the mainstream or establishment narrative.",
                "diverse": "Actively seek a range of perspectives and ideological leanings on this topic — from establishment to fringe, across the spectrum.",
                "critical": "This topic has competing narratives. Actively seek perspectives that challenge or critique the mainstream or official view.",
                "eyewitness": "Prioritize first-hand accounts, personal diaries, primary source documents — records produced by direct participants or observers at the time of the events.",
                "adversarial": "Prioritize sources from the losing side, critics of the dominant narrative, or parties whose interests oppose the mainstream account.",
                "external": "Prioritize records from neutral third-party observers with no direct stake in the outcome (e.g., foreign reporters, independent monitors, non-aligned witnesses).",
                "comprehensive": "Actively collect sources across ALL positions: official, eyewitness, adversarial, external, and academic. Present the range of perspectives.",
            }
            dir_prompt = f"\n\n[Source Direction] {dir_map[dir_lower]}" if dir_lower in dir_map else f"\n\n[Source Direction] {direction}"

        time_context = get_local_time_info() + "\n"
        controls = ("\n\n[Search Controls]\n" + "\n".join(f"- {line}" for line in prompt_lines)) if prompt_lines else ""

        system_content = search_prompt
        if dir_prompt:
            system_content += dir_prompt
        user_content = time_context + query + controls

        if self._use_responses_api:
            if effort:
                api_effort = "high" if effort == "xhigh" else effort
                payload["reasoning"] = {"effort": api_effort}
            payload["instructions"] = system_content
            payload["input"] = user_content
        else:
            if effort:
                payload["reasoning_effort"] = effort
            rp = _build_reasoning_prompt(effort)
            if rp:
                system_content += "\n\n" + rp
            payload["messages"] = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ]

        return payload

    async def search(
        self,
        query: str,
        platform: str = "",
        min_results: int = 3,
        max_results: int = 10,
        ctx=None,
        from_date: str = "",
        to_date: str = "",
        allowed_domains: str = "",
        max_search_results: int = 0,
        reasoning_effort: str = "",
        direction: str = "",
    ) -> List[SearchResult]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_search_payload(
            query=query,
            platform=platform,
            from_date=from_date,
            to_date=to_date,
            allowed_domains=allowed_domains,
            max_search_results=max_search_results,
            reasoning_effort=reasoning_effort,
            direction=direction,
        )

        await log_info(ctx, f"search_payload: {redact_sensitive_text(json.dumps(payload, ensure_ascii=False), self.api_key)}", config.debug_enabled)

        return await self._execute_stream_with_retry(headers, payload, ctx)

    async def fetch(self, url: str, ctx=None) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_chat_payload(
            system=fetch_prompt,
            user=url + "\n获取该网页内容并返回其结构化Markdown格式",
        )
        return await self._execute_stream_with_retry(headers, payload, ctx)

    async def _parse_streaming_response(self, response, ctx=None) -> str:
        content = ""
        full_body_buffer = []
        parse_errors = 0

        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue
            
            full_body_buffer.append(line)

            # 兼容 "data: {...}" 和 "data:{...}" 两种 SSE 格式
            if line.startswith("data:"):
                if line in ("data: [DONE]", "data:[DONE]"):
                    continue
                try:
                    # 去掉 "data:" 前缀，并去除可能的空格
                    json_str = line[5:].lstrip()
                    data = json.loads(json_str)
                    choices = data.get("choices", [])
                    if choices and len(choices) > 0:
                        delta = choices[0].get("delta", {})
                        if "content" in delta:
                            content += delta["content"]
                except (json.JSONDecodeError, IndexError):
                    parse_errors += 1
                    continue
                
        if not content and full_body_buffer:
            try:
                full_text = "".join(full_body_buffer)
                data = json.loads(full_text)
                if "choices" in data and len(data["choices"]) > 0:
                    message = data["choices"][0].get("message", {})
                    content = message.get("content", "")
            except json.JSONDecodeError:
                parse_errors += 1

        if not content:
            if full_body_buffer:
                await log_info(ctx, f"stream parse returned no content (parse_errors={parse_errors})", config.debug_enabled)
            else:
                await log_info(ctx, "stream parse returned empty streaming response", config.debug_enabled)

        await log_info(ctx, f"content: {content}", config.debug_enabled)

        return content

    async def _parse_responses_stream(self, response, ctx=None) -> str:
        content = ""
        full_body_buffer = []

        async for line in response.aiter_lines():
            line = line.strip()
            if not line:
                continue

            full_body_buffer.append(line)

            if line.startswith("data:"):
                if line in ("data: [DONE]", "data:[DONE]"):
                    continue
                try:
                    json_str = line[5:].lstrip()
                    data = json.loads(json_str)
                    t = data.get("type", "")
                    if t == "response.output_text.delta":
                        content += data.get("delta", "")
                except json.JSONDecodeError:
                    continue

        if not content and full_body_buffer:
            try:
                full_text = "".join(full_body_buffer)
                data = json.loads(full_text)
                content = self._extract_content_from_responses(data)
            except json.JSONDecodeError:
                pass

        if not content:
            if full_body_buffer:
                await log_info(ctx, "responses stream parse returned no content", config.debug_enabled)
            else:
                await log_info(ctx, "responses stream parse returned empty streaming response", config.debug_enabled)

        await log_info(ctx, f"responses content: {content}", config.debug_enabled)

        return content

    @staticmethod
    def _extract_content_from_responses(data: dict) -> str:
        output = data.get("output", [])
        texts: list[str] = []
        for item in output:
            if item.get("type") == "message":
                for cb in item.get("content", []):
                    if cb.get("type") == "output_text":
                        texts.append(cb.get("text", ""))
        return "".join(texts)

    async def _execute_stream_with_retry(self, headers: dict, payload: dict, ctx=None) -> str:
        """执行带重试机制的流式 HTTP 请求"""
        read_timeout = self.timeout or config.search_timeout_seconds
        timeout = httpx.Timeout(connect=6.0, read=read_timeout, write=10.0, pool=None)
        endpoint_url = f"{self.api_url}/{self._api_endpoint}"
        parse_fn = self._parse_responses_stream if self._use_responses_api else self._parse_streaming_response

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=config.ssl_verify_enabled) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(config.retry_max_attempts + 1),
                wait=_WaitWithRetryAfter(config.retry_multiplier, config.retry_max_wait),
                retry=retry_if_exception(_is_retryable_exception),
                reraise=True,
            ):
                with attempt:
                    async with client.stream(
                        "POST",
                        endpoint_url,
                        headers=headers,
                        json=payload,
                    ) as response:
                        if response.is_error:
                            await response.aread()
                        response.raise_for_status()
                        content = await parse_fn(response, ctx)
                        if content.strip():
                            return content

        await log_info(ctx, "streaming returned empty content, fallback to non-stream request", config.debug_enabled)
        return await self._execute_non_stream_with_retry(headers, payload, ctx)

    async def _execute_non_stream_with_retry(self, headers: dict, payload: dict, ctx=None) -> str:
        body = dict(payload)
        body["stream"] = False
        read_timeout = self.timeout or config.search_timeout_seconds
        timeout = httpx.Timeout(connect=6.0, read=read_timeout, write=10.0, pool=None)
        endpoint_url = f"{self.api_url}/{self._api_endpoint}"

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, verify=config.ssl_verify_enabled) as client:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(config.retry_max_attempts + 1),
                wait=_WaitWithRetryAfter(config.retry_multiplier, config.retry_max_wait),
                retry=retry_if_exception(_is_retryable_exception),
                reraise=True,
            ):
                with attempt:
                    response = await client.post(
                        endpoint_url,
                        headers=headers, json=body,
                    )
                    response.raise_for_status()
                    data = response.json()
                    if self._use_responses_api:
                        return self._extract_content_from_responses(data)
                    return self._extract_content_from_completion(data)

    @staticmethod
    def _extract_content_from_completion(data: dict) -> str:
        choices = data.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [item["text"] for item in content
                     if isinstance(item, dict) and item.get("type") == "text"]
            return "".join(parts)
        return ""

    async def describe_url(self, url: str, ctx=None) -> dict:
        """让 Grok 阅读单个 URL 并返回 title + extracts"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_chat_payload(
            system=url_describe_prompt,
            user=url,
        )
        result = await self._execute_stream_with_retry(headers, payload, ctx)
        title, extracts = url, ""
        for line in result.strip().splitlines():
            if line.startswith("Title:"):
                title = line[6:].strip() or url
            elif line.startswith("Extracts:"):
                extracts = line[9:].strip()
        return {"title": title, "extracts": extracts, "url": url}

    async def rank_sources(self, query: str, sources_text: str, total: int, ctx=None) -> list[int]:
        """让 Grok 按查询相关度对信源排序，返回排序后的序号列表"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = self._build_chat_payload(
            system=rank_sources_prompt,
            user=f"Query: {query}\n\n{sources_text}",
        )
        result = await self._execute_stream_with_retry(headers, payload, ctx)
        order: list[int] = []
        seen: set[int] = set()
        for token in result.strip().split():
            try:
                n = int(token)
                if 1 <= n <= total and n not in seen:
                    seen.add(n)
                    order.append(n)
            except ValueError:
                continue
        # 补齐遗漏的序号
        for i in range(1, total + 1):
            if i not in seen:
                order.append(i)
        return order
