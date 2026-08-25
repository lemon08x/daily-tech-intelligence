# Scripts

- `setup.ps1`: create/update the local virtual environment.
- `run_daily.ps1`: deterministic API/no-AI/offline production entry.
- `install_scheduled_task.ps1`: install the weekday scheduler.
- `harness/run.py`: use a Harness model through the audited file bridge.
- `harness/*.py`: inspect requests and validate exact quotes.
- `diagnostics/*.py`: read-only database and event inspection helpers.

Business logic belongs in `src/daily_intel`; scripts are entry points and
diagnostic adapters only.
