"""安全增强测试"""
import pytest


def test_telegram_token_encryption():
    """测试 Telegram Token 加密存储"""
    from ocix.crypto import encrypt_token, decrypt_token, is_encrypted
    from ocix.config import SESSION_SECRET

    if not SESSION_SECRET:
        pytest.skip("DEV_MODE: SESSION_SECRET 未配置")

    # 测试加密解密
    plaintext = "1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
    encrypted = encrypt_token(plaintext)

    assert encrypted != plaintext, "加密后应与原文不同"
    assert is_encrypted(encrypted), "应识别为已加密"

    decrypted = decrypt_token(encrypted)
    assert decrypted == plaintext, "解密后应还原为原文"

    # 测试空值
    assert encrypt_token("") == ""
    assert decrypt_token("") == ""

    # 测试错误密文
    assert decrypt_token("invalid_ciphertext") == "invalid_ciphertext"  # 兼容性：返回原文


def test_error_message_sanitization():
    """测试错误消息过滤敏感信息"""
    from ocix.sanitize import sanitize_error_message

    # OCID 过滤
    msg = "Error with ocid1.instance.oc1.iad.anuwcljsexampleuniqueID123456789012345678901234567890"
    sanitized = sanitize_error_message(msg)
    assert "ocid1.***" in sanitized
    assert "anuwcljsexample" not in sanitized

    # Windows 路径过滤
    msg = "Config not found at C:\\Users\\admin\\.oci\\config"
    sanitized = sanitize_error_message(msg)
    assert "C:\\Users" not in sanitized
    assert "***" in sanitized

    # Unix 路径过滤
    msg = "Key file /home/ubuntu/.oci/key.pem not found"
    sanitized = sanitize_error_message(msg)
    assert "/home/ubuntu" not in sanitized

    # 内网 IP 过滤
    msg = "Connection failed to 192.168.1.100"
    sanitized = sanitize_error_message(msg)
    assert "192.168.1.100" not in sanitized
    assert "10.x.x.x" in sanitized

    # JWT Token 过滤
    msg = "Invalid token: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    sanitized = sanitize_error_message(msg)
    assert "eyJhbGci" not in sanitized


def test_sensitive_dict_filtering():
    """测试字典敏感字段过滤"""
    from ocix.sanitize import sanitize_dict

    data = {
        "username": "admin",
        "password": "secret123",
        "api_key": "sk-1234567890",
        "config": {
            "bot_token": "1234:ABCDEF",
            "chat_id": "123456",
        },
        "items": [
            {"name": "test", "secret": "hidden"}
        ]
    }

    filtered = sanitize_dict(data)

    assert filtered["username"] == "admin"
    assert filtered["password"] == "***"
    assert filtered["api_key"] == "***"
    assert filtered["config"]["bot_token"] == "***"
    assert filtered["config"]["chat_id"] == "123456"
    assert filtered["items"][0]["secret"] == "***"


def test_db_column_whitelist():
    """确保 upsert_profile 拒绝未知列名"""
    from ocix.db import upsert_profile, init_db

    init_db()

    # 正常列名应该成功
    upsert_profile("test_profile", region="us-ashburn-1")

    # 恶意列名应该失败
    with pytest.raises(ValueError, match="不存在这些列"):
        upsert_profile("test_profile", malicious_column="DROP TABLE profiles")

    with pytest.raises(ValueError, match="不存在这些列"):
        upsert_profile("test_profile", tier_data="ok", evil="bad")


def test_password_strength():
    """测试密码强度校验"""
    from ocix.schemas import CreateInstanceRequest

    # root 密码至少 12 位
    with pytest.raises(ValueError, match="至少 12 位"):
        CreateInstanceRequest(
            profile="test",
            compartment_id="ocid1.compartment.test",
            display_name="test",
            availability_domain="AD-1",
            image_id="ocid1.image.test",
            shape="VM.Standard.E2.1.Micro",
            root_password="short"  # 太短
        )

    # 控制字符不允许
    with pytest.raises(ValueError, match="控制字符"):
        CreateInstanceRequest(
            profile="test",
            compartment_id="ocid1.compartment.test",
            display_name="test",
            availability_domain="AD-1",
            image_id="ocid1.image.test",
            shape="VM.Standard.E2.1.Micro",
            root_password="password\x00with\x1bnull"
        )


def test_ssh_key_validation():
    """测试 SSH 公钥格式验证"""
    from ocix.schemas import CreateInstanceRequest

    # 私钥应该被拒绝
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        CreateInstanceRequest(
            profile="test",
            compartment_id="ocid1.compartment.test",
            display_name="test",
            availability_domain="AD-1",
            image_id="ocid1.image.test",
            shape="VM.Standard.E2.1.Micro",
            ssh_public_key="-----BEGIN RSA PRIVATE KEY-----\nMIIE..."
        )
    # 检查错误消息包含关键字（支持中文编码问题）
    error_msg = str(exc_info.value)
    assert "ssh" in error_msg.lower() or "key" in error_msg.lower()

    # 格式错误的公钥
    with pytest.raises(ValidationError) as exc_info:
        CreateInstanceRequest(
            profile="test",
            compartment_id="ocid1.compartment.test",
            display_name="test",
            availability_domain="AD-1",
            image_id="ocid1.image.test",
            shape="VM.Standard.E2.1.Micro",
            ssh_public_key="not-a-valid-key"
        )
    error_msg = str(exc_info.value)
    assert "ssh" in error_msg.lower() or "format" in error_msg.lower()

    # 至少需要一种登录方式
    with pytest.raises(ValueError, match="至少要给一样登录方式"):
        CreateInstanceRequest(
            profile="test",
            compartment_id="ocid1.compartment.test",
            display_name="test",
            availability_domain="AD-1",
            image_id="ocid1.image.test",
            shape="VM.Standard.E2.1.Micro",
            ssh_public_key="",
            root_password=""
        )


def test_rate_limiter_bypass_prevention():
    """测试限流器无法通过伪造 IP 绕过"""
    from ocix.security import client_ip
    from unittest.mock import MagicMock

    # 不信任代理时，X-Forwarded-For 应被忽略
    request = MagicMock()
    request.headers.get.return_value = "1.1.1.1, 2.2.2.2, 3.3.3.3"
    request.client.host = "4.4.4.4"

    # 默认 TRUST_PROXY=False
    ip = client_ip(request)
    assert ip == "4.4.4.4", "不信任代理时应使用 client.host"


def test_jwt_token_epoch():
    """测试 JWT epoch 机制（改密后所有令牌失效）"""
    from ocix.security import create_token, set_admin_password, bootstrap_admin
    from ocix.db import init_db
    from jose import jwt
    from ocix.config import SESSION_SECRET

    if not SESSION_SECRET:
        pytest.skip("DEV_MODE: SESSION_SECRET 未配置")

    init_db()
    bootstrap_admin()

    # 创建令牌
    token1 = create_token("admin")
    payload1 = jwt.decode(token1, SESSION_SECRET, algorithms=["HS256"])
    epoch1 = payload1["epoch"]

    # 修改密码
    set_admin_password("new_password_12345")

    # 新令牌应有新 epoch
    token2 = create_token("admin")
    payload2 = jwt.decode(token2, SESSION_SECRET, algorithms=["HS256"])
    epoch2 = payload2["epoch"]

    assert epoch2 > epoch1, "修改密码后 epoch 应递增"


def test_global_http_exception_sanitizer(app_client):
    """测试全局异常处理器自动对 HTTPException detail 进行脱敏"""
    r = app_client.get("/api/instances", params={"profile": "INVALID_TEST_PROFILE"})
    assert r.status_code == 400
    detail = r.json().get("detail", "")
    assert "C:\\Users" not in detail
    assert "/home/" not in detail
    assert "ocid1.instance." not in detail

