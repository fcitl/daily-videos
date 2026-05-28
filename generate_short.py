"""
generate_short.py — Build a daily vehicle Short.

Pipeline:
  1. YouTube Data API   - top Autos/Vehicles video in last 24h
  2. Anthropic          - extract trend, write 6 on-screen lines
  3. Pexels             - one B-roll image per line
  4. Edge TTS           - free Microsoft Neural voiceover (en-GB)
  5. FFmpeg             - compose 9:16 MP4 with portrait PIP, drawtext lines,
                          voiceover audio

Output:
  out/today.mp4
  out/script.txt   (release notes / caption)
  out/meta.json

Required env:
  YOUTUBE_API_KEY, PEXELS_API_KEY, ANTHROPIC_API_KEY

Optional env:
  EDGE_VOICE     (default: en-GB-RyanNeural)
  PORTRAIT_PATH  (default: assets/portrait.png)
  OUT_DIR        (default: out)
  REGION_CODE    (default: GB)
"""

from __future__ import annotations

import os
import sys
import json
import asyncio
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

import requests
import anthropic
import edge_tts

# --- config ------------------------------------------------------------------

YOUTUBE_KEY   = os.environ["YOUTUBE_API_KEY"]
PEXELS_KEY    = os.environ["PEXELS_API_KEY"]
ANTHROPIC_KEY = os.environ["ANTHROPIC_API_KEY"]

EDGE_VOICE = os.environ.get("EDGE_VOICE", "en-GB-RyanNeural")
PORTRAIT   = Path(os.environ.get("PORTRAIT_PATH", "portrait.png"))
OUT_DIR    = Path(os.environ.get("OUT_DIR", "out"))
REGION     = os.environ.get("REGION_CODE", "GB")

OUT_DIR.mkdir(parents=True, exist_ok=True)

LLM = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
LLM_MODEL = "claude-sonnet-4-5"

NUM_LINES = 6


# --- 1. YouTube discovery ----------------------------------------------------

def top_vehicle_video() -> dict:
    """Return the most-viewed Autos/Vehicles video published in last 24h."""
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    r = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "key": YOUTUBE_KEY, "part": "snippet", "type": "video",
            "videoCategoryId": "2", "order": "viewCount",
            "publishedAfter": since, "regionCode": REGION, "maxResults": 10,
        },
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise RuntimeError("YouTube returned no videos for the last 24h.")
    ids = ",".join(i["id"]["videoId"] for i in items)
    r2 = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": YOUTUBE_KEY, "part": "snippet,statistics", "id": ids},
        timeout=30,
    )
    r2.raise_for_status()
    videos = r2.json()["items"]
    videos.sort(key=lambda v: int(v["statistics"].get("viewCount", 0)),
                reverse=True)
    return videos[0]


# --- 2. LLM steps ------------------------------------------------------------

def _llm(prompt: str, max_tokens: int = 400) -> str:
    msg = LLM.messages.create(
        model=LLM_MODEL, max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return msg.content[0].text.strip()


def extract_topic(video: dict) -> str:
    title = video["snippet"]["title"]
    desc  = video["snippet"]["description"][:600]
    chan  = video["snippet"]["channelTitle"]
    return _llm(
        f"""Analyse the most-watched vehicle YouTube video of the last 24 hours.

Title: {title}
Channel: {chan}
Description: {desc}

In 1-2 sentences, describe the SPECIFIC car, event, or news this is about.
Reply with just the description. No preamble.""",
        max_tokens=200,
    )


def write_lines_and_narration(topic: str) -> tuple[list[str], str]:
    """Return ({NUM_LINES} on-screen lines, narration paragraph).
    The on-screen lines are what's burned into the video. The narration is
    a slightly fuller version for the TTS voiceover - same content, written
    to be spoken aloud rather than read."""
    raw = _llm(
        f"""Topic: {topic}

Write content for a {NUM_LINES * 3}-second vertical vehicle short.

Output TWO sections separated by `---`:

SECTION A: exactly {NUM_LINES} on-screen text lines.
- Each line MAX 7 words. Punchy. Readable in 3 seconds.
- Line 1 hooks the specific car/event. Line {NUM_LINES} is the kicker.
- British English. NO AI clichés. NO questions to viewer. NO CTAs.

SECTION B: a single narration paragraph (75-90 words) that says the same
thing the on-screen lines say, but expanded for spoken voiceover.
- Direct enthusiast tone, like a knowledgeable mate at a car meet.
- No "in the world of", "buckle up", "let's dive in", "you won't believe".
- No instructions to subscribe or comment.

Format strictly:
LINES
line 1
line 2
line 3
line 4
line 5
line 6
---
NARRATION
<paragraph>""",
        max_tokens=600,
    )
    if "---" not in raw:
        raise RuntimeError(f"LLM didn't return the expected two sections:\n{raw}")
    a, b = raw.split("---", 1)
    lines = [ln.strip() for ln in a.splitlines()
             if ln.strip() and not ln.strip().upper().startswith("LINES")]
    if len(lines) < NUM_LINES:
        raise RuntimeError(f"Got {len(lines)} lines, expected {NUM_LINES}")
    lines = lines[:NUM_LINES]

    narration = "\n".join(ln for ln in b.splitlines()
                          if ln.strip() and not ln.strip().upper().startswith("NARRATION"))
    narration = narration.strip()
    if not narration:
        raise RuntimeError("Empty narration block")
    return lines, narration


def broll_keywords(topic: str) -> list[str]:
    raw = _llm(
        f"""For a vehicle short on this topic: {topic}

Give {NUM_LINES} stock-footage search queries (2-3 words each) for Pexels.
Each query should match a different visual angle on the topic.
Output one query per line. No numbering, quotes, or explanation.""",
        max_tokens=160,
    )
    qs = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    while len(qs) < NUM_LINES:
        qs.append("car")
    return qs[:NUM_LINES]


# --- 3. Pexels B-roll --------------------------------------------------------

def fetch_broll(keywords: list[str]) -> list[Path]:
    paths: list[Path] = []
    for idx, kw in enumerate(keywords):
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY},
            params={"query": kw, "orientation": "portrait", "per_page": 3},
            timeout=30,
        )
        photos = r.json().get("photos", []) if r.status_code == 200 else []
        if not photos:
            r = requests.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": PEXELS_KEY},
                params={"query": "car", "orientation": "portrait", "per_page": 3},
                timeout=30,
            )
            photos = r.json().get("photos", [])
        if not photos:
            raise RuntimeError(f"No Pexels result for '{kw}' or fallback.")
        url = photos[0]["src"]["large2x"]
        dest = OUT_DIR / f"broll_{idx:02d}.jpg"
        dest.write_bytes(requests.get(url, timeout=30).content)
        paths.append(dest)
    return paths


# --- 4. Edge TTS voiceover ---------------------------------------------------

async def _synth(text: str, out_path: Path) -> None:
    comm = edge_tts.Communicate(text, voice=EDGE_VOICE)
    await comm.save(str(out_path))


def synthesise_voiceover(text: str, out_path: Path) -> None:
    asyncio.run(_synth(text, out_path))


def audio_duration(path: Path) -> float:
    out = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ])
    return float(out.decode().strip())


# --- 5. FFmpeg composition ---------------------------------------------------

def _escape_drawtext(text: str) -> str:
    return (text.replace("\\", "\\\\")
                .replace(":", "\\:")
                .replace("'", "\u2019")
                .replace(",", "\\,"))


def compose(broll: list[Path], lines: list[str], voice: Path,
            duration: float, out_path: Path) -> None:
    per_scene = duration / len(broll)

    chains: list[str] = []
    inputs: list[str] = []
    for i, (img, line) in enumerate(zip(broll, lines)):
        inputs += ["-loop", "1", "-t", f"{per_scene:.3f}", "-i", str(img)]
        chains.append(
            f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            f"crop=1080:1920,setsar=1,fps=30,"
            f"drawbox=x=0:y=1300:w=1080:h=620:color=black@0.55:t=fill,"
            f"drawtext=fontfile=/usr/share/fonts/truetype/dejavu/DejaVu-Sans-Bold.ttf"
            f":text='{_escape_drawtext(line)}'"
            f":fontcolor=white:fontsize=58:x=(w-text_w)/2:y=1440"
            f"[s{i}]"
        )

    concat_in = "".join(f"[s{i}]" for i in range(len(broll)))
    chains.append(f"{concat_in}concat=n={len(broll)}:v=1:a=0[bg]")

    portrait_idx = len(broll)
    audio_idx    = len(broll) + 1
    inputs += ["-i", str(PORTRAIT), "-i", str(voice)]

    # Portrait top-right, clear of the bottom text bar
    chains.append(f"[{portrait_idx}:v]scale=240:240[pip]")
    chains.append("[bg][pip]overlay=W-w-50:80[final]")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "warning",
        *inputs,
        "-filter_complex", ";".join(chains),
        "-map", "[final]", "-map", f"{audio_idx}:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest", str(out_path),
    ]
    subprocess.run(cmd, check=True)


# --- main --------------------------------------------------------------------

def main() -> int:
    if not PORTRAIT.exists():
        print(f"Missing portrait PNG at {PORTRAIT}", file=sys.stderr)
        return 1

    print("[1/5] Finding top vehicle video of the last 24h…")
    video = top_vehicle_video()
    print(f"      {video['snippet']['title']}")
    print(f"      {video['statistics'].get('viewCount', '?')} views · "
          f"{video['snippet']['channelTitle']}")

    print("[2/5] Topic, on-screen lines, narration…")
    topic = extract_topic(video)
    print(f"      topic: {topic}")
    lines, narration = write_lines_and_narration(topic)
    for i, ln in enumerate(lines, 1):
        print(f"      L{i}. {ln}")

    print("[3/5] B-roll…")
    kws = broll_keywords(topic)
    print(f"      keywords: {kws}")
    broll = fetch_broll(kws)

    print("[4/5] Voiceover (Edge TTS)…")
    voice = OUT_DIR / "voice.mp3"
    synthesise_voiceover(narration, voice)
    dur = audio_duration(voice)
    print(f"      {dur:.1f} seconds  ({EDGE_VOICE})")

    print("[5/5] Composing video…")
    out_mp4 = OUT_DIR / "today.mp4"
    compose(broll, lines, voice, dur, out_mp4)

    (OUT_DIR / "script.txt").write_text("\n".join(lines) + "\n",
                                         encoding="utf-8")
    (OUT_DIR / "meta.json").write_text(json.dumps({
        "source_video_id": video["id"],
        "source_title":    video["snippet"]["title"],
        "source_channel":  video["snippet"]["channelTitle"],
        "source_views":    video["statistics"].get("viewCount"),
        "topic":           topic,
        "lines":           lines,
        "narration":       narration,
        "keywords":        kws,
        "duration_s":      dur,
        "voice":           EDGE_VOICE,
    }, indent=2), encoding="utf-8")

    print(f"\nDone → {out_mp4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
