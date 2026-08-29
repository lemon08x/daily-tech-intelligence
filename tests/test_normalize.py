import pandas as pd

from daily_intel.market.normalize import combine_news_frames, normalize_global_quotes, normalize_news
from daily_intel.market.pipeline import rank_market_news


def test_normalize_global_quotes_from_eastmoney_columns() -> None:
    raw = pd.DataFrame([
        {"代码": "NDX", "名称": "纳斯达克", "最新价": 18000, "涨跌幅": 1.2},
        {"代码": "GC", "名称": "COMEX黄金", "最新价": 4600, "涨跌幅": -0.4},
    ])
    result = normalize_global_quotes(raw)
    assert list(result["name"]) == ["纳斯达克", "COMEX黄金"]
    assert result.iloc[0]["pct_change"] == 1.2


def test_sina_global_index_parser_reads_hq_payload(monkeypatch) -> None:
    from daily_intel.market.providers import fetch_sina_global_indices

    class Fake:
        status_code = 200
        text = (
            'var hq_str_int_nasdaq="纳斯达克,22484.07,99.37,0.44";\n'
            'var hq_str_int_dji="道琼斯,46247.29,299.97,0.65";\n'
        )
        encoding = "gb18030"

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("daily_intel.market.providers.http_get", lambda *a, **k: Fake())
    frame = fetch_sina_global_indices()
    assert set(frame["名称"]) == {"纳斯达克", "道琼斯"}
    assert float(frame.loc[frame["名称"].eq("纳斯达克"), "涨跌幅"].iloc[0]) == 0.44


def test_rank_market_news_keeps_event_causes_and_drops_fund_news() -> None:
    news = pd.DataFrame([
        {"title": "北向资金净流入超百亿", "summary": "成交额放大", "published_at": "2026-08-27", "url": "https://example.com/flow"},
        {"title": "券商中报净利润大增", "summary": "中信半年利润超230亿", "published_at": "2026-08-27", "url": "https://example.com/earnings"},
        {"title": "美联储宣布维持利率不变", "summary": "声明偏鹰", "published_at": "2026-08-27", "url": "https://example.com/fed"},
        {"title": "商务部对半导体设备实施出口管制", "summary": "A股相关产业链受政策影响", "published_at": "2026-08-27", "url": "https://example.com/ashare"},
        {"title": "某地文旅节开幕", "summary": "与市场无关", "published_at": "2026-08-27", "url": "https://example.com/tour"},
    ])
    ranked = rank_market_news(news, ["半导体"], 5)
    titles = ranked["title"].tolist()
    assert titles[0].startswith("商务部")
    assert "美联储" in titles[1]
    assert all("北向" not in title and "中报" not in title and "文旅" not in title for title in titles)


def test_rank_market_news_fills_when_event_causes_are_few() -> None:
    news = pd.DataFrame([
        {"title": "北向资金净流入超百亿", "summary": "成交额放大", "published_at": "2026-08-27", "url": "https://example.com/flow"},
        {"title": "美联储宣布维持利率不变", "summary": "声明偏鹰", "published_at": "2026-08-27", "url": "https://example.com/fed"},
        {"title": "英伟达称算力短缺将持续到2028年", "summary": "GPU供给仍然紧张", "published_at": "2026-08-27", "url": "https://example.com/nvda"},
        {"title": "某地文旅节开幕", "summary": "与市场无关", "published_at": "2026-08-27", "url": "https://example.com/tour"},
        {"title": "运机集团：2026年上半年净利润3771.6万元，同比下降48.60%", "summary": "中报披露", "published_at": "2026-08-27", "url": "https://example.com/earn"},
        {"title": "沪指盘中回踩后有色金属走强", "summary": "有色金属板块活跃", "published_at": "2026-08-27", "url": "https://example.com/metal"},
    ])
    ranked = rank_market_news(news, ["有色金属"], 5, min_fill=3)
    titles = ranked["title"].tolist()
    assert any("美联储" in title for title in titles)
    assert any("英伟达" in title for title in titles)
    assert any("沪指" in title for title in titles)
    assert all(
        "北向" not in title and "文旅" not in title and "净利润" not in title
        for title in titles
    )
    assert len(titles) >= 3


def test_rank_market_news_does_not_go_empty_on_earnings_heavy_feed() -> None:
    news = pd.DataFrame([
        {"title": "运机集团：2026年上半年净利润3771.6万元，同比下降48.60%", "summary": "中报", "published_at": "2026-08-27", "url": "https://example.com/a"},
        {"title": "尼泊尔北部山洪遇难人数升至289人", "summary": "救援", "published_at": "2026-08-27", "url": "https://example.com/b"},
        {"title": "美国司法部拟重启捕获法庭以便扣押伊朗油轮", "summary": "加强对伊朗的海上封锁", "published_at": "2026-08-27", "url": "https://example.com/c"},
        {"title": "众诚科技全资子公司签订2.92亿元合同", "summary": "信息化建设采购", "published_at": "2026-08-27", "url": "https://example.com/d"},
        {
            "title": "ST围海：8月31日起撤销其他风险警示 股票简称变更为围海股份",
            "summary": "证券简称由ST围海变更为围海股份，证券代码不变，日涨跌幅限制为10%。",
            "published_at": "2026-08-27", "url": "https://example.com/st",
        },
    ])
    ranked = rank_market_news(news, [], 5, min_fill=3)
    titles = ranked["title"].tolist()
    assert any("扣押" in title for title in titles)
    assert any("合同" in title for title in titles)
    assert all("净利润" not in title and "山洪" not in title and "围海" not in title for title in titles)


def test_news_provider_merges_both_feeds(tmp_path, monkeypatch) -> None:
    from datetime import datetime, timezone

    from daily_intel.market.cache import CsvCache
    from daily_intel.market.providers import AkShareProvider

    ths = pd.DataFrame([
        {"标题": "运机集团：2026年上半年净利润下降", "内容": "中报", "发布时间": "2026-08-27 18:00", "链接": "https://example.com/earn"},
    ])
    sina = pd.DataFrame([
        {"时间": "2026-08-27 10:00", "内容": "【美联储宣布维持利率不变】声明偏鹰"},
    ])
    monkeypatch.setattr("daily_intel.market.providers.ak.stock_info_global_ths", lambda: ths)
    monkeypatch.setattr("daily_intel.market.providers.ak.stock_info_global_sina", lambda: sina)
    provider = AkShareProvider(CsvCache(tmp_path), datetime(2026, 8, 27, tzinfo=timezone.utc))
    dataset = provider.news()
    titles = dataset.frame["title"].tolist()
    assert "运机集团：2026年上半年净利润下降" in titles
    assert "美联储宣布维持利率不变" in titles
    assert "同花顺" in dataset.source and "新浪" in dataset.source


def test_combine_news_frames_merges_ths_and_sina_schemas() -> None:
    ths = pd.DataFrame([
        {"标题": "商务部对半导体设备实施出口管制", "内容": "政策落地", "发布时间": "2026-08-27 09:00", "链接": "https://example.com/ths"},
        {"标题": "运机集团：2026年上半年净利润下降", "内容": "中报", "发布时间": "2026-08-27 18:00", "链接": "https://example.com/earn"},
    ])
    sina = pd.DataFrame([
        {"时间": "2026-08-27 10:00", "内容": "【美联储宣布维持利率不变】声明偏鹰"},
        {"时间": "2026-08-27 11:00", "内容": "【商务部对半导体设备实施出口管制】重复"},
    ])
    combined = combine_news_frames([ths, sina])
    titles = combined["title"].tolist()
    assert "商务部对半导体设备实施出口管制" in titles
    assert "美联储宣布维持利率不变" in titles
    assert titles.count("商务部对半导体设备实施出口管制") == 1
    assert list(combined.columns) == ["title", "summary", "published_at", "url"]
    roundtrip = normalize_news(combined)
    assert list(roundtrip["title"]) == titles
