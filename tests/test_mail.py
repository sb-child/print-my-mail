from __future__ import annotations

from email.message import EmailMessage
from email.utils import format_datetime
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from print_my_mail.config import MailAccount
from print_my_mail.deadletter import DeadLetterStore
from print_my_mail.mail import (
    MailPoller,
    _imap_date,
    _parse_list_line,
    _parse_message,
    decode_imap_utf7,
    list_folders,
)


def _raw(
    subject: str = "subject",
    body: str = "body",
    date: str | None = None,
) -> bytes:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["Subject"] = subject
    if date:
        message["Date"] = date
    message.set_content(body)
    return message.as_bytes()


class FakeIMAP:
    def __init__(self, raw: bytes, *, fetch_status: str = "OK") -> None:
        self.raw = raw
        self.fetch_status = fetch_status
        self.events: list[tuple[str, object]] = []

    def __enter__(self) -> FakeIMAP:
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        self.events.append(("login", user))

    def select(self, folder: str) -> tuple[str, list[bytes]]:
        self.events.append(("select", folder))
        return "OK", [b"1"]

    def uid(self, command: str, *args: str) -> tuple[str, list[object]]:
        self.events.append((command, args))
        if command == "search":
            return "OK", [b"1"]
        if command == "fetch":
            if self.fetch_status != "OK":
                return self.fetch_status, []
            return "OK", [(b"x", self.raw), b")"]
        return "OK", [b""]


def test_parse_message() -> None:
    item = _parse_message("me@example.com", _raw())
    assert item.sender == "sender@example.com"
    assert item.subject == "subject"
    assert item.body == "body"
    assert item.html_body == ""
    assert item.date.utcoffset() == timedelta(hours=8)


def test_recipient_uses_to_header() -> None:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["To"] = "Alice <alice@example.com>"
    message["Subject"] = "subject"
    message.set_content("body")
    item = _parse_message("login@example.com", message.as_bytes())
    assert item.recipient == "Alice <alice@example.com>"


def test_date_is_converted_to_utc8() -> None:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["To"] = "alice@example.com"
    message["Subject"] = "subject"
    message["Date"] = "Mon, 02 Nov 2026 00:00:00 +0000"
    message.set_content("body")
    item = _parse_message("login@example.com", message.as_bytes())
    assert item.date.utcoffset() == timedelta(hours=8)
    assert item.date.hour == 8


def test_html_body_is_converted() -> None:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["Subject"] = "html"
    message.set_content(
        "<style>x{}</style><p>Hello <b>world</b></p><script>bad()</script>",
        subtype="html",
    )
    item = _parse_message("me@example.com", message.as_bytes())
    assert item.body == "Hello world"
    assert "<p>Hello <b>world</b></p>" in item.html_body
    assert "bad()" in item.html_body


def test_empty_plain_falls_back_to_html() -> None:
    message = EmailMessage()
    message["From"] = "sender@example.com"
    message["Subject"] = "html"
    message.set_content("   ")
    message.add_alternative("<p>HTML 正文</p>", subtype="html")
    item = _parse_message("me@example.com", message.as_bytes())
    assert item.body == "HTML 正文"


def test_mark_seen_is_explicit(tmp_path: Path) -> None:
    fake = FakeIMAP(_raw())
    dead_letter = DeadLetterStore(tmp_path / "dead.jsonl")
    poller = MailPoller(
        MailAccount("me@example.com", "pw", "imap.example.com"),
        timeout=12,
        dead_letter=dead_letter,
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "print_my_mail.mail.imaplib.IMAP4_SSL",
            lambda host, port, timeout: fake,
        )
        generator = poller.poll()
        item = next(generator)
        assert item.subject == "subject"
        assert not any(event[0] == "store" for event in fake.events)
        poller.mark_seen(item)
        with pytest.raises(StopIteration):
            next(generator)

    assert any(
        event == ("store", ("1", "+FLAGS", "\\Seen"))
        for event in fake.events
    )


def test_parse_list_line() -> None:
    folder = _parse_list_line(b'(\\HasNoChildren \\Spam) "/" "[Gmail]/Spam"')
    assert folder is not None
    assert folder.name == "[Gmail]/Spam"
    assert folder.display_name == "[Gmail]/Spam"
    assert "\\Spam" in folder.flags


def test_decode_imap_utf7() -> None:
    assert decode_imap_utf7("[Gmail]/&V4NXPpCuTvY-") == "[Gmail]/垃圾邮件"
    assert decode_imap_utf7("INBOX") == "INBOX"
    assert decode_imap_utf7("A&-B") == "A&B"
    assert decode_imap_utf7("&!bad-") == "&!bad-"


def test_imap_date_format() -> None:
    assert _imap_date(datetime(2026, 11, 2)) == "02-Nov-2026"


def test_list_folders(monkeypatch: pytest.MonkeyPatch) -> None:
    class ListIMAP(FakeIMAP):
        def list(self) -> tuple[str, list[bytes]]:
            return "OK", [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren \\Spam) "/" "[Gmail]/Spam"',
            ]

    monkeypatch.setattr("print_my_mail.mail.imaplib.IMAP4_SSL", lambda *a, **k: ListIMAP(_raw()))
    folders = list_folders(MailAccount("me@example.com", "pw", "imap.example.com"))
    assert [folder.name for folder in folders] == ["INBOX", "[Gmail]/Spam"]


def test_parse_failure_writes_dead_letter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeIMAP(_raw())
    dead_letter = DeadLetterStore(tmp_path / "dead.jsonl")
    poller = MailPoller(
        MailAccount("me@example.com", "pw", "imap.example.com"),
        dead_letter=dead_letter,
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise ValueError("bad mail")

    monkeypatch.setattr("print_my_mail.mail.imaplib.IMAP4_SSL", lambda *a, **k: fake)
    monkeypatch.setattr("print_my_mail.mail._parse_message", fail)

    assert list(poller.poll()) == []
    assert any(event[0] == "store" for event in fake.events)
    assert "bad mail" in (tmp_path / "dead.jsonl").read_text(encoding="utf-8")


def test_fetch_error_does_not_mark_seen(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeIMAP(_raw(), fetch_status="NO")
    poller = MailPoller(
        MailAccount("me@example.com", "pw", "imap.example.com"),
        dead_letter=DeadLetterStore(tmp_path / "dead.jsonl"),
    )
    monkeypatch.setattr("print_my_mail.mail.imaplib.IMAP4_SSL", lambda *a, **k: fake)

    with pytest.raises(Exception, match="失败"):
        list(poller.poll())
    assert not any(event[0] == "store" for event in fake.events)


def test_recent_mail_is_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    date = format_datetime(
        datetime.now(timezone.utc) - timedelta(seconds=10)
    )
    fake = FakeIMAP(_raw(date=date))
    poller = MailPoller(
        MailAccount("me@example.com", "pw", "imap.example.com"),
        max_age=60,
        dead_letter=DeadLetterStore(tmp_path / "dead.jsonl"),
    )
    monkeypatch.setattr("print_my_mail.mail.imaplib.IMAP4_SSL", lambda *a, **k: fake)

    assert [item.subject for item in poller.poll()] == ["subject"]


def test_old_mail_is_skipped_without_marking_seen(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    date = format_datetime(
        datetime.now(timezone.utc) - timedelta(minutes=2)
    )
    fake = FakeIMAP(_raw(date=date))
    poller = MailPoller(
        MailAccount("me@example.com", "pw", "imap.example.com"),
        max_age=60,
        dead_letter=DeadLetterStore(tmp_path / "dead.jsonl"),
    )
    monkeypatch.setattr("print_my_mail.mail.imaplib.IMAP4_SSL", lambda *a, **k: fake)

    assert list(poller.poll()) == []
    assert not any(event[0] == "store" for event in fake.events)

    fetches = sum(1 for event in fake.events if event[0] == "fetch")
    assert list(poller.poll()) == []
    assert sum(1 for event in fake.events if event[0] == "fetch") == fetches
