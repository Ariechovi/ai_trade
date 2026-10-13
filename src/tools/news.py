"""消息面工具集（4 个）。

对应 TradingAgents-CN 的 `core/tools/implementations/news/stock_news.py`
与 `social/stock_sentiment.py`。

情绪判断用**词典规则法**而不是再调一次 LLM：便宜、可解释、可测试。
（真要做细可以换成模型打分，但那是另一个量级的成本。）
"""

from __future__ import annotations

from typing import Any, List

from langchain_core.tools import tool

from data import indicators

# 金融场景常见的正负面词
_POSITIVE = (
    "增长", "上涨", "涨停", "突破", "中标", "增持", "回购", "扭亏", "超预期",
    "创新高", "利好", "签约", "获批", "提速", "扩张", "新高", "上调",
)
_NEGATIVE = (
    "下滑", "下跌", "跌停", "亏损", "减持", "违规", "处罚", "诉讼", "退市",
    "风险", "预警", "商誉", "质押", "终止", "失利", "下调", "新低", "解禁",
)


def _score(title: str) -> int:
    hit_pos = sum(1 for word in _POSITIVE if word in title)
    hit_neg = sum(1 for word in _NEGATIVE if word in title)
    return hit_pos - hit_neg


def build_news_tools(service) -> List[Any]:
    """构建消息面工具集。"""

    @tool
    def get_recent_news(ticker: str, limit: int = 5) -> str:
        """获取个股近期新闻标题与发布时间。"""
        return service.get_news(ticker, int(limit))

    @tool
    def filter_news(ticker: str, keyword: str = "", limit: int = 20) -> str:
        """按关键词过滤个股新闻，返回命中的标题（用于验证某个事件是否被报道）。"""
        if not keyword:
            return "请提供要检索的关键词，例如 keyword=\"回购\"。"
        items = service.get_news_items(ticker, int(limit))
        hits = [item for item in items if keyword in str(item.get("title", ""))]
        if not hits:
            return f"[新闻检索] 在 {ticker} 最近 {len(items)} 条新闻中未找到包含「{keyword}」的内容。"
        lines = [f"[新闻检索] {ticker} 命中「{keyword}」的新闻 {len(hits)} 条："]
        lines += [f"- {item.get('title')}" for item in hits]
        return "\n".join(lines)

    @tool
    def score_news_sentiment(ticker: str, limit: int = 20) -> str:
        """对近期新闻做情绪打分（金融正负面词典 + 规则法），返回偏向与命中明细。"""
        items = service.get_news_items(ticker, int(limit))
        if not items:
            return f"[新闻情绪] {ticker} 暂无新闻可打分。"
        scored = [(_score(str(item.get("title", ""))), str(item.get("title", ""))) for item in items]
        total = sum(score for score, _ in scored)
        positive = sum(1 for score, _ in scored if score > 0)
        negative = sum(1 for score, _ in scored if score < 0)
        if total > 0:
            bias = "偏正面"
        elif total < 0:
            bias = "偏负面"
        else:
            bias = "中性"
        highlights = [title for score, title in scored if score != 0][:5]
        lines = [
            f"[新闻情绪] {ticker}（{len(items)} 条新闻，总得分 {total}，{bias}）",
            f"- 正面 {positive} 条 / 负面 {negative} 条 / 中性 {len(items) - positive - negative} 条",
        ]
        if highlights:
            lines.append("- 情绪显著标题：")
            lines += [f"    · {title}" for title in highlights]
        return "\n".join(lines)

    @tool
    def get_market_environment(index_code: str = "sh000001", days: int = 60) -> str:
        """获取大盘指数表现与均线状态，作为个股消息面的市场环境基准。"""
        frame = service.get_index_history(index_code, int(days))
        snapshot = indicators.latest_snapshot(frame)
        close = float(frame["close"].iloc[-1])
        change = (close / float(frame["close"].iloc[0]) - 1.0) * 100.0
        return (
            f"[市场环境{service.source_label(index_code)}] {index_code} 近 {len(frame)} 个交易日\n"
            f"- 最新点位 {close:.2f}，区间涨跌 {change:+.2f}%\n"
            f"- 均线形态：{snapshot.get('ma_trend', '数据不足')}\n"
            f"- RSI(14)={snapshot.get('rsi14')}（{snapshot.get('rsi_zone', '—')}）"
        )

    return [get_recent_news, filter_news, score_news_sentiment, get_market_environment]
