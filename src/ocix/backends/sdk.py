"""基于官方 oci Python SDK 的后端实现。

与 CLI 后端的区别只在于「怎么发请求」：进程内直接调用，省掉每次约 1.1 秒的
子进程启动与 SDK import，并且复用 HTTPS 连接。返回值统一用 ``oci.util.to_dict``
转成 snake_case 的普通 dict，上层的 ``_get(d, "kebab", "snake")`` 两种都认。
"""

from __future__ import annotations

import base64
import threading

import oci
from oci.exceptions import (
    ClientError,
    ConfigFileNotFound,
    InvalidConfig,
    InvalidKeyFilePath,
    InvalidPrivateKey,
    MissingPrivateKeyPassphrase,
    ProfileNotFound,
    ServiceError,
)
from oci.pagination import list_call_get_all_results
from oci.util import to_dict

from ..config import OCI_CONFIG_PATH
from ..oci_cli import OCICLIError
from .base import Backend

# 配置本身有问题（路径、私钥、口令…）。这类错误必须转成可读提示，
# 否则会以 500 冒到前端——私钥路径填错是最常见的情况。
CONFIG_ERRORS = (
    ConfigFileNotFound,
    ProfileNotFound,
    InvalidConfig,
    InvalidKeyFilePath,
    InvalidPrivateKey,
    MissingPrivateKeyPassphrase,
)


def _wrap(fn, *a, **kw):
    """把 SDK 异常翻译成面板统一的 OCICLIError，错误信息保持可读。"""
    try:
        return fn(*a, **kw)
    except ServiceError as e:
        msg = e.message or str(e)
        raise OCICLIError(msg, e.status, e.code) from None
    except CONFIG_ERRORS as e:
        raise OCICLIError(f"OCI 配置有问题: {e}") from None
    except ClientError as e:
        raise OCICLIError(f"OCI 客户端错误: {e}") from None
    except OSError as e:
        raise OCICLIError(f"连接 OCI 失败: {e}") from None


def _d(obj) -> dict:
    return to_dict(obj) if obj is not None else {}


def _list(resp) -> list:
    return [to_dict(x) for x in (resp.data or [])]


class SDKBackend(Backend):
    name = "sdk"

    def __init__(self):
        self._clients: dict = {}
        self._lock = threading.Lock()

    # ---------- 客户端缓存 ----------
    def _cfg(self, profile: str) -> dict:
        try:
            return oci.config.from_file(str(OCI_CONFIG_PATH), profile)
        except CONFIG_ERRORS as e:
            raise OCICLIError(f"读取 OCI 配置失败: {e}") from None

    def _client(self, profile: str, kind: str):
        key = (profile, kind)
        with self._lock:
            hit = self._clients.get(key)
        if hit is not None:
            return hit
        cfg = self._cfg(profile)
        try:
            oci.config.validate_config(cfg)
        except Exception as e:  # noqa: BLE001 - 任何配置问题都要变成可读提示
            raise OCICLIError(f"OCI 配置不合法: {e}") from None
        ctor = {
            "compute": oci.core.ComputeClient,
            "network": oci.core.VirtualNetworkClient,
            "block": oci.core.BlockstorageClient,
            "identity": oci.identity.IdentityClient,
            "monitoring": oci.monitoring.MonitoringClient,
        }[kind]
        client = _wrap(ctor, cfg)
        with self._lock:
            self._clients[key] = client
        return client

    def _compute(self, p):
        return self._client(p, "compute")

    def _net(self, p):
        return self._client(p, "network")

    def _block(self, p):
        return self._client(p, "block")

    def _iam(self, p):
        return self._client(p, "identity")

    def _mon(self, p):
        return self._client(p, "monitoring")

    def _all(self, fn, *a, **kw) -> list:
        """自动翻页，等价于 CLI 的 --all。"""
        return [to_dict(x) for x in _wrap(list_call_get_all_results, fn, *a, **kw).data]

    def version(self) -> str:
        return f"oci SDK {oci.__version__}"

    # ---------- Identity ----------
    def get_user(self, profile, user_id):
        return _d(_wrap(self._iam(profile).get_user, user_id).data)

    def list_compartments(self, profile, tenancy_id):
        return self._all(self._iam(profile).list_compartments, tenancy_id,
                         compartment_id_in_subtree=True, access_level="ACCESSIBLE")

    def list_availability_domains(self, profile, compartment_id):
        return _list(_wrap(self._iam(profile).list_availability_domains, compartment_id))

    # ---------- Compute ----------
    def list_instances(self, profile, compartment_id):
        return self._all(self._compute(profile).list_instances, compartment_id)

    def instance_action(self, profile, instance_id, action):
        return _d(_wrap(self._compute(profile).instance_action,
                        instance_id, action.upper()).data)

    def launch_instance(self, profile, spec):
        vnic = oci.core.models.CreateVnicDetails(
            subnet_id=spec["subnet_id"],
            assign_public_ip=True,
            # SDK 支持在创建时直接要一个 IPv6，不必等网卡挂好再补
            assign_ipv6_ip=bool(spec.get("assign_ipv6")),
        )
        metadata = {"ssh_authorized_keys": spec["ssh_public_key"]}
        if spec.get("user_data"):
            metadata["user_data"] = base64.b64encode(
                spec["user_data"].encode("utf-8")).decode("ascii")

        details = oci.core.models.LaunchInstanceDetails(
            compartment_id=spec["compartment_id"],
            availability_domain=spec["availability_domain"],
            display_name=spec["display_name"],
            shape=spec["shape"],
            create_vnic_details=vnic,
            metadata=metadata,
            source_details=oci.core.models.InstanceSourceViaImageDetails(
                source_type="image",
                image_id=spec["image_id"],
                boot_volume_size_in_gbs=int(spec["boot_gb"]),
            ),
        )
        sc = spec.get("shape_config")
        if sc:
            details.shape_config = oci.core.models.LaunchInstanceShapeConfigDetails(
                ocpus=float(sc["ocpus"]), memory_in_gbs=float(sc["memoryInGBs"]))
        return _d(_wrap(self._compute(profile).launch_instance, details).data)

    def terminate_instance(self, profile, instance_id, preserve_boot_volume):
        _wrap(self._compute(profile).terminate_instance, instance_id,
              preserve_boot_volume=preserve_boot_volume)

    def list_vnic_attachments(self, profile, compartment_id, instance_id=None):
        kw = {"instance_id": instance_id} if instance_id else {}
        return self._all(self._compute(profile).list_vnic_attachments, compartment_id, **kw)

    def list_boot_volume_attachments(self, profile, compartment_id, availability_domain):
        return self._all(self._compute(profile).list_boot_volume_attachments,
                         availability_domain, compartment_id)

    def list_volume_attachments(self, profile, compartment_id):
        return self._all(self._compute(profile).list_volume_attachments, compartment_id)

    def list_images(self, profile, compartment_id, operating_system, shape=None):
        kw = {"operating_system": operating_system,
              "sort_by": "TIMECREATED", "sort_order": "DESC"}
        if shape:
            kw["shape"] = shape
        return self._all(self._compute(profile).list_images, compartment_id, **kw)

    # ---------- Network ----------
    def get_vnic(self, profile, vnic_id):
        return _d(_wrap(self._net(profile).get_vnic, vnic_id).data)

    def list_private_ips(self, profile, vnic_id):
        return self._all(self._net(profile).list_private_ips, vnic_id=vnic_id)

    def get_public_ip_by_private_ip(self, profile, private_ip_id):
        details = oci.core.models.GetPublicIpByPrivateIpIdDetails(private_ip_id=private_ip_id)
        return _d(_wrap(self._net(profile).get_public_ip_by_private_ip_id, details).data)

    def delete_public_ip(self, profile, public_ip_id):
        _wrap(self._net(profile).delete_public_ip, public_ip_id)

    def create_ephemeral_public_ip(self, profile, compartment_id, private_ip_id):
        details = oci.core.models.CreatePublicIpDetails(
            compartment_id=compartment_id, lifetime="EPHEMERAL", private_ip_id=private_ip_id)
        return _d(_wrap(self._net(profile).create_public_ip, details).data)

    def create_ipv6(self, profile, vnic_id):
        details = oci.core.models.CreateIpv6Details(vnic_id=vnic_id)
        return _d(_wrap(self._net(profile).create_ipv6, details).data)

    def list_vcns(self, profile, compartment_id):
        return self._all(self._net(profile).list_vcns, compartment_id)

    def get_vcn(self, profile, vcn_id):
        return _d(_wrap(self._net(profile).get_vcn, vcn_id).data)

    def create_vcn(self, profile, compartment_id, cidr_block, display_name, dns_label):
        details = oci.core.models.CreateVcnDetails(
            compartment_id=compartment_id, cidr_block=cidr_block,
            display_name=display_name, dns_label=dns_label)
        vcn = _wrap(self._net(profile).create_vcn, details).data
        return _d(_wrap(oci.wait_until, self._net(profile),
                        self._net(profile).get_vcn(vcn.id),
                        "lifecycle_state", "AVAILABLE", max_wait_seconds=180).data)

    def add_vcn_ipv6_cidr(self, profile, vcn_id):
        details = oci.core.models.AddVcnIpv6CidrDetails(is_oracle_gua_allocation_enabled=True)
        try:
            _wrap(self._net(profile).add_ipv6_vcn_cidr, vcn_id, add_vcn_ipv6_cidr_details=details)
        except OCICLIError as e:
            msg = (e.message or "").lower()
            # 已经有 GUA 段时直接当成功，保持幂等
            if not ("already" in msg or "limitexceeded" in msg or "conflict" in msg):
                raise

    def list_subnets(self, profile, compartment_id, vcn_id=None):
        kw = {"vcn_id": vcn_id} if vcn_id else {}
        return self._all(self._net(profile).list_subnets, compartment_id, **kw)

    def get_subnet(self, profile, subnet_id):
        return _d(_wrap(self._net(profile).get_subnet, subnet_id).data)

    def create_subnet(self, profile, compartment_id, vcn_id, cidr_block, display_name, dns_label):
        details = oci.core.models.CreateSubnetDetails(
            compartment_id=compartment_id, vcn_id=vcn_id, cidr_block=cidr_block,
            display_name=display_name, dns_label=dns_label)
        subnet = _wrap(self._net(profile).create_subnet, details).data
        return _d(_wrap(oci.wait_until, self._net(profile),
                        self._net(profile).get_subnet(subnet.id),
                        "lifecycle_state", "AVAILABLE", max_wait_seconds=180).data)

    def update_subnet_ipv6_cidr(self, profile, subnet_id, ipv6_cidr):
        details = oci.core.models.UpdateSubnetDetails(ipv6_cidr_block=ipv6_cidr)
        _wrap(self._net(profile).update_subnet, subnet_id, details)

    def list_internet_gateways(self, profile, compartment_id, vcn_id):
        return self._all(self._net(profile).list_internet_gateways, compartment_id, vcn_id=vcn_id)

    def create_internet_gateway(self, profile, compartment_id, vcn_id, display_name):
        details = oci.core.models.CreateInternetGatewayDetails(
            compartment_id=compartment_id, vcn_id=vcn_id,
            is_enabled=True, display_name=display_name)
        ig = _wrap(self._net(profile).create_internet_gateway, details).data
        return _d(_wrap(oci.wait_until, self._net(profile),
                        self._net(profile).get_internet_gateway(ig.id),
                        "lifecycle_state", "AVAILABLE", max_wait_seconds=180).data)

    def get_route_table(self, profile, rt_id):
        return _d(_wrap(self._net(profile).get_route_table, rt_id).data)

    def update_route_rules(self, profile, rt_id, rules):
        models = [oci.core.models.RouteRule(
            destination=r.get("destination"),
            destination_type=r.get("destinationType", "CIDR_BLOCK"),
            network_entity_id=r.get("networkEntityId"),
            description=r.get("description"),
        ) for r in rules]
        details = oci.core.models.UpdateRouteTableDetails(route_rules=models)
        _wrap(self._net(profile).update_route_table, rt_id, details)

    def get_security_list(self, profile, security_list_id):
        return _d(_wrap(self._net(profile).get_security_list, security_list_id).data)

    def update_ingress_rules(self, profile, security_list_id, rules):
        _wrap(self._net(profile).update_security_list, security_list_id,
              oci.core.models.UpdateSecurityListDetails(
                  ingress_security_rules=[_ingress_model(r) for r in rules]))

    # ---------- Block storage ----------
    def list_boot_volumes(self, profile, compartment_id, availability_domain=None):
        kw = {"availability_domain": availability_domain} if availability_domain else {}
        return self._all(self._block(profile).list_boot_volumes,
                         compartment_id=compartment_id, **kw)

    def list_volumes(self, profile, compartment_id):
        return self._all(self._block(profile).list_volumes, compartment_id)

    def delete_boot_volume(self, profile, volume_id):
        _wrap(self._block(profile).delete_boot_volume, volume_id)

    def delete_volume(self, profile, volume_id):
        _wrap(self._block(profile).delete_volume, volume_id)

    def update_boot_volume_vpus(self, profile, volume_id, vpus):
        details = oci.core.models.UpdateBootVolumeDetails(vpus_per_gb=int(vpus))
        return _d(_wrap(self._block(profile).update_boot_volume, volume_id, details).data)

    def update_volume_vpus(self, profile, volume_id, vpus):
        details = oci.core.models.UpdateVolumeDetails(vpus_per_gb=int(vpus))
        return _d(_wrap(self._block(profile).update_volume, volume_id, details).data)

    # ---------- Monitoring ----------
    def summarize_metrics(self, profile, compartment_id, namespace, query, start_time, end_time):
        details = oci.monitoring.models.SummarizeMetricsDataDetails(
            namespace=namespace, query=query, start_time=start_time, end_time=end_time)
        return _list(_wrap(self._mon(profile).summarize_metrics_data, compartment_id, details))


def _port_range(opt: dict):
    rng = (opt or {}).get("destinationPortRange")
    if not rng:
        return None
    return oci.core.models.PortRange(min=int(rng["min"]), max=int(rng["max"]))


def _ingress_model(r: dict):
    """把 camelCase 的规则 dict 转成 SDK 模型。"""
    rule = oci.core.models.IngressSecurityRule(
        protocol=str(r.get("protocol")),
        source=r.get("source"),
        source_type=r.get("sourceType", "CIDR_BLOCK"),
        is_stateless=bool(r.get("isStateless", False)),
        description=r.get("description"),
    )
    if r.get("tcpOptions"):
        rule.tcp_options = oci.core.models.TcpOptions(
            destination_port_range=_port_range(r["tcpOptions"]))
    if r.get("udpOptions"):
        rule.udp_options = oci.core.models.UdpOptions(
            destination_port_range=_port_range(r["udpOptions"]))
    if r.get("icmpOptions"):
        icmp = r["icmpOptions"]
        rule.icmp_options = oci.core.models.IcmpOptions(
            type=icmp.get("type"), code=icmp.get("code"))
    return rule
