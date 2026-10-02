"""The exercise library: seed chains plus instructions and pictures distilled from NotebookLM.

`sportlock library build` fills ~/.local/share/sportlock/library/<id>/ for every seed exercise:

1. Passage search over the notebook (read-only, never touches the notebook's conversation),
   keeping the book illustrations that come back with the passages (tools/nlm_search.py).
2. A free-exercise-db photo as an extra picture candidate, when the seed names a match.
3. Headless Claude reads the passages and looks at the candidate pictures, then writes steps,
   cues and common mistakes, and picks the one picture that really shows the exercise (or none).
4. Without a usable picture, a stick-figure diagram for the movement pattern is drawn instead.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from importlib import resources
from pathlib import Path

from .store import DATA_DIR

LIBRARY_DIR = DATA_DIR / "library"
REPO_DIR = Path(__file__).resolve().parent.parent
NLM_SEARCH = REPO_DIR / "tools" / "nlm_search.py"
FEDB_JSON = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/dist/exercises.json"
FEDB_IMAGES = "https://raw.githubusercontent.com/yuhonas/free-exercise-db/main/exercises/"
MAX_PASSAGES = 10
MAX_PICTURE_CANDIDATES = 8

DETAILS_SCHEMA = {
    "type": "object",
    "properties": {
        "steps": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 8},
        "cues": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 5},
        "mistakes": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 5},
        "breathing": {"type": ["string", "null"]},
        "sources": {"type": "array", "items": {"type": "string"}},
        "grounded": {"type": "boolean"},
        "picture": {"type": ["string", "null"]},
        "picture_reason": {"type": "string"},
    },
    "required": ["steps", "cues", "mistakes", "sources", "grounded", "picture", "picture_reason"],
}

PROMPT = """You are building one entry of an exercise library for a home calisthenics app.

Exercise: {name}
Movement pattern: {pattern}. Progression chain (easiest → hardest): {chain}.
Type: {kind_text}.

Below are passages retrieved from the user's own training books. Write the entry from these
passages. Only fall back to general coaching knowledge where they say nothing, and set
"grounded" to false if most of the entry had to come from general knowledge.

- steps: how to perform one rep (or the hold), in order, short imperative sentences.
- cues: the few form cues to keep in mind while doing it.
- mistakes: common mistakes and how to fix each one.
- breathing: one short line, or null.
- sources: titles of the books the entry draws on.

Picture: candidate image files are listed below; open each with the Read tool. Choose the ONE
that clearly shows THIS exercise ({name}) being performed — not a related variation, not an
anatomy chart of a different movement, not a cover or diagram without a person. Prefer clear
illustrations of the exact movement. Put its file name in "picture" (just the name, e.g.
"a1b2.jpg"), or null if none fits. Explain the choice in one sentence in "picture_reason".

Candidate pictures: {candidates}

Passages:
{passages}
"""


def _seed_raw() -> dict:
    return json.loads(resources.files("sportlock").joinpath("data/exercises.json").read_text())


def load_seed() -> dict[str, dict]:
    """id → spec, with pattern, chain, easier and harder filled in from the chain order."""
    seed = {}
    for chain_id, chain in _seed_raw()["chains"].items():
        ids = [e["id"] for e in chain["exercises"]]
        for index, entry in enumerate(chain["exercises"]):
            seed[entry["id"]] = {
                **entry,
                "pattern": chain["pattern"],
                "chain": chain_id,
                "chain_ids": ids,
                "step": index + 1,
                "easier": ids[index - 1] if index > 0 else None,
                "harder": ids[index + 1] if index + 1 < len(ids) else None,
            }
    return seed


class Library:
    def __init__(self, root: Path = LIBRARY_DIR, notebook_id: str | None = None):
        self.root = root
        self.notebook_id = notebook_id
        self.seed = load_seed()

    # -- reading -------------------------------------------------------------------------------

    def get(self, exercise_id: str) -> dict:
        """Seed spec merged with built details; `image` is an absolute path or ""."""
        spec = dict(self.seed.get(exercise_id) or {"id": exercise_id, "name": exercise_id, "pattern": "other",
                                                    "kind": "reps", "cues": []})
        details_path = self.root / exercise_id / "exercise.json"
        details = {}
        if details_path.exists():
            try:
                details = json.loads(details_path.read_text())
            except ValueError:
                details = {}
        spec.update({k: v for k, v in details.items() if v not in (None, [], "")})
        image = details.get("image")
        spec["image"] = str(self.root / exercise_id / image) if image else ""
        spec.setdefault("steps", [])
        spec.setdefault("mistakes", [])
        spec.setdefault("sources", [])
        return spec

    def status(self) -> dict:
        built = [i for i in self.seed if (self.root / i / "exercise.json").exists()]
        pictures = {}
        for exercise_id in built:
            source = self.get(exercise_id).get("image_source", "none").split(":")[0]
            pictures[source] = pictures.get(source, 0) + 1
        return {"total": len(self.seed), "built": len(built), "pictures": pictures,
                "missing": [i for i in self.seed if i not in built]}

    # -- building ------------------------------------------------------------------------------

    def build(self, ids: list[str] | None = None, *, force: bool = False, workers: int = 4, log=print) -> dict:
        if not self.notebook_id:
            raise RuntimeError("no NotebookLM notebook configured")
        targets = [i for i in (ids or list(self.seed)) if force or not (self.root / i / "exercise.json").exists()]
        unknown = [i for i in targets if i not in self.seed]
        if unknown:
            raise ValueError(f"unknown exercise ids: {', '.join(unknown)}")

        fedb = self._fedb_index() if any(self.seed[i].get("fedb") for i in targets) else {}
        results = {"built": [], "failed": {}}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._build_one, i, fedb): i for i in targets}
            for future in as_completed(futures):
                exercise_id = futures[future]
                try:
                    entry = future.result()
                    results["built"].append(exercise_id)
                    log(f"✓ {entry['name']:<28} picture: {entry['image_source']}")
                except Exception as error:  # one bad exercise must not stop the rest
                    results["failed"][exercise_id] = str(error)
                    log(f"✗ {self.seed[exercise_id]['name']:<28} {error}")
        return results

    def _build_one(self, exercise_id: str, fedb: dict) -> dict:
        spec = self.seed[exercise_id]
        folder = self.root / exercise_id
        candidates_dir = folder / "candidates"
        shutil.rmtree(candidates_dir, ignore_errors=True)
        candidates_dir.mkdir(parents=True)

        passages = self._search(spec, candidates_dir)
        book_pictures = {}
        for passage in passages:
            for image in passage["images"]:
                book_pictures.setdefault(Path(image["file"]).name, passage["source_title"])
        fedb_pictures = self._fedb_pictures(spec, fedb, candidates_dir)
        candidates = list(book_pictures)[:MAX_PICTURE_CANDIDATES - len(fedb_pictures)] + fedb_pictures

        details = self._distill(spec, passages, candidates, candidates_dir)

        entry = {
            "id": exercise_id, "name": spec["name"],
            "steps": details["steps"], "cues": details["cues"], "mistakes": details["mistakes"],
            "breathing": details.get("breathing"), "sources": details["sources"], "grounded": details["grounded"],
            "picture_reason": details["picture_reason"], "built_at": datetime.now().isoformat(timespec="seconds"),
        }
        chosen = details.get("picture")
        if chosen and (candidates_dir / chosen).exists() and chosen in candidates:
            target = "picture" + Path(chosen).suffix
            shutil.copyfile(candidates_dir / chosen, folder / target)
            entry["image"] = target
            entry["image_source"] = (f"book: {book_pictures[chosen]}" if chosen in book_pictures
                                     else "free-exercise-db")
        else:
            (folder / "stick.svg").write_text(stick_figure(spec["pattern"]))
            entry["image"] = "stick.svg"
            entry["image_source"] = "stick figure"

        (folder / "exercise.json").write_text(json.dumps(entry, indent=2, ensure_ascii=False))
        shutil.rmtree(candidates_dir, ignore_errors=True)
        return entry

    def _search(self, spec: dict, candidates_dir: Path) -> list[dict]:
        query = f"{spec['name']} exercise: how to perform it, technique, form, common mistakes"
        result = subprocess.run(
            [str(NLM_SEARCH), self.notebook_id, query, "--limit", str(MAX_PASSAGES), "--images-dir", str(candidates_dir)],
            capture_output=True, text=True, timeout=180,
        )
        if result.returncode != 0:
            raise RuntimeError(f"NotebookLM search failed: {result.stderr.strip()[-300:]}")
        return json.loads(result.stdout)

    def _fedb_index(self) -> dict:
        cache = self.root / "free-exercise-db.json"
        if not cache.exists() or time.time() - cache.stat().st_mtime > 30 * 86400:
            self.root.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(FEDB_JSON, timeout=60) as response:
                cache.write_bytes(response.read())
        return {e["name"]: e for e in json.loads(cache.read_text())}

    def _fedb_pictures(self, spec: dict, fedb: dict, candidates_dir: Path) -> list[str]:
        entry = fedb.get(spec.get("fedb") or "")
        names = []
        for index, image in enumerate((entry or {}).get("images", [])[:2]):
            name = f"fedb-{index}{Path(image).suffix}"
            try:
                with urllib.request.urlopen(FEDB_IMAGES + image, timeout=30) as response:
                    (candidates_dir / name).write_bytes(response.read())
                names.append(name)
            except OSError:
                pass
        return names

    def _distill(self, spec: dict, passages: list[dict], candidates: list[str], candidates_dir: Path) -> dict:
        chain = " → ".join(self.seed[i]["name"] for i in spec["chain_ids"])
        kind_text = {"reps": "repetitions", "hold": "timed hold (isometric)", "timed": "timed block"}[spec["kind"]]
        text = "\n\n".join(f"[{p['source_title']}]\n{p['text'].strip()[:1500]}" for p in passages if p["text"].strip())
        prompt = PROMPT.format(name=spec["name"], pattern=spec["pattern"], chain=chain, kind_text=kind_text,
                               candidates=", ".join(candidates) or "(none)", passages=text or "(no passages found)")
        claude = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        result = subprocess.run(
            [claude, "-p", prompt, "--output-format", "json", "--no-session-persistence",
             "--json-schema", json.dumps(DETAILS_SCHEMA), "--tools", "Read", "--allowedTools", "Read"],
            capture_output=True, text=True, timeout=600, cwd=candidates_dir,
        )
        try:
            response = json.loads(result.stdout)
        except ValueError:
            raise RuntimeError(f"Claude failed: {(result.stderr or result.stdout).strip()[-300:]}") from None
        details = response.get("structured_output")
        if response.get("is_error") or not isinstance(details, dict):
            raise RuntimeError(f"Claude returned no usable entry: {str(response.get('result'))[:300]}")
        return details


# -- stick figures ---------------------------------------------------------------------------

_POSES = {
    # (head (x, y), list of line segments) on a 200 × 140 canvas
    "push": ((40, 62), [(48, 66, 150, 92), (60, 70, 60, 104), (150, 92, 175, 104)]),
    "pull": ((100, 46), [(100, 56, 100, 100), (100, 62, 72, 20), (100, 62, 128, 20), (60, 18, 140, 18),
                         (100, 100, 88, 132), (100, 100, 112, 132)]),
    "squat": ((96, 36), [(96, 46, 104, 82), (100, 56, 140, 60), (104, 82, 136, 92), (136, 92, 120, 124),
                         (120, 124, 136, 124)]),
    "hinge": ((36, 104), [(46, 104, 110, 76), (110, 76, 140, 104), (140, 104, 140, 124), (46, 104, 60, 124)]),
    "core": ((40, 70), [(48, 74, 160, 96), (56, 76, 60, 104), (60, 104, 80, 104), (160, 96, 170, 104)]),
    "mobility": ((100, 30), [(100, 40, 100, 86), (100, 52, 70, 80), (100, 52, 140, 30), (100, 86, 80, 126),
                             (100, 86, 130, 120)]),
    "warmup": ((100, 30), [(100, 40, 100, 86), (100, 52, 70, 22), (100, 52, 130, 22), (100, 86, 86, 128),
                           (100, 86, 114, 128)]),
}


def stick_figure(pattern: str) -> str:
    head, lines = _POSES.get(pattern, _POSES["warmup"])
    segments = "".join(f'<line x1="{a}" y1="{b}" x2="{c}" y2="{d}"/>' for a, b, c, d in lines)
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 140" width="400" height="280">'
        '<g stroke="#9a9a9a" stroke-width="6" stroke-linecap="round" fill="none">'
        f'{segments}<circle cx="{head[0]}" cy="{head[1]}" r="9"/></g>'
        '<line x1="10" y1="134" x2="190" y2="134" stroke="#555" stroke-width="2"/></svg>'
    )
