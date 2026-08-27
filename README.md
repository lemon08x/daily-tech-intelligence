# 科技产业情报与 A 股观察

这是一个可每日运行的模块化单体：从权威白名单采集科技前沿信息，以可替换的模型完成受限筛选、深研和独立证据校验，同时保留原有 AkShare 行情、透明规则评分与缓存降级。两条流水线最后合并成一份 HTML/Markdown 日报。

AI 科技事件和公司关联始终只是研究信息，**不会进入股票综合分**，也不构成投资建议。

## 快速开始

最简单的方式是双击根目录的 `启动日报.cmd`。它与早间定时任务同一条链路：局域网 DeepSeek V4 Flash、`config/settings.deepseek.yaml`，生成后打开当天 HTML。首次运行会创建 `.venv` 并安装依赖。

PowerShell 方式：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
.\scripts\run_daily.ps1 -RequireAI -Open
```

没有 API 密钥也能运行：系统会采集权威来源，发布明确标为“线索”的事件和完整市场报告，不会伪造 AI 分析。

启用 AI 时，只把密钥写入本机环境变量，不要写进配置或仓库：

```powershell
[Environment]::SetEnvironmentVariable("DEEPSEEK_API_KEY", "你的密钥", "User")
$env:DEEPSEEK_API_KEY = "你的密钥"  # 仅让当前 PowerShell 立即生效
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

使用 Qwen Code、DeepSeek Harness 或 Codex 等 Harness 模型作为分析后端：

```powershell
.\.venv\Scripts\python.exe scripts\harness\run.py `
  --harness-name "qwen-code" `
  --model-name "qwen3.8-27b" `
  --experiment-id "qwen3.8-27b"
```

相同模型需要强制重新分析时追加 `--force-analysis`。项目级 Harness
行为约束见 [`AGENTS.md`](AGENTS.md)，这里不使用 Skill。

当前版本只保留 `daily_intel` 应用、`daily-intel` 命令和
`config/settings.yaml` 配置入口，不再维护旧包名、旧命令或旧根配置。

## 每日输出

同一天可以保存多次运行：

```text
output/YYYY-MM-DD/
└── runs/
    ├── HHMMSS-run-id-qwen3.8-27b/
    └── HHMMSS-run-id-deepseek-v4-flash/
```

每个 `runs/<run-name>` 都包含完整 HTML、Markdown、JSON、CSV 和运行元数据；
Harness 运行还包含 `harness_io` 请求/响应审计。同一天重复运行不会覆盖此前
运行目录，也不会在日期根目录生成重复副本。

- `daily_digest.html`：默认显示新闻精选，并通过页签切换到A股行情；
- `daily_digest.md`：适合推送和二次编辑；
- `intelligence.json`：稳定的科技分析数据契约；
- `candidates.csv`：规则过滤后的完整股票候选及因子分；
- `market_snapshot.csv`：标准化全市场快照；
- `run_meta.json`：模型、提示词版本、token、失败源、缓存和新鲜度状态。

HTML 先给出可扫的“今日速读”（科技短句，条目前有主题词），再进入
“新闻精选”。每条常显大白话要点，标题和“阅读原文”链接到可定位来源，深度
分析按需展开。“市场情报”页把交易所当信息源：产业和全球市场只列当日涨跌
前三后三或幅度够大的条目，并保留可归因事件；个股规则分只作折叠参考，不是荐股。

持久化情报位于 `data\intelligence.db`，包括文档、事件、证据、按实验及
模型指纹隔离的分析版本、产业/公司映射、LLM调用和流水线游标。市场CSV缓存
仍位于 `data\cache\`。

## 架构

```text
daily/
├── AGENTS.md         Harness薄操作契约
├── config/           运行、主题、来源配置
├── docs/             架构与扩展说明
├── scripts/
│   ├── harness/      Harness文件桥接与请求核验
│   └── diagnostics/  只读诊断工具
├── src/              可安装Python包
├── tests/            固定Fixture与回归测试
└── output/           按日期、运行名归档的可追溯产物
```

```text
src/daily_intel/
├── app/             CLI 与统一编排器
├── core/            Document/Event/Analysis/Digest 等契约及可替换端口
├── intelligence/    分阶段的采集、事件目录、筛选、模型调用、深研与质量门
├── market/          AkShare 适配、CSV缓存、标准化、规则筛选与评分
├── infrastructure/  SQLite 仓库和 OpenAI 兼容模型客户端
└── publication/     可替换发布器及 HTML/Markdown/JSON/CSV 默认实现
```

关键边界：

- `SourceAdapter.collect()`：可替换科技来源；
- `LLMClient.generate()`：模型供应商与业务逻辑解耦；
- `IntelligenceRepository`：情报持久化契约；
- `MarketProvider`：市场数据适配契约；
- `MarketWorkflow` / `IntelligenceWorkflow`：两条流水线可独立替换；
- `DigestPublisher`：报告渲染、文件输出或后续消息推送的替换边界；
- `MarketSignal`：为下一轮 Qlib/RD-Agent 预留，但本轮没有安装或调用这些项目。

应用实现只位于 `src/daily_intel`，不存在第二套兼容包或重复实现。

情报流水线内部继续拆为 `collection.py`、`discovery.py`、`selection.py`、
`modeling.py`、`research.py` 和 `quality.py`。主 `pipeline.py` 只编排阶段，
不再实现抓取、提示词重试、证据判断或公司映射。完整依赖规则与扩展方式见
[`docs/architecture.md`](docs/architecture.md)。

## 科技情报流程

1. 首次回看 48 小时；之后每个来源从自己的上次成功游标继续，并保留 6 小时重叠。
2. 分组 arXiv、RSS/Atom、官方 sitemap、结构化论文 API 和 GitHub Release 先做来源级去重、主题过滤和 72 小时事件聚类；明显的 nightly/build 自动记录在进入 AI 前丢弃。
3. 初筛模型最多处理 40 个候选事件；最终排序由确定性分数 65% 与模型分数 35% 融合，模型漏项时回退到确定性排序，避免模型完全控制选题。
4. 深研最多 5 个事件，再由独立校验阶段审计；只有至少两条原文逐字证据且包含权威一手来源时才标为“深度结论”，否则确定性降级成“线索”。无效 JSON 或单事件模型失败会重试一次，仍失败则跳过，不合成伪分析。
5. A 股代码和名称必须存在于当日快照；巨潮行业分类只作背景，必须有近 365 天巨潮公告证据才能标为“已核验关联”，否则只能是“待核验假设”。

模型输出还会经过统一质量契约：事实、证据、产业影响、风险与反面观点都有固定上下限；重复项和伪造引用会被程序剔除；存在 `unsupported_claims` 时，即使校验模型返回 `pass` 也强制降级；单一来源和线索状态都有置信度上限。质量分、证据数、来源数与降级原因会写入 HTML、Markdown、`intelligence.json` 和 `run_meta.json`。

首批主题为大模型与 Agent、芯片算力、机器人、云与开发工具、网络安全、智能汽车、能源科技、生物技术。来源白名单见 `config\sources.yaml`，覆盖分组 arXiv、OpenAI、Anthropic、DeepMind、Microsoft Research、NVIDIA、Mistral、Meta、Hugging Face Daily Papers、bioRxiv、Isomorphic Labs、Nature、能源与硬件行业源，以及国内外官方 GitHub Release。市场快讯仅是低权重雷达，不能单独支撑深度结论。分层依据、已验证端点和暂缓来源见 [`docs/sources.md`](docs/sources.md)。

## 配置

- `config\settings.yaml`：路径、市场规则、情报窗口、模型和参数；
- `config\topics.yaml`：主题及中英文关键词；
- `config\sources.yaml`：分层来源白名单、来源级过滤与 GitHub 仓库。

默认模型端点为 `https://api.deepseek.com`，密钥变量为 `DEEPSEEK_API_KEY`。初筛模型 `deepseek-v4-flash`，深研/校验模型 `deepseek-v4-pro`；这些名称、采样参数和供应商扩展参数全部可在 YAML 修改。运行元数据记录客户端报告的实际提供方和模型，不再直接把静态 YAML 当作实际运行结果；文件代理无法获得真实 token 时会明确标为“估算”。

原有股票综合分保持不变：趋势 30%、估值 20%、流动性 15%、活跃度 15%、当日强弱 10%、市值 10%。AI 分析不会读写这条评分路径。

## 自动运行与测试

每天 08:30（含周末）用局域网 DeepSeek V4 Flash 生成日报并发送邮件：

```powershell
.\scripts\install_agent_task.ps1 -At "08:30"
```

该任务读取 `config/settings.deepseek.yaml`，密钥变量为 `OMLX_API_KEY`。同名任务已存在时会就地更新触发器，不会再安装一份。非交易日仍会出科技日报，行情沿用最近交易日。

如需另建工作日 18:10 的默认配置任务：

```powershell
.\scripts\install_scheduled_task.ps1 -At "18:10"
```

运行日志保存在 `logs\`。

测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试使用固定响应和临时 SQLite，不需要网络或真实密钥。第三方组件与许可证见 `THIRD_PARTY_NOTICES.md`。
