"""通用工具 - 错误消息过滤"""
import re


def sanitize_error_message(msg: str) -> str:
    """移除错误消息中的敏感信息，防止信息泄露。

    过滤内容：
    - OCID (Oracle Cloud Identifier)
    - 文件系统路径
    - API 密钥特征
    - 内部 IP 地址

    Args:
        msg: 原始错误消息

    Returns:
        过滤后的安全消息
    """
    if not msg:
        return msg

    # 移除 OCID: ocid1.{resource}.{realm}.{region}.{unique-id}
    # OCID 格式：ocid1.<RESOURCE TYPE>.<REALM>.[REGION][.FUTURE USE].<UNIQUE ID>
    # UNIQUE ID 部分通常是 40-100 个字母数字字符
    msg = re.sub(
        r'ocid1\.[a-z0-9]+\.[a-z0-9]+\.[a-z0-9]+\.[a-z0-9]{40,}',
        'ocid1.***',
        msg,
        flags=re.IGNORECASE
    )

    # 移除 Windows 路径: C:\Users\...
    msg = re.sub(r'[A-Za-z]:[\\\/](?:[^\s\'"]+)', '***', msg)

    # 移除 Unix 绝对路径: /home/.oci/..., /root/..., /opt/...
    # 保留相对路径和常见的配置路径提示
    msg = re.sub(r'\/(?:home|root|opt|etc|var|usr)\/[^\s\'"]+', '***', msg)

    # 移除完整的 .oci 配置路径（但保留单独的 "oci" 关键字）
    # 只匹配明确的路径格式：/path/.oci/file 或 path/.oci/file
    msg = re.sub(r'[^\s]*\/\.oci\/[^\s]*', '***', msg)

    # 移除可能的密钥指纹 (格式: xx:xx:xx:...)
    msg = re.sub(
        r'\b[0-9a-f]{2}(?::[0-9a-f]{2}){15,}\b',
        '***',
        msg,
        flags=re.IGNORECASE
    )

    # 移除内网 IP (10.x, 172.16-31.x, 192.168.x)
    msg = re.sub(
        r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b',
        '10.x.x.x',
        msg
    )

    # 移除可能的 JWT token 特征 (长 base64 字符串)
    msg = re.sub(
        r'\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\b',
        '***',
        msg
    )

    # 移除 http/https URL 中的敏感部分，保留域名
    msg = re.sub(
        r'(https?://)([^\s/]+)(/[^\s]*)',
        r'\1\2/***',
        msg
    )

    return msg


def sanitize_dict(data: dict, keys_to_filter: set = None) -> dict:
    """递归过滤字典中的敏感字段。

    Args:
        data: 要过滤的字典
        keys_to_filter: 需要过滤的键名集合

    Returns:
        过滤后的字典（新对象）
    """
    if keys_to_filter is None:
        keys_to_filter = {
            'password', 'secret', 'token', 'key', 'apikey', 'api_key',
            'private_key', 'ssh_private_key', 'fingerprint',
        }

    result = {}
    for key, value in data.items():
        lower_key = key.lower().replace('-', '_')

        # 检查键名是否需要过滤
        if any(sensitive in lower_key for sensitive in keys_to_filter):
            result[key] = '***'
        elif isinstance(value, dict):
            result[key] = sanitize_dict(value, keys_to_filter)
        elif isinstance(value, list):
            result[key] = [
                sanitize_dict(item, keys_to_filter) if isinstance(item, dict) else item
                for item in value
            ]
        else:
            result[key] = value

    return result


def is_sensitive_path(path: str) -> bool:
    """判断路径是否包含敏感信息。

    Args:
        path: 文件或目录路径

    Returns:
        True 如果路径敏感
    """
    if not path:
        return False

    path_lower = path.lower()
    sensitive_markers = [
        '.oci', '.ssh', '.aws', '.azure', '.config',
        'secret', 'password', 'credential', 'private',
        '.pem', '.key', 'id_rsa', 'id_ed25519',
    ]

    return any(marker in path_lower for marker in sensitive_markers)
