"""LLM 接入:langchain ChatOpenAI 指向 DeepSeek(OpenAI 兼容协议)。

complete() 结果走 Redis 缓存,键/TTL 与 NestJS 版一致:
llm:{model}:{messages JSON}:{temperature},TTL 300s。
"""

import json

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from python_backend.domain.tasks import ToolDefinition
from python_backend.infrastructure.cache import redis_cache
from python_backend.settings import settings

_CACHE_TTL = 300
_DEFAULT_TEMPERATURE = 0.7
_DEFAULT_MAX_TOKENS = 2000

_TYPE_MAP = {
    "string": "string",
    "number": "number",
    "boolean": "boolean",
    "object": "object",
    "array": "array",
}


def _openai_tools(tool_defs: list[ToolDefinition]) -> list[dict]:
    tools = []
    for d in tool_defs:
        parameters = {
            "type": "object",
            "properties": {p.name: {"type": _TYPE_MAP[p.type], "description": p.description} for p in d.parameters},
            "required": [p.name for p in d.parameters if p.required],
        }
        tools.append(
            {
                "type": "function",
                "function": {"name": d.name, "description": d.description, "parameters": parameters},
            }
        )
    return tools


def _client(temperature: float, max_tokens: int) -> ChatOpenAI:
    return ChatOpenAI(
        model=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_api_url.rstrip("/") + "/v1",
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=60.0,
    )


def _to_langchain(messages: list[dict]) -> list:
    """OpenAI 风格消息(role/content/tool_calls/tool_call_id)→ LangChain 消息。

    tool_calls 从 OpenAI 格式({id, type, function:{name, arguments}})转为
    LangChain 格式({name, args, id, type}),否则 AIMessage 构造会抛
    "tool_call() got an unexpected keyword argument 'function'"。
    """
    converted = []
    for msg in messages:
        role = msg["role"]
        content = msg.get("content") or ""
        if role == "system":
            converted.append(SystemMessage(content=content))
        elif role == "assistant":
            tool_calls = None
            if msg.get("tool_calls"):
                tool_calls = [
                    {
                        "name": tc["function"]["name"],
                        "args": json.loads(tc["function"]["arguments"] or "{}"),
                        "id": tc["id"],
                        "type": "function",
                    }
                    for tc in msg["tool_calls"]
                ]
            converted.append(AIMessage(content=content, tool_calls=tool_calls))
        elif role == "tool":
            converted.append(ToolMessage(content=content, tool_call_id=msg.get("tool_call_id")))
        else:
            converted.append(HumanMessage(content=content))
    return converted


class LlmService:
    def complete(
        self,
        messages: list[dict],
        *,
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
        json_mode: bool = False,
    ) -> str:
        cache_key = f"llm:{settings.llm_model}:{json.dumps(messages, ensure_ascii=False)}:{temperature}"
        cached = redis_cache.get_json(cache_key)
        if cached is not None:
            return cached["content"]

        client = _client(temperature, max_tokens)
        response_format = {"type": "json_object"} if json_mode else None
        kwargs = {"response_format": response_format} if response_format else {}
        result: AIMessage = client.invoke(_to_langchain(messages), **kwargs)

        content = result.content if isinstance(result.content, str) else json.dumps(result.content, ensure_ascii=False)
        redis_cache.set_json(cache_key, {"content": content}, ttl=_CACHE_TTL)
        return content

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[ToolDefinition],
        *,
        temperature: float = _DEFAULT_TEMPERATURE,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> AIMessage:
        """返回原始 AIMessage(含 tool_calls),由 ReAct 循环(状态图)消费。"""
        client = _client(temperature, max_tokens).bind_tools(_openai_tools(tools), tool_choice="auto")
        return client.invoke(_to_langchain(messages))
