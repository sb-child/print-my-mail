"""print-my-mail：把未读邮件和 GitHub 通知打印成小票。

快速使用::

    from print_my_mail import load_config, run

    run(load_config())

各模块的配置、命令行、后处理和模板用法见对应文件开头的文档注释。
"""

from .ai import OpenAIPostProcessor
from .cli import main
from .client import DEFAULT_TARGET, PrintMyMailClient
from .config import (
    AIConfig,
    Config,
    GitHubAccount,
    MailAccount,
    find_config,
    load_config,
    read_config,
)
from .github import GitHubDigest
from .mail import MailFolder, MailItem, list_folders
from .postprocess import PostProcessor, TransparentPostProcessor
from .runner import Runner, run, run_once
from .sample import print_test_page
from .template import (
    GitHubItem,
    GitHubKind,
    GitHubNotification,
    make_github_tar,
    make_mail_tar,
)

__all__ = [
    "AIConfig",
    "Config",
    "DEFAULT_TARGET",
    "GitHubAccount",
    "GitHubDigest",
    "GitHubItem",
    "GitHubKind",
    "GitHubNotification",
    "MailAccount",
    "MailFolder",
    "MailItem",
    "OpenAIPostProcessor",
    "PostProcessor",
    "PrintMyMailClient",
    "Runner",
    "TransparentPostProcessor",
    "find_config",
    "load_config",
    "main",
    "make_github_tar",
    "make_mail_tar",
    "list_folders",
    "print_test_page",
    "read_config",
    "run",
    "run_once",
]
