import logging
from pathlib import Path
from typing import Any

import yt_dlp

log = logging.getLogger("wellidkmusic.youtube")

_SEARCH_OPTIONS = {
    "quiet": True,
    "no_warnings": True,
    "skip_download": True,
    "extract_flat": True,
    "noplaylist": True,
    "socket_timeout": 15,
    "retries": 2,
}


def _search(query: str) -> list[dict[str, Any]]:
    options = {**_SEARCH_OPTIONS, "extract_flat": "in_playlist"}
    with yt_dlp.YoutubeDL(options) as ydl:
        result = ydl.extract_info(f"ytsearch8:{query}", download=False) or {}
    tracks = []
    for item in result.get("entries") or []:
        if not item:
            continue
        video_id = item.get("id")
        webpage_url = item.get("webpage_url")
        if not webpage_url and video_id:
            webpage_url = f"https://www.youtube.com/watch?v={video_id}"
        if not video_id or not webpage_url:
            continue
        tracks.append({
            "_source": "youtube",
            "id": f"yt_{video_id}",
            "video_id": video_id,
            "name": item.get("title") or "Unknown title",
            "artist_name": item.get("uploader") or item.get("channel") or "YouTube",
            "duration": int(item.get("duration") or 0),
            "webpage_url": webpage_url,
            "license_url": item.get("license_url"),
        })
    return tracks


async def search_youtube_tracks(query: str) -> list[dict[str, Any]]:
    """Search YouTube metadata only; resolve the media URL only when a track starts."""
    import asyncio

    try:
        return await asyncio.to_thread(_search, query)
    except Exception:
        log.exception("YouTube search failed for query %r", query)
        return []


def _download(webpage_url: str, video_id: str, media_dir: str) -> str:
    folder = Path(media_dir)
    folder.mkdir(parents=True, exist_ok=True)
    cached = next(
        (path for path in folder.glob(f"{video_id}.*") if not path.name.endswith(".part")),
        None,
    )
    if cached and cached.is_file() and cached.stat().st_size > 20_000:
        return str(cached)

    options = {
        "quiet": True,
        "no_warnings": True,
        "format": "bestaudio/best",
        "outtmpl": str(folder / f"{video_id}.%(ext)s"),
        "noplaylist": True,
        "socket_timeout": 25,
        "retries": 3,
        "fragment_retries": 3,
        "continuedl": True,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(webpage_url, download=True)
        candidates = []
        for item in info.get("requested_downloads") or []:
            if item.get("filepath"):
                candidates.append(Path(item["filepath"]))
        if info.get("_filename"):
            candidates.append(Path(info["_filename"]))
        candidates.append(Path(ydl.prepare_filename(info)))
    for path in candidates:
        if path.is_file() and path.stat().st_size > 20_000:
            return str(path)
    raise RuntimeError("YouTube audio download finished without a usable media file.")


async def download_youtube_track(webpage_url: str, video_id: str, media_dir: str) -> str:
    """Download the selected audio to a local file for reliable FFmpeg playback."""
    import asyncio

    return await asyncio.to_thread(_download, webpage_url, video_id, media_dir)


async def resolve_youtube_stream(webpage_url: str) -> str:
    """Resolve a fresh, short-lived audio URL immediately before playback."""
    import asyncio

    return await asyncio.to_thread(_resolve, webpage_url)
