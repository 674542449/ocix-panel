from unittest.mock import patch


def test_telegram_settings_crud(app_client):
    # 1. 查询默认设置
    r = app_client.get("/api/system/telegram")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is False
    assert data["has_token"] is False

    # 2. 保存设置
    r = app_client.post("/api/system/telegram", json={
        "enabled": True,
        "bot_token": "123456789:ABCdefGhIjKlMnOpQrStUvWxYz",
        "chat_id": "-100123456789",
    })
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 3. 再次查询设置，token 被脱敏
    r = app_client.get("/api/system/telegram")
    assert r.status_code == 200
    data = r.json()
    assert data["enabled"] is True
    assert data["has_token"] is True
    assert "*" in data["bot_token"]
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
