# print-my-mail

通过打印一张小票的方式提醒你邮件来了

## 运行我

```bash
uv run print-my-mail
```

## 启动 gRPC 打印服务器

下载 https://github.com/sb-child/dz-print 项目，然后:

```bash
cargo r -r --bin dzserver /tmp/dzserver 127.0.0.1:54321 --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Light.ttf --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Medium.ttf --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Regular.ttf
```

## 生成邮件 tar 包

```python
from datetime import datetime

from print_my_mail import PrintMyMailClient, make_mail_tar

tar = make_mail_tar(
    recp="someone@example.com",
    sender="sender@example.com",
    t=datetime.now(),
    title="邮件标题",
    sender_name="发件人",
    content="正文内容",
)

with PrintMyMailClient("localhost:54321") as client:
    job_id = client.print_typst(tar)
```

模板中的占位符位于 `template/mail/main.typ`，内容会自动进行 Typst 字符串转义。

## 生成 GitHub 通知 tar 包

```python
from datetime import datetime

from print_my_mail import GitHubNotification, PrintMyMailClient, make_github_tar

tar = make_github_tar(
    datetime.now(),
    [
        GitHubNotification(
            repo="sb-child/print-my-mail",
            id=1,
            title="修复 GitHub 通知模板",
            kind="issue",
        ),
        GitHubNotification(
            repo="sb-child/dz-print",
            id=2,
            title="支持 Typst 渲染",
            kind="pr-merged",
        ),
    ],
    content="可选的通知摘要",
)

with PrintMyMailClient("localhost:54321") as client:
    job_id = client.print_typst(tar)
```

`kind` 可选值为 `issue`、`closed-issue`、`pr`、`pr-merged`，关闭或合并的通知会显示删除线。

## 重新生成 gRPC 代码

修改 `proto/dzsrv.proto` 后执行:

```bash
uv run python scripts/gen_proto.py
```

## License

MIT
