"""通过 IMAP 轮询未读邮件。

约定：
- 收件人使用邮件的 `To` 头，缺失时回退到登录账号；
- 所有时间统一转换成 UTC+8；
- `MailItem.body` 是用于打印的纯文本；
- `MailItem.html_body` 保留原始 HTML，供 `PostProcessor` 使用；
- 附件和图片不解析；
- 头字段和正文都从原始字节重新解码，兼容 gb2312 扩展（GBK）、
  charset 标错、无 charset 等常见情况；
- 显示名与地址相同时只保留地址，例如 `"a@b.com" <a@b.com>` → `a@b.com`；
- `folders` 可以配置 INBOX、Spam/Junk 等多个文件夹；
- 启动时只处理 `max_age` 秒以内到达的邮件，更早的未读邮件跳过，
  不打印也不修改已读状态。

用法::

    from print_my_mail import MailAccount
    from print_my_mail.mail import MailPoller, list_folders

    account = MailAccount("me@example.com", "password", "imap.example.com")

    for folder in list_folders(account):
        print(folder.name, folder.flags)

    poller = MailPoller(account)
    for item in poller.poll():
        ...  # 打印成功后调用 poller.mark_seen(item)

`poll()` 只有在调用方恢复迭代时才继续处理下一封邮件；打印失败或
中途退出时，当前邮件会保持未读。
"""

from __future__ import annotations

import base64
import binascii
import codecs
import email
import email.policy
import imaplib
import logging
import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.header import decode_header
from email.message import EmailMessage, MIMEPart
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser

from typing import cast

from .config import MailAccount
from .deadletter import DeadLetterStore
from .timeutil import now, to_utc8

log = logging.getLogger(__name__)

DEFAULT_IMAP_TIMEOUT = 30.0
DEFAULT_MAIL_MAX_AGE = 60.0

_IMAP_MONTHS = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

_CHARSET_ALIASES = {
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "x-gbk": "gb18030",
    "cp936": "gb18030",
    "ms936": "gb18030",
    "euc-cn": "gb18030",
    "utf8": "utf-8",
    "latin1": "latin-1",
}

_UNKNOWN_CHARSETS = frozenset(
    {
        "unknown-8bit",
        "unknown",
        "x-unknown",
        "8bit",
        "binary",
    }
)

_SINGLE_ADDRESS_RE = re.compile(r'"?([^"<>]*?)"?\s*<([^<>]+)>')

_HTML_BLOCK_TAGS = frozenset({"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4"})
_LIST_RE = re.compile(
    rb'^\((?P<flags>[^)]*)\)\s+(?P<delim>NIL|"(?:[^"\\]|\\.)*")\s+(?P<name>.*)$'
)


@dataclass(frozen=True)
class MailFolder:
    """一个 IMAP 文件夹。"""

    name: str
    flags: tuple[str, ...] = ()

    @property
    def display_name(self) -> str:
        """解码 IMAP modified UTF-7，仅用于展示。"""
        return decode_imap_utf7(self.name)


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
    html_body: str = ""
    folder: str = ""
    uid: str = ""


class MailPoller:
    """轮询一个 IMAP 邮箱的未读邮件。"""

    def __init__(
        self,
        account: MailAccount,
        *,
        timeout: float = DEFAULT_IMAP_TIMEOUT,
        max_age: float = DEFAULT_MAIL_MAX_AGE,
        dead_letter: DeadLetterStore | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("IMAP 超时时间必须大于 0")
        if not math.isfinite(max_age) or max_age <= 0:
            raise ValueError("邮件最大年龄必须是大于 0 的有限数字")
        self.account = account
        self.timeout = timeout
        self.max_age = max_age
        self._dead_letter = dead_letter
        self._imap: imaplib.IMAP4_SSL | None = None
        self._cutoff = now() - timedelta(seconds=max_age)
        self._skipped: dict[str, set[str]] = {}
        self._cutoff_logged = False

    def poll(self) -> Iterator[MailItem]:
        """
        产出未读邮件。

        调用方打印成功并确认后继续迭代，才会把这封邮件标记为已读；
        如果中途抛异常，未打印的邮件会保持未读状态。

        `max_age` 之前的未读邮件会跳过并缓存，不打印、也不修改已读状态。
        """
        if not self._cutoff_logged:
            log.info(
                "邮箱 %s 只处理 %s 之后到达的邮件",
                self.account.user,
                self._cutoff.isoformat(timespec="seconds"),
            )
            self._cutoff_logged = True

        try:
            with imaplib.IMAP4_SSL(
                self.account.host,
                self.account.port,
                timeout=self.timeout,
            ) as imap:
                imap.login(self.account.user, self.account.password)
                self._imap = imap
                try:
                    for folder in self.account.folders:
                        status, _ = imap.select(folder)
                        if status != "OK":
                            log.warning(
                                "打开邮箱 %s 的文件夹 %s 失败: %s",
                                self.account.user,
                                folder,
                                status,
                            )
                            continue

                        search_date = _imap_date(
                            self._cutoff - timedelta(days=1)
                        )
                        status, data = imap.uid(
                            "search",
                            "UNSEEN",
                            "SINCE",
                            search_date,
                        )
                        if status != "OK":
                            log.warning(
                                "邮箱 %s 的文件夹 %s 不支持 SINCE 搜索，"
                                "改为搜索全部未读: %s",
                                self.account.user,
                                folder,
                                status,
                            )
                            status, data = imap.uid("search", "UNSEEN")
                        if status != "OK":
                            log.warning(
                                "搜索邮箱 %s 的文件夹 %s 失败: %s",
                                self.account.user,
                                folder,
                                status,
                            )
                            continue
                        if not data or not isinstance(data[0], bytes):
                            log.warning(
                                "邮箱 %s 的文件夹 %s 返回了无效的搜索结果",
                                self.account.user,
                                folder,
                            )
                            continue

                        skipped = self._skipped.setdefault(folder, set())
                        for raw_uid in data[0].split():
                            uid = raw_uid.decode()
                            if uid in skipped:
                                continue
                            item = self._fetch(imap, folder, uid)
                            if item is None:
                                # 无法解析的邮件写日志后标记，避免每一轮都重复失败。
                                self._mark_seen(imap, uid)
                                continue
                            if item.date < self._cutoff:
                                skipped.add(uid)
                                log.debug(
                                    "跳过启动前的邮件 %s/%s UID %s (%s)",
                                    self.account.user,
                                    folder,
                                    uid,
                                    item.date.isoformat(timespec="seconds"),
                                )
                                continue
                            yield item
                finally:
                    self._imap = None
        except MailError:
            raise
        except (imaplib.IMAP4.error, OSError) as exc:
            raise MailError(f"读取邮箱 {self.account.user} 失败: {exc}") from exc

    def _fetch(
        self,
        imap: imaplib.IMAP4_SSL,
        folder: str,
        uid: str,
    ) -> MailItem | None:
        status, data = imap.uid("fetch", uid, "(RFC822)")
        if status != "OK":
            raise MailError(
                f"读取邮箱 {self.account.user} 的邮件 UID {uid} 失败: {status}"
            )

        for part in data:
            if isinstance(part, tuple):
                try:
                    return _parse_message(
                        self.account.user,
                        part[1],
                        folder=folder,
                        uid=uid,
                    )
                except Exception as exc:
                    # 解析失败是永久性错误：记录死信后标记已读，避免反复失败。
                    log.exception(
                        "解析邮箱 %s 的文件夹 %s 的邮件 UID %s 失败",
                        self.account.user,
                        folder,
                        uid,
                    )
                    if self._dead_letter:
                        self._dead_letter.write(
                            kind="mail",
                            account=self.account.user,
                            item=f"{folder} UID {uid}",
                            reason=f"{type(exc).__name__}: {exc}",
                            details={
                                "folder": folder,
                                "uid": uid,
                                "error_type": type(exc).__name__,
                            },
                        )
                    return None

        log.warning(
            "邮箱 %s 的文件夹 %s 的邮件 UID %s 没有返回 RFC822 内容",
            self.account.user,
            folder,
            uid,
        )
        if self._dead_letter:
            self._dead_letter.write(
                kind="mail",
                account=self.account.user,
                item=f"{folder} UID {uid}",
                reason="FETCH 没有返回 RFC822 内容",
                details={"folder": folder, "uid": uid},
            )
        return None

    def mark_seen(self, item: MailItem) -> None:
        """在 poll 生成器暂停期间，把当前邮件标记为已读。"""
        if self._imap is None:
            log.warning(
                "邮箱 %s 的连接已经关闭，无法标记 %s/%s 为已读",
                self.account.user,
                item.folder,
                item.uid,
            )
            return
        self._mark_seen(self._imap, item.uid)

    def _mark_seen(self, imap: imaplib.IMAP4_SSL, uid: str) -> None:
        try:
            status, data = imap.uid("store", uid, "+FLAGS", "\\Seen")
        except (imaplib.IMAP4.error, OSError) as exc:
            log.warning(
                "标记邮箱 %s 的邮件 UID %s 为已读失败: %s",
                self.account.user,
                uid,
                exc,
            )
            return
        if status != "OK":
            log.warning(
                "标记邮箱 %s 的邮件 UID %s 为已读失败: %s %s",
                self.account.user,
                uid,
                status,
                data,
            )


def list_folders(
    account: MailAccount,
    *,
    timeout: float = DEFAULT_IMAP_TIMEOUT,
) -> list[MailFolder]:
    """登录邮箱并列出 IMAP 文件夹。"""
    try:
        with imaplib.IMAP4_SSL(
            account.host,
            account.port,
            timeout=timeout,
        ) as imap:
            imap.login(account.user, account.password)
            status, data = imap.list()
            if status != "OK":
                raise MailError(f"列出邮箱 {account.user} 的文件夹失败: {status}")
            folders = []
            for line in data:
                if not isinstance(line, bytes):
                    continue
                folder = _parse_list_line(line)
                if folder is not None:
                    folders.append(folder)
            return folders
    except MailError:
        raise
    except (imaplib.IMAP4.error, OSError) as exc:
        raise MailError(f"读取邮箱 {account.user} 失败: {exc}") from exc


def _imap_date(value: datetime) -> str:
    """把日期格式化成 IMAP SEARCH 需要的 `02-Nov-2026`。"""
    return f"{value.day:02d}-{_IMAP_MONTHS[value.month - 1]}-{value.year}"


def _parse_list_line(line: bytes) -> MailFolder | None:
    match = _LIST_RE.match(line)
    if match is None:
        log.warning("无法解析 IMAP LIST 响应: %r", line)
        return None

    flags = tuple(
        flag.decode("ascii", errors="replace")
        for flag in match.group("flags").split()
    )
    name = match.group("name").strip()
    if name.startswith(b'"') and name.endswith(b'"'):
        name = (
            name[1:-1]
            .replace(b'\\"', b'"')
            .replace(b"\\\\", b"\\")
        )
    return MailFolder(
        name=name.decode("utf-8", errors="replace"),
        flags=flags,
    )


def decode_imap_utf7(value: str) -> str:
    """解码 IMAP modified UTF-7 文件夹名，解码失败时保留原文。"""
    result: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "&":
            result.append(value[index])
            index += 1
            continue

        end = value.find("-", index + 1)
        if end == -1:
            result.append(value[index:])
            break

        chunk = value[index + 1 : end]
        if not chunk:
            result.append("&")
        else:
            try:
                encoded = chunk.replace(",", "/")
                padding = "=" * (-len(encoded) % 4)
                data = base64.b64decode(encoded + padding, validate=True)
                result.append(data.decode("utf-16-be"))
            except (binascii.Error, UnicodeDecodeError):
                result.append(value[index : end + 1])
        index = end + 1

    return "".join(result)


def _parse_message(
    recipient: str,
    raw: bytes,
    *,
    folder: str = "",
    uid: str = "",
) -> MailItem:
    message = cast(
        EmailMessage,
        email.message_from_bytes(raw, policy=email.policy.default),
    )
    sender_name, sender = parseaddr(_header_value(message, "From"))
    sender_name = _normalize_sender_name(sender_name, sender)
    body, html_body = _message_bodies(message)
    return MailItem(
        recipient=_format_addresses(_header_value(message, "To")) or recipient,
        sender=sender or sender_name or "unknown",
        sender_name=sender_name or sender or "未知发件人",
        subject=_header_value(message, "Subject") or "(无主题)",
        date=_parse_date(message.get("Date")),
        body=body,
        html_body=html_body,
        folder=folder,
        uid=uid,
    )


def _header_value(message: EmailMessage, name: str) -> str:
    """从原始头字段解码，避免 policy 提前用错误字符集替换字节。"""
    raw = _raw_header(message, name)
    if raw is None:
        return ""
    return _decode_header(raw)


def _format_addresses(value: str) -> str:
    """规范化地址头：显示名和地址相同时只保留地址。"""
    if not value:
        return ""

    formatted = [
        _format_mailbox(name, address)
        for name, address in getaddresses([value])
        if address
    ]
    if formatted:
        return ", ".join(formatted)

    # getaddresses 对未加引号的 "a@b <a@b>" 会解析失败，这里兜底。
    match = _SINGLE_ADDRESS_RE.fullmatch(value.strip())
    if match is not None:
        return _format_mailbox(match.group(1), match.group(2))
    return value.strip()


def _format_mailbox(name: str, address: str) -> str:
    name = name.strip().strip('"').strip()
    address = address.strip()
    if not address:
        return name
    if not name or _same_address(name, address):
        return address
    if any(char in name for char in ',;<>"@'):
        escaped = name.replace("\\", "\\\\").replace('"', '\\"')
        name = f'"{escaped}"'
    return f"{name} <{address}>"


def _normalize_sender_name(name: str, address: str) -> str:
    name = name.strip().strip('"').strip()
    if not name:
        return ""
    if _same_address(name, address):
        return address.partition("@")[0]
    return name


def _same_address(name: str, address: str) -> bool:
    return bool(name) and name.strip().casefold() == address.strip().casefold()


def _raw_header(message: EmailMessage, name: str) -> str | None:
    lower = name.lower()
    for key, value in message.raw_items():
        if key.lower() == lower:
            return value
    return None


def _decode_header(value: str) -> str:
    if not value:
        return ""
    # 展开折行，再交给 decode_header 处理 RFC 2047 encoded-word。
    value = re.sub(r"\r?\n[ \t]+", " ", value)
    try:
        chunks = decode_header(value)
    except (LookupError, ValueError):
        chunks = [(value, None)]

    parts: list[str] = []
    for chunk, charset in chunks:
        if isinstance(chunk, bytes):
            parts.append(_decode_bytes(chunk, charset))
        else:
            parts.append(_decode_text_chunk(chunk))
    return "".join(parts).strip()


def _decode_text_chunk(value: str) -> str:
    """处理 decode_header 返回的 str 分片。

    policy 对 8bit 原始头字段会使用 surrogateescape，这里把原始字节还原后
    再按 UTF-8 / GB18030 等编码重新解码。
    """
    try:
        raw = value.encode("ascii", "surrogateescape")
    except UnicodeEncodeError:
        return _fix_mojibake(value)
    return _decode_bytes(raw, None)


def _fix_mojibake(value: str) -> str:
    """修复常见的 UTF-8 被当成 Latin-1 解码的情况。"""
    if not any(ord(char) > 0x7F for char in value):
        return value
    try:
        raw = value.encode("latin-1")
    except UnicodeEncodeError:
        return value
    try:
        fixed = raw.decode("utf-8")
    except UnicodeDecodeError:
        return value
    return fixed if fixed != value else value


def _parse_date(value: str | None) -> datetime:
    if value:
        try:
            return to_utc8(parsedate_to_datetime(value))
        except (TypeError, ValueError):
            pass
    return now()


def _message_bodies(message: EmailMessage) -> tuple[str, str]:
    """返回用于打印的纯文本，以及供后处理器使用的原始 HTML。"""
    plain = _part_text(message.get_body(preferencelist=("plain",)))
    html = _part_text(message.get_body(preferencelist=("html",))) or ""

    if plain is not None and plain.strip():
        body = plain
    elif html:
        body = _html_to_text(html)
    else:
        body = plain or ""

    return body.strip(), html


def _part_text(part: MIMEPart | None) -> str | None:
    if part is None:
        return None

    # 直接取原始字节，避免 get_content() 在字符集标错时静默产生乱码。
    try:
        payload = part.get_payload(decode=True)
    except (LookupError, ValueError):
        payload = None
    if isinstance(payload, bytes):
        return _decode_bytes(payload, part.get_content_charset())
    if isinstance(payload, str):
        return _fix_mojibake(payload)

    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError):
        return None
    return content if isinstance(content, str) else None


def _decode_bytes(data: bytes, charset: str | None) -> str:
    """按声明的字符集解码，并在常见标错场景下自动回退。"""
    if not data:
        return ""

    if data.startswith(codecs.BOM_UTF32_LE) or data.startswith(
        codecs.BOM_UTF32_BE
    ):
        return data.decode("utf-32", errors="replace")
    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig", errors="replace")
    if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(
        codecs.BOM_UTF16_BE
    ):
        return data.decode("utf-16", errors="replace")

    normalized = _normalize_charset(charset)

    # 单字节字符集（Latin-1 等）经常被误用来标注 UTF-8 内容；
    # 多字节字符集则优先信任声明值，避免把真正的 GBK/Big5 解错。
    if normalized is None or _is_single_byte_charset(normalized):
        utf8 = _try_decode(data, "utf-8")
        if utf8 is not None:
            return utf8

    if normalized is not None:
        decoded = _try_decode(data, normalized)
        if decoded is not None:
            return decoded

    for candidate in ("utf-8", "gb18030", "big5", "shift_jis", "euc-kr"):
        if candidate == normalized:
            continue
        decoded = _try_decode(data, candidate)
        if decoded is not None:
            return decoded

    if normalized is not None:
        try:
            return data.decode(normalized, errors="replace")
        except LookupError:
            pass
    return data.decode("utf-8", errors="replace")


def _try_decode(data: bytes, encoding: str) -> str | None:
    try:
        return data.decode(encoding)
    except (LookupError, UnicodeDecodeError):
        return None


def _normalize_charset(value: str | None) -> str | None:
    if not value:
        return None
    name = value.strip().strip('"').split(";", 1)[0].strip().lower()
    if not name or name in _UNKNOWN_CHARSETS:
        return None
    name = _CHARSET_ALIASES.get(name, name)
    try:
        codecs.lookup(name)
    except LookupError:
        return None
    return name


def _is_single_byte_charset(name: str) -> bool:
    return (
        name in {"ascii", "us-ascii", "latin-1", "iso-8859-1"}
        or name.startswith("iso-8859-")
        or name.startswith("windows-125")
        or name.startswith("cp125")
        or name.startswith("koi8-")
    )


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
