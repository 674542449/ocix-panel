
import re

from pydantic import BaseModel, Field, field_validator, model_validator

VALID_ACTIONS = ("START", "STOP", "SOFTSTOP", "RESET", "SOFTRESET")

# 会被原样发往 Oracle 的文本（实例名、VCN 名、安全规则描述）一律限定为可打印 ASCII。
# 这些值最终出现在 OCI 控制台、oci CLI 输出和 Oracle 侧记录里，
# 混进中文在不少工具里会变成乱码或问号，出问题时很难对上号。
# 宁可在这里挡下来让用户改，也不要静默替换——名字被悄悄改掉更难排查。
_NON_ASCII = re.compile(r"[^\x20-\x7e]")


def _ascii_only(value: str, label: str) -> str:
    v = (value or "").strip()
    bad = "".join(dict.fromkeys(_NON_ASCII.findall(v)))
    if bad:
        raise ValueError(
            f"{label}会原样显示在 Oracle 控制台，只能用英文、数字和常见符号；"
            f"请去掉这些字符：{bad}"
        )
    return v


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


class ConsolePasswordPolicyRequest(BaseModel):
    """Oracle 账号（控制台登录）的密码有效期。0 = 永不过期。"""
    days: int = Field(ge=0, le=3650)
    policy_id: str | None = None


class InstanceActionRequest(BaseModel):
    profile: str
    instance_id: str
    action: str  # START / STOP / SOFTSTOP / RESET / SOFTRESET
    compartment_id: str | None = None

    @field_validator("action")
    @classmethod
    def _check_action(cls, v: str) -> str:
        up = (v or "").strip().upper()
        if up not in VALID_ACTIONS:
            raise ValueError(f"不支持的操作，仅允许: {', '.join(VALID_ACTIONS)}")
        return up


class PreflightRequest(BaseModel):
    profile: str
    shape: str
    ocpus: float = 1
    memory_gb: float = 6
    boot_gb: int = 50
    compartment_id: str | None = None


class CapacityRadarRequest(BaseModel):
    profile: str
    compartment_id: str | None = None
    shape: str = "VM.Standard.A1.Flex"
    ocpus: float = 4
    memory_gb: float = 24
    all_regions: bool = False
    regions: list[str] | None = None


class CreateInstanceRequest(BaseModel):
    profile: str
    compartment_id: str
    display_name: str = Field(min_length=1, max_length=255)
    availability_domain: str
    fault_domain: str = ""
    image_id: str
    # 不传就由后端挑一个共用子网，没有则自动建——前端不再让用户选子网
    subnet_id: str | None = None
    shape: str
    ocpus: float = 1
    memory_gb: float = 6
    boot_gb: int = 50
    # 公钥不再必填：可以只用 root + 密码登录。但两样至少要有一样，
    # 一样都没有的机器建出来就进不去了（见下面的校验）
    ssh_public_key: str = ""
    # 填了就开 root + 密码登录，并把密码写进实例标签
    root_password: str = ""
    assign_ipv6: bool = False
    # 建完自动放行全部端口；关掉则只留 OCI 默认的 22 端口
    open_all_ports: bool = True
    # 智能容量探测前置（先探测 OCI 放货状态再下单，避免频繁 429 和封号）
    capacity_probe: bool = True
    # 智能放货抢机（检测到无库存时进入智能低频容量探测直到放货创建）
    auto_retry_until_available: bool = False
    max_retry_minutes: int = 60

    @field_validator("display_name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        name = _ascii_only(v, "实例名称")
        if not name:
            raise ValueError("实例名称不能为空")
        return name

    @field_validator("root_password")
    @classmethod
    def _check_root_password(cls, v: str) -> str:
        pw = v or ""
        if not pw:
            return ""
        if len(pw) < 12:
            # 开了密码登录之后，公网上的 22 端口几分钟内就会被扫。
            # 这里不是形式主义——短密码等于把机器送人。
            raise ValueError("root 密码至少 12 位（开了密码登录的机器会被全网扫）")
        if any(ord(c) < 32 or ord(c) == 127 for c in pw):
            raise ValueError("root 密码里不能有控制字符")
        return pw

    @field_validator("ssh_public_key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        key = (v or "").strip()
        if not key:
            return ""          # 允许留空，由 _need_some_way_in 兜底
        if not key.startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-", "ssh-dss ")):
            raise ValueError("SSH 公钥格式不对，应以 ssh-rsa / ssh-ed25519 / ecdsa-sha2- 开头")
        if "PRIVATE KEY" in key:
            raise ValueError("这是私钥，不要贴私钥——只需要 .pub 文件里的公钥")
        return key

    @model_validator(mode="after")
    def _need_some_way_in(self):
        """公钥和 root 密码至少要有一样。

        两样都不给的话，机器建出来是能跑，但你永远登不进去——
        只能靠串口控制台救，而串口控制台也需要系统里先有密码。
        """
        if not (self.ssh_public_key or self.root_password):
            raise ValueError("至少要给一样登录方式：SSH 公钥，或 root 密码")
        return self

class InstanceRefRequest(BaseModel):
    """只需要定位一台实例的操作（换 IP、查防火墙等）。"""
    profile: str
    instance_id: str
    compartment_id: str | None = None


class FirewallRequest(InstanceRefRequest):
    include_ipv6: bool = True


class PortRuleRequest(InstanceRefRequest):
    protocol: str = "TCP"
    port_from: int = Field(default=80, ge=1, le=65535)
    port_to: int = Field(default=80, ge=1, le=65535)
    source: str = "0.0.0.0/0"
    description: str = ""

    @field_validator("description")
    @classmethod
    def _check_desc(cls, v: str) -> str:
        return _ascii_only(v, "规则描述")

    @field_validator("protocol")
    @classmethod
    def _check_proto(cls, v: str) -> str:
        up = (v or "").strip().upper()
        if up not in ("TCP", "UDP", "ICMP", "ALL"):
            raise ValueError("协议仅支持 TCP / UDP / ICMP / ALL")
        return up


class ResizeShapeRequest(InstanceRefRequest):
    ocpus: float = Field(gt=0, le=64)
    memory_gb: float = Field(gt=0, le=1024)


class ConsoleRequest(InstanceRefRequest):
    public_key: str

    @field_validator("public_key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        key = (v or "").strip()
        if not key.startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-", "ssh-dss ")):
            raise ValueError("串口控制台需要 SSH 公钥，应以 ssh-rsa / ssh-ed25519 开头")
        if "PRIVATE KEY" in key:
            raise ValueError("这是私钥，只需要 .pub 里的公钥")
        return key


class DeleteConsoleRequest(BaseModel):
    profile: str
    connection_id: str


class BackupRequest(BaseModel):
    profile: str
    boot_volume_id: str
    display_name: str = ""
    backup_type: str = "INCREMENTAL"

    @field_validator("display_name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _ascii_only(v, "备份名称")

    @field_validator("backup_type")
    @classmethod
    def _check_type(cls, v: str) -> str:
        up = (v or "INCREMENTAL").strip().upper()
        if up not in ("FULL", "INCREMENTAL"):
            raise ValueError("备份类型只能是 FULL 或 INCREMENTAL")
        return up


class DeleteBackupRequest(BaseModel):
    profile: str
    backup_id: str


class RestoreBackupRequest(BaseModel):
    profile: str
    backup_id: str
    availability_domain: str
    display_name: str = ""
    compartment_id: str | None = None

    @field_validator("display_name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return _ascii_only(v, "引导卷名称")


class ResizeBootVolumeRequest(BaseModel):
    profile: str
    boot_volume_id: str
    size_gb: int = Field(ge=50, le=200)
    compartment_id: str | None = None


class DeleteRuleRequest(InstanceRefRequest):
    index: int = Field(ge=0)


class ClearRulesRequest(InstanceRefRequest):
    keep_ssh: bool = True


class EnableIpv6Request(BaseModel):
    profile: str
    subnet_id: str
    compartment_id: str


class VolumePerformanceRequest(BaseModel):
    profile: str
    volume_id: str
    kind: str  # boot / block
    vpus: int = Field(ge=0, le=120)

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v not in ("boot", "block"):
            raise ValueError("kind 只能是 boot 或 block")
        return v


class TerminateInstanceRequest(BaseModel):
    profile: str
    instance_id: str
    preserve_boot_volume: bool = False
    display_name: str | None = None


class DeleteVolumeRequest(BaseModel):
    profile: str
    volume_id: str
    kind: str  # boot / block

    @field_validator("kind")
    @classmethod
    def _check_kind(cls, v: str) -> str:
        if v not in ("boot", "block"):
            raise ValueError("kind 只能是 boot 或 block")
        return v


class CreateNetworkRequest(BaseModel):
    profile: str
    compartment_id: str
    name: str = "ocix-vcn"

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        name = _ascii_only(v, "网络名称")
        if not name:
            raise ValueError("网络名称不能为空")
        return name


class BatchActionRequest(BaseModel):
    action: str
    targets: list  # [{profile, instance_id, display_name?}]

    @field_validator("action")
    @classmethod
    def _check_action(cls, v: str) -> str:
        up = (v or "").strip().upper()
        if up not in VALID_ACTIONS:
            raise ValueError(f"不支持的操作，仅允许: {', '.join(VALID_ACTIONS)}")
        return up


# ---- SSH 公钥池 ----
class SSHKeyCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    public_key: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        name = (v or "").strip()
        if not name:
            raise ValueError("公钥备注名称不能为空")
        return name

    @field_validator("public_key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        key = (v or "").strip()
        if not key.startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-", "ssh-dss ")):
            raise ValueError("SSH 公钥格式不对，应以 ssh-rsa / ssh-ed25519 / ecdsa-sha2- 开头")
        if "PRIVATE KEY" in key:
            raise ValueError("这是私钥，不要贴私钥——只需要 .pub 文件里的公钥")
        return key


class SSHKeyUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    public_key: str

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        name = (v or "").strip()
        if not name:
            raise ValueError("公钥备注名称不能为空")
        return name

    @field_validator("public_key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        key = (v or "").strip()
        if not key.startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-", "ssh-dss ")):
            raise ValueError("SSH 公钥格式不对，应以 ssh-rsa / ssh-ed25519 / ecdsa-sha2- 开头")
        if "PRIVATE KEY" in key:
            raise ValueError("这是私钥，不要贴私钥——只需要 .pub 文件里的公钥")
        return key


# ---- Telegram 通知设置 ----
class TelegramSettingsRequest(BaseModel):
    enabled: bool = False
    bot_token: str = ""
    chat_id: str = ""


class TelegramTestRequest(BaseModel):
    bot_token: str
    chat_id: str

