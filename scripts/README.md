# Scripts

Root launchers (ASCII-safe `.cmd`, so Windows `cmd.exe` does not choke on UTF-8 Chinese):

- `启动日报.cmd`: LAN DeepSeek V4 Abliterated via `config/settings.deepseek.yaml`.
- `启动日报-qwen.cmd`: optional standalone Qwen 3.8-27B check via `config/settings.qwen.yaml` and `data/intelligence_qwen.db`; it is separate from the scheduled GPT-6.1 Sol production database.

Both print a start banner (backend, config, experiment id) and then Python status lines (`[1/6] …` plus `当前：…`). Live output is also written to `logs/latest.log`.

- `setup.ps1`: create/update the local virtual environment.
- `run_daily.ps1`: shared production entry. Default config is DeepSeek Flash. Pass `-Config` / `-ExperimentId` for Qwen. Also supports `-RequireAI`, `-NoAI`, `-Offline`, `-Open`, `-ForceAnalysis`.
- `run_daily_agent.ps1`: scheduled GPT-6.1 Sol broad reading, top-10 research, and email via `config/settings.daily-gpt.yaml` and `QWEN_LAN_API_KEY`.
- `install_agent_task.ps1`: install or update the daily (including weekend) 01:00 scheduler with a 48-hour execution limit.
- `send_report.py`: email today's latest successful HTML digest.
- `refresh_weekly_catalog.py`: rebuild the optional weekly-blog RSS catalog cache; not part of the daily run.
- `diagnostics/*.py`: read-only database and event inspection helpers.

Business logic belongs in `src/daily_intel`; scripts are entry points and
diagnostic adapters only.
