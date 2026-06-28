import os
import unittest
from unittest.mock import patch

from app.mcp_server import parse_task_request


class ParseTaskRequestTests(unittest.TestCase):
    def test_returns_explicit_schedule_when_provided(self):
        result = parse_task_request("Summarize tech news", scheduled_at="2026-06-29T09:00:00")
        self.assertEqual(result["description"], "Summarize tech news")
        self.assertEqual(result["scheduled_at"], "2026-06-29T09:00:00")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
    @patch("app.mcp_server._call_openai_for_task_parsing")
    def test_uses_llm_when_scheduled_at_missing(self, mock_llm):
        mock_llm.return_value = {
            "description": "Review PR #123",
            "scheduled_at": "2026-06-29T09:00:00",
        }

        result = parse_task_request("Review PR #123 tomorrow at 9am")

        self.assertEqual(result["description"], "Review PR #123")
        self.assertEqual(result["scheduled_at"], "2026-06-29T09:00:00")

    @patch.dict(os.environ, {}, clear=True)
    def test_falls_back_to_raw_request_without_llm(self):
        result = parse_task_request("Review PR #123 tomorrow at 9am")

        self.assertEqual(result["description"], "Review PR #123 tomorrow at 9am")
        self.assertIsNone(result["scheduled_at"])


if __name__ == "__main__":
    unittest.main()
