# Legacy Harness run

This directory groups the report and Harness request/response files that were
originally committed at the day root and `llm_io/`.

The operator identified the actual analysis model as `qwen3.8-27b` through
Qwen Code. The preserved pre-v0.3 `run_meta.json` incorrectly echoes the
DeepSeek YAML configuration and its token counts are estimates. The files are
kept unchanged for auditability; v0.3 records actual Harness model metadata and
the estimated-usage flag correctly.
