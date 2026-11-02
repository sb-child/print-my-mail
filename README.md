# print-my-mail

通过打印一张小票的方式提醒你邮件来了

## 运行我

```bash
# 按 config.toml 持续监听邮箱和 GitHub 通知
uv run print-my-mail

# 打印一封示例邮件和示例 GitHub 通知
uv run print-my-mail test
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
for account in config.github:
    print(account.pat)
```

`load_config()` 会先在当前工作目录、再到项目根目录查找 `config.toml`；找不到时使用默认值。也可以传入路径：`load_config("path/to/config.toml")`。

`[[mail]]` 默认使用 `imap.<邮箱域名>` 作为服务器，Gmail、Outlook 等常见邮箱会自动使用对应的地址；也可以显式指定：

```toml
[[mail]]
user = "your_email@gmail.com"
pass = "your_app_password"
host = "imap.gmail.com"
port = 993
```

## 工作方式

`uv run print-my-mail` 会：

1. 每 60 秒（可通过 `[server] poll_interval` 调整）轮询配置里的所有 IMAP 邮箱和 GitHub 账户；
2. 打印邮箱中的未读邮件；
3. 把 GitHub 的未读通知合并成一张小票打印；
4. 打印任务成功提交后，才把邮件标记为已读、把 GitHub 通知标记为已读。

打印前的内容处理通过 `PostProcessor` 预留：

```python
from print_my_mail import Runner, TransparentPostProcessor, load_config


class MyProcessor(TransparentPostProcessor):
    def mail(self, item):
        # 这里以后可以接 AI 总结
        return item

    def github(self, digest):
        return digest


Runner(load_config(), processor=MyProcessor()).run_forever()
```

默认的 `TransparentPostProcessor` 原样返回内容，不做任何改写。

## 重新生成 gRPC 代码

修改 `proto/dzsrv.proto` 后执行:

```bash
uv run python scripts/gen_proto.py
```

## License

MIT
