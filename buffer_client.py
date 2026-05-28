"""
Buffer GraphQL API — post short videos to YouTube, Instagram, TikTok.

Setup:
    export BUFFER_API_KEY="your_personal_api_key"
    pip install requests

Docs: https://developers.buffer.com/reference.html
Endpoint: https://api.buffer.com

Constraints worth knowing:
- API is in beta. Asset input format changed on 25 May 2026; this
  file uses the post-change shape (assets: list of AssetInput).
- Media must be at a publicly accessible URL. No direct upload.
  Google Drive links don't work.
- Buffer pulls the file at publish time, not when you call createPost.
  Keep the URL alive until the post is sent — especially for TikTok.
- Rate limit: 100 req / 15 min per client, rolling window.
- Instagram no longer supports standard video posts; videos publish
  as Reels. YouTube videos under 60s with 9:16 aspect publish as Shorts.

Spec target for a single 9:16 short that works across all three:
- MP4, H.264, AAC audio
- 1080x1920, <= 60s, <= 100MB to be safe
"""

from __future__ import annotations

import os
import time
import json
from datetime import datetime, timezone
from typing import Any, Iterable

import requests

BUFFER_ENDPOINT = "https://api.buffer.com"
API_KEY = os.environ["BUFFER_API_KEY"]

SESSION = requests.Session()
SESSION.headers.update({
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
})


# --- transport ---------------------------------------------------------------

def gql(query: str, variables: dict[str, Any] | None = None,
        max_retries: int = 3) -> dict[str, Any]:
    payload = {"query": query, "variables": variables or {}}
    for attempt in range(max_retries):
        r = SESSION.post(BUFFER_ENDPOINT, json=payload, timeout=30)
        if r.status_code == 429:
            wait = 2 ** attempt * 5
            print(f"[rate-limited] sleeping {wait}s")
            time.sleep(wait)
            continue
        r.raise_for_status()
        data = r.json()
        if data.get("errors"):
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data["data"]
    raise RuntimeError("Exhausted retries on Buffer API")


# --- discovery ---------------------------------------------------------------

def list_channels() -> list[dict]:
    query = """
    query {
      account {
        organizations {
          id
          name
          channels {
            id
            service
            name
            timezone
          }
        }
      }
    }
    """
    data = gql(query)
    out = []
    for org in data["account"]["organizations"]:
        for ch in org.get("channels", []):
            out.append({**ch, "orgId": org["id"], "orgName": org["name"]})
    return out


def channels_by_service(services: Iterable[str]) -> dict[str, str]:
    """Return {service: channel_id} for the requested services.
    Picks the first match per service — fine if you only have one of each."""
    wanted = {s.lower() for s in services}
    found: dict[str, str] = {}
    for ch in list_channels():
        svc = ch["service"].lower()
        if svc in wanted and svc not in found:
            found[svc] = ch["id"]
    missing = wanted - found.keys()
    if missing:
        raise RuntimeError(f"No connected channel for: {sorted(missing)}")
    return found


def describe_input(type_name: str) -> None:
    """Introspect any input type so you can confirm fields against the
    live schema (the API is still in beta and shapes drift)."""
    query = """
    query($name: String!) {
      __type(name: $name) {
        name
        inputFields {
          name
          type { name kind ofType { name kind ofType { name kind } } }
        }
      }
    }
    """
    print(json.dumps(gql(query, {"name": type_name}), indent=2))


# --- posting -----------------------------------------------------------------

CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess {
      post { id text status dueAt assets { id mimeType } }
    }
    ... on MutationError {
      message
    }
  }
}
"""


def _video_asset(url: str) -> dict:
    """Single video asset in the post-25-May-2026 format.
    AssetInput is @oneOf — exactly one of video/image/document/link."""
    return {"video": {"url": url}}


def _metadata_for(service: str) -> dict:
    """Per-platform metadata that makes sense for short-form vertical video.
    Confirm exact field names against `describe_input('PostInputMetaData')`
    if a post comes back as the wrong type."""
    if service == "instagram":
        # Instagram standard video posts are deprecated; this publishes as a Reel.
        return {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    if service == "youtube":
        # Vertical <= 60s publishes as a Short automatically.
        return {"youtube": {"type": "short"}}
    if service == "tiktok":
        return {"tiktok": {}}  # no required metadata for basic posts
    return {}


def post_video(channel_id: str, service: str, video_url: str,
               caption: str, due_at: datetime | None = None) -> dict:
    """Post one video to one channel. If due_at is None, drops into queue."""
    input_obj: dict[str, Any] = {
        "text": caption,
        "channelId": channel_id,
        "schedulingType": "automatic",
        "assets": [_video_asset(video_url)],
        "metadata": _metadata_for(service),
    }
    if due_at is None:
        input_obj["mode"] = "addToQueue"
    else:
        if due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware (use UTC)")
        input_obj["mode"] = "customScheduled"
        input_obj["dueAt"] = (due_at.astimezone(timezone.utc)
                                   .strftime("%Y-%m-%dT%H:%M:%S.000Z"))
    return gql(CREATE_POST, {"input": input_obj})["createPost"]


def post_to_all(video_url: str, caption: str,
                services: tuple[str, ...] = ("youtube", "instagram", "tiktok"),
                due_at: datetime | None = None) -> dict[str, dict]:
    """Fan out the same video to every requested service.
    Returns {service: result}. Each result is either {'post': {...}} on
    success or {'message': '...'} on failure — one bad platform doesn't
    abort the others."""
    ids = channels_by_service(services)
    results: dict[str, dict] = {}
    for service in services:
        try:
            results[service] = post_video(
                ids[service], service, video_url, caption, due_at
            )
        except Exception as e:
            results[service] = {"message": f"exception: {e}"}
        time.sleep(1)  # gentle pacing under the 100/15min cap
    return results


def print_results(results: dict[str, dict]) -> None:
    for service, r in results.items():
        if "post" in r:
            p = r["post"]
            print(f"{service:10} OK   id={p['id']}  status={p['status']}  "
                  f"dueAt={p.get('dueAt')}")
        else:
            print(f"{service:10} FAIL {r.get('message')}")


# --- demo --------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "channels":
        for ch in list_channels():
            print(f"{ch['service']:12} {ch['name']:30} id={ch['id']}")
        sys.exit(0)

    # Daily run: env vars set by the scheduler
    VIDEO_URL = os.environ["VIDEO_URL"]           # public https URL
    CAPTION   = os.environ.get("CAPTION", "")
    results = post_to_all(VIDEO_URL, CAPTION)
    print_results(results)
    # exit non-zero if anything failed, so the scheduler notices
    if any("post" not in r for r in results.values()):
        sys.exit(1)
