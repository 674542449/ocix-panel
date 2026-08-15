"""登录页粒子场景的数学验证。

这部分没法在浏览器窗格里看——那是个 hidden 文档，requestAnimationFrame
一次都不触发（实测 900ms 内 0 次），动画根本不推进。所以改成把场景模块
抠出来，在 Node 里配一张会记账的假画布跑十几秒，检查：

  * 坐标有没有 NaN（canvas 遇到 NaN 是静默不画，最容易漏）
  * 数据包会不会跑完、会不会换下一对区域
  * 回调给出的是不是真实区域名
  * 关掉动效时是不是只画一帧、不起循环

需要 node。GitHub 的 runner 自带，本地没有就跳过。
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent / "scene_check.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 node 才能跑场景检查")
def test_login_scene_math():
    proc = subprocess.run(
        [shutil.which("node"), str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert proc.returncode == 0, (
        "登录页场景检查未通过：\n" + proc.stdout + proc.stderr
    )
