# Harness operating contract

This repository is a runnable daily-intelligence application, not a Skill.
Harness models are optional LLM backends and must use the existing application
contracts instead of reimplementing collection, analysis, or publication.

## Interpret common requests

- “生成今日日报”: run the application only. Do not edit source code, configs,
  prompts, tests, or historical outputs.
- “审查/比较日报”: read existing run artifacts and report findings. Do not call
  models or modify code unless explicitly asked.
- “修复/优化项目”: code changes are allowed; preserve historical outputs and run
  the full test suite before handing off.

## Harness-backed daily run

Use one truthful, stable model name and experiment id:

```powershell
.\.venv\Scripts\python.exe scripts\harness\run.py `
  --harness-name "qwen-code" `
  --model-name "qwen3.8-27b" `
  --experiment-id "qwen3.8-27b"
```

For a same-model A/B rerun, add `--force-analysis`. A different model or
experiment automatically receives a different analysis and Scout cache scope.
Never reuse a misleading experiment id for another model.
Set `--harness-name` to the actual driver, for example `qwen-code`,
`deepseek-harness`, or `codex`; do not label every run as Qwen Code.

The bridge writes each request under
`output/YYYY-MM-DD/runs/<run-name>/harness_io` and waits for the matching
`.response.json`.

For every request:

1. Read the full request, including `system`, `user`, and `json_schema`.
2. Use only the documents supplied in `user`; do not add remembered or
   web-searched facts. Extra research must first enter the normal Document and
   Evidence pipeline.
3. Write raw JSON matching `json_schema` to the exact `response_file`.
   Do not wrap it in Markdown or add commentary.
4. Evidence quotes must be exact continuous substrings of the supplied
   document. Do not duplicate facts or quotes to meet minimum counts.
5. During `verifier`, audit the draft independently. Any material unsupported
   claim must be listed and the verdict cannot be `pass`.

## Run acceptance

Before reporting success, verify:

- the returned report paths are inside the new unique run directory;
- `run_meta.json` records the real model, experiment, cache scope, estimated
  token flag, source failures, and quality summary;
- every deep conclusion passed the deterministic quality gate;
- no company mapping is marked verified without official evidence;
- day-root files are only latest-run compatibility aliases; earlier run
  directories remain intact.

Do not commit or push merely because a daily run completed. Commit and push only
when the user explicitly requests repository delivery.
