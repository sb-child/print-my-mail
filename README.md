# print-my-mail

通过打印一张小票的方式提醒你邮件来了

## 运行我

```bash
# 按 config.toml 持续监听邮箱和 GitHub 通知
uv run print-my-mail

# 打印一封示例邮件和示例 GitHub 通知
uv run print-my-mail test

# 只轮询一轮，便于排障
uv run print-my-mail once

# 列出邮箱的真实 IMAP 文件夹名
uv run print-my-mail folders
```

## 启动 gRPC 打印服务器

下载 https://github.com/sb-child/dz-print 项目，然后:

```bash
cargo r -r --bin dzserver /tmp/dzserver 127.0.0.1:54321 --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Light.ttf --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Medium.ttf --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Regular.ttf
```

## 配置文件

项目根目录的 `config.toml` 用于存放本地配置。可以从 `config.toml.example` 复制一份后修改。`[[mail]]` 和 `[[github]]` 都可以写多个，分别表示要监听的邮箱和 GitHub 账户。

```python
from print_my_mail import load_config

config = load_config()
print(config.target)
for account in config.mail:
    print(account.user)
print(f"已配置 {len(config.github)} 个 GitHub 账户")
```

`load_config()` 会先在当前工作目录、再到项目根目录查找 `config.toml`；找不到时使用默认值。也可以传入路径：`load_config("path/to/config.toml")`。

`[[mail]]` 默认使用 `imap.<邮箱域名>` 作为服务器，Gmail、Outlook 等常见邮箱会自动使用对应的地址；也可以显式指定：

```toml
[[mail]]
user = "your_email@gmail.com"
pass = "your_app_password"
host = "imap.gmail.com"
port = 993
# 用 `uv run print-my-mail folders` 查看真实名称后直接复制推荐配置
folders = ["INBOX", "[Gmail]/&V4NXPpCuTvY-"]
```

`folders` 默认是 `["INBOX"]`。如果想把垃圾邮件也打印出来，把服务商的 Spam/Junk 文件夹加进去。Gmail 的垃圾邮件文件夹名是 modified UTF-7 编码（例如 `[Gmail]/&V4NXPpCuTvY-`），必须使用 `folders` 命令输出的原始名称，不能手写成 `[Gmail]/Spam`。不要直接监听 `All Mail`，否则归档邮件、已发送邮件会重复打印。

IMAP 没有统一的“所有收件箱”概念，`folders` 也不会自动包含 Spam；需要按服务商的实际文件夹名显式配置。

## 工作方式

`uv run print-my-mail` 会：

1. 每 60 秒（可通过 `[server] poll_interval` 调整）轮询配置里的所有 IMAP 邮箱和 GitHub 账户；
2. 打印邮箱中的未读邮件；
3. 把 GitHub 的未读通知合并成一张小票打印；
4. 等待打印任务进入成功终态后，才把邮件标记为已读、把 GitHub 通知标记为已读。

`[server]` 还支持：

```toml
print_timeout = 120          # 等待打印完成的最长时间（秒）
print_max_retries = 3        # 打印失败后的最大重试次数
print_retry_base_delay = 5   # 指数退避的初始间隔（秒）
print_retry_max_delay = 300  # 指数退避的最大间隔（秒）
imap_timeout = 30            # IMAP 连接和操作超时（秒）
dead_letter_file = "dead-letter.jsonl"  # 永久失败通知的记录文件
github_fetch_content = true  # 是否抓取 GitHub 正文和最新评论
```

每个邮箱和 GitHub 账户会在独立线程里轮询，打印提交仍然串行，避免互相拖慢。

投递语义是 at-least-once：极端情况下（打印完成但还来不及标记已读）可能重复打印一次，但不会静默丢通知。无法解析的邮件、无法转换的 GitHub 通知会写入 `dead_letter_file`，然后标记为已读，避免每轮重复失败。

打印前的内容处理通过 `PostProcessor` 预留：

```python
from dataclasses import replace

from print_my_mail import Runner, TransparentPostProcessor, load_config


class MyProcessor(TransparentPostProcessor):
    def mail(self, item):
        # 这里以后可以接 AI 总结
        return item

    def github(self, digest):
        items = []
        for item in digest.items:
            # 这些字段都可以交给 AI
            print(item.notification.repo, item.notification.title)
            print(item.reason, item.body, item.latest_comment_body)
            items.append(replace(item, content="可选的 AI 摘要"))
        return replace(digest, items=tuple(items))


Runner(load_config(), processor=MyProcessor()).run_forever()
```

`GitHubItem` 会带上：

- `notification`：repo、编号、标题、类型
- `reason`：GitHub 的通知原因，比如 `mention`、`review_requested`
- `updated_at`
- `url`
- `latest_comment_url`
- `body`：Issue / PR / Release 等资源正文
- `latest_comment_body`：最新评论正文
- `content`：打印在通知下面的文本，默认空，通常由后处理器填写

默认的 `TransparentPostProcessor` 原样返回内容，不做任何改写。

## 重新生成 gRPC 代码

修改 `proto/dzsrv.proto` 后执行:

```bash
uv run python scripts/gen_proto.py
```

## License

MIT
