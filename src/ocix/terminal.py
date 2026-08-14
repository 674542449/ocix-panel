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
#
# OCI 给的是一条完整的 ssh 命令，大致长这样：
#   ssh -o ProxyCommand="ssh -W %h:%p -p 443 <连接OCID>@<网关>" <实例OCID>@<网关>
#
# 但各区域 / 各版本的具体写法并不统一（引号位置、选项顺序、有没有 -i、
# 是不是 -o "ProxyCommand=..." 整体加引号…）。早先的实现去匹配命令的形状，
# 结果真实的串反而对不上，报「格式不认识」。
#
# 现在不看命令怎么写，只认里面的 user@host：
# OCI 的两个 OCID 本身就区分了角色——
#   ocid1.instanceconsoleconnection...  是跳板要用的用户名
#   ocid1.instance...                   是内层目标的用户名
# 这个特征比命令行的写法稳定得多。
_PAIR_RE = re.compile(r"(?P<user>[A-Za-z0-9][\w.\-]*)@(?P<host>[A-Za-z0-9][A-Za-z0-9.\-]*)")
_PORT_RE = re.compile(r"-p\s+(\d+)")

# 串口控制台的跳板固定走 443；命令里没写 -p 时用它兜底
_DEFAULT_PROXY_PORT = 443


def parse_console_string(connection_string: str, instance_id: str = "") -> dict:
    """从 OCI 给的 ssh 命令里解析出两跳的信息。

    不把这条命令交给 shell 执行——那等于让外部字符串进 shell。

    **只从串里解析跳板那一跳**。目标那一跳（实例 OCID @ 同一个网关）是调用方
    本来就知道的信息，没必要再从字符串里抠：真实环境里遇到过串被截断、
    或者外层那段写法对不上，导致「只认出一跳」而整个功能不可用。
    能少依赖一点字符串格式，就少一处会坏的地方。
    """
    s = " ".join((connection_string or "").split())
    pairs = list(_PAIR_RE.finditer(s))
    if not pairs:
        raise OCIError(
            "串口控制台连接串里找不到 user@host，没法自动连接。"
            f"实际内容：{s[:400] or '(空)'}")

    # 跳板：优先认 console connection 的 OCID，认不出就取第一个
    jump = next((m for m in pairs
                 if "instanceconsoleconnection" in m.group("user")), pairs[0])

    # 目标用户：优先用调用方给的实例 OCID；没有再从串里找一个不同于跳板的
    target_user = (instance_id or "").strip()
    target_host = jump.group("host")
    if not target_user:
        other = next((m for m in pairs
                      if m.group("user") != jump.group("user")), None)
        if other is None:
            raise OCIError(
                "连接串里只有跳板这一跳，又没拿到实例 OCID，没法组出目标。"
                f"实际内容：{s[:400]}")
        target_user = other.group("user")
        target_host = other.group("host")
    else:
        # 串里若确实带了目标那一跳，用它的主机名（通常和跳板同一个网关）
        other = next((m for m in pairs if m.group("user") == target_user), None)
        if other is not None:
            target_host = other.group("host")

    # 端口：串口控制台里 -p 只属于跳板（443），外层那跳永远是 22。
    # 不按位置去猜哪个 -p 归谁——顺序颠倒的写法会把它算到外层头上。
    ports = _PORT_RE.findall(s)
    proxy_port = int(ports[0]) if ports else _DEFAULT_PROXY_PORT
    target_port = int(ports[1]) if len(ports) > 1 else 22

    return {
        "proxy_user": jump.group("user"),
        "proxy_host": jump.group("host"),
        "proxy_port": proxy_port,
        "target_user": target_user,
        "target_host": target_host,
        "target_port": target_port,
        "raw": s,
    }


# ---- 建立 SSH 会话 ----

# paramiko 2.9 起，RSA 私钥默认按 rsa-sha2-512/256 去认证。
# Oracle 串口控制台的网关是个老实现，只认 SHA-1 的 ssh-rsa，
# 于是认证会莫名其妙地失败——报的还是「认证失败」，很容易被当成密钥不对。
# 先按默认方式试，失败了再退回 SHA-1 重试一次。
_SHA1_FALLBACK = {"pubkeys": ["rsa-sha2-512", "rsa-sha2-256"]}


def supports_ssh_rsa_host_key() -> bool:
    """当前 paramiko 认不认 ssh-rsa 这种主机密钥算法。

    paramiko 5 把它整个移除了（SHA-1 已不安全），可 Oracle 串口控制台的
    网关只提供这一种，握手会直接失败：
        Incompatible ssh peer (no acceptable host key)
    实测 5.0 必挂、4.0 正常，所以 requirements 把 paramiko 钉在 <5。
    这里再兜一道，万一环境里装成了 5.x，至少能说清原因。
    """
    if paramiko is None:
        return False
    return "ssh-rsa" in getattr(paramiko.Transport, "_preferred_keys", ())


def _host_key_hint() -> str:
    if supports_ssh_rsa_host_key():
        return ""
    ver = getattr(paramiko, "__version__", "?")
    return (f"（当前 paramiko {ver} 不支持 ssh-rsa 主机密钥，而 Oracle 网关只提供这一种。"
            "把依赖降到 5.0 以下即可：pip install 'paramiko<5'，或重新执行 update.sh）")


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


def _tunnel_targets(info: dict) -> list:
    """direct-tcpip 要连到哪儿，按可能性从高到低排。

    实测 (网关主机, 22) 会被网关以 CONNECT_FAILED 拒掉——它确实去连了，
    但连不上。Oracle 自己的 VNC 命令里写的是 ``-L 5900:<实例OCID>:5900``，
    也就是把**实例 OCID 当作隧道的目标主机**；串口控制台多半同理。

    没法在真实租户上逐一验证，所以挨个试，并把试过的都记下来放进报错。
    """
    targets = []
    if info.get("target_user"):
        # Oracle 的写法：拿 OCID 当主机名
        targets.append((info["target_user"], 22))
    # %h:%p 字面展开的结果
    targets.append((info["target_host"], info.get("target_port") or 22))
    # 去重但保持顺序
    seen, out = set(), []
    for t in targets:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _console_chain(info: dict, pkey, timeout: int, disabled=None):
    """跳板 → direct-tcpip → 内层 SSH，等价于 ssh 的 ProxyCommand -W。"""
    transports = []
    try:
        jump = _auth((info["proxy_host"], info["proxy_port"]),
                     info["proxy_user"], pkey, timeout, disabled)
        transports.append(jump)

        tunnel, tried = None, []
        for host, port in _tunnel_targets(info):
            try:
                tunnel = jump.open_channel("direct-tcpip", (host, port), ("127.0.0.1", 0))
                break
            except Exception as e:  # noqa: BLE001 - 换下一个候选再试
                tried.append(f"{host}:{port} -> {_describe(e)}")
        if tunnel is None:
            raise OCIError(
                "跳板登录成功了，但开隧道被拒绝。试过这些目标：" + "；".join(tried))

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


def open_console(connection_string: str, pkey, cols: int = 80, rows: int = 24,
                 timeout: int = 20, instance_id: str = "") -> Session:
    """走 Oracle 串口控制台跳板。"""
    if paramiko is None:
        raise OCIError(f"服务端缺少 paramiko（{PARAMIKO_ERROR}）")
    info = parse_console_string(connection_string, instance_id)
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
    except OCIError:
        # 已经是给人看的话了（比如开隧道被拒），原样往上抛
        raise
    except Exception as e:  # noqa: BLE001
        raise OCIError(
            f"连接串口控制台失败：{_describe(e)}。{_host_key_hint()}"
            f"（跳板 {info['proxy_host']}:{info['proxy_port']}，"
            "如果是超时或拒绝连接，说明面板所在服务器出不去这个地址）") from None
    return Session(_shell(transports[-1], cols, rows), transports)
