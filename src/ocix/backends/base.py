"""与 OCI 交互的后端接口。

面板有两套实现：
- ``cli``：调用官方 oci 命令行（每次调用约 1.1 秒进程开销）
- ``sdk``：进程内调用官方 oci Python SDK（约 50-150ms）

两者返回的都是普通 dict / list。字段命名不强制统一——
CLI 输出 kebab-case，SDK 输出 snake_case，上层一律用
``oci_helpers._get(d, "kebab-case", "snake_case")`` 读取，两种都认。

复杂入参（安全列表规则、路由规则）统一用 **camelCase 的 dict**，
与 OCI REST API 的线上格式一致；各后端自行转换成自己需要的形式。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Backend(ABC):
    """一次请求内可复用；实现需保证线程安全（面板会并发调用）。"""

    name: str = "base"

    @abstractmethod
    def version(self) -> str:
        """后端版本描述，展示在 /api/health。"""

    # ---------- Identity ----------
    @abstractmethod
    def get_user(self, profile: str, user_id: str) -> dict: ...

    @abstractmethod
    def list_compartments(self, profile: str, tenancy_id: str) -> list: ...

    @abstractmethod
    def list_availability_domains(self, profile: str, compartment_id: str) -> list:
        """返回 [{"name": ...}, ...]。"""

    # ---------- Compute ----------
    @abstractmethod
    def list_instances(self, profile: str, compartment_id: str) -> list: ...

    @abstractmethod
    def instance_action(self, profile: str, instance_id: str, action: str) -> dict: ...

    @abstractmethod
    def launch_instance(self, profile: str, spec: dict) -> dict:
        """spec 字段：compartment_id / availability_domain / display_name / image_id /
        subnet_id / shape / boot_gb / ssh_public_key / assign_ipv6 /
        ocpus+memory_gb（仅 Flex 规格）。"""

    @abstractmethod
    def terminate_instance(self, profile: str, instance_id: str,
                           preserve_boot_volume: bool) -> None: ...

    @abstractmethod
    def list_vnic_attachments(self, profile: str, compartment_id: str,
                              instance_id: str = None) -> list: ...

    @abstractmethod
    def list_boot_volume_attachments(self, profile: str, compartment_id: str,
                                     availability_domain: str) -> list: ...

    @abstractmethod
    def list_volume_attachments(self, profile: str, compartment_id: str) -> list: ...

    @abstractmethod
    def list_images(self, profile: str, compartment_id: str, operating_system: str,
                    shape: str = None) -> list: ...

    # ---------- Network ----------
    @abstractmethod
    def get_vnic(self, profile: str, vnic_id: str) -> dict: ...

    @abstractmethod
    def list_private_ips(self, profile: str, vnic_id: str) -> list: ...

    @abstractmethod
    def get_public_ip_by_private_ip(self, profile: str, private_ip_id: str) -> dict: ...

    @abstractmethod
    def delete_public_ip(self, profile: str, public_ip_id: str) -> None: ...

    @abstractmethod
    def create_ephemeral_public_ip(self, profile: str, compartment_id: str,
                                   private_ip_id: str) -> dict: ...

    @abstractmethod
    def create_ipv6(self, profile: str, vnic_id: str) -> dict: ...

    @abstractmethod
    def list_vcns(self, profile: str, compartment_id: str) -> list: ...

    @abstractmethod
    def get_vcn(self, profile: str, vcn_id: str) -> dict: ...

    @abstractmethod
    def create_vcn(self, profile: str, compartment_id: str, cidr_block: str,
                   display_name: str, dns_label: str) -> dict: ...

    @abstractmethod
    def add_vcn_ipv6_cidr(self, profile: str, vcn_id: str) -> None:
        """申请一段 Oracle 分配的 /56（isOracleGuaAllocationEnabled=true）。"""

    @abstractmethod
    def list_subnets(self, profile: str, compartment_id: str, vcn_id: str = None) -> list: ...

    @abstractmethod
    def get_subnet(self, profile: str, subnet_id: str) -> dict: ...

    @abstractmethod
    def create_subnet(self, profile: str, compartment_id: str, vcn_id: str,
                      cidr_block: str, display_name: str, dns_label: str) -> dict: ...

    @abstractmethod
    def update_subnet_ipv6_cidr(self, profile: str, subnet_id: str, ipv6_cidr: str) -> None: ...

    @abstractmethod
    def list_internet_gateways(self, profile: str, compartment_id: str, vcn_id: str) -> list: ...

    @abstractmethod
    def create_internet_gateway(self, profile: str, compartment_id: str, vcn_id: str,
                                display_name: str) -> dict: ...

    @abstractmethod
    def get_route_table(self, profile: str, rt_id: str) -> dict: ...

    @abstractmethod
    def update_route_rules(self, profile: str, rt_id: str, rules: list) -> None:
        """rules 为 camelCase dict 列表：destination / destinationType / networkEntityId。"""

    @abstractmethod
    def get_security_list(self, profile: str, security_list_id: str) -> dict: ...

    @abstractmethod
    def update_ingress_rules(self, profile: str, security_list_id: str, rules: list) -> None:
        """rules 为 camelCase dict 列表：protocol / source / sourceType /
        isStateless / description / tcpOptions / udpOptions / icmpOptions。"""

    # ---------- Block storage ----------
    @abstractmethod
    def list_boot_volumes(self, profile: str, compartment_id: str,
                          availability_domain: str = None) -> list: ...

    @abstractmethod
    def list_volumes(self, profile: str, compartment_id: str) -> list: ...

    @abstractmethod
    def delete_boot_volume(self, profile: str, volume_id: str) -> None: ...

    @abstractmethod
    def delete_volume(self, profile: str, volume_id: str) -> None: ...

    @abstractmethod
    def update_boot_volume_vpus(self, profile: str, volume_id: str, vpus: int) -> dict: ...

    @abstractmethod
    def update_volume_vpus(self, profile: str, volume_id: str, vpus: int) -> dict: ...

    # ---------- Monitoring ----------
    @abstractmethod
    def summarize_metrics(self, profile: str, compartment_id: str, namespace: str,
                          query: str, start_time: str, end_time: str) -> list: ...
