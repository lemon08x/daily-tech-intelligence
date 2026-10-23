import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher integration")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def launcher_project(tmp_path):
    root = tmp_path / "日报 project"
    (root / "scripts").mkdir(parents=True)
    (root / ".venv/Scripts").mkdir(parents=True)
    (root / ".venv/Scripts/python.exe").touch()
    shutil.copyfile(ROOT / "启动日报.cmd", root / "启动日报.cmd")
    for name in ("run_daily.ps1", "run_daily_agent.ps1", "install_agent_task.ps1"):
        source = ROOT / "scripts" / name
        encoding = "utf-8-sig" if source.read_bytes().startswith(b"\xef\xbb\xbf") else "utf-8"
        script = source.read_text(encoding=encoding)
        # Only substitute the external executable; exercise the real entrypoint logic.
        script = script.replace('$Python = Join-Path $ProjectRoot ".venv\\Scripts\\python.exe"',
                                "$Python = '" + sys.executable.replace("'", "''") + "'")
        (root / "scripts" / name).write_text(script, encoding=encoding)
    shutil.copytree(ROOT / "config", root / "config")
    package = root / "daily_intel"
    (package / "core").mkdir(parents=True)
    (package / "__init__.py").touch()
    (package / "core/__init__.py").touch()
    (package / "core/settings.py").write_text(
        "import yaml\ndef load_settings(path):\n    return yaml.safe_load(path.read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    (package / "__main__.py").write_text(
        "import json,sys,os\nfrom pathlib import Path\n"
        "Path('run-args.json').write_text(json.dumps(sys.argv[1:]),encoding='utf-8')\n"
        "print('html: fixture/daily_digest.html',flush=True)\n"
        "sys.exit(int(os.environ.get('TEST_RUN_EXIT','0')))\n", encoding="utf-8",
    )
    (root / "scripts/send_report.py").write_text(
        "import json,sys,os\nfrom pathlib import Path\n"
        "log=Path(sys.argv[sys.argv.index('--run-log')+1])\n"
        "Path('mail-args.json').write_text(json.dumps({'args':sys.argv[1:],'log':log.read_text(encoding='utf-8')}),encoding='utf-8')\n"
        "sys.exit(int(os.environ.get('TEST_SEND_EXIT','0')))\n", encoding="utf-8",
    )
    return root


def invoke(root, args, **overrides):
    env = {**os.environ, **overrides}
    return subprocess.run(
        f'"{os.environ["COMSPEC"]}" /d /s /c ""{root / "启动日报.cmd"}" {args}"',
        cwd=root.parent, env=env, stdin=subprocess.DEVNULL,
        capture_output=True, timeout=30,
    )


def test_manual_and_scheduled_share_config_and_experiment(launcher_project):
    root = launcher_project
    manual = invoke(root, "--no-pause")
    assert manual.returncode == 0, manual.stdout + manual.stderr
    manual_args = json.loads((root / "run-args.json").read_text())
    assert "--open" in manual_args
    assert not (root / "mail-args.json").exists()
    scheduled = invoke(root, "--scheduled")
    assert scheduled.returncode == 0, scheduled.stdout + scheduled.stderr
    scheduled_args = json.loads((root / "run-args.json").read_text())
    assert scheduled_args == [arg for arg in manual_args if arg != "--open"]
    assert "--require-ai" in scheduled_args
    assert scheduled_args[scheduled_args.index("--config") + 1] == str(root / "config/settings.daily-gpt.yaml")
    assert scheduled_args[scheduled_args.index("--experiment-id") + 1] == "gpt-6-luna-daily"
    mail = json.loads((root / "mail-args.json").read_text())
    assert "html: fixture/daily_digest.html" in mail["log"]
    assert Path(mail["args"][1]).name.startswith("agent_")
    assert mail["args"][mail["args"].index("--run-exit-code") + 1] == "0"


@pytest.mark.parametrize("run_exit,send_exit,expected", [(7, 0, 7), (0, 9, 9), (7, 9, 7)])
def test_scheduled_failures_return_without_pause(launcher_project, run_exit, send_exit, expected):
    result = invoke(launcher_project, "--scheduled", TEST_RUN_EXIT=str(run_exit), TEST_SEND_EXIT=str(send_exit))
    assert result.returncode == expected, result.stdout + result.stderr
    mail = json.loads((launcher_project / "mail-args.json").read_text())
    assert mail["args"][mail["args"].index("--run-exit-code") + 1] == str(run_exit)


def test_manual_override_and_failure_code_survive_launcher(launcher_project):
    result = invoke(launcher_project, "--no-pause --no-open --force-analysis --experiment-id review-run", TEST_RUN_EXIT="6")
    assert result.returncode == 6, result.stdout + result.stderr
    args = json.loads((launcher_project / "run-args.json").read_text())
    assert "--open" not in args
    assert "--force-analysis" in args
    assert args[args.index("--experiment-id") + 1] == "review-run"
    assert not (launcher_project / "mail-args.json").exists()


def test_installer_action_launches_real_cmd_in_scheduled_mode(launcher_project):
    root = launcher_project
    harness = root / "capture-task.ps1"
    harness.write_text('''
function New-ScheduledTaskAction { param($Execute,$Argument,$WorkingDirectory)
    @{Execute=$Execute;Argument=$Argument;WorkingDirectory=$WorkingDirectory} | ConvertTo-Json | Set-Content -Encoding UTF8 action.json
}
function New-ScheduledTaskTrigger { param($Daily,$DaysInterval,$At) }
function New-ScheduledTaskSettingsSet { param([switch]$AllowStartIfOnBatteries,[switch]$DontStopIfGoingOnBatteries,[switch]$StartWhenAvailable,$MultipleInstances,$ExecutionTimeLimit) }
function Get-ScheduledTask { param($TaskName,$ErrorAction) }
function Register-ScheduledTask { param($TaskName,$Action,$Trigger,$Settings,$Description) }
& "$PSScriptRoot/scripts/install_agent_task.ps1"
'''.replace('param($Daily,', 'param([switch]$Daily,'), encoding="utf-8-sig")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)], cwd=root, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    action = json.loads((root / "action.json").read_text(encoding="utf-8-sig"))
    assert action["WorkingDirectory"] == str(root)
    result = subprocess.run(f'"{action["Execute"]}" {action["Argument"]}', cwd=root, stdin=subprocess.DEVNULL, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / "run-args.json").exists()
    assert (root / "mail-args.json").exists()
