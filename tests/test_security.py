"""安全相关的回归用例。

每一条都对应一个实际查出来的问题或一条必须守住的边界。
"""

import pytest

from ocix.db import upsert_profile

# 一份格式正确的配置：这样 400 只可能来自 profile 名校验，
# 而不是「配置解析失败」——否则用例看着通过，其实什么都没测到。
VALID_CONFIG = ("[X]\n"
                "user=ocid1.user.oc1..aaa\nfingerprint=aa:bb\nkey_file=/nope.pem\n"
                "tenancy=ocid1.tenancy.oc1..bbb\nregion=us-ashburn-1\n")


# ── 限流不能被伪造的 X-Forwarded-For 绕过 ──

def _login(client, headers=None):
    return client.post("/api/auth/login",
                       json={"username": "admin", "password": "wrong-on-purpose"},
                       headers=headers or {})


def test_login_rate_limit_cannot_be_bypassed_by_spoofing_xff(anon_client, monkeypatch):
    """回归（高危）：X-Forwarded-For 左侧由客户端伪造，反代把真实 IP 追加在最右。

    早先取最左边那个，攻击者每次换一个伪造 IP 就能无限爆破密码。
    """
    from ocix import security
    monkeypatch.setattr(security, "TRUST_PROXY", True)
    monkeypatch.setattr(security, "TRUSTED_PROXY_HOPS", 1)
    security.rate_limiter.hits.clear()

    saw_429 = False
    for i in range(security.LOGIN_RATE_LIMIT + 4):
        # 每次伪造一个不同的左侧 IP，右侧才是「反代观测到的」同一个真实 IP
        r = _login(anon_client, {"X-Forwarded-For": f"10.0.0.{i}, 203.0.113.9"})
        if r.status_code == 429:
            saw_429 = True
            break
    assert saw_429, "伪造 X-Forwarded-For 就能绕过登录限流"


def test_client_ip_takes_rightmost_hop(monkeypatch):
    from ocix import security

    class Req:
        def __init__(self, xff):
            self.headers = {"x-forwarded-for": xff}
            self.client = type("C", (), {"host": "172.18.0.5"})()

    monkeypatch.setattr(security, "TRUST_PROXY", True)
    monkeypatch.setattr(security, "TRUSTED_PROXY_HOPS", 1)
    assert security.client_ip(Req("1.2.3.4, 203.0.113.9")) == "203.0.113.9"
    assert security.client_ip(Req("203.0.113.9")) == "203.0.113.9"

    # 两层反代时往左再退一格
    monkeypatch.setattr(security, "TRUSTED_PROXY_HOPS", 2)
    assert security.client_ip(Req("1.2.3.4, 203.0.113.9, 10.1.1.1")) == "203.0.113.9"


def test_xff_is_ignored_when_proxy_is_not_trusted(monkeypatch):
    """没有反代时绝不能采信这个头，否则任何人都能伪装 IP。"""
    from ocix import security

    class Req:
        headers = {"x-forwarded-for": "1.2.3.4", "x-real-ip": "5.6.7.8"}
        client = type("C", (), {"host": "198.51.100.7"})()

    monkeypatch.setattr(security, "TRUST_PROXY", False)
    assert security.client_ip(Req()) == "198.51.100.7"


def test_trust_proxy_defaults_to_off():
    """安全默认：没显式声明有反代就不信任这个头。"""
    from ocix import config
    assert config.TRUST_PROXY is False


# ── 未鉴权时的信息暴露 ──

def test_health_does_not_leak_version_or_paths(anon_client):
    body = anon_client.get("/api/health").json()
    assert body == {"ok": True, "service": "ocix"}


def test_diagnostics_requires_auth(anon_client):
    assert anon_client.get("/api/diagnostics").status_code == 401


def test_diagnostics_available_after_login(app_client):
    body = app_client.get("/api/diagnostics").json()
    assert body["version"] and body["oci_config"]["profiles"] == 1


def test_api_docs_are_disabled_by_default(anon_client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = anon_client.get(path)
        # SPA 兜底会返回 index.html，总之不能是接口文档
        assert "swagger" not in r.text.lower(), path
        assert '"openapi"' not in r.text[:200].lower(), path


# ── 安全响应头 ──

@pytest.mark.parametrize("header,expected", [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
])
def test_security_headers_present(anon_client, header, expected):
    assert anon_client.get("/api/health").headers.get(header) == expected


def test_csp_blocks_framing_and_object_embeds(anon_client):
    csp = anon_client.get("/").headers.get("Content-Security-Policy", "")
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "connect-src 'self'" in csp


def test_api_responses_are_not_cached(app_client):
    r = app_client.get("/api/profiles")
    assert r.headers.get("Cache-Control") == "no-store"


# ── SQL ──

def test_upsert_profile_rejects_unknown_columns(app_client):
    """列名会拼进 SQL，占位符绑不了列名，所以必须白名单。"""
    with pytest.raises(ValueError, match="不存在这些列"):
        upsert_profile("x", **{"region": "r", "evil; DROP TABLE profiles;--": "1"})


@pytest.mark.parametrize("payload", [
    "' OR '1'='1",
    "'; DROP TABLE audit_log; --",
    "\\' UNION SELECT 1,2,3,4,5,6,7,8,9 --",
])
def test_audit_filters_are_parameterised(app_client, payload):
    """审计筛选参数直接来自 query string，注入必须打不穿。"""
    r = app_client.get("/api/audit", params={"action": payload, "result": payload})
    assert r.status_code == 200
    assert r.json()["logs"] == []
    # 表还在
    assert app_client.get("/api/audit").status_code == 200


def test_profile_name_cannot_escape_the_keys_directory(app_client):
    """profile 名会拼进私钥文件路径。"""
    for name in ("../../etc/cron.d/x", "..\\\\windows", "a/b"):
        r = app_client.post("/api/profiles/import",
                            data={"config_text": VALID_CONFIG, "profile_name": name})
        assert r.status_code == 400, name


# ── 凭据与令牌 ──

def test_jwt_algorithm_is_pinned():
    """不锁定算法会被 alg=none / 算法混淆攻击。"""
    import inspect

    from ocix import security
    src = inspect.getsource(security.get_current_user)
    assert 'algorithms=["HS256"]' in src


def test_token_from_another_secret_is_rejected(app_client):
    from jose import jwt
    forged = jwt.encode({"sub": "admin", "epoch": 1}, "attacker-secret", algorithm="HS256")
    r = app_client.get("/api/auth/me", headers={"Authorization": "Bearer " + forged})
    assert r.status_code == 401


def test_alg_none_token_is_rejected(app_client):
    import base64
    import json

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    forged = f"{b64({'alg': 'none', 'typ': 'JWT'})}.{b64({'sub': 'admin', 'epoch': 1})}."
    r = app_client.get("/api/auth/me", headers={"Authorization": "Bearer " + forged})
    assert r.status_code == 401


def test_profiles_never_return_private_key_material(app_client):
    """接口可以回私钥路径，但绝不能回内容。"""
    body = app_client.get("/api/profiles").text
    assert "BEGIN" not in body and "PRIVATE KEY" not in body


def test_admin_password_is_not_exposed_anywhere(app_client):
    for path in ("/api/auth/me", "/api/profiles", "/api/diagnostics", "/api/audit"):
        assert "devpass123" not in app_client.get(path).text, path


# ── 更新检查的目标地址不能被改到别处 ──

@pytest.mark.parametrize("bad", [
    "evil.com/x#", "../../etc", "owner/name/extra", "http://evil.com/a", "",
])
def test_github_repo_falls_back_when_malformed(bad, monkeypatch):
    """OCIX_GITHUB_REPO 会拼进检查更新的 URL，格式不对就退回默认值。"""
    import importlib

    monkeypatch.setenv("OCIX_GITHUB_REPO", bad)
    from ocix import config
    reloaded = importlib.reload(config)
    try:
        assert reloaded.GITHUB_REPO == "674542449/ocix-panel"
    finally:
        monkeypatch.delenv("OCIX_GITHUB_REPO", raising=False)
        importlib.reload(config)


# ── XSS：前端不得存在 HTML 注入点 ──

def test_frontend_has_no_html_injection_sinks():
    """Vue 的 {{ }} 会转义；一旦用上 v-html 或 dangerouslyUseHTMLString，
    实例名、错误信息这些来自 OCI 的数据就能注入脚本。"""
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1]
            / "src" / "ocix" / "web" / "index.html").read_text(encoding="utf-8")
    assert "v-html" not in html
    assert "dangerouslyUseHTMLString" not in html
    # innerHTML 只允许出现在读取自身静态模板那一处
    assert html.count("innerHTML") == 1
    assert "getElementById('app-tpl').innerHTML" in html
