from __future__ import annotations

import json
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any

from print_my_mail import (
    AIConfig,
    Config,
    GitHubDigest,
    GitHubItem,
    GitHubNotification,
    MailItem,
)
from print_my_mail.ai import (
    OpenAIPostProcessor,
    _build_prompt,
    _chat_endpoint,
    _extract_content,
    _limit_answer,
    _truncate,
)
from print_my_mail.runner import Runner


def _config(**overrides: Any) -> AIConfig:
    values = {
        "enabled": True,
        "base_url": "https://api.example.com/v1",
        "token": "sk-test",
        "model": "gpt-test",
        "timeout": 12,
        "max_chars": 100,
        "max_input_chars": 4000,
        "question": "需要我处理吗？",
    }
    values.update(overrides)
    return AIConfig(**values)


def _mail(body: str = "原始正文") -> MailItem:
    return MailItem(
        "me@example.com",
        "sender@example.com",
        "发件人",
        "主题",
        datetime(2026, 11, 2, 12, 0),
        body,
    )


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def read(self, size: int = -1) -> bytes:
        return self.body


def _reply(text: str) -> bytes:
    return json.dumps(
        {"choices": [{"message": {"content": text}}]},
        ensure_ascii=False,
    ).encode("utf-8")


def test_chat_endpoint_normalization() -> None:
    assert (
        _chat_endpoint("https://api.example.com/v1")
        == "https://api.example.com/v1/chat/completions"
    )
    assert (
        _chat_endpoint("https://api.example.com/v1/chat/completions/")
        == "https://api.example.com/v1/chat/completions"
    )


def test_build_prompt_contains_question_and_clips_body() -> None:
    prompt = _build_prompt(
        _config(max_chars=80, max_input_chars=10),
        kind="邮件",
        title="标题",
        meta="发件人：a@example.com",
        body="0123456789ABCDEF",
    )
    assert "以下是邮件的内容。" in prompt
    assert "需要我处理吗？" in prompt
    assert "不超过 80 个字符" in prompt
    assert "0123456789" in prompt
    assert "ABCDEF" not in prompt
    assert "内容已截断" in prompt


def test_limit_answer_is_single_paragraph_and_bounded() -> None:
    assert _limit_answer("  a\n b  ", 100) == "a b"
    assert _limit_answer("字" * 120, 100) == "字" * 100
    assert _limit_answer("abcdef", 1) == "a"
    assert _truncate("  甲乙\n丙丁  ", 3) == "甲乙\n"


def test_extract_content_supports_text_parts() -> None:
    assert (
        _extract_content(
            {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "a"},
                                {"text": "b"},
                            ]
                        }
                    }
                ]
            }
        )
        == "ab"
    )


def test_mail_replaces_body_with_summary(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def fake_urlopen(request: Any, **kwargs: Any) -> FakeResponse:
        captured["request"] = request
        captured["timeout"] = kwargs.get("timeout")
        return FakeResponse(_reply(" 摘要内容 "))

    monkeypatch.setattr(
        "print_my_mail.ai.urllib.request.urlopen",
        fake_urlopen,
    )

    config = _config()
    result = OpenAIPostProcessor(config).mail(_mail())

    assert result.body == "摘要内容"
    assert result.html_body == ""
    request = captured["request"]
    assert request.full_url == "https://api.example.com/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer sk-test"
    assert captured["timeout"] == 12
    payload = json.loads(request.data)
    assert payload["model"] == "gpt-test"
    assert "原始正文" in payload["messages"][1]["content"]
    assert "需要我处理吗？" in payload["messages"][1]["content"]


def test_github_sets_content_per_item(monkeypatch: Any) -> None:
    replies = [_reply("摘要 A"), _reply("摘要 B")]

    def fake_urlopen(request: Any, **kwargs: Any) -> FakeResponse:
        return FakeResponse(replies.pop(0))

    monkeypatch.setattr(
        "print_my_mail.ai.urllib.request.urlopen",
        fake_urlopen,
    )

    digest = GitHubDigest(
        (
            GitHubItem(
                GitHubNotification("owner/repo", 1, "标题 A", "issue"),
                body="正文 A",
            ),
            GitHubItem(
                GitHubNotification("owner/repo", 2, "标题 B", "pr"),
                latest_comment_body="评论 B",
            ),
        )
    )
    result = OpenAIPostProcessor(_config()).github(digest)

    assert [item.content for item in result.items] == ["摘要 A", "摘要 B"]
    assert result.items[0].body == "正文 A"
    assert result.items[1].latest_comment_body == "评论 B"


def test_api_error_falls_back_to_truncated_body(monkeypatch: Any) -> None:
    def fake_urlopen(request: Any, **kwargs: Any) -> FakeResponse:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(
        "print_my_mail.ai.urllib.request.urlopen",
        fake_urlopen,
    )

    processor = OpenAIPostProcessor(_config())
    long_body = "甲" * 200
    item = _mail(long_body)
    result = processor.mail(item)
    assert len(result.body) == 100
    assert result.body == "甲" * 100

    digest = GitHubDigest(
        (
            GitHubItem(
                GitHubNotification("owner/repo", 1, "标题", "issue"),
                body=long_body,
            ),
        )
    )
    result = processor.github(digest)
    assert result.items[0].content == "甲" * 100


def test_fallback_respects_configured_max_chars(monkeypatch: Any) -> None:
    def fake_urlopen(request: Any, **kwargs: Any) -> FakeResponse:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(
        "print_my_mail.ai.urllib.request.urlopen",
        fake_urlopen,
    )

    processor = OpenAIPostProcessor(_config(max_chars=10))
    result = processor.mail(_mail("甲" * 200))
    assert result.body == "甲" * 10


def test_empty_answer_falls_back(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "print_my_mail.ai.urllib.request.urlopen",
        lambda request, **kwargs: FakeResponse(_reply("   ")),
    )
    item = _mail()
    assert OpenAIPostProcessor(_config()).mail(item) == item


def test_runner_uses_ai_processor_when_enabled(tmp_path: Path) -> None:
    enabled = Config(
        dead_letter_file=str(tmp_path / "dead.jsonl"),
        ai=_config(),
    )
    disabled = Config(dead_letter_file=str(tmp_path / "dead.jsonl"))
    assert isinstance(Runner(enabled).processor, OpenAIPostProcessor)
    assert not isinstance(Runner(disabled).processor, OpenAIPostProcessor)
