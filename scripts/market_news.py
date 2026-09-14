"""Fetch and lightly dedupe headlines from public RSS feeds - reused as-is
from the sibling Internship_Tracker project's market_brief feature, just
pointed at per-symbol feed URLs instead of a fixed market-wide list.
"""
from __future__ import annotations
import feedparser

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def fetch_headlines(
    feeds: list[dict], per_feed: int = 8, total_limit: int = 30
) -> list[dict]:
    """Pull the newest few entries from each feed, deduping near-identical
    titles and logging (not raising on) any feed that fails so one dead feed
    doesn't sink the run.
    """
    seen_titles = set()
    headlines = []
    for feed in feeds:
        try:
            parsed = feedparser.parse(
                feed["url"], request_headers={"User-Agent": USER_AGENT}
            )
            for entry in parsed.entries[:per_feed]:
                title = entry.get("title", "").strip()
                if not title:
                    continue
                key = title.lower()[:60]
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                headlines.append(
                    {
                        "source": feed["name"],
                        "title": title,
                        "summary": entry.get("summary", "")[:220],
                        "link": entry.get("link", ""),
                    }
                )
        except Exception as e:
            print(f"market_news: failed to fetch {feed['name']}: {e}")
    return headlines[:total_limit]


def fetch_symbol_headlines(symbol: str, feed_url_template: str, limit: int = 5) -> list[dict]:
    """Convenience wrapper: fetch headlines for a single ticker via a Google
    News RSS search URL (or any template with a {symbol} placeholder).
    """
    feed = {"name": f"{symbol} news", "url": feed_url_template.format(symbol=symbol)}
    return fetch_headlines([feed], per_feed=limit, total_limit=limit)
