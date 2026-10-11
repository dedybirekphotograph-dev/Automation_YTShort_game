import base64, json, os, re, subprocess, sys
from pathlib import Path
from datetime import datetime, timezone
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

ROOT = Path(".")
MANIFEST = ROOT / "assets/clips.json"
OUT = ROOT / "output"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
MAX_TOTAL = 90
CLIP_LIMIT = 5

def run(cmd):
    print("+", " ".join(map(str, cmd)), flush=True)
    subprocess.run(list(map(str, cmd)), check=True)

def validate_clip(c):
    required = ["id","file","streamer","source_url","permission_evidence","approved","start_seconds","duration_seconds","moment_label"]
    if any(k not in c for k in required):
        return False, "missing required field"
    if c["approved"] is not True:
        return False, "approved is not true"
    if not c["permission_evidence"] or "replace with" in c["permission_evidence"].lower():
        return False, "permission evidence not configured"
    if not c["source_url"].startswith(("https://","http://")):
        return False, "source_url must be an http(s) URL"
    p = ROOT / c["file"]
    if not p.is_file():
        return False, f"missing local video file: {p}"
    try:
        start, dur = float(c["start_seconds"]), float(c["duration_seconds"])
        if start < 0 or dur < 3 or dur > 15:
            return False, "start must be >=0 and duration must be 3-15 seconds"
    except (ValueError, TypeError):
        return False, "invalid clip time"
    return True, ""

def ffprobe_duration(path):
    r = subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",str(path)], check=True, capture_output=True, text=True)
    return float(r.stdout.strip())

def make_video(clips, output_path):
    OUT.mkdir(exist_ok=True)
    work = OUT / "parts"
    work.mkdir(exist_ok=True)
    parts = []
    # Put the highest-ranked moment first (#1), while sourcing five distinct approved clips.
    ordered = list(reversed(clips[:CLIP_LIMIT]))
    for i, c in enumerate(ordered, start=1):
        rank = 6 - i  # first clip is #5; final clip is #1
        src = ROOT / c["file"]
        part = work / f"part_{i}.mp4"
        duration = float(c["duration_seconds"])
        start = float(c["start_seconds"])
        label = f'RANK #{rank}  |  {c["streamer"]}'
        source = f'SOURCE: {c["source_url"]}'
        # Escape text for FFmpeg drawtext filter.
        def esc(s):
            return s.replace("\\", r"\\").replace(":", r"\:").replace("'", r"\'").replace("%", r"\%").replace(",", r"\,").replace("[", r"\[").replace("]", r"\]")
        vf = (
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:black,"
            "setsar=1,fps=30,"
            f"drawtext=text='{esc(label)}':fontcolor=white:fontsize=54:borderw=4:bordercolor=black:x=(w-text_w)/2:y=90,"
            f"drawtext=text='{esc(source)}':fontcolor=white:fontsize=25:borderw=3:bordercolor=black:x=35:y=h-120:box=1:boxcolor=black@0.45"
        )
        run(["ffmpeg","-y","-ss",start,"-i",src,"-t",duration,"-vf",vf,"-an","-c:v","libx264","-preset","veryfast","-crf","25","-pix_fmt","yuv420p","-movflags","+faststart",part])
        parts.append(part)
    # concat with re-encoding to keep format stable
    concat_file = work / "concat.txt"
    concat_file.write_text("".join(f"file '{p.resolve()}'\n" for p in parts), encoding="utf-8")
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",concat_file,"-t",MAX_TOTAL,"-c:v","libx264","-preset","veryfast","-crf","25","-pix_fmt","yuv420p","-movflags","+faststart",output_path])
    dur = ffprobe_duration(output_path)
    if dur > MAX_TOTAL:
        raise RuntimeError(f"Output is {dur:.1f}s, above {MAX_TOTAL}s cap")
    return dur

def youtube_client():
    raw = os.environ.get("YOUTUBE_TOKEN_JSON_B64")
    if not raw:
        raise RuntimeError("Missing GitHub Secret YOUTUBE_TOKEN_JSON_B64")
    token_json = base64.b64decode(raw).decode("utf-8")
    token_path = OUT / "token.json"
    token_path.write_text(token_json, encoding="utf-8")
    creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube","v3",credentials=creds)

def main():
    OUT.mkdir(exist_ok=True)
    if not MANIFEST.exists():
        raise RuntimeError("Missing assets/clips.json")
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    eligible = []
    seen = set()
    for c in data.get("clips", []):
        ok, reason = validate_clip(c)
        if ok and c["id"] not in seen:
            eligible.append(c); seen.add(c["id"])
        else:
            print(f"SKIP {c.get('id','<no-id>')}: {reason if not ok else 'duplicate id'}")
    cron = os.environ.get("SCHEDULE_CRON", "")
    slot_by_cron = {"0 1 * * *": 0, "0 5 * * *": 1, "0 9 * * *": 2, "0 13 * * *": 3}
    slot = slot_by_cron.get(cron, 0)  # manual workflow_dispatch defaults to slot 1
    start_index = slot * CLIP_LIMIT
    clips = eligible[start_index:start_index + CLIP_LIMIT]
    if len(clips) < CLIP_LIMIT:
        raise RuntimeError(
            f"FAIL-CLOSED: slot {slot+1} needs {CLIP_LIMIT} approved, valid clips "
            f"(manifest entries {start_index+1}-{start_index+CLIP_LIMIT}); "
            f"found {len(clips)} for this slot. No video uploaded. Add up to 20 approved clips/day."
        )
    title = "5 Best Gaming Streamer Moments | Ranked #1–#5 #Shorts"
    output = OUT / "gaming_best_moments.mp4"
    duration = make_video(clips, output)
    description = "Five ranked gaming moments from the sources listed below. Clips are used under the permissions/licences recorded by the uploader. Attribution does not replace permission.\n\n"
    for idx, c in enumerate(reversed(clips), start=1):
        rank = 6 - idx
        description += f"#{rank} {c['streamer']} — {c['moment_label']}\nSource: {c['source_url']}\nPermission record: {c['permission_evidence']}\n\n"
    description += "Fan-made compilation. Rights holders retain ownership of their original footage."
    yt = youtube_client()
    body = {
        "snippet": {"title": title, "description": description, "tags": ["gaming","streamer","gaming highlights","shorts"], "categoryId": "20", "defaultLanguage":"en"},
        "status": {"privacyStatus":"private", "selfDeclaredMadeForKids": False}
    }
    try:
        request = yt.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(str(output), mimetype="video/mp4", resumable=True), notifySubscribers=False)
        response = request.execute()
        print(f"UPLOAD_OK video_id={response.get('id')} privacy=private duration={duration:.1f}s")
        (OUT / "upload_result.json").write_text(json.dumps({"video_id":response.get("id"),"privacy":"private","duration_seconds":duration,"title":title},indent=2),encoding="utf-8")
    except HttpError as e:
        print(f"UPLOAD_FAILED YouTube API error: {e}", file=sys.stderr)
        raise

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
