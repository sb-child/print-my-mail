"""读取项目根目录的 `config.toml`。"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .client import DEFAULT_TARGET

CONFIG_NAME = "config.toml"

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
    password: str
    host: str
    port: int = 993


@dataclass(frozen=True)
class GitHubAccount:
    """一个需要监听的 GitHub 账户。"""

    pat: str


@dataclass(frozen=True)
class Config:
    """`config.toml` 中与程序相关的配置项。"""

    target: str = DEFAULT_TARGET
    poll_interval: float = 60.0
    mail: tuple[MailAccount, ...] = ()
    github: tuple[GitHubAccount, ...] = ()


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

    mail = tuple(
        _mail_account(item, index)
        for index, item in enumerate(_array_of_tables(data, "mail"), start=1)
    )
    github = tuple(
        GitHubAccount(
            pat=_require_str(item, "pat", f"[[github]] 第 {index} 项"),
        )
        for index, item in enumerate(_array_of_tables(data, "github"), start=1)
    )

    return Config(
        target=_get_str(server, "target", DEFAULT_TARGET),
        poll_interval=_get_float(server, "poll_interval", 60.0),
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


def _get_float(data: dict[str, Any], key: str, default: float) -> float:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"配置项 {key} 必须是数字")
    return float(value)


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
    )


def _default_imap_host(user: str) -> str:
    domain = user.rpartition("@")[2].lower()
    if not domain:
        raise ValueError(f"无法从邮箱地址推断 IMAP 服务器: {user}")
    if domain in _IMAP_HOSTS:
        return _IMAP_HOSTS[domain]
    return f"imap.{domain}"

