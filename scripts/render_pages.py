#!/usr/bin/env python3
"""
PDFの各ページをPNG画像としてレンダリングする。

Usage:
  python render_pages.py /path/to/file.pdf
  python render_pages.py /path/to/file.pdf -o /tmp/output_dir
  python render_pages.py /path/to/file.pdf --dpi 300

PPTX等はユーザーが事前にPDFへ変換してから使用する。
"""

import argparse
import os
import sys
import tempfile

import fitz


def render_pdf_pages(file_path, output_dir, dpi=200):
    """PDFの各ページをPNGにレンダリング"""
    doc = fitz.open(file_path)
    pages = []
    for page_num in range(len(doc)):
        page = doc[page_num]
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat)
        output_path = os.path.join(output_dir, f"page_{page_num + 1:03d}.png")
        pix.save(output_path)
        pages.append(output_path)
        print(f"  [render] page {page_num + 1}/{len(doc)}", file=sys.stderr)
    doc.close()
    return pages


def main():
    parser = argparse.ArgumentParser(
        description="PDFの各ページをPNG画像にレンダリング",
    )
    parser.add_argument("file", help="PDFファイルパス")
    parser.add_argument("-o", "--output-dir",
                        help="出力ディレクトリ（省略時は自動生成）")
    parser.add_argument("--dpi", type=int, default=200,
                        help="レンダリングDPI（デフォルト: 200）")
    args = parser.parse_args()

    file_path = os.path.abspath(args.file)
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    ext = os.path.splitext(file_path)[1].lower()
    if ext != ".pdf":
        print(f"PDFのみ対応しています（入力: {ext}）。PPTX等は事前にPDFへ変換してください。",
              file=sys.stderr)
        sys.exit(1)

    if args.output_dir:
        output_dir = os.path.abspath(args.output_dir)
        os.makedirs(output_dir, exist_ok=True)
    else:
        output_dir = tempfile.mkdtemp(prefix="render_pages_")

    print(f"Input:  {file_path}", file=sys.stderr)
    print(f"Output: {output_dir}", file=sys.stderr)

    pages = render_pdf_pages(file_path, output_dir, args.dpi)

    print(f"Total: {len(pages)} image(s)", file=sys.stderr)

    # stdout にはファイルパスのみ出力（スキルからのパース用）
    for p in pages:
        print(p)


if __name__ == "__main__":
    main()
