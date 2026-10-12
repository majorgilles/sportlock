"""Demonstration videos: choosing the YouTube video that shows an exercise.

Search results are judged by their title (it must name the exercise), their length (a short
demonstration, not a full workout) and their rank. No video is better than a wrong one.
"""

from __future__ import annotations

import re

from sportlock.exercises.domain.catalogue import Exercise
from sportlock.shared_kernel.base import ValueObject

MIN_SECONDS = 10
MAX_SECONDS = 6 * 60
_STOPWORDS = {"a", "an", "the", "on", "with", "to", "of", "and", "or", "in", "for"}
_TEACHING = ("how to", "tutorial", "form", "technique", "proper", "demo", "guide")


class VideoCandidate(ValueObject):
    """One search result."""

    video_id: str
    title: str
    channel: str = ""
    seconds: int | None = None
    url: str


def _words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower().replace("-", " "))
    return {w.rstrip("s") for w in words if w not in _STOPWORDS}


def search_query(exercise: Exercise) -> str:
    """What to type into YouTube."""
    return f"{exercise.name} exercise how to"


def title_match(names: list[str], title: str) -> float:
    """The best share of a name's words found in the title (0 to 1)."""
    found = _words(title)
    return max((len(_words(n) & found) / len(_words(n)) for n in names if _words(n)), default=0.0)


def choose_video(exercise: Exercise, candidates: list[VideoCandidate]) -> VideoCandidate | None:
    """The best demonstration among the search results, or None when none names the exercise."""
    names = [exercise.name, *exercise.aliases]
    best, best_score = None, 0.0
    for rank, candidate in enumerate(candidates):
        if candidate.seconds is None or not MIN_SECONDS <= candidate.seconds <= MAX_SECONDS:
            continue
        match = title_match(names, candidate.title)
        if match < 0.75:
            continue
        score = 10 * match + (2 if any(t in candidate.title.lower() for t in _TEACHING) else 0) - 0.5 * rank
        if 20 <= candidate.seconds <= 180:
            score += 1
        if score > best_score:
            best, best_score = candidate, score
    return best
