# Scripts

- `setup.ps1`: create/update the local virtual environment.
- `run_daily.ps1`: deterministic API/no-AI/offline production entry.
- `run_daily_agent.ps1`: scheduled DeepSeek V4 Flash run plus email.
- `install_agent_task.ps1`: install or update the daily (including weekend) 08:30 scheduler.
- `install_scheduled_task.ps1`: install the optional weekday 18:10 scheduler.
- `send_report.py`: email today's latest successful digest.
- `harness/run.py`: use a Harness model through the audited file bridge.
- `harness/*.py`: inspect requests and validate exact quotes.
- `diagnostics/*.py`: read-only database and event inspection helpers.

Business logic belongs in `src/daily_intel`; scripts are entry points and
diagnostic adapters only.
