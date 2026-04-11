#!/usr/bin/env python3
"""
Convert PDF (or other documents) to structured Markdown using Microsoft markitdown.

Usage:
  python pdf_markitdown.py /path/to/file.pdf
  python pdf_markitdown.py /path/to/file.pdf -o output.md
"""

import argparse
import os
import sys


def convert_to_markdown(file_path):
    """Convert document to Markdown using markitdown.

    Args:
        file_path: Path to the input file (PDF, DOCX, PPTX, XLSX, etc.)

    Returns:
        Markdown string.
    """
    try:
        from markitdown import MarkItDown
    except ImportError:
        print("  [error] markitdown not installed. Run: pip install 'markitdown[pdf]'",
              file=sys.stderr)
        sys.exit(1)

    md = MarkItDown()
    try:
        result = md.convert(file_path)
    except Exception as e:
        print(f"  [error] markitdown conversion failed: {e}", file=sys.stderr)
        sys.exit(1)
    return result.markdown


def build_output_path(file_path, output=None):
    """Determine the output file path.

    Args:
        file_path: Input file path.
        output: Explicit output path (optional).

    Returns:
        Absolute path for the output .md file.
    """
    if output:
        return os.path.abspath(output)
    stem = os.path.splitext(os.path.abspath(file_path))[0]
    return f"{stem}_markitdown.md"


def main():
    parser = argparse.ArgumentParser(
        description="Convert PDF to structured Markdown using markitdown")
    parser.add_argument("file", help="Input file path (PDF, DOCX, PPTX, XLSX, etc.)")
    parser.add_argument("-o", "--output", default=None,
                        help="Output .md path (default: {stem}_markitdown.md alongside input)")
    args = parser.parse_args()

    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        print(f"  [error] File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    output_path = build_output_path(file_path, args.output)

    output_dir = os.path.dirname(output_path)
    if output_dir and not os.path.isdir(output_dir):
        print(f"  [error] Output directory does not exist: {output_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"  [input]  {file_path}", file=sys.stderr)
    print(f"  [output] {output_path}", file=sys.stderr)

    markdown = convert_to_markdown(file_path)
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
