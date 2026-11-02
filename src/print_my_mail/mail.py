"""通过 IMAP 轮询未读邮件。"""

from __future__ import annotations

import email
import email.policy
import imaplib
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime
from email.header import decode_header, make_header
from email.message import EmailMessage
from email.utils import parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from typing import cast

from .config import MailAccount

log = logging.getLogger(__name__)

_HTML_BLOCK_TAGS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"})


class MailError(RuntimeError):
    """连接或读取邮箱失败。"""


@dataclass(frozen=True)
class MailItem:
    """一封准备打印的邮件。"""

    recipient: str
    sender: str
    sender_name: str
    subject: str
    date: datetime
    body: str


class MailPoller:
    """轮询一个 IMAP 邮箱的未读邮件。"""

    def __init__(self, account: MailAccount) -> None:
        self.account = account

    def poll(self) -> Iterator[MailItem]:
        """
        产出未读邮件。

        调用方打印成功后继续迭代，才会把这封邮件标记为已读；
        如果中途抛异常，未打印的邮件会保持未读状态。
        """
        try:
            with imaplib.IMAP4_SSL(
                self.account.host, self.account.port
            ) as imap:
                imap.login(self.account.user, self.account.password)
                imap.select("INBOX")
                status, data = imap.uid("search", "UNSEEN")
                if status != "OK" or not data:
                    raise MailError(
                        f"搜索邮箱 {self.account.user} 的未读邮件失败: {status}"
                    )

                for raw_uid in data[0].split():
                    uid = raw_uid.decode()
                    item = self._fetch(imap, uid)
                    if item is None:
                        # 无法解析的邮件也要标记，避免每一轮都重复失败。
                        self._mark_seen(imap, uid)
                        continue
                    yield item
                    self._mark_seen(imap, uid)
        except MailError:
            raise
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"读取邮箱 {self.account.user} 失败: {exc}") from exc

    def _fetch(self, imap: imaplib.IMAP4_SSL, uid: str) -> MailItem | None:
        try:
            status, data = imap.uid("fetch", uid, "(RFC822)")
            if status != "OK":
                log.warning("读取邮件 UID %s 失败: %s", uid, status)
                return None
            for part in data:
                if isinstance(part, tuple):
                    return _parse_message(self.account.user, part[1])
        except Exception as exc:
            # 单封邮件解析失败不应影响这台邮箱的后续邮件。
            log.warning("解析邮件 UID %s 失败: %s", uid, exc)
        return None

    def _mark_seen(self, imap: imaplib.IMAP4_SSL, uid: str) -> None:
        try:
            imap.uid("store", uid, "+FLAGS", "\\Seen")
        except (imaplib.IMAP4.error, OSError) as exc:
            log.warning("标记邮件 UID %s 为已读失败: %s", uid, exc)


def _parse_message(recipient: str, raw: bytes) -> MailItem:
    message = cast(
        EmailMessage,
        email.message_from_bytes(raw, policy=email.policy.default),
    )
    sender_name, sender = parseaddr(_decode_header(message.get("From")))
    return MailItem(
        recipient=recipient,
        sender=sender or sender_name or "unknown",
        sender_name=sender_name or sender or "未知发件人",
        subject=_decode_header(message.get("Subject")) or "(无主题)",
        date=_parse_date(message.get("Date")),
        body=_message_body(message),
    )


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except (UnicodeDecodeError, LookupError):
        return value


def _parse_date(value: str | None) -> datetime:
    if value:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError):
            pass
    return datetime.now()


def _message_body(message: EmailMessage) -> str:
    part = message.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""

    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        payload = part.get_payload(decode=True)
        content = (
            payload.decode("utf-8", errors="replace")
            if isinstance(payload, bytes)
            else ""
        )

    if not isinstance(content, str):
        return ""

    if part.get_content_subtype() == "html":
        content = _html_to_text(content)
    return content.strip()


def _html_to_text(html: str) -> str:
    parser = _TextExtractor()
    parser.feed(html)
    text = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", text)


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
        elif tag in _HTML_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip_depth:
            self._skip_depth -= 1
        elif tag in _HTML_BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)
