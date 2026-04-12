#!/usr/bin/env python3
"""
Convert YouTube video to structured Markdown using Microsoft markitdown.

Extracts video metadata (title, keywords, runtime), description, and transcript.

Usage:
  python youtube_markitdown.py "https://www.youtube.com/watch?v=VIDEO_ID"
  python youtube_markitdown.py "https://youtu.be/VIDEO_ID" -o output.md
  python youtube_markitdown.py "https://www.youtube.com/watch?v=VIDEO_ID" --lang ja en
"""

import argparse
import os
import re
import sys
from urllib.parse import parse_qs, urlparse

# YouTube video IDs are 11 characters: alphanumeric, dash, underscore
_VIDEO_ID_RE = re.compile(r'^[A-Za-z0-9_-]{10,12}$')


def _validate_video_id(video_id):
    """Validate extracted video ID format. Returns ID or None."""
    if video_id and _VIDEO_ID_RE.match(video_id):
        return video_id
    return None


def extract_video_id(url):
    """Extract video ID from various YouTube URL formats.

    Supported formats:
      - https://www.youtube.com/watch?v=ID
      - https://youtube.com/watch?v=ID
      - https://m.youtube.com/watch?v=ID
      - https://youtu.be/ID
      - https://www.youtube.com/embed/ID
      - https://www.youtube.com/shorts/ID
      - https://www.youtube.com/live/ID

    Returns:
        Video ID string, or None if invalid.
    """
    if not url:
        return None

    try:
        parsed = urlparse(url)
    except Exception:
        return None

    if parsed.scheme not in ("http", "https"):
        return None

    if not parsed.hostname:
        return None

    hostname = parsed.hostname.lower()

    # youtu.be/VIDEO_ID
    if hostname == "youtu.be":
        video_id = parsed.path.lstrip("/").split("/")[0]
        return _validate_video_id(video_id)

    # youtube.com variants
    if hostname not in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        return None

    # /embed/VIDEO_ID, /shorts/VIDEO_ID, /live/VIDEO_ID
    for prefix in ("/embed/", "/shorts/", "/live/"):
        if parsed.path.startswith(prefix):
            parts = parsed.path.split("/")
            video_id = parts[2] if len(parts) > 2 else None
            return _validate_video_id(video_id)

    # /watch?v=VIDEO_ID
    if parsed.path == "/watch":
        qs = parse_qs(parsed.query)
        ids = qs.get("v")
        return _validate_video_id(ids[0]) if ids else None

    return None


def normalize_youtube_url(url):
    """Normalize any YouTube URL to canonical format.

    Returns:
        Canonical URL: https://www.youtube.com/watch?v=VIDEO_ID

    Raises:
        ValueError if URL is not a valid YouTube video URL.
    """
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {url}")
    return f"https://www.youtube.com/watch?v={video_id}"


def convert_youtube_to_markdown(url, languages=None):
    """Convert YouTube video to Markdown using markitdown.

    Args:
        url: YouTube video URL (any supported format).
        languages: Transcript language priority list (default: ["ja", "en"]).

    Returns:
        Markdown string.
    """
    try:
        from markitdown import MarkItDown
    except ImportError:
        print("  [error] markitdown not installed. Run: pip install 'markitdown[pdf]'",
              file=sys.stderr)
        sys.exit(1)

    # Check transcript capability
    try:
        from markitdown.converters._youtube_converter import IS_YOUTUBE_TRANSCRIPT_CAPABLE
        if not IS_YOUTUBE_TRANSCRIPT_CAPABLE:
            print("  [warn] youtube-transcript-api not installed. "
                  "Transcript will not be extracted. "
                  "Run: pip install youtube-transcript-api", file=sys.stderr)
    except ImportError:
        pass

    if languages is None:
        languages = ["ja", "en"]

    canonical_url = normalize_youtube_url(url)

    md = MarkItDown()
    try:
        result = md.convert(canonical_url, youtube_transcript_languages=languages)
    except Exception as e:
        print(f"  [error] markitdown conversion failed: {e}", file=sys.stderr)
        sys.exit(1)
    return result.markdown


def build_output_path(video_id, output=None):
    """Determine the output file path.

    Args:
        video_id: YouTube video ID.
        output: Explicit output path (optional).

    Returns:
        Absolute path for the output .md file.
    """
    if output:
        return os.path.abspath(output)
    return os.path.abspath(os.path.join("docs", f"youtube_{video_id}_markitdown.md"))


def main():
    parser = argparse.ArgumentParser(
        description="Convert YouTube video to structured Markdown using markitdown")
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument("-o", "--output", default=None,
                        help="Output .md path (default: docs/youtube_{id}_markitdown.md)")
    parser.add_argument("--lang", nargs="+", default=["ja", "en"],
                        help="Transcript language priority (default: ja en)")
    args = parser.parse_args()

    video_id = extract_video_id(args.url)
    if not video_id:
        print(f"  [error] Invalid YouTube URL: {args.url}", file=sys.stderr)
        sys.exit(1)

    output_path = build_output_path(video_id, args.output)

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.isdir(output_dir):
        print(f"  [error] Output directory does not exist: {output_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"  [input]  {args.url}", file=sys.stderr)
    print(f"  [output] {output_path}", file=sys.stderr)

    markdown = convert_youtube_to_markdown(args.url, args.lang)
    if not markdown or not markdown.strip():
        print("  [error] Conversion produced empty output", file=sys.stderr)
        sys.exit(1)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(markdown)

    char_count = len(markdown)
    line_count = markdown.count("\n") + 1
    print(f"  [done] {char_count} chars, {line_count} lines written", file=sys.stderr)

    # stdout: output path only (for pipeline consumption)
    print(output_path)


if __name__ == "__main__":
    main()
