
import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError


ROOT = Path(".")
MANIFEST = ROOT / "assets" / "clips.json"
OUT = ROOT / "output"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
MAX_TOTAL = 90
CLIP_LIMIT = 5

SLOT_BY_CRON = {
    "0 1 * * *": 0,
    "0 5 * * *": 1,
    "0 9 * * *": 2,
    "0 13 * * *": 3,
}


def run(cmd):
    """Run a command and stop if it fails."""
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True)


def ffprobe_has_audio(path):
    """Return True if the input video contains an audio stream."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=codec_type",
            "-of", "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def ffprobe_duration(path):
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def validate_clip(clip):
    required = [
        "id",
        "file",
        "streamer",
        "source_url",
        "permission_evidence",
        "approved",
        "start_seconds",
        "duration_seconds",
        "moment_label",
    ]

    if not isinstance(clip, dict):
        return False, "clip entry must be an object"

    if any(key not in clip for key in required):
        return False, "missing required field"

    if clip["approved"] is not True:
        return False, "approved is not true"

    evidence = clip["permission_evidence"]
    if (
        not isinstance(evidence, str)
        or not evidence.strip()
        or "replace with" in evidence.lower()
    ):
        return False, "permission evidence is missing or still a placeholder"

    source_url = clip["source_url"]
    if not isinstance(source_url, str) or not source_url.startswith(
        ("https://", "http://")
    ):
        return False, "source_url must be an HTTP(S) URL"

    filename = clip["file"]
    if not isinstance(filename, str) or not filename.strip():
        return False, "invalid file path"

    path = (ROOT / filename).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        return False, "file path must stay inside the repository"

    if not path.is_file():
        return False, f"missing local video file: {filename}"

    try:
        start = float(clip["start_seconds"])
        duration = float(clip["duration_seconds"])
    except (ValueError, TypeError):
        return False, "invalid clip time"

    if start < 0 or duration < 3 or duration > 15:
        return False, "start must be >= 0 and duration must be 3-15 seconds"

    for key in ("id", "streamer", "moment_label"):
        if not isinstance(clip[key], str) or not clip[key].strip():
            return False, f"{key} must be a non-empty string"

    return True, ""


def escape_drawtext(value):
    """Escape common special characters for FFmpeg drawtext."""
    value = str(value)
    value = value.replace("\\", r"\\")
    value = value.replace(":", r"\:")
    value = value.replace("'", r"\'")
    value = value.replace("%", r"\%")
    value = value.replace(",", r"\,")
    value = value.replace("[", r"\[")
    value = value.replace("]", r"\]")
    value = value.replace("\n", " ")
    return value


def safe_metadata_text(value, max_length=100):
    """Normalize text for YouTube metadata."""
    value = re.sub(r"\s+", " ", str(value)).strip()
    return value[:max_length]


def make_video(clips, output_path):
    OUT.mkdir(exist_ok=True)
    work = OUT / "parts"
    work.mkdir(exist_ok=True)

    # Manifest order: best moment first (#1).
    # Playback order: #5 first and #1 last.
    ordered = list(reversed(clips))
    parts = []

    for index, clip in enumerate(ordered, start=1):
        rank = 6 - index
        source_path = (ROOT / clip["file"]).resolve()
        part_path = work / f"part_{index}.mp4"

        start = float(clip["start_seconds"])
        duration = float(clip["duration_seconds"])

        label = safe_metadata_text(
            f"RANK #{rank} | {clip['streamer']}", 100
        )
        source = safe_metadata_text(
            f"SOURCE: {clip['source_url']}", 240
        )

        vf = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
            "setsar=1,fps=30,"
            f"drawtext=text='{escape_drawtext(label)}':"
            "fontcolor=white:fontsize=54:borderw=4:"
            "bordercolor=black:x=(w-text_w)/2:y=90,"
            f"drawtext=text='{escape_drawtext(source)}':"
            "fontcolor=white:fontsize=25:borderw=3:"
            "bordercolor=black:x=35:y=h-120:"
            "box=1:boxcolor=black@0.45"
        )

        # Keep original audio when present.
        # If a source has no audio, add a silent audio stream so all
        # five segments have compatible streams for concatenation.
        has_audio = ffprobe_has_audio(source_path)

        command = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(source_path),
        ]

        if not has_audio:
            command += [
                "-f", "lavfi",
                "-t", str(duration),
                "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            ]

        command += [
            "-t", str(duration),
            "-map", "0:v:0",
        ]

        if has_audio:
            command += ["-map", "0:a:0"]
        else:
            command += ["-map", "1:a:0"]

        command += [
            "-vf", vf,
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "25",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-ar", "44100",
            "-af", "aresample=async=1:first_pts=0",
            "-t", str(duration),
            "-shortest",
            "-movflags", "+faststart",
            str(part_path),
        ]

        run(command)
        parts.append(part_path)

    concat_file = work / "concat.txt"

    # The output paths are generated internally in the output directory.
    concat_file.write_text(
        "".join(f"file '{part.resolve()}'\n" for part in parts),
        encoding="utf-8",
    )

    run([
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_file),
        "-t", str(MAX_TOTAL),
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "25",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-movflags", "+faststart",
        str(output_path),
    ])

    duration = ffprobe_duration(output_path)

    if duration > MAX_TOTAL:
        raise RuntimeError(
            f"Output is {duration:.1f}s, above {MAX_TOTAL}s cap"
        )

    return duration


def youtube_client():
    """Load OAuth credentials directly from the GitHub Secret."""
    raw = os.environ.get("YOUTUBE_TOKEN_JSON_B64")

    if not raw:
        raise RuntimeError(
            "Missing GitHub Secret YOUTUBE_TOKEN_JSON_B64"
        )

    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        token_data = json.loads(decoded)
    except Exception as exc:
        raise RuntimeError(
            "The GitHub Secret is not valid Base64-encoded OAuth JSON"
        ) from exc

    # Do not write OAuth credentials into output/ or workflow artifacts.
    creds = Credentials.from_authorized_user_info(
        token_data,
        SCOPES,
    )

    if creds.expired:
        if not creds.refresh_token:
            raise RuntimeError(
                "OAuth access token expired and no refresh token is available"
            )

        creds.refresh(Request())
        print("OAuth access token refreshed successfully.")

    if not creds.valid:
        raise RuntimeError(
            "OAuth credentials are invalid; check the Google OAuth setup"
        )

    return build(
        "youtube",
        "v3",
        credentials=creds,
        cache_discovery=False,
    )


def build_metadata(clips):
    """Build title, description, and tags from the selected clips."""
    best_clip = clips[0]

    streamer_names = []
    for clip in clips:
        name = safe_metadata_text(clip["streamer"], 40)
        if name.lower() not in [n.lower() for n in streamer_names]:
            streamer_names.append(name)

    moment_names = [
        safe_metadata_text(clip["moment_label"], 50)
        for clip in clips
    ]

    if len(streamer_names) == 1:
        title_subject = streamer_names[0]
    elif len(streamer_names) == 2:
        title_subject = f"{streamer_names[0]} & {streamer_names[1]}"
    else:
        title_subject = f"{streamer_names[0]} and more"

    moment = safe_metadata_text(best_clip["moment_label"], 45)
    title = safe_metadata_text(
        f"5 Best Gaming Moments: {moment} | {title_subject} #Shorts",
        100,
    )

    description_lines = [
        "Five ranked gaming moments, from #5 to #1.",
        "",
        "Clips are listed with their source and permission record. "
        "Attribution does not replace permission.",
        "",
    ]

    # Playback order is #5 down to #1.
    for index, clip in enumerate(reversed(clips), start=1):
        rank = 6 - index
        description_lines.extend([
            f"#{rank} {clip['streamer']} — {clip['moment_label']}",
            f"Source: {clip['source_url']}",
            f"Permission record: {clip['permission_evidence']}",
            "",
        ])

    description_lines.append(
        "Fan-made compilation. Original footage remains the property "
        "of its respective rights holders."
    )

    description = "\n".join(description_lines)

    tags = [
        "gaming",
        "gaming highlights",
        "gaming moments",
        "streamer clips",
        "shorts",
    ]

    for name in streamer_names:
        tags.append(name)

    for name in moment_names:
        tags.extend(
            word for word in re.findall(r"[A-Za-z0-9]+", name)
            if len(word) >= 3
        )

    # YouTube tags should be concise and unique.
    unique_tags = []
    seen = set()

    for tag in tags:
        tag = safe_metadata_text(tag, 30)
        key = tag.lower()
        if tag and key not in seen:
            unique_tags.append(tag)
            seen.add(key)

    return title, description, unique_tags[:20]


def main():
    OUT.mkdir(exist_ok=True)

    if not MANIFEST.is_file():
        raise RuntimeError("Missing assets/clips.json")

    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("clips"), list):
        raise RuntimeError("Manifest must contain a clips list")

    eligible = []
    seen = set()

    for clip in data["clips"]:
        ok, reason = validate_clip(clip)

        if ok and clip["id"] not in seen:
            eligible.append(clip)
            seen.add(clip["id"])
        else:
            clip_id = clip.get("id", "<no-id>") if isinstance(clip, dict) else "<invalid>"
            print(
                f"SKIP {clip_id}: "
                f"{reason if not ok else 'duplicate id'}"
            )

    cron = os.environ.get("SCHEDULE_CRON", "")
    slot = SLOT_BY_CRON.get(cron, 0)

    start_index = slot * CLIP_LIMIT
    clips = eligible[start_index:start_index + CLIP_LIMIT]

    if len(clips) < CLIP_LIMIT:
        raise RuntimeError(
            f"FAIL-CLOSED: slot {slot + 1} needs {CLIP_LIMIT} "
            f"approved, valid clips; found {len(clips)}. "
            "No video uploaded. Check assets/clips.json and source files."
        )

    output = OUT / "gaming_best_moments.mp4"

    duration = make_video(clips, output)
    title, description, tags = build_metadata(clips)

    yt = youtube_client()

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "20",
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
        },
    }

    try:
        request = yt.videos().insert(
            part="snippet,status",
            body=body,
            media_body=MediaFileUpload(
                str(output),
                mimetype="video/mp4",
                resumable=True,
            ),
            notifySubscribers=False,
        )

        response = request.execute()
        video_id = response.get("id")

        print(
            f"UPLOAD_OK video_id={video_id} "
            f"privacy=private duration={duration:.1f}s"
        )

        # Store only non-secret upload information in the artifact.
        result = {
            "video_id": video_id,
            "privacy": "private",
            "duration_seconds": duration,
            "title": title,
            "tags": tags,
        }

        (OUT / "upload_result.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    except HttpError as exc:
        print(
            f"UPLOAD_FAILED YouTube API error: {exc}",
            file=sys.stderr,
        )
        raise


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
