# print-my-mail

通过打印一张小票提醒你有新邮件或 GitHub 通知。程序会轮询配置的 IMAP 邮箱和 GitHub 账户，把未读内容提交给 dz-print，并在确认打印成功后标记为已读。

## 快速开始

1. 复制 `config.toml.example` 为 `config.toml`，填写邮箱应用专用密码和 GitHub PAT。
2. 启动 dz-print 的 [gRPC 打印服务](https://github.com/sb-child/dz-print#%E6%89%93%E5%8D%B0%E6%9C%8D%E5%8A%A1%E5%99%A8dzserver)。

```bash
dz-print/target/release/dzserver /tmp/dzserver '127.0.0.1:54321' --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Light.ttf --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Medium.ttf --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Regular.ttf
```

3. 在当前目录执行 `uv run print-my-mail` 开始监听；`uv run print-my-mail test` 打印示例，`once` 只轮询一轮，`folders` 查看真实 IMAP 文件夹名。

第一次运行前建议用 `folders` 确认文件夹原始名称，并且只保留 INBOX 和垃圾邮件文件夹，避免把归档或已发送邮件重复打印。

## 文档位置

用法示例和配置说明都写在对应模块开头的文档注释里：

- 配置与 `config.toml` 示例：`src/print_my_mail/config.py`
- 命令行：`src/print_my_mail/cli.py`
- 后台轮询与投递语义：`src/print_my_mail/runner.py`
- 打印前处理、接 AI 摘要的入口：`src/print_my_mail/postprocess.py`
- 邮件轮询：`src/print_my_mail/mail.py`
- GitHub 轮询：`src/print_my_mail/github.py`
- Typst 模板打包：`src/print_my_mail/template.py`
- gRPC 客户端与 dz-print 启动：`src/print_my_mail/client.py`
- 死信格式：`src/print_my_mail/deadletter.py`
- 示例打印页：`src/print_my_mail/sample.py`

## License

MIT
