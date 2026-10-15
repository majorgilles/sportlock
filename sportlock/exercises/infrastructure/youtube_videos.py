"""Demonstration videos from YouTube, found and downloaded with yt-dlp (`sportlock library videos`).

Each video is kept next to the exercise's other files (<library>/<id>/video.mp4 and video.json),
at 480p at most, so it plays on the lock screen without a network.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from sportlock.exercises.domain.catalogue import Catalogue
from sportlock.exercises.domain.videos import VideoCandidate, choose_video, search_query

RESULTS = 8
FORMAT = "b[height<=480][ext=mp4]/bv*[height<=480][ext=mp4]+ba[ext=m4a]/b[height<=480]"


def _yt_dlp(*args: str, timeout: float) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["yt-dlp", *args], capture_output=True, text=True, timeout=timeout, check=False)
    except OSError, subprocess.TimeoutExpired:
        return None


def parse_search(output: str) -> list[VideoCandidate]:
    """`yt-dlp --flat-playlist -j ytsearchN:…` output, one JSON object per line."""
    candidates = []
    for line in output.splitlines():
        try:
            item = json.loads(line)
            duration = item.get("duration")
            candidates.append(
                VideoCandidate(
                    video_id=item["id"],
                    title=item.get("title") or "",
                    channel=item.get("channel") or item.get("uploader") or "",
                    seconds=int(duration) if duration else None,
                    url=item.get("url") or f"https://www.youtube.com/watch?v={item['id']}",
                )
            )
        except ValueError, KeyError, TypeError:
            continue
    return candidates


class YouTubeVideoLibrary:
    """Finds and downloads a demonstration video for each exercise."""

    def __init__(self, catalogue: Catalogue, root: Path) -> None:
        self.catalogue = catalogue
        self.root = root

    def has_video(self, exercise_id: str) -> bool:
        """Whether a video was downloaded."""
        folder = self.root / exercise_id
        try:
            data = json.loads((folder / "video.json").read_text())
        except OSError, ValueError:
            return False
        return bool(data.get("file")) and (folder / data["file"]).exists()

    def build(
        self, ids: list[str] | None = None, *, force: bool = False, progress: Callable[[str], None] = print
    ) -> dict[str, list[str]]:
        """Download missing (or the given) exercises' videos; returns {"found", "none", "failed"}."""
        results: dict[str, list[str]] = {"found": [], "none": [], "failed": []}
        targets = [e.id for e in self.catalogue.all() if e.id in ids] if ids else [e.id for e in self.catalogue.all()]
        for exercise_id in targets:
            if not force and self.has_video(exercise_id):
                continue
            exercise = self.catalogue.get(exercise_id)
            search = _yt_dlp("--flat-playlist", "-j", f"ytsearch{RESULTS}:{search_query(exercise)}", timeout=60)
            if not search or search.returncode != 0:
                results["failed"].append(exercise_id)
                progress(f"✗ {exercise.name}: search failed")
                continue
            chosen = choose_video(exercise, parse_search(search.stdout))
            if chosen is None:
                results["none"].append(exercise_id)
                progress(f"– {exercise.name}: no video names this exercise")
                continue
            folder = self.root / exercise_id
            folder.mkdir(parents=True, exist_ok=True)
            download = _yt_dlp(
                "-q", "--no-playlist", "-f", FORMAT, "--merge-output-format", "mp4", "--force-overwrites",
                "-o", str(folder / "video.mp4"), chosen.url,
                timeout=300,
            )  # fmt: skip
            if not download or download.returncode != 0 or not (folder / "video.mp4").exists():
                results["failed"].append(exercise_id)
                progress(f"✗ {exercise.name}: download failed")
                continue
            entry = {
                "file": "video.mp4",
                "title": chosen.title,
                "channel": chosen.channel,
                "url": chosen.url,
                "seconds": chosen.seconds,
                "downloaded_at": datetime.now().isoformat(timespec="seconds"),
            }
            (folder / "video.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False))
            results["found"].append(exercise_id)
            progress(f"✓ {exercise.name}: {chosen.title} ({chosen.channel})")
        return results
