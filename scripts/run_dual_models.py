"""双模型对比跑: OpenCode 中的 DeepSeek V4 Flash 与 Qwen3.8 27B。

用法:
  python scripts/run_dual_models.py            # 串行跑两个模型
  python scripts/run_dual_models.py --parallel # 并行跑两个模型(独立DB/独立输出)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
OPENCODE_CONFIG = Path.home() / ".config" / "opencode" / "opencode.json"

RUNS = [
    {
        "tag": "deepseek",
        "label": "DeepSeek V4 Flash (deepseek-v4-flash-0731)",
        "config": PROJECT_ROOT / "config" / "settings.deepseek.yaml",
        "experiment_id": "deepseek-v4-flash",
    },
    {
        "tag": "qwen",
        "label": "Qwen3.8 27B (qwen3.8-27b)",
        "config": PROJECT_ROOT / "config" / "settings.qwen.yaml",
        "experiment_id": "qwen3.8-27b",
    },
]


def load_dotenv() -> None:
    path = PROJECT_ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def load_opencode_keys() -> None:
    if not OPENCODE_CONFIG.exists():
        return
    data = json.loads(OPENCODE_CONFIG.read_text(encoding="utf-8"))
    options = data.get("provider", {}).get("qwen-lan", {}).get("options", {})
    key = str(options.get("apiKey") or "")
    if key.startswith("{env:") and key.endswith("}"):
        key = os.getenv(key[5:-1], "")
    if key:
        os.environ.setdefault("QWEN_LAN_API_KEY", key)


def run_one(run: dict) -> dict:
    tag = run["tag"]
    log_path = PROJECT_ROOT / "logs" / f"run_{tag}.log"
    log_path.parent.mkdir(exist_ok=True)
    started = time.time()
    print(f"[{tag}] 开始: {run['label']}", flush=True)
    cmd = [
        str(PYTHON), "-m", "daily_intel", "run",
        "--config", str(run["config"]),
        "--require-ai",
        "--force-analysis",
        "--experiment-id", run["experiment_id"],
    ]
    with log_path.open("w", encoding="utf-8") as log:
        proc = subprocess.run(
            cmd, cwd=PROJECT_ROOT, stdout=log, stderr=subprocess.STDOUT, text=True,
            env=os.environ.copy(),
        )
    elapsed = time.time() - started
    ok = proc.returncode == 0
    print(f"[{tag}] 结束: exit={proc.returncode} 耗时={elapsed:.0f}s", flush=True)
    return {
        "tag": tag, "label": run["label"], "exit": proc.returncode,
        "elapsed_s": elapsed, "ok": ok, "log": str(log_path),
        "experiment_id": run["experiment_id"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parallel", action="store_true", help="并行跑两个模型")
    parser.add_argument("--only", choices=["deepseek", "qwen"], help="只跑指定模型")
    args = parser.parse_args()
    load_dotenv()
    load_opencode_keys()
    selected = [r for r in RUNS if not args.only or r["tag"] == args.only]

    results = []
    if args.parallel and len(selected) > 1:
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(run_one, run): run for run in selected}
            for future in concurrent.futures.as_completed(futures):
                results.append(future.result())
    else:
        for run in selected:
            results.append(run_one(run))

    summary = PROJECT_ROOT / "logs" / "dual_run_summary.json"
    summary.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== 汇总 ===")
    for r in results:
        print(f"  [{r['tag']}] {r['label']}: exit={r['exit']} 耗时={r['elapsed_s']:.0f}s log={r['log']}")
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())