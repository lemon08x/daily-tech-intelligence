# Scripts

Root launchers (ASCII-safe `.cmd`, so Windows `cmd.exe` does not choke on UTF-8 Chinese):

- `启动日报.cmd`: shared manual/scheduled launcher, defaulting to GPT-6 Luna via `config/settings.daily-gpt.yaml`. The scheduled task runs this exact launcher with `--scheduled`.
- `启动日报-qwen.cmd`: optional standalone Qwen 3.8-27B check via `config/settings.qwen.yaml` and `data/intelligence_qwen.db`; it is separate from the scheduled GPT-6 Luna production database.

Both print a start banner (backend, config, experiment id) and then Python status lines (`[1/6] …` plus `当前：…`). Manual output is written to `logs/latest.log`; scheduled runs use unique `logs/agent_<timestamp>.log` files and email the result of that invocation. `--scheduled` suppresses browser opening and failure pauses; `--no-pause` suppresses pauses for other unattended invocations.

- `setup.ps1`: create/update the local virtual environment.
- `run_daily.ps1`: shared production entry. Default config is `settings.daily-gpt.yaml`; experiment identity and model label come from the selected config. Pass `-Config` for optional DeepSeek/Qwen runs, or override `-ExperimentId` for an explicit experiment. Also supports `-RequireAI`, `-NoAI`, `-Offline`, `-Open`, `-ForceAnalysis`, and `-Scheduled` (per-run logging and email).
- `run_daily_agent.ps1`: compatibility forwarder for old task registrations; calls `启动日报.cmd --scheduled`, without its own model or generation settings.
- `install_agent_task.ps1`: install or update the daily (including weekend) 01:00 scheduler with a 48-hour execution limit.
- `send_report.py`: email today's latest successful HTML digest.
- `refresh_weekly_catalog.py`: rebuild the optional weekly-blog RSS catalog cache; not part of the daily run.
- `diagnostics/*.py`: read-only database and event inspection helpers.

Business logic belongs in `src/daily_intel`; scripts are entry points and
diagnostic adapters only.
