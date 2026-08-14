"""网页终端：票据、私钥解析、连接串解析，以及对着一台真 SSH 服务端跑通桥接。

桥接这段不能只靠 mock——paramiko 的握手、认证、PTY 都是真行为，
所以这里用 paramiko 自己起一个最小 SSH 服务端，端到端验证。
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from ocix import terminal
from ocix.common import OCIError

paramiko = pytest.importorskip("paramiko")


# ── 票据 ──

def test_ticket_is_single_use():
    t = terminal.issue_ticket("admin")
    assert terminal.consume_ticket(t) == "admin"
    assert terminal.consume_ticket(t) is None, "票据必须用一次就作废"


def test_unknown_ticket_is_rejected():
    assert terminal.consume_ticket("nope") is None
    assert terminal.consume_ticket("") is None


def test_expired_ticket_is_rejected():
    t = terminal.issue_ticket("admin")
    # 把到期时间拨到过去，比等 30 秒实在
    with terminal._ticket_lock:
        user, _ = terminal._tickets[t]
        terminal._tickets[t] = (user, 0)
    assert terminal.consume_ticket(t) is None


def test_ws_requires_a_ticket(app_client):
    """没票据的 WebSocket 必须被拒，否则终端就是裸奔的。"""
    from starlette.websockets import WebSocketDisconnect
    with pytest.raises(WebSocketDisconnect):
        with app_client.websocket_connect("/api/terminal/ws"):
            pass


def test_ticket_endpoint_requires_auth(anon_client):
    assert anon_client.post("/api/terminal/ticket").status_code == 401


# ── 私钥 ──

def _gen_key():
    return paramiko.RSAKey.generate(2048)


def _key_text(key, password=None):
    import io as _io
    buf = _io.StringIO()
    key.write_private_key(buf, password=password)
    return buf.getvalue()


def test_load_rsa_key():
    key = _gen_key()
    loaded = terminal.load_private_key(_key_text(key))
    assert loaded.get_base64() == key.get_base64()


def test_load_key_with_passphrase():
    key = _gen_key()
    loaded = terminal.load_private_key(_key_text(key, "s3cret"), "s3cret")
    assert loaded.get_base64() == key.get_base64()


def test_encrypted_key_without_passphrase_says_so():
    key = _gen_key()
    with pytest.raises(OCIError, match="口令"):
        terminal.load_private_key(_key_text(key, "s3cret"))


def test_public_key_is_rejected_with_a_clear_message():
    """贴成 .pub 是最容易犯的错，报错要说清楚。"""
    with pytest.raises(OCIError, match="不是 .pub"):
        terminal.load_private_key("ssh-rsa AAAAB3NzaC1yc2E test@host")


def test_empty_key_is_rejected():
    with pytest.raises(OCIError, match="请提供私钥"):
        terminal.load_private_key("")


# ── 串口控制台连接串 ──
#
# 真实环境里报过「格式不认识」：早先的实现去匹配 ssh 命令的形状
# （ProxyCommand=、-W %h:%p 的位置…），而各区域 / 各版本写法并不统一。
# 现在改成只认 user@host，按 OCID 类型区分两跳——这个特征稳定得多。

CC = "ocid1.instanceconsoleconnection.oc1.phx.aaaaabbbb"
IN = "ocid1.instance.oc1.phx.ccccdddd"
GW = "instance-console.us-phoenix-1.oci.oraclecloud.com"

VARIANTS = {
    "文档标准型": f'ssh -o ProxyCommand="ssh -W %h:%p -p 443 {CC}@{GW}" {IN}@{GW}',
    "单引号": f"ssh -o ProxyCommand='ssh -W %h:%p -p 443 {CC}@{GW}' {IN}@{GW}",
    "-o 整体加引号": f'ssh -o "ProxyCommand=ssh -W %h:%p -p 443 {CC}@{GW}" {IN}@{GW}',
    "带 -i 私钥路径": (f'ssh -i ~/.ssh/id_rsa -o ProxyCommand="ssh -i ~/.ssh/id_rsa '
                  f'-W %h:%p -p 443 {CC}@{GW}" {IN}@{GW}'),
    "带 UserKnownHostsFile": (f'ssh -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no '
                              f'-o ProxyCommand="ssh -W %h:%p -p 443 {CC}@{GW}" {IN}@{GW}'),
    "-J 跳板写法": f'ssh -J {CC}@{GW}:443 {IN}@{GW}',
    "老域名（无 .oci）": (f'ssh -o ProxyCommand="ssh -W %h:%p -p 443 '
                     f'{CC}@instance-console.us-phoenix-1.oraclecloud.com" '
                     f'{IN}@instance-console.us-phoenix-1.oraclecloud.com'),
    "折行与多空格": f'ssh -o ProxyCommand="ssh   -W %h:%p  -p 443\n    {CC}@{GW}"\n   {IN}@{GW}',
    "顺序颠倒（目标在前）": f'ssh {IN}@{GW} -o ProxyCommand="ssh -W %h:%p -p 443 {CC}@{GW}"',
    "没写 -p（默认 443）": f'ssh -o ProxyCommand="ssh -W %h:%p {CC}@{GW}" {IN}@{GW}',
}

BASE = VARIANTS["文档标准型"]


@pytest.mark.parametrize("name", list(VARIANTS))
def test_parse_console_string_variants(name):
    info = terminal.parse_console_string(VARIANTS[name], IN)
    assert info["proxy_user"] == CC, name
    assert info["target_user"] == IN, name
    assert info["proxy_host"].startswith("instance-console"), name
    assert info["proxy_port"] == 443, name
    assert info["target_port"] == 22, name


def test_roles_come_from_the_ocid_not_the_position():
    """靠 OCID 类型认角色，所以目标写在前面也不会搞反。"""
    info = terminal.parse_console_string(VARIANTS["顺序颠倒（目标在前）"], IN)
    assert info["proxy_user"] == CC
    assert info["target_user"] == IN


def test_falls_back_to_position_when_ocids_are_unfamiliar():
    """认不出 OCID 时退回位置：第一个当跳板，最后一个当目标。"""
    s = 'ssh -o ProxyCommand="ssh -W %h:%p -p 443 jump@gw.example.com" target@gw.example.com'
    info = terminal.parse_console_string(s)
    assert info["proxy_user"] == "jump"
    assert info["target_user"] == "target"


def test_console_regexes_have_no_control_characters():
    """回归：正则里混进 0x08 会让所有连接串都匹配不上。"""
    for rx in (terminal._PAIR_RE, terminal._PORT_RE):
        bad = [hex(ord(c)) for c in rx.pattern if ord(c) < 32]
        assert not bad, f"{rx.pattern!r} 里有控制字符 {bad}"


def test_error_includes_the_actual_string():
    """解析不了时要把原文带出来，否则没法判断是什么格式。"""
    with pytest.raises(OCIError) as exc:
        terminal.parse_console_string("ssh no-at-sign-here")
    assert "no-at-sign-here" in str(exc.value)


def test_single_hop_string_is_rejected():
    """只有一跳、又没给实例 OCID 时才该报错。"""
    with pytest.raises(OCIError, match="只有跳板这一跳"):
        terminal.parse_console_string("ssh someone@example.com")


# 用户真实环境（us-sanjose-1）报过「只认出一跳」：连接串里只解析到跳板。
# 目标那一跳的用户名就是实例 OCID——调用方本来就知道，不该依赖字符串。
REAL_CC = ("ocid1.instanceconsoleconnection.oc1.us-sanjose-1."
           "anzwuljr2ub7k6ycbwgutzvzpuwwlxkpbrxqtas2rgyyew6eluzrjocov44a")
REAL_GW = "instance-console.us-sanjose-1.oci.oraclecloud.com"
REAL_IN = "ocid1.instance.oc1.us-sanjose-1.anzwuljr2ub7k6ycsomeinstanceocid0001"
ONLY_JUMP = f"ssh -o ProxyCommand='ssh -W %h:%p -p 443 {REAL_CC}@{REAL_GW}"


def test_real_string_with_only_the_jump_hop():
    """回归：串里只有跳板时，靠传入的实例 OCID 也要能组出两跳。"""
    info = terminal.parse_console_string(ONLY_JUMP, REAL_IN)
    assert info["proxy_user"] == REAL_CC
    assert info["proxy_host"] == REAL_GW
    assert info["proxy_port"] == 443
    assert info["target_user"] == REAL_IN
    assert info["target_host"] == REAL_GW   # 同一个网关
    assert info["target_port"] == 22


def test_jump_ocid_is_not_mistaken_for_the_target():
    """instanceconsoleconnection 里也含 'instance'，不能把跳板当成目标。"""
    info = terminal.parse_console_string(ONLY_JUMP, REAL_IN)
    assert info["proxy_user"] != info["target_user"]


def test_instance_id_wins_over_whatever_is_in_the_string():
    """调用方给了实例 OCID 就以它为准，不受串里内容影响。"""
    other = f"ssh -o ProxyCommand='ssh -W %h:%p -p 443 {REAL_CC}@{REAL_GW}' stale@{REAL_GW}"
    assert terminal.parse_console_string(other, REAL_IN)["target_user"] == REAL_IN


def test_empty_string_is_rejected():
    with pytest.raises(OCIError, match="找不到 user@host"):
        terminal.parse_console_string("")


# ── RSA SHA-1 回退 ──

def test_console_retries_with_sha1_when_auth_fails(monkeypatch):
    """Oracle 网关只认 SHA-1 的 ssh-rsa；第一次认证失败要自动退回去再试。"""
    calls = []

    def fake_auth(sock, username, pkey, timeout, disabled=None):
        calls.append(disabled)
        if disabled is None:
            raise paramiko.AuthenticationException("Authentication failed.")
        return _FakeTransport()

    monkeypatch.setattr(terminal, "_auth", fake_auth)
    monkeypatch.setattr(terminal, "_shell", lambda t, c, r: "chan")
    sess = terminal.open_console(BASE, object())
    assert sess.channel == "chan"
    # 第一次不带限制，随后带上 SHA-1 回退
    assert calls[0] is None
    assert terminal._SHA1_FALLBACK in calls


def test_console_reports_original_error_when_both_attempts_fail(monkeypatch):
    """两次都失败时要把原始信息带出来，否则没法排查。"""
    def always_fail(*a, **kw):
        raise paramiko.AuthenticationException("Authentication failed.")

    monkeypatch.setattr(terminal, "_auth", always_fail)
    with pytest.raises(OCIError) as exc:
        terminal.open_console(BASE, object())
    assert "创建这条连接时填的公钥" in str(exc.value)
    assert "Authentication failed" in str(exc.value)


def test_console_network_error_names_the_gateway(monkeypatch):
    """连不上跳板时要说清是哪个地址，好判断是不是出站被挡。"""
    def boom(*a, **kw):
        raise OSError("timed out")

    monkeypatch.setattr(terminal, "_auth", boom)
    with pytest.raises(OCIError) as exc:
        terminal.open_console(BASE, object())
    assert GW in str(exc.value)
    assert "443" in str(exc.value)


class _FakeTransport:
    def open_channel(self, *a, **kw):
        return "tunnel"

    def close(self):
        pass


# ── 端到端：对着一台真 SSH 服务端 ──

class _Server(paramiko.ServerInterface):
    def __init__(self, authorized_key):
        self.authorized = authorized_key
        self.shell = threading.Event()

    def check_auth_publickey(self, username, key):
        ok = key.get_base64() == self.authorized.get_base64()
        return paramiko.AUTH_SUCCESSFUL if ok else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "publickey"

    def check_channel_request(self, kind, chanid):
        return (paramiko.OPEN_SUCCEEDED if kind == "session"
                else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)

    def check_channel_pty_request(self, *a, **kw):
        return True

    def check_channel_shell_request(self, channel):
        self.shell.set()
        return True


@pytest.fixture()
def ssh_server():
    """一台最小 SSH 服务端：认一把指定公钥，登录后回显收到的内容。"""
    host_key = paramiko.RSAKey.generate(2048)
    client_key = paramiko.RSAKey.generate(2048)
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    state = {"key": client_key, "port": port, "banner": "OCIX-TEST> "}

    def serve_one():
        try:
            conn, _ = sock.accept()
            t = paramiko.Transport(conn)
            t.add_server_key(host_key)
            server = _Server(client_key)
            t.start_server(server=server)
            chan = t.accept(10)
            if chan is None:
                return
            server.shell.wait(10)
            chan.send(state["banner"].encode())
            # 简单回显，好验证双向都通
            deadline = time.time() + 10
            while time.time() < deadline:
                if chan.recv_ready():
                    data = chan.recv(1024)
                    if not data:
                        break
                    chan.send(b"echo:" + data)
                else:
                    time.sleep(0.02)
            chan.close()
        except Exception:  # noqa: BLE001
            pass

    def serve():
        # 认证失败会触发一次 SHA-1 回退重连，所以这里要能接多次
        while True:
            try:
                serve_one()
            except OSError:
                break

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    yield state
    try:
        sock.close()
    except OSError:
        pass


def test_direct_ssh_end_to_end(ssh_server):
    """真的连上去、拿到 PTY、双向收发。"""
    sess = terminal.open_direct("127.0.0.1", ssh_server["port"], "ubuntu",
                                ssh_server["key"], cols=100, rows=30)
    try:
        got = b""
        deadline = time.time() + 10
        while time.time() < deadline and b"OCIX-TEST>" not in got:
            if sess.channel.recv_ready():
                got += sess.channel.recv(1024)
            else:
                time.sleep(0.02)
        assert b"OCIX-TEST>" in got, "没收到服务端 banner"

        sess.channel.send("whoami\n")
        got = b""
        deadline = time.time() + 10
        while time.time() < deadline and b"echo:" not in got:
            if sess.channel.recv_ready():
                got += sess.channel.recv(1024)
            else:
                time.sleep(0.02)
        assert b"echo:whoami" in got, "发出去的内容没有回来"
    finally:
        sess.close()


def test_direct_ssh_wrong_key_is_reported(ssh_server):
    other = paramiko.RSAKey.generate(2048)
    with pytest.raises(OCIError, match="认证失败"):
        terminal.open_direct("127.0.0.1", ssh_server["port"], "ubuntu", other)


def test_direct_ssh_without_host_is_reported():
    with pytest.raises(OCIError, match="没有公网 IP"):
        terminal.open_direct("", 22, "ubuntu", None)


def test_unreachable_host_is_reported():
    # 关掉的端口：连不上要给可读的话，而不是抛原始异常
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    with pytest.raises(OCIError, match="连不上"):
        terminal.open_direct("127.0.0.1", port, "ubuntu",
                             paramiko.RSAKey.generate(2048), timeout=3)


def test_resize_does_not_raise_after_close(ssh_server):
    """会话关掉后前端可能还会发一次 resize，不能因此炸掉。"""
    sess = terminal.open_direct("127.0.0.1", ssh_server["port"], "ubuntu",
                                ssh_server["key"])
    sess.close()
    sess.resize(120, 40)   # 不抛异常即通过


# ── direct-tcpip 目标的候选顺序 ──
#
# 真实环境报过 ChannelException(2, 'Connect failed')：跳板登录成功了，
# 但网关拒绝把隧道开到「网关主机:22」。Oracle 自己的 VNC 命令写的是
# -L 5900:<实例OCID>:5900，即把 OCID 当目标主机，串口控制台多半同理。

def test_tunnel_tries_the_instance_ocid_first():
    info = terminal.parse_console_string(BASE, IN)
    targets = terminal._tunnel_targets(info)
    assert targets[0] == (IN, 22), "应当先试实例 OCID 当目标主机"
    assert (info["target_host"], 22) in targets, "字面展开的目标也要作为备选"


def test_tunnel_targets_are_deduplicated():
    info = dict(target_user="x", target_host="x", target_port=22)
    assert terminal._tunnel_targets(info) == [("x", 22)]


def test_console_falls_back_to_the_next_tunnel_target(monkeypatch):
    """第一个目标被拒时要接着试下一个，而不是直接放弃。"""
    tried = []

    class Jump:
        def open_channel(self, kind, dest, src):
            tried.append(dest)
            if dest[0] == IN:          # 第一个候选：拒绝
                raise paramiko.ChannelException(2, "Connect failed")
            return "tunnel"            # 第二个候选：放行

        def close(self):
            pass

    def fake_auth(sock, username, pkey, timeout, disabled=None):
        return Jump() if isinstance(sock, tuple) else _FakeTransport()

    monkeypatch.setattr(terminal, "_auth", fake_auth)
    monkeypatch.setattr(terminal, "_shell", lambda t, c, r: "chan")
    sess = terminal.open_console(BASE, object(), instance_id=IN)
    assert sess.channel == "chan"
    assert len(tried) == 2, f"应当试了两个目标，实际 {tried}"


def test_console_reports_every_tunnel_target_it_tried(monkeypatch):
    """全都被拒时，报错要列出试过哪些，否则无从下手。"""
    class Jump:
        def open_channel(self, kind, dest, src):
            raise paramiko.ChannelException(2, "Connect failed")

        def close(self):
            pass

    monkeypatch.setattr(terminal, "_auth",
                        lambda sock, u, k, t, disabled=None: Jump())
    with pytest.raises(OCIError) as exc:
        terminal.open_console(BASE, object(), instance_id=IN)
    msg = str(exc.value)
    assert "跳板登录成功" in msg, "要说清楚是卡在开隧道而不是登录"
    assert IN in msg and GW in msg, "试过的目标都要列出来"
