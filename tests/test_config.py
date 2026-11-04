from __future__ import annotations

from pathlib import Path

import pytest

from print_my_mail import (
    AIConfig,
    GitHubAccount,
    MailAccount,
    load_config,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_load_config(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "config.toml",
        """
[server]
target = "127.0.0.1:1234"
poll_interval = 10
github_poll_interval = 90
print_timeout = 30
print_max_retries = 2
print_retry_base_delay = 1
print_retry_max_delay = 10
imap_timeout = 15
mail_max_age = 30
dead_letter_file = "dead.jsonl"
github_fetch_content = false

[[mail]]
user = "a@example.com"
pass = "secret"
folders = ["INBOX", "[Gmail]/Spam"]

[[github]]
pat = "token"

[ai]
enabled = true
base_url = "https://api.example.com/v1"
token = "sk-test"
model = "gpt-test"
timeout = 12
max_chars = 80
max_input_chars = 2000
question = "需要我处理吗？"
output_mode = "json"
max_retries = 4
""",
    )

    config = load_config(path)

    assert config.target == "127.0.0.1:1234"
    assert config.poll_interval == 10
    assert config.github_poll_interval == 90
    assert config.print_timeout == 30
    assert config.print_max_retries == 2
    assert config.print_retry_base_delay == 1
    assert config.print_retry_max_delay == 10
    assert config.imap_timeout == 15
    assert config.mail_max_age == 30
    assert config.dead_letter_file == "dead.jsonl"
    assert config.github_fetch_content is False
    assert config.mail[0].user == "a@example.com"
    assert config.mail[0].folders == ("INBOX", "[Gmail]/Spam")
    assert config.github[0].pat == "token"
    assert config.ai.enabled is True
    assert config.ai.base_url == "https://api.example.com/v1"
    assert config.ai.token == "sk-test"
    assert config.ai.model == "gpt-test"
    assert config.ai.timeout == 12
    assert config.ai.max_chars == 80
    assert config.ai.max_input_chars == 2000
    assert config.ai.question == "需要我处理吗？"
    assert config.ai.output_mode == "json"
    assert config.ai.max_retries == 4


def test_defaults(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path / "empty.toml", ""))
    assert config.target == "127.0.0.1:54321"
    assert config.poll_interval == 60
    assert config.github_poll_interval == 60
    assert config.print_timeout == 120
    assert config.imap_timeout == 30
    assert config.mail_max_age == 60
    assert config.mail == ()
    assert config.github == ()
    assert config.ai.enabled is False
    assert config.ai.max_chars == 100
    assert config.ai.max_input_chars == 4000
    assert config.ai.output_mode == "tool"
    assert config.ai.max_retries == 2


@pytest.mark.parametrize(
    "text",
    [
        "[server]\npoll_interval = 0\n",
        "[server]\npoll_interval = nan\n",
        "[server]\ngithub_poll_interval = 0\n",
        "[server]\ngithub_poll_interval = nan\n",
        "[server]\nprint_timeout = -1\n",
        "[server]\nprint_max_retries = -1\n",
        "[server]\nprint_retry_base_delay = 0\n",
        "[server]\nprint_retry_base_delay = 10\nprint_retry_max_delay = 5\n",
        '[server]\ngithub_fetch_content = "yes"\n',
        "[server]\nimap_timeout = inf\n",
        "[server]\nmail_max_age = 0\n",
        "[server]\nmail_max_age = nan\n",
        '[server]\ntarget = ""\n',
        '[server]\ndead_letter_file = ""\n',
        '[[mail]]\nuser = "a@example.com"\npass = "x"\nport = 0\n',
        '[[mail]]\nuser = "a@example.com"\npass = "x"\nhost = ""\n',
        '[[mail]]\nuser = "a@example.com"\npass = "x"\nfolders = []\n',
        '[[mail]]\nuser = "a@example.com"\npass = "x"\nfolders = [""]\n',
        '[[github]]\npat = ""\n',
        "[ai]\nenabled = true\n",
        '[ai]\nenabled = true\nbase_url = "https://x"\nmodel = ""\n',
        '[ai]\nenabled = true\nbase_url = "https://x"\nquestion = ""\n',
        '[ai]\nenabled = true\nbase_url = "https://x"\nmax_chars = 0\n',
        '[ai]\nenabled = true\nbase_url = "https://x"\nmax_input_chars = -1\n',
        '[ai]\nenabled = true\nbase_url = "https://x"\nmax_retries = -1\n',
        '[ai]\nenabled = true\nbase_url = "https://x"\noutput_mode = "xml"\n',
        '[ai]\nenabled = true\nbase_url = "https://x"\ntimeout = 0\n',
        '[ai]\nenabled = "yes"\n',
    ],
)
def test_invalid_values(tmp_path: Path, text: str) -> None:
    with pytest.raises(ValueError):
        load_config(_write(tmp_path / "bad.toml", text))


def test_secrets_are_not_in_repr() -> None:
    account = MailAccount("u", "mail-secret", "imap.example.com")
    github = GitHubAccount("gh-secret")
    ai = AIConfig(
        enabled=True,
        base_url="https://api.example.com/v1",
        token="sk-secret",
    )
    assert "mail-secret" not in repr(account)
    assert "gh-secret" not in repr(github)
    assert "sk-secret" not in repr(ai)
