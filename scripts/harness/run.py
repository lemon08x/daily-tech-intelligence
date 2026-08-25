"""Run the daily pipeline with a Harness model through an audited file bridge.

Each model request and response is stored beside its unique daily report under
output/YYYY-MM-DD/runs/<run-name>/harness_io.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_intel.app.orchestrator import run_application
from daily_intel.core.ports import LLMClient, LLMResult
from daily_intel.core.runs import sanitize_run_identifier
from daily_intel.core.settings import load_settings, resolve_path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class AgentLLMClient(LLMClient):
    def __init__(
        self, harness_name: str, model_name: str, io_dir: Path, experiment_id: str,
        timeout_seconds: int,
    ) -> None:
        self.harness_name = harness_name
        self.model_name = model_name
        self.io_dir = io_dir
        self.experiment_id = experiment_id
        self.timeout_seconds = timeout_seconds
        self._seq = 0

    @property
    def available(self) -> bool:
        return True

    def runtime_metadata(self) -> dict:
        return {
            "provider": self.harness_name,
            "transport": "local-file-bridge",
            "configured_models": {
                "scout": self.model_name,
                "analyst": self.model_name,
                "verifier": self.model_name,
            },
            "usage_reporting": "estimated",
            "harness_io": self.io_dir.relative_to(PROJECT_ROOT).as_posix(),
        }

    def generate(self, stage: str, system: str, user: str, schema):
        self._seq += 1
        request_id = f"{self._seq:02d}_{stage}"
        request_path = self.io_dir / f"{request_id}.request.json"
        response_path = self.io_dir / f"{request_id}.response.json"
        request_path.write_text(
            json.dumps(
                {
                    "request_id": request_id,
                    "stage": stage,
                    "harness_name": self.harness_name,
                    "experiment_id": self.experiment_id,
                    "model_name": self.model_name,
                    "system": system,
                    "user": user,
                    "schema": schema.__name__,
                    "json_schema": schema.model_json_schema(),
                    "response_file": response_path.name,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(
            f"[harness] 等待响应: {request_path} -> {response_path.name}",
            flush=True,
        )
        deadline = time.time() + self.timeout_seconds
        while not response_path.exists():
            if time.time() > deadline:
                raise TimeoutError(
                    f"等待 {response_path.name} 超时（{self.timeout_seconds}s）"
                )
            time.sleep(2)
        raw = response_path.read_text(encoding="utf-8")
        value = schema.model_validate(json.loads(raw))
        return LLMResult(
            value=value,
            model=self.model_name,
            input_tokens=max(1, len(system + user) // 3),
            output_tokens=max(1, len(raw) // 3),
            usage_estimated=True,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="使用Harness模型生成独立归档的每日日报")
    parser.add_argument(
        "--config", type=Path, default=PROJECT_ROOT / "config" / "settings.yaml"
    )
    parser.add_argument(
        "--harness-name",
        default=os.getenv("HARNESS_NAME", "qwen-code"),
        help="实际驱动本次文件桥接的Harness名称",
    )
    parser.add_argument(
        "--model-name",
        default=os.getenv("HARNESS_MODEL_NAME", "qwen3.8-27b"),
        help="写入分析和运行审计的实际模型名称",
    )
    parser.add_argument(
        "--experiment-id",
        help="缓存和输出实验标识；默认使用model-name",
    )
    parser.add_argument(
        "--run-name",
        help="本次输出目录名；默认使用时间和实验标识生成",
    )
    parser.add_argument(
        "--force-analysis", action="store_true",
        help="忽略当前模型实验的分析缓存",
    )
    parser.add_argument(
        "--timeout-seconds", type=int, default=3600,
        help="每个Harness响应文件的最长等待时间",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = load_settings(args.config)
    experiment_id = sanitize_run_identifier(
        args.experiment_id or args.model_name
    )
    timezone = ZoneInfo(settings["app"]["timezone"])
    now = datetime.now(timezone)
    run_name = sanitize_run_identifier(
        args.run_name
        or f"{now:%H%M%S}-{now.microsecond // 1000:03d}-{experiment_id}"
    )
    run_dir = (
        resolve_path(settings, "output_dir")
        / now.strftime("%Y-%m-%d")
        / "runs"
        / run_name
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    io_dir = run_dir / "harness_io"
    io_dir.mkdir()
    client = AgentLLMClient(
        args.harness_name, args.model_name, io_dir, experiment_id,
        args.timeout_seconds,
    )
    outputs = run_application(
        settings,
        now=now,
        llm=client,
        experiment_id=experiment_id,
        force_analysis=args.force_analysis,
        run_name=run_name,
    )
    print("生成完成：")
    for kind, path in outputs.items():
        print(f"  {kind}: {path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
