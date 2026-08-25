from __future__ import annotations

import json
import os
import re
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from daily_intel.core.ports import LLMClient, LLMResult


T = TypeVar("T", bound=BaseModel)


class OpenAICompatibleLLM(LLMClient):
    def __init__(self, config: dict) -> None:
        self.config = config
        self.api_key = os.getenv(config["api_key_env"], "").strip()
        self._client = OpenAI(api_key=self.api_key, base_url=config["base_url"]) if self.api_key else None

    @property
    def available(self) -> bool:
        return self._client is not None

    def generate(self, stage: str, system: str, user: str, schema: type[T]) -> LLMResult[T]:
        if self._client is None:
            raise RuntimeError(f"缺少环境变量 {self.config['api_key_env']}")
        stage_config = self.config[stage]
        extra_body = {
            "thinking": {"type": "enabled" if stage_config.get("thinking", False) else "disabled"},
            "reasoning_effort": stage_config.get("reasoning_effort", "low"),
        }
        response = self._client.chat.completions.create(
            model=stage_config["model"],
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            response_format={"type": "json_object"},
            max_tokens=int(stage_config["max_output_tokens"]),
            temperature=0,
            stream=False,
            extra_body=extra_body,
        )
        content = response.choices[0].message.content or ""
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip(), flags=re.I | re.S)
        value = schema.model_validate(json.loads(content))
        usage = response.usage
        return LLMResult(
            value=value, model=stage_config["model"],
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
        )
