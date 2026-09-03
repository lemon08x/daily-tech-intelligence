import pandas as pd

from daily_intel.market.normalize import combine_news_frames, normalize_news


def test_market_pipeline_exposes_full_akshare_feed_to_scout_without_a_second_selection(
    tmp_path,
) -> None:
    from datetime import datetime, timezone

    from daily_intel.market.cache import Dataset
    from daily_intel.market.pipeline import MarketPipeline

    news = pd.DataFrame([
        {
            "title": "美联储宣布维持利率不变", "summary": "声明偏鹰",
            "published_at": "2026-08-27 10:00", "url": "https://example.com/fed",
        },
        {
            "title": "普通市场线索也交给 Scout 判断", "summary": "不在这里预先删掉",
            "published_at": "2026-08-27 11:00", "url": "https://example.com/raw",
        },
    ])
    calendar = pd.DataFrame([{"trade_date": "2026-08-27"}])

    class Provider:
        def news(self):
            return Dataset("news", news, "AkShare", "2026-08-27T12:00:00+00:00")

        def trading_calendar(self):
            return Dataset(
                "trading_calendar", calendar, "AkShare",
                "2026-08-27T12:00:00+00:00",
            )

    result = MarketPipeline(
        {}, tmp_path, datetime(2026, 8, 27, 12, tzinfo=timezone.utc), False,
        provider=Provider(),
    ).run()

    assert result.radar_news["title"].tolist() == news["title"].tolist()
    assert "news_records" not in result.context
    assert result.context["market_source_status"][0]["name"] == "news"


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
    assert combined.loc[combined["title"].eq("商务部对半导体设备实施出口管制"), "url"].iloc[0] == "https://example.com/ths"


def test_combine_news_prefers_tonghuashun_url_for_similar_sina_duplicate() -> None:
    ths = pd.DataFrame([
        {
            "标题": "江河集团：全资子公司中标沙特幕墙工程 中标金额折合人民币约2.23亿元",
            "内容": "中标金额约2.23亿元",
            "发布时间": "2026-08-31 15:50:00",
            "链接": "https://news.10jqka.com.cn/20260831/c679444232.shtml",
        },
        {
            "标题": "外交部：中方从不刻意追求贸易顺差",
            "内容": "反对单边关税",
            "发布时间": "2026-08-31 15:32:00",
            "链接": "https://news.10jqka.com.cn/20260831/c679443440.shtml",
        },
    ])
    sina = pd.DataFrame([
        {"时间": "2026-08-31 15:48:00", "内容": "【江河集团：中标沙特幕墙工程 金额约2.23亿元】尚未签正式合同"},
        {"时间": "2026-08-31 15:44:00", "内容": "【外交部：中方愿推动中俄新时代全面战略协作伙伴关系高水平发展】丁薛祥将出席论坛"},
    ])
    combined = combine_news_frames([ths, sina])
    titles = combined["title"].tolist()
    jianghe = combined[combined["title"].str.contains("江河集团")]
    assert len(jianghe) == 1
    assert jianghe["url"].iloc[0].startswith("https://news.10jqka.com.cn/")
    assert any("贸易顺差" in title for title in titles)
    assert any("中俄" in title for title in titles)
    russia = combined[combined["title"].str.contains("中俄")]
    assert russia["url"].iloc[0] == ""
