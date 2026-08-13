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
# ProxyCommand 里的跳板：ssh -W %h:%p [-p 443] <连接OCID>@<网关>
# 中间可能夹着 -o 这类选项，所以用 [^@\s]+@ 之前允许任意非引号内容。
_PROXY_RE = re.compile(
    r"ProxyCommand\s*=\s*[\"']?\s*ssh(?P<opts>[^\"']*?)"
    r"(?P<user>[^@\s\"']+)@(?P<host>[A-Za-z0-9][A-Za-z0-9.\-]*)"
)
# 外层目标：<实例OCID>@<网关>，取最后一个匹配（ProxyCommand 在前面）
_TARGET_RE = re.compile(
    r"(?P<user>ocid1\.instance\.[^@\s\"']+)@(?P<host>[A-Za-z0-9][A-Za-z0-9.\-]*)"
)
_PORT_RE = re.compile(r"-p\s+(\d+)")


def parse_console_string(connection_string: str) -> dict:
    """从 OCI 给的 ssh 命令里解析出两跳的信息。

    不去 shell 里执行它——那等于把外部字符串交给 shell。
    这里只取出主机、端口、用户名，然后用 paramiko 自己搭。
    """
    s = " ".join((connection_string or "").split())
    proxy = _PROXY_RE.search(s)
    targets = list(_TARGET_RE.finditer(s))
    if not proxy or not targets:
        raise OCIError("串口控制台连接串的格式不认识，没法自动连接。"
                       "把上面那条命令复制到本地终端执行同样可用。")

    # ProxyCommand 里的端口（通常 443）；外层没写 -p 就是 22
    proxy_port = _PORT_RE.search(proxy.group("opts") or "")
    outer = s[proxy.end():]
    outer_port = _PORT_RE.search(outer)

    target = targets[-1]
    return {
        "proxy_user": proxy.group("user"),
        "proxy_host": proxy.group("host"),
        "proxy_port": int(proxy_port.group(1)) if proxy_port else 22,
        "target_user": target.group("user"),
        "target_host": target.group("host"),
        "target_port": int(outer_port.group(1)) if outer_port else 22,
    }


# ---- 建立 SSH 会话 ----

# paramiko 2.9 起，RSA 私钥默认按 rsa-sha2-512/256 去认证。
# Oracle 串口控制台的网关是个老实现，只认 SHA-1 的 ssh-rsa，
# 于是认证会莫名其妙地失败——报的还是「认证失败」，很容易被当成密钥不对。
# 先按默认方式试，失败了再退回 SHA-1 重试一次。
_SHA1_FALLBACK = {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}


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


def _auth(sock, username: str, pkey, timeout: int, disabled=None):
    """建一条已认证的 Transport。sock 可以是 (host, port)，也可以是一条通道。"""
    kw = {"disabled_algorithms": disabled} if disabled else {}
    transport = paramiko.Transport(sock, **kw)
    transport.banner_timeout = timeout
    try:
        transport.start_client(timeout=timeout)
        transport.auth_publickey(username, pkey)
    except Exception:
        try:
            transport.close()
        except Exception:  # noqa: BLE001
            pass
        raise
    return transport


def _describe(e: Exception) -> str:
    """把异常压成一句有信息量的话——原始类型名往往才是线索。"""
    text = str(e).strip()
    name = type(e).__name__
    return f"{text}（{name}）" if text else name


def open_direct(host: str, port: int, username: str, pkey,
                cols: int = 80, rows: int = 24, timeout: int = 15) -> Session:
    """直连实例的 SSH。"""
    if paramiko is None:
        raise OCIError(f"服务端缺少 paramiko（{PARAMIKO_ERROR}）")
    if not host:
        raise OCIError("这台实例没有公网 IP，没法直连。可以改用串口控制台。")

    addr = (host, int(port or 22))
    try:
        transport = _auth(addr, username, pkey, timeout)
    except paramiko.AuthenticationException as first:
        try:
            transport = _auth(addr, username, pkey, timeout, _SHA1_FALLBACK)
        except Exception:  # noqa: BLE001
            # 重试本身怎么挂的不重要——真正的问题是认证没过。
            # 报「连不上」会把人引到网络上去查，方向就错了。
            raise OCIError(
                f"认证失败（用户名 {username}）。确认：① 用户名对不对"
                "（Oracle 的 Ubuntu 镜像是 ubuntu，Oracle Linux 是 opc）；"
                f"② 这把私钥对应的公钥已经在实例上。原始信息：{_describe(first)}") from None
    except Exception as e:  # noqa: BLE001
        raise OCIError(f"连不上 {host}:{addr[1]}：{_describe(e)}") from None
    return Session(_shell(transport, cols, rows), [transport])


def _console_chain(info: dict, pkey, timeout: int, disabled=None):
    """跳板 → direct-tcpip → 内层 SSH，等价于 ssh 的 ProxyCommand -W。"""
    transports = []
    try:
        jump = _auth((info["proxy_host"], info["proxy_port"]),
                     info["proxy_user"], pkey, timeout, disabled)
        transports.append(jump)
        tunnel = jump.open_channel(
            "direct-tcpip", (info["target_host"], info["target_port"]), ("127.0.0.1", 0))
        inner = _auth(tunnel, info["target_user"], pkey, timeout, disabled)
        transports.append(inner)
        return transports
    except Exception:
        for t in reversed(transports):
            try:
                t.close()
            except Exception:  # noqa: BLE001
                pass
        raise


def open_console(connection_string: str, pkey,
                 cols: int = 80, rows: int = 24, timeout: int = 20) -> Session:
    """走 Oracle 串口控制台跳板。"""
    if paramiko is None:
        raise OCIError(f"服务端缺少 paramiko（{PARAMIKO_ERROR}）")
    info = parse_console_string(connection_string)
    try:
        transports = _console_chain(info, pkey, timeout)
    except paramiko.AuthenticationException as first:
        # 老网关只认 SHA-1 的 ssh-rsa，退回去再试一次
        try:
            transports = _console_chain(info, pkey, timeout, _SHA1_FALLBACK)
        except Exception:  # noqa: BLE001
            # 同上：重试怎么挂的不是重点，认证没过才是
            raise OCIError(
                "串口控制台认证失败。这把私钥必须与**创建这条连接时填的公钥**是一对"
                "（不是实例登录用的那把，除非你用的是同一把）。"
                f"原始信息：{_describe(first)}") from None
    except Exception as e:  # noqa: BLE001
        raise OCIError(
            f"连接串口控制台失败：{_describe(e)}。"
            f"（跳板 {info['proxy_host']}:{info['proxy_port']}，"
            "面板所在服务器需要能出站访问这个地址）") from None
    return Session(_shell(transports[-1], cols, rows), transports)
