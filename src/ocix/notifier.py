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
        with urllib.request.urlopen(req, timeout=10) as resp:
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
    enabled = db.get_setting("tg_enabled", "0") in ("1", "true", "True")
    token = bot_token or db.get_setting("tg_bot_token", "")
    cid = chat_id or db.get_setting("tg_chat_id", "")

    if not enabled and not bot_token:
        return
    if not token or not cid:
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
        "• 说明: 实例创建与删除通知已就绪。"
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
    """仅通知实例创建成功或失败。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    region_info = f" ({region})" if region else ""

    if success:
        spec_parts = [shape]
        if ocpus or memory_gb:
            spec_parts.append(f"{ocpus or 1}C / {memory_gb or 1}G")
        if boot_gb:
            spec_parts.append(f"引导卷 {boot_gb}G")
        spec_str = " · ".join(spec_parts)

        text = (
            "🟢 <b>【OCIX】实例创建成功</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"• <b>账户</b>: <code>{profile}</code>{region_info}\n"
            f"• <b>实例名</b>: <code>{display_name}</code>\n"
            f"• <b>配置</b>: {spec_str}\n"
            f"• <b>公网 IPv4</b>: <code>{public_ip or '分配中/未获取'}</code>\n"
        )
        if ipv6:
            text += f"• <b>IPv6</b>: <code>{ipv6}</code>\n"
        text += (
            f"• <b>耗时</b>: {elapsed:.1f}s\n"
            f"• <b>时间</b>: {now_str}"
        )
    else:
        text = (
            "🔴 <b>【OCIX】实例创建失败</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"• <b>账户</b>: <code>{profile}</code>{region_info}\n"
            f"• <b>实例名</b>: <code>{display_name}</code>\n"
            f"• <b>规格</b>: {shape}\n"
            f"• <b>原因</b>: <i>{error_msg or '未知错误'}</i>\n"
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
    """仅通知实例终止/删除。"""
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    name_str = f"<code>{display_name}</code>\n• <b>OCID</b>: " if display_name else ""
    boot_action = "保留引导卷" if preserve_boot_volume else "已连同引导卷一并删除"
    actor_str = f"{user} ({ip})" if ip else user

    text = (
        "🗑️ <b>【OCIX】实例已终止 / 删除</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"• <b>账户</b>: <code>{profile}</code>\n"
        f"• <b>实例</b>: {name_str}<code>{instance_id}</code>\n"
        f"• <b>存储处理</b>: {boot_action}\n"
        f"• <b>操作人</b>: <code>{actor_str or 'admin'}</code>\n"
        f"• <b>时间</b>: {now_str}"
    )

    send_telegram_async(text)
