# print-my-mail

通过打印一张小票的方式提醒你来邮件了

## 运行我

```bash
uv run print-my-mail
```

## 启动 gRPC 打印服务器

下载 https://github.com/sb-child/dz-print 项目，然后:

```bash
cargo r -r --bin dzserver /tmp/dzserver 127.0.0.1:54321 --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Light.ttf --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Medium.ttf --font /usr/local/share/fonts/LXGWWenKaiGB/LXGWWenKaiGB-Regular.ttf
```

## 重新生成 gRPC 代码

修改 `proto/dzsrv.proto` 后执行:

```bash
uv run python scripts/gen_proto.py
```

## License

MIT
