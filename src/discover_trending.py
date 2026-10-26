
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from urllib.error import HTTPError, URLError

API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
CHANNEL_FILE = Path("assets/approved_channels.json")
OUT = Path("output")
OUT.mkdir(parents=True, exist_ok=True)

DAYS_BACK = 30
MAX_RESULTS = 50
MIN_SECONDS = 30
MAX_SECONDS = 900

GOOD_TERMS = [
    "highlight", "highlights", "funny", "moments",
    "clutch", "reaction", "clips", "insane",
    "best moments", "rage", "fails", "crazy",
    "plays", "gaming",
]

BAD_TERMS = [
    "giveaway", "free robux", "24/7", "nonstop live",
    "study music", "casino", "poker", "full stream",
    "full vod", "full episode",
]

def api_get(resource, params):
    query = dict(params)
    query["key"] = API_KEY
    url = (
        "https://www.googleapis.com/youtube/v3/"
        + resource + "?" + urlencode(query)
    )
    try:
        with urlopen(url, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"YouTube API HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

def parse_duration(value):
    match = re.fullmatch(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        value or ""
    )
    if not match:
        return None
    return (
        int(match.group(1) or 0) * 3600
        + int(match.group(2) or 0) * 60
        + int(match.group(3) or 0)
    )

def load_approved_channels():
    if not CHANNEL_FILE.exists():
        raise RuntimeError(
            f"Missing file: {CHANNEL_FILE}"
        )

    data = json.loads(CHANNEL_FILE.read_text(encoding="utf-8"))
    channels = data.get("channels", [])

    # Require both channel approval and confirmed reuse permission.
    approved = [
        c for c in channels
        if c.get("approved") is True
        and c.get("permission_confirmed") is True
        and c.get("channel_id")
        and c.get("name")
    ]

    return approved

def search_highlights(channel, published_after):
    data = api_get("search", {
        "part": "snippet",
        "type": "video",
        "channelId": channel["channel_id"],
        "publishedAfter": published_after,
        "order": "date",
        "maxResults": MAX_RESULTS,
        "safeSearch": "moderate",
    })
    return data.get("items", [])

def get_video_details(ids):
    result = {}
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        if not batch:
            continue

        data = api_get("videos", {
            "part": "snippet,statistics,contentDetails,status",
            "id": ",".join(batch),
            "maxResults": 50,
        })
        for item in data.get("items", []):
            result[item["id"]] = item
    return result

def main():
    if not API_KEY:
        raise RuntimeError("Missing YOUTUBE_API_KEY secret.")

    channels = load_approved_channels()

    if not channels:
        print(
            "No approved channels. Review assets/"
            "approved_channels.json first."
        )
        print("No videos were searched.")
        return

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DAYS_BACK)
    after = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates = []
    seen = set()

    for channel in channels:
        print("Searching approved channel:", channel["name"])
        try:
            items = search_highlights(channel, after)
        except Exception as exc:
            print("Search error:", exc)
            continue

        for item in items:
            video_id = item.get("id", {}).get("videoId")
            snippet = item.get("snippet", {})
            title = re.sub(
                r"\s+", " ", snippet.get("title", "")
            ).strip()
            lower = title.lower()

            if not video_id or video_id in seen:
                continue
            if any(term in lower for term in BAD_TERMS):
                continue
            if not any(term in lower for term in GOOD_TERMS):
                continue

            published = snippet.get("publishedAt", "")
            try:
                date = datetime.fromisoformat(
                    published.replace("Z", "+00:00")
                )
            except ValueError:
                continue
            if date < cutoff:
                continue

            seen.add(video_id)
            candidates.append({
                "requested_streamer": channel["name"],
                "channel_title": snippet.get("channelTitle", ""),
                "channel_id": channel["channel_id"],
                "video_id": video_id,
                "title": title,
                "published_at": published,
                "url": (
                    "https://www.youtube.com/watch?v=" + video_id
                ),
                "duration": "",
                "duration_seconds": "",
                "views": 0,
                "likes": 0,
                "views_per_day_estimate": 0,
                "channel_identity_review": "MANUALLY_APPROVED",
                "permission_review": "PERMISSION_RECORDED_CHECK_DETAILS",
                "downloaded": False,
                "edited": False,
            })

    details = get_video_details([
        row["video_id"] for row in candidates
    ]) if candidates else {}

    filtered = []
    for row in candidates:
        item = details.get(row["video_id"], {})
        seconds = parse_duration(
            item.get("contentDetails", {}).get("duration", "")
        )
        if seconds is None or not MIN_SECONDS <= seconds <= MAX_SECONDS:
            continue

        # Reuse permission must still be checked for each specific video.
        row["duration"] = item.get(
            "contentDetails", {}
        ).get("duration", "")
        row["duration_seconds"] = seconds
        stats = item.get("statistics", {})
        row["views"] = int(stats.get("viewCount", 0))
        row["likes"] = int(stats.get("likeCount", 0))

        try:
            published = datetime.fromisoformat(
                row["published_at"].replace("Z", "+00:00")
            )
            age_days = max(
                1, (now - published).total_seconds() / 86400
            )
        except ValueError:
            age_days = 1

        row["views_per_day_estimate"] = round(
            row["views"] / age_days, 2
        )
        filtered.append(row)

    filtered.sort(
        key=lambda r: (
            r["views_per_day_estimate"], r["views"]
        ),
        reverse=True,
    )

    report = {
        "generated_at": now.isoformat(),
        "mode": "DISCOVERY_ONLY_NO_DOWNLOAD_NO_EDIT",
        "recent_window_days": DAYS_BACK,
        "duration_range_seconds": [MIN_SECONDS, MAX_SECONDS],
        "channels_searched": [c["name"] for c in channels],
        "candidates": filtered,
        "note": (
            "Channel approval and video reuse permission are separate. "
            "Permission evidence must be checked for each video. "
            "This script does not download, edit, or upload videos."
        ),
    }

    (OUT / "trending_candidates.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "requested_streamer", "channel_title", "channel_id",
        "video_id", "title", "published_at", "url",
        "duration", "duration_seconds", "views", "likes",
        "views_per_day_estimate", "channel_identity_review",
        "permission_review", "downloaded", "edited",
    ]

    with (OUT / "trending_candidates.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(filtered)

    print("Approved channels searched:", len(channels))
    print("Eligible highlight candidates:", len(filtered))
    print("Report:", OUT / "trending_candidates.csv")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print("ERROR:", exc)
        sys.exit(1)
