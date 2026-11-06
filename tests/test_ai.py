from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openai import OpenAIError as SDKOpenAIError

from print_my_mail import (
    AIConfig,
    Config,
    GitHubComment,
    GitHubDigest,
    GitHubItem,
    GitHubNotification,
    MailItem,
)
from print_my_mail.ai import (
    SUMMARY_TOOL_NAME,
    OpenAIError,
    OpenAIPostProcessor,
    _base_url,
    _build_prompt,
    _extract_summary,
    _extract_tool_summary,
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


def _content(summary: str) -> str:
    return json.dumps({"summary": summary}, ensure_ascii=False)


def _tool_calls(arguments: str) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            function=SimpleNamespace(
                name=SUMMARY_TOOL_NAME,
                arguments=arguments,
            )
        )
    ]


def _tool_reply(summary: str) -> list[SimpleNamespace]:
    return _tool_calls(_content(summary))


class FakeCompletions:
    def __init__(
        self,
        replies: list[str | None | list[SimpleNamespace] | Exception],
    ) -> None:
        self._replies = iter(replies)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        reply = next(self._replies)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, list):
            message = SimpleNamespace(content="", tool_calls=reply)
        else:
            message = SimpleNamespace(content=reply, tool_calls=None)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
        )


class FakeClient:
    def __init__(self, completions: FakeCompletions) -> None:
        self.chat = SimpleNamespace(completions=completions)


def _setup(
    monkeypatch: Any,
    replies: list[str | None | list[SimpleNamespace] | Exception],
    **overrides: Any,
) -> tuple[OpenAIPostProcessor, FakeCompletions, dict[str, Any]]:
    completions = FakeCompletions(replies)
    created: dict[str, Any] = {}

    def fake_openai(**kwargs: Any) -> FakeClient:
        created.update(kwargs)
        return FakeClient(completions)

    monkeypatch.setattr("print_my_mail.ai.OpenAI", fake_openai)
    # 重试时不要真的等待。
    monkeypatch.setattr("print_my_mail.ai.RETRY_BASE_DELAY", 0.0)
    return OpenAIPostProcessor(_config(**overrides)), completions, created


def test_base_url_normalization() -> None:
    assert _base_url("https://api.example.com/v1") == (
        "https://api.example.com/v1"
    )
    assert _base_url("https://api.example.com/v1/chat/completions/") == (
        "https://api.example.com/v1"
    )
    assert _base_url("https://api.deepseek.com") == (
        "https://api.deepseek.com"
    )


def test_build_prompt_is_json_and_clips_body() -> None:
    prompt = _build_prompt(
        _config(max_chars=80, max_input_chars=10),
        kind="邮件",
        title="标题",
        meta={"from": "a@example.com"},
        body="0123456789ABCDEF",
    )
    payload = json.loads(prompt)

    assert payload["kind"] == "邮件"
    assert payload["title"] == "标题"
    assert payload["meta"] == {"from": "a@example.com"}
    assert payload["question"] == "需要我处理吗？"
    assert payload["requirements"]["max_summary_chars"] == 80
    assert payload["content"].startswith("0123456789")
    assert "ABCDEF" not in payload["content"]
    assert "内容已截断" in payload["content"]


def test_build_prompt_keeps_github_content_fields() -> None:
    prompt = _build_prompt(
        _config(),
        kind="GitHub 通知",
        title="owner/repo#1 标题",
        meta={"type": "issue"},
        body={"body": "正文", "latest_comment": "评论"},
    )
    payload = json.loads(prompt)

    assert payload["content"] == {
        "body": "正文",
        "latest_comment": "评论",
    }


def test_limit_answer_is_single_paragraph_and_bounded() -> None:
    assert _limit_answer("  a\n b  ", 100) == "a b"
    assert _limit_answer("字" * 120, 100) == "字" * 100
    assert _limit_answer("abcdef", 1) == "a"
    assert _truncate("  甲乙\n丙丁  ", 3) == "甲乙\n"


def test_extract_summary_parses_json_object() -> None:
    assert _extract_summary('{"summary": " 摘要内容 "}') == "摘要内容"


def test_extract_summary_rejects_empty_and_invalid() -> None:
    for content in (None, "", "   ", "纯文本", "{}", '{"text": "x"}'):
        with pytest.raises(OpenAIError):
            _extract_summary(content)


def test_extract_tool_summary() -> None:
    message = SimpleNamespace(tool_calls=_tool_reply("工具摘要"))
    assert _extract_tool_summary(message) == "工具摘要"

    for tool_calls in (
        None,
        [],
        _tool_calls("不是 JSON"),
        _tool_calls("{}"),
        _tool_calls(_content("")),
    ):
        with pytest.raises(OpenAIError):
            _extract_tool_summary(
                SimpleNamespace(tool_calls=tool_calls)
            )


def test_mail_uses_tool_by_default(monkeypatch: Any) -> None:
    processor, completions, created = _setup(
        monkeypatch,
        [_tool_reply(" 摘要内容 ")],
    )

    result = processor.mail(_mail())

    assert result.body == "摘要内容"
    assert result.html_body == ""
    assert created["base_url"] == "https://api.example.com/v1"
    assert created["api_key"] == "sk-test"
    assert created["timeout"] == 12
    assert created["max_retries"] == 2

    payload = completions.calls[0]
    assert payload["model"] == "gpt-test"
    assert payload["tool_choice"] == "auto"
    assert payload["tools"][0]["function"]["name"] == SUMMARY_TOOL_NAME
    assert "response_format" not in payload
    assert "submit_summary" in payload["messages"][0]["content"]
    prompt = json.loads(payload["messages"][1]["content"])
    assert prompt["content"] == "原始正文"
    assert prompt["question"] == "需要我处理吗？"
    assert prompt["meta"]["from"] == "发件人 <sender@example.com>"


def test_json_output_mode(monkeypatch: Any) -> None:
    processor, completions, _ = _setup(
        monkeypatch,
        [_content("摘要")],
        output_mode="json",
    )

    result = processor.mail(_mail())

    assert result.body == "摘要"
    payload = completions.calls[0]
    assert payload["response_format"] == {"type": "json_object"}
    assert "tools" not in payload
    assert "tool_choice" not in payload


def test_prompt_output_mode(monkeypatch: Any) -> None:
    processor, completions, _ = _setup(
        monkeypatch,
        [_content("摘要")],
        output_mode="prompt",
    )

    assert processor.mail(_mail()).body == "摘要"
    payload = completions.calls[0]
    assert "response_format" not in payload
    assert "tools" not in payload


def test_missing_tool_call_is_retried(monkeypatch: Any) -> None:
    processor, completions, _ = _setup(
        monkeypatch,
        [[], _tool_reply("重试成功")],
        max_retries=1,
    )

    result = processor.mail(_mail())

    assert result.body == "重试成功"
    assert len(completions.calls) == 2


def test_invalid_tool_arguments_are_retried(monkeypatch: Any) -> None:
    processor, completions, _ = _setup(
        monkeypatch,
        [_tool_calls("不是 JSON"), _tool_reply("重试成功")],
        max_retries=1,
    )

    assert processor.mail(_mail()).body == "重试成功"
    assert len(completions.calls) == 2


def test_json_empty_answer_is_retried(monkeypatch: Any) -> None:
    processor, completions, _ = _setup(
        monkeypatch,
        ["   ", _content("重试成功")],
        output_mode="json",
        max_retries=1,
    )

    assert processor.mail(_mail()).body == "重试成功"
    assert len(completions.calls) == 2


def test_retry_exhausted_falls_back(monkeypatch: Any) -> None:
    processor, completions, _ = _setup(
        monkeypatch,
        [[], []],
        max_retries=1,
    )

    result = processor.mail(_mail("原始正文"))

    assert result.body == "原始正文"
    assert len(completions.calls) == 2


def test_github_sets_content_per_item(monkeypatch: Any) -> None:
    processor, completions, _ = _setup(
        monkeypatch,
        [_tool_reply("摘要 A"), _tool_reply("摘要 B")],
    )

    digest = GitHubDigest(
        (
            GitHubItem(
                GitHubNotification("owner/repo", 1, "标题 A", "issue"),
                body="正文 A",
            ),
            GitHubItem(
                GitHubNotification("owner/repo", 2, "标题 B", "pr"),
                body="正文 B",
                latest_comment_body="评论 B",
                comments=(
                    GitHubComment("alice", "第一条评论"),
                    GitHubComment("bob", "评论 B"),
                ),
            ),
        )
    )
    result = processor.github(digest)

    assert [item.content for item in result.items] == ["摘要 A", "摘要 B"]
    assert result.items[0].body == "正文 A"
    assert result.items[1].latest_comment_body == "评论 B"
    assert len(completions.calls) == 2

    prompt = json.loads(completions.calls[1]["messages"][1]["content"])
    assert prompt["content"]["body"] == "正文 B"
    assert prompt["content"]["comments"] == [
        {"author": "alice", "body": "第一条评论"},
        {"author": "bob", "body": "评论 B"},
    ]
    assert "latest_comment" not in prompt["content"]


def test_api_error_falls_back_to_truncated_body(monkeypatch: Any) -> None:
    processor, completions, _ = _setup(
        monkeypatch,
        [SDKOpenAIError("boom"), SDKOpenAIError("boom")],
    )

    long_body = "甲" * 200
    item = _mail(long_body)
    result = processor.mail(item)
    assert len(result.body) == 100
    assert result.body == "甲" * 100
    # SDK 自身负责网络/限流重试；这里不额外重试业务错误。
    assert len(completions.calls) == 1

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
    processor, _, _ = _setup(
        monkeypatch,
        [SDKOpenAIError("boom")],
        max_chars=10,
    )

    result = processor.mail(_mail("甲" * 200))
    assert result.body == "甲" * 10


def test_runner_uses_ai_processor_when_enabled(tmp_path: Path) -> None:
    enabled = Config(
        dead_letter_file=str(tmp_path / "dead.jsonl"),
        ai=_config(),
    )
    disabled = Config(dead_letter_file=str(tmp_path / "dead.jsonl"))
    assert isinstance(Runner(enabled).processor, OpenAIPostProcessor)
    assert not isinstance(Runner(disabled).processor, OpenAIPostProcessor)
