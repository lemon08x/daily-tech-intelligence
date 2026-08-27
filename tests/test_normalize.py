import pandas as pd

from daily_intel.market.normalize import normalize_global_quotes, normalize_snapshot
from daily_intel.market.pipeline import rank_market_news


def test_normalize_tencent_units_and_code() -> None:
    raw = pd.DataFrame(
        [{
            "code": "sh600000", "name": "浦发银行", "zxj": "10.5", "zdf": "1.2",
            "hsl": "2", "lb": "1.3", "pe_ttm": "6", "pn": "0.6", "zsz": "1000",
            "ltsz": "900", "turnover": "12345", "zdf_d5": "2", "zdf_d10": "3",
            "zdf_d20": "4", "zdf_d60": "8", "zdf_y": "9",
        }]
    )
    result = normalize_snapshot(raw).iloc[0]
    assert result["code"] == "600000"
    assert result["market"] == "sh"
    assert result["amount_cny"] == 123_450_000
    assert result["market_cap_cny"] == 100_000_000_000


def test_normalize_sina_keeps_required_fields() -> None:
    raw = pd.DataFrame(
        [{"代码": "sz000001", "名称": "平安银行", "最新价": 11, "涨跌幅": -0.5, "成交额": 2e8}]
    )
    result = normalize_snapshot(raw).iloc[0]
    assert result["code"] == "000001"
    assert result["amount_cny"] == 2e8
    assert pd.isna(result["momentum_60d"])


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
