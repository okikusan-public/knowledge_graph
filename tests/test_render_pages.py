#!/usr/bin/env python3
"""render_pages.py のユニットテスト"""

import os
import subprocess
import shutil
import sys
import tempfile
import unittest

import fitz

# テスト対象のインポート
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from render_pages import render_pdf_pages


def _create_test_pdf(path, num_pages=3):
    """テスト用PDFを生成"""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 100), f"Page {i + 1}", fontsize=20)
    doc.save(path)
    doc.close()


class TestRenderPdfPages(unittest.TestCase):
    """render_pdf_pages 関数のテスト"""

    def setUp(self):
        self.output_dir = tempfile.mkdtemp(prefix="test_render_")
        self.pdf_path = os.path.join(self.output_dir, "test.pdf")

    def tearDown(self):
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_renders_all_pages(self):
        """全ページ分のPNGが生成される"""
        _create_test_pdf(self.pdf_path, num_pages=3)
        pages = render_pdf_pages(self.pdf_path, self.output_dir)
        self.assertEqual(len(pages), 3)

    def test_output_files_exist(self):
        """出力PNGファイルが実際に存在する"""
        _create_test_pdf(self.pdf_path, num_pages=2)
        pages = render_pdf_pages(self.pdf_path, self.output_dir)
        for p in pages:
            self.assertTrue(os.path.isfile(p))

    def test_output_filenames(self):
        """ファイル名がpage_001.png形式になる"""
        _create_test_pdf(self.pdf_path, num_pages=2)
        pages = render_pdf_pages(self.pdf_path, self.output_dir)
        self.assertTrue(pages[0].endswith("page_001.png"))
        self.assertTrue(pages[1].endswith("page_002.png"))

    def test_output_is_valid_png(self):
        """出力ファイルが有効なPNG画像である"""
        _create_test_pdf(self.pdf_path, num_pages=1)
        pages = render_pdf_pages(self.pdf_path, self.output_dir)
        with open(pages[0], "rb") as f:
            header = f.read(8)
        # PNGマジックバイト
        self.assertEqual(header[:4], b"\x89PNG")

    def test_single_page(self):
        """1ページPDFが正しく処理される"""
        _create_test_pdf(self.pdf_path, num_pages=1)
        pages = render_pdf_pages(self.pdf_path, self.output_dir)
        self.assertEqual(len(pages), 1)

    def test_dpi_affects_size(self):
        """DPIが高いほど画像サイズが大きくなる"""
        _create_test_pdf(self.pdf_path, num_pages=1)
        pages_low = render_pdf_pages(self.pdf_path, self.output_dir, dpi=72)
        size_low = os.path.getsize(pages_low[0])

        out_hi = os.path.join(self.output_dir, "hi")
        os.makedirs(out_hi)
        pages_high = render_pdf_pages(self.pdf_path, out_hi, dpi=300)
        size_high = os.path.getsize(pages_high[0])

        self.assertGreater(size_high, size_low)


class TestRenderPagesCLI(unittest.TestCase):
    """render_pages.py のCLIテスト"""

    SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "render_pages.py")

    def setUp(self):
        self.output_dir = tempfile.mkdtemp(prefix="test_cli_")
        self.pdf_path = os.path.join(self.output_dir, "test.pdf")

    def tearDown(self):
        shutil.rmtree(self.output_dir, ignore_errors=True)

    def test_cli_success(self):
        """CLIが正常終了し、stdoutにパスを出力する"""
        _create_test_pdf(self.pdf_path, num_pages=2)
        result = subprocess.run(
            [sys.executable, self.SCRIPT, self.pdf_path, "-o", self.output_dir],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        stdout_lines = [l for l in result.stdout.strip().split("\n") if l]
        self.assertEqual(len(stdout_lines), 2)
        for line in stdout_lines:
            self.assertTrue(line.endswith(".png"))

    def test_cli_file_not_found(self):
        """存在しないファイルでexit 1"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "/nonexistent/file.pdf"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)

    def test_cli_non_pdf_rejected(self):
        """PDF以外のファイルでexit 1"""
        pptx_path = os.path.join(self.output_dir, "test.pptx")
        with open(pptx_path, "w") as f:
            f.write("dummy")
        result = subprocess.run(
            [sys.executable, self.SCRIPT, pptx_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("PDF", result.stderr)

    def test_cli_auto_output_dir(self):
        """出力ディレクトリ未指定でも動作する"""
        _create_test_pdf(self.pdf_path, num_pages=1)
        result = subprocess.run(
            [sys.executable, self.SCRIPT, self.pdf_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        output_path = result.stdout.strip()
        self.assertTrue(os.path.isfile(output_path))
        # クリーンアップ
        shutil.rmtree(os.path.dirname(output_path), ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
