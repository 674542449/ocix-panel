"""网页终端：把浏览器的 WebSocket 桥到一条 SSH 会话。

两种目标：
  * ``direct``  —— 直连实例公网 IP 的 22 端口，日常用；
  * ``console`` —— 走 Oracle 的串口控制台跳板，SSH / 网络坏了也能进。

**私钥只存在于内存**：随 WebSocket 的第一条消息传进来，会话结束即销毁，
任何时候都不写磁盘、不进日志。面板被攻破也拿不到你的 shell 凭据——
这是刻意的取舍，代价是每次开终端都要选一次私钥文件。
"""

from __future__ import annotations

import io
import re
import secrets
import threading
import time

# paramiko 是可选依赖：没装也要能启动，只是终端功能不可用。
try:
    import paramiko

    PARAMIKO_ERROR = None
except Exception as e:  # noqa: BLE001 - 缺依赖不该让整个面板起不来
    paramiko = None
    PARAMIKO_ERROR = str(e)

from .common import OCIError

# ---- 一次性票据 ----
# 浏览器的 WebSocket API 没法自定义 Authorization 头，而把 JWT 放进 URL
# 会漏进反代日志和浏览器历史。所以先用已鉴权的接口换一张短命票据，
# WS 只带票据；用过即焚，30 秒过期。
TICKET_TTL = 30
_tickets: dict[str, tuple[str, float]] = {}
_ticket_lock = threading.Lock()


def issue_ticket(user: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _ticket_lock:
        # 顺手清掉过期的，省得内存里越攒越多
        for k in [k for k, (_, exp) in _tickets.items() if exp < now]:
            _tickets.pop(k, None)
        _tickets[token] = (user, now + TICKET_TTL)
    return token


def consume_ticket(token: str) -> str | None:
    """校验并作废票据，返回用户名；无效返回 None。"""
    with _ticket_lock:
        hit = _tickets.pop(token or "", None)
    if not hit:
        return None
    user, exp = hit
    return user if exp >= time.time() else None


# ---- 私钥 ----

def load_private_key(text: str, passphrase: str = ""):
    """从文本读私钥。挨个类型试——OpenSSH 新格式不带类型标记，只能试。"""
    if paramiko is None:
        raise OCIError(f"服务端缺少 paramiko，无法使用网页终端（{PARAMIKO_ERROR}）")
    text = (text or "").strip()
    if not text:
        raise OCIError("请提供私钥")
    if "PRIVATE KEY" not in text:
        raise OCIError("这不像是私钥。网页终端需要的是私钥文件（不是 .pub 公钥）")

    pw = passphrase or None
    errors = []
    # 类型按名字取：paramiko 5 移除了 DSSKey，写死引用会在导入时就炸。
    # 顺序无所谓，反正是挨个试。
    candidates = [getattr(paramiko, name, None)
                  for name in ("Ed25519Key", "RSAKey", "ECDSAKey", "DSSKey")]
    for cls in [c for c in candidates if c is not None]:
        try:
            return cls.from_private_key(io.StringIO(text), password=pw)
        except paramiko.PasswordRequiredException:
            raise OCIError("这把私钥有口令，请填写口令") from None
        except paramiko.SSHException as e:
            errors.append(f"{cls.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            errors.append(f"{cls.__name__}: {e}")
    raise OCIError("私钥解析失败，确认是完整的私钥且口令正确")


# ---- 串口控制台连接串解析 ----
# OCI 给的是一条完整的 ssh 命令，形如：
#   ssh -o ProxyCommand="ssh -W %h:%p -p 443 <连接OCID>@instance-console.<区域>.oci.oraclecloud.com"
#       <实例OCID>@instance-console.<区域>.oci.oraclecloud.com
# 我们不去 shell 里执行它，而是把里面的跳板信息解析出来，用 paramiko 自己搭这两跳。
_PROXY_RE = re.compile(
    r"ProxyCommand=[\"']?ssh\s+-W\s+%h:%p(?:\s+-p\s+(?P<port>\d+))?\s+"
    r"(?P<user>[^@\s]+)@(?P<host>[^\s\"']+)"
)
_TARGET_RE = re.compile(r"(?P<user>ocid1\.instance\.[^@\s]+)@(?P<host>[A-Za-z0-9.\-]+)")


def parse_console_string(connection_string: str) -> dict:
    s = (connection_string or "").strip()
    proxy = _PROXY_RE.search(s)
    target = _TARGET_RE.search(s)
    if not proxy or not target:
        raise OCIError("串口控制台连接串格式不认识，无法自动连接。"
                       "可以复制命令到本地终端执行。")
    return {
        "proxy_user": proxy.group("user"),
        "proxy_host": proxy.group("host"),
        "proxy_port": int(proxy.group("port") or 22),
        "target_user": target.group("user"),
        "target_host": target.group("host"),
    }


# ---- 建立 SSH 会话 ----

class Session:
    """一条已经打开的 SSH 会话，只暴露收发和改窗口大小。"""

    def __init__(self, channel, transports: list):
        self.channel = channel
        self._transports = transports

    def resize(self, cols: int, rows: int):
        try:
            self.channel.resize_pty(width=max(1, int(cols)), height=max(1, int(rows)))
        except Exception:  # noqa: BLE001 - 改不了大小不值得中断会话
            pass

    def close(self):
        try:
            self.channel.close()
        except Exception:  # noqa: BLE001
            pass
        for t in reversed(self._transports):
            try:
                t.close()
            except Exception:  # noqa: BLE001
                pass


def _shell(transport, cols: int, rows: int):
    chan = transport.open_session()
    chan.get_pty(term="xterm-256color", width=max(1, cols), height=max(1, rows))
    chan.invoke_shell()
    chan.settimeout(0.0)
    return chan


def open_direct(host: str, port: int, username: str, pkey,
                cols: int = 80, rows: int = 24, timeout: int = 15) -> Session:
    """直连实例的 SSH。"""
    if paramiko is None:
        raise OCIError(f"服务端缺少 paramiko（{PARAMIKO_ERROR}）")
    if not host:
        raise OCIError("这台实例没有公网 IP，没法直连。可以改用串口控制台。")
    try:
        transport = paramiko.Transport((host, int(port or 22)))
        transport.banner_timeout = timeout
        transport.start_client(timeout=timeout)
        transport.auth_publickey(username, pkey)
    except paramiko.AuthenticationException:
        raise OCIError(f"认证失败：{username} 这把私钥不被接受。"
                       "确认用户名对（Ubuntu 镜像通常是 ubuntu）且公钥已在实例上。") from None
    except Exception as e:  # noqa: BLE001
        raise OCIError(f"连不上 {host}:{port}：{e}") from None
    return Session(_shell(transport, cols, rows), [transport])


def open_console(connection_string: str, pkey,
                 cols: int = 80, rows: int = 24, timeout: int = 20) -> Session:
    """走 Oracle 串口控制台跳板。

    等价于 ssh 的 ProxyCommand + -W：先连跳板，再在跳板上开一条
    direct-tcpip 通道到目标，然后在那条通道上再跑一次 SSH。
    """
    if paramiko is None:
        raise OCIError(f"服务端缺少 paramiko（{PARAMIKO_ERROR}）")
    info = parse_console_string(connection_string)
    transports = []
    try:
        jump = paramiko.Transport((info["proxy_host"], info["proxy_port"]))
        jump.banner_timeout = timeout
        jump.start_client(timeout=timeout)
        jump.auth_publickey(info["proxy_user"], pkey)
        transports.append(jump)

        tunnel = jump.open_channel(
            "direct-tcpip", (info["target_host"], 22), ("127.0.0.1", 0))

        inner = paramiko.Transport(tunnel)
        inner.banner_timeout = timeout
        inner.start_client(timeout=timeout)
        inner.auth_publickey(info["target_user"], pkey)
        transports.append(inner)
    except paramiko.AuthenticationException:
        for t in transports:
            t.close()
        raise OCIError("串口控制台认证失败：这把私钥与创建连接时用的公钥不是一对。") from None
    except Exception as e:  # noqa: BLE001
        for t in transports:
            t.close()
        raise OCIError(f"连接串口控制台失败：{e}") from None
    return Session(_shell(inner, cols, rows), transports)
