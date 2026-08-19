import html
import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

from . import db

logger = logging.getLogger("ocix.notifier")

# 北京时间时区 (UTC+8)
BEIJING_TZ = timezone(timedelta(hours=8))


def beijing_now_str() -> str:
    """获取当前北京时间 (UTC+8) 格式化字符串。"""
    return datetime.now(BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S")


_SERVER_IP_CACHE: str | None = None
_SERVER_IP_CACHE_TIME: float = 0.0


def get_server_public_ip() -> str:
    """获取面板宿主机公网 IP（带 1 小时内存缓存）。"""
    global _SERVER_IP_CACHE, _SERVER_IP_CACHE_TIME
    now = datetime.now().timestamp()
    if _SERVER_IP_CACHE and (now - _SERVER_IP_CACHE_TIME < 3600):
        return _SERVER_IP_CACHE

    services = [
        "https://api64.ipify.org",
        "https://ifconfig.me/ip",
        "https://icanhazip.com",
    ]
    for url in services:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                ip = resp.read().decode("utf-8").strip()
                if ip and len(ip) <= 45:
                    _SERVER_IP_CACHE = ip
                    _SERVER_IP_CACHE_TIME = now
                    return ip
        except Exception:
            continue

    return _SERVER_IP_CACHE or "未知"


def _post_telegram(bot_token: str, chat_id: str, text: str, parse_mode: str = "HTML") -> tuple[bool, str]:
    """同步发送 Telegram 消息。返回 (是否成功, 提示信息)。"""
    if not bot_token or not chat_id:
        return False, "Bot Token 或 Chat ID 未配置"
    url = f"https://api.telegram.org/bot{bot_token.strip()}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id.strip(),
        "text": text,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "ocix-panel/notifier"},
    )
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                return True, "发送成功"
            return False, data.get("description", "Telegram API 返回错误")
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode("utf-8"))
            msg = err_body.get("description", str(e))
        except Exception:
            msg = str(e)
        return False, f"Telegram 请求失败 ({e.code}): {msg}"
    except Exception as e:
        return False, f"连不上 Telegram API: {e}"


def send_telegram_async(text: str, bot_token: str | None = None, chat_id: str | None = None) -> None:
    """非阻塞后台线程发送 Telegram 消息。"""
    from .crypto import decrypt_token

    if bot_token:
        token = bot_token.strip()
    else:
        token_encrypted = db.get_setting("tg_bot_token", "")
        token = decrypt_token(token_encrypted) if token_encrypted else ""

    cid = (chat_id or db.get_setting("tg_chat_id", "")).strip()

    if not token or not cid:
        return

    enabled_raw = db.get_setting("tg_enabled", "1")
    enabled = enabled_raw not in ("0", "false", "False", "")
    if not enabled and not bot_token:
        return

    def _worker():
        try:
            ok, msg = _post_telegram(token, cid, text)
            if not ok:
                logger.warning("Telegram 通知发送失败: %s", msg)
        except Exception as e:
            logger.warning("Telegram 后台发送异常: %s", e)

    threading.Thread(target=_worker, daemon=True, name="ocix-tg-notify").start()


def test_telegram(bot_token: str, chat_id: str) -> tuple[bool, str]:
    now_str = beijing_now_str()
    server_ip = get_server_public_ip()
    msg = (
        "<b>OCIX · Telegram 通知测试</b>\n\n"
        "<b>状态</b>: 连接成功\n"
        f"<b>服务器 IP</b>: <code>{html.escape(server_ip)}</code>\n\n"
        f"<b>时间</b>: {now_str}"
    )
    return _post_telegram(bot_token, chat_id, msg)


def _detect_arch(shape: str) -> str:
    s = (shape or "").lower()
    if "a1" in s or "arm" in s or "aarch64" in s:
        return "ARM"
    if "intel" in s or "standard3" in s or "optimized3" in s:
        return "Intel"
    return "AMD"


def notify_instance_created(
    profile: str,
    display_name: str,
    shape: str,
    ocpus: int | float | None = None,
    memory_gb: int | float | None = None,
    boot_gb: int | None = None,
    public_ip: str | None = None,
    ipv6: str | None = None,
    region: str | None = None,
    root_password: str | None = None,
    vpus_per_gb: int | None = None,
    instance_id: str | None = None,
    compartment_id: str | None = None,
    success: bool = True,
    error_msg: str | None = None,
    elapsed: float = 0.0,
) -> None:
    from .crypto import decrypt_token

    token_encrypted = db.get_setting("tg_bot_token", "").strip()
    token = decrypt_token(token_encrypted) if token_encrypted else ""
    cid = db.get_setting("tg_chat_id", "").strip()
    enabled_raw = db.get_setting("tg_enabled", "1")
    enabled = enabled_raw not in ("0", "false", "False", "")

    if not token or not cid or not enabled:
        return

    def _notify_task():
        nonlocal public_ip, ipv6
        # 如果当前尚未拿到公网 IP，在后台等待直到 OCI 分配完成
        if success and not public_ip and instance_id:
            import time

            from .oci_helpers import attach_ips
            for _ in range(12):
                try:
                    res = attach_ips(profile, [{"id": instance_id, "compartment_id": compartment_id}])
                    if res and res[0].get("_public_ip"):
                        public_ip = res[0].get("_public_ip")
                        if not ipv6 and res[0].get("_ipv6"):
                            ipv6 = res[0].get("_ipv6")
                        break
                except Exception:
                    pass
                time.sleep(2.5)

        p_safe = html.escape(str(profile))
        s_safe = html.escape(str(shape))
        r_safe = html.escape(str(region or "默认区域"))
        arch = _detect_arch(shape)

        if success:
            c_val = float(ocpus or 1.0)
            m_val = float(memory_gb or 1.0)
            b_val = int(boot_gb or 50)
            vpu_str = f"({vpus_per_gb}VPUs)" if vpus_per_gb else ""
            cfg_str = f"{c_val:.1f}C / {m_val:.1f}GB / {b_val}GB{vpu_str}"

            lines = [
                "🎉 <b>实例创建成功！（1/1）</b>\n",
                f"👤 <b>租户</b>：{p_safe}",
                f"🌍 <b>区域</b>：{r_safe}",
                f"⚙️ <b>架构</b>：{arch}",
                f"💻 <b>Shape</b>：{s_safe}",
                f"📊 <b>配置</b>：{cfg_str}",
                f"🌐 <b>公网IP</b>：<code>{html.escape(public_ip or '已分配/查看控制台')}</code>",
            ]
            if ipv6:
                lines.append(f"🌐 <b>IPv6</b>：<code>{html.escape(str(ipv6))}</code>")
            if root_password:
                lines.append(f"🔑 <b>密码</b>：<code>{html.escape(root_password)}</code>")
            text = "\n".join(lines)
        else:
            err_safe = html.escape(str(error_msg or '未知错误'))
            text = (
                "⚠️ <b>实例创建失败</b>\n\n"
                f"👤 <b>租户</b>：{p_safe}\n"
                f"🌍 <b>区域</b>：{r_safe}\n"
                f"💻 <b>Shape</b>：{s_safe}\n\n"
                f"❌ <b>原因</b>：<i>{err_safe}</i>"
            )
        try:
            ok, msg = _post_telegram(token, cid, text)
            if not ok:
                logger.warning("Telegram 通知发送失败: %s", msg)
        except Exception as e:
            logger.warning("Telegram 后台发送异常: %s", e)

    threading.Thread(target=_notify_task, daemon=True, name="ocix-tg-create-notify").start()


def notify_instance_terminated(
    profile: str,
    instance_id: str,
    display_name: str | None = None,
    preserve_boot_volume: bool = False,
    user: str = "",
    ip: str = "",
) -> None:
    """通知实例终止/删机。"""
    p_safe = html.escape(str(profile))
    n_safe = html.escape(str(display_name or instance_id))
    boot_action = "保留引导卷" if preserve_boot_volume else "引导卷已一并删除"
    actor = f"{user} ({ip})" if ip else user
    actor_safe = html.escape(str(actor or "admin"))

    text = (
        "🗑️ <b>实例已终止 / 删除</b>\n\n"
        f"👤 <b>租户</b>：{p_safe}\n"
        f"💻 <b>实例</b>：{n_safe}\n"
        f"💾 <b>存储</b>：{boot_action}\n"
        f"👤 <b>操作人</b>：{actor_safe}"
    )

    send_telegram_async(text)


def notify_instance_action(
    profile: str,
    instance_id: str,
    action: str,
    display_name: str | None = None,
    success: bool = True,
    error_msg: str | None = None,
    user: str = "",
    ip: str = "",
) -> None:
    """通知实例生命周期操作（开机/关机/重启）。"""
    action_map = {
        "START": ("🚀", "实例已开机"),
        "STOP": ("🛑", "实例已强行停止"),
        "SOFTSTOP": ("⏸️", "实例已关机"),
        "RESET": ("🔄", "实例已重启"),
        "SOFTRESET": ("🔄", "实例已软重启"),
    }
    icon, title = action_map.get(action.upper(), ("⚡", f"实例操作 ({action})"))
    p_safe = html.escape(str(profile))
    n_safe = html.escape(str(display_name or instance_id))
    actor = f"{user} ({ip})" if ip else user
    actor_safe = html.escape(str(actor or "admin"))

    if success:
        text = (
            f"{icon} <b>{title}</b>\n\n"
            f"👤 <b>租户</b>：{p_safe}\n"
            f"💻 <b>实例</b>：{n_safe}\n"
            f"👤 <b>操作人</b>：{actor_safe}"
        )
    else:
        err_safe = html.escape(str(error_msg or "操作失败"))
        text = (
            f"❌ <b>{title} 失败</b>\n\n"
            f"👤 <b>租户</b>：{p_safe}\n"
            f"💻 <b>实例</b>：{n_safe}\n"
            f"❌ <b>原因</b>：<i>{err_safe}</i>\n"
            f"👤 <b>操作人</b>：{actor_safe}"
        )

    send_telegram_async(text)
