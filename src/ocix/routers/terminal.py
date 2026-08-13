"""网页终端的 HTTP + WebSocket 入口。"""

from __future__ import annotations

import asyncio
import json
import threading

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect

from .. import security, terminal
from ..common import OCIError, account_gate
from ..db import audit
from ..oci_helpers import instance_detail, pick_console

router = APIRouter(prefix="/api/terminal", tags=["terminal"])


@router.post("/ticket")
def issue_ticket(request: Request, user: str = Depends(security.get_current_user)):
    """换一张一次性票据给 WebSocket 用。

    浏览器的 WebSocket API 加不了 Authorization 头，而把 JWT 塞进 URL
    会漏进反代日志和浏览器历史。票据 30 秒过期且用一次就作废。
    """
    security.check_rate(request, security.API_RATE_LIMIT)
    return {"ticket": terminal.issue_ticket(user), "expires_in": terminal.TICKET_TTL}


@router.get("/available")
def available(user: str = Depends(security.get_current_user)):
    """服务端有没有装 paramiko——没装就别在界面上给按钮。"""
    return {"available": terminal.paramiko is not None, "error": terminal.PARAMIKO_ERROR}


async def _send(ws: WebSocket, payload: dict):
    try:
        await ws.send_text(json.dumps(payload, ensure_ascii=False))
    except Exception:  # noqa: BLE001 - 对端已经走了
        pass


@router.websocket("/ws")
async def terminal_ws(ws: WebSocket):
    """一条 WebSocket 对应一条 SSH 会话。

    协议（都是 JSON 文本帧）：
      客户端 → 服务端： {type:"open", ...连接参数与私钥} / {type:"data"} / {type:"resize"}
      服务端 → 客户端： {type:"ready"} / {type:"data"} / {type:"error"} / {type:"closed"}

    私钥只在 open 这条消息里出现一次，用完就留在内存里，不落盘也不记日志。
    """
    user = terminal.consume_ticket(ws.query_params.get("ticket", ""))
    if not user:
        # 4401：自定义的「未认证」关闭码，浏览器能拿到
        await ws.close(code=4401)
        return
    await ws.accept()

    loop = asyncio.get_running_loop()
    session: terminal.Session | None = None
    reader: threading.Thread | None = None
    stop = threading.Event()

    def pump(sess: terminal.Session):
        """后台线程把 SSH 的输出推给浏览器。

        paramiko 的 channel 是阻塞式的，塞不进 asyncio，只能单起一个线程，
        再用 run_coroutine_threadsafe 把数据交回事件循环。
        """
        try:
            while not stop.is_set():
                if sess.channel.recv_ready():
                    data = sess.channel.recv(32768)
                    if not data:
                        break
                    asyncio.run_coroutine_threadsafe(
                        _send(ws, {"type": "data",
                                   "data": data.decode("utf-8", errors="replace")}), loop)
                elif sess.channel.exit_status_ready() and not sess.channel.recv_ready():
                    break
                else:
                    stop.wait(0.02)
        except Exception:  # noqa: BLE001 - 断开就是断开，不用喊
            pass
        finally:
            asyncio.run_coroutine_threadsafe(
                _send(ws, {"type": "closed"}), loop)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")

            if kind == "open":
                if session is not None:
                    continue
                cols = int(msg.get("cols") or 80)
                rows = int(msg.get("rows") or 24)
                target = msg.get("target") or "direct"
                profile = msg.get("profile") or ""
                try:
                    pkey = terminal.load_private_key(
                        msg.get("private_key", ""), msg.get("passphrase", ""))
                    if target == "console":
                        with account_gate(profile):
                            conn = pick_console(profile, msg.get("instance_id"),
                                                msg.get("compartment_id"))
                        session = await asyncio.to_thread(
                            terminal.open_console, conn["ssh_command"], pkey, cols, rows)
                    else:
                        host = msg.get("host") or ""
                        if not host:
                            with account_gate(profile):
                                d = instance_detail(profile, msg.get("instance_id"),
                                                    msg.get("compartment_id"))
                            host = d.get("public_ip") or ""
                        session = await asyncio.to_thread(
                            terminal.open_direct, host, int(msg.get("port") or 22),
                            msg.get("username") or "ubuntu", pkey, cols, rows)
                except OCIError as e:
                    await _send(ws, {"type": "error", "message": e.message})
                    continue
                except Exception as e:  # noqa: BLE001
                    await _send(ws, {"type": "error", "message": str(e)})
                    continue

                audit(user, "terminal", profile=profile,
                      target=msg.get("instance_id") or msg.get("host"),
                      detail=f"打开网页终端（{'串口控制台' if target == 'console' else '直连 SSH'}）",
                      result="ok", ip=security.client_ip(ws))
                reader = threading.Thread(target=pump, args=(session,), daemon=True)
                reader.start()
                await _send(ws, {"type": "ready"})

            elif kind == "data" and session is not None:
                session.channel.send(msg.get("data", ""))

            elif kind == "resize" and session is not None:
                session.resize(msg.get("cols", 80), msg.get("rows", 24))

    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        pass
    finally:
        stop.set()
        if session is not None:
            session.close()
        if reader is not None:
            reader.join(timeout=1)
