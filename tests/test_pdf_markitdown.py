#!/usr/bin/env python3
"""pdf_markitdown.py のユニットテスト"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import fitz

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from pdf_markitdown import build_output_path, convert_to_markdown


# --- markitdown が利用可能かチェック ---
_markitdown_available = (
    subprocess.run(
        [sys.executable, "-c", "from markitdown import MarkItDown"],
        capture_output=True,
    ).returncode == 0
)


def _create_test_pdf(path, num_pages=1, with_table=False):
    """テスト用PDFを生成"""
    doc = fitz.open()
    for i in range(num_pages):
        page = doc.new_page(width=595, height=842)
        page.insert_text((72, 80), f"Heading {i + 1}", fontsize=20)
        page.insert_text((72, 120), f"Paragraph content on page {i + 1}.", fontsize=12)
        if with_table and i == 0:
            page.insert_text((72, 160), "Name    | Role    | Team", fontsize=10)
            page.insert_text((72, 175), "Alice   | Engineer| Alpha", fontsize=10)
            page.insert_text((72, 190), "Bob     | Manager | Beta", fontsize=10)
    doc.save(path)
    doc.close()


class TestConvertToMarkdown(unittest.TestCase):
    """convert_to_markdown 関数のテスト"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_convert_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    def test_returns_markdown_string(self):
        """PDF変換結果がMarkdown文字列で返される"""
        pdf_path = os.path.join(self.tmpdir, "test.pdf")
        _create_test_pdf(pdf_path)
        result = convert_to_markdown(pdf_path)
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 0)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    def test_contains_pdf_text(self):
        """変換結果に元PDFのテキストが含まれる"""
        pdf_path = os.path.join(self.tmpdir, "test.pdf")
        _create_test_pdf(pdf_path)
        result = convert_to_markdown(pdf_path)
        self.assertIn("Heading", result)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    def test_nonexistent_file_exits(self):
        """存在しないファイルでSystemExit"""
        with self.assertRaises(SystemExit):
            convert_to_markdown("/nonexistent/file.pdf")

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    def test_corrupted_file_returns_fallback(self):
        """壊れたPDFでもmarkitdownはフォールバックテキストを返す"""
        bad_path = os.path.join(self.tmpdir, "bad.pdf")
        with open(bad_path, "w") as f:
            f.write("not a real pdf")
        result = convert_to_markdown(bad_path)
        self.assertIsInstance(result, str)


class TestBuildOutputPath(unittest.TestCase):
    """build_output_path 関数のテスト"""

    def test_default_output_pdf(self):
        """PDFのデフォルト出力は {stem}_markitdown.md"""
        result = build_output_path("/path/to/document.pdf")
        self.assertEqual(result, "/path/to/document_markitdown.md")

    def test_default_output_docx(self):
        """DOCXでも同様に動作する"""
        result = build_output_path("/path/to/report.docx")
        self.assertEqual(result, "/path/to/report_markitdown.md")

    def test_custom_output(self):
        """-o 指定時はそのパスを使用"""
        result = build_output_path("/path/to/doc.pdf", output="/tmp/custom.md")
        self.assertEqual(result, "/tmp/custom.md")

    def test_filename_with_spaces(self):
        """スペースを含むファイル名を処理できる"""
        result = build_output_path("/path/to/my document.pdf")
        self.assertEqual(result, "/path/to/my document_markitdown.md")

    def test_nested_path(self):
        """深いディレクトリパスを処理できる"""
        result = build_output_path("/a/b/c/d/file.pdf")
        self.assertEqual(result, "/a/b/c/d/file_markitdown.md")


class TestPdfMarkitdownCLI(unittest.TestCase):
    """pdf_markitdown.py のCLIテスト"""

    SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "pdf_markitdown.py")

    def test_cli_no_arguments(self):
        """引数なしでexit non-zero"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_cli_file_not_found(self):
        """存在しないファイルでexit 1"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "/nonexistent/file.pdf"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr.lower())

    def test_cli_help(self):
        """--helpが正常に表示される"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("markitdown", result.stdout.lower())

    def test_cli_output_dir_not_found(self):
        """出力先ディレクトリが存在しない場合exit 1"""
        tmpdir = tempfile.mkdtemp(prefix="test_cli_")
        try:
            pdf_path = os.path.join(tmpdir, "test.pdf")
            _create_test_pdf(pdf_path)
            result = subprocess.run(
                [sys.executable, self.SCRIPT, pdf_path, "-o", "/nonexistent/dir/out.md"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("directory", result.stderr.lower())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestPdfMarkitdownIntegration(unittest.TestCase):
    """markitdown を使った統合テスト (markitdown インストール必須)"""

    SCRIPT = os.path.join(os.path.dirname(__file__), "..", "scripts", "pdf_markitdown.py")

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_markitdown_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    def test_convert_simple_pdf(self):
        """シンプルなPDFを変換しMarkdownファイルが生成される"""
        pdf_path = os.path.join(self.tmpdir, "test.pdf")
        _create_test_pdf(pdf_path)

        result = subprocess.run(
            [sys.executable, self.SCRIPT, pdf_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        output_path = result.stdout.strip()
        self.assertTrue(os.path.isfile(output_path))
        self.assertTrue(output_path.endswith("_markitdown.md"))

        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertGreater(len(content), 0)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    def test_convert_multipage_pdf(self):
        """複数ページPDFの変換"""
        pdf_path = os.path.join(self.tmpdir, "multi.pdf")
        _create_test_pdf(pdf_path, num_pages=3)

        result = subprocess.run(
            [sys.executable, self.SCRIPT, pdf_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")

        output_path = result.stdout.strip()
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertGreater(len(content), 0)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    def test_custom_output_path(self):
        """-o フラグでカスタム出力先に書き込める"""
        pdf_path = os.path.join(self.tmpdir, "test.pdf")
        _create_test_pdf(pdf_path)

        custom_output = os.path.join(self.tmpdir, "custom_output.md")
        result = subprocess.run(
            [sys.executable, self.SCRIPT, pdf_path, "-o", custom_output],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertTrue(os.path.isfile(custom_output))

        output_path = result.stdout.strip()
        self.assertEqual(output_path, custom_output)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    def test_output_contains_text(self):
        """変換結果にPDFのテキスト内容が含まれる"""
        pdf_path = os.path.join(self.tmpdir, "content.pdf")
        _create_test_pdf(pdf_path)

        result = subprocess.run(
            [sys.executable, self.SCRIPT, pdf_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)

        output_path = result.stdout.strip()
        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Heading", content)
        self.assertIn("Paragraph", content)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    def test_stderr_shows_status(self):
        """stderrにステータスメッセージが出力される"""
        pdf_path = os.path.join(self.tmpdir, "test.pdf")
        _create_test_pdf(pdf_path)

        result = subprocess.run(
            [sys.executable, self.SCRIPT, pdf_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("[input]", result.stderr)
        self.assertIn("[output]", result.stderr)
        self.assertIn("[done]", result.stderr)


if __name__ == "__main__":
    unittest.main()
