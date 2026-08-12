"""发往 Oracle 的文本必须是纯 ASCII。

规则描述、实例名、VCN 名会原样出现在 OCI 控制台和 oci CLI 输出里，
混进中文在不少工具里会变成乱码或问号，排查问题时对不上号。
"""

import pytest
from pydantic import ValidationError

from fakes import FakeBackend
from ocix import oci_helpers as H
from ocix.backends import set_backend
from ocix.schemas import CreateInstanceRequest, CreateNetworkRequest, PortRuleRequest

PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI test@host"


def non_ascii(value: str) -> str:
    """返回超出可打印 ASCII 的字符，空串表示干净。"""
    return "".join(dict.fromkeys(c for c in (value or "") if not 0x20 <= ord(c) <= 0x7E))


@pytest.fixture()
def fake():
    b = FakeBackend()
    set_backend(b)
    original = H.tenancy_of
    H.tenancy_of = lambda p: "root"
    yield b
    H.tenancy_of = original
    set_backend(None)


def _all_strings(obj):
    """把任意嵌套结构里的字符串全都摊平出来。"""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _all_strings(k)
            yield from _all_strings(v)
    elif isinstance(obj, (list, tuple, set)):
        for item in obj:
            yield from _all_strings(item)


def assert_clean(payload, what):
    dirty = [(s, non_ascii(s)) for s in _all_strings(payload) if non_ascii(s)]
    assert not dirty, f"{what} 里有非 ASCII 文本发往 Oracle: {dirty}"


# ── 面板自己写进安全列表的规则 ──

def test_open_all_ports_writes_ascii_only(fake):
    H.open_all_ports_on_subnet("P", "subnet1", include_ipv6=True)
    assert fake.ingress_rules, "应当写入了规则"
    assert_clean(fake.ingress_rules, "一键放行")


def test_clear_rules_keeps_ascii_ssh_rule(fake):
    H.clear_ingress_rules("P", "subnet1", keep_ssh=True)
    assert_clean(fake.ingress_rules, "清空规则时保留的 SSH 规则")


def test_add_port_rule_default_description_is_ascii(fake):
    H.add_port_rule("P", "subnet1", "TCP", 8080, 8090, "0.0.0.0/0")
    assert_clean(fake.ingress_rules, "新增端口规则")


def test_no_rule_description_mentions_chinese(fake):
    """把上面几条写出来的描述汇总再看一遍，防止将来有人加回中文。"""
    H.open_all_ports_on_subnet("P", "subnet1", include_ipv6=True)
    descriptions = [r.get("description", "") for r in fake.ingress_rules]
    assert descriptions and all(d.startswith("ocix: ") for d in descriptions), descriptions
    assert_clean(descriptions, "规则描述")


# ── 用户填进来的文本不能带着中文发出去 ──

def _instance(name):
    return CreateInstanceRequest(
        profile="P", compartment_id="c1", display_name=name,
        availability_domain="AD-1", image_id="img1", shape="VM.Standard.E2.1.Micro",
        ssh_public_key=PUBKEY,
    )


@pytest.mark.parametrize("name", ["我的服务器", "web-01（主）", "server—dash", "ok✓"])
def test_instance_name_rejects_non_ascii(name):
    with pytest.raises(ValidationError, match="Oracle 控制台"):
        _instance(name)


@pytest.mark.parametrize("name", ["payments-api-01", "web_01", "srv.02", "A-Z 0-9"])
def test_instance_name_accepts_plain_ascii(name):
    assert _instance(name).display_name == name.strip()


def test_instance_name_is_trimmed():
    assert _instance("  web-01  ").display_name == "web-01"


def test_blank_instance_name_is_rejected():
    with pytest.raises(ValidationError):
        _instance("   ")


def test_firewall_rule_description_rejects_non_ascii():
    with pytest.raises(ValidationError, match="Oracle 控制台"):
        PortRuleRequest(profile="P", instance_id="i1", description="网站端口")


def test_firewall_rule_description_allows_ascii_and_empty():
    assert PortRuleRequest(profile="P", instance_id="i1").description == ""
    assert PortRuleRequest(profile="P", instance_id="i1",
                           description="web").description == "web"


def test_network_name_rejects_non_ascii():
    with pytest.raises(ValidationError, match="Oracle 控制台"):
        CreateNetworkRequest(profile="P", compartment_id="c1", name="我的网络")


def test_error_message_names_the_offending_characters():
    """报错要指名道姓，用户才知道删哪个字。"""
    with pytest.raises(ValidationError) as exc:
        _instance("web-01 主机")
    assert "主机" in str(exc.value)


# ── 兜底：源码里发往 OCI 的字面量不许再混中文 ──

def test_outbound_literals_in_source_are_ascii():
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "ocix" / "oci_helpers.py"
           ).read_text(encoding="utf-8")
    # 只看会被塞进 OCI 请求体的那些 key，注释和报错信息保持中文没问题
    bad = [m.group(0) for m in
           re.finditer(r'"(?:description|displayName|display_name)"\s*:\s*[^,\n]+', src)
           if non_ascii(m.group(0))]
    assert not bad, f"这些字面量会带着中文发往 Oracle: {bad}"


# ── 校验错误要能被前端直接显示 ──

def _launch(client, **over):
    body = {"profile": "EXISTING", "compartment_id": "c1", "display_name": "web-01",
            "availability_domain": "AD-1", "image_id": "i1",
            "shape": "VM.Standard.E2.1.Micro", "ssh_public_key": PUBKEY}
    body.update(over)
    return client.post("/api/provision/instances", json=body)


def test_validation_error_detail_is_a_plain_string(app_client):
    """FastAPI 默认返回 [{loc,msg,...}] 数组，前端取 detail 会弹出 [object Object]。"""
    r = _launch(app_client, display_name="我的服务器")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str), detail
    assert "Oracle" in detail and "我的服务器"[0] in detail


def test_validation_error_drops_pydantic_prefix(app_client):
    r = _launch(app_client, ssh_public_key="not-a-key")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert isinstance(detail, str)
    assert not detail.startswith("Value error")
    assert "SSH" in detail


def test_multiple_field_errors_are_all_reported(app_client):
    r = _launch(app_client, display_name="服务器", ssh_public_key="nope")
    detail = r.json()["detail"]
    assert isinstance(detail, str)
    # 多个字段出错时带上字段名，才知道各自说的是谁
    assert "display_name" in detail and "ssh_public_key" in detail
