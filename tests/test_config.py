from __future__ import annotations

from pathlib import Path

import pytest

from print_my_mail import GitHubAccount, MailAccount, load_config


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
print_timeout = 30
print_max_retries = 2
print_retry_base_delay = 1
print_retry_max_delay = 10
imap_timeout = 15
dead_letter_file = "dead.jsonl"
github_fetch_content = false

[[mail]]
user = "a@example.com"
pass = "secret"
folders = ["INBOX", "[Gmail]/Spam"]

[[github]]
pat = "token"
""",
    )

    config = load_config(path)

    assert config.target == "127.0.0.1:1234"
    assert config.poll_interval == 10
    assert config.print_timeout == 30
    assert config.print_max_retries == 2
    assert config.print_retry_base_delay == 1
    assert config.print_retry_max_delay == 10
    assert config.imap_timeout == 15
    assert config.dead_letter_file == "dead.jsonl"
    assert config.github_fetch_content is False
    assert config.mail[0].user == "a@example.com"
    assert config.mail[0].folders == ("INBOX", "[Gmail]/Spam")
    assert config.github[0].pat == "token"


def test_defaults(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path / "empty.toml", ""))
    assert config.target == "127.0.0.1:54321"
    assert config.poll_interval == 60
    assert config.print_timeout == 120
    assert config.imap_timeout == 30
    assert config.mail == ()
    assert config.github == ()


@pytest.mark.parametrize(
    "text",
    [
        "[server]\npoll_interval = 0\n",
        "[server]\npoll_interval = nan\n",
        "[server]\nprint_timeout = -1\n",
        "[server]\nprint_max_retries = -1\n",
        "[server]\nprint_retry_base_delay = 0\n",
        "[server]\nprint_retry_base_delay = 10\nprint_retry_max_delay = 5\n",
        '[server]\ngithub_fetch_content = "yes"\n',
        "[server]\nimap_timeout = inf\n",
        '[server]\ntarget = ""\n',
        '[server]\ndead_letter_file = ""\n',
        '[[mail]]\nuser = "a@example.com"\npass = "x"\nport = 0\n',
        '[[mail]]\nuser = "a@example.com"\npass = "x"\nhost = ""\n',
        '[[mail]]\nuser = "a@example.com"\npass = "x"\nfolders = []\n',
        '[[mail]]\nuser = "a@example.com"\npass = "x"\nfolders = [""]\n',
        '[[github]]\npat = ""\n',
    ],
)
def test_invalid_values(tmp_path: Path, text: str) -> None:
    with pytest.raises(ValueError):
        load_config(_write(tmp_path / "bad.toml", text))


def test_secrets_are_not_in_repr() -> None:
    account = MailAccount("u", "mail-secret", "imap.example.com")
    github = GitHubAccount("gh-secret")
    assert "mail-secret" not in repr(account)
    assert "gh-secret" not in repr(github)
