"""冒烟测试:验证双模型各自的 analyst→verifier 闭环能否跑通。

通过 --config 传入配置,临时把 max_deep_events 压到 1 以缩短时间。
用法: python scripts/smoke_dual.py <settings.yaml>
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from daily_intel.app.orchestrator import run_application
from daily_intel.core.settings import load_settings

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    config_path = Path(sys.argv[1]).resolve()
    settings = load_settings(config_path)
    # 压小规模,只深研 1 条
    settings["intelligence"]["max_deep_events"] = 1
    settings["intelligence"]["max_scout_events"] = 8
    started = time.time()
    print(f"[smoke] config={config_path.name} model="
          f"{settings['llm']['analyst']['model']}", flush=True)
    outputs = run_application(settings)
    print(f"[smoke] OK 耗时={time.time()-started:.0f}s 输出={outputs['intelligence']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())