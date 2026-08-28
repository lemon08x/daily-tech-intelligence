# Scripts

Root launchers (ASCII-safe `.cmd`, so Windows `cmd.exe` does not choke on UTF-8 Chinese):

- `启动日报.cmd`: LAN DeepSeek V4 Flash via `config/settings.deepseek.yaml`.
- `启动日报-qwen.cmd`: LAN Qwen 3.8-27B via `config/settings.qwen.yaml` and `data/intelligence_qwen.db`. Use this for a faster local check; it does not share analysis cache with DeepSeek.

Both print a start banner (backend, config, experiment id) and then Python status lines (`[1/6] …` plus `当前：…`). Live output is also written to `logs/latest.log`.

- `setup.ps1`: create/update the local virtual environment.
- `run_daily.ps1`: shared production entry. Default config is DeepSeek Flash. Pass `-Config` / `-ExperimentId` for Qwen. Also supports `-RequireAI`, `-NoAI`, `-Offline`, `-Open`, `-ForceAnalysis`.
- `run_daily_agent.ps1`: scheduled DeepSeek V4 Flash run plus email.
- `install_agent_task.ps1`: install or update the daily (including weekend) 08:30 scheduler.
- `install_scheduled_task.ps1`: install the optional weekday 18:10 scheduler.
- `send_report.py`: email today's latest successful digest.
- `refresh_weekly_catalog.py`: rebuild the optional weekly-blog RSS catalog cache; not part of the daily run.
- `harness/run.py`: use a Harness model through the audited file bridge.
- `harness/*.py`: inspect requests and validate exact quotes.
- `diagnostics/*.py`: read-only database and event inspection helpers.

Business logic belongs in `src/daily_intel`; scripts are entry points and
diagnostic adapters only.
