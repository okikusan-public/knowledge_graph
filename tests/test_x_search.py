#!/usr/bin/env python3
"""x_search.py のユニットテスト"""

import datetime
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from x_search import (
    build_output_path,
    build_tools_config,
    format_response_as_markdown,
    sanitize_query,
)


# --- openai が利用可能かチェック ---
_openai_available = (
    subprocess.run(
        [sys.executable, "-c", "from openai import OpenAI"],
        capture_output=True,
    ).returncode == 0
)


class TestSanitizeQuery(unittest.TestCase):
    """sanitize_query 関数のテスト"""

    def test_simple_query(self):
        """通常のクエリをサニタイズ"""
        self.assertEqual(sanitize_query("AI news"), "AI_news")

    def test_special_characters(self):
        """特殊文字が除去される"""
        result = sanitize_query("@elonmusk #AI $TSLA")
        self.assertNotIn("@", result)
        self.assertNotIn("#", result)
        self.assertNotIn("$", result)

    def test_long_query_truncated(self):
        """長いクエリが50文字に切り詰められる"""
        long_query = "a" * 100
        result = sanitize_query(long_query)
        self.assertLessEqual(len(result), 50)

    def test_empty_string(self):
        """空文字列はuntitledを返す"""
        self.assertEqual(sanitize_query(""), "untitled")

    def test_none_returns_untitled(self):
        """Noneはuntitledを返す"""
        self.assertEqual(sanitize_query(None), "untitled")

    def test_unicode_query(self):
        """日本語クエリが処理される"""
        result = sanitize_query("生成AI トレンド")
        self.assertGreater(len(result), 0)

    def test_whitespace_collapsed(self):
        """連続空白が1つのアンダースコアに"""
        self.assertEqual(sanitize_query("a   b"), "a_b")

    def test_all_special_chars(self):
        """全て特殊文字の場合はsearchを返す"""
        self.assertEqual(sanitize_query("@#$%^&"), "search")


class TestBuildOutputPath(unittest.TestCase):
    """build_output_path 関数のテスト"""

    def test_default_output(self):
        """デフォルト出力にx_search_と日付が含まれる"""
        result = build_output_path("AI news")
        self.assertIn("x_search_", result)
        self.assertIn(datetime.date.today().isoformat(), result)
        self.assertTrue(result.endswith(".md"))

    def test_custom_output(self):
        """-o 指定時はそのパスを使用"""
        result = build_output_path("query", output="/tmp/custom.md")
        self.assertEqual(result, "/tmp/custom.md")

    def test_output_is_absolute(self):
        """結果は常に絶対パス"""
        result = build_output_path("test query")
        self.assertTrue(os.path.isabs(result))

    def test_query_in_filename(self):
        """サニタイズ済みクエリがファイル名に含まれる"""
        result = build_output_path("AI agent")
        self.assertIn("AI_agent", result)


class TestBuildToolsConfig(unittest.TestCase):
    """build_tools_config 関数のテスト"""

    def test_default_config(self):
        """デフォルト設定でx_searchツールが返る"""
        tools = build_tools_config()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["type"], "x_search")
        self.assertIn("from_date", tools[0])
        self.assertIn("to_date", tools[0])

    def test_date_range(self):
        """日付範囲がdays引数に基づく"""
        tools = build_tools_config(days=30)
        from_date = datetime.date.fromisoformat(tools[0]["from_date"])
        to_date = datetime.date.fromisoformat(tools[0]["to_date"])
        self.assertEqual((to_date - from_date).days, 30)

    def test_with_handles(self):
        """handlesが設定される"""
        tools = build_tools_config(handles=["elonmusk", "openai"])
        self.assertEqual(tools[0]["allowed_x_handles"], ["elonmusk", "openai"])

    def test_with_exclude_handles(self):
        """exclude_handlesが設定される"""
        tools = build_tools_config(exclude_handles=["bot1"])
        self.assertEqual(tools[0]["excluded_x_handles"], ["bot1"])

    def test_handles_and_exclude_raises(self):
        """両方指定でValueError"""
        with self.assertRaises(ValueError):
            build_tools_config(handles=["a"], exclude_handles=["b"])

    def test_web_search_flag(self):
        """web_search=Trueで2つのツールが返る"""
        tools = build_tools_config(web_search=True)
        self.assertEqual(len(tools), 2)
        types = [t["type"] for t in tools]
        self.assertIn("x_search", types)
        self.assertIn("web_search", types)

    def test_handles_strip_at(self):
        """@プレフィックスが除去される"""
        tools = build_tools_config(handles=["@elonmusk", "@openai"])
        self.assertEqual(tools[0]["allowed_x_handles"], ["elonmusk", "openai"])

    def test_handles_max_10(self):
        """ハンドルは最大10個に制限"""
        handles = [f"user{i}" for i in range(20)]
        tools = build_tools_config(handles=handles)
        self.assertEqual(len(tools[0]["allowed_x_handles"]), 10)


class TestFormatResponseAsMarkdown(unittest.TestCase):
    """format_response_as_markdown 関数のテスト"""

    def _make_mock_response(self, text="Search results here", citations=None):
        """モックレスポンスを作成"""
        content = MagicMock()
        content.type = "output_text"
        content.text = text
        annotations = []
        if citations:
            for title, url in citations:
                ann = MagicMock()
                ann.type = "url_citation"
                ann.url = url
                ann.title = title
                annotations.append(ann)
        content.annotations = annotations

        message = MagicMock()
        message.type = "message"
        message.content = [content]

        response = MagicMock()
        response.output = [message]
        return response

    def test_basic_formatting(self):
        """基本的なMarkdownフォーマット"""
        response = self._make_mock_response("AI is trending on X.")
        md = format_response_as_markdown(
            response, "AI trends", "2026-04-06", "2026-04-13", "grok-4-1-fast"
        )
        self.assertIn("# X Search: AI trends", md)
        self.assertIn("2026-04-06", md)
        self.assertIn("AI is trending on X.", md)

    def test_empty_response(self):
        """空レスポンスでも有効なMarkdownが生成される"""
        response = MagicMock()
        response.output = []
        md = format_response_as_markdown(
            response, "obscure query", "2026-04-06", "2026-04-13", "grok-4-1-fast"
        )
        self.assertIn("# X Search:", md)
        self.assertIn("No results found", md)

    def test_citations_included(self):
        """引用URLがCitationsセクションに含まれる"""
        response = self._make_mock_response(
            "Results with citations",
            citations=[("Post 1", "https://x.com/post/1"), ("Post 2", "https://x.com/post/2")],
        )
        md = format_response_as_markdown(
            response, "test", "2026-04-06", "2026-04-13", "grok-4-1-fast"
        )
        self.assertIn("## Citations", md)
        self.assertIn("https://x.com/post/1", md)
        self.assertIn("https://x.com/post/2", md)


class TestXSearchCLI(unittest.TestCase):
    """x_search.py のCLIテスト"""

    SCRIPT = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "x_search.py"
    )

    def test_cli_no_arguments(self):
        """引数なしでexit non-zero"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_cli_help(self):
        """--helpが正常に表示される"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("search", result.stdout.lower())

    def test_cli_missing_api_key(self):
        """XAI_API_KEY未設定でexit 1"""
        env = os.environ.copy()
        env.pop("XAI_API_KEY", None)
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "test query"],
            capture_output=True, text=True, env=env,
        )
        # openai未インストールの場合もexit 1になるのでreturncode確認のみ
        self.assertEqual(result.returncode, 1)

    def test_cli_output_dir_not_found(self):
        """出力先ディレクトリが存在しない場合exit 1"""
        env = os.environ.copy()
        env["XAI_API_KEY"] = "test-key"
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "test query",
             "-o", "/nonexistent/dir/out.md"],
            capture_output=True, text=True, env=env,
        )
        # openai未インストールの場合はそちらでexit 1
        self.assertEqual(result.returncode, 1)


class TestXSearchIntegration(unittest.TestCase):
    """Grok APIを使った統合テスト (ネットワーク + openai + XAI_API_KEY 必須)"""

    SCRIPT = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "x_search.py"
    )

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_x_search_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @unittest.skipUnless(_openai_available, "openai not installed")
    @unittest.skipUnless(
        os.environ.get("RUN_INTEGRATION_TESTS"),
        "Set RUN_INTEGRATION_TESTS=1 to run network-dependent tests",
    )
    @unittest.skipUnless(
        os.environ.get("XAI_API_KEY"),
        "Set XAI_API_KEY to run Grok API integration tests",
    )
    def test_search_and_save(self):
        """X検索を実行しMarkdownファイルが生成される"""
        output_path = os.path.join(self.tmpdir, "test_output.md")
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "AI", "--days", "1",
             "-o", output_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertTrue(os.path.isfile(output_path))

        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("# X Search:", content)


if __name__ == "__main__":
    unittest.main()
