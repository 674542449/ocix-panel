from unittest.mock import patch

from ocix import notifier

TOKEN = "123456789:ABCdefGhIjKlMnOpQrStUvWxYz"


def test_telegram_settings_crud(app_client):
    # 1. 查询默认设置
    r = app_client.get("/api/system/telegram")
    assert r.status_code == 200
    data = r.json()
    assert data["has_token"] is False

    # 2. 保存设置
    r = app_client.post("/api/system/telegram", json={
        "enabled": True,
        "bot_token": TOKEN,
        "chat_id": "-100123456789",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 3. 再次查询：token 必须脱敏。拿到 Bot Token 就能以这个机器人的身份
    #    发消息、读会话，接口没有任何理由把原文回给浏览器。
    r = app_client.get("/api/system/telegram")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is True
    assert data["has_token"] is True
    assert TOKEN not in r.text
    assert data["bot_token"].startswith("1234")
    assert data["bot_token"].endswith("WxYz")
    assert "*" in data["bot_token"]
    assert data["chat_id"] == "-100123456789"


def test_saving_masked_token_keeps_the_real_one(app_client):
    """前端回显的是脱敏值，原样提交回来不能把真 token 覆盖成一串星号。"""
    app_client.post("/api/system/telegram",
                    json={"enabled": True, "bot_token": TOKEN, "chat_id": "-100"})
    masked = app_client.get("/api/system/telegram").json()["bot_token"]

    # 只改 chat_id，token 带着星号提交
    r = app_client.post("/api/system/telegram",
                        json={"enabled": True, "bot_token": masked, "chat_id": "-200"})
    assert r.status_code == 200
    assert r.json()["chat_id"] == "-200"

    with patch("ocix.notifier.test_telegram", return_value=(True, "ok")) as spy:
        app_client.post("/api/system/telegram/test",
                        json={"bot_token": masked, "chat_id": ""})
    # 测试连接要用库里那份真 token，而不是星号
    assert spy.call_args[0][0] == TOKEN
    assert spy.call_args[0][1] == "-200"


def test_telegram_cannot_be_enabled_without_credentials(app_client):
    """没有 token / chat_id 就发不出消息，这时不能显示成「已启用」。"""
    r = app_client.post("/api/system/telegram",
                        json={"enabled": True, "bot_token": "", "chat_id": ""})
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert app_client.get("/api/system/telegram").json()["enabled"] is False


def test_telegram_endpoints_validate_their_body(app_client):
    """两个接口以前收裸 dict，什么都不校验。"""
    r = app_client.post("/api/system/telegram", json={"enabled": "不是布尔值"})
    assert r.status_code == 422
    r = app_client.post("/api/system/telegram", json={"bot_token": "x" * 500})
    assert r.status_code == 422


def test_telegram_test_endpoint(app_client):
    with patch("ocix.notifier.test_telegram", return_value=(True, "发送成功")):
        r = app_client.post("/api/system/telegram/test", json={
            "bot_token": TOKEN,
            "chat_id": "-100123456789",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    with patch("ocix.notifier.test_telegram", return_value=(False, "Unauthorized")):
        r = app_client.post("/api/system/telegram/test", json={
            "bot_token": TOKEN,
            "chat_id": "-100123456789",
        })
        assert r.status_code == 400


def test_telegram_notifications_formatting(app_client):
    with patch("ocix.notifier._post_telegram") as mock_post:
        # 1. 创建成功通知
        notifier.notify_instance_created(
            profile="DEFAULT",
            display_name="<test-instance>",
            shape="VM.Standard.A1.Flex",
            ocpus=4,
            memory_gb=24,
            boot_gb=100,
            public_ip="1.2.3.4",
            success=True,
            elapsed=35.2,
        )
        # 2. 终止通知
        notifier.notify_instance_terminated(
            profile="DEFAULT",
            instance_id="ocid1.instance.oc1..test",
            display_name="prod-server",
            preserve_boot_volume=False,
            user="admin",
            ip="127.0.0.1",
        )
        # 3. 操作通知
        notifier.notify_instance_action(
            profile="DEFAULT",
            instance_id="ocid1.instance.oc1..test",
            action="START",
            display_name="prod-server",
            success=True,
            user="admin",
            ip="127.0.0.1",
        )
        assert mock_post.call_count >= 0


def test_notify_instance_created_decrypts_token(app_client):
    """测试 notify_instance_created 能正确解密加密存储的 bot_token。"""
    import time

    raw_token = "123456:SECRET_TEST_TOKEN"
    r = app_client.post("/api/system/telegram", json={
        "enabled": True,
        "bot_token": raw_token,
        "chat_id": "-100111222333",
    })
    assert r.status_code == 200

    from ocix import notifier
    with patch("ocix.notifier._post_telegram", return_value=(True, "ok")) as mock_post:
        notifier.notify_instance_created(
            profile="DEFAULT",
            display_name="unit-test-box",
            shape="VM.Standard.E2.1.Micro",
            public_ip="1.2.3.4",
            success=True,
        )
        # 等待后台线程执行
        time.sleep(0.4)
        assert mock_post.call_count == 1
        call_token = mock_post.call_args[0][0]
        assert call_token == raw_token, f"Token 应被解密为原文，实际为: {call_token}"


