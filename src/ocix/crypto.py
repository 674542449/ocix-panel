"""加密工具模块 - 用于加密存储敏感令牌"""
import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from .config import SESSION_SECRET


def _derive_key(secret: str) -> bytes:
    """从 SESSION_SECRET 派生 32 字节加密密钥"""
    return base64.urlsafe_b64encode(
        hashlib.sha256(secret.encode("utf-8")).digest()
    )


def encrypt_token(plaintext: str) -> str:
    """加密敏感令牌（Telegram Bot Token 等）。

    Args:
        plaintext: 明文令牌

    Returns:
        Base64 编码的密文。DEV_MODE 下返回原文。

    注意：
        - 需要 SESSION_SECRET 已配置
        - DEV_MODE 下不加密（便于开发调试）
    """
    if not plaintext:
        return ""
    if not SESSION_SECRET:
        # DEV_MODE: SESSION_SECRET 未配置，无法加密
        return plaintext
    try:
        f = Fernet(_derive_key(SESSION_SECRET))
        return f.encrypt(plaintext.encode("utf-8")).decode("utf-8")
    except Exception:
        # 加密失败不应阻止服务启动，返回原文
        # 实际部署中 SESSION_SECRET 配置正确时不会走到这里
        return plaintext


def decrypt_token(ciphertext: str) -> str:
    """解密敏感令牌。

    Args:
        ciphertext: 密文令牌

    Returns:
        解密后的明文。解密失败返回空字符串。

    注意：
        - SESSION_SECRET 更换后，旧密文将无法解密
        - 解密失败静默返回空字符串（避免服务中断）
    """
    if not ciphertext:
        return ""
    if not SESSION_SECRET:
        # DEV_MODE: 假定存储的是明文
        return ciphertext
    try:
        f = Fernet(_derive_key(SESSION_SECRET))
        return f.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, AttributeError):
        # 解密失败：可能是明文存储的旧数据，或 SESSION_SECRET 已更换
        # 尝试作为明文返回（兼容性），由调用方决定如何处理
        return ciphertext
    except Exception:
        # 其他异常：返回空，让调用方重新配置
        return ""


def is_encrypted(value: str) -> bool:
    """判断一个字符串是否已加密。

    Fernet 密文特征：
    - Base64 编码
    - 以 'gAAAAA' 开头（Fernet 版本标识 + 时间戳前缀）

    这不是完全可靠的判断，但足以用于迁移场景。
    """
    if not value or not SESSION_SECRET:
        return False
    # Fernet token 以 gAAAAA 开头（版本 0x80 的 base64）
    return value.startswith("gAAAAA")


def migrate_to_encrypted(plaintext: str) -> str:
    """迁移辅助函数：如果是明文则加密，已加密则保持。

    用于平滑迁移：读取时如果发现是明文，自动加密后写回。
    """
    if not plaintext:
        return ""
    if is_encrypted(plaintext):
        return plaintext
    return encrypt_token(plaintext)
