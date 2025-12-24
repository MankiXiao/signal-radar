# sources/reddit_rss.py
import feedparser

def fetch_reddit_items(feeds, max_items=50):
    items = []
    for url in feeds:
        feed = feedparser.parse(url)
        for e in feed.entries[:max_items]:
            items.append({
                "source": "reddit",
                "feed": url,
                "title": getattr(e, "title", "") or "",
                "link": getattr(e, "link", "") or "",
                "published": getattr(e, "published", "") or ""
            })
    return items
