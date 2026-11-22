# onssh

在浏览器里登录 Linux 服务器的 SSH 终端。后端是 Python（tornado + paramiko），前端是 xterm.js：打开网页，填主机、端口、用户名和密码，就得到一个可以运行 vim、top 的交互式终端，窗口大小随浏览器变化。

适合的场景：在一台跳板机上起一个服务，团队里的人用浏览器登录内网机器，不需要在本地装 SSH 客户端。它不是堡垒机，没有自己的用户体系、审计和录屏，见下文的限制。

## 快速开始

需要 Python 3.10 或更新版本。

```bash
git clone https://github.com/chenziyaos/onssh.git && cd onssh
uv run onssh                      # 或：pip install . && onssh
# 打开 http://127.0.0.1:8000/
```

默认只监听 `127.0.0.1`。要让别人访问，放在反向代理后面并加上 HTTPS 和认证，见[部署](#部署)。

## 命令行参数

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--address` | `127.0.0.1` | 监听地址。改成非本机地址时启动日志会给出警告 |
| `--port` | `8000` | 监听端口 |
| `--allow CIDR\|NAME` | 不限制 | 允许连接的目标，可重复或用逗号分隔。网段按解析后的 IP 匹配（`10.0.0.0/8`），名字按填写的主机名匹配（`db1.example.com`，`*.example.com` 匹配所有子域名） |
| `--known-hosts` | `~/.config/onssh/known_hosts` | 记录服务器主机密钥的文件 |
| `--strict-host-keys` | 关 | 只连接 known_hosts 里已有的服务器 |
| `--debug` | 关 | tornado 调试模式（改代码自动重启） |

例：只允许登录 `10.0.0.0/8` 和 `*.corp.example.com`：

```bash
uv run onssh --allow 10.0.0.0/8,*.corp.example.com
```

## 主机密钥

第一次连接某台服务器时，onssh 把它的主机密钥写进 `--known-hosts` 文件（权限 600），并在终端里打印一行 `Permanently added <主机> (<类型> SHA256:...)`。指纹格式和 OpenSSH 相同，可以和服务器上 `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` 的输出核对。

之后再连接时，如果服务器的密钥变了，onssh 拒绝连接并显示新密钥的指纹。确认是服务器重装导致的，就从 known_hosts 文件里删掉旧的那一行。运行 onssh 的账号自己的 `~/.ssh/known_hosts` 也会读取，但不会写入。

需要更严格时加 `--strict-host-keys`：未知服务器一律拒绝，known_hosts 由管理员事先准备（例如 `ssh-keyscan host >> ~/.config/onssh/known_hosts` 之后人工核对指纹）。

## 部署

onssh 本身没有登录页，谁能打开网页，谁就能通过它尝试登录任何它能连到的 SSH 服务器。对外提供服务时至少要做到：

1. onssh 只监听 `127.0.0.1`，前面放 nginx 之类的反向代理；
2. 反向代理启用 HTTPS（密码要经过网络传输）和认证（basic auth、SSO 网关等）；
3. 用 `--allow` 限定可以登录的网段或主机。

[`deploy/nginx.conf`](deploy/nginx.conf) 是一份 nginx 配置示例，包括 WebSocket 需要的 `Upgrade` 头和长连接超时。WebSocket 会校验 `Origin` 和 `Host` 是否一致，所以反向代理要转发原始的 `Host` 头。

## 安全上做了什么

- 密码只在登录时经 WebSocket 发给服务端一次，不写日志，错误信息里也不带；浏览器的“记住”只保存主机、端口和用户名。0.1 版存在 localStorage 里的密码，新版页面打开时会删掉。
- 连接目标服务器时不使用运行 onssh 的账号的私钥和 ssh-agent（`look_for_keys=False`、`allow_agent=False`），不会把它们暴露给用户填写的任意服务器。
- `--allow` 在 DNS 解析之后按实际要连接的 IP 检查，检查和连接用的是同一个地址。
- WebSocket 拒绝跨站请求（tornado 默认的 Origin 校验），别的网站不能借用户的浏览器打开 SSH 会话；页面带 Content-Security-Policy，不加载任何外部资源。
- 每条 WebSocket 消息最大 1 MB。

## 限制

- 只支持密码登录，不支持私钥、键盘交互式认证（例如 OTP 二次验证）和 SSH 证书。
- 没有会话审计、录屏和用户管理；需要这些请用专门的堡垒机。
- 每个浏览器标签页一个 SSH 连接，关闭页面即断开，不支持断线重连。
- 不支持 SFTP 文件传输和端口转发。
- 在 macOS 上手工测试过，CI 在 Linux 上跑测试；Windows 没有测试过。

## 开发

```bash
uv run ruff check .
uv run python -m unittest discover -s tests -v
```

测试在 `127.0.0.1` 的随机端口上起一个用 paramiko 写的 SSH 服务端和 onssh，经 WebSocket 走完整的登录流程，覆盖回显和 UTF-8、首次记录主机密钥、密钥变化被拒、`--strict-host-keys`、`--allow`、密码错误不进日志、窗口大小变化、跨站 WebSocket 被拒和非法输入。CI 在 Python 3.10 – 3.14 上运行。

## 第三方代码与许可

- `onssh/static/vendor/xterm/`：[xterm.js](https://github.com/xtermjs/xterm.js) 6.0.0 和 addon-fit 0.11.0，MIT 许可，原样复制自 npm 包。
- onssh 本身使用 MIT 许可，见 [LICENSE](LICENSE)。

## 版本历史

- 1.0.0：迁移到 Python 3、tornado 6、paramiko 3 及以上；终端换成 xterm.js；去掉自写的多线程事件循环、jQuery 和外部 CSS；修复密码出现在错误信息和 localStorage 里的问题；增加主机密钥校验、目标白名单、窗口大小同步和测试。
- 0.1.x（2017）：Python 2.7 版本。
