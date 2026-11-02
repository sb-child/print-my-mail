from .cli import main
from .client import DEFAULT_TARGET, PrintMyMailClient
from .config import (
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
