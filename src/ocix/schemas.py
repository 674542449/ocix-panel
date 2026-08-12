
from pydantic import BaseModel, Field, field_validator

VALID_ACTIONS = ("START", "STOP", "SOFTSTOP", "RESET", "SOFTRESET")


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8, max_length=128)


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


class CreateInstanceRequest(BaseModel):
    profile: str
    compartment_id: str
    display_name: str = Field(min_length=1, max_length=255)
    availability_domain: str
    image_id: str
    # 不传就由后端挑一个共用子网，没有则自动建——前端不再让用户选子网
    subnet_id: str | None = None
    shape: str
    ocpus: float = 1
    memory_gb: float = 6
    boot_gb: int = 50
    ssh_public_key: str
    assign_ipv6: bool = False
    # 建完自动放行全部端口；关掉则只留 OCI 默认的 22 端口
    open_all_ports: bool = True

    @field_validator("ssh_public_key")
    @classmethod
    def _check_key(cls, v: str) -> str:
        key = (v or "").strip()
        if not key.startswith(("ssh-rsa ", "ssh-ed25519 ", "ecdsa-sha2-", "ssh-dss ")):
            raise ValueError("SSH 公钥格式不对，应以 ssh-rsa / ssh-ed25519 / ecdsa-sha2- 开头")
        if "PRIVATE KEY" in key:
            raise ValueError("这是私钥，不要贴私钥——只需要 .pub 文件里的公钥")
        return key

    @field_validator("display_name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        return v.strip()


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

    @field_validator("protocol")
    @classmethod
    def _check_proto(cls, v: str) -> str:
        up = (v or "").strip().upper()
        if up not in ("TCP", "UDP", "ICMP", "ALL"):
            raise ValueError("协议仅支持 TCP / UDP / ICMP / ALL")
        return up


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
