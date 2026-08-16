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
USER_AGENT = "IwakiNow/5.1 (+public headline/event-date aggregator; contact site operator)"
EVENT_PAGE_FETCH_LIMIT = 36
EVENT_DATE_RECHECK_HOURS = 24
MAX_HTML_BYTES = 2_500_000
CITY_CALENDAR_BASE = "https://www.city.iwaki.lg.jp/www/genre/1000100000345/"
CITY_EVENT_GENRE_URL = "https://www.city.iwaki.lg.jp/www/genre/1452741939257/index.html"
TOURISM_EVENT_LIST_URL = "https://kankou-iwaki.or.jp/event"
CITY_CALENDAR_ALLOWED_TYPES = ("イベント・祭り", "講座・講演", "スポーツ・健康", "文化・芸術", "子育て", "その他")
DATE_CUES = ("開催日", "開催期間", "イベント開催期間", "開催日時", "日時", "日程", "会期", "期間")
DATE_META_EXCLUDES = ("登録日", "更新日", "掲載日", "公開日", "投稿日", "記事公開", "最終更新")

DIRECT_FEEDS = [
    {"name": "いわき市・新着情報", "source": "いわき市", "url": "https://www.city.iwaki.lg.jp/www/rss/news.rdf", "kind": "official", "group": "行政"},
    {"name": "いわき市・トピックス", "source": "いわき市", "url": "https://www.city.iwaki.lg.jp/www/rss/topics.rdf", "kind": "official", "group": "行政"},
    {"name": "いわき市・募集情報", "source": "いわき市", "url": "https://www.city.iwaki.lg.jp/www/rss/bosyu.rdf", "kind": "official", "group": "行政"},
    {"name": "福島県・いわき地方振興局", "source": "福島県 いわき地方振興局", "url": "https://www.pref.fukushima.lg.jp/rss/10/sec-3-29.xml", "kind": "official", "group": "行政"},
    {"name": "いわき民報", "source": "いわき民報", "url": "https://iwaki-minpo.co.jp/feed/", "kind": "media", "group": "報道"},
]

GOOGLE_NEWS_QUERIES = [
    {"name": "いわき総合ニュース", "query": '"いわき市" when:14d', "group": "報道"},
    {"name": "13地域ニュース", "query": '(小名浜 OR 勿来 OR 四倉 OR 内郷 OR 好間 OR 常磐 OR 湯本 OR 久之浜 OR 大久 OR 遠野 OR 小川 OR 三和 OR 田人 OR 川前) いわき when:14d', "group": "地域"},
    {"name": "いわきFC", "query": '"いわきFC" when:30d', "group": "スポーツ"},
    {"name": "いわき観光", "query": 'いわき (観光 OR イベント OR 祭り OR 海水浴 OR アリオス) when:30d', "group": "観光・文化"},
    {"name": "いわき経済", "query": 'いわき (企業 OR 工場 OR 商工 OR 開店 OR 閉店 OR 雇用 OR 求人) when:30d', "group": "経済"},
    {"name": "地域団体サイト", "query": 'いわき (site:iwakifc.com OR site:kankou-iwaki.or.jp OR site:iwaki-alios.jp OR site:iwakicci.or.jp) when:45d', "group": "地域団体"},
    {"name": "いわき開店・閉店", "query": 'いわき (開店 OR 閉店 OR オープン OR 新店舗 OR 新店 OR 移転 OR リニューアル) when:45d', "group": "開店・閉店"},
    {"name": "いわき週末イベント", "query": 'いわき (イベント OR 祭り OR まつり OR 花火 OR フェス OR コンサート OR マルシェ OR 展示 OR 公演 OR 盆踊り) when:30d', "group": "イベント"},
]

BLOCK_TERMS = ("お悔やみ", "訃報", "葬儀", "死亡広告")
BLOCK_SOURCES = ("PR TIMES", "valuepress", "アットプレス")
RELEVANCE_TERMS = ("いわき", "小名浜", "勿来", "四倉", "内郷", "好間", "湯本", "常磐", "久之浜", "久ノ浜", "大久", "遠野", "小川町", "小川郷", "三和町", "田人", "川前")

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

BREAKING_TERMS = ("避難指示", "緊急安全確保", "津波", "震度", "地震", "大雨警報", "洪水警報", "土砂災害", "火災", "通行止め", "運休", "断水", "停電", "クマ", "熊")
IMPORTANT_TERMS = ("市長", "市議会", "選挙", "予算", "条例", "医療", "病院", "学校", "休校", "道路", "水道", "給付", "補助", "開店", "閉店", "いわきFC")
OPENING_CLOSING_TERMS = ("開店", "閉店", "オープン", "OPEN", "新店舗", "新店", "新規出店", "移転オープン", "リニューアルオープン", "営業終了", "閉館", "閉鎖")
OPENING_CLOSING_EXCLUDE_TERMS = ("オープンキャンパス", "オープンデータ", "オープンイノベーション", "オープン戦", "オープン大会", "オープン講座")
EVENT_TERMS = ("イベント", "祭", "まつり", "花火", "フェス", "コンサート", "マルシェ", "展示", "展覧会", "公演", "盆踊り", "七夕", "おどり", "ワークショップ", "開催")

TAG_RE = re.compile(r"<[^>]+>")
SPACE_RE = re.compile(r"\s+")
TITLE_PUNCT_RE = re.compile(r"[\s　\-―ー:：|｜/／・]+")

NAVIGATION_EXCLUDE_TERMS = (
    "市役所へのアクセス", "このサイトについて", "サイトマップ", "組織一覧", "各部署連絡先", "情報提供指針",
    "著作権", "リンクについて", "ウェブアクセシビリティ", "セキュリティーポリシー", "AIチャットボット",
    "前の月", "次の月", "トップページ", "お問い合わせ", "プライバシーポリシー", "Cookie", "利用規約",
)


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
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5", "Accept-Language": "ja,en;q=0.5"})
    with urllib.request.urlopen(req, timeout=25) as res:
        raw = res.read(MAX_HTML_BYTES)
        charset = res.headers.get_content_charset() or "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return text, res.geturl()


def google_news_url(query: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query, "hl": "ja", "gl": "JP", "ceid": "JP:ja"})


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
        entries.append({"title": title, "link": link, "description": description, "date": date_value, "source": source_name, "source_home": source_home})
    return entries


def should_keep(title: str, summary: str, source: str, official=False) -> bool:
    hay = f"{title} {summary}"
    if any(term in hay for term in BLOCK_TERMS):
        return False
    if any(term.lower() in source.lower() for term in BLOCK_SOURCES):
        return False
    if official:
        return True
    return any(term in hay for term in RELEVANCE_TERMS)


def detect_opening_closing(title: str, summary: str) -> bool:
    hay = f"{title} {summary}"
    if any(x in hay for x in OPENING_CLOSING_EXCLUDE_TERMS):
        return False
    return any(x in hay for x in OPENING_CLOSING_TERMS)


def weekend_bounds(today=None):
    today = today or datetime.now(JST).date()
    days_to_sat = (5 - today.weekday()) % 7
    sat = today + timedelta(days=days_to_sat)
    sun = sat + timedelta(days=1)
    return sat, sun


def _safe_date(year: int, month: int, day: int):
    from datetime import date
    return date(year, month, day)


def normalize_japanese_year(text: str, default_year: int) -> tuple[str, int]:
    m = re.search(r"令和\s*(\d+)\s*年", text)
    if m:
        year = 2018 + int(m.group(1))
        text = re.sub(r"令和\s*\d+\s*年", f"{year}年", text)
        return text, year
    return text, default_year


def extract_event_ranges(text: str, default_year: int) -> list[tuple]:
    from datetime import date
    text = clean(text)
    if not text:
        return []
    text, default_year = normalize_japanese_year(text, default_year)
    text = text.replace("（", "(").replace("）", ")").replace("～", "〜").replace("~", "〜").replace("－", "-").replace("–", "-").replace("—", "-")
    ranges: list[tuple[date, date]] = []

    full_range = re.compile(r"(?:(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})日[^\d]{0,12}(?:〜|-|から|～)[^\d]{0,12}(?:(\d{4})年\s*)?(?:(\d{1,2})月\s*)?(\d{1,2})日")
    for m in full_range.finditer(text):
        y1 = int(m.group(1) or default_year); m1 = int(m.group(2)); d1 = int(m.group(3))
        y2 = int(m.group(4) or y1); m2 = int(m.group(5) or m1); d2 = int(m.group(6))
        try:
            a, b = date(y1, m1, d1), date(y2, m2, d2)
            if b < a and not m.group(4):
                y2 += 1; b = date(y2, m2, d2)
            ranges.append((a, b))
        except ValueError:
            pass

    slash_range = re.compile(r"(?:(\d{4})[/-])?(\d{1,2})[/-](\d{1,2})\s*(?:〜|-|から)\s*(?:(\d{4})[/-])?(?:(\d{1,2})[/-])?(\d{1,2})")
    for m in slash_range.finditer(text):
        y1 = int(m.group(1) or default_year); m1 = int(m.group(2)); d1 = int(m.group(3))
        y2 = int(m.group(4) or y1); m2 = int(m.group(5) or m1); d2 = int(m.group(6))
        try:
            a, b = date(y1, m1, d1), date(y2, m2, d2)
            ranges.append((a, b))
        except ValueError:
            pass

    single = re.compile(r"(?:(\d{4})年)?\s*(\d{1,2})月\s*(\d{1,2})日")
    occupied = [(m.start(), m.end()) for m in full_range.finditer(text)]
    for m in single.finditer(text):
        if any(a <= m.start() < b for a, b in occupied):
            continue
        try:
            dt = date(int(m.group(1) or default_year), int(m.group(2)), int(m.group(3)))
            ranges.append((dt, dt))
        except ValueError:
            pass

    uniq = []
    seen = set()
    for a, b in ranges:
        key = (a.isoformat(), b.isoformat())
        if key not in seen:
            seen.add(key); uniq.append((a, b))
    return uniq


def ranges_intersect(ranges, start, end):
    return any(a <= end and b >= start for a, b in ranges)


def serialize_event_ranges(ranges):
    return [{"start": a.isoformat(), "end": b.isoformat()} for a, b in ranges]


def deserialize_event_ranges(item):
    out = []
    for r in item.get("eventDates", []) or []:
        try:
            out.append((datetime.fromisoformat(r["start"]).date(), datetime.fromisoformat(r["end"]).date()))
        except Exception:
            pass
    return out


def format_event_ranges(ranges):
    if not ranges:
        return ""
    a, b = ranges[0]
    if a == b:
        return f"{a.month}月{a.day}日"
    if a.year == b.year and a.month == b.month:
        return f"{a.month}月{a.day}日〜{b.day}日"
    return f"{a.month}月{a.day}日〜{b.month}月{b.day}日"


def is_navigation_title(title: str) -> bool:
    t = clean(title)
    if not t or len(t) < 2:
        return True
    return any(term.lower() in t.lower() for term in NAVIGATION_EXCLUDE_TERMS)


def looks_like_city_event_url(url: str) -> bool:
    p = urllib.parse.urlparse(url)
    if p.netloc and p.netloc != "www.city.iwaki.lg.jp":
        return False
    return bool(re.search(r"/www/contents/\d+/index\.html$", p.path))


def nearest_date_context(anchor, max_up=4) -> str:
    node = anchor
    collected = []
    for _ in range(max_up + 1):
        if node is None:
            break
        text = clean(node.get_text(" ", strip=True))
        if text:
            collected.append(text[:1400])
        if any(cue in text for cue in DATE_CUES) and extract_event_ranges(text, datetime.now(JST).year):
            return text
        node = node.parent
    return " ".join(collected)


def parse_event_listing_page(page_html: str, base_url: str, site_kind: str = "generic"):
    soup = BeautifulSoup(page_html, "html.parser")
    out = []
    year = datetime.now(JST).year
    seen_urls = set()

    for a in soup.find_all("a", href=True):
        title = clean(a.get_text(" ", strip=True))
        url = urllib.parse.urljoin(base_url, a["href"])
        if is_navigation_title(title):
            continue
        if site_kind == "city" and not looks_like_city_event_url(url):
            continue
        if site_kind == "tourism" and not re.search(r"/event/\d+/?$", urllib.parse.urlparse(url).path):
            continue
        if url in seen_urls:
            continue

        context = nearest_date_context(a)
        ranges = extract_event_ranges(context, year)
        if not ranges:
            continue
        cue_present = any(cue in context for cue in DATE_CUES)
        eventish = any(term in f"{title} {context}" for term in EVENT_TERMS)
        if site_kind == "city" and not cue_present:
            continue
        if site_kind == "tourism" and not (cue_present or eventish):
            continue

        out.append((title, url, ranges, context[:500]))
        seen_urls.add(url)
    return out


def parse_city_calendar_page(page_html: str, base_url: str, year: int, month: int):
    soup = BeautifulSoup(page_html, "html.parser")
    out = []
    seen = {}
    day_re = re.compile(r"(^|\D)(\d{1,2})日")
    for row in soup.find_all(["tr", "li", "article", "div"]):
        text = clean(row.get_text(" ", strip=True))
        dm = day_re.search(text)
        if not dm:
            continue
        day = int(dm.group(2))
        if not (1 <= day <= 31):
            continue
        allowed = any(t in text for t in CITY_CALENDAR_ALLOWED_TYPES) or "イベント" in text or "祭" in text
        if not allowed:
            continue
        try:
            dt = _safe_date(year, month, day)
        except Exception:
            continue
        for a in row.find_all("a", href=True):
            title = clean(a.get_text(" ", strip=True))
            if is_navigation_title(title):
                continue
            url = urllib.parse.urljoin(base_url, a["href"])
            if not title or url in seen:
                continue
            seen[url] = (title, url, [(dt, dt)], text[:400])
    return list(seen.values())


def event_item(title, url, ranges, evidence, source, source_kind, confidence):
    now = datetime.now(JST)
    summary = evidence[:240] if evidence else f"{format_event_ranges(ranges)} 開催予定"
    return {
        "title": title,
        "summary": summary,
        "url": url,
        "publishedAt": now.isoformat(timespec="seconds"),
        "source": source,
        "sourceHome": urllib.parse.urljoin(url, "/"),
        "group": "イベント",
        "kind": "official",
        "category": "イベント",
        "area": classify_area(title, summary),
        "eventDates": serialize_event_ranges(ranges),
        "eventDateLabel": format_event_ranges(ranges),
        "eventDateSource": source_kind,
        "eventDateConfidence": confidence,
        "lastEventDateCheckedAt": now.isoformat(timespec="seconds"),
    }


def collect_official_events(existing, statuses):
    count = 0
    now = datetime.now(JST)
    year, month = now.year, now.month
    candidates = []
    sources = [
        ("いわき市イベントカレンダー", urllib.parse.urljoin(CITY_CALENDAR_BASE, f"index.html?year={year}&month={month}"), "calendar"),
        ("いわき市イベント・祭り", CITY_EVENT_GENRE_URL, "city"),
        ("いわき市観光サイト", TOURISM_EVENT_LIST_URL, "tourism"),
    ]
    for source, url, kind in sources:
        try:
            page_html, final = fetch_html(url)
            if kind == "calendar":
                parsed = parse_city_calendar_page(page_html, final, year, month)
            else:
                parsed = parse_event_listing_page(page_html, final, site_kind=kind)
            statuses.append({"name": source, "ok": True, "count": len(parsed), "type": "official-event"})
            for title, event_url, ranges, evidence in parsed:
                item = event_item(title, event_url, ranges, evidence, source, source, 98 if kind != "city" else 96)
                existing[item["url"]] = item
                candidates.append(item)
                count += 1
        except Exception as exc:
            statuses.append({"name": source, "ok": False, "error": str(exc)[:200], "type": "official-event"})
    return count


def extract_schedule_from_article(page_html: str, url: str, default_year: int):
    soup = BeautifulSoup(page_html, "html.parser")
    for bad in soup(["script", "style", "nav", "footer", "header", "aside"]):
        bad.decompose()
    body = clean(soup.get_text(" ", strip=True))
    segments = re.split(r"(?<=[。\n])", body)
    best_ranges = []
    best_evidence = ""
    best_conf = 0
    for seg in segments:
        if any(x in seg for x in DATE_META_EXCLUDES):
            continue
        if not any(cue in seg for cue in DATE_CUES):
            continue
        ranges = extract_event_ranges(seg, default_year)
        if ranges:
            conf = 94 if urllib.parse.urlparse(url).netloc.endswith(("city.iwaki.lg.jp", "kankou-iwaki.or.jp")) else 82
            if conf > best_conf:
                best_ranges, best_evidence, best_conf = ranges, seg[:500], conf
    return best_ranges, best_evidence, best_conf


def enrich_event_article_bodies(existing, statuses):
    now = datetime.now(JST)
    candidates = []
    for item in existing.values():
        hay = f"{item.get('title','')} {item.get('summary','')}"
        if not any(x in hay for x in EVENT_TERMS):
            continue
        last = item.get("lastEventDateCheckedAt")
        if last:
            try:
                if now - datetime.fromisoformat(last) < timedelta(hours=EVENT_DATE_RECHECK_HOURS):
                    continue
            except Exception:
                pass
        candidates.append(item)
    candidates.sort(key=lambda x: str(x.get("publishedAt", "")), reverse=True)
    checked = updated = 0
    for item in candidates[:EVENT_PAGE_FETCH_LIMIT]:
        url = str(item.get("url", ""))
        if not url.startswith("http"):
            continue
        try:
            page_html, final = fetch_html(url)
            ranges, evidence, conf = extract_schedule_from_article(page_html, final, now.year)
            item["lastEventDateCheckedAt"] = now.isoformat(timespec="seconds")
            checked += 1
            if ranges:
                current_conf = int(item.get("eventDateConfidence", 0) or 0)
                if conf >= current_conf:
                    item["eventDates"] = serialize_event_ranges(ranges)
                    item["eventDateLabel"] = format_event_ranges(ranges)
                    item["eventDateSource"] = "公式ページ" if conf >= 90 else "記事本文"
                    item["eventDateConfidence"] = conf
                    item["eventDateEvidence"] = evidence
                    updated += 1
        except Exception:
            continue
    statuses.append({"name": "記事本文・公式ページ日程確認", "ok": True, "count": checked, "updated": updated, "type": "event-enrichment"})


def collect_direct(existing, statuses):
    success = 0
    for cfg in DIRECT_FEEDS:
        try:
            entries = parse_feed(fetch_xml(cfg["url"]))
            kept = 0
            for e in entries:
                title, summary = e["title"], e["description"]
                source = cfg["source"]
                if not should_keep(title, summary, source, official=cfg["kind"] == "official"):
                    continue
                item = {
                    "title": title, "summary": summary, "url": e["link"], "publishedAt": parse_date(e["date"]).isoformat(),
                    "source": source, "sourceHome": cfg["url"], "group": cfg["group"], "kind": cfg["kind"],
                    "category": classify_category(title, summary), "area": classify_area(title, summary),
                }
                if item["url"]:
                    existing[item["url"]] = {**existing.get(item["url"], {}), **item}
                    kept += 1
            statuses.append({"name": cfg["name"], "ok": True, "count": kept, "type": "rss"}); success += 1
        except Exception as exc:
            statuses.append({"name": cfg["name"], "ok": False, "error": str(exc)[:200], "type": "rss"})
    return success


def collect_google_news(existing, statuses):
    success = 0
    for cfg in GOOGLE_NEWS_QUERIES:
        try:
            entries = parse_feed(fetch_xml(google_news_url(cfg["query"])))
            kept = 0
            for e in entries:
                title = e["title"]
                source = e["source"] or "Google News掲載媒体"
                summary = e["description"]
                if not should_keep(title, summary, source):
                    continue
                item = {
                    "title": title, "summary": summary, "url": e["link"], "publishedAt": parse_date(e["date"]).isoformat(),
                    "source": source, "sourceHome": e["source_home"], "group": cfg["group"], "kind": "media",
                    "category": classify_category(title, summary), "area": classify_area(title, summary),
                }
                if item["url"]:
                    existing[item["url"]] = {**existing.get(item["url"], {}), **item}
                    kept += 1
            statuses.append({"name": cfg["name"], "ok": True, "count": kept, "type": "google-news"}); success += 1
        except Exception as exc:
            statuses.append({"name": cfg["name"], "ok": False, "error": str(exc)[:200], "type": "google-news"})
    return success


def load_existing():
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"news": []}


def expand_existing_news(news):
    out = {}
    for item in news:
        base = {k: v for k, v in item.items() if k not in ("relatedSources", "coverageCount", "importanceScore", "isBreaking", "isImportant", "isOpeningClosing", "isWeekendEvent", "id", "detailUrl")}
        if base.get("url"):
            out[base["url"]] = base
        for src in item.get("relatedSources", []) or []:
            url = src.get("url")
            if not url:
                continue
            clone = dict(base); clone.update({"url": url, "source": src.get("source", base.get("source")), "sourceHome": src.get("sourceHome", ""), "publishedAt": src.get("publishedAt", base.get("publishedAt"))})
            out[url] = clone
    return out


def norm_title(title):
    s = unicodedata.normalize("NFKC", title or "").lower()
    s = re.sub(r"\s*[-–—|｜].*$", "", s)
    return TITLE_PUNCT_RE.sub("", s)


def title_similarity(a, b):
    na, nb = norm_title(a), norm_title(b)
    if not na or not nb: return 0
    if na in nb or nb in na: return min(len(na), len(nb)) / max(len(na), len(nb))
    return difflib.SequenceMatcher(None, na, nb).ratio()


def story_id(item):
    basis = f"{item.get('title','')}|{item.get('url','')}"
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:14]


def importance_score(cluster):
    hay = " ".join(f"{x.get('title','')} {x.get('summary','')}" for x in cluster)
    score = 0
    if any(x in hay for x in BREAKING_TERMS): score += 60
    if any(x in hay for x in IMPORTANT_TERMS): score += 20
    score += min(20, (len(cluster)-1)*8)
    try:
        newest = max(parse_date(x.get("publishedAt", "")) for x in cluster)
        age_h = max(0, (datetime.now(JST)-newest).total_seconds()/3600)
        if age_h <= 6: score += 18
        elif age_h <= 24: score += 12
        elif age_h <= 72: score += 6
    except Exception: pass
    return score


def is_weekend_event_cluster(cluster, weekend_start, weekend_end):
    for item in cluster:
        ranges = deserialize_event_ranges(item)
        if ranges and ranges_intersect(ranges, weekend_start, weekend_end):
            return True
    return False


def merge_cluster(cluster):
    cluster.sort(key=lambda x: str(x.get("publishedAt", "")), reverse=True)
    primary = dict(cluster[0])
    related = []
    seen = set()
    for x in cluster:
        key = (x.get("source"), x.get("url"))
        if key in seen: continue
        seen.add(key)
        related.append({"source": x.get("source"), "url": x.get("url"), "sourceHome": x.get("sourceHome", ""), "publishedAt": x.get("publishedAt")})
    score = importance_score(cluster)
    hay = " ".join(f"{x.get('title','')} {x.get('summary','')}" for x in cluster)
    primary["coverageCount"] = len(related)
    primary["relatedSources"] = related
    primary["importanceScore"] = score
    primary["isBreaking"] = any(x in hay for x in BREAKING_TERMS)
    primary["isImportant"] = score >= 42
    primary["isOpeningClosing"] = any(detect_opening_closing(x.get("title", ""), x.get("summary", "")) for x in cluster)
    ws, we = weekend_bounds()
    primary["isWeekendEvent"] = is_weekend_event_cluster(cluster, ws, we)
    if primary["isWeekendEvent"]:
        best = max(cluster, key=lambda x: int(x.get("eventDateConfidence", 0) or 0))
        for k in ("eventDates", "eventDateLabel", "eventDateSource", "eventDateConfidence", "eventDateEvidence"):
            if best.get(k): primary[k] = best[k]
    primary["id"] = story_id(primary)
    primary["detailUrl"] = f"news/{primary['id']}.html"
    return primary


def cluster_items(items):
    items.sort(key=lambda x: str(x.get("publishedAt", "")), reverse=True)
    clusters = []
    for item in items:
        placed = False
        for c in clusters:
            if title_similarity(item.get("title", ""), c[0].get("title", "")) >= 0.77:
                c.append(item); placed = True; break
        if not placed: clusters.append([item])
    merged = [merge_cluster(c) for c in clusters]
    merged.sort(key=lambda x: str(x.get("publishedAt", "")), reverse=True)
    return merged


def weekend_label(a, b):
    return f"{a.month}月{a.day}日（土）〜{b.month}月{b.day}日（日）"


def article_page_html(item, generated_at):
    title = html.escape(str(item.get("title", "いわきNOW")))
    summary = html.escape(str(item.get("summary", "元記事で詳細をご確認ください。")))
    source = html.escape(str(item.get("source", "")))
    source_url = html.escape(str(item.get("url", "#")), quote=True)
    category = html.escape(str(item.get("category", "暮らし")))
    area = html.escape(str(item.get("area", "全市")))
    published = html.escape(str(item.get("publishedAt", ""))[:16].replace("T", " "))
    coverage = int(item.get("coverageCount", 1) or 1)
    related = item.get("relatedSources", []) or []
    sources_html = []
    for src in related:
        src_name = html.escape(str(src.get("source", "出典")))
        src_url = html.escape(str(src.get("url", "#")), quote=True)
        src_time = html.escape(str(src.get("publishedAt", ""))[:16].replace("T", " "))
        sources_html.append(f'<li><div><strong>{src_name}</strong><span>{src_time}</span></div><a href="{src_url}" target="_blank" rel="noopener noreferrer">元記事を読む ↗</a></li>')
    breaking_html = '<span class="breaking-badge">速報・重要</span>' if item.get("isBreaking") or item.get("isImportant") else ""
    shop_html = '<span class="feature-badge shop">開店・閉店</span>' if item.get("isOpeningClosing") else ""
    weekend_html = '<span class="feature-badge weekend">今週末</span>' if item.get("isWeekendEvent") else ""
    coverage_html = f'<span class="coverage-badge">{coverage}媒体が掲載</span>' if coverage > 1 else ""
    event_schedule_html = ""
    if item.get("eventDateLabel"):
        event_label = html.escape(str(item.get("eventDateLabel")))
        event_source = html.escape(str(item.get("eventDateSource", "公開ページ")))
        event_schedule_html = f'<section class="event-schedule-detail"><span>開催日程</span><strong>{event_label}</strong><small>日程確認：{event_source}</small></section>'
    return f'''<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title} | いわきNOW</title><meta name="description" content="{summary[:150]}"><link rel="stylesheet" href="../styles.css"></head><body class="article-page"><header class="site-header"><div class="wrap header-main"><a class="brand" href="../"><span class="brand-iwaki">いわき</span><span class="brand-now">NOW</span></a><p class="tagline">今日のいわきを、ひと目で。</p></div></header><main class="wrap article-page-main"><nav class="breadcrumb"><a href="../">トップ</a><span>›</span><span>{category}</span></nav><article class="detail-article"><div class="detail-badges"><span class="category-chip">{category}</span><span class="area-chip">{area}</span>{breaking_html}{shop_html}{weekend_html}{coverage_html}</div><h1>{title}</h1><div class="detail-meta"><time>{published}</time><span>主な出典：{source}</span></div>{event_schedule_html}<section class="detail-summary"><h2>概要</h2><p>{summary}</p></section><section class="detail-sources"><h2>このニュースの出典</h2><p>同じ出来事を複数媒体が掲載している場合は、まとめて表示しています。</p><ul>{''.join(sources_html)}</ul></section><div class="detail-actions"><a class="source-button" href="{source_url}" target="_blank" rel="noopener noreferrer">主な出典で確認する ↗</a><a class="back-button" href="../">いわきNOWへ戻る</a></div><p class="detail-note">いわきNOWは公開情報の見出し・概要を整理する地域ニュース集約サイトです。重要な情報は必ず出典元でご確認ください。</p></article></main><footer class="site-footer"><div class="wrap footer-row"><div><strong>いわきNOW</strong><p>福島県いわき市の地域情報を、短く、分かりやすく。</p></div></div></footer></body></html>'''


def render_article_pages(items, generated_at):
    NEWS_DIR.mkdir(parents=True, exist_ok=True)
    wanted = set()
    for item in items:
        filename = f"{item['id']}.html"; wanted.add(filename)
        (NEWS_DIR / filename).write_text(article_page_html(item, generated_at), encoding="utf-8")
    for path in NEWS_DIR.glob("*.html"):
        if path.name not in wanted and path.name != "index.html": path.unlink()
    (NEWS_DIR / "index.html").write_text('<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=../"><title>いわきNOW</title><a href="../">いわきNOWへ戻る</a>', encoding="utf-8")


def enrich_and_write(raw_items, statuses=None):
    cutoff = datetime.now(JST) - timedelta(days=MAX_AGE_DAYS)
    eligible = []
    for n in raw_items:
        dt = parse_date(str(n.get("publishedAt", "")))
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
    payload = {"generatedAt": generated_at, "collectorVersion": "5.1", "weekend": {"start": weekend_start.isoformat(), "end": weekend_end.isoformat(), "label": weekend_label(weekend_start, weekend_end)}, "featureCounts": {"openingClosing": sum(1 for n in items if n.get("isOpeningClosing")), "weekendEvents": sum(1 for n in items if n.get("isWeekendEvent"))}, "sourceCount": len(active_sources), "sources": active_sources, "collectorStatus": statuses or [], "news": items}
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    render_article_pages(items, generated_at)
    print(f"Wrote {len(items)} clustered stories from {len(active_sources)} publishers")
    return payload


def render_only():
    old = load_existing(); raw = list(expand_existing_news(old.get("news", [])).values()); enrich_and_write(raw, old.get("collectorStatus", [])); return 0


def self_test():
    start = datetime(2026, 8, 15, 12, tzinfo=JST).date(); end = datetime(2026, 8, 16, 12, tzinfo=JST).date()
    r = extract_event_ranges("開催期間：2026年8月11日(火祝)～16日(日)", 2026)
    assert r == [(_safe_date(2026, 8, 11), _safe_date(2026, 8, 16))], r
    assert ranges_intersect(r, start, end)
    r2 = extract_event_ranges("開催日：令和8年8月29日（土）", 2026)
    assert r2 == [(_safe_date(2026, 8, 29), _safe_date(2026, 8, 29))], r2
    city_fixture = '''<html><body><h2>2026年8月の情報</h2><table><tr><th>15日(土曜日)</th><td><img alt="イベント・祭り"><a href="/event/a">海辺の夏イベント</a></td></tr><tr><th>16日(日曜日)</th><td><img alt="イベント・祭り"><a href="/event/a">海辺の夏イベント</a><a href="/event/b">日曜マルシェ</a></td></tr><tr><th>16日(日曜日)</th><td><img alt="相談"><a href="/consult/c">法律相談</a></td></tr></table></body></html>'''
    parsed = parse_city_calendar_page(city_fixture, "https://www.city.iwaki.lg.jp/calendar", 2026, 8); by_title = {x[0]: x for x in parsed}
    assert format_event_ranges(by_title["海辺の夏イベント"][2]) == "8月15日〜16日", parsed
    assert "法律相談" not in by_title, parsed
    tourism_fixture = '''<html><body><article><h3><a href="/event/12345">ほるる de 夏休みイベント2026</a></h3><p>開催期間：2026年8月11日(火祝)～16日(日)</p></article></body></html>'''
    tourism = parse_event_listing_page(tourism_fixture, "https://kankou-iwaki.or.jp/event", site_kind="tourism")
    assert tourism and format_event_ranges(tourism[0][2]) == "8月11日〜16日", tourism
    city_list_fixture = '''<html><body><main><ul class="event-list"><li><h3><a href="/www/contents/1780000000001/index.html">本物の夏イベント</a></h3><p>開催日：2026年8月16日</p><p>会場：平地区</p></li></ul></main><footer><a href="/www/contents/1000000000001/index.html">市役所へのアクセス</a><a href="/www/contents/1000000000002/index.html">このサイトについて</a><a href="/www/contents/1000000000003/index.html">ウェブアクセシビリティについて</a></footer></body></html>'''
    city_list = parse_event_listing_page(city_list_fixture, "https://www.city.iwaki.lg.jp/www/genre/1452741939257/index.html", site_kind="city")
    assert len(city_list) == 1, city_list
    assert city_list[0][0] == "本物の夏イベント", city_list
    assert "市役所へのアクセス" not in {x[0] for x in city_list}, city_list
    article_fixture = '''<html><body><p>登録日：2026年8月5日</p><main><h1>夏のイベント</h1><section><h2>開催概要</h2><p>イベント開催期間 2026年8月11日(火祝)～8月16日(日)</p></section></main></body></html>'''
    aranges, evidence, confidence = extract_schedule_from_article(article_fixture, "https://kankou-iwaki.or.jp/event/1", 2026)
    assert ranges_intersect(aranges, start, end), (aranges, evidence)
    assert confidence >= 90
    cluster = [{"title": "夏休みイベント", "summary": "開催します", "category": "イベント", "area": "常磐", "publishedAt": "2026-08-14T10:00:00+09:00", "eventDates": serialize_event_ranges(aranges), "eventDateConfidence": 95, "eventDateSource": "公式ページ"}]
    assert is_weekend_event_cluster(cluster, start, end)
    print("Self-test OK: weekend date parsing, city calendar, navigation-safe city event listing, tourism listing, article body")
    return 0


def main():
    old = load_existing(); existing = expand_existing_news(old.get("news", [])); statuses = []
    successful_sources = collect_direct(existing, statuses)
    successful_sources += collect_google_news(existing, statuses)
    successful_sources += collect_official_events(existing, statuses)
    enrich_event_article_bodies(existing, statuses)
    if not successful_sources:
        print("No sources fetched successfully; keeping existing stories and regenerating pages."); return render_only()
    enrich_and_write(list(existing.values()), statuses); return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv: raise SystemExit(self_test())
    if "--render-only" in sys.argv: raise SystemExit(render_only())
    raise SystemExit(main())
