"""用 root + 密码登录时要下发的 cloud-init，以及密码存放的标签名。

**先说清楚这条路的代价**，因为它是用安全换方便：

1. 密码明文存在实例的自由标签里。凡是能读这个租户的人都看得见——
   OCI 控制台、`oci compute instance get`、任何调 API 的工具。
   标签不是保险箱。好处是换台电脑、换个浏览器都能直接看到密码，
   这正是要它的原因。
2. 密码明文也会出现在实例的 user_data 里，而 user_data **实例自己读得到**
   （169.254.169.254 那个元数据服务）。也就是说机器上任何一个本地账号
   都能把 root 密码读出来。本来可以下发密码哈希来避开这一条，但 Python
   3.13 移除了 crypt 模块，而本项目声明支持 3.10+，为这个引一个新依赖
   不划算。下面会在开机末尾把落盘的那份 user-data 删掉，
   但元数据服务里的那份删不掉。
3. Oracle 的 Ubuntu 镜像默认关掉了 root 登录和密码认证。打开之后，
   挂着公网 IP 的机器几分钟内就会被扫。**务必用长密码**，
   面板默认生成 20 位。

只想图省事的话，其实用密钥登录 + 面板自带的网页终端就够了，
不需要开这条。
"""

from __future__ import annotations

import secrets
import string

# 密码存在实例的自由标签里，键名固定成这个
ROOT_PW_TAG = "ocix-root-pw"

# 去掉了容易看混的 0/O、1/l/I —— 这个密码是要用眼睛抄的
_ALPHABET = (
    "".join(c for c in string.ascii_letters + string.digits if c not in "0O1lI")
    + "!@#%^*_-+="
)


def generate_password(length: int = 20) -> str:
    """生成一个能直接用的 root 密码。

    长度默认 20：这台机器的 22 端口一开就会被全网扫，短密码撑不住。
    """
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def root_password_cloud_config(password: str) -> str:
    """生成开启 root + 密码登录的 cloud-config。

    全面兼容 Alpine / Ubuntu / Debian / RHEL / CentOS 等多种 Linux 发行版：
      * Ubuntu/Debian: 写入 99-ocix.conf drop-in 并移除 60-cloudimg-settings.conf
      * Alpine/其他: 确保 /etc/ssh/sshd_config 写入 PermitRootLogin 与 PasswordAuthentication
      * 兼容 systemd、OpenRC (Alpine) 与 SysVinit
    """
    if not password:
        raise ValueError("密码不能为空")
    drop_in = "/etc/ssh/sshd_config.d/99-ocix.conf"
    lines = "PermitRootLogin yes\\nPasswordAuthentication yes\\n"
    cmds = [
        "mkdir -p /etc/ssh/sshd_config.d",
        # 写入 99- 开头的 drop-in（字典序最后，针对 Ubuntu/Debian 云镜像）
        f"printf '{lines}' > {drop_in}",
        # Ubuntu 云镜像用 60- 这个 drop-in 把密码登录关掉，不删它上面那条就白写
        "rm -f /etc/ssh/sshd_config.d/60-cloudimg-settings.conf",
        # 兼容 Alpine / 不支持 sshd_config.d 的系统，直接确保主配置文件开启
        "grep -q 'PermitRootLogin yes' /etc/ssh/sshd_config 2>/dev/null "
        "|| echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config",
        "grep -q 'PasswordAuthentication yes' /etc/ssh/sshd_config 2>/dev/null "
        "|| echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config",
        # 重启 ssh 服务：兼容 systemd、OpenRC (Alpine)、service 与 init.d
        "systemctl restart ssh 2>/dev/null || systemctl restart sshd 2>/dev/null "
        "|| rc-service sshd restart 2>/dev/null || service ssh restart 2>/dev/null "
        "|| service sshd restart 2>/dev/null || /etc/init.d/ssh restart 2>/dev/null "
        "|| /etc/init.d/sshd restart 2>/dev/null || true",
        # 把落盘的那份 user-data 抹掉，别让密码留在磁盘上
        "shred -u /var/lib/cloud/instance/user-data.txt* 2>/dev/null "
        "|| rm -f /var/lib/cloud/instance/user-data.txt*",
    ]
    # 使用通用的 sh -c 执行，兼容 Alpine（默认无 bash）和 Debian/Ubuntu
    runcmd = "\n".join(f'  - [ sh, -c, "{c}" ]' for c in cmds)
    return f"""#cloud-config
disable_root: false
ssh_pwauth: true
chpasswd:
  expire: false
  list: |
    root:{password}
runcmd:
{runcmd}
"""
