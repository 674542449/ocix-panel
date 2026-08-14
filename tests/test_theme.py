"""面板配色的几条红线。

配色本身可以换（换过一次：彩色 → 纯灰阶 → 现在的低饱和石墨青），
但下面这几条不管换成什么都得成立：

  * 不许艳丽——所有彩色的饱和度有上限
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

# 饱和度上限。用户明确否掉过「艳丽」的配色——原来那套 accent #38bdf8 是 93%、
# ok #22c55e 是 71%、crit #ef4444 是 84%。60% 这条线放得过现在的一套
# （最高 50%），但拦得住任何一个 Tailwind 默认色。
MAX_SATURATION = 60
# 亮度太低的颜色算「暗色底」，不受饱和度约束——深色背景本来就该有点色调
MIN_LIGHTNESS_FOR_CHECK = 25

_HEX = re.compile(r"#([0-9a-fA-F]{6})\b")


def _strip_comments(text: str) -> str:
    """注释里会提到历史配色（说明为什么换掉），不该被判成违规。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _hls(hex_color: str) -> tuple[float, float, float]:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))
    hue, light, sat = colorsys.rgb_to_hls(r, g, b)
    return hue * 360, light * 100, sat * 100


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
        f"这些颜色太艳了：{bad}。面板的配色是低饱和的，"
        f"上限 {MAX_SATURATION}%——用户明确否掉过高饱和那一版。"
    )


def test_no_vivid_colors_in_favicon():
    bad = _too_vivid(FAVICON.read_text(encoding="utf-8"))
    assert not bad, f"favicon 里的颜色太艳：{bad}"


def test_status_hues_are_far_enough_apart():
    """状态色的色相要拉开，否则低饱和之下两个状态会糊成一片。

    尤其是 warn（沙黄）和 crit（陶土玫瑰）——把饱和度压下来之后，
    这两个如果色相只差二十几度，并排摆着就分不出来了。
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
            "低饱和下分不出来"
        )


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
    # 呼吸的光晕原地涨落（扩散半径变化），脉冲是环向外推
    assert "9px" in breathe and "4px" in breathe, "呼吸应当是光晕大小在变"

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
    """登录页三颗灯要错开相位。

    同步呼吸像圣诞灯，真机架上的指示灯不会同步。
    这里踩过一个坑：ARM 那块也是 .alloc 的第二个子元素，
    `.alloc-box:nth-child(2)` 会连它一起选中，而且特异性还压过
    `.alloc-arm .led`，结果两颗灯同相位。所以必须限定在 .alloc-col 里数。
    """
    css = INDEX.read_text(encoding="utf-8")
    assert ".alloc-col .alloc-box:nth-child(2) .led" in css, (
        "第二颗灯的选择器必须限定在 .alloc-col 内，否则会误伤 ARM 那块"
    )
    delays = re.findall(r"\.led \{ animation-delay:(-[\d.]+)s", css)
    assert len(set(delays)) == len(delays) and len(delays) >= 2, (
        f"各颗灯的相位要互不相同，现在是 {delays}"
    )


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
