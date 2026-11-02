from .client import DEFAULT_TARGET, PrintMyMailClient
from .template import GitHubNotification, make_github_tar, make_mail_tar

__all__ = [
    "DEFAULT_TARGET",
    "GitHubNotification",
    "PrintMyMailClient",
    "main",
    "make_github_tar",
    "make_mail_tar",
]


def main() -> None:
    print("Hello from print-my-mail!")
    cli = PrintMyMailClient(DEFAULT_TARGET)
    
