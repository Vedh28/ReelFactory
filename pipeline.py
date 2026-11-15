import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent


def load_env():
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def request(url, *, method="GET", headers=None, data=None, timeout=120):
    req = Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            return response.read(), response.headers
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code} from {url}: {detail}") from exc


def json_request(url, *, method="GET", headers=None, payload=None, timeout=120):
    headers = {"Accept": "application/json", **(headers or {})}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    body, _ = request(url, method=method, headers=headers, data=data, timeout=timeout)
    return json.loads(body)


def grok_script(genre):
    key = os.environ["XAI_API_KEY"]
    result = json_request(
        "https://api.x.ai/v1/responses",
        method="POST",
        headers={"Authorization": f"Bearer {key}"},
        payload={
            "model": os.getenv("XAI_TEXT_MODEL", "grok-4.7"),
            "store": False,
            "input": [
                {"role": "system", "content": "Create one original Instagram Reel concept. Return only valid JSON with string fields narration, video_prompt, caption. narration must be 25-32 spoken words, natural and factual; avoid unsupported claims. video_prompt must describe a visually engaging vertical 9:16 scene and contain no text or subtitles. caption should be short and include 3-5 relevant hashtags."},
                {"role": "user", "content": f"Genre: {genre}. Make today's idea fresh and specific."},
            ],
        },
    )
    text = "".join(
        block.get("text", "")
        for item in result.get("output", []) if item.get("type") == "message"
        for block in item.get("content", []) if block.get("type") == "output_text"
    )
    try:
        script = json.loads(text)
        for field in ("narration", "video_prompt", "caption"):
            if not isinstance(script.get(field), str) or not script[field].strip():
                raise ValueError(f"Grok response missing {field}")
        return script
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Could not parse Grok script JSON: {text}") from exc


def make_voice(text, destination):
    key = os.environ["ELEVENLABS_API_KEY"]
    voice = os.environ["ELEVENLABS_VOICE_ID"]
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_128"
    body, _ = request(
        url,
        method="POST",
        headers={"xi-api-key": key, "Content-Type": "application/json", "Accept": "audio/mpeg"},
        data=json.dumps({
            "text": text,
            "model_id": os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2"),
        }).encode(),
    )
    destination.write_bytes(body)


def make_video(prompt, destination):
    key = os.environ["XAI_API_KEY"]
    headers = {"Authorization": f"Bearer {key}"}
    started = json_request(
        "https://api.x.ai/v1/videos/generations",
        method="POST",
        headers=headers,
        payload={
            "model": os.getenv("XAI_VIDEO_MODEL", "grok-imagine-video-1.5-lite"),
            "prompt": prompt,
            "duration": 15,
            "aspect_ratio": "9:16",
            "resolution": "720p",
        },
    )
    request_id = started.get("request_id")
    if not request_id:
        raise RuntimeError(f"Grok video response missing request_id: {started}")
    deadline = time.time() + 900
    while time.time() < deadline:
        result = json_request(f"https://api.x.ai/v1/videos/{request_id}", headers=headers)
        status = result.get("status")
        if status == "done":
            video_url = result.get("video", {}).get("url")
            if not video_url:
                raise RuntimeError(f"Grok marked video done but returned no URL: {result}")
            video, _ = request(video_url, timeout=300)
            destination.write_bytes(video)
            return
        if status in {"failed", "error"}:
            raise RuntimeError(f"Grok video generation failed: {result}")
        time.sleep(10)
    raise TimeoutError(f"Grok video request {request_id} timed out")


def mux(video, audio, destination):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFmpeg is required. Install it and ensure ffmpeg is on PATH.")
    subprocess.run([
        ffmpeg, "-y", "-i", str(video), "-i", str(audio),
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "medium",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-shortest",
        "-movflags", "+faststart", str(destination),
    ], check=True)


def graph_json(path, token, *, method="GET", params=None, version="v25.0", timeout=120):
    url = f"https://graph.facebook.com/{version}/{path}"
    if method == "GET":
        url += "?" + urlencode({**(params or {}), "access_token": token})
        return json_request(url, timeout=timeout)
    payload = urlencode({**(params or {}), "access_token": token}).encode()
    body, _ = request(
        url, method=method,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        data=payload, timeout=timeout,
    )
    return json.loads(body)


def publish_reel(video, caption):
    ig_id, token = os.environ["IG_USER_ID"], os.environ["IG_ACCESS_TOKEN"]
    version = os.getenv("IG_GRAPH_VERSION", "v25.0")
    container = graph_json(
        f"{ig_id}/media", token, method="POST", version=version,
        params={"media_type": "REELS", "upload_type": "resumable", "caption": caption, "share_to_feed": "true"},
    )
    container_id = container.get("id")
    if not container_id:
        raise RuntimeError(f"Instagram did not return a container ID: {container}")

    data = video.read_bytes()
    request(
        f"https://rupload.facebook.com/ig-api-upload/{container_id}", method="POST",
        headers={
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(len(data)),
            "Content-Type": "application/octet-stream",
        },
        data=data, timeout=300,
    )

    for _ in range(30):
        status = graph_json(container_id, token, version=version, params={"fields": "status_code"})
        if status.get("status_code") == "FINISHED":
            break
        if status.get("status_code") == "ERROR":
            raise RuntimeError(f"Instagram rejected Reel processing: {status}")
        time.sleep(10)
    else:
        raise TimeoutError(f"Instagram Reel {container_id} did not finish processing")

    published = graph_json(
        f"{ig_id}/media_publish", token, method="POST", version=version,
        params={"creation_id": container_id},
    )
    if not published.get("id"):
        raise RuntimeError(f"Instagram did not return a published media ID: {published}")
    return published["id"]


def main():
    load_env()
    for required in ("XAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        if not os.getenv(required):
            raise RuntimeError(f"Set {required} in .env")
    if os.getenv("AUTO_PUBLISH", "true").lower() == "true":
        for required in ("IG_USER_ID", "IG_ACCESS_TOKEN"):
            if not os.getenv(required):
                raise RuntimeError(f"Set {required} in .env, or set AUTO_PUBLISH=false to generate drafts")
    output_dir = ROOT / os.getenv("OUTPUT_DIR", "output")
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    paths = {name: output_dir / f"{stamp}-{name}.{ext}" for name, ext in {
        "script": "json", "video_raw": "mp4", "voice": "mp3", "reel": "mp4",
    }.items()}

    script = grok_script(os.getenv("GENRE", "motivational facts"))
    paths["script"].write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Script: {paths['script']}")
    make_video(script["video_prompt"], paths["video_raw"])
    make_voice(script["narration"], paths["voice"])
    mux(paths["video_raw"], paths["voice"], paths["reel"])
    print(f"Reel: {paths['reel']}")

    if os.getenv("AUTO_PUBLISH", "true").lower() == "true":
        media_id = publish_reel(paths["reel"], script["caption"])
        print(f"Published Instagram media ID: {media_id}")
    else:
        print("Not published. Set AUTO_PUBLISH=true after configuring Instagram credentials.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
