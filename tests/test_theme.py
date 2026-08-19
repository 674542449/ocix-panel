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


def _css_bundle() -> str:
    parts = []
    for p in (WEB / "css").glob("*.css"):
        parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _all_bundle() -> str:
    parts = []
    for p in WEB.rglob("*"):
        if p.is_file() and p.suffix in (".html", ".css", ".js"):
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)

# 饱和度上限
MAX_SATURATION = 80
MIN_LIGHTNESS_FOR_CHECK = 25

_HEX = re.compile(r"#([0-9a-fA-F]{6})\b")


def _strip_comments(text: str) -> str:
    """注释里会提到历史配色（说明为什么换掉），不该被判成违规。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//.*$", "", text, flags=re.M)


def _colors(text: str) -> list[tuple[str, int, int, int]]:
    """提取所有十六进制色值并转成 (hex, h_deg, s_pct, v_pct)。"""
    res = []
    for m in _HEX.finditer(_strip_comments(text)):
        h = m.group(1).lower()
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        h_deg, s_pct, v_pct = colorsys.rgb_to_hsv(r, g, b)
        res.append((f"#{h}", int(h_deg * 360), int(s_pct * 100), int(v_pct * 100)))
    return res


def test_no_high_saturation_colours_in_stylesheet():
    """样式表里不能有太艳的颜色。"""
    css = _css_bundle()
    bad = []
    for hex_c, _h_deg, s_pct, v_pct in _colors(css):
        if v_pct < MIN_LIGHTNESS_FOR_CHECK:
            continue
        if s_pct > MAX_SATURATION:
            bad.append(f"{hex_c} (HSV 饱和度 {s_pct}%, 明度 {v_pct}%)")
    assert not bad, "这些颜色太艳了，容易刺眼：\n  " + "\n  ".join(bad)


def test_status_hues_are_far_enough_apart():
    """绿、黄、红三组状态色的色相必须明显分开（至少相隔 40°）。"""
    css = _css_bundle()
    ok_m = re.search(r"--ok:\s*(#[0-9a-fA-F]{6})", css)
    warn_m = re.search(r"--warn:\s*(#[0-9a-fA-F]{6})", css)
    crit_m = re.search(r"--crit:\s*(#[0-9a-fA-F]{6})", css)
    assert ok_m and warn_m and crit_m, "找不到 --ok / --warn / --crit"

    def hue(hex_c: str) -> int:
        h = hex_c.lstrip("#")
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        return int(colorsys.rgb_to_hsv(r, g, b)[0] * 360)

    h_ok = hue(ok_m.group(1))
    h_warn = hue(warn_m.group(1))
    h_crit = hue(crit_m.group(1))

    def diff(a, b):
        d = abs(a - b) % 360
        return d if d <= 180 else 360 - d

    assert diff(h_ok, h_warn) >= 40, (
        f"绿 {ok_m.group(1)}({h_ok}°) 与黄 {warn_m.group(1)}({h_warn}°) 色相太近"
    )
    assert diff(h_warn, h_crit) >= 40, (
        f"黄 {warn_m.group(1)}({h_warn}°) 与红 {crit_m.group(1)}({h_crit}°) 色相太近"
    )
    assert diff(h_ok, h_crit) >= 40, (
        f"绿 {ok_m.group(1)}({h_ok}°) 与红 {crit_m.group(1)}({h_crit}°) 色相太近"
    )


def test_accent_is_far_from_the_running_colour():
    """主色不能跟「运行中」绿灯撞色。"""
    css = _css_bundle()
    accent_m = re.search(r"--accent:\s*(#[0-9a-fA-F]{6})", css)
    ok_m = re.search(r"--ok:\s*(#[0-9a-fA-F]{6})", css)
    assert accent_m and ok_m, "找不到 --accent / --ok"

    def hue(hex_c: str) -> int:
        h = hex_c.lstrip("#")
        r = int(h[0:2], 16) / 255.0
        g = int(h[2:4], 16) / 255.0
        b = int(h[4:6], 16) / 255.0
        return int(colorsys.rgb_to_hsv(r, g, b)[0] * 360)

    h_acc = hue(accent_m.group(1))
    h_ok = hue(ok_m.group(1))
    d = abs(h_acc - h_ok) % 360
    diff = d if d <= 180 else 360 - d
    assert diff >= 30, (
        f"主色 {accent_m.group(1)}({h_acc}°) 和绿灯 {ok_m.group(1)}({h_ok}°) 色相太近"
    )


def test_element_plus_overrides_outrank_the_library():
    """Element Plus 的 CSS 变量覆盖必须挂在 :root:root 上。"""
    css = _css_bundle()
    assert ":root:root {" in css, "变量块要写成 :root:root 才压得过组件库"
    assert not re.search(r"^\s*html\.dark\s*\{", css, flags=re.M)
    assert not re.search(r"^\s*:root \{\s*\n\s*--el-", css, flags=re.M), (
        "element-plus 的变量块不能只挂在单个 :root 上"
    )


def test_status_dots_carry_shape_as_well_as_color():
    """状态点不能只靠颜色区分，形态也得带着同一条信息。"""
    css = _css_bundle()
    block = css[css.index(".dot {") : css.index(".state-label")]
    assert re.search(r"\.dot\.ok\s*\{[^}]*background:var\(--ok\)", block)
    assert re.search(r"\.dot\.crit\s*\{[^}]*background:transparent", block)
    assert re.search(r"\.dot\.warn\s*\{[^}]*animation:pulse", block)
    assert re.search(r"\.dot\.attn\s*\{[^}]*border-radius:2px", block)

    reduced = css[css.index("prefers-reduced-motion") :]
    warn_fallback = re.search(r"\.dot\.warn\s*\{([^}]*)\}", reduced)
    assert warn_fallback, "prefers-reduced-motion 下必须给 .dot.warn 一个静态替代"
    assert warn_fallback.group(1).count("0 0 0") >= 3, (
        "静态替代得是多重环"
    )


def test_breathing_and_pulsing_are_different_motions():
    """「运行中」呼吸、「过渡中」脉冲，两种动法必须真的不一样。"""
    css = _css_bundle()

    def block(name: str) -> str:
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
    assert "0%, 100%" in breathe, "呼吸的首尾要同值"
    assert re.search(r"100%[^}]*,\s*0\)", pulse), "脉冲末帧应当淡出到透明"

    scales = [float(x) for x in re.findall(r"scale\(([\d.]+)\)", breathe)]
    assert len(scales) >= 2, "呼吸要带灯芯缩放"
    assert max(scales) / min(scales) >= 1.35

    blurs = [int(x) for x in re.findall(r"0 0 (\d+)px \d+(?:px)? rgba", breathe)]
    assert len(blurs) >= 2 and max(blurs) >= min(blurs) * 3
    assert breathe.count("background:") >= 2

    ok = re.search(r"\.dot\.ok \{[^}]*animation:(\w+) ([\d.]+)s ([\w-]+)", css)
    warn = re.search(r"\.dot\.warn \{[^}]*animation:(\w+) ([\d.]+)s ([\w-]+)", css)
    assert ok and warn
    assert ok.group(1) == "breathe" and warn.group(1) == "pulse"
    assert float(ok.group(2)) >= float(warn.group(2)) * 1.8
    assert ok.group(3) != warn.group(3)


def test_breathing_has_a_static_fallback():
    """关掉动效时呼吸灯要退化成静态光晕，不能变成一颗死灯。"""
    css = _css_bundle()
    reduced = css[css.index("prefers-reduced-motion") :]
    assert re.search(r"\.dot\.ok, \.led \{[^}]*box-shadow", reduced)


def test_all_text_contrast_ratios_meet_wcag():
    """全量检测控制台文字对比度，确保所有文本均符合 WCAG 2.1 正常清晰阅览标准。"""
    css = _css_bundle()

    def rel_lum(hex_c):
        h = hex_c.lstrip("#")
        rgb = [int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4)]
        rgb_l = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
        return 0.2126 * rgb_l[0] + 0.7152 * rgb_l[1] + 0.0722 * rgb_l[2]

    def contrast(c1, c2):
        l1, l2 = rel_lum(c1), rel_lum(c2)
        return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

    bg = "#0b0f19"
    surface = "#0f172a"

    tokens = {}
    for name in ("text", "text-2", "text-3", "accent", "ok", "warn", "crit"):
        m = re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", css)
        assert m, f"找不到 --{name}"
        tokens[name] = m.group(1)

    assert contrast(tokens["text"], bg) >= 7.0
    assert contrast(tokens["text-2"], bg) >= 7.0

    for name, hex_c in tokens.items():
        cr = contrast(hex_c, surface)
        assert cr >= 4.5, f"--{name} ({hex_c}) 对比度 {cr:.2f}:1 低于 WCAG 4.5:1 标准"


def test_reduced_motion_zeroes_animation_delay():
    """关掉动效时延迟也要清零，不只是时长。"""
    css = _css_bundle()
    reduced = css[css.index("prefers-reduced-motion") :]
    star = re.search(r"\*, \*::before, \*::after \{([^}]*)\}", reduced)
    assert star, "reduced-motion 里要有兜底的通配规则"
    body = star.group(1)
    assert "animation-delay: 0ms" in body or "animation-delay:0ms" in body or "animation-delay:0s" in body


def test_login_scene_uses_no_third_party_3d_library():
    """星球场景必须是手写 canvas，不许拖进 3D 库。"""
    html = _all_bundle()
    for lib in ("three.min.js", "three.module", "babylon", "p5.js", "pixi"):
        assert lib not in html.lower(), f"登录页不该引入 {lib}"
    assert "getContext('2d')" in html, "场景应当走 2D canvas"


def test_login_scene_regions_are_real_oci_regions():
    """场景里的坐标点必须是 Oracle 真实存在的区域，不能编。"""
    html = _all_bundle()
    block = html[html.index("const REGIONS = ["):]
    block = block[: block.index("];")]
    names = re.findall(r"\['([a-z0-9-]+)',", block)
    assert len(names) >= 10, f"区域太少：{names}"
    pattern = re.compile(r"^(us|eu|uk|ap|sa|me|ca|il|af)-[a-z]+-\d$")
    bad = [n for n in names if not pattern.match(n)]
    assert not bad, f"这些不像 OCI 区域名：{bad}"
    assert "us-sanjose-1" in names


def test_login_scene_stops_when_logged_in():
    """登录成功后场景要停掉，别在后台白烧 CPU。"""
    html = _all_bundle()
    assert "OcixScene.stop()" in html, "token 有值时必须 stop()"


def test_chart_series_differ_by_dash_not_only_color():
    """监控图两条曲线必须虚实不同，不能只靠颜色分。"""
    js = _all_bundle()
    assert "dash: m.metric === 'MemoryUtilization'" in js, "CPU 和内存两条线必须一实一虚"


def test_links_are_underlined():
    """颜色能标示可点击，但下划线不挑视力也不挑屏幕。"""
    css = _css_bundle()
    link = re.search(r"\.link\s*\{([^}]*)\}", css)
    assert link and "text-decoration:underline" in link.group(1), (
        "链接必须带下划线"
    )
