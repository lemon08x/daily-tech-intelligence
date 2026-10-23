# Application operating contract

This repository is a runnable daily-intelligence application, not a Skill.
LLM backends must use the existing application contracts instead of
reimplementing collection, analysis, or publication.

## Interpret common requests

- “生成今日日报”: run the application only. Do not edit source code, configs,
  prompts, tests, or historical outputs.
- “审查/比较日报”: read existing run artifacts and report findings. Do not call
  models or modify code unless explicitly asked.
- “修复/优化项目”: code changes are allowed; preserve historical outputs and run
  the full test suite before handing off.

## Daily run

Production runs daily at 01:00 (including weekends), with a 48-hour
task execution limit. GPT-6 Luna scans every event in batches, keeps the
global top 40, reranks that shortlist, and only the final top 10 run Analyst +
Verifier. The remaining shortlisted events are broad-reading leads:

```powershell
.\scripts\run_daily.ps1 -RequireAI -Open -Config .\config\settings.daily-gpt.yaml -ExperimentId "gpt-6-luna-daily"
```

The scheduled task directly calls `启动日报.cmd --scheduled` at 01:00;
install or update it with `scripts/install_agent_task.ps1`.
Manual and scheduled startup share `scripts/run_daily.ps1`, whose default
config is `config/settings.daily-gpt.yaml`; the experiment id and model label
are read from that config. Scheduled mode suppresses browser opening and
failure pauses, saves a unique run log, and emails that invocation's result.
`scripts/run_daily_agent.ps1` is only a compatibility forwarder to the launcher.
The existing LAN gateway key env remains `QWEN_LAN_API_KEY` (historical name).
DeepSeek is an explicit optional configuration (`config/settings.deepseek.yaml`,
`OMLX_API_KEY`), not the default manual launcher.

Occasional local check with Qwen 3.8-27B: `启动日报-qwen.cmd` or

```powershell
.\scripts\run_daily.ps1 -RequireAI -Open -Config .\config\settings.qwen.yaml -ExperimentId "qwen3.8-27b"
```

That path uses `data/intelligence_qwen.db` and does not share analysis cache
with the scheduled GPT-6 Luna run, which uses `data/intelligence_gpt6_luna_daily.db`. Key env
`QWEN_LAN_API_KEY`. It remains an isolated manual model check.

Same-model A/B rerun: add `-ForceAnalysis`. A different model or experiment
automatically receives a different analysis and Scout cache scope. Never reuse
a misleading experiment id for another model.

There is no Harness file bridge. Do not recreate `scripts/harness`.

## Model stages

Read the configured broad-reading / `scout` / `analyst` / `verifier` /
`digest_brief` stages. Broad-reading leads are not deep conclusions and must
not be written to the scheduled GPT-6 Luna analysis cache.
Use only documents supplied to that stage. Evidence quotes must be exact
continuous substrings of the supplied document. Do not invent company mappings
or stock calls.

During `verifier`, audit the draft independently. Any material unsupported
claim must be listed and the verdict cannot be `pass`. The deterministic
quality gate, not the model verdict, decides whether a conclusion is `deep`.

## Run acceptance

Before reporting success, verify:

- the returned report path is `daily_digest.html` inside the new unique run
  directory;
- no Markdown, JSON, CSV, process page, or latest-run manifest was written
  at the date root or in the run directory;
- SQLite run metadata records the real model, experiment, cache scope,
  estimated token flag, source failures, and quality summary;
- every deep conclusion passed the deterministic quality gate;
- earlier run directories remain intact.

Do not commit or push merely because a daily run completed. Commit and push
only when the user explicitly requests repository delivery. Never commit
`output/`, `data/`, `logs/`, or `.env`.
