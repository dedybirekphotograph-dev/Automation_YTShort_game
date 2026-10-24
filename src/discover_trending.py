
import csv
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
OUTPUT = Path("output")
OUTPUT.mkdir(parents=True, exist_ok=True)

# Batasi dua pencarian per workflow agar kuota tidak cepat habis.
SEARCHES = [
    {
        "label": "creative_commons_gaming",
        "params": {
            "q": "gaming best moments streamer",
            "type": "video",
            "videoCategoryId": "20",
            "videoLicense": "creativeCommon",
            "order": "viewCount",
            "publishedAfter": None,
            "maxResults": "25",
            "regionCode": "US",
            "safeSearch": "moderate",
        },
    },
    {
        "label": "active_gaming_live",
        "params": {
            "q": "gaming live streamer",
            "type": "video",
            "eventType": "live",
            "order": "viewCount",
            "maxResults": "25",
            "regionCode": "US",
            "safeSearch": "moderate",
        },
    },
]


def youtube_search(params):
    clean = {k: v for k, v in params.items() if v is not None}
    clean["part"] = "snippet"
    clean["key"] = API_KEY
    url = (
        "https://www.googleapis.com/youtube/v3/search?"
        + urllib.parse.urlencode(clean)
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "Automation_YTShort_game/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    if not API_KEY:
        raise SystemExit(
            "Missing YOUTUBE_API_KEY. Add it under "
            "Repository Settings > Secrets and variables > Actions."
        )

    collected = {}
    errors = []

    for search in SEARCHES:
        try:
            payload = youtube_search(search["params"])
            for item in payload.get("items", []):
                video_id = item.get("id", {}).get("videoId")
                snippet = item.get("snippet", {})
                if not video_id:
                    continue

                row = collected.setdefault(video_id, {
                    "video_id": video_id,
                    "title": snippet.get("title", ""),
                    "channel_title": snippet.get("channelTitle", ""),
                    "channel_id": snippet.get("channelId", ""),
                    "published_at": snippet.get("publishedAt", ""),
                    "description": snippet.get("description", ""),
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "found_by": [],
                    "permission_review": "NOT_VERIFIED",
                    "downloaded": False,
                    "edited": False,
                })
                row["found_by"].append(search["label"])
        except Exception as exc:
            errors.append(f"{search['label']}: {exc}")

    results = list(collected.values())
    results.sort(key=lambda x: (x["channel_title"].lower(), x["published_at"]))

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "DISCOVERY_ONLY_NO_DOWNLOAD",
        "candidate_count": len(results),
        "note": (
            "Search results are candidates only. Confirm ownership, "
            "license terms, permission and all third-party rights before "
            "downloading, editing or reusing any video. Live streams are "
            "not automatically reusable."
        ),
        "errors": errors,
        "candidates": results,
    }

    json_path = OUTPUT / "trending_candidates.json"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    csv_path = OUTPUT / "trending_candidates.csv"
    columns = [
        "channel_title", "channel_id", "title", "published_at",
        "url", "found_by", "permission_review",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for item in results:
            row = item.copy()
            row["found_by"] = ", ".join(row["found_by"])
            writer.writerow({key: row.get(key, "") for key in columns})

    print(f"Found {len(results)} candidate videos.")
    print(f"Saved report: {json_path}")
    print(f"Saved spreadsheet-compatible CSV: {csv_path}")
    if errors:
        print("Some searches failed:")
        for error in errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
