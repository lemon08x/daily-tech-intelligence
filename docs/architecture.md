# 架构与扩展边界

## 目标

项目采用模块化单体：保持一次安装、一个 CLI 和一份日报，同时让数据源、分析阶段、市场逻辑与展示方式可以独立演进。模型负责生成候选结构，程序负责去重、裁剪、证据核验、质量裁决和发布规格，因此更换模型不会改变“什么可以作为深度结论”的底线。

## 运行流

```text
SourceAdapter[] ──> DocumentCollector ──> EventCatalog ──> EventSelector
                                                       │
                                                       v
                         AnalysisQualityGate <── EventResearcher <── ModelStageRunner
                                  │
                                  v
MarketWorkflow ─────────────────> app/orchestrator ─────────────────> DigestPublisher
```

- `DocumentCollector`：只负责来源游标、并发采集与低权重市场雷达转换。
- `EventCatalog`：只负责文档持久化、72 小时聚类和事件文档装配。
- `EventSelector`：确定性得分与模型得分受限融合，并负责主题平衡。
- `ModelStageRunner`：统一模型调用、重试、用量和实际运行时审计。
- `EventResearcher`：全文增强、分析、独立校验和通过质量门后的公司映射。
- `AnalysisQualityGate`：模型无关的发布裁决；模型的 `verdict` 不是最终决定。
- `MarketProvider` / `MarketWorkflow`：可注入的数据适配器，以及完全独立的标准化和规则评分路径。
- `DigestPublisher`：把统一上下文交给默认文件渲染器，或替换成其他展示/推送实现。

## 依赖规则

1. `core` 不依赖业务实现，只保存稳定数据契约和端口。
2. `intelligence` 与 `market` 互不调用；二者只在 `app/orchestrator.py` 汇合。
3. AI 分析和公司映射不得写入市场候选分数。
4. `infrastructure` 实现端口，但业务阶段不依赖 SQLite、AkShare 或某个模型厂商的具体类。
5. `publication` 只消费标准化结果，不采集数据、不调用模型、不重新裁决质量。
6. 新阶段失败应返回局部错误或降级状态，不能补造事实。
7. 分析与Scout缓存必须包含实验标识和模型指纹；不同模型不得互相复用结果。

## 模型无关质量契约

默认 `evidence-gate-v2` 规定：

- 发布 3–6 条不重复事实、2–6 条有效证据、2–5 项风险、1–4 项反面观点和最多 4 项产业影响。
- 深度结论必须包含 `plain_takeaway`：2–3 句大白话，专业名词首次出现必须解释。
- 引文必须能在对应文档正文或摘要中逐字定位；重复和伪造引文被剔除。
- 深度结论必须有至少两条有效证据和至少一个一手来源。
- 校验结果含实质性 `unsupported_claims` 时强制降级，即使模型返回 `pass`。
- 单一来源置信度最高 0.85，深度结论最高 0.90，线索最高 0.49。
- 降级结果隐藏技术推断、产业影响与公司映射，只保留大白话要点、可核验事实、证据和原因。
- 发布层用一句完整的新闻短句拼“今日速读”，并按泛读/硬核分栏；主题词只看标题和速读句；不展示产业和全球市场。
- 前一天已经发布过的事件，第二天入选权重降低。Git 页展示 GitHub 今日最热和本周增长最快的项目。
- 市场页列出当日涨幅前三、跌幅后三或幅度够大的产业/指数，以及可归因事件（政策、监管、供给冲击等）；过滤资金流向、财报噪音和股票简称/风险警示变更；日报不展示个股扫描。

修改阈值时应更新 `quality.policy_version`，使历史分析不会被错误复用。修改提示词时应更新 `llm.prompt_version`。

## 运行与输出

一次运行由 `experiment_id`、唯一 `run_name` 和模型指纹共同标识。每次发布
只写入 `output/YYYY-MM-DD/runs/<run-name>`，不生成日期根副本或“最新运行”
清单。SQLite 的 `analysis_variants` 保存同一事件的多个模型实验版本。

`--force-analysis` 只绕过当前实验/模型作用域内的分析缓存，不删除任何历史
输出。Harness请求与响应与报告归档在同一运行目录，便于后续高级模型复盘。

## 三类常见增强

### 增加数据来源

实现 `SourceAdapter.collect(since, limit)`，在 `config/sources.yaml` 增加配置，并由来源工厂创建适配器。来源只输出 `Document`，不得直接调用聚类、模型或发布模块。采集游标使用唯一的适配器 `id`；同一发布者可用共享 `publisher_id` 防止分类查询、社区精选和转载被误计为独立证据。为解析、超时、降级和去重增加 fixture 测试。来源等级和已验证端点见 [`sources.md`](sources.md)。

### 优化分析流程

单独替换 `EventSelector`、`ModelStageRunner` 或 `EventResearcher`，也可在构造 `IntelligencePipeline` 时注入新实现。新增阶段应以稳定契约作为输入输出，并把模型结果交回 `AnalysisQualityGate`；不能绕过质量门直接构造深度结论。

### 优化数据可视化

实现新的 `DigestPublisher.publish(...)` 并注入 `run_application()`。可保留 `intelligence.json` 和 `run_meta.json` 作为稳定数据层，再替换 HTML 模板、图表库、静态站点或消息推送，不需要改采集和分析流程。

## 回归要求

完整测试流程、每个文件锁住什么、以及故意不测什么，见 [`testing.md`](testing.md)。

- 任何模型或提示词变更都要运行固定响应集，比较通过率、降级原因、证据有效率和输出长度，而不只比较文字观感。
- 市场 fixture 的标准化、可归因新闻排序和关键报告区块必须保持回归。
- `run_meta.json` 必须记录实际客户端元数据；无法读取真实用量时标记 `usage_reporting=estimated`。
- `Analysis` 必须显式包含质量字段；不符合当前契约的旧数据不会进入现行发布流程。
