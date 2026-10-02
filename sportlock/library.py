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
INFOGRAPHICS_PER_DAY = 10
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

PICTURE_SCHEMA = {
    "type": "object",
    "properties": {"picture": {"type": ["string", "null"]}, "picture_reason": {"type": "string"}},
    "required": ["picture", "picture_reason"],
}

PICTURE_PROMPT = """Pick a picture for the exercise "{name}" ({kind_text}; also known as: {aliases}).
Movement pattern: {pattern}. Progression chain (easiest → hardest): {chain}.

Candidate image files from the user's training books: {candidates}
Open each with the Read tool. Choose the ONE that clearly shows THIS exercise being performed —
not an easier or harder variation from the chain, not an anatomy chart of a different movement,
not a page without a person doing it. Put its file name in "picture" (e.g. "a1b2.jpg"), or null
if none fits, and explain in one sentence in "picture_reason".
"""

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

    def retry_pictures(self, ids: list[str] | None = None, *, workers: int = 4, log=print) -> dict:
        """Second, picture-only pass for exercises that ended up with a stick figure: search again
        under the exercise's other names and let Claude pick from the new candidates."""
        targets = ids or [i for i in self.seed if self.get(i).get("image_source") == "stick figure"]
        results = {"found": [], "none": [], "failed": {}}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._retry_picture, i): i for i in targets}
            for future in as_completed(futures):
                exercise_id = futures[future]
                name = self.seed[exercise_id]["name"]
                try:
                    source = future.result()
                    (results["found"] if source else results["none"]).append(exercise_id)
                    log(f"{'✓' if source else '·'} {name:<28} {source or 'still no matching picture'}")
                except Exception as error:
                    results["failed"][exercise_id] = str(error)
                    log(f"✗ {name:<28} {error}")
        return results

    def _retry_picture(self, exercise_id: str) -> str | None:
        spec = self.seed[exercise_id]
        folder = self.root / exercise_id
        entry_path = folder / "exercise.json"
        entry = json.loads(entry_path.read_text())
        candidates_dir = folder / "candidates"
        shutil.rmtree(candidates_dir, ignore_errors=True)
        candidates_dir.mkdir(parents=True)

        names = [spec["name"], *spec.get("aliases", [])]
        book_pictures: dict[str, str] = {}
        for query in [f"{n} exercise, starting position and movement" for n in names]:
            for passage in self._search(spec, candidates_dir, query=query, limit=6):
                for image in passage["images"]:
                    book_pictures.setdefault(Path(image["file"]).name, passage["source_title"])
        candidates = list(book_pictures)[:12]
        if not candidates:
            shutil.rmtree(candidates_dir, ignore_errors=True)
            return None

        chain = " → ".join(self.seed[i]["name"] for i in spec["chain_ids"])
        kind_text = {"reps": "repetitions", "hold": "timed hold", "timed": "timed block"}[spec["kind"]]
        prompt = PICTURE_PROMPT.format(name=spec["name"], kind_text=kind_text, pattern=spec["pattern"], chain=chain,
                                       aliases=", ".join(spec.get("aliases", [])) or "—",
                                       candidates=", ".join(candidates))
        choice = self._claude(prompt, PICTURE_SCHEMA, candidates_dir)
        chosen = choice.get("picture")
        source = None
        if chosen in candidates and (candidates_dir / chosen).exists():
            target = "picture" + Path(chosen).suffix
            shutil.copyfile(candidates_dir / chosen, folder / target)
            (folder / "stick.svg").unlink(missing_ok=True)
            source = f"book: {book_pictures[chosen]}"
            entry.update(image=target, image_source=source, picture_reason=choice["picture_reason"])
            entry_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False))
        shutil.rmtree(candidates_dir, ignore_errors=True)
        return source

    # -- infographics (last resort before a stick figure) ----------------------------------------

    def infographics(self, ids: list[str] | None = None, *, per_day: int = INFOGRAPHICS_PER_DAY, log=print) -> dict:
        """Generate a NotebookLM infographic for exercises still drawn as stick figures.

        At most `per_day` a day. Every artifact this creates is recorded in infographics.json and
        deleted from the notebook's Studio panel once downloaded; nothing else is ever deleted."""
        ledger_path = self.root / "infographics.json"
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {"days": {}, "artifacts": {}}
        today = datetime.now().date().isoformat()

        def save():
            ledger_path.write_text(json.dumps(ledger, indent=2))

        # Finish anything a previous run left behind (generated but not yet downloaded/deleted).
        pending = {a: info for a, info in ledger["artifacts"].items() if not info.get("deleted")}
        targets = [i for i in (ids or self.seed) if self.get(i).get("image_source") == "stick figure"
                   and i not in {info["exercise"] for info in pending.values()}]
        room = max(0, per_day - ledger["days"].get(today, 0))
        if len(targets) > room:
            log(f"daily limit: generating {room} of {len(targets)} today")
        for exercise_id in targets[:room]:
            task = self._nlm_json("generate", "infographic", self._infographic_prompt(exercise_id),
                                  "--orientation", "landscape", "--detail", "concise", "--style", "instructional")
            artifact = task["task_id"]
            ledger["artifacts"][artifact] = {"exercise": exercise_id, "created": today}
            ledger["days"][today] = ledger["days"].get(today, 0) + 1
            save()
            pending[artifact] = ledger["artifacts"][artifact]
            log(f"… {self.seed[exercise_id]['name']:<28} generating")

        results = {"installed": [], "failed": {}}
        for artifact, info in pending.items():
            exercise_id = info["exercise"]
            name = self.seed[exercise_id]["name"]
            try:
                if not info.get("downloaded"):
                    waited = self._nlm_json("artifact", "wait", artifact, "--timeout", "900")
                    if waited.get("status") != "completed":
                        raise RuntimeError(f"generation {waited.get('status')}: {waited.get('error')}")
                    folder = self.root / exercise_id
                    self._nlm("download", "infographic", str(folder / "picture.png"), "-a", artifact)
                    entry_path = folder / "exercise.json"
                    entry = json.loads(entry_path.read_text())
                    entry.update(image="picture.png", image_source="NotebookLM infographic",
                                 picture_reason="Generated from your notebook: no book illustration shows this exercise.")
                    entry_path.write_text(json.dumps(entry, indent=2, ensure_ascii=False))
                    (folder / "stick.svg").unlink(missing_ok=True)
                    info["downloaded"] = True
                    save()
                self._nlm("artifact", "delete", artifact, "-y")
                info["deleted"] = True
                save()
                results["installed"].append(exercise_id)
                log(f"✓ {name:<28} infographic installed, removed from Studio")
            except Exception as error:
                results["failed"][exercise_id] = str(error)
                log(f"✗ {name:<28} {error}")
        return results

    def _infographic_prompt(self, exercise_id: str) -> str:
        entry = self.get(exercise_id)
        cues = "; ".join(entry.get("cues", [])[:3])
        return (f"A single clear instructional illustration of ONE exercise: the {entry['name']}"
                f"{' (also called ' + ', '.join(entry['aliases']) + ')' if entry.get('aliases') else ''}. "
                f"Show the start position and the end position of the movement side by side. "
                f"Minimal text: the exercise name and at most 3 short form cues ({cues}). No other exercises.")

    def _nlm(self, *args: str) -> str:
        notebooklm = shutil.which("notebooklm") or str(Path.home() / ".local/bin/notebooklm")
        result = subprocess.run([notebooklm, *args, "-n", self.notebook_id], capture_output=True, text=True, timeout=1200)
        if result.returncode != 0:
            raise RuntimeError(f"notebooklm {args[0]} {args[1]} failed: {(result.stderr or result.stdout).strip()[-300:]}")
        return result.stdout

    def _nlm_json(self, *args: str) -> dict:
        return json.loads(self._nlm(*args, "--json"))

    def _search(self, spec: dict, candidates_dir: Path, *, query: str | None = None,
                limit: int = MAX_PASSAGES) -> list[dict]:
        query = query or f"{spec['name']} exercise: how to perform it, technique, form, common mistakes"
        result = subprocess.run(
            [str(NLM_SEARCH), self.notebook_id, query, "--limit", str(limit), "--images-dir", str(candidates_dir)],
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
        return self._claude(prompt, DETAILS_SCHEMA, candidates_dir)

    def _claude(self, prompt: str, schema: dict, workdir: Path) -> dict:
        claude = shutil.which("claude") or str(Path.home() / ".local/bin/claude")
        result = subprocess.run(
            [claude, "-p", prompt, "--output-format", "json", "--no-session-persistence",
             "--json-schema", json.dumps(schema), "--tools", "Read", "--allowedTools", "Read"],
            capture_output=True, text=True, timeout=600, cwd=workdir,
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
