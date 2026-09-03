# 科技产业情报日报

这是一个可每日运行的模块化单体：从权威白名单采集科技前沿信息，以可替换的模型完成受限筛选、深研和独立证据校验，并附带产业/全球市场作为信息源。最后写成一份 HTML 日报。

本报告只做公开信息整理与研究观察，不构成投资建议。

## 快速开始

最简单的方式是双击根目录的 `启动日报.cmd`。它与早间定时任务同一条链路：局域网 DeepSeek V4 Flash、`config/settings.deepseek.yaml`，生成后打开当天 HTML。首次运行会创建 `.venv` 并安装依赖。

本地想用更快的局域网 Qwen 3.8-27B 验证改动时，双击 `启动日报-qwen.cmd`。它走 `config/settings.qwen.yaml` 和独立库 `data/intelligence_qwen.db`，不会覆盖 DeepSeek 的分析缓存。密钥变量是 `QWEN_LAN_API_KEY`。

两个启动窗口都会先打印后端、配置和实验 id，再输出 `[1/6]` 到 `[6/6]` 阶段，以及 `当前：…` 明细。

PowerShell 方式：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
.\scripts\run_daily.ps1 -RequireAI -Open
```

Qwen 一次性运行：

```powershell
.\scripts\run_daily.ps1 -RequireAI -Open -Config .\config\settings.qwen.yaml -ExperimentId "qwen3.8-27b"
```

没有 API 密钥也能运行：系统会采集权威来源，发布明确标为“线索”的事件，不会伪造 AI 分析。

每日定时任务走局域网 DeepSeek，密钥变量是 `OMLX_API_KEY`，只写本机环境变量，不要写进配置或仓库：

```powershell
[Environment]::SetEnvironmentVariable("OMLX_API_KEY", "你的密钥", "User")
$env:OMLX_API_KEY = "你的密钥"  # 仅让当前 PowerShell 立即生效
.\scripts\run_daily.ps1 -RequireAI -Open
```

常用运行方式：

```powershell
# 采集科技来源，但明确禁止模型调用
.\scripts\run_daily.ps1 -NoAI -Open

# 只使用市场缓存以及 SQLite 中已存的情报/分析
.\scripts\run_daily.ps1 -Offline -Open

# 检查依赖、配置、来源数量和密钥状态，不访问网络
.\.venv\Scripts\python.exe -m daily_intel doctor --config .\config\settings.yaml
```

当前版本只保留 `daily_intel` 应用、`daily-intel` 命令和 YAML 配置入口。没有 Harness 文件桥，也不再维护旧包名。

## 每日输出

同一天可以保存多次运行：

```text
output/YYYY-MM-DD/
└── runs/
    ├── HHMMSS-run-id-deepseek-v4-flash/
    └── HHMMSS-run-id-qwen3.8-27b/
```

每个 `runs/<run-name>` 只写 `daily_digest.html`。同一天重复运行不会覆盖此前运行目录，也不会在日期根目录生成副本。

HTML 先给出精读条目的“今日速读”，再提供“精读 / 泛读 / Git”三个页签。科技白名单和 AkShare 快讯统一进入 72 小时事件池与 Scout；Scout 保留的事件全部经过 Analyst、Verifier 和确定性质量门。应用按 Scout 顺序从材料充足的结果中补足最多 10 条精读：至少 2 条经校验证据，且去重后的可定位引文不少于 160 字；材料过短及其余结果进入泛读。精读与今日速读都按首次出现的主题稳定分组，同主题相邻且组内保留 Scout 顺序；泛读按主题聚类并以一句话缩略展示。“Git”页独立列出 GitHub 今日最热和本周增长最快的真实仓库、总星标，以及 AI 逐仓库对照 README、清单和源码入口生成的具体使用场景；星标增量只在副标题展示，不绘制无量纲进度条。前一天已经出现过的事件第二天会降低排序权重，已完成的同版本分析会直接复用缓存。

持久化情报位于 `data\intelligence_deepseek.db`（Qwen 为 `intelligence_qwen.db`），包括文档、事件、证据、按实验及模型指纹隔离的分析版本、LLM 调用和流水线游标。市场 CSV 缓存仍位于 `data\cache\`。

## 架构

```text
daily/
├── AGENTS.md         应用操作契约
├── config/           运行、主题、来源配置
├── docs/             架构、来源与测试说明
├── scripts/          启动、定时任务、发信与只读诊断
├── src/              可安装 Python 包
├── tests/            固定 Fixture 与回归测试
└── output/           按日期、运行名归档的 HTML
```

```text
src/daily_intel/
├── app/             CLI 与统一编排器
├── core/            Document/Event/Analysis/Digest 等契约及可替换端口
├── intelligence/    分阶段的采集、事件目录、筛选、模型调用、深研与质量门
├── github/          GitHub Trending 解析与总星标
├── market/          AkShare 适配、CSV 缓存与标准化；内容交给统一 Scout
├── infrastructure/  SQLite 仓库和 OpenAI 兼容模型客户端
└── publication/     可替换发布器及 HTML 默认实现
```

关键边界：

- `SourceAdapter.collect()`：可替换科技来源；
- `LLMClient.generate()`：模型供应商与业务逻辑解耦；
- `IntelligenceRepository`：情报持久化契约；
- `MarketProvider`：市场数据适配契约；
- `MarketWorkflow` / `IntelligenceWorkflow`：两条流水线可独立替换；
- `DigestPublisher`：报告渲染或后续消息推送的替换边界。

应用实现只位于 `src/daily_intel`。情报流水线内部继续拆为 `collection.py`、`discovery.py`、`selection.py`、`modeling.py`、`research.py` 和 `quality.py`。主 `pipeline.py` 只编排阶段。完整依赖规则见 [`docs/architecture.md`](docs/architecture.md)。

## 统一情报流程

1. 首次回看 48 小时；之后每个来源从自己的上次成功游标继续，并保留 6 小时重叠。
2. 分组 arXiv、RSS/Atom、官方 sitemap、结构化论文 API、GitHub Release 和 AkShare 市场快讯先做来源级去重与 72 小时事件聚类；明显的 nightly/build 在进入 AI 前丢弃，未命中规则主题的内容保留为 `other`。
3. Scout 分批审阅全部候选；最终排序由确定性分数 35% 与模型分数 65% 融合，模型漏项时回退到确定性排序。栏目、主题和官方发布位只调整顺序，不硬裁剪数量。
4. Scout 保留的全部事件依次检查分析缓存；未命中缓存的事件进入 Analyst 和独立 Verifier。只有满足逐字证据及一手来源要求的结果才标为“深度结论”，否则确定性降级为“线索”。单事件失败会重试一次，仍失败则跳过，不合成伪分析。
5. 成功结果按 Scout 顺序通过精读材料门并顺位补位：最多前 `intensive_reading_events`（默认 10）进入精读详情；有效证据少于 2 条或去重引文少于 160 字的 AI 结果，以及名额之外的结果，全部进入泛读缩略列表。

模型输出还会经过统一质量契约：事实、证据、产业影响、风险与反面观点都有固定上下限；重复项和伪造引用会被程序剔除；存在 `unsupported_claims` 时，即使校验模型返回 `pass` 也强制降级；单一来源和线索状态都有置信度上限。质量分、证据数、来源数与降级原因会写入 HTML。

首批主题为大模型与 Agent、芯片算力、机器人、云与开发工具、网络安全、智能汽车、能源科技、生物技术。来源白名单见 `config\sources.yaml`。分层依据见 [`docs/sources.md`](docs/sources.md)。

## 配置

- `config\settings.deepseek.yaml`：早间任务实际配置；
- `config\settings.qwen.yaml`：本地 Qwen 验证配置；
- `config\settings.yaml`：云端 DeepSeek 示例，不是早间任务路径；
- `config\topics.yaml`：主题及中英文关键词；
- `config\sources.yaml`：分层来源白名单、来源级过滤与 GitHub 仓库。

日报内容主链使用 `scout`（全量初筛）、`analyst`（逐事件深研）和 `verifier`（独立校验）；Git 项目另用 `git_brief`。`digest_brief` 配置保留兼容，但 AkShare 改入统一 Scout 后，日常发布不再调用独立市场热点文案链。每个阶段仍可在 YAML 中配置不同模型。

日常定时任务用的是 `config/settings.deepseek.yaml`：局域网 `http://192.168.31.236:8000/v1`，密钥变量 `OMLX_API_KEY`，各阶段目前都指向同一个 `deepseek-v4-flash-0731`。本地 Qwen / DeepSeek 各阶段都开思考，并使用端点支持的最高 reasoning（Qwen 默认 `xhigh`，DeepSeek 显式 `xhigh`），输出上限放到 32000，质量优先、不省 token。

## 自动运行与测试

每天 06:00（含周末）用局域网 DeepSeek V4 Flash 生成日报并发送邮件：

```powershell
.\scripts\install_agent_task.ps1 -At "06:00"
```

该任务读取 `config/settings.deepseek.yaml`，密钥变量为 `OMLX_API_KEY`。同名任务已存在时会就地更新触发器，不会再安装一份。非交易日仍会出科技日报。

全量深研的冷启动可能持续约 9～10 小时；计划任务上限为 18 小时，并禁止同一任务并行重入。稳定运行后会复用同模型、同提示词和同质量策略的逐事件分析缓存。

运行日志保存在 `logs\`。

回归测试说明见 [`docs/testing.md`](docs/testing.md)。固定响应加临时 SQLite，不需要网络或真实密钥：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

第三方组件与许可证见 `THIRD_PARTY_NOTICES.md`。
