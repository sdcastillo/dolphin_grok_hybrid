import asyncio
import feedparser
import httpx
import re
from html import unescape
from supabase import create_client
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import os
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_KEY")
TWITTER_API_KEY = os.getenv("TWITTER_API_KEY")
TWITTER_TRENDS_URL = os.getenv(
    "TWITTER_TRENDS_URL",
    "https://api.twitterapi.io/twitter/trends",
)
TWITTER_TRENDS_WOEID = int(os.getenv("TWITTER_TRENDS_WOEID", "23424977"))
NEWS_RSS_PER_FEED = int(os.getenv("NEWS_RSS_PER_FEED", "3"))
# Drop stale RSS items (CNN topstories still lists 2023 Dominion live-blog entries).
NEWS_RSS_MAX_AGE_HOURS = float(os.getenv("NEWS_RSS_MAX_AGE_HOURS", "72"))
# Scan deeper than per-feed limit so old sticky items don't crowd out fresh ones.
NEWS_RSS_SCAN_MULTIPLIER = int(os.getenv("NEWS_RSS_SCAN_MULTIPLIER", "8"))
NEWS_RSS_REQUIRE_DATE = os.getenv("NEWS_RSS_REQUIRE_DATE", "true").lower() in ("1", "true", "yes")
NEWS_RSS_FEEDS: list[tuple[str, str]] = [
    ("cnn", "http://rss.cnn.com/rss/cnn_topstories.rss"),
    ("bbc", "http://feeds.bbci.co.uk/news/rss.xml"),
    ("nytimes", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
    # Reuters/AP native RSS endpoints are often blocked; use Google News site feeds.
    ("reuters", "https://news.google.com/rss/search?q=site:reuters.com&hl=en-US&gl=US&ceid=US:en"),
    ("ap", "https://news.google.com/rss/search?q=site:apnews.com&hl=en-US&gl=US&ceid=US:en"),
    ("npr", "https://feeds.npr.org/1001/rss.xml"),
    ("fox_news", "http://feeds.foxnews.com/foxnews/latest"),
    ("google_news", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"),
    ("yahoo_news", "http://news.yahoo.com/rss/"),
]
TRENDS_FETCH_INTERVAL_MINUTES = float(os.getenv("TRENDS_FETCH_INTERVAL_MINUTES", "240"))
TRENDS_FETCH_LIMIT = int(os.getenv("TRENDS_FETCH_LIMIT", "30"))
TRENDS_SCHEDULER_ENABLED = os.getenv("TRENDS_SCHEDULER_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
)

_scheduler_task: asyncio.Task | None = None
_supabase = None


def _supabase_client():
    global _supabase
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY are required for news storage")
    if _supabase is None:
        _supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    return _supabase


def _parse_twitter_trends(payload) -> list[dict]:
    if isinstance(payload, list):
        trends = payload
    elif isinstance(payload, dict):
        trends = (
            payload.get("trends")
            or payload.get("data")
            or payload.get("results")
            or []
        )
    else:
        trends = []

    parsed = []
    for trend in trends:
        if not isinstance(trend, dict):
            continue
        name = trend.get("name") or trend.get("topic") or trend.get("title")
        if not name:
            continue
        parsed.append(
            {
                "name": name,
                "tweet_volume": trend.get("tweet_volume") or trend.get("volume"),
                "source": "x_trending",
            }
        )
    return parsed



def _parse_published(value) -> datetime | None:
    """Best-effort parse of RSS published/updated timestamps to aware UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(value, (list, tuple)) and len(value) >= 6:
        # feedparser published_parsed / updated_parsed
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except Exception:
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    try:
        dt = parsedate_to_datetime(raw)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _parse_date_from_url(url: str | None) -> datetime | None:
    """Infer a publish day from common CMS URL patterns."""
    if not url:
        return None
    # /2023/04/17/ or _2023-04-17 or -04-18-23 (CNN live-news)
    patterns = [
        r"/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|$)",
        r"(20\d{2})-(\d{1,2})-(\d{1,2})",
        r"(?:^|/)(\d{1,2})-(\d{1,2})-(\d{2})(?:/|$)",  # MM-DD-YY
    ]
    for i, pat in enumerate(patterns):
        m = re.search(pat, url)
        if not m:
            continue
        try:
            a, b, c = m.groups()
            if i == 2:
                month, day, yy = int(a), int(b), int(c)
                year = 2000 + yy if yy < 100 else yy
            else:
                year, month, day = int(a), int(b), int(c)
            return datetime(year, month, day, tzinfo=timezone.utc)
        except Exception:
            continue
    # Year-only fallback in path: /2023/
    m = re.search(r"/(20\d{2})(?:/|$)", url)
    if m:
        try:
            year = int(m.group(1))
            # Mid-year stand-in; age filter still rejects old years quickly.
            return datetime(year, 6, 15, tzinfo=timezone.utc)
        except Exception:
            return None
    return None


def _resolve_published(published, published_parsed=None, link: str | None = None) -> datetime | None:
    return (
        _parse_published(published_parsed)
        or _parse_published(published)
        or _parse_date_from_url(link)
    )


def _is_fresh(published, max_age_hours: float | None = None, *, require_date: bool | None = None) -> bool:
    """True when the story is within the max-age window.

    Undated items are dropped when require_date is true (default), because some
    feeds (notably CNN topstories) keep ancient live-blog entries without dates.
    """
    hours = NEWS_RSS_MAX_AGE_HOURS if max_age_hours is None else max_age_hours
    must_date = NEWS_RSS_REQUIRE_DATE if require_date is None else require_date
    dt = _parse_published(published) if not isinstance(published, datetime) else (
        published if published.tzinfo else published.replace(tzinfo=timezone.utc)
    )
    # Allow callers to pass an already-resolved datetime via published.
    if dt is None and isinstance(published, datetime):
        dt = published
    if dt is None:
        return not must_date
    if hours <= 0:
        return True
    age = datetime.now(timezone.utc) - dt.astimezone(timezone.utc)
    return age <= timedelta(hours=hours)


def _strip_html(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"<[^>]+>", " ", text)
    return unescape(re.sub(r"\s+", " ", cleaned)).strip()


def fetch_news(feed_url: str, source: str | None = None, limit: int = 10) -> list[dict]:
    """Parse an RSS/Atom feed into story dicts (sync; use httpx fetch for remote URLs)."""
    feed = feedparser.parse(feed_url)
    feed_source = source or getattr(feed.feed, "title", "rss")
    stories: list[dict] = []
    scan = max(limit * max(1, NEWS_RSS_SCAN_MULTIPLIER), limit)

    for entry in feed.entries[:scan]:
        title = getattr(entry, "title", None)
        if not title:
            continue
        link = getattr(entry, "link", None)
        published = (
            entry.get("published")
            or entry.get("updated")
            or ""
        ) or None
        published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        published_dt = _resolve_published(published, published_parsed, link)
        if not _is_fresh(published_dt):
            continue
        stories.append(
            {
                "name": title.strip(),
                "title": title.strip(),
                "link": link,
                "summary": _strip_html(entry.get("summary", ""))[:500] or None,
                "published": published or (published_dt.isoformat() if published_dt else None),
                "source": feed_source,
                "source_title": getattr(feed.feed, "title", feed_source),
                "tweet_volume": None,
                "_published_dt": published_dt,
            }
        )

    stories.sort(
        key=lambda s: s.get("_published_dt") or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    for s in stories:
        s.pop("_published_dt", None)
    return stories[:limit]


def _parse_feed_content(content: bytes | str, source: str, limit: int) -> list[dict]:
    return fetch_news(content, source=source, limit=limit)


async def _fetch_single_rss(
    client: httpx.AsyncClient,
    source: str,
    url: str,
    per_feed: int,
) -> list[dict]:
    try:
        response = await client.get(url, follow_redirects=True)
        response.raise_for_status()
        return await asyncio.to_thread(_parse_feed_content, response.content, source, per_feed)
    except Exception as exc:
        print(f"RSS fetch failed ({source}): {exc}")
        return []


async def _fetch_rss_trends(per_feed: int | None = None) -> list[dict]:
    """Fetch headlines from major news RSS feeds."""
    feed_limit = per_feed if per_feed is not None else NEWS_RSS_PER_FEED
    headers = {"User-Agent": "AlphaPredictiveInsights/1.0 (+news-rss-fetcher)"}

    async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
        results = await asyncio.gather(
            *[
                _fetch_single_rss(client, source, url, feed_limit)
                for source, url in NEWS_RSS_FEEDS
            ]
        )

    combined: list[dict] = []
    seen: set[str] = set()
    for batch in results:
        for item in batch:
            name = item["name"]
            if name in seen:
                continue
            seen.add(name)
            combined.append(item)
    return combined


async def _fetch_fallback_trends(limit: int = 10) -> list[dict]:
    """RSS headlines, with Hacker News as a last-resort supplement."""
    trends = await _fetch_rss_trends()
    if trends:
        return trends[:limit]

    async with httpx.AsyncClient(timeout=20.0) as client:
        hn = await client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"tags": "front_page", "hitsPerPage": limit},
        )
        hn.raise_for_status()
        return [
            {
                "name": hit.get("title") or hit.get("story_title"),
                "tweet_volume": hit.get("points"),
                "source": "hackernews",
            }
            for hit in hn.json().get("hits", [])
            if hit.get("title") or hit.get("story_title")
        ][:limit]


def _save_trends(trends: list[dict]) -> list[dict]:
    saved = []
    fetched_at = datetime.now(timezone.utc).isoformat()

    for trend in trends:
        row = {
            "topic": trend.get("name") or trend.get("title"),
            "tweet_volume": trend.get("tweet_volume"),
            "fetched_at": fetched_at,
            "summary": trend.get("summary"),
            "link": trend.get("link"),
            "published": trend.get("published"),
            "source": trend.get("source", "x_trending"),
        }
        try:
            _supabase_client().table("news_trends").upsert(
                row,
                on_conflict="topic,source",
            ).execute()
        except Exception:
            from direct_db import execute_write

            execute_write(
                """
                INSERT INTO news_trends (topic, tweet_volume, fetched_at, summary, link, published, source)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (topic, source)
                DO UPDATE SET
                    tweet_volume = EXCLUDED.tweet_volume,
                    fetched_at = EXCLUDED.fetched_at,
                    summary = EXCLUDED.summary,
                    link = EXCLUDED.link,
                    published = EXCLUDED.published
                """,
                (
                    row["topic"],
                    row["tweet_volume"],
                    fetched_at,
                    row["summary"],
                    row["link"],
                    row["published"],
                    row["source"],
                ),
            )
        saved.append(row)

    return saved


def _merge_trends(*groups: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for item in group:
            name = item.get("name")
            if not name or name in seen:
                continue
            seen.add(name)
            merged.append(item)
    return merged


async def fetch_trending_topics(limit: int = 10) -> dict:
    """Fetch trending topics and upsert them into Supabase `news_trends`."""
    try:
        sources_used: list[str] = []
        twitter_trends: list[dict] = []
        rss_trends = await _fetch_rss_trends()

        if rss_trends:
            sources_used.append("rss")

        if TWITTER_API_KEY:
            try:
                async with httpx.AsyncClient(timeout=20.0) as client:
                    response = await client.get(
                        TWITTER_TRENDS_URL,
                        headers={"x-api-key": TWITTER_API_KEY},
                        params={"woeid": TWITTER_TRENDS_WOEID, "count": max(30, limit)},
                    )
                    response.raise_for_status()
                    twitter_trends = _parse_twitter_trends(response.json())[:limit]
                    if twitter_trends:
                        sources_used.append("x_trending")
            except Exception as exc:
                print(f"Twitter trends API failed ({exc}); using RSS sources")

        trends = _merge_trends(twitter_trends, rss_trends)
        if not trends:
            trends = await _fetch_fallback_trends(limit)
            sources_used.append("fallback")

        saved = _save_trends(trends)
        print(f"✅ Trending topics saved ({len(saved)} from {', '.join(sources_used) or 'none'})")
        return {
            "saved": len(saved),
            "source": "+".join(sources_used) if sources_used else "none",
            "topics": saved[:limit],
        }
    except Exception as e:
        print("Error fetching trends:", e)
        return {"saved": 0, "error": str(e), "topics": []}


async def fetch_trending_headlines(limit: int = 10) -> list[str]:
    result = await fetch_trending_topics(limit=limit)
    return [item["topic"] for item in result.get("topics", []) if item.get("topic")]


def _filter_fresh_rows(rows: list, limit: int) -> list[dict]:
    """Normalize DB rows and drop stale published headlines."""
    out: list[dict] = []
    for row in rows or []:
        if isinstance(row, dict):
            item = row
        elif isinstance(row, (list, tuple)) and len(row) >= 8:
            item = {
                "id": row[0],
                "topic": row[1],
                "tweet_volume": row[2],
                "summary": row[3],
                "link": row[4],
                "published": row[5],
                "source": row[6],
                "fetched_at": row[7],
            }
        else:
            continue
        published_dt = _resolve_published(item.get("published"), link=item.get("link"))
        if not _is_fresh(published_dt):
            continue
        out.append(item)
        if len(out) >= limit:
            break
    return out


def get_stored_trends(limit: int = 20) -> list[dict]:
    # Over-fetch then age-filter so sticky old CNN items cannot dominate prompt chips.
    fetch_n = max(limit * 5, 40)
    try:
        result = (
            _supabase_client().table("news_trends")
            .select("id, topic, tweet_volume, summary, link, published, source, fetched_at")
            .order("fetched_at", desc=True)
            .limit(fetch_n)
            .execute()
        )
        return _filter_fresh_rows(result.data or [], limit)
    except Exception:
        try:
            from direct_db import execute
            rows = execute(
                """
                SELECT id, topic, tweet_volume, summary, link, published, source, fetched_at
                FROM news_trends
                ORDER BY fetched_at DESC
                LIMIT %s
                """,
                (fetch_n,),
            ) or []
            return _filter_fresh_rows(rows, limit)
        except Exception:
            return []


def get_trending_context(limit: int = 8) -> str:
    """Build a short system-prompt snippet from the latest stored trends."""
    try:
        trends = get_stored_trends(limit=limit)
    except Exception:
        return ""
    topics: list[str] = []
    seen: set[str] = set()
    for row in trends:
        topic = (row.get("topic") or "").strip() if hasattr(row, 'get') else (row[1] if isinstance(row, (list, tuple)) else "")
        if not topic or topic in seen:
            continue
        seen.add(topic)
        topics.append(topic)
    if not topics:
        return ""
    bullets = "\n".join(f"- {topic}" for topic in topics)
    return (
        "You have access to freshly fetched news headlines. "
        "When the user asks about current events, today's news, or trending topics, "
        "answer using ONLY these headlines. Do not invent stories or rely on outdated training data.\n"
        f"Current news headlines:\n{bullets}"
    )


async def run_trending_scheduler(
    interval_minutes: float | None = None,
    limit: int | None = None,
) -> None:
    """Fetch trending topics immediately, then on a fixed interval."""
    minutes = interval_minutes if interval_minutes is not None else TRENDS_FETCH_INTERVAL_MINUTES
    fetch_limit = limit if limit is not None else TRENDS_FETCH_LIMIT
    interval_seconds = max(60.0, minutes * 60.0)

    while True:
        try:
            result = await fetch_trending_topics(limit=fetch_limit)
            if result.get("error"):
                print(f"⚠️ Trending scheduler: {result['error']}")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"⚠️ Trending scheduler error: {exc}")

        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            break


def start_trending_scheduler(
    interval_minutes: float | None = None,
    limit: int | None = None,
) -> asyncio.Task:
    """Start the background trends fetch loop."""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return _scheduler_task
    _scheduler_task = asyncio.create_task(
        run_trending_scheduler(interval_minutes=interval_minutes, limit=limit)
    )
    minutes = interval_minutes if interval_minutes is not None else TRENDS_FETCH_INTERVAL_MINUTES
    print(f"📅 Trending scheduler started (every {minutes:g} min)")
    return _scheduler_task


async def stop_trending_scheduler() -> None:
    """Cancel the background trends fetch loop."""
    global _scheduler_task
    if _scheduler_task is None:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None