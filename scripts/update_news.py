#!/usr/bin/env python3
"""Refresh data/news.json from a broader set of public sources.

Collection strategy
-------------------
1. Direct RSS/RDF from public institutions and local media.
2. Google News search RSS for publishers that do not expose a stable public feed.
3. No article-body scraping. We store headline, source, link and a short neutral notice.
4. Exact/near-exact duplicates are collapsed before publishing.
5. Failure of any source never wipes the existing dataset.

The collector intentionally favors source links over copied text. Always confirm
important information at the original source.
"""
from __future__ import annotations

import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "news.json"
JST = timezone(timedelta(hours=9))
MAX_ITEMS = 240
MAX_AGE_DAYS = 45
USER_AGENT = "IwakiNow/2.0 (+public headline aggregator; contact site operator)"

# Stable public feeds whose URLs have been verified from the sites themselves.
DIRECT_FEEDS = [
    {
        "name": "いわき市・新着情報",
        "source": "いわき市",
        "url": "https://www.city.iwaki.lg.jp/www/rss/news.rdf",
        "kind": "official",
        "group": "行政",
    },
    {
        "name": "いわき市・トピックス",
        "source": "いわき市",
        "url": "https://www.city.iwaki.lg.jp/www/rss/topics.rdf",
        "kind": "official",
        "group": "行政",
    },
    {
        "name": "いわき市・募集情報",
        "source": "いわき市",
        "url": "https://www.city.iwaki.lg.jp/www/rss/bosyu.rdf",
        "kind": "official",
        "group": "行政",
    },
    {
        "name": "福島県・いわき地方振興局",
        "source": "福島県 いわき地方振興局",
        "url": "https://www.pref.fukushima.lg.jp/rss/10/sec-3-29.xml",
        "kind": "official",
        "group": "行政",
    },
    {
        "name": "いわき民報",
        "source": "いわき民報",
        "url": "https://iwaki-minpo.co.jp/feed/",
        "kind": "media",
        "group": "報道",
    },
]

# Google News search RSS lets us discover headlines from outlets that have no
# convenient public RSS endpoint. The resulting item keeps the publisher name.
# These queries are intentionally few and broad to avoid hammering the service.
GOOGLE_NEWS_QUERIES = [
    {
        "name": "いわき総合ニュース",
        "query": '"いわき市" when:14d',
        "group": "報道",
    },
    {
        "name": "13地域ニュース",
        "query": '(小名浜 OR 勿来 OR 四倉 OR 内郷 OR 好間 OR 常磐 OR 湯本 OR 久之浜 OR 大久 OR 遠野 OR 小川 OR 三和 OR 田人 OR 川前) いわき when:14d',
        "group": "地域",
    },
    {
        "name": "いわきFC",
        "query": '"いわきFC" when:30d',
        "group": "スポーツ",
    },
    {
        "name": "いわき観光",
        "query": 'いわき (観光 OR イベント OR 祭り OR 海水浴 OR アリオス) when:30d',
        "group": "観光・文化",
    },
    {
        "name": "いわき経済",
        "query": 'いわき (企業 OR 工場 OR 商工 OR 開店 OR 閉店 OR 雇用 OR 求人) when:30d',
        "group": "経済",
    },
    {
        "name": "地域団体サイト",
        "query": 'いわき (site:iwakifc.com OR site:kankou-iwaki.or.jp OR site:iwaki-alios.jp OR site:iwakicci.or.jp) when:45d',
        "group": "地域団体",
    },
]

BLOCK_TERMS = ("お悔やみ", "訃報", "葬儀", "死亡広告")
BLOCK_SOURCES = ("PR TIMES", "valuepress", "アットプレス")
RELEVANCE_TERMS = (
    "いわき", "小名浜", "勿来", "四倉", "内郷", "好間", "湯本", "常磐",
    "久之浜", "久ノ浜", "大久", "遠野", "小川町", "小川郷", "三和町", "田人", "川前",
)

AREA_RULES = [
    ("久之浜・大久", ("久之浜", "久ノ浜", "大久町", "大久村")),
    ("四倉", ("四倉", "四ツ倉")),
    ("小名浜", ("小名浜", "江名", "泉町", "鹿島町")),
    ("勿来", ("勿来", "植田", "錦町", "山田町", "川部町")),
    ("常磐", ("常磐", "湯本", "藤原町")),
    ("内郷", ("内郷",)),
    ("遠野", ("遠野",)),
    ("小川", ("小川町", "小川郷")),
    ("好間", ("好間",)),
    ("三和", ("三和町", "三和地区")),
    ("田人", ("田人",)),
    ("川前", ("川前",)),
    ("平", ("平商店街", "いわき駅", "いわき平", "平地区", "平字", "平競輪", "平市")),
]

CATEGORY_RULES = [
    ("防災・安全", ("地震", "津波", "避難", "防災", "火災", "クマ", "熊", "警察", "事故", "警戒アラート", "大雨", "洪水", "停電")),
    ("教育・子育て", ("学校", "教育", "子育て", "保育", "児童", "生徒", "講座", "公民館", "高校", "大学")),
    ("スポーツ", ("いわきFC", "サッカー", "野球", "競輪", "スポーツ", "大会", "アスリート", "J2", "Jリーグ")),
    ("イベント", ("祭", "まつり", "イベント", "開催", "展示", "コンサート", "花火", "おどり", "フェス", "アリオス")),
    ("経済", ("企業", "工場", "商工", "産業", "雇用", "求人", "経営", "事業者", "観光事業者", "開店", "閉店", "出店")),
    ("市政", ("市長", "市議会", "議会", "選挙", "条例", "予算", "市政", "職員採用", "協定", "市役所")),
]

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
TITLE_PUNCT_RE = re.compile(r"[\s　\-―ー:：|｜/／・]+")


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def text_of(elem: ET.Element, names: tuple[str, ...]) -> str:
    for child in list(elem):
        if local(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def element_of(elem: ET.Element, names: tuple[str, ...]) -> ET.Element | None:
    for child in list(elem):
        if local(child.tag) in names:
            return child
    return None


def clean(text: str) -> str:
    text = html.unescape(text or "")
    text = TAG_RE.sub(" ", text)
    return SPACE_RE.sub(" ", text).strip()


def parse_date(value: str) -> datetime:
    if not value:
        return datetime.now(JST)
    try:
        dt = parsedate_to_datetime(value)
    except Exception:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return datetime.now(JST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def classify_area(title: str, summary: str) -> str:
    hay = f"{title} {summary}"
    for area, keys in AREA_RULES:
        if any(k in hay for k in keys):
            return area
    return "全市"


def classify_category(title: str, summary: str) -> str:
    hay = f"{title} {summary}"
    for category, keys in CATEGORY_RULES:
        if any(k in hay for k in keys):
            return category
    return "暮らし"


def fetch_xml(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/rdf+xml, application/xml, text/xml;q=0.9, */*;q=0.5"})
    with urllib.request.urlopen(req, timeout=25) as res:
        return res.read()


def google_news_url(query: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({
        "q": query,
        "hl": "ja",
        "gl": "JP",
        "ceid": "JP:ja",
    })


def parse_feed(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    entries: list[dict] = []
    for elem in root.iter():
        if local(elem.tag) not in ("item", "entry"):
            continue
        title = clean(text_of(elem, ("title",)))
        if not title:
            continue
        link = text_of(elem, ("link",))
        if not link:
            for child in list(elem):
                if local(child.tag) == "link" and child.attrib.get("href"):
                    link = child.attrib["href"].strip()
                    break
        description = clean(text_of(elem, ("description", "summary", "content")))
        date_value = text_of(elem, ("pubdate", "date", "published", "updated"))
        source_elem = element_of(elem, ("source",))
        source_name = clean(source_elem.text if source_elem is not None and source_elem.text else "")
        source_home = source_elem.attrib.get("url", "").strip() if source_elem is not None else ""
        entries.append({
            "title": title,
            "link": link,
            "description": description,
            "published": parse_date(date_value),
            "source_name": source_name,
            "source_home": source_home,
        })
    return entries


def load_existing() -> dict:
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
    return {"generatedAt": None, "news": []}


def make_id(url: str, title: str) -> str:
    return hashlib.sha256(f"{url}|{title}".encode()).hexdigest()[:18]


def title_key(title: str) -> str:
    s = html.unescape(title).lower()
    s = re.sub(r"[【】〖〗「」『』\[\]()（）]", "", s)
    s = TITLE_PUNCT_RE.sub("", s)
    return s[:180]


def strip_google_source_suffix(title: str, source: str) -> str:
    if source and title.endswith(f" - {source}"):
        return title[: -(len(source) + 3)].strip()
    return title


def is_relevant(title: str, description: str) -> bool:
    hay = f"{title} {description}"
    return any(term in hay for term in RELEVANCE_TERMS)


def short_official_summary(description: str, title: str, source: str) -> str:
    if not description:
        return f"{source}から「{title}」に関する新着情報が公開されました。詳しくは公式情報をご確認ください。"
    description = description[:180].rstrip("。、 ")
    return description + ("。" if description and not description.endswith(("。", "！", "？")) else "")


def headline_summary(source: str, title: str) -> str:
    return f"{source}が「{title}」に関する情報を公開しています。詳細は出典元でご確認ください。"


def upsert(existing: dict[str, dict], item: dict, *, prefer: bool = False) -> None:
    """Insert by id, and collapse exact normalized-title duplicates.

    Direct RSS is preferred over a Google News redirect for the same headline.
    """
    key = title_key(item["title"])
    duplicates = [k for k, v in existing.items() if title_key(str(v.get("title", ""))) == key and key]
    if duplicates:
        current_key = duplicates[0]
        current = existing[current_key]
        if prefer or current.get("via") == "google-news":
            del existing[current_key]
            existing[item["id"]] = item
        return
    existing[item["id"]] = item


def collect_direct(existing: dict[str, dict], statuses: list[dict]) -> int:
    successes = 0
    for feed in DIRECT_FEEDS:
        try:
            items = parse_feed(fetch_xml(feed["url"]))
            successes += 1
            statuses.append({"name": feed["name"], "group": feed["group"], "ok": True, "items": len(items)})
            print(f"{feed['name']}: {len(items)} items")
        except Exception as exc:
            statuses.append({"name": feed["name"], "group": feed["group"], "ok": False, "items": 0, "error": str(exc)[:140]})
            print(f"WARN {feed['name']}: {exc}")
            continue

        for raw_item in items:
            title = raw_item["title"]
            if any(term in title for term in BLOCK_TERMS):
                continue
            url = raw_item["link"]
            if not url.startswith(("http://", "https://")):
                continue
            source = feed["source"]
            summary = short_official_summary(raw_item["description"], title, source) if feed["kind"] == "official" else headline_summary(source, title)
            item_id = make_id(url, title)
            upsert(existing, {
                "id": item_id,
                "title": title,
                "summary": summary,
                "category": classify_category(title, summary),
                "area": classify_area(title, summary),
                "publishedAt": raw_item["published"].isoformat(timespec="seconds"),
                "source": source,
                "sourceUrl": url,
                "sourceGroup": feed["group"],
                "via": "direct-rss",
                "note": "公開RSSから自動取得。内容は必ず出典元で確認してください。",
            }, prefer=True)
    return successes


def collect_google_news(existing: dict[str, dict], statuses: list[dict]) -> int:
    successes = 0
    for query in GOOGLE_NEWS_QUERIES:
        url = google_news_url(query["query"])
        try:
            items = parse_feed(fetch_xml(url))
            successes += 1
            statuses.append({"name": query["name"], "group": query["group"], "ok": True, "items": len(items)})
            print(f"Google News/{query['name']}: {len(items)} items")
        except Exception as exc:
            statuses.append({"name": query["name"], "group": query["group"], "ok": False, "items": 0, "error": str(exc)[:140]})
            print(f"WARN Google News/{query['name']}: {exc}")
            continue

        for raw_item in items:
            source = raw_item["source_name"] or "Google News掲載媒体"
            if any(block.lower() in source.lower() for block in BLOCK_SOURCES):
                continue
            title = strip_google_source_suffix(raw_item["title"], source)
            if any(term in title for term in BLOCK_TERMS):
                continue
            if not is_relevant(title, raw_item["description"]):
                continue
            item_url = raw_item["link"]
            if not item_url.startswith(("http://", "https://")):
                continue
            summary = headline_summary(source, title)
            item_id = make_id(item_url, title)
            upsert(existing, {
                "id": item_id,
                "title": title,
                "summary": summary,
                "category": classify_category(title, summary),
                "area": classify_area(title, summary),
                "publishedAt": raw_item["published"].isoformat(timespec="seconds"),
                "source": source,
                "sourceUrl": item_url,
                "sourceHome": raw_item["source_home"],
                "sourceGroup": query["group"],
                "via": "google-news",
                "note": "Google News検索RSSから見出しを自動取得。リンク先の原媒体で内容をご確認ください。",
            })
    return successes


def main() -> int:
    old = load_existing()
    existing = {
        str(n.get("id") or make_id(str(n.get("sourceUrl", "")), str(n.get("title", "")))): n
        for n in old.get("news", [])
    }
    statuses: list[dict] = []

    successful_sources = collect_direct(existing, statuses)
    successful_sources += collect_google_news(existing, statuses)

    if not successful_sources:
        print("No sources fetched successfully; keeping existing file unchanged.")
        return 0

    cutoff = datetime.now(JST) - timedelta(days=MAX_AGE_DAYS)
    items: list[dict] = []
    for n in existing.values():
        try:
            dt = parse_date(str(n.get("publishedAt", "")))
        except Exception:
            dt = datetime.now(JST)
        if dt >= cutoff:
            items.append(n)

    # Second pass dedupe for pre-existing items and cross-source imports.
    seen: set[str] = set()
    deduped: list[dict] = []
    for n in sorted(items, key=lambda x: parse_date(str(x.get("publishedAt", ""))), reverse=True):
        key = title_key(str(n.get("title", "")))
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        deduped.append(n)

    items = deduped[:MAX_ITEMS]
    active_sources = sorted({str(n.get("source", "")) for n in items if n.get("source")})
    payload = {
        "generatedAt": datetime.now(JST).isoformat(timespec="seconds"),
        "collectorVersion": "2.0",
        "sourceCount": len(active_sources),
        "sources": active_sources,
        "collectorStatus": statuses,
        "news": items,
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(f"Wrote {len(items)} items from {len(active_sources)} publishers to {DATA_FILE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
