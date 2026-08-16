#!/usr/bin/env python3
"""Refresh data/news.json from a broader set of public sources.

Collection strategy
-------------------
1. Direct RSS/RDF from public institutions and local media.
2. Google News search RSS for publishers that do not expose a stable public feed.
3. Event dates are enriched from public article/official pages without republishing body text.
4. Iwaki City event calendars and the official tourism event listing are collected directly.
5. Exact/near-exact duplicates are collapsed before publishing.
6. Failure of any source never wipes the existing dataset.

The collector intentionally favors source links over copied text. Always confirm
important information at the original source.
"""
from __future__ import annotations

import difflib
import hashlib
import html
import json
import re
import sys
import urllib.parse
import urllib.request
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "news.json"
NEWS_DIR = ROOT / "news"
JST = timezone(timedelta(hours=9))
MAX_ITEMS = 240
MAX_AGE_DAYS = 45
USER_AGENT = "IwakiNow/5.0 (+public headline/event-date aggregator; contact site operator)"
EVENT_PAGE_FETCH_LIMIT = 36
EVENT_DATE_RECHECK_HOURS = 24
MAX_HTML_BYTES = 2_500_000
CITY_CALENDAR_BASE = "https://www.city.iwaki.lg.jp/www/genre/1000100000345/"
CITY_EVENT_GENRE_URL = "https://www.city.iwaki.lg.jp/www/genre/1452741939257/index.html"
TOURISM_EVENT_LIST_URL = "https://kankou-iwaki.or.jp/event"
CITY_CALENDAR_ALLOWED_TYPES = ("イベント・祭り", "講座・講演", "スポーツ・健康", "文化・芸術", "子育て", "その他")
DATE_CUES = ("開催日", "開催期間", "イベント開催期間", "開催日時", "日時", "日程", "会期", "期間")
DATE_META_EXCLUDES = ("登録日", "更新日", "掲載日", "公開日", "投稿日", "記事公開", "最終更新")

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
    {
        "name": "いわき開店・閉店",
        "query": 'いわき (開店 OR 閉店 OR オープン OR 新店舗 OR 新店 OR 移転 OR リニューアル) when:45d',
        "group": "開店・閉店",
    },
    {
        "name": "いわき週末イベント",
        "query": 'いわき (イベント OR 祭り OR まつり OR 花火 OR フェス OR コンサート OR マルシェ OR 展示 OR 公演 OR 盆踊り) when:30d',
        "group": "イベント",
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

BREAKING_TERMS = (
    "避難指示", "緊急安全確保", "津波", "震度", "地震", "大雨警報", "洪水警報",
    "土砂災害", "火災", "通行止め", "運休", "断水", "停電", "クマ", "熊",
)
IMPORTANT_TERMS = (
    "市長", "市議会", "選挙", "予算", "条例", "医療", "病院", "学校", "休校",
    "道路", "水道", "給付", "補助", "開店", "閉店", "いわきFC",
)

OPENING_CLOSING_TERMS = (
    "開店", "閉店", "オープン", "OPEN", "新店舗", "新店", "新規出店", "移転オープン",
    "リニューアルオープン", "営業終了", "閉館", "閉鎖",
)
OPENING_CLOSING_EXCLUDE_TERMS = (
    "オープンキャンパス", "オープンデータ", "オープンイノベーション", "オープン戦",
    "オープン大会", "オープン講座",
)
EVENT_TERMS = (
    "イベント", "祭", "まつり", "花火", "フェス", "コンサート", "マルシェ", "展示",
    "展覧会", "公演", "盆踊り", "七夕", "おどり", "ワークショップ", "開催",
)

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


def fetch_html(url: str) -> tuple[str, str]:
    """Fetch a public HTML page, returning decoded HTML and the final URL.

    Only a bounded prefix is read. The collector extracts dates/links and never
    republishes the fetched article body.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5",
        "Accept-Language": "ja,en;q=0.5",
    })
    with urllib.request.urlopen(req, timeout=25) as res:
        raw = res.read(MAX_HTML_BYTES)
        charset = res.headers.get_content_charset() or "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return text, res.geturl()


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
    """Insert/update the exact item while retaining other publishers' coverage.

    Cross-publisher duplicates are intentionally kept here and clustered later, so
    the public site can show how many outlets covered the same story.
    """
    current = existing.get(item["id"])
    if current is None or prefer or current.get("via") == "google-news":
        existing[item["id"]] = item


def expand_existing_news(old_news: list[dict]) -> dict[str, dict]:
    """Expand previously clustered records back into source-level items."""
    expanded: dict[str, dict] = {}
    for n in old_news:
        base = dict(n)
        base.pop("relatedSources", None)
        base.pop("coverageCount", None)
        base.pop("priorityScore", None)
        base.pop("isBreaking", None)
        base.pop("detailPath", None)
        base_id = str(base.get("id") or make_id(str(base.get("sourceUrl", "")), str(base.get("title", ""))))
        base["id"] = base_id
        expanded[base_id] = base
        for src in n.get("relatedSources", []) or []:
            url = str(src.get("url", ""))
            source = str(src.get("source", ""))
            if not url or not source:
                continue
            clone = dict(base)
            clone.update({
                "id": make_id(url, str(base.get("title", ""))),
                "source": source,
                "sourceUrl": url,
                "publishedAt": src.get("publishedAt") or base.get("publishedAt"),
                "via": src.get("via") or "related-source",
            })
            expanded[clone["id"]] = clone
    return expanded

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



def _stable_published_at(existing: dict[str, dict], item_id: str) -> str:
    prior = existing.get(item_id) or {}
    return str(prior.get("publishedAt") or datetime.now(JST).isoformat(timespec="seconds"))


def _event_item(existing: dict[str, dict], *, title: str, url: str, source: str, via: str,
                ranges: list[tuple[datetime.date, datetime.date]], confidence: int,
                date_source: str, evidence: str, source_group: str = "イベント") -> dict:
    item_id = make_id(url, title)
    label = format_event_ranges(ranges)
    summary = f"{date_source}に掲載されたイベント情報です。開催日：{label}。詳細は公式ページでご確認ください。"
    return {
        "id": item_id,
        "title": title,
        "summary": summary,
        "category": "イベント",
        "area": classify_area(title, summary),
        "publishedAt": _stable_published_at(existing, item_id),
        "source": source,
        "sourceUrl": url,
        "sourceGroup": source_group,
        "via": via,
        "note": "公式イベント情報から開催日・開催期間を自動取得。内容は公式ページで確認してください。",
        "eventDates": serialize_event_ranges(ranges),
        "eventStart": ranges[0][0].isoformat() if ranges else None,
        "eventEnd": ranges[-1][1].isoformat() if ranges else None,
        "eventDateConfidence": confidence,
        "eventDateSource": date_source,
        "eventDateSourceUrl": url,
        "eventDateEvidence": evidence[:180],
        "eventDateMethod": via,
        "eventDateCheckedAt": datetime.now(JST).isoformat(timespec="seconds"),
    }


def _city_calendar_url(year: int, month: int) -> str:
    current = datetime.now(JST).date()
    if current.year == year and current.month == month:
        return urllib.parse.urljoin(CITY_CALENDAR_BASE, "index.html")
    return urllib.parse.urljoin(CITY_CALENDAR_BASE, f"{year:04d}{month:02d}.html")


def parse_city_calendar_page(page_html: str, page_url: str, fallback_year: int, fallback_month: int) -> list[tuple[str, str, list[tuple[datetime.date, datetime.date]], str]]:
    """Return (title, url, ranges, evidence) from an Iwaki City calendar page."""
    soup = BeautifulSoup(page_html, "html.parser")
    page_text = clean(soup.get_text(" ", strip=True))
    hm = re.search(r"(20\d{2})年\s*(\d{1,2})月の情報", page_text)
    year, month = (int(hm.group(1)), int(hm.group(2))) if hm else (fallback_year, fallback_month)
    grouped: dict[str, dict] = {}
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) < 2:
            continue
        dm = re.search(r"(\d{1,2})日", clean(cells[0].get_text(" ", strip=True)))
        if not dm:
            continue
        day = int(dm.group(1))
        date = _safe_date(year, month, day)
        if not date:
            continue
        for a in cells[1].find_all("a", href=True):
            title = clean(a.get_text(" ", strip=True))
            href = urllib.parse.urljoin(page_url, a["href"])
            if len(title) < 3 or not href.startswith(("http://", "https://")):
                continue
            # Associate the event link with the closest preceding calendar icon.
            # This matters on dates containing both an event and a consultation.
            prev_img = a.find_previous("img")
            kind = ""
            if prev_img is not None and prev_img.find_parent(["td", "th"]) is cells[1]:
                kind = clean(prev_img.get("alt", ""))
            if kind and kind not in CITY_CALENDAR_ALLOWED_TYPES:
                continue
            # Ignore icon-only/navigation anchors.
            if title in CITY_CALENDAR_ALLOWED_TYPES or title in {"イベント", "イベント・祭り"}:
                continue
            rec = grouped.setdefault(href, {"title": title, "dates": []})
            if len(title) > len(rec["title"]):
                rec["title"] = title
            rec["dates"].append(date)
    out = []
    for href, rec in grouped.items():
        dates = sorted(set(rec["dates"]))
        ranges = _merge_date_ranges([(d, d) for d in dates])
        out.append((rec["title"], href, ranges, f"いわき市イベントカレンダー {format_event_ranges(ranges)}"))
    return out


def _nearest_schedule_block(anchor, markers: tuple[str, ...]) -> str:
    node = anchor
    best = ""
    for _ in range(7):
        node = getattr(node, "parent", None)
        if node is None:
            break
        text = clean(node.get_text(" ", strip=True))
        if len(text) > 3000:
            continue
        if any(marker in text for marker in markers):
            best = text
            if len(text) <= 900:
                break
    return best


def parse_event_listing_page(page_html: str, page_url: str, *, site_kind: str) -> list[tuple[str, str, list[tuple[datetime.date, datetime.date]], str]]:
    soup = BeautifulSoup(page_html, "html.parser")
    results: dict[str, tuple[str, str, list[tuple[datetime.date, datetime.date]], str]] = {}
    now_year = datetime.now(JST).year
    for a in soup.find_all("a", href=True):
        href = urllib.parse.urljoin(page_url, a["href"])
        title = clean(a.get_text(" ", strip=True))
        if len(title) < 4:
            continue
        if site_kind == "tourism":
            if not re.search(r"/event/\d+/?(?:$|[?#])", href):
                continue
            markers = ("開催期間", "イベント開催期間")
        else:
            if "city.iwaki.lg.jp" not in urllib.parse.urlparse(href).netloc or "/www/contents/" not in href:
                continue
            markers = ("開催日", "開催期間")
        block = _nearest_schedule_block(a, markers)
        if not block:
            continue
        ranges = extract_event_ranges(block, now_year)
        if not ranges:
            continue
        evidence = next((part for part in re.split(r"[。\n]", block) if any(m in part for m in markers) and re.search(r"\d", part)), block)
        prev = results.get(href)
        current = (title, href, ranges, clean(evidence)[:180])
        if prev is None or len(title) > len(prev[0]):
            results[href] = current
    return list(results.values())


def collect_official_events(existing: dict[str, dict], statuses: list[dict]) -> int:
    """Collect structured event schedules from the city calendar and tourism site."""
    successes = 0
    weekend_start, weekend_end = weekend_bounds()
    month_keys = {(weekend_start.year, weekend_start.month), (weekend_end.year, weekend_end.month)}
    # Also scan the current month so ongoing events spanning into the weekend are found.
    today = datetime.now(JST).date()
    month_keys.add((today.year, today.month))

    for year, month in sorted(month_keys):
        url = _city_calendar_url(year, month)
        try:
            body, final_url = fetch_html(url)
            events = parse_city_calendar_page(body, final_url, year, month)
            successes += 1
            statuses.append({"name": f"いわき市イベントカレンダー {year}/{month:02d}", "group": "イベント", "ok": True, "items": len(events)})
            print(f"City calendar {year}/{month:02d}: {len(events)} events")
            for title, href, ranges, evidence in events:
                item = _event_item(existing, title=title, url=href, source="いわき市イベントカレンダー", via="city-calendar", ranges=ranges, confidence=100, date_source="いわき市イベントカレンダー", evidence=evidence)
                upsert(existing, item, prefer=True)
        except Exception as exc:
            statuses.append({"name": f"いわき市イベントカレンダー {year}/{month:02d}", "group": "イベント", "ok": False, "items": 0, "error": str(exc)[:140]})
            print(f"WARN city calendar {year}/{month:02d}: {exc}")

    official_lists = [
        ("いわき市・イベント祭り", CITY_EVENT_GENRE_URL, "city", "いわき市", "city-event-list", 98),
        ("いわき市観光サイト・イベント", TOURISM_EVENT_LIST_URL, "tourism", "いわき市観光サイト", "tourism-event-list", 98),
    ]
    for name, url, site_kind, source, via, confidence in official_lists:
        try:
            body, final_url = fetch_html(url)
            events = parse_event_listing_page(body, final_url, site_kind=site_kind)
            successes += 1
            statuses.append({"name": name, "group": "イベント", "ok": True, "items": len(events)})
            print(f"{name}: {len(events)} events")
            for title, href, ranges, evidence in events:
                item = _event_item(existing, title=title, url=href, source=source, via=via, ranges=ranges, confidence=confidence, date_source=name, evidence=evidence)
                upsert(existing, item, prefer=True)
        except Exception as exc:
            statuses.append({"name": name, "group": "イベント", "ok": False, "items": 0, "error": str(exc)[:140]})
            print(f"WARN {name}: {exc}")
    return successes


def _event_candidate(item: dict) -> bool:
    hay = f"{item.get('title','')} {item.get('summary','')}"
    return str(item.get("category")) == "イベント" or str(item.get("sourceGroup")) in {"イベント", "観光・文化"} or any(term in hay for term in EVENT_TERMS)


def _recently_checked(item: dict) -> bool:
    value = item.get("eventDateCheckedAt")
    if not value:
        return False
    try:
        checked = parse_date(str(value))
        return datetime.now(JST) - checked < timedelta(hours=EVENT_DATE_RECHECK_HOURS)
    except Exception:
        return False


def _schedule_segments_from_html(page_html: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(page_html, "html.parser")
    for bad in soup(["script", "style", "noscript", "svg"]):
        bad.decompose()
    segments: list[tuple[int, str]] = []
    for node in soup.find_all(["p", "li", "tr", "dl", "dd", "dt", "h1", "h2", "h3", "h4", "div"]):
        text = clean(node.get_text(" ", strip=True))
        if not text or len(text) > 1600 or not re.search(r"\d|令和", text):
            continue
        if any(x in text for x in DATE_META_EXCLUDES) and not any(cue in text for cue in DATE_CUES):
            continue
        score = 0
        if any(cue in text for cue in DATE_CUES):
            score += 8
        if any(term in text for term in EVENT_TERMS):
            score += 3
        if re.search(r"(?:20\d{2}年|令和\s*\d+年)?\s*\d{1,2}月\s*\d{1,2}日|\d{1,2}/\d{1,2}", text):
            score += 2
        if score >= 5:
            segments.append((score, text))
    # Highest-signal, shortest snippets first; dedupe identical text.
    seen = set()
    out = []
    for score, text in sorted(segments, key=lambda x: (-x[0], len(x[1]))):
        if text in seen:
            continue
        seen.add(text)
        out.append((score, text))
        if len(out) >= 24:
            break
    return out


def extract_schedule_from_article(page_html: str, final_url: str, reference_year: int) -> tuple[list[tuple[datetime.date, datetime.date]], str, int]:
    segments = _schedule_segments_from_html(page_html)
    candidates: list[tuple[int, list[tuple[datetime.date, datetime.date]], str]] = []
    for score, text in segments:
        ranges = extract_event_ranges(text, reference_year)
        if ranges:
            candidates.append((score, ranges, text))
    if not candidates:
        return [], "", 0
    weekend_start, weekend_end = weekend_bounds()
    candidates.sort(key=lambda c: (ranges_intersect(c[1], weekend_start, weekend_end), c[0], -len(c[2])), reverse=True)
    best_score, best_ranges, evidence = candidates[0]
    host = urllib.parse.urlparse(final_url).netloc.lower()
    official = host.endswith("city.iwaki.lg.jp") or host.endswith("kankou-iwaki.or.jp")
    confidence = 95 if official else min(82, 62 + best_score)
    return best_ranges, evidence[:180], confidence


def enrich_event_article_bodies(existing: dict[str, dict], statuses: list[dict]) -> int:
    """Read event article pages only to extract schedule dates; never republish bodies."""
    candidates = [x for x in existing.values() if _event_candidate(x) and not deserialize_event_ranges(x) and not _recently_checked(x)]
    candidates.sort(key=lambda x: parse_date(str(x.get("publishedAt", ""))), reverse=True)
    checked = found = 0
    for item in candidates[:EVENT_PAGE_FETCH_LIMIT]:
        url = str(item.get("sourceUrl", ""))
        host = urllib.parse.urlparse(url).netloc.lower()
        if not url.startswith(("http://", "https://")) or host.endswith("news.google.com"):
            continue
        checked += 1
        try:
            body, final_url = fetch_html(url)
            ranges, evidence, confidence = extract_schedule_from_article(body, final_url, weekend_bounds()[0].year)
            item["eventDateCheckedAt"] = datetime.now(JST).isoformat(timespec="seconds")
            item["eventDateCheckOk"] = True
            if ranges:
                found += 1
                item["eventDates"] = serialize_event_ranges(ranges)
                item["eventStart"] = ranges[0][0].isoformat()
                item["eventEnd"] = ranges[-1][1].isoformat()
                item["eventDateConfidence"] = confidence
                item["eventDateSource"] = f"{item.get('source','出典')}の記事・公式ページ"
                item["eventDateSourceUrl"] = final_url
                item["eventDateEvidence"] = evidence
                item["eventDateMethod"] = "article-body"
        except Exception as exc:
            item["eventDateCheckedAt"] = datetime.now(JST).isoformat(timespec="seconds")
            item["eventDateCheckOk"] = False
            item["eventDateCheckError"] = str(exc)[:120]
    statuses.append({"name": "イベント記事本文の日程確認", "group": "イベント", "ok": True, "items": found, "checked": checked})
    print(f"Event page schedule enrichment: checked {checked}, found {found}")
    return checked


def weekend_bounds(now: datetime | None = None) -> tuple[datetime.date, datetime.date]:
    """Return the Saturday/Sunday that should be presented as "this weekend" in JST.

    On Saturday or Sunday, the current weekend is used. Monday-Friday points to
    the upcoming Saturday/Sunday.
    """
    current = (now or datetime.now(JST)).astimezone(JST).date()
    weekday = current.weekday()
    if weekday == 5:  # Saturday
        start = current
    elif weekday == 6:  # Sunday
        start = current - timedelta(days=1)
    else:
        start = current + timedelta(days=5 - weekday)
    return start, start + timedelta(days=1)


def weekend_label(start, end) -> str:
    weekdays = "月火水木金土日"
    return f"{start.month}月{start.day}日({weekdays[start.weekday()]})〜{end.month}月{end.day}日({weekdays[end.weekday()]})"


def _safe_date(year: int, month: int, day: int):
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def _normalize_date_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", html.unescape(text or ""))
    # Japanese era years commonly used by the city site.
    def repl_reiwa(m):
        n = 1 if m.group(1) == "元" else int(m.group(1))
        return f"{2018+n}年"
    text = re.sub(r"令和\s*(元|\d{1,2})\s*年", repl_reiwa, text)
    text = re.sub(r"[（(](?:月|火|水|木|金|土|日)(?:曜日)?(?:・?祝)?[）)]", "", text)
    text = text.replace("から", "~").replace("〜", "~").replace("～", "~").replace("－", "~").replace("—", "~").replace("‐", "~")
    return SPACE_RE.sub(" ", text).strip()


def _merge_date_ranges(ranges: list[tuple[datetime.date, datetime.date]]) -> list[tuple[datetime.date, datetime.date]]:
    valid = sorted((min(a, b), max(a, b)) for a, b in ranges if a and b)
    if not valid:
        return []
    merged = [valid[0]]
    for start, end in valid[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end + timedelta(days=1):
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return merged


def extract_event_ranges(text: str, reference_year: int) -> list[tuple[datetime.date, datetime.date]]:
    """Extract Japanese event dates/ranges from a short piece of schedule text.

    Supported examples include:
      2026年8月11日～16日
      8月30日～9月1日
      令和8年8月16日
      2026/08/15 - 2026/08/16
      8月15日・16日
    """
    t = _normalize_date_text(text)
    ranges: list[tuple[datetime.date, datetime.date]] = []

    # YYYY年M月D日 ~ YYYY年M月D日 (year on either side may be omitted)
    for m in re.finditer(r"(?:(\d{4})年\s*)?(\d{1,2})月\s*(\d{1,2})日\s*~\s*(?:(\d{4})年\s*)?(?:(\d{1,2})月\s*)?(\d{1,2})日", t):
        y1 = int(m.group(1) or reference_year)
        mo1, d1 = int(m.group(2)), int(m.group(3))
        y2 = int(m.group(4) or y1)
        mo2 = int(m.group(5) or mo1)
        d2 = int(m.group(6))
        a, b = _safe_date(y1, mo1, d1), _safe_date(y2, mo2, d2)
        if a and b:
            # Handle year rollover when the end month wraps into January.
            if b < a and not m.group(4) and mo2 < mo1:
                b = _safe_date(y1 + 1, mo2, d2)
            if b:
                ranges.append((a, b))

    # YYYY/M/D ~ YYYY/M/D
    for m in re.finditer(r"(?<!\d)(\d{4})/(\d{1,2})/(\d{1,2})\s*~\s*(\d{4})/(\d{1,2})/(\d{1,2})(?!\d)", t):
        a = _safe_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        b = _safe_date(int(m.group(4)), int(m.group(5)), int(m.group(6)))
        if a and b:
            ranges.append((a, b))

    # M月D日・D日 / M月D日、D日 (explicit multiple days, not continuous)
    for m in re.finditer(r"(?:(\d{4})年\s*)?(\d{1,2})月\s*(\d{1,2})日\s*[・、,]\s*(\d{1,2})日", t):
        year = int(m.group(1) or reference_year)
        month = int(m.group(2))
        for day in (int(m.group(3)), int(m.group(4))):
            d = _safe_date(year, month, day)
            if d:
                ranges.append((d, d))

    # Single YYYY年M月D日 / M月D日. These may duplicate range endpoints; dedupe later.
    for m in re.finditer(r"(?:(\d{4})年\s*)?(\d{1,2})月\s*(\d{1,2})日", t):
        year = int(m.group(1) or reference_year)
        d = _safe_date(year, int(m.group(2)), int(m.group(3)))
        if d:
            ranges.append((d, d))

    # M/D or M.D, excluding the components of YYYY/M/D.
    for m in re.finditer(r"(?<![\d/])(\d{1,2})[/.](\d{1,2})(?![/.]\d)", t):
        d = _safe_date(reference_year, int(m.group(1)), int(m.group(2)))
        if d:
            ranges.append((d, d))

    return _merge_date_ranges(ranges)


def extract_event_dates(text: str, reference_year: int) -> set[datetime.date]:
    dates: set[datetime.date] = set()
    for start, end in extract_event_ranges(text, reference_year):
        cur = start
        # Expand only modest ranges; callers use this mainly for compatibility/tests.
        while cur <= end and (cur - start).days <= 62:
            dates.add(cur)
            cur += timedelta(days=1)
    return dates


def serialize_event_ranges(ranges: list[tuple[datetime.date, datetime.date]]) -> list[dict]:
    return [{"start": a.isoformat(), "end": b.isoformat()} for a, b in _merge_date_ranges(ranges)]


def deserialize_event_ranges(item: dict) -> list[tuple[datetime.date, datetime.date]]:
    out: list[tuple[datetime.date, datetime.date]] = []
    for r in item.get("eventDates", []) or []:
        try:
            a = datetime.fromisoformat(str(r.get("start"))).date()
            b = datetime.fromisoformat(str(r.get("end"))).date()
            out.append((a, b))
        except Exception:
            continue
    if not out and item.get("eventStart"):
        try:
            a = datetime.fromisoformat(str(item["eventStart"])).date()
            b = datetime.fromisoformat(str(item.get("eventEnd") or item["eventStart"])).date()
            out.append((a, b))
        except Exception:
            pass
    return _merge_date_ranges(out)


def format_event_ranges(ranges: list[tuple[datetime.date, datetime.date]]) -> str:
    pieces: list[str] = []
    for a, b in _merge_date_ranges(ranges):
        if a == b:
            pieces.append(f"{a.month}月{a.day}日")
        elif a.year == b.year and a.month == b.month:
            pieces.append(f"{a.month}月{a.day}日〜{b.day}日")
        elif a.year == b.year:
            pieces.append(f"{a.month}月{a.day}日〜{b.month}月{b.day}日")
        else:
            pieces.append(f"{a.year}年{a.month}月{a.day}日〜{b.year}年{b.month}月{b.day}日")
    return "・".join(pieces)


def ranges_intersect(ranges: list[tuple[datetime.date, datetime.date]], start: datetime.date, end: datetime.date) -> bool:
    return any(a <= end and b >= start for a, b in ranges)


def event_date_priority(item: dict) -> int:
    try:
        return int(item.get("eventDateConfidence") or 0)
    except Exception:
        return 0


def choose_cluster_event_schedule(cluster: list[dict], weekend_start: datetime.date, weekend_end: datetime.date) -> tuple[list[tuple[datetime.date, datetime.date]], dict | None]:
    candidates = [(event_date_priority(x), x, deserialize_event_ranges(x)) for x in cluster]
    candidates = [c for c in candidates if c[2]]
    if not candidates:
        combined = " ".join(f"{x.get('title','')} {x.get('summary','')}" for x in cluster)
        fallback = extract_event_ranges(combined, weekend_start.year)
        return fallback, None
    best_conf = max(c[0] for c in candidates)
    # Trust the strongest evidence tier; merge equally authoritative sources.
    strongest = [c for c in candidates if c[0] == best_conf]
    merged = _merge_date_ranges([r for _, _, ranges in strongest for r in ranges])
    # Prefer a source whose schedule actually overlaps this weekend for attribution.
    strongest.sort(key=lambda c: (ranges_intersect(c[2], weekend_start, weekend_end), c[0]), reverse=True)
    return merged, strongest[0][1]


def is_opening_closing_text(text: str) -> bool:
    hay = text or ""
    if any(term in hay for term in OPENING_CLOSING_EXCLUDE_TERMS):
        return False
    return any(term in hay for term in OPENING_CLOSING_TERMS)


def is_weekend_event_cluster(cluster: list[dict], start, end) -> bool:
    combined = " ".join(f"{x.get('title','')} {x.get('summary','')}" for x in cluster)
    if not any(term in combined for term in EVENT_TERMS) and not any(deserialize_event_ranges(x) for x in cluster):
        return False
    ranges, _ = choose_cluster_event_schedule(cluster, start, end)
    if ranges:
        return ranges_intersect(ranges, start, end)
    return "今週末" in combined or "週末開催" in combined


def compact_title(title: str) -> str:
    s = html.unescape(title).lower()
    s = re.sub(r"[【】〖〗「」『』\[\]()（）〈〉《》]", "", s)
    s = re.sub(r"\d{1,2}[月/.-]\d{1,2}日?", "", s)
    s = re.sub(r"[^0-9a-zぁ-んァ-ヶ一-龠]+", "", s)
    return s


def ngram_set(text: str, n: int = 2) -> set[str]:
    text = compact_title(text)
    if len(text) < n:
        return {text} if text else set()
    return {text[i:i+n] for i in range(len(text)-n+1)}


def title_similarity(a: str, b: str) -> float:
    aa, bb = compact_title(a), compact_title(b)
    if not aa or not bb:
        return 0.0
    if aa == bb or aa in bb or bb in aa:
        shorter = min(len(aa), len(bb))
        longer = max(len(aa), len(bb))
        if shorter >= 10 and shorter / longer >= 0.58:
            return 1.0
    seq = difflib.SequenceMatcher(None, aa, bb).ratio()
    sa, sb = ngram_set(aa), ngram_set(bb)
    jac = len(sa & sb) / len(sa | sb) if sa | sb else 0.0
    return max(seq, jac)


def same_story(a: dict, b: dict) -> bool:
    da, db = parse_date(str(a.get("publishedAt", ""))), parse_date(str(b.get("publishedAt", "")))
    ca, cb = str(a.get("category", "")), str(b.get("category", ""))
    if ca != cb and "暮らし" not in (ca, cb):
        return False
    aa, ab = str(a.get("area", "全市")), str(b.get("area", "全市"))
    if aa != ab and "全市" not in (aa, ab):
        return False
    similarity = title_similarity(str(a.get("title", "")), str(b.get("title", "")))
    if similarity < 0.72:
        return False
    gap_hours = abs((da-db).total_seconds()) / 3600
    if gap_hours <= 72:
        return True
    # Official calendars may discover an event days after the original article.
    # For event stories, allow a wider window when at least one side carries a
    # parsed event schedule, so the official entry can merge with earlier media.
    eventish = ca == "イベント" or cb == "イベント"
    has_schedule = bool(deserialize_event_ranges(a) or deserialize_event_ranges(b))
    return eventish and has_schedule and gap_hours <= MAX_AGE_DAYS * 24


def representative_rank(item: dict) -> tuple:
    via = str(item.get("via", ""))
    source = str(item.get("source", ""))
    direct = 2 if via == "direct-rss" else 0
    official = 1 if source.startswith("いわき市") or source.startswith("福島県") else 0
    return (direct + official, parse_date(str(item.get("publishedAt", ""))).timestamp())


def priority_score(item: dict, coverage_count: int) -> int:
    score = {
        "防災・安全": 65,
        "市政": 38,
        "暮らし": 28,
        "教育・子育て": 25,
        "経済": 22,
        "イベント": 14,
        "スポーツ": 14,
    }.get(str(item.get("category", "")), 15)
    hay = f"{item.get('title','')} {item.get('summary','')}"
    if any(k in hay for k in BREAKING_TERMS):
        score += 28
    if any(k in hay for k in IMPORTANT_TERMS):
        score += 10
    source = str(item.get("source", ""))
    if source.startswith("いわき市") or source.startswith("福島県"):
        score += 7
    score += min(max(coverage_count - 1, 0) * 5, 20)
    age_h = max(0.0, (datetime.now(JST) - parse_date(str(item.get("publishedAt", "")))).total_seconds() / 3600)
    if age_h <= 6:
        score += 20
    elif age_h <= 24:
        score += 12
    elif age_h <= 72:
        score += 5
    return score


def cluster_items(items: list[dict]) -> list[dict]:
    weekend_start, weekend_end = weekend_bounds()
    clusters: list[list[dict]] = []
    for item in sorted(items, key=lambda x: parse_date(str(x.get("publishedAt", ""))), reverse=True):
        target = None
        for cluster in clusters:
            if any(same_story(item, other) for other in cluster[:4]):
                target = cluster
                break
        if target is None:
            clusters.append([item])
        else:
            target.append(item)

    merged: list[dict] = []
    for cluster in clusters:
        representative = max(cluster, key=representative_rank)
        rep = dict(representative)
        seen_publishers: set[str] = set()
        all_sources: list[dict] = []
        for src_item in sorted(cluster, key=representative_rank, reverse=True):
            source = str(src_item.get("source", "")).strip()
            url = str(src_item.get("sourceUrl", "")).strip()
            if not source or source in seen_publishers:
                continue
            seen_publishers.add(source)
            all_sources.append({
                "source": source,
                "url": url,
                "publishedAt": src_item.get("publishedAt"),
                "via": src_item.get("via"),
            })
        primary_source = str(rep.get("source", ""))
        all_sources.sort(key=lambda x: 0 if x["source"] == primary_source else 1)
        coverage_count = len(all_sources)
        rep["relatedSources"] = all_sources[1:]
        rep["coverageCount"] = coverage_count
        rep["priorityScore"] = priority_score(rep, coverage_count)
        age_h = max(0.0, (datetime.now(JST) - parse_date(str(rep.get("publishedAt", "")))).total_seconds() / 3600)
        hay = " ".join(f"{x.get('title','')} {x.get('summary','')}" for x in cluster)
        rep["isBreaking"] = age_h <= 24 and any(k in hay for k in BREAKING_TERMS) and rep["priorityScore"] >= 85
        rep["isOpeningClosing"] = is_opening_closing_text(hay)
        event_ranges, schedule_source = choose_cluster_event_schedule(cluster, weekend_start, weekend_end)
        if event_ranges:
            rep["eventDates"] = serialize_event_ranges(event_ranges)
            rep["eventStart"] = event_ranges[0][0].isoformat()
            rep["eventEnd"] = event_ranges[-1][1].isoformat()
            rep["eventDateLabel"] = format_event_ranges(event_ranges)
            if schedule_source:
                for key in ("eventDateConfidence", "eventDateSource", "eventDateSourceUrl", "eventDateEvidence", "eventDateMethod", "eventDateCheckedAt"):
                    if schedule_source.get(key) is not None:
                        rep[key] = schedule_source.get(key)
        rep["isWeekendEvent"] = bool(event_ranges and ranges_intersect(event_ranges, weekend_start, weekend_end)) or is_weekend_event_cluster(cluster, weekend_start, weekend_end)
        rep["detailPath"] = f"news/{rep['id']}.html"
        merged.append(rep)
    return sorted(merged, key=lambda x: parse_date(str(x.get("publishedAt", ""))), reverse=True)


def article_page_html(item: dict, generated_at: str) -> str:
    title = html.escape(str(item.get("title", "")))
    summary = html.escape(str(item.get("summary", "")))
    source = html.escape(str(item.get("source", "")))
    source_url = html.escape(str(item.get("sourceUrl", "")), quote=True)
    area = html.escape(str(item.get("area", "全市")))
    category = html.escape(str(item.get("category", "暮らし")))
    published = parse_date(str(item.get("publishedAt", ""))).strftime("%Y年%m月%d日 %H:%M")
    related = item.get("relatedSources", []) or []
    sources_html = [f'<li><a href="{source_url}" target="_blank" rel="noopener noreferrer">{source}</a> <span>主な出典</span></li>']
    for src in related:
        su = html.escape(str(src.get("url", "")), quote=True)
        sn = html.escape(str(src.get("source", "")))
        sources_html.append(f'<li><a href="{su}" target="_blank" rel="noopener noreferrer">{sn}</a></li>')
    coverage = int(item.get("coverageCount", 1) or 1)
    coverage_html = f'<span class="coverage-chip">{coverage}媒体が掲載</span>' if coverage > 1 else ''
    breaking_html = '<span class="breaking-chip">速報・重要</span>' if item.get("isBreaking") else ''
    shop_html = '<span class="shop-chip">開店・閉店</span>' if item.get("isOpeningClosing") else ''
    weekend_html = '<span class="weekend-chip">今週末イベント</span>' if item.get("isWeekendEvent") else ''
    event_ranges = deserialize_event_ranges(item)
    event_label = html.escape(str(item.get("eventDateLabel") or format_event_ranges(event_ranges))) if event_ranges else ""
    event_source_name = html.escape(str(item.get("eventDateSource") or ""))
    event_source_url = html.escape(str(item.get("eventDateSourceUrl") or item.get("sourceUrl", "")), quote=True)
    event_schedule_html = ""
    if event_label:
        event_schedule_html = f'<section class="detail-event-schedule"><h2>開催日・開催期間</h2><p class="detail-event-date">{event_label}</p><p class="detail-event-source">日程確認：<a href="{event_source_url}" target="_blank" rel="noopener noreferrer">{event_source_name or source}</a></p></section>'
    jsonld = json.dumps({
        "@context": "https://schema.org",
        "@type": "NewsArticle",
        "headline": str(item.get("title", "")),
        "description": str(item.get("summary", "")),
        "datePublished": str(item.get("publishedAt", "")),
        "dateModified": generated_at,
        "articleSection": str(item.get("category", "")),
        "author": {"@type": "Organization", "name": "いわきNOW"},
        "publisher": {"@type": "Organization", "name": "いわきNOW"},
    }, ensure_ascii=False)
    return f'''<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}｜いわきNOW</title><meta name="description" content="{summary[:150]}"><link rel="icon" href="../favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="../styles.css">
<script type="application/ld+json">{jsonld}</script></head>
<body><header class="article-page-header"><div class="wrap"><a class="brand" href="../"><strong>いわき <span>NOW</span></strong><small>今日のいわきを、ひと目で。</small></a></div></header>
<main class="wrap article-page-main"><nav class="breadcrumb"><a href="../">トップ</a><span>›</span><span>{category}</span></nav>
<article class="detail-article"><div class="detail-badges"><span class="category-chip">{category}</span><span class="area-chip">{area}</span>{breaking_html}{shop_html}{weekend_html}{coverage_html}</div>
<h1>{title}</h1><div class="detail-meta"><time>{published}</time><span>主な出典：{source}</span></div>
{event_schedule_html}
<section class="detail-summary"><h2>概要</h2><p>{summary}</p></section>
<section class="detail-sources"><h2>このニュースの出典</h2><p>同じ出来事を複数媒体が掲載している場合は、まとめて表示しています。</p><ul>{''.join(sources_html)}</ul></section>
<div class="detail-actions"><a class="source-button" href="{source_url}" target="_blank" rel="noopener noreferrer">主な出典で確認する ↗</a><a class="back-button" href="../">いわきNOWへ戻る</a></div>
<p class="detail-note">いわきNOWは公開情報の見出し・概要を整理する地域ニュース集約サイトです。重要な情報は必ず出典元でご確認ください。</p></article></main>
<footer class="site-footer"><div class="wrap footer-row"><div><strong>いわきNOW</strong><p>福島県いわき市の地域情報を、短く、分かりやすく。</p></div></div></footer></body></html>'''


def render_article_pages(items: list[dict], generated_at: str) -> None:
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    wanted: set[str] = set()
    for item in items:
        filename = f"{item['id']}.html"
        wanted.add(filename)
        (NEWS_DIR / filename).write_text(article_page_html(item, generated_at), encoding="utf-8")
    for path in NEWS_DIR.glob("*.html"):
        if path.name not in wanted and path.name != "index.html":
            path.unlink()
    (NEWS_DIR / "index.html").write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=../"><title>いわきNOW</title><a href="../">いわきNOWへ戻る</a>', encoding="utf-8")


def enrich_and_write(raw_items: list[dict], statuses: list[dict] | None = None) -> dict:
    cutoff = datetime.now(JST) - timedelta(days=MAX_AGE_DAYS)
    eligible = []
    for n in raw_items:
        try:
            dt = parse_date(str(n.get("publishedAt", "")))
        except Exception:
            dt = datetime.now(JST)
        event_ranges = deserialize_event_ranges(n)
        event_still_relevant = bool(event_ranges and max(b for _, b in event_ranges) >= datetime.now(JST).date() - timedelta(days=1))
        if dt >= cutoff or event_still_relevant:
            n = dict(n)
            n["category"] = n.get("category") or classify_category(str(n.get("title", "")), str(n.get("summary", "")))
            n["area"] = n.get("area") or classify_area(str(n.get("title", "")), str(n.get("summary", "")))
            eligible.append(n)
    items = cluster_items(eligible)[:MAX_ITEMS]
    active_sources = sorted({str(n.get("source", "")) for n in items if n.get("source")} | {str(src.get("source", "")) for n in items for src in (n.get("relatedSources", []) or []) if src.get("source")})
    generated_at = datetime.now(JST).isoformat(timespec="seconds")
    weekend_start, weekend_end = weekend_bounds()
    payload = {
        "generatedAt": generated_at,
        "collectorVersion": "5.0",
        "weekend": {
            "start": weekend_start.isoformat(),
            "end": weekend_end.isoformat(),
            "label": weekend_label(weekend_start, weekend_end),
        },
        "featureCounts": {
            "openingClosing": sum(1 for n in items if n.get("isOpeningClosing")),
            "weekendEvents": sum(1 for n in items if n.get("isWeekendEvent")),
        },
        "sourceCount": len(active_sources),
        "sources": active_sources,
        "collectorStatus": statuses or [],
        "news": items,
    }
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    render_article_pages(items, generated_at)
    print(f"Wrote {len(items)} clustered stories from {len(active_sources)} publishers")
    return payload


def render_only() -> int:
    old = load_existing()
    raw = list(expand_existing_news(old.get("news", [])).values())
    enrich_and_write(raw, old.get("collectorStatus", []))
    return 0


def self_test() -> int:
    start = datetime(2026, 8, 15, 12, tzinfo=JST).date()
    end = datetime(2026, 8, 16, 12, tzinfo=JST).date()

    r = extract_event_ranges("開催期間：2026年8月11日(火祝)～16日(日)", 2026)
    assert r == [(_safe_date(2026, 8, 11), _safe_date(2026, 8, 16))], r
    assert ranges_intersect(r, start, end)

    r2 = extract_event_ranges("開催日：令和8年8月29日（土）", 2026)
    assert r2 == [(_safe_date(2026, 8, 29), _safe_date(2026, 8, 29))], r2

    city_fixture = '''<html><body><h2>2026年8月の情報</h2><table>
    <tr><th>15日(土曜日)</th><td><img alt="イベント・祭り"><a href="/event/a">海辺の夏イベント</a></td></tr>
    <tr><th>16日(日曜日)</th><td><img alt="イベント・祭り"><a href="/event/a">海辺の夏イベント</a><a href="/event/b">日曜マルシェ</a></td></tr>
    <tr><th>16日(日曜日)</th><td><img alt="相談"><a href="/consult/c">法律相談</a></td></tr>
    </table></body></html>'''
    parsed = parse_city_calendar_page(city_fixture, "https://www.city.iwaki.lg.jp/calendar", 2026, 8)
    by_title = {x[0]: x for x in parsed}
    assert format_event_ranges(by_title["海辺の夏イベント"][2]) == "8月15日〜16日", parsed
    assert "法律相談" not in by_title, parsed

    tourism_fixture = '''<html><body><article><h3><a href="/event/12345">ほるる de 夏休みイベント2026</a></h3>
    <p>開催期間：2026年8月11日(火祝)～16日(日)</p></article></body></html>'''
    tourism = parse_event_listing_page(tourism_fixture, "https://kankou-iwaki.or.jp/event", site_kind="tourism")
    assert tourism and format_event_ranges(tourism[0][2]) == "8月11日〜16日", tourism

    article_fixture = '''<html><body><p>登録日：2026年8月5日</p><main><h1>夏のイベント</h1>
    <section><h2>開催概要</h2><p>イベント開催期間 2026年8月11日(火祝)～8月16日(日)</p></section></main></body></html>'''
    aranges, evidence, confidence = extract_schedule_from_article(article_fixture, "https://kankou-iwaki.or.jp/event/1", 2026)
    assert ranges_intersect(aranges, start, end), (aranges, evidence)
    assert confidence >= 90

    cluster = [{
        "title": "夏休みイベント", "summary": "開催します", "category": "イベント", "area": "常磐",
        "publishedAt": "2026-08-14T10:00:00+09:00", "eventDates": serialize_event_ranges(aranges),
        "eventDateConfidence": 95, "eventDateSource": "公式ページ"
    }]
    assert is_weekend_event_cluster(cluster, start, end)
    print("Self-test OK: weekend event date parsing, city calendar, tourism listing, article body")
    return 0



def main() -> int:
    old = load_existing()
    existing = expand_existing_news(old.get("news", []))
    statuses: list[dict] = []

    successful_sources = collect_direct(existing, statuses)
    successful_sources += collect_google_news(existing, statuses)
    successful_sources += collect_official_events(existing, statuses)
    enrich_event_article_bodies(existing, statuses)

    if not successful_sources:
        print("No sources fetched successfully; keeping existing stories and regenerating pages.")
        return render_only()

    enrich_and_write(list(existing.values()), statuses)
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        raise SystemExit(self_test())
    if "--render-only" in sys.argv:
        raise SystemExit(render_only())
    raise SystemExit(main())
