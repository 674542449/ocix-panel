"""两个后端各自的实现细节。

SDK 那部分是**离线签名核对**：不发任何请求，只确认我们调用的方法与参数
在安装的 oci SDK 里真实存在。这类错误在 CLI 时代只有连上真实租户才会暴露
（曾经踩到 `No such option: --create-vnic-details`、
`get-public-ip-by-private-ip-id` 不是子命令）。
"""

import inspect

import pytest

from ocix.backends import get_backend, set_backend
from ocix.backends.base import Backend
from ocix.backends.cli import CLIBackend

oci = pytest.importorskip("oci")
from ocix.backends.sdk import SDKBackend, _ingress_model  # noqa: E402

ABSTRACT = sorted(
    name for name, val in vars(Backend).items()
    if getattr(val, "__isabstractmethod__", False)
)


# ── 接口一致性 ──

@pytest.mark.parametrize("cls", [CLIBackend, SDKBackend])
def test_backend_implements_full_interface(cls):
    assert not cls.__abstractmethods__, f"{cls.__name__} 未实现: {cls.__abstractmethods__}"


@pytest.mark.parametrize("method", ABSTRACT)
def test_signatures_match_between_backends(method):
    """两个后端签名必须一致，否则切换 OCIX_BACKEND 会在运行时炸。"""
    cli_sig = inspect.signature(getattr(CLIBackend, method))
    sdk_sig = inspect.signature(getattr(SDKBackend, method))
    assert list(cli_sig.parameters) == list(sdk_sig.parameters), method


def test_backend_selection_defaults_to_cli(monkeypatch):
    set_backend(None)
    monkeypatch.setattr("ocix.backends.OCI_BACKEND", "cli")
    assert get_backend().name == "cli"
    set_backend(None)


def test_backend_selection_can_pick_sdk(monkeypatch):
    set_backend(None)
    monkeypatch.setattr("ocix.backends.OCI_BACKEND", "sdk")
    assert get_backend().name == "sdk"
    set_backend(None)


# ── SDK：调用的方法是否真实存在 ──

SDK_METHODS = [
    ("core.ComputeClient", ["launch_instance", "terminate_instance", "instance_action",
                            "list_instances", "list_vnic_attachments", "list_images",
                            "list_boot_volume_attachments", "list_volume_attachments"]),
    ("core.VirtualNetworkClient", ["get_vnic", "create_ipv6", "add_ipv6_vcn_cidr",
                                   "create_public_ip", "delete_public_ip",
                                   "get_public_ip_by_private_ip_id", "list_private_ips",
                                   "update_security_list", "get_security_list",
                                   "update_subnet", "update_route_table", "get_route_table",
                                   "list_subnets", "get_subnet", "create_subnet",
                                   "list_vcns", "get_vcn", "create_vcn",
                                   "list_internet_gateways", "create_internet_gateway"]),
    ("core.BlockstorageClient", ["list_boot_volumes", "list_volumes", "update_boot_volume",
                                 "update_volume", "delete_boot_volume", "delete_volume"]),
    ("identity.IdentityClient", ["list_compartments", "list_availability_domains", "get_user"]),
    ("monitoring.MonitoringClient", ["summarize_metrics_data"]),
]


@pytest.mark.parametrize("path,methods", SDK_METHODS)
def test_sdk_client_methods_exist(path, methods):
    mod, cls = path.split(".")
    client = getattr(getattr(oci, mod), cls)
    missing = [m for m in methods if not hasattr(client, m)]
    assert not missing, f"{path} 缺少方法: {missing}"


SDK_MODELS = [
    ("LaunchInstanceDetails", ["compartment_id", "availability_domain", "display_name",
                               "shape", "create_vnic_details", "metadata", "source_details",
                               "shape_config"]),
    ("CreateVnicDetails", ["subnet_id", "assign_public_ip", "assign_ipv6_ip"]),
    ("InstanceSourceViaImageDetails", ["image_id", "boot_volume_size_in_gbs"]),
    ("LaunchInstanceShapeConfigDetails", ["ocpus", "memory_in_gbs"]),
    ("IngressSecurityRule", ["protocol", "source", "source_type", "is_stateless",
                             "description", "tcp_options", "udp_options", "icmp_options"]),
    ("RouteRule", ["destination", "destination_type", "network_entity_id"]),
    ("AddVcnIpv6CidrDetails", ["is_oracle_gua_allocation_enabled"]),
    ("CreatePublicIpDetails", ["compartment_id", "lifetime", "private_ip_id"]),
    ("CreateIpv6Details", ["vnic_id"]),
    ("GetPublicIpByPrivateIpIdDetails", ["private_ip_id"]),
    ("UpdateSubnetDetails", ["ipv6_cidr_block"]),
    ("UpdateBootVolumeDetails", ["vpus_per_gb"]),
    ("UpdateVolumeDetails", ["vpus_per_gb"]),
]


@pytest.mark.parametrize("model,fields", SDK_MODELS)
def test_sdk_model_fields_exist(model, fields):
    """模型字段名写错在 SDK 里是 TypeError，这里离线就能抓到。"""
    cls = getattr(oci.core.models, model)
    attrs = cls().attribute_map
    missing = [f for f in fields if f not in attrs]
    assert not missing, f"{model} 缺少字段: {missing}"


def test_assign_ipv6_at_launch_is_supported_by_sdk():
    """SDK 能在创建实例时直接分配 IPv6——CLI 没有对应参数，只能创建完再补挂。"""
    vnic = oci.core.models.CreateVnicDetails(subnet_id="s", assign_ipv6_ip=True)
    assert vnic.assign_ipv6_ip is True


# ── SDK：规则 dict → 模型的转换 ──

def test_ingress_model_maps_tcp_port_range():
    rule = _ingress_model({
        "protocol": "6", "source": "0.0.0.0/0", "sourceType": "CIDR_BLOCK",
        "isStateless": False, "description": "web",
        "tcpOptions": {"destinationPortRange": {"min": 80, "max": 443}},
    })
    assert rule.protocol == "6"
    assert rule.tcp_options.destination_port_range.min == 80
    assert rule.tcp_options.destination_port_range.max == 443
    assert rule.is_stateless is False


def test_ingress_model_handles_all_protocol_without_ports():
    rule = _ingress_model({"protocol": "all", "source": "::/0", "isStateless": False})
    assert rule.protocol == "all"
    assert rule.tcp_options is None
    assert rule.udp_options is None


def test_ingress_model_maps_udp_and_icmp():
    udp = _ingress_model({"protocol": "17", "source": "0.0.0.0/0",
                          "udpOptions": {"destinationPortRange": {"min": 53, "max": 53}}})
    assert udp.udp_options.destination_port_range.min == 53
    icmp = _ingress_model({"protocol": "1", "source": "0.0.0.0/0",
                           "icmpOptions": {"type": 3, "code": 4}})
    assert icmp.icmp_options.type == 3 and icmp.icmp_options.code == 4


# ── CLI 后端：命令行参数形状 ──

@pytest.fixture()
def cli_calls(monkeypatch):
    calls = []

    def fake_run(profile, *args, **kwargs):
        calls.append(list(args))
        return {"data": {"id": "x", "lifecycle-state": "PROVISIONING"}}

    monkeypatch.setattr("ocix.backends.cli.run_oci", fake_run)
    return calls


CLI_SPEC = {
    "compartment_id": "c", "availability_domain": "AD-1", "display_name": "box",
    "image_id": "img", "subnet_id": "sub1", "shape": "VM.Standard.E2.1.Micro",
    "boot_gb": 50, "ssh_public_key": "ssh-ed25519 AAAA test@x",
}


@pytest.mark.parametrize("assign_ipv6", [False, True])
def test_cli_launch_never_uses_create_vnic_details(cli_calls, assign_ipv6):
    """回归：--create-vnic-details 在 oci CLI 里根本不存在（是 SDK 层字段）。"""
    CLIBackend().launch_instance("P", {**CLI_SPEC, "assign_ipv6": assign_ipv6})
    args = cli_calls[0]
    assert "--create-vnic-details" not in args
    assert "--subnet-id" in args and "--assign-public-ip" in args


def test_cli_uses_public_ip_get_with_private_ip_id(cli_calls):
    """回归：get-public-ip-by-private-ip-id 不是子命令，正确写法是 public-ip get。"""
    CLIBackend().get_public_ip_by_private_ip("P", "pip1")
    joined = " ".join(cli_calls[0])
    assert "get-public-ip-by-private-ip-id" not in joined
    assert joined.startswith("network public-ip get") and "--private-ip-id" in joined


def test_cli_vcn_ipv6_declares_oracle_allocation(cli_calls):
    """AddVcnIpv6CidrDetails 必须给 isOracleGuaAllocationEnabled，否则服务端拒绝。"""
    CLIBackend().add_vcn_ipv6_cidr("P", "vcn1")
    assert "--is-oracle-gua-allocation-enabled" in cli_calls[0]


def test_cli_launch_sends_ssh_key_as_metadata(cli_calls):
    CLIBackend().launch_instance("P", dict(CLI_SPEC))
    args = cli_calls[0]
    meta = args[args.index("--metadata") + 1]
    assert "ssh_authorized_keys" in meta and "ssh-ed25519" in meta


def test_cli_shape_config_only_for_flex(cli_calls):
    CLIBackend().launch_instance("P", {**CLI_SPEC, "shape": "VM.Standard.A1.Flex",
                                       "shape_config": {"ocpus": 4, "memoryInGBs": 24}})
    assert "--shape-config" in cli_calls[0]
    cli_calls.clear()
    CLIBackend().launch_instance("P", dict(CLI_SPEC))
    assert "--shape-config" not in cli_calls[0]


# ── SDK：配置类错误必须是可读的 400，不能是 500 ──

@pytest.mark.parametrize("exc_name", [
    "ConfigFileNotFound", "ProfileNotFound", "InvalidConfig",
    "InvalidKeyFilePath", "InvalidPrivateKey", "MissingPrivateKeyPassphrase",
])
def test_sdk_config_errors_become_readable(monkeypatch, exc_name):
    """回归：私钥路径写错时 from_file 抛 InvalidKeyFilePath，
    早先没捕获，直接以 500 冒到前端。"""
    from ocix.oci_cli import OCICLIError

    exc = getattr(oci.exceptions, exc_name)

    def boom(*a, **kw):
        try:
            raise exc("配置有问题")
        except TypeError:          # 个别异常构造签名不同
            raise exc("cfg", "profile", "配置有问题") from None

    backend = SDKBackend()
    monkeypatch.setattr(oci.config, "from_file", boom)
    with pytest.raises(OCICLIError) as ei:
        backend.list_instances("P", "c")
    assert "配置" in str(ei.value)


def test_sdk_service_error_keeps_message_and_code(monkeypatch):
    from ocix.backends.sdk import _wrap
    from ocix.oci_cli import OCICLIError

    def boom():
        raise oci.exceptions.ServiceError(404, "NotAuthorizedOrNotFound", {}, "找不到该资源")

    with pytest.raises(OCICLIError) as ei:
        _wrap(boom)
    assert ei.value.message == "找不到该资源"
    assert ei.value.code == "NotAuthorizedOrNotFound"


def test_sdk_network_failure_is_readable(monkeypatch):
    from ocix.backends.sdk import _wrap
    from ocix.oci_cli import OCICLIError

    def boom():
        raise OSError("name resolution failed")

    with pytest.raises(OCICLIError, match="连接 OCI 失败"):
        _wrap(boom)
