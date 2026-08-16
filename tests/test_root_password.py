"""用 root + 密码建实例。

这条路是**拿安全换方便**，代价写在 src/ocix/cloudinit.py 开头。
这里盯住的是：别让它悄悄退化成更糟的样子——
密码不够长、机器建出来登不进去、密码没写进标签所以换台电脑就找不到了。
"""

import time

import pytest
import yaml

from ocix.cloudinit import ROOT_PW_TAG, generate_password, root_password_cloud_config

PUB_KEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyForTests test@example"


# ── cloud-init ──

def test_generated_password_is_long_and_unambiguous():
    """22 端口一暴露就会被全网扫，短密码撑不住；
    而这密码是要用眼睛从界面上抄下来的，别放容易看混的字符。"""
    pw = generate_password()
    assert len(pw) >= 20
    assert not (set(pw) & set("0O1lI")), f"混进了容易看错的字符：{pw}"
    assert len({generate_password() for _ in range(50)}) == 50, "不能重复"


@pytest.mark.parametrize("pw", [
    "Abcdef123456!",
    "a'b\"c#d$e 12345",          # 引号、井号、空格
    "pa ss#w0rd!!x",
    "结尾有中文也不该炸xxxxx",
])
def test_cloud_config_is_valid_yaml_and_keeps_the_password_intact(pw):
    """密码里带引号 / # / 空格都不能把 YAML 结构带歪，也不能被截断。

    用的是字面块（|），所以里面的字符不参与 YAML 解析——
    这一条是防止以后有人「顺手」改成行内字符串。
    """
    doc = yaml.safe_load(root_password_cloud_config(pw))
    assert doc["ssh_pwauth"] is True
    assert doc["disable_root"] is False
    got = doc["chpasswd"]["list"].strip()
    assert got == f"root:{pw}", f"密码被改动了：{got!r}"


def test_cloud_config_defeats_the_ubuntu_drop_in():
    """Ubuntu 云镜像用 60-cloudimg-settings.conf 把密码登录关掉。

    只改 sshd_config 主文件没用——那个 drop-in 后加载，会把设置覆盖回去。
    所以必须既写一个 99- 开头的（字典序最后），又把 60- 那个删掉。
    """
    cfg = root_password_cloud_config("Abcdef123456!")
    assert "99-ocix.conf" in cfg
    assert "rm -f /etc/ssh/sshd_config.d/60-cloudimg-settings.conf" in cfg
    assert "PermitRootLogin yes" in cfg
    assert "PasswordAuthentication yes" in cfg


def test_cloud_config_wipes_the_on_disk_copy():
    """落盘的那份 user-data 里有明文密码，开机末尾抹掉。

    （元数据服务里的那份删不掉，这一点写在 cloudinit.py 开头了。）
    """
    cfg = root_password_cloud_config("Abcdef123456!")
    assert "/var/lib/cloud/instance/user-data.txt" in cfg


def test_empty_password_is_refused():
    with pytest.raises(ValueError):
        root_password_cloud_config("")


# ── 接口 ──

def _spec(**kw):
    body = {
        "profile": "EXISTING", "compartment_id": "cid", "display_name": "pw-box",
        "availability_domain": "AD-1", "image_id": "img-ubuntu",
        "shape": "VM.Standard.E2.1.Micro", "ocpus": 1, "memory_gb": 1, "boot_gb": 50,
        "assign_ipv6": False, "open_all_ports": False,
    }
    body.update(kw)
    return body


def _drain(client, job_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = client.get(f"/api/provision/jobs/{job_id}").json()
        if snap["state"] != "running":
            return snap
        time.sleep(0.05)
    raise AssertionError("任务没跑完")


def test_instance_with_no_key_and_no_password_is_refused(app_client):
    """两样都不给的机器建出来是能跑，但你永远登不进去。

    只能靠串口控制台救——而串口控制台也要求系统里先有密码。
    """
    r = app_client.post("/api/provision/instances", json=_spec())
    assert r.status_code == 422, r.text
    assert "登录方式" in r.text


@pytest.mark.parametrize("pw", ["short", "1234567890x"])
def test_short_root_password_is_refused(app_client, pw):
    r = app_client.post("/api/provision/instances", json=_spec(root_password=pw))
    assert r.status_code == 422, r.text
    assert "12 位" in r.text


def test_password_only_instance_can_be_created(app_client, live_backend):
    """不给公钥、只给 root 密码也能建——这是这个功能的重点。"""
    r = app_client.post("/api/provision/instances",
                        json=_spec(root_password="Abcdef123456!"))
    assert r.status_code == 202, r.text
    snap = _drain(app_client, r.json()["job_id"])
    assert snap["state"] == "ok", snap
    assert snap["result"]["root_password"] == "Abcdef123456!"


def test_password_is_written_into_the_instance_tag(app_client, live_backend):
    """密码要落在实例的自由标签上——换台电脑、换个浏览器才看得到。"""
    r = app_client.post("/api/provision/instances",
                        json=_spec(display_name="tagged", root_password="Abcdef123456!"))
    _drain(app_client, r.json()["job_id"])
    spec = live_backend.launched[-1]
    assert spec["freeform_tags"][ROOT_PW_TAG] == "Abcdef123456!"
    # 同时要下发 cloud-init 真的把密码登录打开
    assert "ssh_pwauth" in (spec.get("user_data") or "")


def test_key_only_instance_carries_no_tag_and_no_user_data(app_client, live_backend):
    """没选密码就什么都别多做——不打标签、不下发 cloud-init。"""
    r = app_client.post("/api/provision/instances",
                        json=_spec(display_name="keyonly", ssh_public_key=PUB_KEY))
    _drain(app_client, r.json()["job_id"])
    spec = live_backend.launched[-1]
    assert not (spec.get("freeform_tags") or {})
    assert not spec.get("user_data")
    assert spec["ssh_public_key"] == PUB_KEY


def test_instance_list_surfaces_the_password(app_client, live_backend):
    """列表要把标签里的密码带出来，否则界面上看不到。"""
    live_backend.instances = [{
        "id": "i-pw", "display-name": "pw-box", "lifecycle-state": "RUNNING",
        "shape": "VM.Standard.E2.1.Micro", "compartment-id": "cid",
        "availability-domain": "AD-1", "time-created": "2026-08-01T00:00:00+00:00",
        "freeform-tags": {ROOT_PW_TAG: "Abcdef123456!"},
    }]
    r = app_client.get("/api/instances", params={"profile": "EXISTING"})
    assert r.status_code == 200, r.text
    assert r.json()["instances"][0]["root_password"] == "Abcdef123456!"
