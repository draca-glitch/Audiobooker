"""Book assembler: merge per-chapter M4B files into a single book-length M4B with chapter markers.

Takes a YAML manifest listing chapters in order with their titles, probes
each file for duration, generates ffmpeg chapter metadata, and concatenates
everything into one M4B with chapter markers that audiobook players
(Apple Books, Prologue, Smart Audiobook Player, etc.) can navigate.

No re-encoding: the chapters are already AAC, so the assembler just remuxes
them into a single container with -c:a copy. Fast even for long books.

Usage:
    audiobooker-assemble book.yaml --output my-book.m4b

Manifest format (book.yaml):
    title: "My Book Title"
    author: "Author Name"
    chapters:
      - title: "Prologue"
        file: out/abc123.m4b
      - title: "Chapter 1"
        file: out/def456.m4b
      - title: "Chapter 2"
        file: out/ghi789.m4b
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


def probe_duration_ms(file_path: str) -> int:
    """Get the duration of an audio file in milliseconds using ffprobe."""
    result = subprocess.run(
        [
            "ffprobe", "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "json",
            file_path,
        ],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {file_path}: {result.stderr}")
    data = json.loads(result.stdout)
    duration_s = float(data["format"]["duration"])
    return int(duration_s * 1000)


def build_metadata(chapters: list[dict], book_title: str | None = None, author: str | None = None) -> str:
    """Build an FFMETADATA1 string with chapter markers and optional book metadata."""
    lines = [";FFMETADATA1"]

    if book_title:
        lines.append(f"title={book_title}")
    if author:
        lines.append(f"artist={author}")
        lines.append(f"album={book_title or 'Audiobook'}")
        lines.append(f"album_artist={author}")
    lines.append("genre=Audiobook")
    lines.append("")

    cursor_ms = 0
    for ch in chapters:
        duration_ms = ch["duration_ms"]
        lines.append("[CHAPTER]")
        lines.append("TIMEBASE=1/1000")
        lines.append(f"START={cursor_ms}")
        lines.append(f"END={cursor_ms + duration_ms}")
        lines.append(f"title={ch['title']}")
        lines.append("")
        cursor_ms += duration_ms

    return "\n".join(lines)


def assemble_book(manifest_path: str, output_path: str) -> None:
    """Assemble per-chapter audio files into a single M4B with chapter markers."""
    manifest = Path(manifest_path)
    if not manifest.is_file():
        print(f"ERROR: manifest not found: {manifest}", file=sys.stderr)
        sys.exit(1)

    with manifest.open() as f:
        book = yaml.safe_load(f)

    book_title = book.get("title")
    author = book.get("author")
    chapters = book.get("chapters", [])

    if not chapters:
        print("ERROR: manifest has no chapters listed", file=sys.stderr)
        sys.exit(1)

    # Resolve file paths relative to the manifest's directory
    manifest_dir = manifest.parent
    for ch in chapters:
        ch_path = Path(ch["file"])
        if not ch_path.is_absolute():
            ch_path = manifest_dir / ch_path
        ch["resolved_path"] = str(ch_path.resolve())
        if not ch_path.is_file():
            print(f"ERROR: chapter file not found: {ch_path}", file=sys.stderr)
            sys.exit(1)

    # Probe durations
    total_duration_ms = 0
    for i, ch in enumerate(chapters):
        print(f"  [{i+1}/{len(chapters)}] Probing {ch['title']}...")
        ch["duration_ms"] = probe_duration_ms(ch["resolved_path"])
        total_duration_ms += ch["duration_ms"]
        minutes = ch["duration_ms"] / 60000
        print(f"           {minutes:.1f} min")

    total_minutes = total_duration_ms / 60000
    print(f"\nTotal: {len(chapters)} chapters, {total_minutes:.1f} min")

    # Write ffmpeg concat file list
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        concat_path = f.name
        for ch in chapters:
            f.write(f"file '{ch['resolved_path']}'\n")

    # Write ffmpeg metadata file
    metadata_str = build_metadata(chapters, book_title=book_title, author=author)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        metadata_path = f.name
        f.write(metadata_str)

    # Assemble: concat + chapter markers, no re-encoding (copy codec).
    # Wrap in try/finally so temp files are always cleaned up, even if
    # ffmpeg itself crashes or the process is killed mid-run.
    print(f"Assembling {output_path}...")
    try:
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "concat", "-safe", "0", "-i", concat_path,
                "-i", metadata_path,
                "-map_metadata", "1",
                "-c:a", "copy",
                output_path,
            ],
            capture_output=True, text=True,
        )
    finally:
        for tmp in (concat_path, metadata_path):
            try:
                os.unlink(tmp)
            except OSError:
                pass

    if result.returncode != 0:
        print(f"ERROR: ffmpeg assemble failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    size_mb = Path(output_path).stat().st_size / (1024 * 1024)
    print(f"\nDone: {output_path} ({size_mb:.1f} MB, {total_minutes:.1f} min, {len(chapters)} chapters)")
    if book_title:
        print(f"  Title:  {book_title}")
    if author:
        print(f"  Author: {author}")
    print(f"  Format: M4B with chapter markers")


def main():
    parser = argparse.ArgumentParser(
        description="Assemble per-chapter audio files into a single M4B audiobook with chapter markers",
    )
    parser.add_argument("manifest", help="Path to book manifest YAML (lists chapters in order with titles)")
    parser.add_argument("--output", required=True, help="Output M4B path")
    args = parser.parse_args()

    assemble_book(args.manifest, args.output)


if __name__ == "__main__":
    main()
