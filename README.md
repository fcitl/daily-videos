# daily-videos

Automated daily vehicle YouTube Short — generated, queued, and cross-posted with
no manual steps.

## How it works

Two scheduled GitHub Actions:

1. **Generate** ([`.github/workflows/generate-daily.yml`](.github/workflows/generate-daily.yml), 05:00 UTC)
   runs [`generate_short.py`](generate_short.py):
   - Finds the most-viewed Autos/Vehicles video of the last 24h (YouTube Data API).
   - Claude extracts the topic and writes 6 on-screen lines + a narration paragraph.
   - Pulls one portrait B-roll image per line from Pexels.
   - Synthesises a voiceover with Edge TTS (free Microsoft Neural voice).
   - Composes a 9:16 MP4 with FFmpeg (B-roll + caption bar + optional portrait PIP).
   - Publishes the result as a GitHub release tagged `queue-<tomorrow>`.

2. **Post** ([`.github/workflows/post-daily.yml`](.github/workflows/post-daily.yml), 12:00 UTC)
   runs [`actions_post.py`](actions_post.py):
   - Reads today's `queue-<date>` release.
   - Hands the video's public download URL to Buffer ([`buffer_client.py`](buffer_client.py)),
     which fans it out to YouTube / Instagram / TikTok.
   - Marks the release `[POSTED ...]` on success so it isn't re-posted.

Both jobs can also be run manually from the Actions tab (`workflow_dispatch`).
The generate job takes a `dry_run` toggle (build artefacts but don't queue a release);
the post job takes an optional `tag` to post a specific queue out of band.

## Configuration

Set these as **repository secrets** (Settings → Secrets and variables → Actions):

| Secret | Used by | Purpose |
|---|---|---|
| `YOUTUBE_API_KEY` | generate | YouTube Data API v3 |
| `PEXELS_API_KEY` | generate | Pexels stock images |
| `ANTHROPIC_API_KEY` | generate | Claude (topic + script) |
| `BUFFER_API_KEY` | post | Buffer publishing |

`GITHUB_TOKEN` is provided automatically.

Optional **repository variables**:

| Variable | Default | Purpose |
|---|---|---|
| `EDGE_VOICE` | `en-GB-RyanNeural` | Edge TTS voice |

Other knobs read from the environment by [`generate_short.py`](generate_short.py):
`PORTRAIT_PATH` (default `portrait.png`), `FONT_PATH`, `OUT_DIR`, `REGION_CODE`
(default `GB`).

### Portrait overlay (optional)

If a `portrait.png` exists at the repo root (or wherever `PORTRAIT_PATH` points),
it's overlaid as a small picture-in-picture in the top-right corner. **It's
optional** — when absent, the video renders without the PIP and everything else
works as normal. Drop one in and commit it to enable the overlay.

## Running locally

```bash
pip install -r requirements.txt
# ffmpeg + a bold TTF must be installed (e.g. `brew install ffmpeg`)

export YOUTUBE_API_KEY=... PEXELS_API_KEY=... ANTHROPIC_API_KEY=...
python generate_short.py            # writes out/today.mp4, script.txt, meta.json

export BUFFER_API_KEY=...
python buffer_client.py channels    # list connected Buffer channels
```
