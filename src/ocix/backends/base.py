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
    def list_availability_domains(self, profile: str, compartment_id: str,
                                  region: str = None) -> list:
        """返回 [{"name": ...}, ...]，支持指定 region。"""

    @abstractmethod
    def list_region_subscriptions(self, profile: str, tenancy_id: str) -> list:
        """返回已订阅的区域列表 [{"region_name": ...}, ...]。"""

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
    def create_compute_capacity_report(self, profile: str, compartment_id: str,
                                       availability_domain: str,
                                       shape_availabilities: list[dict],
                                       region: str = None) -> dict:
        """创建计算实例容量报告，探测各可用域与故障域是否有可用库存。"""

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

    # ---------- Limits / 订阅（判断账户等级）----------
    @abstractmethod
    def list_limit_values(self, profile: str, compartment_id: str,
                          service_name: str) -> list: ...

    @abstractmethod
    def list_subscriptions(self, profile: str, compartment_id: str) -> list: ...

    @abstractmethod
    def get_subscription(self, profile: str, subscription_id: str) -> dict: ...

    # ---------- Identity Domains（控制台登录密码策略）----------
    @abstractmethod
    def list_domains(self, profile: str, compartment_id: str) -> list: ...

    @abstractmethod
    def list_password_policies(self, profile: str, domain_url: str) -> list: ...

    @abstractmethod
    def set_password_expiry(self, profile: str, domain_url: str,
                            policy_id: str, days: int) -> dict: ...

    # ---------- 实例详情 / 改规格 ----------
    @abstractmethod
    def get_instance(self, profile: str, instance_id: str) -> dict: ...

    @abstractmethod
    def update_instance_shape(self, profile: str, instance_id: str,
                              ocpus: float, memory_gb: float) -> dict: ...

    # ---------- 串口控制台 ----------
    @abstractmethod
    def create_console_connection(self, profile: str, instance_id: str,
                                  public_key: str) -> dict: ...

    @abstractmethod
    def list_console_connections(self, profile: str, compartment_id: str,
                                 instance_id: str = None) -> list: ...

    @abstractmethod
    def delete_console_connection(self, profile: str, connection_id: str) -> None: ...

    # ---------- 引导卷备份 / 扩容 ----------
    @abstractmethod
    def get_boot_volume(self, profile: str, boot_volume_id: str) -> dict: ...

    @abstractmethod
    def update_boot_volume_size(self, profile: str, boot_volume_id: str,
                                size_gb: int) -> dict: ...

    @abstractmethod
    def create_boot_volume_backup(self, profile: str, boot_volume_id: str,
                                  display_name: str, backup_type: str) -> dict: ...

    @abstractmethod
    def list_boot_volume_backups(self, profile: str, compartment_id: str,
                                 boot_volume_id: str = None) -> list: ...

    @abstractmethod
    def delete_boot_volume_backup(self, profile: str, backup_id: str) -> None: ...

    @abstractmethod
    def create_boot_volume_from_backup(self, profile: str, compartment_id: str,
                                       availability_domain: str, backup_id: str,
                                       display_name: str, size_gb: int) -> dict: ...

    # ---------- 账单 / 用量 ----------
    @abstractmethod
    def home_region(self, profile: str, tenancy_id: str) -> str: ...

    @abstractmethod
    def list_invoices(self, profile: str, tenancy_id: str, home_region: str,
                      limit: int) -> list: ...

    @abstractmethod
    def list_payment_methods(self, profile: str, tenancy_id: str, home_region: str = None) -> list: ...

    @abstractmethod
    def summarize_usage(self, profile: str, tenant_id: str, start, end,
                        granularity: str, group_by: list) -> list: ...

    # ---------- Monitoring ----------
    @abstractmethod
    def summarize_metrics(self, profile: str, compartment_id: str, namespace: str,
                          query: str, start_time: str, end_time: str,
                          subtree: bool = False) -> list: ...
