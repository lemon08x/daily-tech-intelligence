# 运行笔记 · 110150-568-qwen-code-agent（2026-08-26）

代理：qwen-code-agent（Qwen Code 会话内直接完成 scout/analyst/verifier 三次角色）
结果：5 deep / 0 lead，质量均分 100，降级 0；9 次模型调用（scout 1 + analyst 4 + verifier 4），SRPO 命中昨日缓存（1 hit / 4 miss）。

## 遇到的问题

### 1. `relevant=false` 不保证剔除（selection 语义）
`EventSelector` 的剔除条件是 `not item.relevant and event.deterministic_score < model_reject_floor(55)`。
网络安全方法论综述（c8cb1fb）被 scout 标为 `relevant=false`，但确定性分 69.5 ≥ 55，仍按混合分
（det×0.65 + 模型四维×0.35，模型四维仍取 scout 输出值）参与排名，并独占 cybersecurity 主题槽位进入深研。
建议：在 `selection.py` 该分支加注释，或在 AGENTS.md 写明此边界行为。

### 2. 排名复算脚本的公式理解偏差（代理侧）
排查 PhageLys 与 Cyber 的先后顺序时，最初误以为不相关事件只用纯确定性分排名；实际管线对
`relevant=false` 且高于 floor 的事件同样计算混合分。修正复算脚本（`scripts/recompute_rank.py`）后
与 `pipeline_state` 中缓存的选择顺序完全一致。

### 3. bioRxiv 429 限流（来源侧）
PhageLysData（f0b01b2）全文富化失败：
`HTTPError: 429 Too Many Requests for url: https://www.biorxiv.org/content/10.64898/2026.08.24.746620v1?rss=1`
分析基于 1802 字符摘要完成，置信度降至 0.65，maturity/risks 已如实标注"全文未成功富化"。
新来源首日启用，bioRxiv 抓取频率敏感，建议后续对 biorxiv_ai4science 增加限速或退避。

### 4. PDF 提取伪影（每日固定成本）
当日 5 处引文因提取伪影首次校验失败，均经 `check_quotes.py` / `find_text.py` 定位后修正：
- Maia 200（2608.24664）：图注插入句中（"Internal data suggests that Maia [图1] 200 saves 30%…"），2 条引文改锚到句子片段
- RINU（2608.24842）：`judg-ments`、`re-searcher's` 连字符断行；`disclo-3 sure's`（页码插入单词）；`introducemarginal`（丢空格）
- Cyber 综述（2608.24850）：`re-searcher's` 连字符断行
全部引文最终 100% 逐字通过，无解析重试。

### 5. openai_news 来源 stale
当日唯一 failed_or_cached 科技来源，不影响主体（当日无新内容亦属正常）。

### 6. run_meta.json 结构为扁平布局
新代码的 run_meta 顶层直接是 `snapshot_rows`/`eligible_rows`/`sources` 等字段（无 `market` 嵌套键），
与旧版结构不同，外部读取脚本需适配。

## 流程要点（沿用昨日教训）
- 每个 analyst 请求先核实 `event_id` 再写草稿（昨日 174956 运行曾因顺序假设错误导致 3 事件张冠李戴、被质量门降级）
- 草稿遵守 quality_contract：key_facts 3-6、证据 ≤6、各节 ≤900 字、列表项 ≤420 字
- verifier 审计：无实质性 unsupported_claims 才给 pass（非空即降级）
