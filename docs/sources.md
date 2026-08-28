# 信息源策略与维护

## 分层原则

- Tier 1：原始论文、政府和研究机构、企业官方发布、项目官方 Release。可作为深度结论的一手来源。
- Tier 2：有编辑筛选和技术深度的行业媒体。用于补充产业影响或交叉验证，不能单独支撑深度结论。
- Tier 3：市场快讯和聚合线索。只进入雷达，不得用于深度结论。

来源等级描述的是证据身份，不等于文章一定正确。所有深度结果仍需通过逐字引文、独立校验和确定性质量门。

## 当前覆盖

- arXiv 拆为 AI/软件、芯片/系统/应用物理、计算生物三组独立查询，避免不同领域争用同一个 30 条结果窗口。新增 `cs.AR`、`cs.SE`、`eess.SP`、`physics.app-ph` 和 `cond-mat.mes-hall`。
- 官方 RSS 包括 OpenAI、DeepMind、Microsoft Research、NVIDIA、Hugging Face、Mistral、Meta、MIT、Nature 和美国能源部。
- Anthropic 与 Isomorphic Labs 没有可用 RSS，通过官方 sitemap 的 `lastmod` 增量发现文章；入选后仍走统一全文提取。
- Hugging Face Daily Papers API 只提供社区精选入口；存储身份、正文链接和证据仍归属原始 arXiv 论文，不能被计算成第二个独立来源。
- bioRxiv 全量源先做 AI4Science/生物技术关键词过滤，入选后提取原文。
- IEEE Spectrum、Tom's Hardware 和 CleanTechnica 为 Tier 2，并在采集层过滤促销和非目标内容。
- 国内机构网站暂未发现稳定 RSS，使用智源 FlagOpen、上海 AI Lab 的 InternLM/OpenMMLab 官方 Release。
- 巨潮资讯已在公司映射阶段核验近 365 天官方公告；它不是泛化新闻源。无公告证据的公司只能显示为待核验假设。

## 未启用的候选

- 用户建议的 Anthropic RSS URL 当前返回 404，因此使用官方 sitemap。
- NREL 当前在项目默认 TLS 客户端中握手失败，不写入启用列表，避免每天产生固定失败；恢复稳定后可按 RSS/Feed 方式加入。
- 智源和上海 AI Lab 官网当前没有可解析的 RSS/Atom，禁止把普通 HTML 首页伪装成 Feed。
- AnandTech 已不适合作为持续更新来源；硬件产业动态由官方源、IEEE 和带过滤的 Tom's Hardware 补充。
- GitHub Commits、Pull Requests、Discussions 和 Trending 暂不启用。它们噪声高、公共 API 限流明显，后续应作为独立 Radar 适配器实现高信号规则，不能混入正式 Release。
- 科技周刊进入泛读栏：阮一峰周刊 Issue 投稿池为 Tier 3 线索；Hacker Newsletter、Import AI、TLDR、Golang/JavaScript Weekly 为 Tier 2。缺少 Tier 1 一手来源时质量门会降为线索，不会单独变成深度结论。
- 阮一峰周刊历史 `docs/` 外链不在每日主流程里 git pull。用 `scripts/refresh_weekly_catalog.py` 解析栏目并探测 RSS，写入 `data/cache/weekly_blog_feeds.json` 后作为 Tier 3 泛读补充源。
- 周刊短链会还原成最终 URL，再按 canonical_url / GitHub owner/repo 去重，避免同一项目被多个周刊重复深研。

## 配置和去重约定

每个采集器必须有唯一 `id`，用于独立游标和失败状态。`publisher_id` 表示证据发布者；同一 arXiv 论文可能由分类 API 与 Hugging Face 精选同时发现，但统一 `publisher_id: arxiv` 后只存一份、只计一个来源。

Feed 可设置：

- `include_keywords` / `exclude_keywords`：来源级预过滤；短词按完整单词匹配。
- `max_items`：限制单次进入主题过滤前的条数，不能突破全局上限。
- `content_type`：显式标注 `paper`、`article` 或 `github_release`。
- `fetch_full_text`：入选深研后是否尝试正文提取。

新增来源应先实时验证 HTTP 状态、内容类型、时间字段和最近条目，再补 fixture 测试。失败来源由现有采集器独立降级，不影响其他来源。
