<div align="center">

# OCIX

**Oracle Cloud Always Free 开机面板**

多账户实例管理 · 建机前额度硬闸门 · 防火墙 / IPv6 / 存储一站管完

[![CI](https://github.com/674542449/ocix-panel/actions/workflows/ci.yml/badge.svg)](https://github.com/674542449/ocix-panel/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-informational.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)

</div>

---

## 一键搭建

在一台 Ubuntu / Debian 服务器上执行（需要 Docker）：

```bash
sudo git clone https://github.com/674542449/ocix-panel.git /opt/ocix && cd /opt/ocix && sudo bash scripts/install.sh
```

装到 **`/opt/ocix`**。脚本会依次问你：**用域名还是 IP+端口**访问、**管理员用户名和密码**（明文显示便于核对）。会话密钥自动生成，不用你填任何密钥类的东西。

**不想交互，一条命令装完：**

```bash
# 方式 A：域名 + 自动 HTTPS（证书在部署时就申请好）
sudo git clone https://github.com/674542449/ocix-panel.git /opt/ocix && cd /opt/ocix && \
  sudo bash scripts/install.sh --domain panel.example.com --email you@example.com \
    --admin-user admin --admin-password 你的密码
```

```bash
# 方式 B：IP + 端口 直连（无 HTTPS，适合内网或临时使用）
sudo git clone https://github.com/674542449/ocix-panel.git /opt/ocix && cd /opt/ocix && \
  sudo bash scripts/install.sh --port 8000 --admin-user admin --admin-password 你的密码
```

> **域名模式**需要域名的 A 记录已指向本机公网 IP，且 80 / 443 没被占用、安全组已放行。
> 脚本会**先查 DNS 再部署**，并在部署阶段等到证书真正签发落盘才算成功——不会等你第一次访问时才去申请。
>
> **直连模式**没有 HTTPS，密码是明文传输，别长期挂公网。记得在云厂商安全组放行你选的端口。

> 从别的目录跑也行，脚本会自动把项目搬到 `/opt/ocix` 再继续；加 `--dir /其他/路径` 可改位置，`--here` 则就地安装。

没装 Docker 的话先跑：`curl -fsSL https://get.docker.com | sh`

---

## 功能

**实例**
- 多账户：每个 OCI 账户 = 一个 `[PROFILE]`，全部在网页端导入管理
- 跨账户总览（并发查询），一键 开机 / 关机 / 重启，支持批量
- Compartment 下拉选择，可递归查子 compartment
- 公网 / 内网 / IPv6 地址直接显示并一键复制
- **更换公网 IPv4**、**一键给已有机器加 IPv6**、**终止实例**（需输入实例名确认）

**建机前的额度闸门**
- 只提供 Always Free 规格与 Ubuntu 镜像（仅最新两个大版本）
- 子网全自动：所有机器共用一个，账户第一次开机时自动建 VCN + 网关 + 子网
- 勾一下就分配 IPv6：自动开通子网 IPv6，实例建好后自动挂上地址
- 建完**自动放行全部入站端口**（有 IPv6 则 `::/0` 一并放行），可在表单里关掉
- 右侧实时预检：每项显示 `当前 +新增 = 建后 / 上限`，超标即禁用创建
- 闸门在**服务端**，绕过界面直接调接口同样拦得住

**网络与存储**
- 防火墙：查看云端安全列表规则，可放行全部端口、按协议/端口新增单条规则、逐条删除
- IPv6：创建时勾选即可，或在实例列表里给已有机器一键补上
- 存储：卷清单、**未挂载卷标红可删**、卷性能 0–120 VPU/GB 自由调节

**运维**
- 免费额度对照、CPU / 内存时序图（1 小时 ~ 7 天）
- 审计日志（登录 / 改密 / 开关机 / 账户增删，含来源 IP）
- 登录鉴权（JWT）、限流、自动 HTTPS

> ⚠️ **合规说明**：本面板**仅管理你自己的 OCI 租户**，不做账号注册 / 养号 / 自动抢机。
> 被回收的免费实例**只提醒、不自动重生**；ARM 抢不到容量时只报错、**不后台循环重试**。
> 使用前请自行阅读 Oracle *Always Free* 与 *Acceptable Use Policy* 条款。

---

## 装好之后

1. 打开面板给出的地址，用 `admin` + 随机密码登录，**先去「密码」页改掉**
2. 到「账户配置」粘贴 `~/.oci/config` 里的段落，并**上传或粘贴私钥**
   （面板跑在容器里，读不到你本机的 `.pem` 文件）
3. 「新建实例」建机器，「防火墙」放行端口

### 准备 OCI API 密钥

在**你自己的电脑**上生成，然后把公钥传到 OCI 控制台（用户设置 → API 密钥）：

```bash
mkdir -p ~/.oci && openssl genrsa -out ~/.oci/oci_api_key.pem 2048
openssl rsa -pubout -in ~/.oci/oci_api_key.pem -out ~/.oci/oci_api_key_public.pem
```

`~/.oci/config` 长这样，整段粘进面板即可：

```ini
[DEFAULT]
user=ocid1.user.oc1..xxxxxxxx
fingerprint=xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx:xx
key_file=~/.oci/oci_api_key.pem
tenancy=ocid1.tenancy.oc1..xxxxxxxx
region=us-ashburn-1
```

---

## 免费额度模型

建机前会先查真实用量，再核算「这台建出来之后」的结果，**超出一律拒绝**：

| 项目 | 上限 |
|---|---|
| AMD 微型 `VM.Standard.E2.1.Micro` | 2 台（1 OCPU / 1GB，规格固定） |
| ARM `VM.Standard.A1.Flex` | 合计 4 OCPU + 24GB，最多拆 4 台，每 OCPU ≤ 6GB |
| 存储（引导卷 + 块存储） | 200 GB，单个引导卷最小 50GB |
| 额外块存储卷（不含引导卷） | 2 个 |

典型满配：`2×AMD(50G) + 1×ARM 4C24G(100G) = 200GB`，正好用满且不超额。

几个容易踩的点：

- **引导卷计入 200GB**。每台机器自带一个引导卷，所以「2 个块存储卷」指的是引导卷**之外**额外挂的卷——3 台机器有 3 个引导卷是正常的。
- **孤儿卷是额度杀手**。终止实例时保留了引导卷，卷会继续占额度却不挂在任何机器上。「存储」页会标红并允许删除（面板终止实例时默认连引导卷一起删）。
- 面板会检测**非 Always Free 规格**的实例并告警——那部分一定在计费。

### 防火墙是两层，别只开一层

面板管的是 **OCI 云端安全列表**。Oracle 的 Ubuntu 镜像**实例内部还自带 iptables**，默认只放 22，两层都开端口才通：

```bash
sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT && sudo netfilter-persistent save
```

放行全部端口时会**先清空该子网现有的入站规则**（含 Oracle 预置的默认规则）再写入，
避免规则叠加。也可以只添加单条规则，例如仅放行 TCP 80。

安全列表是**子网级**的，任何改动都会影响同子网下所有实例。

---

## 目录结构

```
ocix-panel/
├── src/ocix/            后端（FastAPI）+ 前端单页 (web/)
│   ├── routers/         API 路由
│   ├── freetier.py      Always Free 额度模型与预检闸门
│   ├── oci_helpers.py   oci CLI 封装
│   └── web/index.html   单文件面板 (Vue3 + Element Plus)
├── deploy/              docker compose 编排 + Caddy 模板
├── docker/Dockerfile
├── scripts/
│   ├── install.sh       一键部署（域名 / IP 二选一）
│   ├── release.sh       提版本号 + 打 tag + 推送
│   ├── update.sh        在线更新（拉代码 + 重建 + 重启）
│   └── ocix.sh          docker compose 包装
├── tests/               pytest
└── VERSION              唯一版本号来源
```

## 常用命令

`ocix.sh` 会自动带上正确的 compose 参数，不用管当前是域名模式还是直连模式：

```bash
bash /opt/ocix/scripts/ocix.sh logs -f      # 跟踪日志
bash /opt/ocix/scripts/ocix.sh restart      # 重启
bash /opt/ocix/scripts/ocix.sh ps           # 看状态
```

## 更新

面板「更新」页会显示当前版本、检查 GitHub 上有没有新版本，并给出该执行的命令。
在服务器上跑这一条即可，配置、密码、审计记录都不动：

```bash
bash /opt/ocix/scripts/update.sh
```

```bash
bash /opt/ocix/scripts/update.sh --check    # 只看有没有新版本和改了什么
```

> 面板**不会**自己去改宿主机上的代码——那需要把 docker 控制权交给容器，风险远大于省这一步。

## 本地开发

```bash
pip install -r requirements.txt && pip install -e ".[dev]"
export OCIX_SESSION_SECRET=$(openssl rand -hex 32) OCIX_ADMIN_PASSWORD=devpass123
uvicorn ocix.main:app --reload --app-dir src --port 8000
```

前端是单文件 `src/ocix/web/index.html`，无需构建。本地没有 `web/assets/`（那是构建期下载的）时会自动回退到 CDN。

跑测试：`pytest` ｜ 检查代码：`ruff check src tests`

## 发布

```bash
bash scripts/release.sh          # 补丁位 +1，跑测试，打 tag 并推送
bash scripts/release.sh minor    # 次版本 +1
bash scripts/release.sh 1.2.0    # 指定版本号
```

版本号只存在 `VERSION` 一处，`/api/health` 会把它报出来。

## 底层实现

面板**不直接调用 OCI Python SDK**，所有操作都通过 Oracle 官方的 `oci` CLI（子进程）完成：

- 唯一出口是 `src/ocix/oci_cli.py` 的 `run_oci()`，统一加 `--config-file` / `--profile` / `--output json`
- 凭据完全交给 CLI 按官方 `~/.oci/config` 规则处理，面板不自己签名、不自己发 HTTP
- `oci-cli` 本身依赖官方 `oci` SDK，所以调用链仍是 官方 SDK → OCI REST API，只是多了一层官方 CLI
- 好处是行为与你在终端手敲 `oci ...` 完全一致，出问题可以直接复制命令自己复现

依赖只设下限 `oci-cli>=3.70,<4`：面板用到的子命令（`network ipv6 create`、
`network vcn add-ipv6-vcn-cidr` 等）在老版本 CLI 里可能不存在。

### 关于响应速度

每次调用 `oci` 都要新起一个进程并 import 整个 OCI SDK，**固定开销约 1.1 秒**
（实测：进程总耗时 1150ms，其中 import 682ms），与网络和 CDN 无关。
面板通过三点缓解：并发调用（默认 8 路）、compartment 列表缓存 5 分钟、
实例与卷列表缓存 30 秒且写操作后立即失效。

若仍嫌慢，最有效的做法是把「含子 compartment」关掉——它会让查询次数随
compartment 数量成倍增长。

## 安全要点

- OCI 凭据**不入库**：只引用 `~/.oci/config`，私钥落在容器卷并设 `600`
- 面板密码以 bcrypt 持久化在数据卷，重启不丢；改密后已签发的 JWT 立即失效
- **忘记密码**：改 `.env` 里的 `OCIX_ADMIN_PASSWORD` 再重启即可重置
- 所有写操作记入审计日志（含来源 IP）；登录与 API 均有限流
- 反代场景按 `X-Forwarded-For` 识别真实 IP

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 探活 + 环境自检（无需鉴权） |
| POST | `/api/auth/login` | 登录获取 JWT |
| GET | `/api/profiles` · POST `/api/profiles/import` | 账户管理 |
| GET | `/api/instances` · `/api/instances/all` | 实例列表 / 跨账户聚合 |
| POST | `/api/instances/action` · `/batch-action` | 开关机 / 批量 |
| POST | `/api/provision/preflight` | 额度预检（不创建资源） |
| POST | `/api/provision/instances` | 创建实例（服务端强制预检） |
| POST | `/api/provision/instances/change-ip` | 更换公网 IPv4 |
| GET/POST | `/api/provision/firewall*` | 防火墙查看 / 放行 / 撤销 |
| GET/POST | `/api/provision/storage*` | 卷清单 / 删除 / 性能 |
| GET | `/api/monitor/usage` · `/metrics` | 额度 / 监控 |
| GET | `/api/audit` | 审计日志 |
| GET | `/api/system/info` | 版本信息与更新指引 |

完整交互式文档：部署后访问 `/docs`。

## License

[MIT](LICENSE)
