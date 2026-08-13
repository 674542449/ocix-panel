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

CONSOLE_STR = (
    'ssh -o ProxyCommand="ssh -W %h:%p -p 443 '
    'ocid1.instanceconsoleconnection.oc1..abc@instance-console.us-ashburn-1.oci.oraclecloud.com" '
    'ocid1.instance.oc1..xyz@instance-console.us-ashburn-1.oci.oraclecloud.com'
)


def test_parse_console_string():
    info = terminal.parse_console_string(CONSOLE_STR)
    assert info["proxy_user"] == "ocid1.instanceconsoleconnection.oc1..abc"
    assert info["proxy_host"] == "instance-console.us-ashburn-1.oci.oraclecloud.com"
    assert info["proxy_port"] == 443
    assert info["target_user"] == "ocid1.instance.oc1..xyz"


def test_parse_console_string_with_single_quotes():
    info = terminal.parse_console_string(CONSOLE_STR.replace('"', "'"))
    assert info["proxy_port"] == 443


def test_unparseable_console_string_is_reported():
    with pytest.raises(OCIError, match="格式不认识"):
        terminal.parse_console_string("ssh someone@example.com")


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

    def serve():
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
