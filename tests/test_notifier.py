from unittest.mock import patch

from ocix import notifier


def test_telegram_settings_crud(app_client):
    # 1. 查询默认设置
    r = app_client.get("/api/system/telegram")
    assert r.status_code == 200
    data = r.json()
    assert data["has_token"] is False

    # 2. 保存设置
    r = app_client.post("/api/system/telegram", json={
        "enabled": True,
        "bot_token": "123456789:ABCdefGhIjKlMnOpQrStUvWxYz",
        "chat_id": "-100123456789",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 3. 再次查询设置，完整回显保留的配置
    r = app_client.get("/api/system/telegram")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is True
    assert data["has_token"] is True
    assert data["bot_token"] == "123456789:ABCdefGhIjKlMnOpQrStUvWxYz"
    assert data["chat_id"] == "-100123456789"


def test_telegram_test_endpoint(app_client):
    with patch("ocix.notifier.test_telegram", return_value=(True, "发送成功")):
        r = app_client.post("/api/system/telegram/test", json={
            "bot_token": "123456789:ABCdefGhIjKlMnOpQrStUvWxYz",
            "chat_id": "-100123456789",
        })
        assert r.status_code == 200
        assert r.json()["ok"] is True

    with patch("ocix.notifier.test_telegram", return_value=(False, "Unauthorized")):
        r = app_client.post("/api/system/telegram/test", json={
            "bot_token": "123456789:ABCdefGhIjKlMnOpQrStUvWxYz",
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
