# 科技产业情报与 A 股观察

这是一个可每日运行的模块化单体：从权威白名单采集科技前沿信息，以可选的 DeepSeek 模型完成筛选、深研和独立证据校验，同时保留原有 AkShare 行情、透明规则评分与缓存降级。两条流水线最后合并成一份 HTML/Markdown 日报。

AI 科技事件和公司关联始终只是研究信息，**不会进入股票综合分**，也不构成投资建议。

## 快速开始

最简单的方式是双击根目录的 `启动日报.cmd`。首次运行会创建 `.venv`、安装依赖，之后生成并打开当天统一日报。

PowerShell 方式：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
.\scripts\run_daily.ps1 -Open
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

旧入口仍可用：`python -m daily_a_share`、`daily-a-share`、根目录 `config.yaml` 都会转入新实现。旧缓存和历史输出不会被迁移或删除。

## 每日输出

文件位于 `output\YYYY-MM-DD\`。同一天重复运行会更新当日报告，文档、事件、模型分析按稳定标识从 SQLite 复用。

- `daily_digest.html`：统一可视化日报；
- `daily_digest.md`：适合推送和二次编辑；
- `intelligence.json`：稳定的科技分析数据契约；
- `candidates.csv`：规则过滤后的完整股票候选及因子分；
- `market_snapshot.csv`：标准化全市场快照；
- `run_meta.json`：模型、提示词版本、token、失败源、缓存和新鲜度状态。

持久化情报位于 `data\intelligence.db`，包括文档、事件、证据、分析、产业/公司映射、LLM 调用和流水线游标。市场 CSV 缓存仍位于 `data\cache\`。

## 架构

```text
src/daily_intel/
├── app/             CLI 与统一编排器
├── core/            Document/Event/Analysis/Digest 等契约及可替换端口
├── intelligence/    来源采集、全文/PDF提取、聚类、AI深研、证据与公司核验
├── market/          AkShare 适配、CSV缓存、标准化、规则筛选与评分
├── infrastructure/  SQLite 仓库和 OpenAI 兼容模型客户端
└── publication/     HTML/Markdown/JSON/CSV 统一发布
```

关键边界：

- `SourceAdapter.collect()`：可替换科技来源；
- `LLMClient.generate()`：模型供应商与业务逻辑解耦；
- `IntelligenceRepository`：情报持久化契约；
- `MarketProvider`：市场数据适配契约；
- `MarketSignal`：为下一轮 Qlib/RD-Agent 预留，但本轮没有安装或调用这些项目。

`src/daily_a_share` 和根目录 `daily_a_share` 只是兼容层，业务实现都在 `daily_intel`。

## 科技情报流程

1. 首次回看 48 小时；之后每个来源从自己的上次成功游标继续，并保留 6 小时重叠。
2. arXiv、RSS/Atom 和 GitHub Release 先做来源级去重、主题过滤和 72 小时事件聚类；明显的 nightly/build 自动记录在进入 AI 前丢弃。
3. Flash 最多筛选 40 个候选事件；Pro 深研最多 5 个，再由 Pro 独立校验。
4. 只有至少两条原文可定位证据且包含权威一手来源时才标为“深度结论”，否则降级成“线索”。无效 JSON 或单事件模型失败会重试一次，仍失败则跳过，不合成伪分析。
5. A 股代码和名称必须存在于当日快照；巨潮行业分类只作背景，必须有近 365 天巨潮公告证据才能标为“已核验关联”，否则只能是“待核验假设”。

首批主题为大模型与 Agent、芯片算力、机器人、云与开发工具、网络安全、智能汽车、能源科技、生物技术。来源白名单见 `config\sources.yaml`，包括 arXiv、OpenAI、DeepMind、Microsoft Research、NVIDIA、Hugging Face、MIT、IEEE、Nature、美国能源部以及配置的 GitHub 项目。市场快讯仅是低权重雷达，不能单独支撑深度结论。

## 配置

- `config\settings.yaml`：路径、市场规则、情报窗口、模型和参数；
- `config\topics.yaml`：主题及中英文关键词；
- `config\sources.yaml`：权威来源白名单与 GitHub 仓库；
- `config.yaml`：保留一版的旧配置兼容入口。

默认模型端点为 `https://api.deepseek.com`，密钥变量为 `DEEPSEEK_API_KEY`。初筛模型 `deepseek-v4-flash`，深研/校验模型 `deepseek-v4-pro`；这些名称与参数全部可在 YAML 修改，代码不绑定模型厂商。

原有股票综合分保持不变：趋势 30%、估值 20%、流动性 15%、活跃度 15%、当日强弱 10%、市值 10%。AI 分析不会读写这条评分路径。

## 自动运行与测试

确认手动运行成功后创建工作日 18:10 任务：

```powershell
.\scripts\install_scheduled_task.ps1 -At "18:10"
```

同名任务已存在时脚本会停止，不会静默覆盖。运行日志保存在 `logs\latest.log`。

测试：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试使用固定响应和临时 SQLite，不需要网络或真实密钥。第三方组件与许可证见 `THIRD_PARTY_NOTICES.md`。
