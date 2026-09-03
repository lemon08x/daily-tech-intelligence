from __future__ import annotations

import argparse
import importlib.metadata
import os
import sys
import webbrowser
from pathlib import Path

from daily_intel.app.orchestrator import run_application
from daily_intel.core.settings import load_settings, resolve_path
from daily_intel.intelligence.sources.factory import configured_source_count


DEFAULT_CONFIG = Path("config/settings.yaml")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="生成科技产业情报日报")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="采集数据并生成统一日报")
    run.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    run.add_argument("--offline", action="store_true", help="只使用市场缓存及已存情报/分析")
    run.add_argument("--open", action="store_true", help="生成后打开HTML报告")
    ai_group = run.add_mutually_exclusive_group()
    ai_group.add_argument("--no-ai", action="store_true", help="采集科技信息但不调用模型")
    ai_group.add_argument("--require-ai", action="store_true", help="缺少AI密钥时返回失败")
    run.add_argument(
        "--experiment-id", default="default",
        help="本次模型/实验标识，用于隔离缓存和区分同日多份输出",
    )
    run.add_argument(
        "--force-analysis", action="store_true",
        help="忽略当前实验的分析缓存并重新调用模型",
    )
    doctor = subparsers.add_parser("doctor", help="检查依赖、配置、缓存与AI密钥状态")
    doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "未安装"


def run_doctor(config_path: Path) -> int:
    settings = load_settings(config_path)
    print(f"配置: {settings['_config_path']}")
    print(f"Python: {sys.version.split()[0]}")
    for distribution in (
        "akshare", "pandas", "Jinja2", "PyYAML", "feedparser", "openai",
        "pydantic", "pypdf", "rapidfuzz", "trafilatura",
    ):
        print(f"{distribution}: {_version(distribution)}")
    source_count = configured_source_count(settings["sources"])
    key_env = settings["llm"]["api_key_env"]
    print(f"科技来源: {source_count} 个已启用")
    print(f"AI密钥 {key_env}: {'已设置' if os.getenv(key_env, '').strip() else '未设置（将生成线索版日报）'}")
    print(f"市场缓存: {resolve_path(settings, 'cache_dir')}")
    print(f"情报数据库: {resolve_path(settings, 'intelligence_db')}")
    print("检查通过。网络来源会在 run 时逐个验证并独立降级。")
    return 0


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="replace")
        except (OSError, ValueError, AttributeError):
            continue


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return run_doctor(args.config)
        settings = load_settings(args.config)
        outputs = run_application(
            settings, offline=args.offline, no_ai=args.no_ai, require_ai=args.require_ai,
            experiment_id=args.experiment_id, force_analysis=args.force_analysis,
        )
        print("生成完成：")
        for kind, path in outputs.items():
            print(f"  {kind}: {path.resolve()}")
        if args.open:
            webbrowser.open(outputs["html"].resolve().as_uri())
        return 0
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        if isinstance(exc, UnicodeError):
            raise
        print(f"失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
