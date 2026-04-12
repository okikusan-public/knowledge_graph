#!/usr/bin/env python3
"""youtube_markitdown.py のユニットテスト"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from youtube_markitdown import (
    build_output_path,
    convert_youtube_to_markdown,
    extract_video_id,
    normalize_youtube_url,
)


# --- markitdown が利用可能かチェック ---
_markitdown_available = (
    subprocess.run(
        [sys.executable, "-c", "from markitdown import MarkItDown"],
        capture_output=True,
    ).returncode == 0
)


class TestExtractVideoId(unittest.TestCase):
    """extract_video_id 関数のテスト"""

    def test_standard_url(self):
        """標準的なYouTube URLからIDを抽出"""
        self.assertEqual(
            extract_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_no_www(self):
        """www無しのURLからIDを抽出"""
        self.assertEqual(
            extract_video_id("https://youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_short_url(self):
        """短縮URL (youtu.be) からIDを抽出"""
        self.assertEqual(
            extract_video_id("https://youtu.be/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_embed_url(self):
        """embed URLからIDを抽出"""
        self.assertEqual(
            extract_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_mobile_url(self):
        """モバイルURLからIDを抽出"""
        self.assertEqual(
            extract_video_id("https://m.youtube.com/watch?v=dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_url_with_extra_params(self):
        """追加パラメータ付きURLからIDを抽出"""
        self.assertEqual(
            extract_video_id(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=120&list=PLxxx"
            ),
            "dQw4w9WgXcQ",
        )

    def test_invalid_url_returns_none(self):
        """YouTube以外のURLはNoneを返す"""
        self.assertIsNone(extract_video_id("https://example.com/video"))

    def test_empty_string_returns_none(self):
        """空文字列はNoneを返す"""
        self.assertIsNone(extract_video_id(""))

    def test_none_returns_none(self):
        """NoneはNoneを返す"""
        self.assertIsNone(extract_video_id(None))

    def test_no_video_id_returns_none(self):
        """video IDのないwatch URLはNoneを返す"""
        self.assertIsNone(extract_video_id("https://www.youtube.com/watch?"))

    def test_youtube_channel_url_returns_none(self):
        """チャンネルURLはNoneを返す"""
        self.assertIsNone(
            extract_video_id("https://www.youtube.com/channel/UCxxx")
        )

    def test_plain_text_returns_none(self):
        """ただのテキストはNoneを返す"""
        self.assertIsNone(extract_video_id("not a url at all"))

    def test_shorts_url(self):
        """Shorts URLからIDを抽出"""
        self.assertEqual(
            extract_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_live_url(self):
        """Live URLからIDを抽出"""
        self.assertEqual(
            extract_video_id("https://www.youtube.com/live/dQw4w9WgXcQ"),
            "dQw4w9WgXcQ",
        )

    def test_ftp_scheme_returns_none(self):
        """ftp://スキームはNoneを返す"""
        self.assertIsNone(
            extract_video_id("ftp://www.youtube.com/watch?v=dQw4w9WgXcQ")
        )

    def test_malformed_video_id_returns_none(self):
        """不正な形式のvideo IDはNoneを返す"""
        self.assertIsNone(
            extract_video_id("https://youtu.be/x")
        )

    def test_empty_embed_path_returns_none(self):
        """embed/の後にIDがない場合はNoneを返す"""
        self.assertIsNone(
            extract_video_id("https://www.youtube.com/embed/")
        )


class TestNormalizeYoutubeUrl(unittest.TestCase):
    """normalize_youtube_url 関数のテスト"""

    def test_normalizes_short_url(self):
        """短縮URLを正規形に変換"""
        self.assertEqual(
            normalize_youtube_url("https://youtu.be/dQw4w9WgXcQ"),
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_already_canonical(self):
        """正規形URLはそのまま返す"""
        canonical = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        self.assertEqual(normalize_youtube_url(canonical), canonical)

    def test_raises_on_invalid(self):
        """無効なURLでValueErrorが発生"""
        with self.assertRaises(ValueError):
            normalize_youtube_url("https://example.com/video")

    def test_raises_on_empty(self):
        """空文字列でValueErrorが発生"""
        with self.assertRaises(ValueError):
            normalize_youtube_url("")


class TestBuildOutputPath(unittest.TestCase):
    """build_output_path 関数のテスト"""

    def test_default_output(self):
        """デフォルト出力はdocs/youtube_{id}_markitdown.md"""
        result = build_output_path("dQw4w9WgXcQ")
        self.assertTrue(result.endswith("docs/youtube_dQw4w9WgXcQ_markitdown.md"))
        self.assertTrue(os.path.isabs(result))

    def test_custom_output(self):
        """-o 指定時はそのパスを使用"""
        result = build_output_path("dQw4w9WgXcQ", output="/tmp/custom.md")
        self.assertEqual(result, "/tmp/custom.md")

    def test_output_is_absolute(self):
        """結果は常に絶対パス"""
        result = build_output_path("dQw4w9WgXcQ")
        self.assertTrue(os.path.isabs(result))

    def test_video_id_with_dash_underscore(self):
        """ダッシュ・アンダースコア入りのIDが正しく処理される"""
        result = build_output_path("abc-_def1234")
        self.assertIn("youtube_abc-_def1234_markitdown.md", result)


class TestConvertYoutubeToMarkdown(unittest.TestCase):
    """convert_youtube_to_markdown 関数のテスト (モック使用)"""

    def test_calls_markitdown_with_languages(self):
        """markitdownに言語パラメータが渡される"""
        mock_instance = MagicMock()
        mock_result = MagicMock()
        mock_result.markdown = "# YouTube\n\n## Test Video"
        mock_instance.convert.return_value = mock_result

        mock_markitdown_module = MagicMock()
        mock_markitdown_module.MarkItDown.return_value = mock_instance

        # 関数内importをモックするためsys.modulesをパッチ
        with patch.dict("sys.modules", {
            "markitdown": mock_markitdown_module,
            "markitdown.converters": MagicMock(),
            "markitdown.converters._youtube_converter": MagicMock(
                IS_YOUTUBE_TRANSCRIPT_CAPABLE=True
            ),
        }):
            result = convert_youtube_to_markdown(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                languages=["ja", "en"],
            )

        self.assertEqual(result, "# YouTube\n\n## Test Video")
        mock_instance.convert.assert_called_once()
        call_kwargs = mock_instance.convert.call_args
        self.assertEqual(
            call_kwargs.kwargs.get("youtube_transcript_languages"),
            ["ja", "en"],
        )


class TestYoutubeMarkitdownCLI(unittest.TestCase):
    """youtube_markitdown.py のCLIテスト"""

    SCRIPT = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "youtube_markitdown.py"
    )

    def test_cli_no_arguments(self):
        """引数なしでexit non-zero"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT],
            capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_cli_invalid_url(self):
        """無効なURLでexit 1"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "https://example.com/video"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("invalid", result.stderr.lower())

    def test_cli_help(self):
        """--helpが正常に表示される"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("youtube", result.stdout.lower())

    def test_cli_output_dir_not_found(self):
        """出力先ディレクトリが存在しない場合exit 1"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT,
             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
             "-o", "/nonexistent/dir/out.md"],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("directory", result.stderr.lower())

    def test_cli_lang_flag_parsed(self):
        """--lang フラグがパースされる (--helpの出力で確認)"""
        result = subprocess.run(
            [sys.executable, self.SCRIPT, "--help"],
            capture_output=True, text=True,
        )
        self.assertIn("--lang", result.stdout)


class TestYoutubeMarkitdownIntegration(unittest.TestCase):
    """markitdown を使った統合テスト (ネットワーク + markitdown 必須)"""

    SCRIPT = os.path.join(
        os.path.dirname(__file__), "..", "scripts", "youtube_markitdown.py"
    )

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="test_youtube_markitdown_")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    @unittest.skipUnless(
        os.environ.get("RUN_INTEGRATION_TESTS"),
        "Set RUN_INTEGRATION_TESTS=1 to run network-dependent tests",
    )
    def test_convert_youtube_url(self):
        """YouTube URLを変換しMarkdownファイルが生成される"""
        output_path = os.path.join(self.tmpdir, "test_output.md")
        result = subprocess.run(
            [sys.executable, self.SCRIPT,
             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
             "-o", output_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"stderr: {result.stderr}")
        self.assertTrue(os.path.isfile(output_path))

        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("YouTube", content)
        self.assertGreater(len(content), 100)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    @unittest.skipUnless(
        os.environ.get("RUN_INTEGRATION_TESTS"),
        "Set RUN_INTEGRATION_TESTS=1 to run network-dependent tests",
    )
    def test_output_contains_title(self):
        """変換結果に動画タイトルが含まれる"""
        output_path = os.path.join(self.tmpdir, "title_test.md")
        result = subprocess.run(
            [sys.executable, self.SCRIPT,
             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
             "-o", output_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)

        with open(output_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("Rick Astley", content)

    @unittest.skipUnless(_markitdown_available, "markitdown not installed")
    @unittest.skipUnless(
        os.environ.get("RUN_INTEGRATION_TESTS"),
        "Set RUN_INTEGRATION_TESTS=1 to run network-dependent tests",
    )
    def test_stderr_shows_status(self):
        """stderrにステータスメッセージが出力される"""
        output_path = os.path.join(self.tmpdir, "status_test.md")
        result = subprocess.run(
            [sys.executable, self.SCRIPT,
             "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
             "-o", output_path],
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("[input]", result.stderr)
        self.assertIn("[output]", result.stderr)
        self.assertIn("[done]", result.stderr)


if __name__ == "__main__":
    unittest.main()
