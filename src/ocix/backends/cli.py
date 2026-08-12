"""基于官方 oci 命令行的后端实现。

行为与面板早期版本完全一致：每个操作起一个 oci 子进程。
优点是每一步都能复制成命令自己复现；代价是每次调用固定约 1.1 秒进程开销。
"""

from __future__ import annotations

import base64
import json

from ..config import OCI_LAUNCH_TIMEOUT
from ..oci_cli import cli_version, run_oci
from .base import Backend


def _data(resp) -> object:
    return (resp or {}).get("data")


class CLIBackend(Backend):
    name = "cli"

    def version(self) -> str:
        return cli_version() or "oci CLI"

    # ---------- Identity ----------
    def get_user(self, profile, user_id):
        return _data(run_oci(profile, "iam", "user", "get", "--user-id", user_id)) or {}

    def list_compartments(self, profile, tenancy_id):
        return _data(run_oci(
            profile, "iam", "compartment", "list",
            "--compartment-id", tenancy_id,
            "--compartment-id-in-subtree", "true",
            "--access-level", "ACCESSIBLE", "--all")) or []

    def list_availability_domains(self, profile, compartment_id):
        return _data(run_oci(profile, "iam", "availability-domain", "list",
                             "--compartment-id", compartment_id)) or []

    # ---------- Compute ----------
    def list_instances(self, profile, compartment_id):
        return _data(run_oci(profile, "compute", "instance", "list",
                             "--compartment-id", compartment_id, "--all")) or []

    def instance_action(self, profile, instance_id, action):
        return _data(run_oci(profile, "compute", "instance", "action",
                             "--instance-id", instance_id,
                             "--action", action.upper())) or {}

    def launch_instance(self, profile, spec):
        args = [
            "compute", "instance", "launch",
            "--compartment-id", spec["compartment_id"],
            "--availability-domain", spec["availability_domain"],
            "--display-name", spec["display_name"],
            "--image-id", spec["image_id"],
            "--shape", spec["shape"],
            "--boot-volume-size-in-gbs", str(spec["boot_gb"]),
            # 只用各版本 CLI 都有的扁平参数：--create-vnic-details 在 CLI 里不存在，
            # 因此 IPv6 是创建完成后再单独挂上去的
            "--subnet-id", spec["subnet_id"],
            "--assign-public-ip", "true",
        ]
        if spec.get("shape_config"):
            args += ["--shape-config", json.dumps(spec["shape_config"])]

        metadata = {"ssh_authorized_keys": spec["ssh_public_key"]}
        if spec.get("user_data"):
            metadata["user_data"] = base64.b64encode(
                spec["user_data"].encode("utf-8")).decode("ascii")
        args += ["--metadata", json.dumps(metadata)]
        return _data(run_oci(profile, *args, timeout=OCI_LAUNCH_TIMEOUT)) or {}

    def terminate_instance(self, profile, instance_id, preserve_boot_volume):
        run_oci(profile, "compute", "instance", "terminate",
                "--instance-id", instance_id, "--force",
                "--preserve-boot-volume", "true" if preserve_boot_volume else "false")

    def list_vnic_attachments(self, profile, compartment_id, instance_id=None):
        args = ["compute", "vnic-attachment", "list", "--compartment-id", compartment_id, "--all"]
        if instance_id:
            args += ["--instance-id", instance_id]
        return _data(run_oci(profile, *args)) or []

    def list_boot_volume_attachments(self, profile, compartment_id, availability_domain):
        return _data(run_oci(profile, "compute", "boot-volume-attachment", "list",
                             "--compartment-id", compartment_id,
                             "--availability-domain", availability_domain, "--all")) or []

    def list_volume_attachments(self, profile, compartment_id):
        return _data(run_oci(profile, "compute", "volume-attachment", "list",
                             "--compartment-id", compartment_id, "--all")) or []

    def list_images(self, profile, compartment_id, operating_system, shape=None):
        args = ["compute", "image", "list", "--compartment-id", compartment_id,
                "--operating-system", operating_system,
                "--sort-by", "TIMECREATED", "--sort-order", "DESC", "--all"]
        if shape:
            args += ["--shape", shape]
        return _data(run_oci(profile, *args)) or []

    # ---------- Network ----------
    def get_vnic(self, profile, vnic_id):
        return _data(run_oci(profile, "network", "vnic", "get", "--vnic-id", vnic_id)) or {}

    def list_private_ips(self, profile, vnic_id):
        return _data(run_oci(profile, "network", "private-ip", "list",
                             "--vnic-id", vnic_id, "--all")) or []

    def get_public_ip_by_private_ip(self, profile, private_ip_id):
        # 是 `public-ip get --private-ip-id`；没有 get-public-ip-by-private-ip-id 这个子命令
        return _data(run_oci(profile, "network", "public-ip", "get",
                             "--private-ip-id", private_ip_id)) or {}

    def delete_public_ip(self, profile, public_ip_id):
        run_oci(profile, "network", "public-ip", "delete",
                "--public-ip-id", public_ip_id, "--force")

    def create_ephemeral_public_ip(self, profile, compartment_id, private_ip_id):
        return _data(run_oci(profile, "network", "public-ip", "create",
                             "--compartment-id", compartment_id,
                             "--lifetime", "EPHEMERAL",
                             "--private-ip-id", private_ip_id,
                             "--wait-for-state", "ASSIGNED",
                             timeout=OCI_LAUNCH_TIMEOUT)) or {}

    def create_ipv6(self, profile, vnic_id):
        return _data(run_oci(profile, "network", "ipv6", "create",
                             "--vnic-id", vnic_id, timeout=OCI_LAUNCH_TIMEOUT)) or {}

    def list_vcns(self, profile, compartment_id):
        return _data(run_oci(profile, "network", "vcn", "list",
                             "--compartment-id", compartment_id, "--all")) or []

    def get_vcn(self, profile, vcn_id):
        return _data(run_oci(profile, "network", "vcn", "get", "--vcn-id", vcn_id)) or {}

    def create_vcn(self, profile, compartment_id, cidr_block, display_name, dns_label):
        return _data(run_oci(profile, "network", "vcn", "create",
                             "--compartment-id", compartment_id,
                             "--cidr-block", cidr_block,
                             "--display-name", display_name,
                             "--dns-label", dns_label,
                             "--wait-for-state", "AVAILABLE",
                             timeout=OCI_LAUNCH_TIMEOUT)) or {}

    def add_vcn_ipv6_cidr(self, profile, vcn_id):
        from ..oci_cli import OCICLIError
        try:
            run_oci(profile, "network", "vcn", "add-ipv6-vcn-cidr", "--vcn-id", vcn_id,
                    "--is-oracle-gua-allocation-enabled", "true", timeout=OCI_LAUNCH_TIMEOUT)
            return
        except OCICLIError as e:
            msg = (e.message or "").lower()
            if "already" in msg or "limitexceeded" in msg or "conflict" in msg:
                return
            if "no such option" not in msg and "unrecognized" not in msg and "usage:" not in msg:
                raise
        run_oci(profile, "network", "vcn", "add-ipv6-vcn-cidr", "--vcn-id", vcn_id,
                timeout=OCI_LAUNCH_TIMEOUT)

    def list_subnets(self, profile, compartment_id, vcn_id=None):
        args = ["network", "subnet", "list", "--compartment-id", compartment_id, "--all"]
        if vcn_id:
            args += ["--vcn-id", vcn_id]
        return _data(run_oci(profile, *args)) or []

    def get_subnet(self, profile, subnet_id):
        return _data(run_oci(profile, "network", "subnet", "get",
                             "--subnet-id", subnet_id)) or {}

    def create_subnet(self, profile, compartment_id, vcn_id, cidr_block, display_name, dns_label):
        return _data(run_oci(profile, "network", "subnet", "create",
                             "--compartment-id", compartment_id, "--vcn-id", vcn_id,
                             "--cidr-block", cidr_block, "--display-name", display_name,
                             "--dns-label", dns_label, "--wait-for-state", "AVAILABLE",
                             timeout=OCI_LAUNCH_TIMEOUT)) or {}

    def update_subnet_ipv6_cidr(self, profile, subnet_id, ipv6_cidr):
        run_oci(profile, "network", "subnet", "update", "--subnet-id", subnet_id,
                "--ipv6-cidr-block", ipv6_cidr, "--force", timeout=OCI_LAUNCH_TIMEOUT)

    def list_internet_gateways(self, profile, compartment_id, vcn_id):
        return _data(run_oci(profile, "network", "internet-gateway", "list",
                             "--compartment-id", compartment_id,
                             "--vcn-id", vcn_id, "--all")) or []

    def create_internet_gateway(self, profile, compartment_id, vcn_id, display_name):
        return _data(run_oci(profile, "network", "internet-gateway", "create",
                             "--compartment-id", compartment_id, "--vcn-id", vcn_id,
                             "--is-enabled", "true", "--display-name", display_name,
                             "--wait-for-state", "AVAILABLE",
                             timeout=OCI_LAUNCH_TIMEOUT)) or {}

    def get_route_table(self, profile, rt_id):
        return _data(run_oci(profile, "network", "route-table", "get", "--rt-id", rt_id)) or {}

    def update_route_rules(self, profile, rt_id, rules):
        run_oci(profile, "network", "route-table", "update", "--rt-id", rt_id, "--force",
                "--route-rules", json.dumps(rules), timeout=OCI_LAUNCH_TIMEOUT)

    def get_security_list(self, profile, security_list_id):
        return _data(run_oci(profile, "network", "security-list", "get",
                             "--security-list-id", security_list_id)) or {}

    def update_ingress_rules(self, profile, security_list_id, rules):
        run_oci(profile, "network", "security-list", "update",
                "--security-list-id", security_list_id, "--force",
                "--ingress-security-rules", json.dumps(rules), timeout=OCI_LAUNCH_TIMEOUT)

    # ---------- Block storage ----------
    def list_boot_volumes(self, profile, compartment_id, availability_domain=None):
        args = ["bv", "boot-volume", "list", "--compartment-id", compartment_id, "--all"]
        if availability_domain:
            args += ["--availability-domain", availability_domain]
        return _data(run_oci(profile, *args)) or []

    def list_volumes(self, profile, compartment_id):
        return _data(run_oci(profile, "bv", "volume", "list",
                             "--compartment-id", compartment_id, "--all")) or []

    def delete_boot_volume(self, profile, volume_id):
        run_oci(profile, "bv", "boot-volume", "delete", "--boot-volume-id", volume_id, "--force")

    def delete_volume(self, profile, volume_id):
        run_oci(profile, "bv", "volume", "delete", "--volume-id", volume_id, "--force")

    def update_boot_volume_vpus(self, profile, volume_id, vpus):
        return _data(run_oci(profile, "bv", "boot-volume", "update",
                             "--boot-volume-id", volume_id, "--vpus-per-gb", str(vpus),
                             timeout=OCI_LAUNCH_TIMEOUT)) or {}

    def update_volume_vpus(self, profile, volume_id, vpus):
        return _data(run_oci(profile, "bv", "volume", "update",
                             "--volume-id", volume_id, "--vpus-per-gb", str(vpus),
                             timeout=OCI_LAUNCH_TIMEOUT)) or {}

    # ---------- Monitoring ----------
    def summarize_metrics(self, profile, compartment_id, namespace, query, start_time, end_time):
        return _data(run_oci(
            profile, "monitoring", "metric-data", "summarize-metrics-data",
            "--compartment-id", compartment_id,
            "--namespace", namespace,
            "--query-text", query,
            "--start-time", start_time,
            "--end-time", end_time)) or []
