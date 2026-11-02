from datetime import datetime


def make_mail_tar(
    recp: str, sender: str, t: datetime, title: str, sender_name: str, content: str
) -> bytes:
    """
    通过模板 `template/mail/main.typ` 制作邮件tar包
    """
    pass
