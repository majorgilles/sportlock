#!/home/gilozoaire/.local/share/uv/tools/notebooklm-py/bin/python
"""Passage search over the NotebookLM notebook, keeping the book illustrations.

notebooklm-py's `source search` drops chunks that contain images. This helper runs the same
read-only RetrieveRelevantChunks call, captures the raw rows, and returns every chunk with its
text and its inline images (saved as JPEG files).

Usage: nlm_search.py NOTEBOOK_ID QUERY --limit N --images-dir DIR   → JSON on stdout
Runs under the notebooklm-py tool interpreter (see shebang); sportlock calls it as a subprocess.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
from pathlib import Path

import notebooklm._web.sources.search as search_mod
from notebooklm import NotebookLMClient

logging.getLogger("notebooklm").setLevel(logging.ERROR)

captured: list = []
_original = search_mod.decode_relevant_chunks


def _spy(payload, **kwargs):
    captured.append(payload)
    return _original(payload, **kwargs)


search_mod.decode_relevant_chunks = _spy


def _walk_parts(parts, images_dir: Path | None):
    """A chunk's content parts are either [text] or [None, [None, None, base64, mime]]."""
    texts, images = [], []
    for part in parts if isinstance(parts, list) else []:
        if isinstance(part, list) and part and isinstance(part[0], str):
            texts.append(part[0])
        elif (isinstance(part, list) and len(part) > 1 and isinstance(part[1], list) and len(part[1]) > 3
              and isinstance(part[1][2], str) and str(part[1][3]).startswith("image/")):
            data = base64.b64decode(part[1][2])
            if images_dir is not None and len(data) > 2000:  # skip tiny glyphs/bullets
                name = hashlib.sha1(data).hexdigest()[:16] + ".jpg"
                images_dir.mkdir(parents=True, exist_ok=True)
                (images_dir / name).write_bytes(data)
                images.append({"file": str(images_dir / name), "position": len(texts), "bytes": len(data)})
    return "".join(texts), images


def _chunks(payload, images_dir: Path | None) -> list[dict]:
    out = []
    rows = payload[0] if isinstance(payload, list) and payload and isinstance(payload[0], list) else []
    for source in rows:
        if not isinstance(source, list) or len(source) < 2 or not isinstance(source[1], list):
            continue
        for chunk in source[1]:
            try:
                parts = chunk[0][0]
                rank = chunk[1]
            except (IndexError, TypeError):
                continue
            text, images = _walk_parts(parts, images_dir)
            out.append({"source_id": source[0], "rank": rank, "text": text, "images": images})
    return sorted(out, key=lambda c: c["rank"] if isinstance(c["rank"], int) else 999)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("notebook")
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--images-dir")
    args = parser.parse_args()

    async with NotebookLMClient.from_storage() as client:
        await client.sources.search(args.notebook, args.query, limit=args.limit)
        titles = {s.id: s.title for s in await client.sources.list(args.notebook)}

    images_dir = Path(args.images_dir) if args.images_dir else None
    chunks = _chunks(captured[-1], images_dir) if captured else []
    for chunk in chunks:
        chunk["source_title"] = titles.get(chunk["source_id"], "")
    print(json.dumps(chunks[: args.limit * 2]))


asyncio.run(main())
