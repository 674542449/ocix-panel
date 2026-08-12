"""SDK 后端：离线签名核对。

不发任何请求，只确认我们调用的方法、参数、模型字段在安装的 oci SDK 里真实存在。
这类错误在早期用 oci 命令行时只有连上真实租户才会暴露
（踩过 `No such option: --create-vnic-details`、
`get-public-ip-by-private-ip-id` 不是子命令、list_volumes 位置传参 TypeError）。
"""

import pytest

from ocix.backends import get_backend, set_backend
from ocix.backends.base import Backend

oci = pytest.importorskip("oci")
from ocix.backends.sdk import SDKBackend, _ingress_model  # noqa: E402


def test_backend_implements_full_interface():
    assert not SDKBackend.__abstractmethods__,         f"SDKBackend 未实现: {SDKBackend.__abstractmethods__}"


def test_default_backend_is_the_sdk():
    set_backend(None)
    try:
        b = get_backend()
        assert b.name == "sdk"
        assert isinstance(b, Backend)
    finally:
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


# ── SDK：配置类错误必须是可读的 400，不能是 500 ──

@pytest.mark.parametrize("exc_name", [
    "ConfigFileNotFound", "ProfileNotFound", "InvalidConfig",
    "InvalidKeyFilePath", "InvalidPrivateKey", "MissingPrivateKeyPassphrase",
])
def test_sdk_config_errors_become_readable(monkeypatch, exc_name):
    """回归：私钥路径写错时 from_file 抛 InvalidKeyFilePath，
    早先没捕获，直接以 500 冒到前端。"""
    from ocix.common import OCIError

    exc = getattr(oci.exceptions, exc_name)

    def boom(*a, **kw):
        try:
            raise exc("配置有问题")
        except TypeError:          # 个别异常构造签名不同
            raise exc("cfg", "profile", "配置有问题") from None

    backend = SDKBackend()
    monkeypatch.setattr(oci.config, "from_file", boom)
    with pytest.raises(OCIError) as ei:
        backend.list_instances("P", "c")
    assert "配置" in str(ei.value)


def test_sdk_service_error_keeps_message_and_code(monkeypatch):
    from ocix.backends.sdk import _wrap
    from ocix.common import OCIError

    def boom():
        raise oci.exceptions.ServiceError(404, "NotAuthorizedOrNotFound", {}, "找不到该资源")

    with pytest.raises(OCIError) as ei:
        _wrap(boom)
    assert ei.value.message == "找不到该资源"
    assert ei.value.code == "NotAuthorizedOrNotFound"


def test_sdk_network_failure_is_readable(monkeypatch):
    from ocix.backends.sdk import _wrap
    from ocix.common import OCIError

    def boom():
        raise OSError("name resolution failed")

    with pytest.raises(OCIError, match="连接 OCI 失败"):
        _wrap(boom)


# ── SDK：按后端真实的调用方式做形参绑定检查 ──
# 只做绑定，不发请求。回归：list_volumes 的签名是 (self, **kwargs)，
# 位置传参会直接 TypeError，存储页和额度页都会挂。

BINDINGS = [
    ("core.ComputeClient", "list_instances", ("cid",), {}),
    ("core.ComputeClient", "instance_action", ("iid", "START"), {}),
    ("core.ComputeClient", "terminate_instance", ("iid",), {"preserve_boot_volume": False}),
    ("core.ComputeClient", "list_vnic_attachments", ("cid",), {"instance_id": "iid"}),
    ("core.ComputeClient", "list_boot_volume_attachments", ("AD-1", "cid"), {}),
    ("core.ComputeClient", "list_volume_attachments", ("cid",), {}),
    ("core.ComputeClient", "list_images", ("cid",),
     {"operating_system": "Canonical Ubuntu", "sort_by": "TIMECREATED", "sort_order": "DESC"}),
    ("core.VirtualNetworkClient", "get_vnic", ("v1",), {}),
    ("core.VirtualNetworkClient", "list_private_ips", (), {"vnic_id": "v1"}),
    ("core.VirtualNetworkClient", "delete_public_ip", ("p1",), {}),
    ("core.VirtualNetworkClient", "add_ipv6_vcn_cidr", ("vcn1",), {}),
    ("core.VirtualNetworkClient", "list_subnets", ("cid",), {"vcn_id": "vcn1"}),
    ("core.VirtualNetworkClient", "get_subnet", ("s1",), {}),
    ("core.VirtualNetworkClient", "list_vcns", ("cid",), {}),
    ("core.VirtualNetworkClient", "get_vcn", ("vcn1",), {}),
    ("core.VirtualNetworkClient", "list_internet_gateways", ("cid",), {"vcn_id": "vcn1"}),
    ("core.VirtualNetworkClient", "get_route_table", ("rt1",), {}),
    ("core.VirtualNetworkClient", "get_security_list", ("sl1",), {}),
    # 这三个签名是 (self, **kwargs)，必须关键字传参
    ("core.BlockstorageClient", "list_volumes", (), {"compartment_id": "cid"}),
    ("core.BlockstorageClient", "list_boot_volumes", (),
     {"compartment_id": "cid", "availability_domain": "AD-1"}),
    ("core.BlockstorageClient", "delete_boot_volume", ("b1",), {}),
    ("core.BlockstorageClient", "delete_volume", ("v1",), {}),
    ("identity.IdentityClient", "get_user", ("u1",), {}),
    ("identity.IdentityClient", "list_compartments", ("t1",),
     {"compartment_id_in_subtree": True, "access_level": "ACCESSIBLE"}),
    ("identity.IdentityClient", "list_availability_domains", ("cid",), {}),
]


@pytest.mark.parametrize("path,method,args,kwargs", BINDINGS)
def test_sdk_calls_bind_to_real_signatures(path, method, args, kwargs):
    """用后端真实的传参方式做一次形参绑定；签名不符会在这里 TypeError。"""
    mod, cls = path.split(".")
    fn = getattr(getattr(getattr(oci, mod), cls), method)
    try:
        fn(None, *args, **kwargs)      # self=None，只走到形参绑定
    except TypeError as e:
        if "positional argument" in str(e) or "unexpected keyword" in str(e):
            pytest.fail(f"{path}.{method} 传参方式不对: {e}")
    except Exception:
        pass                            # 绑定通过后失败在预期之内（没有真实客户端）


def test_list_volumes_rejects_positional_compartment():
    """把上面那个 bug 单独钉死：别再改回位置传参。"""
    with pytest.raises(TypeError, match="positional argument"):
        oci.core.BlockstorageClient.list_volumes(None, "cid")
