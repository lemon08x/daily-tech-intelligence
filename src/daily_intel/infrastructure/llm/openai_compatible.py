from __future__ import annotations

import json
import os
import re
from typing import Any, TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from daily_intel.core.ports import LLMClient, LLMResult


T = TypeVar("T", bound=BaseModel)

_JSON_START = re.compile(r"[{\[]", re.S)


def extract_json_object(text: str) -> str:
    """从模型输出中宽容提取首个完整 JSON 对象。

    容忍:thinking 前缀、```json 围栏、围栏后拖尾文本、对象前导说明。
    返回第一个配对完整的 JSON 对象/数组文本;找不到则原样返回。
    """
    text = text.strip()
    if not text:
        return text
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I | re.S)
    match = _JSON_START.search(text)
    if not match:
        return text
    start = match.start()
    opener = text[start]
    closer = "]" if opener == "[" else "}"
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return text


class OpenAICompatibleLLM(LLMClient):
    def __init__(self, config: dict) -> None:
        self.config = config
        self.api_key = os.getenv(config["api_key_env"], "").strip()
        self._client = OpenAI(api_key=self.api_key, base_url=config["base_url"]) if self.api_key else None

    @property
    def available(self) -> bool:
        return self._client is not None

    def runtime_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.config.get("provider", "openai_compatible"),
            "base_url": self.config.get("base_url", ""),
            "api_key_env": self.config.get("api_key_env", ""),
            "configured_models": {
                stage: self.config.get(stage, {}).get("model", "")
                for stage in ("scout", "analyst", "verifier")
            },
            "usage_reporting": "reported",
        }

    def generate(self, stage: str, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        if self._client is None:
            raise RuntimeError(f"缺少环境变量 {self.config['api_key_env']}")
        stage_config = self.config[stage]
        extra_body = dict(stage_config.get("extra_body", {}))
        # 把 schema 描述补进 system,弥补端点不强制执行 response_format 的不足
        schema_hint = (
            f"\n输出必须是一个JSON对象，字段严格符合以下JSON Schema：\n"
            f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}\n"
            "不要输出Markdown围栏或解释文字。"
        )
        system_with_schema = system + schema_hint
        max_tokens = int(stage_config.get("max_output_tokens", 6000))
        last_error: str | None = None
        for attempt in range(2):
            messages = [{"role": "system", "content": system_with_schema}, {"role": "user", "content": user}]
            if last_error:
                messages.append({
                    "role": "user",
                    "content": f"你上一次的输出无法解析: {last_error}\n请重新输出严格符合schema的JSON对象。",
                })
            request = {
                "model": stage_config["model"],
                "messages": messages,
                "response_format": {"type": "json_object"},
                "max_tokens": max_tokens,
                "temperature": float(stage_config.get("temperature", 0)),
                "stream": False,
            }
            if extra_body:
                request["extra_body"] = extra_body
            response = self._client.chat.completions.create(**request)
            content = response.choices[0].message.content or ""
            try:
                candidate = extract_json_object(content)
                value = schema.model_validate(json.loads(candidate))
                usage = response.usage
                return LLMResult(
                    value=value, model=str(getattr(response, "model", "") or stage_config["model"]),
                    input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                    output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                )
            except (ValueError, ValidationError) as exc:
                last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
        raise RuntimeError(f"模型输出解析失败(重试2次): {last_error}")
