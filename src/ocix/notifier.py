import html
import json
import logging
import threading
import urllib.error
import urllib.request
from datetime import datetime

from . import db

logger = logging.getLogger("ocix.notifier")


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
    token = (bot_token or db.get_setting("tg_bot_token", "")).strip()
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
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    msg = (
        "🤖 <b>【OCIX 控制台】Telegram 通知测试</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"• 状态: <b>连接成功</b>\n"
        f"• 时间: {now_str}\n"
        "• 说明: 实例开机、关机、创建与删除通知已就绪。"
    )
    return _post_telegram(bot_token, chat_id, msg)


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
    success: bool = True,
    error_msg: str | None = None,
    elapsed: float = 0.0,
) -> None:
    """通知实例创建（开机）成功或失败。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    region_info = f" ({html.escape(region)})" if region else ""
    p_safe = html.escape(str(profile))
    n_safe = html.escape(str(display_name))
    s_safe = html.escape(str(shape))

    if success:
        spec_parts = [s_safe]
        if ocpus or memory_gb:
            spec_parts.append(f"{ocpus or 1}C / {memory_gb or 1}G")
        if boot_gb:
            spec_parts.append(f"引导卷 {boot_gb}G")
        spec_str = " · ".join(spec_parts)

        text = (
            "🟢 <b>【OCIX】实例创建 / 开机成功</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"• <b>账户</b>: <code>{p_safe}</code>{region_info}\n"
            f"• <b>实例名</b>: <code>{n_safe}</code>\n"
            f"• <b>配置</b>: {spec_str}\n"
            f"• <b>公网 IPv4</b>: <code>{html.escape(public_ip or '分配中/未获取')}</code>\n"
        )
        if ipv6:
            text += f"• <b>IPv6</b>: <code>{html.escape(str(ipv6))}</code>\n"
        text += (
            f"• <b>耗时</b>: {elapsed:.1f}s\n"
            f"• <b>时间</b>: {now_str}"
        )
    else:
        err_safe = html.escape(str(error_msg or '未知错误'))
        text = (
            "🔴 <b>【OCIX】实例创建失败</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"• <b>账户</b>: <code>{p_safe}</code>{region_info}\n"
            f"• <b>实例名</b>: <code>{n_safe}</code>\n"
            f"• <b>规格</b>: {s_safe}\n"
            f"• <b>原因</b>: <i>{err_safe}</i>\n"
            f"• <b>时间</b>: {now_str}"
        )

    send_telegram_async(text)


def notify_instance_terminated(
    profile: str,
    instance_id: str,
    display_name: str | None = None,
    preserve_boot_volume: bool = False,
    user: str = "",
    ip: str = "",
) -> None:
    """通知实例终止/删机。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p_safe = html.escape(str(profile))
    id_safe = html.escape(str(instance_id))
    name_str = f"<code>{html.escape(str(display_name))}</code>\n• <b>OCID</b>: " if display_name else ""
    boot_action = "保留引导卷" if preserve_boot_volume else "已连同引导卷一并删除"
    actor = f"{user} ({ip})" if ip else user
    actor_safe = html.escape(str(actor or "admin"))

    text = (
        "🗑️ <b>【OCIX】实例已终止 / 删除</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"• <b>账户</b>: <code>{p_safe}</code>\n"
        f"• <b>实例</b>: {name_str}<code>{id_safe}</code>\n"
        f"• <b>存储处理</b>: {boot_action}\n"
        f"• <b>操作人</b>: <code>{actor_safe}</code>\n"
        f"• <b>时间</b>: {now_str}"
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
        "START": ("🚀 实例开机", "🟢 已发送开机指令"),
        "STOP": ("🛑 实例停止 (强行关机)", "🔴 已发送停止指令"),
        "SOFTSTOP": ("⏸️ 实例关机", "🟡 已发送关机指令"),
        "RESET": ("🔄 实例重启", "🟡 已发送重启指令"),
        "SOFTRESET": ("🔄 实例软重启", "🟡 已发送软重启指令"),
    }
    title, desc = action_map.get(action.upper(), (f"⚡ 实例操作 ({action})", action))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p_safe = html.escape(str(profile))
    id_safe = html.escape(str(instance_id))
    name_str = f"<code>{html.escape(str(display_name))}</code>\n• <b>OCID</b>: " if display_name else ""
    actor = f"{user} ({ip})" if ip else user
    actor_safe = html.escape(str(actor or "admin"))

    if success:
        text = (
            f"<b>【OCIX】{title}</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"• <b>账户</b>: <code>{p_safe}</code>\n"
            f"• <b>实例</b>: {name_str}<code>{id_safe}</code>\n"
            f"• <b>状态</b>: {desc}\n"
            f"• <b>操作人</b>: <code>{actor_safe}</code>\n"
            f"• <b>时间</b>: {now_str}"
        )
    else:
        err_safe = html.escape(str(error_msg or "操作失败"))
        text = (
            f"❌ <b>【OCIX】{title} 失败</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"• <b>账户</b>: <code>{p_safe}</code>\n"
            f"• <b>实例</b>: {name_str}<code>{id_safe}</code>\n"
            f"• <b>原因</b>: <i>{err_safe}</i>\n"
            f"• <b>操作人</b>: <code>{actor_safe}</code>\n"
            f"• <b>时间</b>: {now_str}"
        )

    send_telegram_async(text)
