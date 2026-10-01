#!/usr/bin/env python3
"""Unit tests: Telegram delivery — token resolution, API call error handling, send contract."""
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import notify as ntf


class FakeResponse:
    def __init__(self, body=b"{}"):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.body

    def decode(self):
        return self.body.decode()


def _http_error(code, body: bytes):
    from email.message import Message
    return urllib.error.HTTPError("https://api.telegram.org/x", code, "err",
                                  Message(), io.BytesIO(body))


class TestBotToken(unittest.TestCase):
    def tearDown(self):
        mock.patch.dict("os.environ", {}, clear=False).stop()

    def test_env_var_wins(self):
        with mock.patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "envtok"}, clear=False):
            self.assertEqual(ntf.bot_token(), "envtok")

    def test_reads_from_env_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            envp = Path(tmp) / ".env"
            envp.write_text('# comment\nTELEGRAM_BOT_TOKEN="filetok"\nOTHER=1\n')
            self.assertEqual(ntf.bot_token(envp), "filetok")

    def test_missing_file_raises(self):
        with self.assertRaises(ntf.NotifyError):
            ntf.bot_token(Path("/nonexistent/.env"))

    def test_missing_token_in_file_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            envp = Path(tmp) / ".env"
            envp.write_text("OTHER=1\n")
            with self.assertRaises(ntf.NotifyError):
                ntf.bot_token(envp)


class TestApiCall(unittest.TestCase):
    @mock.patch("urllib.request.urlopen")
    def test_ok_response_parsed(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({"ok": True, "result": {"id": 1}}).encode())
        r = ntf.api_call("tok", "getChat", chat_id="x")
        self.assertTrue(r["ok"])

    @mock.patch("urllib.request.urlopen")
    def test_http_error_with_json_body_parsed(self, urlopen):
        urlopen.side_effect = _http_error(400, json.dumps({"ok": False, "description": "bad"}).encode())
        r = ntf.api_call("tok", "sendMessage")
        self.assertEqual(r, {"ok": False, "description": "bad"})

    @mock.patch("urllib.request.urlopen")
    def test_http_error_with_non_json_body_degrades(self, urlopen):
        urlopen.side_effect = _http_error(502, b"<html>gateway</html>")
        r = ntf.api_call("tok", "sendMessage")
        self.assertFalse(r["ok"])
        self.assertIn("HTTP 502", r["error"])


class TestSendMessage(unittest.TestCase):
    @mock.patch("urllib.request.urlopen")
    def test_success_returns_payload(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({"ok": True, "result": {"message_id": 7}}).encode())
        r = ntf.send_message("tok", 42, "hello")
        self.assertEqual(r["result"]["message_id"], 7)

    @mock.patch("urllib.request.urlopen")
    def test_failure_raises(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({"ok": False, "description": "chat not found"}).encode())
        with self.assertRaises(ntf.NotifyError):
            ntf.send_message("tok", 42, "hello")

    @mock.patch("urllib.request.urlopen")
    def test_resolve_chat_success(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({"ok": True, "result": {"id": 12345}}).encode())
        self.assertEqual(ntf.resolve_chat("tok", "@dropwatchdog"), 12345)

    @mock.patch("urllib.request.urlopen")
    def test_resolve_chat_failure_raises(self, urlopen):
        urlopen.return_value = FakeResponse(json.dumps({"ok": False, "description": "not found"}).encode())
        with self.assertRaises(ntf.NotifyError):
            ntf.resolve_chat("tok", "@missing")


if __name__ == "__main__":
    unittest.main()