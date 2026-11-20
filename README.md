# daily-videos

Automated daily vehicle YouTube Short — generated, queued, and cross-posted with
no manual steps.

## How it works

Two scheduled GitHub Actions:

1. **Generate** ([`.github/workflows/generate-daily.yml`](.github/workflows/generate-daily.yml), 05:00 UTC)
   runs [`generate_short.py`](generate_short.py):
   - Finds recent popular Autos/Vehicles videos and asks Gemini to reject unrelated results.
   - Gemini extracts the topic and writes 6 on-screen lines + a narration paragraph.
   - Uses six user-supplied B-roll images, or optionally obtains them from Pexels.
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
| `PEXELS_API_KEY` | generate | Only when `BROLL_SOURCE=pexels` |
| `GEMINI_API_KEY` | generate | Gemini API (topic + script) |
| `BUFFER_API_KEY` | post | Buffer publishing |

`GITHUB_TOKEN` is provided automatically.

Create a Gemini API key in [Google AI Studio](https://aistudio.google.com/apikey)
and store it as `GEMINI_API_KEY`. The YouTube discovery key is separate.
Google AI subscriptions do not automatically provide Gemini API quota; check
the project's [API pricing](https://ai.google.dev/gemini-api/docs/pricing).
The generator uses `gemini-3.1-flash-lite`.

Optional **repository variables**:

| Variable | Default | Purpose |
|---|---|---|
| `EDGE_VOICE` | `en-GB-RyanNeural` | Edge TTS voice |
| `BROLL_SOURCE` | `local` | `local` images or `pexels` |

### Local B-roll

Put at least six appropriately licensed `.jpg`, `.jpeg`, `.png` or `.webp`
images in `assets/broll/` and commit them to the branch run by GitHub Actions.
Images are selected alphabetically; the first six are used each day. The repository
is public, so only commit images you are permitted to redistribute publicly.
The included [image sources](assets/broll/SOURCES.md) are CC0. Local images may
not match the specific topic. Review the video before publishing. Without the
images, generation fails before making
API calls. If you have an active Pexels API key, set `BROLL_SOURCE=pexels` to
use the previous automatic search instead.

Gemini's topic filter and script prompts reduce irrelevant results and invented
claims but do not guarantee accuracy. Always review the source, script and
images before publishing; the workflows should stay disabled until reviewed.

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

export YOUTUBE_API_KEY=... GEMINI_API_KEY=...
python generate_short.py            # writes out/today.mp4, script.txt, meta.json

export BUFFER_API_KEY=...
python buffer_client.py channels    # list connected Buffer channels
```
