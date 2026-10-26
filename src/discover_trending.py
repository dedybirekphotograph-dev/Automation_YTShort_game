
import csv
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
OUT = Path("output")
OUT.mkdir(parents=True, exist_ok=True)

DAYS_BACK = 30
MAX_RESULTS_PER_CHANNEL = 50
MIN_DURATION_SECONDS = 30
MAX_DURATION_SECONDS = 15 * 60
REGION = "US"

# Nama awal untuk pencarian channel.
# Hasil teratas belum tentu channel resmi; wajib diperiksa.
STREAMERS = [
    "Kai Cenat",
    "IShowSpeed",
    "xQc",
    "Jynxzi",
    "CaseOh",
    "TheBurntPeanut",
    "Ninja",
    "Typical Gamer",
    "Ludwig",
    "moistcr1tikal",
]

# Kata kunci untuk menemukan highlight yang sudah diedit.
SEARCH_QUERY = (
    "highlights|funny moments|gaming clips|clutch|"
    "best moments|streamer reaction|insane moments"
)

GOOD_TERMS = [
    "highlight", "highlights", "funny", "moments",
    "clutch", "reaction", "clips", "insane",
    "best moments", "rage", "fails", "crazy",
    "plays", "streamer", "gaming",
]

BAD_TERMS = [
    "giveaway", "free robux", "24/7", "24 hours live",
    "nonstop live", "study music", "casino", "poker",
    "full stream", "full vod", "full episode",
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
            f"YouTube API HTTP {exc.code}: {detail[:700]}"
        ) from exc
    except (URLError, TimeoutError) as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()

def parse_duration(value):
    """Convert ISO 8601 duration, such as PT4M32S, to seconds."""
    match = re.fullmatch(
        r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?",
        value or "",
    )
    if not match:
        return None

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds

def find_channel(streamer):
    data = api_get("search", {
        "part": "snippet",
        "type": "channel",
        "q": streamer,
        "maxResults": 5,
        "regionCode": REGION,
    })

    items = data.get("items", [])
    if not items:
        return None

    # Pilihan pertama hanya kandidat, bukan verifikasi resmi.
    item = items[0]
    channel_id = item.get("id", {}).get("channelId")
    snippet = item.get("snippet", {})

    if not channel_id:
        return None

    return {
        "requested_streamer": streamer,
        "channel_title": clean(snippet.get("title")),
        "channel_id": channel_id,
        "channel_url": (
            f"https://www.youtube.com/channel/{channel_id}"
        ),
        "channel_identity_review": "NOT_VERIFIED",
    }

def find_highlights(channel_id, published_after):
    data = api_get("search", {
        "part": "snippet",
        "type": "video",
        "channelId": channel_id,
        "q": SEARCH_QUERY,
        "publishedAfter": published_after,
        "order": "date",
        "maxResults": MAX_RESULTS_PER_CHANNEL,
        "videoCategoryId": "20",
        "regionCode": REGION,
        "safeSearch": "moderate",
    })
    return data.get("items", [])

def fetch_video_details(video_ids):
    details = {}

    # videos.list costs much less quota than search.list.
    for start in range(0, len(video_ids), 50):
        batch = video_ids[start:start + 50]
        if not batch:
            continue

        data = api_get("videos", {
            "part": "statistics,contentDetails,snippet,status",
            "id": ",".join(batch),
            "maxResults": 50,
        })

        for item in data.get("items", []):
            details[item["id"]] = item

    return details

def main():
    if not API_KEY:
        print("ERROR: Secret YOUTUBE_API_KEY is missing.")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DAYS_BACK)
    published_after = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    channels = []
    candidates = []
    seen = set()

    for streamer in STREAMERS:
        print(f"Searching streamer: {streamer}")

        try:
            channel = find_channel(streamer)
        except Exception as exc:
            print(f"Channel search failed: {exc}")
            continue

        if not channel:
            print(f"No channel candidate found: {streamer}")
            continue

        channels.append(channel)

        try:
            videos = find_highlights(
                channel["channel_id"], published_after
            )
        except Exception as exc:
            print(f"Video search failed for {streamer}: {exc}")
            continue

        for item in videos:
            video_id = item.get("id", {}).get("videoId")
            snippet = item.get("snippet", {})

            if not video_id or video_id in seen:
                continue

            title = clean(snippet.get("title"))
            title_lower = title.lower()

            if any(term in title_lower for term in BAD_TERMS):
                continue

            if not any(term in title_lower for term in GOOD_TERMS):
                continue

            published_at = snippet.get("publishedAt", "")
            try:
                published_dt = datetime.fromisoformat(
                    published_at.replace("Z", "+00:00")
                )
            except ValueError:
                continue

            if published_dt < cutoff:
                continue

            seen.add(video_id)
            candidates.append({
                "requested_streamer": streamer,
                "channel_title": clean(
                    snippet.get("channelTitle")
                ),
                "channel_id": snippet.get("channelId", ""),
                "video_id": video_id,
                "title": title,
                "published_at": published_at,
                "url": (
                    f"https://www.youtube.com/watch?v={video_id}"
                ),
                "found_by": "famous_streamer_highlights",
                "duration_seconds": "",
                "duration": "",
                "views": 0,
                "likes": 0,
                "views_per_day_estimate": 0,
                "channel_identity_review": "NOT_VERIFIED",
                "permission_review": "NOT_VERIFIED",
                "downloaded": False,
                "edited": False,
            })

    if candidates:
        try:
            details = fetch_video_details([
                row["video_id"] for row in candidates
            ])
        except Exception as exc:
            print(f"Video details lookup failed: {exc}")
            details = {}

        filtered = []

        for row in candidates:
            item = details.get(row["video_id"], {})
            content = item.get("contentDetails", {})
            stats = item.get("statistics", {})

            duration = content.get("duration", "")
            seconds = parse_duration(duration)

            # Reject videos outside the desired highlight length.
            if seconds is None:
                continue
            if not (
                MIN_DURATION_SECONDS
                <= seconds
                <= MAX_DURATION_SECONDS
            ):
                continue

            # Skip videos marked private or unavailable.
            status = item.get("status", {})
            if status.get("privacyStatus") == "private":
                continue

            views = int(stats.get("viewCount", 0))
            likes = int(stats.get("likeCount", 0))

            try:
                published_dt = datetime.fromisoformat(
                    row["published_at"].replace("Z", "+00:00")
                )
                age_days = max(
                    1, (now - published_dt).total_seconds() / 86400
                )
            except ValueError:
                age_days = 1

            row["duration"] = duration
            row["duration_seconds"] = seconds
            row["views"] = views
            row["likes"] = likes
            row["views_per_day_estimate"] = round(
                views / age_days, 2
            )

            filtered.append(row)

        candidates = filtered

    # Rank by estimated views/day, then total views.
    candidates.sort(
        key=lambda row: (
            row["views_per_day_estimate"],
            row["views"],
        ),
        reverse=True,
    )

    report = {
        "generated_at": now.isoformat(),
        "recent_window_days": DAYS_BACK,
        "duration_range_seconds": [
            MIN_DURATION_SECONDS,
            MAX_DURATION_SECONDS,
        ],
        "mode": "DISCOVERY_ONLY_NO_DOWNLOAD_NO_EDIT",
        "important_note": (
            "Search results are candidates only. Verify channel "
            "identity and reuse permission before using content. "
            "This script does not download, edit, or upload videos."
        ),
        "channels_to_review": channels,
        "candidates": candidates,
    }

    (OUT / "trending_candidates.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    video_fields = [
        "requested_streamer", "channel_title", "channel_id",
        "video_id", "title", "published_at", "url", "found_by",
        "duration", "duration_seconds", "views", "likes",
        "views_per_day_estimate", "channel_identity_review",
        "permission_review", "downloaded", "edited",
    ]

    with (OUT / "trending_candidates.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=video_fields)
        writer.writeheader()
        writer.writerows(candidates)

    channel_fields = [
        "requested_streamer", "channel_title",
        "channel_id", "channel_url", "channel_identity_review",
    ]

    with (OUT / "channels_to_review.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=channel_fields)
        writer.writeheader()
        writer.writerows(channels)

    print(f"Channels to review: {len(channels)}")
    print(f"Highlight candidates: {len(candidates)}")
    print(f"Saved: {OUT / 'trending_candidates.csv'}")
    print(f"Saved: {OUT / 'trending_candidates.json'}")
    print(f"Saved: {OUT / 'channels_to_review.csv'}")

if __name__ == "__main__":
    main()
