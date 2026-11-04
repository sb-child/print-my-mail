"""读取项目根目录的 `config.toml`。

`config.toml` 示例::

    [server]
    target = "127.0.0.1:54321"
    poll_interval = 60
    github_poll_interval = 60
    print_timeout = 120
    print_max_retries = 3
    print_retry_base_delay = 5
    print_retry_max_delay = 300
    imap_timeout = 30
    mail_max_age = 60
    dead_letter_file = "dead-letter.jsonl"
    github_fetch_content = true

    [[mail]]
    user = "your_email@gmail.com"
    pass = "your_app_password"
    # host = "imap.gmail.com"
    # port = 993
    # folders = ["INBOX"]

    [[github]]
    pat = "xxxx"

    [ai]
    enabled = true
    base_url = "https://api.openai.com/v1"
    token = "sk-..."
    model = "gpt-4o-mini"
    timeout = 30
    max_chars = 100
    max_input_chars = 4000
    question = "用不超过 100 字总结内容，并说明是否需要我处理。"
    output_mode = "tool"
    max_retries = 2

读取配置::

    from print_my_mail import load_config

    config = load_config()
    print(config.target)
    for account in config.mail:
        print(account.user)

`load_config()` 会依次在当前工作目录和项目根目录查找 `config.toml`，
找不到时使用默认值；也可以显式传入路径：`load_config("path/to/config.toml")`。

`[server]` 字段：

- `target`：gRPC 打印服务器地址；
- `poll_interval`：轮询间隔；
- `github_poll_interval`：GitHub 初始轮询间隔，实际间隔不会低于
  `poll_interval`，也不会低于 GitHub 返回的 `X-Poll-Interval`；
- `print_timeout`：提交打印和等待打印完成的最长时间；
- `print_max_retries` / `print_retry_base_delay` / `print_retry_max_delay`：
  打印失败后的指数退避重试策略；
- `imap_timeout`：IMAP 超时；
- `mail_max_age`：只打印启动前多少秒以内到达的邮件，更早的未读邮件跳过；
- `dead_letter_file`：死信 JSONL 路径；
- `github_fetch_content`：是否抓取 GitHub 正文和最新评论。

`[[mail]]` 支持 `user`、`pass`、`host`、`port`、`folders`。
`[[github]]` 支持 `pat`。

`[ai]` 字段：

- `enabled`：是否启用 AI 摘要；
- `base_url`：OpenAI 兼容接口地址，可以写 base URL 或完整的
  `/chat/completions` 地址；
- `token`：Bearer token，留空则不发送 Authorization 头（本地服务）；
- `model`：模型名；
- `timeout`：单次请求超时；
- `max_chars`：回答的最大字符数，默认 100；
- `max_input_chars`：发送给模型的正文最大字符数；
- `question`：拼接进提示词的问题，默认要求 100 字内总结；
- `output_mode`：结构化输出的方式，默认 `"tool"`：
  - `"tool"`：要求模型调用 `submit_summary` 工具，DeepSeek / OpenAI 支持；
  - `"json"`：发送 `response_format = {"type": "json_object"}`；
  - `"prompt"`：只靠提示词要求 JSON，适合不支持 tools 和
    `response_format` 的本地服务；
- `max_retries`：返回无效内容时的重试次数，默认 2。

`folders` 默认是 `["INBOX"]`。Gmail 的垃圾邮件文件夹名是 modified UTF-7
编码（例如 `[Gmail]/&V4NXPpCuTvY-`），必须使用 `print-my-mail folders`
输出的原始名称。不要监听“所有邮件”，否则归档和已发送邮件会重复打印。
"""

from __future__ import annotations

import logging
import math
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .client import DEFAULT_TARGET

log = logging.getLogger(__name__)

CONFIG_NAME = "config.toml"

DEFAULT_AI_QUESTION = "用不超过 100 字总结内容，并说明是否需要我处理。"
_AI_OUTPUT_MODES = frozenset({"tool", "json", "prompt"})

_TOP_LEVEL_KEYS = frozenset({"server", "mail", "github", "ai"})
_SERVER_KEYS = frozenset(
    {
        "target",
        "poll_interval",
        "github_poll_interval",
        "print_timeout",
        "print_max_retries",
        "print_retry_base_delay",
        "print_retry_max_delay",
        "imap_timeout",
        "mail_max_age",
        "dead_letter_file",
        "github_fetch_content",
    }
)
_MAIL_KEYS = frozenset({"user", "pass", "host", "port", "folders"})
_GITHUB_KEYS = frozenset({"pat"})
_AI_KEYS = frozenset(
    {
        "enabled",
        "base_url",
        "token",
        "model",
        "timeout",
        "max_chars",
        "max_input_chars",
        "question",
        "output_mode",
        "max_retries",
    }
)

_IMAP_HOSTS = {
    "gmail.com": "imap.gmail.com",
    "googlemail.com": "imap.gmail.com",
    "outlook.com": "imap-mail.outlook.com",
    "hotmail.com": "imap-mail.outlook.com",
    "live.com": "imap-mail.outlook.com",
    "qq.com": "imap.qq.com",
    "163.com": "imap.163.com",
    "126.com": "imap.126.com",
    "yahoo.com": "imap.mail.yahoo.com",
    "icloud.com": "imap.mail.me.com",
}


@dataclass(frozen=True)
class MailAccount:
    """一个需要监听的 IMAP 邮箱。"""

    user: str
    password: str = field(repr=False)
    host: str
    port: int = 993
    folders: tuple[str, ...] = ("INBOX",)

    def __post_init__(self) -> None:
        if not self.user:
            raise ValueError("邮箱 user 不能为空")
        if not self.password:
            raise ValueError("邮箱 pass 不能为空")
        if not self.host:
            raise ValueError("邮箱 host 不能为空")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"邮箱端口超出范围: {self.port}")
        if not self.folders or any(not folder for folder in self.folders):
            raise ValueError("邮箱 folders 必须包含至少一个非空文件夹名")


@dataclass(frozen=True)
class GitHubAccount:
    """一个需要监听的 GitHub 账户。"""

    pat: str = field(repr=False)

    def __post_init__(self) -> None:
        if not self.pat:
            raise ValueError("GitHub pat 不能为空")


@dataclass(frozen=True)
class AIConfig:
    """OpenAI 兼容接口的摘要配置。

    `enabled = false` 时完全不发请求。启用后 `base_url` 和 `model` 必填；
    `base_url` 可以写 base URL，也可以直接写完整的 `/chat/completions` 地址。
    """

    enabled: bool = False
    base_url: str = ""
    token: str = field(default="", repr=False)
    model: str = "gpt-4o-mini"
    timeout: float = 30.0
    max_chars: int = 100
    max_input_chars: int = 4000
    question: str = DEFAULT_AI_QUESTION
    output_mode: str = "tool"
    max_retries: int = 2

    def __post_init__(self) -> None:
        if not self.enabled:
            return
        if not self.base_url.strip():
            raise ValueError("ai.enabled = true 时 ai.base_url 不能为空")
        if not self.model.strip():
            raise ValueError("ai.enabled = true 时 ai.model 不能为空")
        if not self.question.strip():
            raise ValueError("ai.question 不能为空")
        if self.output_mode not in _AI_OUTPUT_MODES:
            raise ValueError(
                "ai.output_mode 必须是 tool、json 或 prompt 之一"
            )
        if self.max_chars <= 0:
            raise ValueError("ai.max_chars 必须大于 0")
        if self.max_input_chars <= 0:
            raise ValueError("ai.max_input_chars 必须大于 0")
        if self.max_retries < 0:
            raise ValueError("ai.max_retries 不能小于 0")
        _require_positive_finite(self.timeout, "ai.timeout")


@dataclass(frozen=True)
class Config:
    """`config.toml` 中与程序相关的配置项。"""

    target: str = DEFAULT_TARGET
    poll_interval: float = 60.0
    github_poll_interval: float = 60.0
    print_timeout: float = 120.0
    print_max_retries: int = 3
    print_retry_base_delay: float = 5.0
    print_retry_max_delay: float = 300.0
    imap_timeout: float = 30.0
    mail_max_age: float = 60.0
    dead_letter_file: str = "dead-letter.jsonl"
    github_fetch_content: bool = True
    ai: AIConfig = field(default_factory=AIConfig)
    mail: tuple[MailAccount, ...] = ()
    github: tuple[GitHubAccount, ...] = ()

    def __post_init__(self) -> None:
        if not self.target:
            raise ValueError("server.target 不能为空")
        if not self.dead_letter_file:
            raise ValueError("server.dead_letter_file 不能为空")
        if self.print_max_retries < 0:
            raise ValueError("server.print_max_retries 不能小于 0")
        _require_positive_finite(self.poll_interval, "server.poll_interval")
        _require_positive_finite(
            self.github_poll_interval,
            "server.github_poll_interval",
        )
        _require_positive_finite(self.print_timeout, "server.print_timeout")
        _require_positive_finite(self.imap_timeout, "server.imap_timeout")
        _require_positive_finite(self.mail_max_age, "server.mail_max_age")
        _require_positive_finite(
            self.print_retry_base_delay,
            "server.print_retry_base_delay",
        )
        _require_positive_finite(
            self.print_retry_max_delay,
            "server.print_retry_max_delay",
        )
        if self.print_retry_max_delay < self.print_retry_base_delay:
            raise ValueError(
                "server.print_retry_max_delay 不能小于 print_retry_base_delay"
            )


def load_config(path: str | Path | None = None) -> Config:
    """
    读取配置并转换成 `Config`。

    不传 `path` 时会依次在当前工作目录和项目根目录查找 `config.toml`；
    找不到时返回默认配置。
    """
    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(f"找不到配置文件: {config_path}")
        data = _read_toml(config_path)
    else:
        data = _load_default_data()

    server = _table(data, "server")
    _warn_unknown(data, _TOP_LEVEL_KEYS, "顶层")
    _warn_unknown(server, _SERVER_KEYS, "[server]")

    ai_table = _table(data, "ai")
    _warn_unknown(ai_table, _AI_KEYS, "[ai]")

    mail_items = _array_of_tables(data, "mail")
    github_items = _array_of_tables(data, "github")
    for index, item in enumerate(mail_items, start=1):
        _warn_unknown(item, _MAIL_KEYS, f"[[mail]] 第 {index} 项")
    for index, item in enumerate(github_items, start=1):
        _warn_unknown(item, _GITHUB_KEYS, f"[[github]] 第 {index} 项")

    mail = tuple(
        _mail_account(item, index)
        for index, item in enumerate(mail_items, start=1)
    )
    github = tuple(
        GitHubAccount(
            pat=_require_str(item, "pat", f"[[github]] 第 {index} 项"),
        )
        for index, item in enumerate(github_items, start=1)
    )

    return Config(
        target=_get_str(server, "target", DEFAULT_TARGET),
        poll_interval=_get_float(server, "poll_interval", 60.0),
        github_poll_interval=_get_float(
            server,
            "github_poll_interval",
            60.0,
        ),
        print_timeout=_get_float(server, "print_timeout", 120.0),
        print_max_retries=_get_int(server, "print_max_retries", 3),
        print_retry_base_delay=_get_float(
            server, "print_retry_base_delay", 5.0
        ),
        print_retry_max_delay=_get_float(server, "print_retry_max_delay", 300.0),
        imap_timeout=_get_float(server, "imap_timeout", 30.0),
        mail_max_age=_get_float(server, "mail_max_age", 60.0),
        dead_letter_file=_get_str(
            server, "dead_letter_file", "dead-letter.jsonl"
        ),
        github_fetch_content=_get_bool(server, "github_fetch_content", True),
        ai=_ai_config(ai_table),
        mail=mail,
        github=github,
    )


def read_config(path: str | Path | None = None) -> dict[str, Any]:
    """读取并返回 `config.toml` 的原始字典，不做字段校验。"""
    if path is not None:
        config_path = Path(path)
        if not config_path.is_file():
            raise FileNotFoundError(f"找不到配置文件: {config_path}")
        return _read_toml(config_path)

    return _load_default_data()


def find_config() -> Path | None:
    """按“当前工作目录 -> 项目根目录”的顺序查找配置文件。"""
    candidates = (
        Path.cwd() / CONFIG_NAME,
        Path(__file__).resolve().parents[2] / CONFIG_NAME,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _load_default_data() -> dict[str, Any]:
    config_path = find_config()
    return {} if config_path is None else _read_toml(config_path)


def _read_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as file:
        return tomllib.load(file)


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"配置项 [{name}] 必须是一个表")
    return value


def _array_of_tables(data: dict[str, Any], name: str) -> list[dict[str, Any]]:
    value = data.get(name, [])
    if not isinstance(value, list):
        raise ValueError(f"配置项 [[{name}]] 必须是一个表数组")
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise ValueError(f"配置项 [[{name}]] 第 {index} 项必须是一个表")
    return value


def _get_str(data: dict[str, Any], key: str, default: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise ValueError(f"配置项 {key} 必须是字符串")
    return value


def _get_int(data: dict[str, Any], key: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"配置项 {key} 必须是整数")
    return value


def _get_bool(data: dict[str, Any], key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"配置项 {key} 必须是布尔值")
    return value


def _get_float(data: dict[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"配置项 {key} 必须是数字")
    return float(value)


def _get_str_list(
    data: dict[str, Any],
    key: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = data.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or not value:
        raise ValueError(f"配置项 {key} 必须是非空字符串数组")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"配置项 {key} 必须是非空字符串数组")
    return tuple(value)


def _require_positive_finite(value: float, name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"配置项 {name} 必须是大于 0 的有限数字")


def _warn_unknown(data: dict[str, Any], known: frozenset[str], context: str) -> None:
    unknown = sorted(set(data) - known)
    if unknown:
        log.warning("配置项 %s 中存在未知字段: %s", context, ", ".join(unknown))


def _require_str(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} 的 {key} 必须是非空字符串")
    return value


def _mail_account(item: dict[str, Any], index: int) -> MailAccount:
    context = f"[[mail]] 第 {index} 项"
    user = _require_str(item, "user", context)
    return MailAccount(
        user=user,
        password=_require_str(item, "pass", context),
        host=_get_str(item, "host", _default_imap_host(user)),
        port=_get_int(item, "port", 993),
        folders=_get_str_list(item, "folders", ("INBOX",)),
    )


def _ai_config(data: dict[str, Any]) -> AIConfig:
    return AIConfig(
        enabled=_get_bool(data, "enabled", False),
        base_url=_get_str(data, "base_url", ""),
        token=_get_str(data, "token", ""),
        model=_get_str(data, "model", "gpt-4o-mini"),
        timeout=_get_float(data, "timeout", 30.0),
        max_chars=_get_int(data, "max_chars", 100),
        max_input_chars=_get_int(data, "max_input_chars", 4000),
        question=_get_str(data, "question", DEFAULT_AI_QUESTION),
        output_mode=_get_str(data, "output_mode", "tool"),
        max_retries=_get_int(data, "max_retries", 2),
    )


def _default_imap_host(user: str) -> str:
    domain = user.rpartition("@")[2].lower()
    if not domain:
        raise ValueError(f"无法从邮箱地址推断 IMAP 服务器: {user}")
    if domain in _IMAP_HOSTS:
        return _IMAP_HOSTS[domain]
    return f"imap.{domain}"

