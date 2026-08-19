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

全面原生兼容 **Ubuntu / Debian / Alpine Linux** 等主流 Linux 系统（需要 Docker）：

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

**安装依赖环境（未安装 Docker 时）：**
- **Ubuntu / Debian**：`curl -fsSL https://get.docker.com | sh`
- **Alpine Linux**：`apk add --no-cache git bash curl docker docker-cli-compose openssl && rc-update add docker default && service docker start`

---

## 功能

**实例**
- 多账户：每个 OCI 账户 = 一个 `[PROFILE]`，在网页端导入管理，顶部随时切换
- **锁定账户**：锁定后所有页面固定用它，顶部不再能误切（设置存服务端，换浏览器也生效）
- **机位视图**：Always Free 就那么几台机器，实例页默认按「机位」摆——
  一台一张卡，公网 IP 是卡上最大的信息，没开满的位置画成虚线空位点一下就去开机；
  卡片上方是机队额度条（AMD 台数 / ARM OCPU / ARM 内存，直接从实例列表算，不额外打 OCI）。
  要批量操作或机器多的时候可以切回表格视图，选择记在本地
- 一键 开机 / 关机 / 重启，支持批量
- Compartment 下拉选择，可递归查子 compartment
- 公网 / 内网 / IPv6 地址直接显示并一键复制
- **更换公网 IPv4**、**一键给已有机器加 IPv6**、**终止实例**（需输入实例名确认）
- **实例详情抽屉**：规格 / 网络 / 引导卷 / 串口控制台 / 备份，一处看全
- **串口控制台**：SSH 进不去时的带外救命通道，公钥可直接选文件导入
- **网页终端**：浏览器里直接开 SSH——直连实例，或走串口控制台
- **改规格**：ARM Flex 机型在线调 OCPU / 内存，闸门会算上其它实例已占的份额
- **引导卷备份与还原**、**引导卷扩容**（都过 200GB 额度闸门）

**建机前的额度闸门**
- 只提供 Always Free 规格与 Ubuntu 镜像（仅最新两个大版本）
- 子网全自动：所有机器共用一个，账户第一次开机时自动建 VCN + 网关 + 子网
- 勾一下就分配 IPv6：自动开通子网 IPv6，实例建好后自动挂上地址
- **root + 密码登录**（可选）：密码存在实例的自由标签上，
  换台电脑、换个浏览器都能在实例卡片上直接看到。**这是拿安全换方便**，
  开之前先看下面那节
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
- **账户等级**：免费号 / 已升级，依据租户订阅记录。**从不自动检测**——
  它要往 OCI 打一次订阅查询，而进页面多半只是想看看账户还在不在。
  点「检测全部等级」整表查，或在某一行的操作栏点「检测等级」只查那一个号
  （整表是串行的，号多时要等很久）
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

> 粘的时候不用太讲究：带 BOM、整段缩进、CRLF、行尾注释、键名大写、
> 粘重了同一个键、甚至只抄中间几行没带 `[段名]`——这些都能正常解析。
> 中文输入法打出的全角 `＝` 也会自动纠正。


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

### root + 密码登录：先看清代价

建实例时可以选「root + 密码」，密码会存在实例的**自由标签**上，
面板的机位卡片和 OCI 控制台都能看到——换台电脑也不用翻记录。
方便是真方便，代价也是真的：

1. **密码是明文存的。** 凡是能读这个租户的人都看得见（控制台、
   `oci compute instance get`、任何调 API 的工具）。标签不是保险箱。
2. **实例自己也能读到密码。** 密码会进 `user_data`，而 user_data 可以从
   机器内部通过元数据服务（`169.254.169.254`）读出来——机器上任何一个
   本地账号都能拿到 root 密码。本来可以只下发密码哈希来避开这条，
   但 Python 3.13 移除了 `crypt` 模块，而本项目声明支持 3.10+，
   为这个引一个新依赖不划算。开机末尾会把**落盘**的那份 user-data 抹掉，
   但元数据服务里的那份删不掉。
3. **开了密码登录就会被扫。** Oracle 的 Ubuntu 镜像默认关掉 root 登录和
   密码认证，打开之后挂公网 IP 的机器几分钟内就有人来试。
   所以密码强制至少 12 位，面板默认生成 20 位。

只是图省事的话，**用密钥登录 + 面板自带的网页终端就够了**，不必开这条。

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

## 建实例是后台任务，不是一个长请求

点「创建实例」之后接口**立刻返回一个任务号**，活儿在服务端的后台线程里干，
前端轮询进度并显示当前步骤（核算额度 → 准备网络 → 下单 → 放行端口 → 分配 IPv6）。

之所以必须这样，是因为这条链路本来就慢：账户第一次开机要建 VCN、网关、子网，
每步都等资源变成 AVAILABLE（`oci.wait_until`，各自上限 180 秒）；实例建出来还要
等网卡挂好才能分配 IPv6（再 90 秒）。静态上界六百多秒，正常首次开机也要一分多钟。

**而面板挂在 Cloudflare 后面时，对方 100 秒拿不到响应就直接给访客一个 524
「源站超时」页。** 麻烦在于请求并没有被取消：它还在服务器上跑，实例照样会建出来，
用户看到的却是报错，于是重试，于是又建一台。

改成任务之后：
- 接口一秒内返回，不再撞 Cloudflare 的超时
- **同一个账户 + 同一个实例名，前一单还在跑时重复提交会拿回同一个任务号**，
  手快点两下不会变成两台（去重只覆盖「还在飞」的那段；跑完之后再提交
  同名的是允许的——你可能真要再建一台）
- 关掉页面不影响创建，任务照常跑完（结果保留一小时）

额度闸门一点没松，只是判定结果通过轮询回给前端。

## 请求 OCI 的节制

面板默认**不在后台偷偷请求**：

- 不开「自动刷新」就没有任何定时器在跑，静置时请求数为 **0**
- 更新进度只在真的有更新在执行时才轮询，停在更新页不会反复请求
- **同一时刻只有一个账户在跟 OCI 通信**：多账户并发容易触发对方限流，
  出错也难归因。串行由服务端的闸门保证，绕过界面直接调接口同样有效。
  闸门按引用计数放锁——同账户允许并发（一个页面要查好几样），
  但要等最后一个同账户线程离开，别的账户才进得来
- 列表页只取用得上的字段（例如账户列表不查服务配额）
- **账户等级从不自动检测**：进「账户配置」「免费额度」、切换账户都不会触发，
  只有点按钮才查

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

## 界面配色

**亮色主题**：底色 `#FCFCFB`，卡片纯白，文字是暖深灰，强调色是赤陶。
整体偏纸感、克制，只有一个强调色。

| 用途 | 颜色 | 色相 |
|---|---|---|
| 强调色 / 可操作 | `#b04f2d` 赤陶 | 16° |
| 运行中 | `#2d774b` 深绿 | 144° |
| 需留意 / 过渡中 | `#7f601b` 深琥珀 | 41° |
| 出错 / 已终止 | `#b03a52` 深玫瑰 | 348° |

前景色不是挑出来的，是**算出来的**：每一个都压深到「在最暗的那层背景
（hover 面）上仍有 4.5:1」为止。暗色时代的那套直接搬过来只有 1.1~3.0:1，
等于全部消失——翻主题不是换一个底色值的事。

网页终端仍然是深色。终端就该是深色的，亮色的 xterm 反而别扭。

### 一个踩了两次的坑

面板的变量块写成 `:root:root`，故意重复一次。

element-plus 的变量和面板的变量定义在同一层，**同特异性下谁后加载谁赢**，
而 CDN 回退是运行时把组件库的 css 追加到 `<head>` 末尾的，正好排在面板
这段 `<style>` 后面。暗色时代靠 `:root.dark` 的两个类压住；翻成亮色后
`.dark` 类去掉了，选择器顺手写成 `:root`，特异性掉回一个类——
同一个 bug 立刻复发，实测 32 处对比度不达标。`:root:root` 拿回 (0,2,0)。

### 登录页是一幕场景

整屏是一颗缓慢自转的粒子星球，表单浮在上面。星球上的每一个亮点都是
**Oracle 真实开放的区域**（us-ashburn-1、ap-tokyo-1、us-sanjose-1…），
区域之间沿大圆弧飞着数据包；每跑完一趟，底部状态条会写出刚才那条链路。

底部三块机位标签就是 Always Free 的额度（2×AMD 微型 + 1×ARM），
各带一颗呼吸灯，相位错开。旁边写的是真事：共用子网 `10.0.0.0/24`，
面板首次开机时真的会创建它。

**没有引入任何 3D 库。** 手写 canvas，两百行不到：斐波那契球面点阵、
球面线性插值算大圆弧、透视投影。面板是单文件、无构建、CDN 挂了要能退化，
为一个登录页背景拖进几百 KB 依赖和这套约束是冲突的。

登录成功后场景立刻停掉 `requestAnimationFrame`，标签页切走时也跳过绘制。
关掉动效（`prefers-reduced-motion`）时只画一帧静止的星球，不起循环。

场景的数学在 `tests/scene_check.js` 里用假画布验证：坐标不出 NaN
（canvas 遇到 NaN 是静默不画，最容易漏）、数据包会跑完并换下一对区域、
区域名都是真实的、关动效时确实只画一帧。

### 页面结构

每页一个 `h1`（页名，视觉上不显示——左侧导航已经标了当前位置，密集面板
再摆一行大标题是浪费纵向空间，但读屏器需要它定位），卡片标题是 `h2`,
抽屉小节是 `h3`,不跳级。

顶部有一条「跳到主内容」，平时收在屏幕外，键盘 Tab 时滑出来——
不然每换一页都要 Tab 过顶栏和 11 项导航才够得着内容。

卡片标题 15px、正文 14px。这个顺序看着理所当然，但之前是反的
（标题 13px 比正文还小）。

### 运行中是一盏呼吸灯

在跑的机器，状态灯会缓慢地一呼一吸——和真实机架上的指示灯一样。

关键是它**不能和「正在开机」的脉冲长得像**，那恰恰是最需要分清的一对：

|      | 呼吸（运行中） | 脉冲（过渡中） |
|---|---|---|
| 周期 | 3.2 秒（慢一倍） | 1.6 秒 |
| 几何 | 光晕原地涨落，灯始终不灭 | 环向外扩散后消失 |
| 缓动 | ease-in-out，平稳 | ease-out，冲出去 |
| 读感 | 稳定的心跳 | 正在发生 |

列表里多台机器时**相位是错开的**——同步呼吸像圣诞灯，真机架不会同步。

除此之外全站不再加任何动效。关掉动效（`prefers-reduced-motion`）时，
呼吸退化成静态光晕，仍然比其它状态亮一圈，不会变成一颗死灯。

### 状态不只靠颜色

每个状态点同时用**颜色和形态**两个通道：

| 状态 | 颜色 | 形态 |
|---|---|---|
| 运行中 | 鼠尾草绿 | 实心圆 + **呼吸光晕** |
| 已终止 | 陶土玫瑰 | **空心圆环** |
| 正在变化 | 沙黄 | 实心 + 向外脉动 |
| 需留意（账户已升级）| 沙黄 | **圆角方块** + 静止环 |
| 未知 | 灰 | 实心圆 |

方块那一档是为关掉动效的人准备的：`prefers-reduced-motion` 下「正在变化」
只剩一个静止的点，和同为沙黄的「需留意」就只剩形状能分了。

同样的做法用在别处：语义标签除了颜色还有线型（实线 / 虚线 / 加粗），
监控图两条曲线一实一虚，额度超标除了变色还打斜纹，链接一律带下划线。
色弱的人、亮度调到最低的屏幕、关掉动效的场景，都还剩下另一条线索。

所有文字对各自背景都过 WCAG AA 4.5:1（禁用态的按钮除外，那是标准明确豁免的）。

`tests/test_theme.py` 把这些钉住：饱和度上限、状态色的色相间隔、
状态点的形态区分、呼吸与脉冲的节奏差、登录页各灯的相位、
曲线的虚实区分、链接下划线，改坏任何一条都会红。

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

## 出事之后怎么办

**SSH 进不去了**（防火墙关错、`fstab` 写坏、sshd 配置改崩）——
实例详情里建一个**串口控制台**连接（公钥选文件导入即可），走带外链路，
不依赖实例自身网络。这是不重建实例的唯一进入方式。
建好后点「在网页里连接」，直接在浏览器里进串口控制台，不用本地 SSH 客户端。

**系统被自己搞坏了**——先在详情里对引导卷「立即备份」，出事再「还原」。
但要清楚：**OCI 没有原地回滚**，还原是用备份造一个**新的引导卷**，
原实例不受影响，之后要拿这个新卷开一台实例才能真正用上。
新卷同样占 200GB 额度，所以面板会先算一遍，不够就直接拦下——
免得还原成功了额度却被撑爆，反而开不出机器。

**磁盘不够用**——详情里可以给引导卷扩容（只能变大）。
扩完云端卷是大了，但系统里还看不到，要进实例执行：

```bash
sudo /usr/libexec/oci-growfs
```

**ARM 想换个配比**——详情里直接改 OCPU / 内存，不用销毁重建。
闸门会把**其它 ARM 实例已经占掉的份额**算进来：两台各要 4 OCPU 是过不了的。
OCI 可能会为此重启实例。

## 网页终端

实例详情里有两个入口：**直连 SSH**（日常用，连实例的 22 端口）和
**串口控制台**（救命用，走 Oracle 跳板，网络/防火墙坏了也能进）。

### 私钥不落盘

浏览器自己连不了 SSH，所以私钥必须经过面板后端。这里的处理是：

- 私钥随建立连接的那一条 WebSocket 消息传入，**只存在于内存**，
  会话结束即销毁；任何时候都不写磁盘、不进日志、不进审计
- 连上之后前端也立刻把私钥从内存里抹掉
- 因此**每次开终端都要重新选一次私钥文件**——这是刻意的，
  换来的是「面板被攻破也拿不到你的服务器 shell」

如果哪天觉得每次选文件太烦，可以改成存服务端，但那等于把 shell 凭据
和 OCI 凭据放在同一个篮子里，我不建议。

### WebSocket 的鉴权

浏览器的 WebSocket API 加不了 `Authorization` 头，而把 JWT 放进 URL
会漏进反代日志和浏览器历史。所以先用已鉴权的接口换一张**一次性票据**
（30 秒过期、用一次作废），WebSocket 只带票据。

> 终端依赖 `paramiko`，且**必须是 5.0 以下**。
> paramiko 5 移除了 `ssh-rsa`（SHA-1）主机密钥算法，而 Oracle 串口控制台的
> 网关只提供这一种，握手会直接失败：`Incompatible ssh peer (no acceptable host key)`。
> 实测 5.0 必失败、4.0 正常，`requirements.txt` 已钉死 `<5`。
> 没装或版本不对时面板照常跑，只是终端不可用——`/api/terminal/available`
> 会报告 paramiko 版本与 `ssh_rsa_host_key` 是否满足。

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
