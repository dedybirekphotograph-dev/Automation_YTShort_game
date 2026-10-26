
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
MAX_PER_STREAMER = 10
REGION = "US"

# Nama pencarian awal. Hasil channel harus diperiksa sebelum digunakan.
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

BAD_TERMS = [
    "giveaway", "free robux", "24/7", "24 hours live",
    "nonstop live", "study music", "casino", "poker",
]

GOOD_TERMS = [
    "funny", "moments", "reaction", "clutch", "insane",
    "best", "rage", "fails", "highlights", "crazy",
    "stream", "gaming", "plays",
]

def youtube_api(resource, params):
    params["key"] = API_KEY
    url = (
        "https://www.googleapis.com/youtube/v3/"
        + resource + "?" + urlencode(params)
    )
    try:
        with urlopen(url, timeout=25) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"YouTube API HTTP {exc.code}: {detail[:500]}"
        ) from exc
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc}") from exc

def clean(value):
    return re.sub(r"\s+", " ", value or "").strip()

def search_channel(name):
    data = youtube_api("search", {
        "part": "snippet",
        "type": "channel",
        "q": name,
        "maxResults": 5,
        "regionCode": REGION,
    })
    return data.get("items", [])

def search_videos(channel_id, streamer, published_after):
    data = youtube_api("search", {
        "part": "snippet",
        "type": "video",
        "channelId": channel_id,
        "publishedAfter": published_after,
        "order": "date",
        "maxResults": MAX_PER_STREAMER,
        "videoCategoryId": "20",
        "regionCode": REGION,
        "safeSearch": "moderate",
    })
    return data.get("items", [])

def main():
    if not API_KEY:
        print("ERROR: GitHub Secret YOUTUBE_API_KEY is missing.")
        sys.exit(1)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=DAYS_BACK)
    published_after = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    candidates = []
    channels_found = []
    seen_video_ids = set()

    for streamer in STREAMERS:
        print(f"Searching channel: {streamer}")

        try:
            channel_items = search_channel(streamer)
        except Exception as exc:
            print(f"Channel search failed for {streamer}: {exc}")
            continue

        if not channel_items:
            print(f"No channel results for {streamer}")
            continue

        # Ambil kandidat channel teratas, tetapi jangan menganggapnya
        # otomatis resmi. Simpan nama dan URL agar dapat diperiksa.
        channel = channel_items[0]
        channel_id = channel.get("id", {}).get("channelId")
        snippet = channel.get("snippet", {})

        if not channel_id:
            continue

        channel_title = clean(snippet.get("title"))
        channel_url = f"https://www.youtube.com/channel/{channel_id}"

        channels_found.append({
            "requested_streamer": streamer,
            "channel_title": channel_title,
            "channel_id": channel_id,
            "channel_url": channel_url,
            "channel_identity_review": "NOT_VERIFIED",
        })

        try:
            videos = search_videos(
                channel_id, streamer, published_after
            )
        except Exception as exc:
            print(f"Video search failed for {streamer}: {exc}")
            continue

        for item in videos:
            video_id = item.get("id", {}).get("videoId")
            if not video_id or video_id in seen_video_ids:
                continue

            video_snippet = item.get("snippet", {})
            title = clean(video_snippet.get("title"))
            title_lower = title.lower()

            if any(term in title_lower for term in BAD_TERMS):
                continue

            if not any(term in title_lower for term in GOOD_TERMS):
                # Simpan hanya judul yang berindikasi relevan.
                continue

            published_at = video_snippet.get("publishedAt", "")
            try:
                published_dt = datetime.fromisoformat(
                    published_at.replace("Z", "+00:00")
                )
                if published_dt < cutoff:
                    continue
            except ValueError:
                continue

            seen_video_ids.add(video_id)
            candidates.append({
                "requested_streamer": streamer,
                "channel_title": clean(
                    video_snippet.get("channelTitle")
                ),
                "channel_id": video_snippet.get("channelId", ""),
                "video_id": video_id,
                "title": title,
                "published_at": published_at,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "found_by": "famous_streamer_recent_search",
                "permission_review": "NOT_VERIFIED",
                "channel_identity_review": "NOT_VERIFIED",
                "downloaded": False,
                "edited": False,
            })

    # Ambil jumlah views untuk membantu menyusun prioritas.
    for start in range(0, len(candidates), 50):
        batch = candidates[start:start + 50]
        ids = ",".join(row["video_id"] for row in batch)
        if not ids:
            continue

        try:
            details = youtube_api("videos", {
                "part": "statistics,contentDetails",
                "id": ids,
                "maxResults": 50,
            })
            by_id = {
                item["id"]: item
                for item in details.get("items", [])
            }
            for row in batch:
                detail = by_id.get(row["video_id"], {})
                stats = detail.get("statistics", {})
                row["views"] = int(stats.get("viewCount", 0))
                row["likes"] = int(stats.get("likeCount", 0))
                row["duration"] = detail.get(
                    "contentDetails", {}
                ).get("duration", "")
                age_days = max(
                    1,
                    (now - datetime.fromisoformat(
                        row["published_at"].replace("Z", "+00:00")
                    )).days,
                )
                row["views_per_day_estimate"] = round(
                    row["views"] / age_days, 2
                )
        except Exception as exc:
            print(f"Statistics lookup failed: {exc}")
            for row in batch:
                row["views"] = 0
                row["likes"] = 0
                row["duration"] = ""
                row["views_per_day_estimate"] = 0

    candidates.sort(
        key=lambda row: (
            row.get("views_per_day_estimate", 0),
            row.get("views", 0),
        ),
        reverse=True,
    )

    report = {
        "generated_at": now.isoformat(),
        "recent_window_days": DAYS_BACK,
        "mode": "DISCOVERY_ONLY_NO_DOWNLOAD",
        "note": (
            "Channel identity and reuse permission are unverified. "
            "Verify the channel, video license, and reuse rights "
            "before obtaining or editing any video."
        ),
        "channels_found": channels_found,
        "candidates": candidates,
    }

    json_path = OUT / "trending_candidates.json"
    csv_path = OUT / "trending_candidates.csv"

    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "requested_streamer", "channel_title", "channel_id",
        "video_id", "title", "published_at", "url", "found_by",
        "views", "likes", "duration", "views_per_day_estimate",
        "channel_identity_review", "permission_review",
        "downloaded", "edited",
    ]

    with csv_path.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(candidates)

    channels_path = OUT / "channels_to_review.csv"
    with channels_path.open(
        "w", newline="", encoding="utf-8-sig"
    ) as file:
        fields_channels = [
            "requested_streamer", "channel_title",
            "channel_id", "channel_url",
            "channel_identity_review",
        ]
        writer = csv.DictWriter(
            file, fieldnames=fields_channels
        )
        writer.writeheader()
        writer.writerows(channels_found)

    print(f"Channels found: {len(channels_found)}")
    print(f"Video candidates: {len(candidates)}")
    print(f"Saved report: {json_path}")
    print(f"Saved channel review: {channels_path}")

if __name__ == "__main__":
    main()
