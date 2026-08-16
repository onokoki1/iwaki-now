#!/usr/bin/env python3
"""Remove stale/non-event navigation entries from generated news data.

This is a defensive post-processing step. The main collector already avoids
navigation links, but older bad entries can survive because existing news.json
is carried forward between runs. Running this after collection guarantees that
navigation pages never appear in feature counts or on the published site.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_FILE = ROOT / "data" / "news.json"
NEWS_DIR = ROOT / "news"
JST = timezone(timedelta(hours=9))

NAVIGATION_TITLES = {
    "市役所へのアクセス",
    "このサイトについて",
    "情報提供指針",
    "著作権・リンクについて",
    "ウェブアクセシビリティについて",
    "セキュリティーポリシー",
    "サイトマップ",
    "組織一覧・各部署連絡先",
    "組織一覧",
    "各部署連絡先",
    "お問い合わせ",
    "プライバシーポリシー",
    "利用規約",
    "AIチャットボット",
}

NAVIGATION_PARTIALS = (
    "著作権・リンク",
    "ウェブアクセシビリティ",
    "セキュリティーポリシー",
    "このサイトについて",
    "市役所へのアクセス",
    "情報提供指針",
)


def is_navigation_item(item: dict) -> bool:
    title = str(item.get("title", "")).strip()
    if title in NAVIGATION_TITLES:
        return True
    return any(term in title for term in NAVIGATION_PARTIALS)


def main() -> int:
    payload = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    news = payload.get("news", []) or []

    kept = []
    removed = []
    for item in news:
        if is_navigation_item(item):
            removed.append(item)
        else:
            kept.append(item)

    payload["news"] = kept
    payload.setdefault("featureCounts", {})
    payload["featureCounts"]["openingClosing"] = sum(1 for n in kept if n.get("isOpeningClosing"))
    payload["featureCounts"]["weekendEvents"] = sum(1 for n in kept if n.get("isWeekendEvent"))
    payload["cleanup"] = {
        "cleanedAt": datetime.now(JST).isoformat(timespec="seconds"),
        "removedCount": len(removed),
        "removedTitles": [str(x.get("title", "")) for x in removed],
    }

    DATA_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for item in removed:
        detail = item.get("detailPath") or item.get("detailUrl")
        if not detail:
            continue
        path = ROOT / str(detail)
        try:
            if path.is_file() and NEWS_DIR in path.parents:
                path.unlink()
        except OSError:
            pass

    print(f"Cleanup complete: removed {len(removed)} navigation item(s).")
    print(f"Weekend events after cleanup: {payload['featureCounts']['weekendEvents']}")
    if removed:
        for item in removed:
            print(" -", item.get("title", ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
