"""可记录调用的假后端，用于在不连 OCI 的前提下验证上层逻辑。

因为它实现的是 ``Backend`` 接口，同一份业务逻辑测试对 cli / sdk 两种实现都成立。
"""

from __future__ import annotations

from ocix.backends.base import Backend
from ocix.common import OCIError


def _to_sdk_shape(obj):
    """把面板写出去的 camelCase 结构转成 SDK 读回来的 snake_case 形态。

    OCI 收 camelCase、回 snake_case；假后端也得这样，
    否则「写进去什么就读回什么」会让键名不匹配的问题隐形。
    """
    import re as _re

    def snake(k):
        return _re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", k.replace("-", "_")).lower()

    if isinstance(obj, dict):
        return {snake(k): _to_sdk_shape(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_sdk_shape(v) for v in obj]
    return obj


class FakeBackend(Backend):
    name = "fake"

    def __init__(self, **overrides):
        self.calls: list[str] = []
        # 默认按纯免费号构造：只有 micro / A1 非零，其余机型都是 0
        self.limit_values = [
            {"name": "standard-e2-micro-core-count", "value": 2},
            {"name": "standard-a1-core-count", "value": 4},
            {"name": "standard-e4-core-count", "value": 0},
            {"name": "standard3-core-count", "value": 0},
            {"name": "gpu2-count", "value": 0},
        ]
        # 默认：读不到订阅记录（免费号常见，权限不足）
        self.subscriptions = []
        self.subscription_details = {}
        # 路由表里已有的规则（SDK 形态），用来验证重写时不会把它们弄坏
        self.route_rules_existing = []
        # 监控数据点，按 SDK 形态回给上层
        self.metrics = []
        self.home_region_name = "us-ashburn-1"
        self.invoices = []
        self.console = []
        self.boot_volume_attachments = []
        self.backups = []
        self.usage_items = []
        # Identity Domain 与控制台密码策略（默认就是 Oracle 那个 120 天）
        self.domains = [{"id": "dom1", "display_name": "Default",
                         "url": "https://idcs-x.identity.oraclecloud.com", "type": "DEFAULT"}]
        self.password_policies = {
            "https://idcs-x.identity.oraclecloud.com": [
                {"id": "pol1", "name": "Default Password Policy",
                 "priority": 1, "passwordExpiresAfter": 120},
            ],
        }
        self.instances: list = []
        self.images: list = []
        self.compartments: list = []
        self.ads: list = [{"name": "AD-1"}, {"name": "AD-2"}]
        self.boot_volumes: list = []
        self.volumes: list = []
        self.vnic_attachments: list = []
        self.vnic: dict = {"public_ip": "1.2.3.4", "private_ip": "10.0.0.2",
                           "subnet_id": "sub1", "ipv6_addresses": []}
        self.subnet: dict = {"security_list_ids": ["sl1"], "vcn_id": "vcn1",
                             "display_name": "public", "route_table_id": "rt1"}
        self.ingress_rules: list = []
        self.launched: list = []
        self.private_ips: list = [{"id": "pip1", "is_primary": True}]
        self.public_ip: dict = {"id": "pub1", "lifetime": "EPHEMERAL"}
        self.__dict__.update(overrides)

    def _rec(self, op):
        self.calls.append(op)

    def count(self, op) -> int:
        return self.calls.count(op)

    def version(self):
        return "fake"

    # ---------- Identity ----------
    def get_user(self, profile, user_id):
        self._rec("get_user")
        return {"name": "tester", "email": "t@example.com"}

    def list_compartments(self, profile, tenancy_id):
        self._rec("list_compartments")
        return list(self.compartments)

    def list_availability_domains(self, profile, compartment_id):
        self._rec("list_availability_domains")
        return list(self.ads)

    # ---------- Compute ----------
    def list_instances(self, profile, compartment_id):
        self._rec("list_instances")
        return [i for i in self.instances
                if i.get("compartment_id", compartment_id) == compartment_id]

    def instance_action(self, profile, instance_id, action):
        self._rec("instance_action")
        return {"lifecycle_state": "STARTING"}

    def launch_instance(self, profile, spec):
        self._rec("launch_instance")
        self.launched.append(spec)
        return {"id": "ocid1.instance.oc1..new", "display_name": spec["display_name"],
                "lifecycle_state": "PROVISIONING"}

    def terminate_instance(self, profile, instance_id, preserve_boot_volume):
        self._rec("terminate_instance")

    def list_vnic_attachments(self, profile, compartment_id, instance_id=None):
        self._rec("list_vnic_attachments")
        return list(self.vnic_attachments)

    def list_boot_volume_attachments(self, profile, compartment_id, availability_domain):
        self._rec("list_boot_volume_attachments")
        return [_to_sdk_shape(a) for a in self.boot_volume_attachments]

    def list_volume_attachments(self, profile, compartment_id):
        self._rec("list_volume_attachments")
        return []

    def list_images(self, profile, compartment_id, operating_system, shape=None):
        self._rec("list_images")
        return list(self.images)

    # ---------- Network ----------
    def get_vnic(self, profile, vnic_id):
        self._rec("get_vnic")
        return dict(self.vnic)

    def list_private_ips(self, profile, vnic_id):
        self._rec("list_private_ips")
        return list(self.private_ips)

    def get_public_ip_by_private_ip(self, profile, private_ip_id):
        self._rec("get_public_ip_by_private_ip")
        return dict(self.public_ip)

    def delete_public_ip(self, profile, public_ip_id):
        self._rec("delete_public_ip")

    def create_ephemeral_public_ip(self, profile, compartment_id, private_ip_id):
        self._rec("create_ephemeral_public_ip")
        return {"ip_address": "5.6.7.8"}

    def create_ipv6(self, profile, vnic_id):
        self._rec("create_ipv6")
        return {"ip_address": "2603:c020::1"}

    def list_vcns(self, profile, compartment_id):
        self._rec("list_vcns")
        return [{"id": "vcn1", "display_name": "vcn", "lifecycle_state": "AVAILABLE"}]

    def get_vcn(self, profile, vcn_id):
        self._rec("get_vcn")
        return {"id": vcn_id, "ipv6_cidr_blocks": [], "default_route_table_id": "rt1"}

    def create_vcn(self, profile, compartment_id, cidr_block, display_name, dns_label):
        self._rec("create_vcn")
        return {"id": "vcn-new", "display_name": display_name,
                "default_route_table_id": "rt-new"}

    def add_vcn_ipv6_cidr(self, profile, vcn_id):
        self._rec("add_vcn_ipv6_cidr")

    def list_subnets(self, profile, compartment_id, vcn_id=None):
        self._rec("list_subnets")
        return [{"id": "sub1", "display_name": "public", "cidr_block": "10.0.0.0/24",
                 "lifecycle_state": "AVAILABLE"}]

    def get_subnet(self, profile, subnet_id):
        self._rec("get_subnet")
        return dict(self.subnet)

    def create_subnet(self, profile, compartment_id, vcn_id, cidr_block,
                      display_name, dns_label):
        self._rec("create_subnet")
        return {"id": "sub-new", "display_name": display_name}

    def update_subnet_ipv6_cidr(self, profile, subnet_id, ipv6_cidr):
        self._rec("update_subnet_ipv6_cidr")

    def list_internet_gateways(self, profile, compartment_id, vcn_id):
        self._rec("list_internet_gateways")
        return [{"id": "ig1", "lifecycle_state": "AVAILABLE"}]

    def create_internet_gateway(self, profile, compartment_id, vcn_id, display_name):
        self._rec("create_internet_gateway")
        return {"id": "ig-new"}

    def get_route_table(self, profile, rt_id):
        self._rec("get_route_table")
        return {"route_rules": [_to_sdk_shape(r) for r in self.route_rules_existing]}

    def update_route_rules(self, profile, rt_id, rules):
        self._rec("update_route_rules")
        self.route_rules = rules

    def get_security_list(self, profile, security_list_id):
        self._rec("get_security_list")
        # 真实 SDK 回的是 snake_case。这里必须照做——
        # 之前原样回显面板写进去的 camelCase，等于把「读回来」这一步的
        # 键名问题全遮住了，结果 tcp_options 取不到的 bug 一直测不出来。
        return {"ingress_security_rules": [_to_sdk_shape(r) for r in self.ingress_rules],
                "display_name": "Default Security List"}

    def update_ingress_rules(self, profile, security_list_id, rules):
        self._rec("update_ingress_rules")
        self.ingress_rules = list(rules)

    # ---------- Block storage ----------
    def list_boot_volumes(self, profile, compartment_id, availability_domain=None):
        self._rec("list_boot_volumes")
        return list(self.boot_volumes)

    def list_volumes(self, profile, compartment_id):
        self._rec("list_volumes")
        return list(self.volumes)

    def delete_boot_volume(self, profile, volume_id):
        self._rec("delete_boot_volume")

    def delete_volume(self, profile, volume_id):
        self._rec("delete_volume")

    def update_boot_volume_vpus(self, profile, volume_id, vpus):
        self._rec("update_boot_volume_vpus")
        return {"id": volume_id, "vpus_per_gb": vpus}

    def update_volume_vpus(self, profile, volume_id, vpus):
        self._rec("update_volume_vpus")
        return {"id": volume_id, "vpus_per_gb": vpus}

    # ---------- Monitoring ----------
    def summarize_metrics(self, profile, compartment_id, namespace, query,
                          start_time, end_time, subtree=False):
        self._rec("summarize_metrics")
        # 真实 SDK 回 snake_case 的 aggregated_datapoints
        return [_to_sdk_shape(m) for m in self.metrics
                if not m.get("name") or m["name"] in query]

    def list_limit_values(self, profile, compartment_id, service_name):
        self._rec("list_limit_values")
        return list(self.limit_values)

    def list_subscriptions(self, profile, compartment_id):
        self._rec("list_subscriptions")
        return list(self.subscriptions)

    # ---------- 详情 / 改规格 / 控制台 / 备份 ----------
    def get_instance(self, profile, instance_id):
        self._rec("get_instance")
        for i in self.instances:
            if i.get("id") == instance_id:
                return _to_sdk_shape(i)
        return _to_sdk_shape({"id": instance_id, "shape": "VM.Standard.A1.Flex",
                              "compartment-id": "cid", "availability-domain": "AD-1",
                              "lifecycle-state": "RUNNING", "display-name": "box",
                              "shape-config": {"ocpus": 1, "memory-in-gbs": 6}})

    def update_instance_shape(self, profile, instance_id, ocpus, memory_gb):
        self._rec("update_instance_shape")
        self.resized = {"instance_id": instance_id, "ocpus": ocpus, "memory_gb": memory_gb}
        return {"id": instance_id, "shape_config": {"ocpus": ocpus, "memory_in_gbs": memory_gb}}

    def create_console_connection(self, profile, instance_id, public_key):
        self._rec("create_console_connection")
        conn = {"id": "conn1", "instance-id": instance_id, "lifecycle-state": "ACTIVE",
                "connection-string": "ssh -o ProxyCommand=... ocid1.instance..x",
                "vnc-connection-string": "ssh -L 5900:... ocid1.instance..x",
                "service-host-key-fingerprint": "aa:bb"}
        self.console.append(conn)
        return _to_sdk_shape(conn)

    def list_console_connections(self, profile, compartment_id, instance_id=None):
        self._rec("list_console_connections")
        rows = [c for c in self.console
                if not instance_id or c.get("instance-id") == instance_id]
        return [_to_sdk_shape(c) for c in rows]

    def delete_console_connection(self, profile, connection_id):
        self._rec("delete_console_connection")
        self.console = [c for c in self.console if c.get("id") != connection_id]

    def get_boot_volume(self, profile, boot_volume_id):
        self._rec("get_boot_volume")
        for v in self.boot_volumes:
            if v.get("id") == boot_volume_id:
                return _to_sdk_shape(v)
        return _to_sdk_shape({"id": boot_volume_id, "size-in-gbs": 50,
                              "display-name": "bv", "lifecycle-state": "AVAILABLE"})

    def update_boot_volume_size(self, profile, boot_volume_id, size_gb):
        self._rec("update_boot_volume_size")
        self.resized_volume = {"id": boot_volume_id, "size_gb": size_gb}
        return {"id": boot_volume_id, "size_in_gbs": size_gb}

    def create_boot_volume_backup(self, profile, boot_volume_id, display_name, backup_type):
        self._rec("create_boot_volume_backup")
        row = {"id": "bk-new", "boot-volume-id": boot_volume_id,
               "display-name": display_name, "type": backup_type,
               "lifecycle-state": "CREATING", "size-in-gbs": 50}
        self.backups.append(row)
        return _to_sdk_shape(row)

    def list_boot_volume_backups(self, profile, compartment_id, boot_volume_id=None):
        self._rec("list_boot_volume_backups")
        rows = [b for b in self.backups
                if not boot_volume_id or b.get("boot-volume-id") == boot_volume_id]
        return [_to_sdk_shape(b) for b in rows]

    def delete_boot_volume_backup(self, profile, backup_id):
        self._rec("delete_boot_volume_backup")
        self.backups = [b for b in self.backups if b.get("id") != backup_id]

    def create_boot_volume_from_backup(self, profile, compartment_id, availability_domain,
                                       backup_id, display_name, size_gb):
        self._rec("create_boot_volume_from_backup")
        self.restored = {"backup_id": backup_id, "name": display_name, "size_gb": size_gb}
        return {"id": "bv-restored", "display_name": display_name}

    def home_region(self, profile, tenancy_id):
        self._rec("home_region")
        return self.home_region_name

    def list_invoices(self, profile, tenancy_id, home_region, limit):
        self._rec("list_invoices")
        return [_to_sdk_shape(x) for x in self.invoices[:limit]]

    def summarize_usage(self, profile, tenant_id, start, end, granularity, group_by):
        self._rec("summarize_usage")
        return [_to_sdk_shape(x) for x in self.usage_items]

    def list_domains(self, profile, compartment_id):
        self._rec("list_domains")
        return list(self.domains)

    def list_password_policies(self, profile, domain_url):
        self._rec("list_password_policies")
        return [_to_sdk_shape(p) for p in self.password_policies.get(domain_url, [])]

    def set_password_expiry(self, profile, domain_url, policy_id, days):
        self._rec("set_password_expiry")
        for p in self.password_policies.get(domain_url, []):
            if p.get("id") == policy_id:
                p["passwordExpiresAfter"] = int(days) if int(days) > 0 else None
                return dict(p)
        raise OCIError("policy not found")

    def get_subscription(self, profile, subscription_id):
        self._rec("get_subscription")
        return dict(self.subscription_details.get(subscription_id, {}))
