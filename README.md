# print-my-mail

通过打印一张小票的方式提醒你邮件来了

## 运行我

```bash
# 正式入口
uv run print-my-mail

# 运行当前的示例逻辑
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

## 重新生成 gRPC 代码

修改 `proto/dzsrv.proto` 后执行:

```bash
uv run python scripts/gen_proto.py
```

## License

MIT
