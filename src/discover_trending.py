
import csv
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
OUTPUT = Path("output")
OUTPUT.mkdir(parents=True, exist_ok=True)

NOW = datetime.now(timezone.utc)
RECENT_AFTER = (NOW - timedelta(days=30)).isoformat(
    timespec="seconds"
).replace("+00:00", "Z")

SEARCHES = [
    {
        "label": "recent_creative_commons_gaming",
        "params": {
            "q": "gaming streamer funny moments",
            "type": "video",
            "videoCategoryId": "20",
            "videoLicense": "creativeCommon",
            "order": "date",
            "publishedAfter": RECENT_AFTER,
            "maxResults": "25",
            "safeSearch": "moderate",
        },
    },
    {
        "label": "active_gaming_live",
        "params": {
            "q": "gaming streamer",
            "type": "video",
            "eventType": "live",
            "order": "viewCount",
            "maxResults": "25",
            "safeSearch": "moderate",
        },
    },
]


def youtube_search(params):
    query = dict(params)
    query["part"] = "snippet"
    query["key"] = API_KEY
    url = (
        "https://www.googleapis.com/youtube/v3/search?"
        + urllib.parse.urlencode(query)
    )
    request = urllib.request.Request(
        url, headers={"User-Agent": "GamingShortsDiscovery/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main():
    if not API_KEY:
        raise SystemExit("Missing YOUTUBE_API_KEY GitHub secret.")

    videos = {}
    errors = []

    for search in SEARCHES:
        try:
            data = youtube_search(search["params"])
            for item in data.get("items", []):
                video_id = item.get("id", {}).get("videoId")
                snippet = item.get("snippet", {})
                if not video_id:
                    continue

                row = videos.setdefault(video_id, {
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

    results = list(videos.values())
    results.sort(
        key=lambda item: (
            item["channel_title"].casefold(),
            item["published_at"],
        )
    )

    report = {
        "generated_at_utc": NOW.isoformat(),
        "recent_window_days": 30,
        "mode": "DISCOVERY_ONLY_NO_DOWNLOAD",
        "candidate_count": len(results),
        "note": (
            "Candidates only. Verify license terms, uploader authority, "
            "third-party rights and reuse permission before using a video. "
            "Live streams are not automatically reusable."
        ),
        "errors": errors,
        "candidates": results,
    }

    (OUTPUT / "trending_candidates.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    columns = [
        "channel_title",
        "channel_id",
        "title",
        "published_at",
        "url",
        "found_by",
        "permission_review",
    ]
    with (OUTPUT / "trending_candidates.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for item in results:
            row = item.copy()
            row["found_by"] = ", ".join(row["found_by"])
            writer.writerow({key: row.get(key, "") for key in columns})

    print(f"Candidates found: {len(results)}")
    print("CSV and JSON reports saved in output/.")
    if errors:
        print("Search warnings:")
        for error in errors:
            print(f"- {error}")


if __name__ == "__main__":
    main()
