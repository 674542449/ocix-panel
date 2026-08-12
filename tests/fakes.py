"""可记录调用的假后端，用于在不连 OCI 的前提下验证上层逻辑。

因为它实现的是 ``Backend`` 接口，同一份业务逻辑测试对 cli / sdk 两种实现都成立。
"""

from __future__ import annotations

from ocix.backends.base import Backend


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
        return []

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
        return {"route_rules": []}

    def update_route_rules(self, profile, rt_id, rules):
        self._rec("update_route_rules")
        self.route_rules = rules

    def get_security_list(self, profile, security_list_id):
        self._rec("get_security_list")
        return {"ingress_security_rules": list(self.ingress_rules),
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
                          start_time, end_time):
        self._rec("summarize_metrics")
        return []

    def list_limit_values(self, profile, compartment_id, service_name):
        self._rec("list_limit_values")
        return list(self.limit_values)

    def list_subscriptions(self, profile, compartment_id):
        self._rec("list_subscriptions")
        return list(self.subscriptions)

    def get_subscription(self, profile, subscription_id):
        self._rec("get_subscription")
        return dict(self.subscription_details.get(subscription_id, {}))
