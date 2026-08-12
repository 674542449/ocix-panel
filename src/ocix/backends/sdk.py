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

from ..common import OCIError
from ..config import OCI_CONFIG_PATH, OCI_LAUNCH_TIMEOUT
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
    """把 SDK 异常翻译成面板统一的 OCIError，错误信息保持可读。"""
    try:
        return fn(*a, **kw)
    except ServiceError as e:
        msg = e.message or str(e)
        raise OCIError(msg, e.status, e.code) from None
    except CONFIG_ERRORS as e:
        raise OCIError(f"OCI 配置有问题: {e}") from None
    except ClientError as e:
        raise OCIError(f"OCI 客户端错误: {e}") from None
    except OSError as e:
        raise OCIError(f"连接 OCI 失败: {e}") from None


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
            raise OCIError(f"读取 OCI 配置失败: {e}") from None

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
            raise OCIError(f"OCI 配置不合法: {e}") from None
        ctor = {
            "compute": oci.core.ComputeClient,
            "network": oci.core.VirtualNetworkClient,
            "block": oci.core.BlockstorageClient,
            "identity": oci.identity.IdentityClient,
            "monitoring": oci.monitoring.MonitoringClient,
            "limits": oci.limits.LimitsClient,
            "subscription": oci.tenant_manager_control_plane.SubscriptionClient,
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

    def _limits(self, p):
        return self._client(p, "limits")

    def _all(self, fn, *a, **kw) -> list:
        """自动翻页，等价于 CLI 的 --all。"""
        return [to_dict(x) for x in _wrap(list_call_get_all_results, fn, *a, **kw).data]

    def _wait(self, profile: str, getter, resource_id: str, state: str = "AVAILABLE"):
        """等资源变成目标状态，返回资源对象。

        建 VCN / 子网 / 网关都要等就绪，否则下一步会拿到还没生效的资源。
        """
        resp = _wrap(getter, resource_id)
        done = _wrap(oci.wait_until, self._net(profile), resp,
                     "lifecycle_state", state, max_wait_seconds=OCI_LAUNCH_TIMEOUT)
        return done.data

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
        return _d(self._wait(profile, self._net(profile).get_vcn, vcn.id))

    def add_vcn_ipv6_cidr(self, profile, vcn_id):
        details = oci.core.models.AddVcnIpv6CidrDetails(is_oracle_gua_allocation_enabled=True)
        try:
            _wrap(self._net(profile).add_ipv6_vcn_cidr, vcn_id, add_vcn_ipv6_cidr_details=details)
        except OCIError as e:
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
        return _d(self._wait(profile, self._net(profile).get_subnet, subnet.id))

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
        return _d(self._wait(profile, self._net(profile).get_internet_gateway, ig.id))

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
        # 注意：list_volumes 的签名是 (self, **kwargs)，只能用关键字传参
        return self._all(self._block(profile).list_volumes, compartment_id=compartment_id)

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

    # ---------- Limits / 订阅 ----------
    def list_limit_values(self, profile, compartment_id, service_name):
        # 注意全是关键字参数，位置传参会直接 TypeError
        return self._all(self._limits(profile).list_limit_values,
                         compartment_id=compartment_id, service_name=service_name)

    def list_subscriptions(self, profile, compartment_id):
        """租户的订阅记录——判断账户等级的权威依据。

        entity_version="V1" 不能省：带上它返回的才是 ClassicSubscriptionSummary
        子类型，里面有 payment_model；不带的话拿到的是基类，没有这个字段，
        等级就无从判断了。

        返回的是 SubscriptionCollection，取 .items；租户的订阅条数极少，不用翻页。
        """
        resp = _wrap(self._client(profile, "subscription").list_subscriptions,
                     compartment_id=compartment_id, entity_version="V1")
        return [to_dict(x) for x in (getattr(resp.data, "items", None) or [])]

    # ---------- Identity Domains（控制台登录密码策略）----------
    def _domains_client(self, profile: str, domain_url: str):
        """每个 Identity Domain 有自己的 SCIM 端点，按 URL 缓存客户端。"""
        key = (profile, "domains", domain_url)
        with self._lock:
            hit = self._clients.get(key)
        if hit is not None:
            return hit
        endpoint = (domain_url or "").strip().rstrip("/")
        if not endpoint:
            raise OCIError("Identity Domain 的 URL 为空，无法读取密码策略")
        client = _wrap(oci.identity_domains.IdentityDomainsClient,
                       self._cfg(profile), service_endpoint=endpoint)
        with self._lock:
            self._clients[key] = client
        return client

    def list_domains(self, profile, compartment_id):
        return self._all(self._iam(profile).list_domains, compartment_id,
                         lifecycle_state="ACTIVE")

    def list_password_policies(self, profile, domain_url):
        # attribute_sets=["all"] 不能省：默认返回的精简视图里没有 passwordExpiresAfter
        resp = _wrap(self._domains_client(profile, domain_url).list_password_policies,
                     count=100, attribute_sets=["all"])
        return [to_dict(x) for x in (getattr(resp.data, "resources", None) or [])]

    def set_password_expiry(self, profile, domain_url, policy_id, days):
        """days=0 表示永不过期（把字段整个删掉），>0 则设成具体天数。

        用的是 SCIM PatchOp。op 必须大写（REMOVE / REPLACE），
        小写会被域直接拒掉。
        """
        from oci.identity_domains.models import Operations, PatchOp

        client = self._domains_client(profile, domain_url)
        if int(days) <= 0:
            ops = [Operations(op=Operations.OP_REMOVE, path="passwordExpiresAfter"),
                   Operations(op=Operations.OP_REMOVE, path="passwordExpireWarning")]
            fallback = [
                Operations(op=Operations.OP_REPLACE, path="passwordExpiresAfter", value=None),
                Operations(op=Operations.OP_REPLACE, path="passwordExpireWarning", value=None),
            ]
        else:
            ops = [Operations(op=Operations.OP_REPLACE,
                              path="passwordExpiresAfter", value=int(days))]
            fallback = None

        def _patch(operations):
            patch = PatchOp(schemas=["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                            operations=operations)
            return _wrap(client.patch_password_policy, policy_id,
                         patch_op=patch, attribute_sets=["all"])

        try:
            resp = _patch(ops)
        except OCIError:
            # 有的域不接受 REMOVE，退回用 REPLACE 置空
            if fallback is None:
                raise
            resp = _patch(fallback)
        return _d(resp.data)

    def get_subscription(self, profile, subscription_id):
        """订阅详情，含 subscription_tier 与 promotion。

        注意别传 entity_version：这个方法的 kwargs 白名单里没有它，
        传了会直接 ValueError（list_subscriptions 才接受）。
        """
        return to_dict(_wrap(self._client(profile, "subscription").get_subscription,
                             subscription_id).data)


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
