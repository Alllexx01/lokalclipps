from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from core import process_video
from downloader import download_video, search_public_videos
from postcopy import build_variants
from tiktok import TikTokClient, TikTokError


ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data" / "auto_fitnacraft"
DEFAULT_FOCUS = "lustige, überraschende oder eskalierende FitnaCraft-Momente mit einer starken Reaktion oder Pointe"
TIKTOK_PRIVACY_LEVELS = {
    "PUBLIC_TO_EVERYONE",
    "MUTUAL_FOLLOW_FRIENDS",
    "FOLLOWER_OF_CREATOR",
    "SELF_ONLY",
}


def load_state(path: Path) -> dict:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(state, dict) and isinstance(state.get("videos", {}), dict):
            return state
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    return {"version": 1, "videos": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def candidate_is_new(item: dict, state: dict, max_age_days: int) -> bool:
    record = state.get("videos", {}).get(item["id"], {})
    if record.get("status") == "done":
        return False
    age = item.get("age_days")
    return age is not None and 0 <= int(age) <= max_age_days


def matches_topic(item: dict, query: str) -> bool:
    topic = "".join(character.lower() for character in query if character.isalnum())
    title = "".join(character.lower() for character in str(item.get("title", "")) if character.isalnum())
    return bool(topic) and topic in title


def progress_logger(video_id: str):
    def report(percent: int, message: str) -> None:
        print(f"[{video_id}] {percent:3d}% {message}", flush=True)

    return report


def process_candidate(item: dict, data_dir: Path, focus: str, keep_source: bool = False) -> dict:
    video_id = item["id"]
    video_dir = data_dir / video_id
    video_dir.mkdir(parents=True, exist_ok=True)
    progress = progress_logger(video_id)
    reusable = [
        path for path in video_dir.glob("source.*")
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl", ".json"}
    ]
    source = max(reusable, key=lambda path: path.stat().st_size) if reusable else download_video(
        item["url"], video_dir, progress, max_height=720
    )
    results, analysis = process_video(
        video=source,
        output_dir=video_dir,
        count=5,
        min_seconds=20,
        max_seconds=55,
        model_name="base",
        language="de",
        focus=focus,
        caption_style="karaoke",
        caption_position="bottom",
        caption_size=68,
        caption_color="#FFFFFF",
        highlight_color="#FFD400",
        progress=progress,
        reframe="auto",
        tighten=True,
        hook=True,
        snap_scenes=True,
        zoom="punch",
    )
    manifest = {
        "video": item,
        "focus": focus,
        "clips": results,
        "analysis": analysis,
        "completed_at": int(time.time()),
    }
    (video_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if not keep_source:
        for candidate in video_dir.glob("source.*"):
            if candidate.is_file() and candidate.suffix.lower() not in {".json", ".ass"}:
                candidate.unlink(missing_ok=True)
        audio = video_dir / "audio.wav"
        audio.unlink(missing_ok=True)
    return manifest


def tiktok_caption(manifest: dict, video_dir: Path) -> str:
    clip = manifest.get("clips", [{}])[0]
    transcript = ""
    try:
        clip_data = json.loads((video_dir / "clips.json").read_text(encoding="utf-8"))
        first = next(
            (item for item in clip_data.get("clips", []) if int(item.get("index", 1)) == int(clip.get("index", 1))),
            (clip_data.get("clips") or [{}])[0],
        )
        transcript = str(first.get("transcript", ""))
    except (OSError, ValueError, TypeError, json.JSONDecodeError, StopIteration):
        pass
    variants = build_variants(
        str(clip.get("title", "FitnaCraft Highlight")),
        transcript,
        str(clip.get("hook", "")),
        "de",
    )
    caption = variants[1]["text"] if len(variants) > 1 else str(clip.get("title", "FitnaCraft Highlight"))
    channel = str(manifest.get("video", {}).get("channel", "")).strip()
    credit = f"\n\nQuelle: {channel}" if channel else ""
    tags = " #fitnacraft #minecraft"
    if "#fitnacraft" in caption.lower():
        tags = " #minecraft"
    return (caption + credit + tags)[:2200]


def refresh_tiktok_statuses(state: dict, client: TikTokClient) -> bool:
    changed = False
    for record in state.get("videos", {}).values():
        post = record.get("tiktok") or {}
        publish_id = str(post.get("publish_id", ""))
        if not publish_id or post.get("status") in {"PUBLISH_COMPLETE", "FAILED"}:
            continue
        try:
            result = client.post_status(publish_id)
        except TikTokError as exc:
            post["last_status_error"] = str(exc)[:300]
            changed = True
            continue
        status = str(result.get("status", post.get("status", "PROCESSING_UPLOAD")))
        post.update(status=status, checked_at=int(time.time()))
        post.pop("last_status_error", None)
        changed = True
    return changed


def publish_pending_tiktok(
    state: dict,
    data_dir: Path,
    *,
    privacy: str,
    max_posts: int,
    client: TikTokClient | None = None,
) -> dict:
    client = client or TikTokClient(ROOT)
    status = client.status()
    if not status.get("configured"):
        return {"posted": 0, "blocked": "TikTok-App ist noch nicht eingerichtet."}
    if not status.get("connected"):
        return {"posted": 0, "blocked": "TikTok-Konto ist noch nicht verbunden."}
    creator = client.creator_info()
    allowed = set(creator.get("privacy_level_options") or [])
    if privacy not in allowed:
        return {
            "posted": 0,
            "blocked": f"Die gewünschte TikTok-Sichtbarkeit {privacy} ist für dieses Konto nicht verfügbar.",
        }

    state_path = data_dir / "state.json"
    if refresh_tiktok_statuses(state, client):
        save_state(state_path, state)
    posted = 0
    errors: list[str] = []
    ordered = sorted(
        state.get("videos", {}).items(),
        key=lambda pair: int(pair[1].get("completed_at", 0)),
    )
    for video_id, record in ordered:
        if posted >= max_posts or record.get("status") != "done" or (record.get("tiktok") or {}).get("publish_id"):
            continue
        video_dir = data_dir / video_id
        try:
            manifest = json.loads((video_dir / "manifest.json").read_text(encoding="utf-8"))
            clip = (manifest.get("clips") or [])[0]
            video = video_dir / Path(clip["filename"]).name
            if not video.is_file() or video.suffix.lower() != ".mp4":
                raise TikTokError("Der beste Clip fehlt.")
            result = client.direct_post(
                video,
                tiktok_caption(manifest, video_dir),
                privacy,
                True,
                False,
                False,
            )
            record["tiktok"] = {
                "publish_id": str(result.get("publish_id", "")),
                "status": str(result.get("status", "PROCESSING_UPLOAD")),
                "clip": video.name,
                "privacy": privacy,
                "submitted_at": int(time.time()),
            }
            save_state(state_path, state)
            posted += 1
            print(f"[{video_id}] TikTok-Upload übermittelt: {video.name}", flush=True)
        except (OSError, KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError, TikTokError) as exc:
            record["tiktok_error"] = str(exc)[:500]
            save_state(state_path, state)
            errors.append(f"{video_id}: {exc}")
    return {"posted": posted, "errors": errors}


def run(
    *,
    data_dir: Path,
    query: str,
    focus: str,
    max_age_days: int,
    max_per_run: int,
    discover_only: bool,
    keep_source: bool,
    publish_tiktok: bool = False,
    tiktok_privacy: str = "PUBLIC_TO_EVERYONE",
    max_tiktok_posts: int = 2,
) -> int:
    data_dir.mkdir(parents=True, exist_ok=True)
    state_path = data_dir / "state.json"
    state = load_state(state_path)
    print(f"Suche neue YouTube-Videos zu: {query}", flush=True)
    results = search_public_videos(
        query, "gaming", 36, min_minutes=3, max_minutes=180, sort="newest", exact_query=True
    )
    candidates = [
        item for item in results
        if matches_topic(item, query) and candidate_is_new(item, state, max_age_days)
    ]
    print(f"{len(candidates)} neue passende Videos gefunden.", flush=True)
    if discover_only:
        for item in candidates:
            print(f"- {item['published_at']} | {item['channel']} | {item['title']} | {item['url']}")
        return 0

    completed = 0
    for item in candidates[:max_per_run]:
        video_id = item["id"]
        record = {
            "status": "running",
            "title": item["title"],
            "channel": item["channel"],
            "url": item["url"],
            "started_at": int(time.time()),
        }
        state["videos"][video_id] = record
        save_state(state_path, state)
        try:
            manifest = process_candidate(item, data_dir, focus, keep_source)
        except Exception as exc:
            record.update(status="error", error=str(exc)[:500], failed_at=int(time.time()))
            save_state(state_path, state)
            print(f"[{video_id}] FEHLER: {exc}", file=sys.stderr, flush=True)
            continue
        record.update(
            status="done",
            completed_at=manifest["completed_at"],
            output_dir=str((data_dir / video_id).resolve()),
            clips=len(manifest["clips"]),
        )
        record.pop("error", None)
        save_state(state_path, state)
        completed += 1
        print(f"[{video_id}] Fertig: {len(manifest['clips'])} Clips", flush=True)
    exit_code = 0
    if publish_tiktok:
        publish_result = publish_pending_tiktok(
            state,
            data_dir,
            privacy=tiktok_privacy,
            max_posts=max_tiktok_posts,
        )
        if publish_result.get("blocked"):
            print(f"TikTok wartet: {publish_result['blocked']}", file=sys.stderr, flush=True)
            exit_code = 2
        elif publish_result.get("errors"):
            for error in publish_result["errors"]:
                print(f"TikTok-Fehler: {error}", file=sys.stderr, flush=True)
            exit_code = 1
        else:
            print(f"TikTok: {publish_result.get('posted', 0)} neue Posts übermittelt.", flush=True)
    print(f"Lauf beendet: {completed} Videos verarbeitet.", flush=True)
    return exit_code


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Neue FitnaCraft-Videos automatisch finden und clippen.")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--query", default="FitnaCraft")
    parser.add_argument("--focus", default=DEFAULT_FOCUS)
    parser.add_argument("--max-age-days", type=int, default=2)
    parser.add_argument("--max-per-run", type=int, default=2)
    parser.add_argument("--discover-only", action="store_true")
    parser.add_argument("--keep-source", action="store_true")
    parser.add_argument("--publish-tiktok", action="store_true")
    parser.add_argument("--tiktok-privacy", default="PUBLIC_TO_EVERYONE", choices=sorted(TIKTOK_PRIVACY_LEVELS))
    parser.add_argument("--max-tiktok-posts", type=int, default=2)
    args = parser.parse_args(argv)
    args.max_age_days = max(0, min(30, args.max_age_days))
    args.max_per_run = max(1, min(10, args.max_per_run))
    args.max_tiktok_posts = max(1, min(5, args.max_tiktok_posts))
    return args


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv)
    return run(
        data_dir=args.data_dir.resolve(),
        query=args.query,
        focus=args.focus,
        max_age_days=args.max_age_days,
        max_per_run=args.max_per_run,
        discover_only=args.discover_only,
        keep_source=args.keep_source,
        publish_tiktok=args.publish_tiktok,
        tiktok_privacy=args.tiktok_privacy,
        max_tiktok_posts=args.max_tiktok_posts,
    )


if __name__ == "__main__":
    raise SystemExit(main())
