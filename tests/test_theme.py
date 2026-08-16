"""面板配色的几条红线。

配色换过好几轮（艳丽 → 纯灰阶 → 低饱和石墨青 → 石墨靛 → 现在的「青春」），
但下面这几条不管换成什么都得成立：

  * 饱和度有上限——上限本身可以随方向调整，但必须是明写的，不能没有
  * 状态不能只靠颜色区分——形态也得带着信息
  * 变量块的特异性要压得过 element-plus
  * 链接要有下划线

前两条尤其容易在「顺手调个颜色」时被悄悄破坏：页面照样能看，
只是色弱的人、亮度调很低的屏幕、关掉动效的场景开始分不出状态。
"""

import colorsys
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "ocix" / "web"
INDEX = WEB / "index.html"
FAVICON = WEB / "favicon.svg"

# 饱和度上限。**这条线抬过一次，说明原因。**
#
# 早先用户否掉了「艳丽」的配色，于是这里卡在 HSL 饱和度 60%。
# 后来用户要「青春的配色」——方向反过来了，就该明说、把线抬上去，
# 而不是留着一条过不去的规则再想办法绕。
#
# 顺带把度量换了：HSL 饱和度在高明度下会虚高（#6f8dff 是 HSL 100%，
# 但它是个亮蓝，并不刺眼），HSV 饱和度才贴近「看着有多艳」。实测：
#     现在这套（青春）  HSV 51~72%
#     被否掉的那套      HSV 72~96%
#     纯霓虹绿 #00ff00  HSV 100%
# 也就是说新配色其实**比被否掉那版更不艳**，只是更亮、色相更鲜。
# 80% 这条线放得过现在这套，仍拦得住霓虹和 #f59e0b 那种（96%）。
MAX_SATURATION = 80
# 亮度太低的颜色算「暗色底」，不受约束——深色背景本来就该有点色调
MIN_LIGHTNESS_FOR_CHECK = 25

_HEX = re.compile(r"#([0-9a-fA-F]{6})\b")


def _strip_comments(text: str) -> str:
    """注释里会提到历史配色（说明为什么换掉），不该被判成违规。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _hls(hex_color: str) -> tuple[float, float, float]:
    """返回 (色相°, 明度%, **HSV 饱和度%**)。

    第三个值刻意用 HSV 而不是 HSL 的饱和度：HSL 的那个在高明度下会虚高，
    把「亮但不刺眼」的颜色误判成艳色。
    """
    h = hex_color.lstrip("#")
    ch = [int(h[i : i + 2], 16) for i in (0, 2, 4)]
    r, g, b = (v / 255 for v in ch)
    hue, light, _ = colorsys.rgb_to_hls(r, g, b)
    mx = max(ch)
    sat_v = 0.0 if mx == 0 else (mx - min(ch)) / mx * 100
    return hue * 360, light * 100, sat_v


def _too_vivid(text: str) -> list[str]:
    out = []
    for m in _HEX.finditer(_strip_comments(text)):
        _, light, sat = _hls(m.group(0))
        if light >= MIN_LIGHTNESS_FOR_CHECK and sat > MAX_SATURATION:
            out.append(f"{m.group(0)}（饱和度 {sat:.0f}%）")
    return sorted(set(out))


def test_no_vivid_colors_in_panel():
    bad = _too_vivid(INDEX.read_text(encoding="utf-8"))
    assert not bad, (
        f"这些颜色太艳了：{bad}。上限是 HSV 饱和度 {MAX_SATURATION}%——"
        "要抬这条线就在常量那儿改并写清理由，别在颜色上凑。"
    )


def test_no_vivid_colors_in_favicon():
    bad = _too_vivid(FAVICON.read_text(encoding="utf-8"))
    assert not bad, f"favicon 里的颜色太艳：{bad}"


def test_status_hues_are_far_enough_apart():
    """状态色的色相要拉开，否则两个状态会糊成一片。

    踩过两次：warn 和 crit 曾经只差 26°；主色曾经是青 189°，离「运行中」
    的绿只有 46°，既不像可操作又跟状态抢意思。
    """
    css = INDEX.read_text(encoding="utf-8")
    tokens = {}
    for name in ("accent", "ok", "warn", "crit"):
        m = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", css)
        assert m, f"找不到 --{name} 的定义"
        tokens[name] = m.group(1)

    hues = sorted((_hls(v)[0], k) for k, v in tokens.items())
    for i, (hue, name) in enumerate(hues):
        nxt_hue, nxt_name = hues[(i + 1) % len(hues)]
        gap = (nxt_hue - hue) % 360
        assert gap >= 40, (
            f"{name} ({hue:.0f}°) 和 {nxt_name} ({nxt_hue:.0f}°) 只差 {gap:.0f}°，"
            "分不出来"
        )


def test_secondary_accent_is_not_a_status_colour():
    """副色只跟主色搭着用，不能挤进状态语义。

    它跟主色只差 36°（本来就是要搭在一起做渐变的），所以刻意不放进上面
    那条「相邻至少 40°」的检查里；但必须离四档状态色都够远，
    免得有人看见紫色以为是某种状态。
    """
    css = INDEX.read_text(encoding="utf-8")
    m = re.search(r"--accent-2:\s*(#[0-9a-fA-F]{6})", css)
    assert m, "找不到 --accent-2"
    a2 = _hls(m.group(1))[0]
    for name in ("ok", "warn", "crit"):
        v = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", css)
        gap = min((a2 - _hls(v.group(1))[0]) % 360, (_hls(v.group(1))[0] - a2) % 360)
        assert gap >= 40, f"副色离 {name} 只有 {gap:.0f}°，会被当成状态色"


def test_element_plus_overrides_outrank_the_library():
    """本站变量必须写成 :root.dark，不能退回 html.dark。

    CDN 回退路径是**运行时**把 element-plus 的样式表追加到 head 末尾的，
    排在面板这段 <style> 后面。同特异性下后来者赢，于是面板的配色会被
    组件库的默认蓝整片盖掉——页面照常渲染，只是按钮全变回 #409eff。
    :root.dark 是 (0,2,0)，压过 element-plus 的 html.dark (0,1,1)。
    """
    css = INDEX.read_text(encoding="utf-8")
    assert ":root.dark {" in css, "变量块必须挂在 :root.dark 上"
    assert not re.search(r"^\s*html\.dark\s*\{", css, flags=re.M), (
        "html.dark 的特异性压不过 element-plus 自己的 html.dark，"
        "CDN 回退时配色会被组件库盖掉"
    )


def test_status_dots_carry_shape_as_well_as_color():
    """状态点不能只靠颜色区分，形态也得带着同一条信息。

    这是无障碍的硬规则（不要只用颜色传达信息），也是实用问题：
    关掉动效之后 .dot.warn 只剩一个静止的点，如果它和 .dot.attn
    同色同形就彻底分不出来了——所以 attn 是方的。
    """
    css = INDEX.read_text(encoding="utf-8")
    block = css[css.index(".dot {") : css.index(".state-label")]
    assert re.search(r"\.dot\.ok\s*\{[^}]*background:var\(--ok\)", block)
    # 空心 = 已停止，不依赖颜色
    assert re.search(r"\.dot\.crit\s*\{[^}]*background:transparent", block)
    # 脉动 = 过渡中
    assert re.search(r"\.dot\.warn\s*\{[^}]*animation:pulse", block)
    # 方形 = 另一类提醒，不跟运行状态混
    assert re.search(r"\.dot\.attn\s*\{[^}]*border-radius:2px", block)

    # 关掉动效时 warn 仍要和 attn 分得开
    reduced = css[css.index("prefers-reduced-motion") :]
    warn_fallback = re.search(r"\.dot\.warn\s*\{([^}]*)\}", reduced)
    assert warn_fallback, "prefers-reduced-motion 下必须给 .dot.warn 一个静态替代"
    assert warn_fallback.group(1).count("0 0 0") >= 3, (
        "静态替代得是多重环，单环会和 .dot.attn 撞脸"
    )


def test_breathing_and_pulsing_are_different_motions():
    """「运行中」呼吸、「过渡中」脉冲，两种动法必须真的不一样。

    这是整个状态体系的一条线：如果两者动得像，「正在开机」和「已经在跑」
    就分不出来了——而这恰恰是最需要分清的一对。
    区别在三处：周期、几何、缓动。
    """
    css = INDEX.read_text(encoding="utf-8")

    def block(name: str) -> str:
        """取出一整组关键帧。内层每一帧也有大括号，所以要数配对，
        非贪婪匹配到第一个 } 只会拿到第一帧。"""
        i = css.index(f"@keyframes {name} {{")
        depth, j = 0, css.index("{", i)
        for k in range(j, len(css)):
            if css[k] == "{":
                depth += 1
            elif css[k] == "}":
                depth -= 1
                if depth == 0:
                    return css[j + 1 : k]
        raise AssertionError(f"@keyframes {name} 的大括号没闭合")

    breathe, pulse = block("breathe"), block("pulse")

    # 呼吸首尾同值 -> 无缝循环；脉冲末帧透明 -> 扩散后消失
    assert "0%, 100%" in breathe, "呼吸的首尾要同值，否则每圈会跳一下"
    assert re.search(r"100%[^}]*,\s*0\)", pulse), "脉冲末帧应当淡出到透明"
    # 幅度得够大才看得见。之前只让光晕在 .10~.22 之间涨落，用户的原话是
    # 「几乎看不到在呼吸」——所以这里卡的是变化量，不是「有没有动画」。
    scales = [float(x) for x in re.findall(r"scale\(([\d.]+)\)", breathe)]
    assert len(scales) >= 2, "呼吸要带灯芯缩放，只改光晕看不出来"
    assert max(scales) / min(scales) >= 1.35, (
        f"灯芯缩放比只有 {max(scales) / min(scales):.2f}，太小了看不见"
    )
    # 扩散值写成裸 0 还是 0px 都算数：`0 0 4px 0 rgba(...)` 和
    # `0 0 16px 3px rgba(...)` 两种写法都要认出来
    blurs = [int(x) for x in re.findall(r"0 0 (\d+)px \d+(?:px)? rgba", breathe)]
    assert len(blurs) >= 2 and max(blurs) >= min(blurs) * 3, (
        f"光晕模糊半径 {blurs}，涨落幅度不够"
    )
    assert breathe.count("background:") >= 2, "灯芯亮度也要跟着变"

    ok = re.search(r"\.dot\.ok \{[^}]*animation:(\w+) ([\d.]+)s ([\w-]+)", css)
    warn = re.search(r"\.dot\.warn \{[^}]*animation:(\w+) ([\d.]+)s ([\w-]+)", css)
    assert ok and warn, "两个状态点都要声明动画"
    assert ok.group(1) == "breathe" and warn.group(1) == "pulse"
    # 周期至少差一倍，节奏才明显不同
    assert float(ok.group(2)) >= float(warn.group(2)) * 1.8, (
        f"呼吸 {ok.group(2)}s 和脉冲 {warn.group(2)}s 太接近，节奏区分不出来"
    )
    assert ok.group(3) != warn.group(3), "缓动也应当不同（平稳 vs 冲出去）"


def test_breathing_has_a_static_fallback():
    """关掉动效时呼吸灯要退化成静态光晕，不能变成一颗死灯。"""
    css = INDEX.read_text(encoding="utf-8")
    reduced = css[css.index("prefers-reduced-motion") :]
    assert re.search(r"\.dot\.ok, \.led \{[^}]*box-shadow", reduced), (
        "prefers-reduced-motion 下要给 .dot.ok / .led 一个静态光晕"
    )


def test_login_leds_are_out_of_phase():
    """登录页状态条上三颗灯要错开相位。

    同步呼吸像圣诞灯，真机架上的指示灯不会同步。
    """
    css = INDEX.read_text(encoding="utf-8")
    assert ".chip:nth-child(2) .led" in css and ".chip-arm .led" in css, (
        "三颗机位灯各自要有相位"
    )
    delays = re.findall(r"\.led \{ animation-delay:(-[\d.]+)s", css)
    assert len(set(delays)) == len(delays) and len(delays) >= 2, (
        f"各颗灯的相位要互不相同，现在是 {delays}"
    )


def test_reduced_motion_zeroes_animation_delay():
    """关掉动效时延迟也要清零，不只是时长。

    只压 duration 的话，带 animation-delay + fill:both 的元素会在延迟那段时间里
    停在 from 那一帧——入场的表单和状态条标签会位移 14px 且透明地闪一下。
    """
    css = INDEX.read_text(encoding="utf-8")
    reduced = css[css.index("prefers-reduced-motion") :]
    star = re.search(r"\*, \*::before, \*::after \{([^}]*)\}", reduced)
    assert star, "reduced-motion 里要有兜底的通配规则"
    assert "animation-delay:0s" in star.group(1), (
        "通配兜底里必须把 animation-delay 也清零"
    )


def test_login_scene_uses_no_third_party_3d_library():
    """星球场景必须是手写 canvas，不许拖进 3D 库。

    面板是单文件、无构建、CDN 挂了要能退化到最低限度可用。
    为一个登录页背景引入几百 KB 的依赖，和这套约束是冲突的。
    """
    html = INDEX.read_text(encoding="utf-8")
    for lib in ("three.min.js", "three.module", "babylon", "p5.js", "pixi"):
        assert lib not in html.lower(), f"登录页不该引入 {lib}"
    assert "getContext('2d')" in html, "场景应当走 2D canvas"


def test_login_scene_regions_are_real_oci_regions():
    """场景里的坐标点必须是 Oracle 真实存在的区域，不能编。

    这些名字会显示在状态条上（「刚跑完的链路」），编的名字就是假信息。
    """
    html = INDEX.read_text(encoding="utf-8")
    block = html[html.index("const REGIONS = ["):]
    block = block[: block.index("];")]
    names = re.findall(r"\['([a-z0-9-]+)',", block)
    assert len(names) >= 10, f"区域太少：{names}"
    pattern = re.compile(r"^(us|eu|uk|ap|sa|me|ca|il|af)-[a-z]+-\d$")
    bad = [n for n in names if not pattern.match(n)]
    assert not bad, f"这些不像 OCI 区域名：{bad}"
    # 用户自己的机器在 us-sanjose-1，这个区域应当在列表里
    assert "us-sanjose-1" in names


def test_login_scene_stops_when_logged_in():
    """登录成功后场景要停掉，别在后台白烧 CPU。"""
    html = INDEX.read_text(encoding="utf-8")
    assert re.search(r"if \(token\.value\) \{ OcixScene\.stop\(\); return; \}", html), (
        "token 有值时必须 stop()"
    )
    assert "onUnmounted(() => OcixScene.stop())" in html, "组件卸载时也要 stop()"
    assert "document.hidden" in html, "标签页不可见时应当跳过绘制"


def test_chart_series_differ_by_dash_not_only_color():
    """监控图两条曲线必须虚实不同，不能只靠颜色分。"""
    js = INDEX.read_text(encoding="utf-8")
    cpu = re.search(r"CpuUtilization:\s*\{[^}]*dash:\s*(\w+)", js)
    mem = re.search(r"MemoryUtilization:\s*\{[^}]*dash:\s*(\w+)", js)
    assert cpu and mem, "找不到两条曲线的样式定义"
    assert cpu.group(1) != mem.group(1), "CPU 和内存两条线必须一实一虚"


def test_links_are_underlined():
    """颜色能标示可点击，但下划线不挑视力也不挑屏幕。"""
    css = INDEX.read_text(encoding="utf-8")
    link = re.search(r"\.link\s*\{([^}]*)\}", css)
    assert link and "text-decoration:underline" in link.group(1), (
        "链接必须带下划线"
    )
