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
- 多账户：每个 OCI 账户 = 一个 `[PROFILE]`，在网页端导入管理，顶部随时切换
- **锁定账户**：锁定后所有页面固定用它，顶部不再能误切（设置存服务端，换浏览器也生效）
- 一键 开机 / 关机 / 重启，支持批量
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
- 免费额度对照、CPU / 内存时序图（1 小时 ~ 7 天）、**当月出网流量**
- **账单**：当月消费（按服务拆分）与账单记录（待支付 / 已支付 / 已逾期）
- 审计日志（登录 / 改密 / 开关机 / 账户增删，含来源 IP）
- 登录鉴权（JWT）、限流、自动 HTTPS
- **改 Oracle 账号的密码有效期**：默认 120 天强制改密，面板里一键设为永不过期
- **账户等级**：账户列表直接标出免费号 / 已升级，依据租户订阅记录
- **网页一键更新**：点一下就更新，不用 SSH

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
│   ├── backends/        OCI 通信层（官方 Python SDK）
│   ├── routers/         API 路由
│   ├── freetier.py      Always Free 额度模型与预检闸门
│   ├── oci_helpers.py   业务逻辑（额度、防火墙、账户等级…）
│   └── web/index.html   单文件面板 (Vue3 + Element Plus)
├── deploy/              docker compose 编排 + Caddy 模板 + systemd 单元
├── docker/Dockerfile
├── scripts/
│   ├── install.sh       一键部署（域名 / IP 二选一）+ 安装更新代理
│   ├── release.sh       提版本号 + 打 tag + 推送
│   ├── update.sh        在线更新（拉代码 + 重建 + 重启）
│   ├── update-agent.sh  宿主机更新代理，网页一键更新由它执行
│   └── ocix.sh          docker compose 包装
├── tests/               pytest
└── VERSION              唯一版本号来源
```

## 请求 OCI 的节制

面板默认**不在后台偷偷请求**：

- 不开「自动刷新」就没有任何定时器在跑，静置时请求数为 **0**
- 更新进度只在真的有更新在执行时才轮询，停在更新页不会反复请求
- **同一时刻只有一个账户在跟 OCI 通信**：多账户并发容易触发对方限流，
  出错也难归因。串行由服务端的闸门保证，绕过界面直接调接口同样有效
- 列表页只取用得上的字段（例如账户列表不查服务配额）

## 常用命令

`ocix.sh` 会自动带上正确的 compose 参数，不用管当前是域名模式还是直连模式：

```bash
bash /opt/ocix/scripts/ocix.sh logs -f      # 跟踪日志
bash /opt/ocix/scripts/ocix.sh restart      # 重启
bash /opt/ocix/scripts/ocix.sh ps           # 看状态
```

## 更新

「更新」页会显示当前版本并检查 GitHub 上有没有新版本，**点「立即更新」就能直接更新**，
配置、密码、审计记录都不动。原理见下面的[网页一键更新](#网页一键更新)。

也可以在服务器上手动执行：

```bash
bash /opt/ocix/scripts/update.sh
```

```bash
bash /opt/ocix/scripts/update.sh --check    # 只看有没有新版本和改了什么
```

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

版本号只存在 `VERSION` 一处，登录后在「更新」页或 `/api/diagnostics` 可以看到。

## 底层实现

面板通过 **Oracle 官方 `oci` Python SDK** 直接调用 OCI REST API：进程内完成、复用 HTTPS 连接，
**不依赖 oci 命令行**。凭据仍按官方 `~/.oci/config` 规则处理，面板不自己签名。

代码分层：`src/ocix/backends/` 定义接口与 SDK 实现，`oci_helpers.py` 只调接口。
保留这层抽象是为了测试——业务逻辑不依赖 SDK，测试用假后端就能验证全部行为。

早期版本用的是 oci 命令行（起子进程），每次调用固定约 1.1 秒进程开销，
且命令行的参数表面与 API 并不一一对应，踩过几个只有连上真实租户才暴露的坑
（`--create-vnic-details` 不存在、`get-public-ip-by-private-ip-id` 不是子命令）。
改用 SDK 后这些都能**离线核对**：`tests/test_backends.py` 会检查每个方法、
每个模型字段、每处传参方式是否与安装的 SDK 一致。

### 发往 Oracle 的文本一律是英文

写进 OCI 的内容（安全规则描述、实例名、VCN 名）**只允许可打印 ASCII**：
这些值会原样出现在 OCI 控制台、`oci` 命令行输出和 Oracle 侧记录里，
混进中文在不少工具中会变成乱码或问号，出问题时对不上号。

面板自己写的规则统一是 `ocix: allow all (IPv4)`、`ocix: keep ssh` 这类前缀，
在控制台里一眼能认出哪些是面板加的。填了中文的实例名或规则描述会被**挡下并提示**，
而不是静默替换——名字被悄悄改掉更难排查。

界面本身仍是中文，只有出境到 Oracle 的字段受此限制。

### 一个反复踩的坑：SDK 返回的是 snake_case

改用 SDK 后返回值统一是 `snake_case`，但不少地方还沿用 CLI 时代的
`kebab-case` / `camelCase` 键名。取不到不会报错，只会静默变成 `None`，
后果一度很严重：

- 监控页永远空白（`aggregated_datapoints` 取不到）
- **加一条防火墙规则会把已有 TCP 规则放大成全端口**（`tcp_options` 丢了，
  重写规则集时端口范围一起没了）
- 开 IPv6 会清空已有路由的目标（`network_entity_id` 取不到）

现在 `_get()` 会自动补上每个键的 snake_case 变体，不再指望每个调用点写全；
测试里的假后端也改成**按 SDK 形态返回**——之前它原样回显面板写进去的
camelCase，等于把这一整类问题全遮住了。

### 关于响应速度

**调用次数就是等待时间**，所以优化的核心是减少调用。以「3 个 compartment、3 台实例」为例：

| 页面 | 早期 | 现在 |
|---|---|---|
| 实例页 | 14 次 | **5 次** |
| 存储页 | 22 次 | **6 次** |
| 防火墙加一条规则 | 11 次 | **8 次** |

做法：实例页不再顺带查免费额度；默认只查租户根 compartment；
可用域、主网卡、子网安全列表、镜像、子网列表全部加缓存；
实例与卷列表缓存 30 秒且**写操作后立即失效**。

界面上，任何请求在飞时顶部会有进度条，刷新按钮显示「刷新中…」并带旋转图标，
首次加载显示骨架屏、二次刷新保留旧数据并压暗。

## 账户等级

「账户配置」页的账户列表直接显示每个账户是**免费号**还是**已升级为付费**，
「免费额度」页也能看到当前账户的详细判定依据。

判定**只看租户的订阅记录**（`payment_model` / `subscription_tier`）：

| 订阅记录 | 结论 |
|---|---|
| `Pay as you go` / `Monthly` / `Annual` / `Commit` | 已升级 / 付费（PAYG） |
| 含 `Free` / `Trial` / `Promo` | Always Free / 未升级 |
| 读不到（多为权限不足） | 无法确定，并给出要加的策略 |

两个**不能**用来判断的信号，都踩过：

- **服务配额（Service Limits）不能用**。Oracle 在纯免费账号上同样会给付费机型返回
  非零配额，拿它当证据会把免费号判成已升级。配额现在只作参考展示，不参与结论。
- **有没有产生账单也不能用**。升级成 PAYG 但只跑免费资源的账号账单是 0，
  和纯免费号分不开。

读不到订阅记录时不会瞎猜，会直接说「无法确定」并提示补一条策略：

```
Allow group <你所在的组> to inspect subscriptions in tenancy
```

> 分辨这个的实际意义：免费号超额只是开不出机器，**已升级的号超额是真扣钱**。

## 网页一键更新

「更新」页点「立即更新」即可，不用再 SSH 上去敲命令。

面板容器**刻意没有** docker 权限——它只往交换目录里写一个「请求更新」的标记，
真正执行的是宿主机上常驻的 `ocix-updater` 服务，跑的是固定的 `scripts/update.sh`，
标记文件的内容不会以任何形式进入命令行。这样即便面板被攻破，
攻击者能做的也只是触发一次正常更新，而不是拿到整台宿主机。

代理由 `install.sh` 用 root 安装为 systemd 服务。没装或没运行时，
更新页会直接说明并给出处理命令，而不是让你对着转圈等。

```bash
systemctl status ocix-updater
```

## Oracle 账号密码有效期

免费 / 新版租户默认 **120 天**必须改一次控制台密码，这条规则存在
Identity Domain 的 `passwordExpiresAfter` 里。「密码」页可以直接改：

- 填 **0** → 删掉该字段，控制台密码不再到期
- 填具体天数 → 改成那个值

走 SCIM PatchOp，等同于在 OCI 控制台里改密码策略，需要当前 API 用户对该域有写权限。
经典 IAM 租户没有 Identity Domain，面板会直接说明而不是报个空。

> 面板**自己**的登录密码没有有效期。曾经加过一个，那是把这条 Oracle 规则
> 误解成了面板自身的策略，已经移除。

## 账单与流量

「账单」页分两块，对应 OCI 两套不同的接口，别混：

| | 回答什么 | 接口 |
|---|---|---|
| 当月消费 | 这个月**花了**多少，按天与按服务拆开 | Usage API |
| 账单记录 | Oracle 实际开了多少张票、**付没付、逾期没有** | OSP Gateway |

纯 Always Free 账号两边都是空的——那是正确答案，不是故障，界面会如实说明。

「免费额度」页另有**当月出网流量**（Always Free 每月含 10 TB）。这个数是
**上限估算**，不是账单：`VnicToNetworkBytes` 把 VCN 内、区域内这些 Oracle
并不计费的流量也算进去了，而且按区域统计而免费额度是按租户算的。
实际计费流量只会更少，这两点都写在界面上，不做无声的四舍五入。

## 安全要点

- OCI 凭据**不入库**：只引用 `~/.oci/config`，私钥落在容器卷并设 `600`
- 面板密码以 bcrypt 持久化在数据卷，重启不丢；改密后已签发的 JWT 立即失效
- **忘记密码**：改 `.env` 里的 `OCIX_ADMIN_PASSWORD` 再重启即可重置
- 所有写操作记入审计日志（含来源 IP）；登录与 API 均有限流
- 反代场景按 `X-Forwarded-For` **最右侧**条目识别真实 IP（左侧可被客户端伪造）；
  `OCIX_TRUST_PROXY` 默认关闭，只有确实在自己的反代后面才该打开
- 应用自身下发 CSP、`X-Frame-Options: DENY`、`nosniff` 等安全响应头，
  直连模式（无 Caddy）同样受保护；`/api/*` 一律 `Cache-Control: no-store`
- `/docs`、`/openapi.json` 默认关闭，需要时用 `OCIX_ENABLE_DOCS=true` 打开
- 未鉴权只能拿到 `{ok, service}`，版本与配置路径需登录后经 `/api/diagnostics` 获取
- SQL 全部走参数绑定；需要拼列名的地方（`upsert_profile`）用白名单卡死
- 前端不使用 `v-html`，全部走 Vue 的 `{{ }}` 转义——实例名、错误信息等
  来自 OCI 的文本不会被当成 HTML 执行
- 无命令注入面：改用 OCI Python SDK 后，代码里不存在 `subprocess` / `eval` / `exec`

> JWT 存在 `localStorage`，理论上一旦出现 XSS 就会被读走。
> 面板已无 HTML 注入点且有 CSP 兜底，风险可控；
> `tests/test_security.py` 对上述每一条都有回归用例。

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 探活（无需鉴权，只回 ok） |
| GET | `/api/diagnostics` | 环境自检：版本 / SDK / 配置状态（需登录） |
| GET | `/api/profiles/{name}/tier` | 账户等级；`?limits=false` 可省掉配额查询 |
| GET · PUT | `/api/auth/password-policy` | **面板**登录密码有效期（0 = 永不过期） |
| GET · PUT | `/api/profiles/{name}/console-password-policy` | **Oracle 账号**控制台密码有效期 |
| POST | `/api/system/update` | 请求更新（由宿主机代理执行） |
| GET | `/api/system/update/status` | 更新进度与日志 |
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
