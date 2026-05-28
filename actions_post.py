"""
GitHub Actions entry point for Buffer scheduled posting.

Reads today's queue release from the repo (tag: queue-YYYY-MM-DD),
hands its video asset URL to Buffer for fan-out to YT/IG/TikTok.

Env vars (all injected by the workflow):
    BUFFER_API_KEY   - your repo secret
    GITHUB_TOKEN     - automatic, scoped to this repo
    GITHUB_REPO      - owner/name, from github.repository
    OVERRIDE_TAG     - optional, from workflow_dispatch input
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import requests

from buffer_client import post_to_all, print_results

GITHUB_API = "https://api.github.com"
TOKEN = os.environ["GITHUB_TOKEN"]
REPO = os.environ["GITHUB_REPO"]

GH = requests.Session()
GH.headers.update({
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
})

POSTED_MARKER = "[POSTED"  # prefix added to release body after success
VIDEO_EXTS = (".mp4", ".mov", ".m4v")


def find_release(tag: str) -> dict | None:
    r = GH.get(f"{GITHUB_API}/repos/{REPO}/releases/tags/{tag}", timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def mark_posted(release_id: int, body: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    GH.patch(
        f"{GITHUB_API}/repos/{REPO}/releases/{release_id}",
        json={"body": f"{POSTED_MARKER} {stamp}]\n\n{body}"},
        timeout=30,
    ).raise_for_status()


def main() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    tag = os.environ.get("OVERRIDE_TAG") or f"queue-{today}"
    print(f"Looking for release: {tag}")

    release = find_release(tag)
    if release is None:
        # Empty queue is not a failure — just a quiet day.
        print(f"No release tagged '{tag}'. Nothing to post.")
        return 0

    body = (release.get("body") or "").strip()
    if body.startswith(POSTED_MARKER):
        print(f"Release {tag} already marked posted — skipping.")
        return 0

    videos = [a for a in (release.get("assets") or [])
              if a["name"].lower().endswith(VIDEO_EXTS)]
    if not videos:
        print(f"Release {tag} has no video asset (.mp4/.mov/.m4v).",
              file=sys.stderr)
        return 1

    asset = videos[0]
    video_url = asset["browser_download_url"]
    caption = body

    print(f"Asset  : {asset['name']}  ({asset['size'] / 1_000_000:.1f} MB)")
    print(f"URL    : {video_url}")
    print(f"Caption: {caption[:80]}{'…' if len(caption) > 80 else ''}")
    print()

    results = post_to_all(video_url, caption)
    print_results(results)

    failed = [s for s, r in results.items() if "post" not in r]
    if failed:
        print(f"\nPartial failure on: {failed}", file=sys.stderr)
        # Don't mark posted — leaves the release in the queue for retry.
        return 1

    mark_posted(release["id"], body)
    print(f"\nMarked '{tag}' as posted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
