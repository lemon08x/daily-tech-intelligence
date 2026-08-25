"""以 Qwen Code 代理作为 AI 分析模型的日报驱动脚本。

流水线每次调用 LLM 时，把 system/user 提示词写入 llm_io/NN_<stage>.request.json，
然后阻塞等待代理把符合 schema 的 JSON 写入同编号的 .response.json。
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from daily_intel.app.orchestrator import run_application
from daily_intel.core.ports import LLMClient, LLMResult
from daily_intel.core.settings import load_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent
IO_DIR = PROJECT_ROOT / "llm_io"
MODEL_NAME = "qwen-code-agent"
CALL_TIMEOUT_SECONDS = 3600


class AgentLLMClient(LLMClient):
    def __init__(self, config: dict) -> None:
        self.config = config
        self._seq = 0

    @property
    def available(self) -> bool:
        return True

    def generate(self, stage: str, system: str, user: str, schema):
        self._seq += 1
        name = f"{self._seq:02d}_{stage}"
        request_path = IO_DIR / f"{name}.request.json"
        response_path = IO_DIR / f"{name}.response.json"
        request_path.write_text(
            json.dumps(
                {
                    "stage": stage,
                    "model_name": MODEL_NAME,
                    "system": system,
                    "user": user,
                    "schema": schema.__name__,
                    "response_file": response_path.name,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[agent-llm] 等待代理响应: {request_path.name} -> {response_path.name}", flush=True)
        deadline = time.time() + CALL_TIMEOUT_SECONDS
        while not response_path.exists():
            if time.time() > deadline:
                raise TimeoutError(f"等待 {response_path.name} 超时（{CALL_TIMEOUT_SECONDS}s）")
            time.sleep(2)
        raw = response_path.read_text(encoding="utf-8")
        payload = json.loads(raw)
        value = schema.model_validate(payload)
        return LLMResult(
            value=value,
            model=MODEL_NAME,
            input_tokens=max(1, len(user) // 3),
            output_tokens=max(1, len(raw) // 3),
        )


def main() -> int:
    IO_DIR.mkdir(exist_ok=True)
    settings = load_settings(PROJECT_ROOT / "config" / "settings.yaml")
    outputs = run_application(settings, llm=AgentLLMClient(settings["llm"]))
    print("生成完成：")
    for kind, path in outputs.items():
        print(f"  {kind}: {path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
