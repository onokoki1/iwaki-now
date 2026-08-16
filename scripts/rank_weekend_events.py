#!/usr/bin/env python3
"""Classify and rank weekend events for the public top page.

The collector determines whether an event overlaps the current weekend. This
post-processing step answers a different question: "Which events are especially
useful to surface this weekend?"

Rules intentionally favor short, time-sensitive, official-schedule events and
separate long-running exhibitions/museum programs into their own group.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "news.json"

LONG_EXHIBITION_TERMS = (
    "企画展", "特別展", "展覧会", "展示会", "展示", "美術館", "博物館", "文学館",
    "資料館", "記念館", "伝承郷", "考古資料館", "アンモナイトセンター",
)

WEEKEND_FRIENDLY_TERMS = (
    "祭", "まつり", "花火", "盆踊り", "マルシェ", "コンサート", "ライブ", "公演",
    "フェス", "ワークショップ", "体験", "自然体験", "観察", "教室", "講習", "大会",
    "ツアー", "上映", "縁日", "夜市", "おどり", "踊り",
)

SEASONAL_TERMS = (
    "海水浴場", "海開き", "常設", "通年", "年間", "プール見学", "摘み取り",
)


def parse_day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def event_ranges(item: dict) -> list[tuple[date, date]]:
    ranges: list[tuple[date, date]] = []
    for raw in item.get("eventDates", []) or []:
        a, b = parse_day(raw.get("start")), parse_day(raw.get("end"))
        if a and b:
            ranges.append((min(a, b), max(a, b)))
    if not ranges:
        a = parse_day(item.get("eventStart"))
        b = parse_day(item.get("eventEnd")) or a
        if a and b:
            ranges.append((min(a, b), max(a, b)))
    return sorted(ranges)


def overlapping_ranges(item: dict, weekend_start: date, weekend_end: date) -> list[tuple[date, date]]:
    return [(a, b) for a, b in event_ranges(item) if a <= weekend_end and b >= weekend_start]


def classify(item: dict, weekend_start: date, weekend_end: date) -> tuple[int, str, str, list[str]]:
    hay = f"{item.get('title', '')} {item.get('summary', '')} {item.get('eventDateEvidence', '')}"
    ranges = overlapping_ranges(item, weekend_start, weekend_end)
    if not ranges:
        return 0, "other", "日程確認中", []

    score = 50
    reasons: list[str] = []
    all_ranges = event_ranges(item)
    overall_start = min(a for a, _ in all_ranges)
    overall_end = max(b for _, b in all_ranges)
    total_days = (overall_end - overall_start).days + 1
    weekend_days = sum(1 for d in (weekend_start, weekend_end) if any(a <= d <= b for a, b in all_ranges))

    single_day = any(a == b and weekend_start <= a <= weekend_end for a, b in all_ranges)
    short_event = total_days <= 2
    starts_weekend = overall_start in (weekend_start, weekend_end)
    ends_weekend = overall_end in (weekend_start, weekend_end)
    official = int(item.get("eventDateConfidence") or 0) >= 95
    long_exhibition = total_days >= 14 and any(term in hay for term in LONG_EXHIBITION_TERMS)

    if single_day:
        score += 30
        reasons.append("単日開催")
    elif short_event:
        score += 22
        reasons.append("週末中心")

    if starts_weekend:
        score += 18
        reasons.append("今週末スタート")
    if ends_weekend:
        score += 24
        reasons.append("今週末まで")
    if weekend_days == 2 and total_days <= 3:
        score += 8
    if official:
        score += 8
        reasons.append("公式日程")
    if any(term in hay for term in WEEKEND_FRIENDLY_TERMS):
        score += 10
        reasons.append("体験・催事")

    if total_days >= 14:
        score -= 14
    if total_days >= 30:
        score -= 10
    if total_days >= 60:
        score -= 8
    if any(term in hay for term in SEASONAL_TERMS) and total_days >= 14:
        score -= 8
    if long_exhibition:
        score -= 28

    if long_exhibition:
        group = "long-running"
        label = "長期開催・企画展"
    else:
        group = "weekend-pick"
        if ends_weekend:
            label = "今週末まで"
        elif single_day:
            label = "今週末限定"
        elif starts_weekend:
            label = "今週末スタート"
        elif total_days <= 3:
            label = "週末開催"
        else:
            label = "今週末開催中"

    return max(0, score), group, label, reasons[:3]


def main() -> int:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    weekend = payload.get("weekend", {}) or {}
    weekend_start = parse_day(weekend.get("start"))
    weekend_end = parse_day(weekend.get("end"))
    if not weekend_start or not weekend_end:
        raise SystemExit("weekend.start/end missing from data/news.json")

    picks = long_running = 0
    for item in payload.get("news", []) or []:
        if not item.get("isWeekendEvent"):
            item.pop("weekendRankScore", None)
            item.pop("weekendEventGroup", None)
            item.pop("weekendRankLabel", None)
            item.pop("weekendRankReasons", None)
            item.pop("isLongRunningEvent", None)
            continue

        score, group, label, reasons = classify(item, weekend_start, weekend_end)
        item["weekendRankScore"] = score
        item["weekendEventGroup"] = group
        item["weekendRankLabel"] = label
        item["weekendRankReasons"] = reasons
        item["isLongRunningEvent"] = group == "long-running"
        if group == "long-running":
            long_running += 1
        else:
            picks += 1

    counts = payload.setdefault("featureCounts", {})
    counts["weekendPicks"] = picks
    counts["longRunningWeekendEvents"] = long_running
    payload["weekendRanking"] = {
        "rankedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "ruleVersion": "1.0",
        "weekendPicks": picks,
        "longRunning": long_running,
    }

    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Weekend ranking complete: {picks} weekend picks, {long_running} long-running exhibitions/programs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
