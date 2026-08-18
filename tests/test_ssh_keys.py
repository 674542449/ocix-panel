def test_ssh_keys_crud(app_client):
    # 1. 初始列表为空
    r = app_client.get("/api/ssh-keys")
    assert r.status_code == 200
    assert r.json()["keys"] == []

    # 2. 创建公钥
    valid_key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExamplePublicKeyForTestingOnly user@host"
    r = app_client.post("/api/ssh-keys", json={
        "name": "My MacBook",
        "public_key": valid_key,
    })
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["key"]["name"] == "My MacBook"
    assert data["key"]["key_type"] == "ssh-ed25519"
    assert data["key"]["fingerprint"].startswith("SHA256:")
    key_id = data["key"]["id"]

    # 3. 查列表包含该公钥
    r = app_client.get("/api/ssh-keys")
    assert r.status_code == 200
    keys = r.json()["keys"]
    assert len(keys) == 1
    assert keys[0]["id"] == key_id
    assert keys[0]["name"] == "My MacBook"

    # 4. 创建同名公钥拒绝
    r = app_client.post("/api/ssh-keys", json={
        "name": "My MacBook",
        "public_key": valid_key,
    })
    assert r.status_code == 400

    # 5. 更新公钥
    r = app_client.put(f"/api/ssh-keys/{key_id}", json={
        "name": "My MacBook Pro",
        "public_key": valid_key,
    })
    assert r.status_code == 200
    assert r.json()["key"]["name"] == "My MacBook Pro"

    # 6. 删除公钥
    r = app_client.delete(f"/api/ssh-keys/{key_id}")
    assert r.status_code == 200
    assert r.json()["ok"] is True

    # 7. 再次查看为空
    r = app_client.get("/api/ssh-keys")
    assert r.json()["keys"] == []


def test_ssh_key_validation(app_client):
    # 格式错误
    r = app_client.post("/api/ssh-keys", json={
        "name": "Bad Key",
        "public_key": "not-a-valid-ssh-key",
    })
    assert r.status_code == 422 or r.status_code == 400

    # 私钥拒绝
    r = app_client.post("/api/ssh-keys", json={
        "name": "Private Key",
        "public_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
    })
    assert r.status_code == 422 or r.status_code == 400
