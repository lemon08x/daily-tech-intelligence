from __future__ import annotations

import json
import os
import re
from typing import Any, TypeVar

from openai import OpenAI, APIConnectionError, APIStatusError, APITimeoutError
from pydantic import BaseModel, ValidationError

from daily_intel.core.ports import LLMClient, LLMResult


T = TypeVar("T", bound=BaseModel)

class ModelResponseError(RuntimeError):
    def __init__(self, category: str, *, model: str = "", input_tokens: int = 0,
                 output_tokens: int = 0, usage_unknown: bool = True, finish_reason: str = "",
                 response_content: str = "", reasoning_content: str = "", validation_errors: str = "",
                 request_parameters: dict | None = None):
        super().__init__(category)
        self.category = category
        self.model = model
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.usage_unknown = usage_unknown
        self.finish_reason = finish_reason
        self.response_content = response_content
        self.reasoning_content = reasoning_content
        self.validation_errors = validation_errors
        self.request_parameters = request_parameters or {}


def json_objects(text: str):
    """Scan complete top-level values, skipping arrays and nested subobjects."""
    decoder = json.JSONDecoder()
    cursor = 0
    while cursor < len(text):
        match = re.search(r"[\[{]", text[cursor:])
        if not match:
            return
        start = cursor + match.start()
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            # A malformed object must never be salvaged from its nested fields.
            if text[start] == "{":
                return
            cursor = start + 1
            continue
        if isinstance(value, dict):
            yield text[start:end]
        cursor = end


def extract_json_object(text: str) -> str:
    return next(json_objects(text.strip()), text.strip())


class OpenAICompatibleLLM(LLMClient):
    def __init__(self, config: dict) -> None:
        self.config = config
        self.api_key = os.getenv(config["api_key_env"], "").strip()
        timeout_seconds = float(config.get("request_timeout_seconds", 1200))
        self._client = (
            OpenAI(api_key=self.api_key, base_url=config["base_url"], timeout=timeout_seconds, max_retries=0)
            if self.api_key else None
        )

    @property
    def available(self) -> bool:
        return self._client is not None

    def runtime_metadata(self) -> dict[str, Any]:
        return {
            "provider": self.config.get("provider", "openai_compatible"),
            "base_url": self.config.get("base_url", ""),
            "api_key_env": self.config.get("api_key_env", ""),
            "configured_models": {
                stage: self.config.get(stage, self.config.get("scout", {})).get("model", "")
                for stage in ("scout", "analyst", "verifier", "digest_brief", "git_brief")
            },
            "usage_reporting": "reported",
        }

    def retry_generate(self, stage: str, system: str, user: str, schema: type[T], error: Exception) -> LLMResult[T]:
        config = self.config.get(stage) or self.config["scout"]
        budget = None
        if getattr(error, "category", "") == "truncated_response":
            budget = int(config.get("retry_max_output_tokens") or max(
                int(config.get("max_output_tokens") or 32768) * 2,
                int(getattr(error, "output_tokens", 0)) * 2,
            ))
        elif getattr(error, "category", "") in {"schema_mismatch", "json_syntax", "empty_response"}:
            system += "\n上次输出未通过校验，请修正并返回完整 JSON。校验信息：" + str(
                getattr(error, "validation_errors", "") or error
            )[:3000]
        return self.generate(stage, system, user, schema, output_budget=budget)

    def generate(self, stage: str, system: str, user: str, schema: type[T], *, output_budget: int | None = None) -> LLMResult[T]:
        if self._client is None:
            raise RuntimeError(f"缺少环境变量 {self.config['api_key_env']}")
        stage_config = self.config.get(stage) or self.config["scout"]
        extra_body = dict(stage_config.get("extra_body", {}))
        schema_hint = (
            "\n输出一个严格符合以下 JSON Schema 的对象，不要解释文字：\n"
            + json.dumps(schema.model_json_schema(), ensure_ascii=False)
        )
        # Stage prompts already carry the schema; avoid doubling the large contract.
        system_with_schema = system if '"$defs"' in user or '"properties"' in user else system + schema_hint
        request = {
            "model": stage_config["model"],
            "messages": [{"role": "system", "content": system_with_schema}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
            "temperature": float(stage_config.get("temperature", 0)),
            "stream": False,
        }
        if extra_body:
            request["extra_body"] = extra_body
        budget = output_budget or stage_config.get("max_output_tokens")
        if budget is not None:
            if int(budget) <= 0:
                raise ValueError("max_output_tokens must be positive")
            request["max_tokens"] = int(budget)
        try:
            response = self._client.chat.completions.create(**request)
        except APITimeoutError as exc:
            raise ModelResponseError("network_timeout", model=stage_config["model"], request_parameters=request) from exc
        except APIConnectionError as exc:
            raise ModelResponseError("network_connection", model=stage_config["model"], request_parameters=request) from exc
        except APIStatusError as exc:
            raise ModelResponseError(f"http_status_{exc.status_code}", model=stage_config["model"], request_parameters=request) from exc
        usage = response.usage
        metadata = dict(model=str(getattr(response, "model", "") or stage_config["model"]),
                        input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                        output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                        usage_unknown=usage is None, request_parameters=request)
        if not response.choices:
            raise ModelResponseError("empty_response", **metadata)
        choice = response.choices[0]
        reason = str(getattr(choice, "finish_reason", "") or "")
        content = choice.message.content or ""
        metadata.update(response_content=content,
                        reasoning_content=str(getattr(choice.message, "reasoning_content", "") or ""))
        if reason == "length":
            raise ModelResponseError("truncated_response", finish_reason=reason, **metadata)
        if not content.strip():
            raise ModelResponseError("empty_response", finish_reason=reason, **metadata)
        candidates = list(json_objects(content))
        category = "json_syntax" if not candidates else "schema_mismatch"
        if not candidates:
            try:
                json.loads(content)
            except ValueError:
                pass
            else:
                category = "schema_mismatch"
        for candidate in candidates:
            try:
                value = schema.model_validate(json.loads(candidate))
            except (ValueError, ValidationError) as exc:
                metadata["validation_errors"] = (json.dumps(exc.errors(include_input=False, include_url=False),
                    ensure_ascii=False, default=str) if isinstance(exc, ValidationError) else str(exc))
                continue
            result = LLMResult(value=value, model=metadata["model"],
                               input_tokens=metadata["input_tokens"], output_tokens=metadata["output_tokens"],
                               usage_estimated=metadata["usage_unknown"])
            result.finish_reason = reason
            result.usage_unknown = metadata["usage_unknown"]
            result.request_parameters = request
            return result
        raise ModelResponseError(category, finish_reason=reason, **metadata)
