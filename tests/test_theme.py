"""面板配色是单色的——这里把它钉死。

改成黑白灰不是「顺手调一下」，而是一整套替代方案：颜色原本承担的
信息（运行中 / 已停止 / 过渡中）改由填充、空心、外环、形状去承担。
只要有人往回加一个彩色值，那套替代方案就会开始失效，而且失效得很安静：
页面照样能看，只是状态又开始只靠颜色区分了。所以用测试挡住。
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "ocix" / "web"
INDEX = WEB / "index.html"
FAVICON = WEB / "favicon.svg"

# 允许的通道差。0 是纯灰；给 6 是因为 #cfcfcf 这类值将来若手抖写成
# #cfd0cf 也不该算「彩色」，但 #38bdf8（差 192）一定拦得住。
MAX_CHANNEL_SPREAD = 6

_HEX = re.compile(r"#([0-9a-fA-F]{6})\b")


def _strip_comments(text: str) -> str:
    """注释里会提到旧配色（说明为什么换掉），不该被判成违规。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"<!--.*?-->", "", text, flags=re.S)


def _hued(text: str) -> list[str]:
    out = []
    for m in _HEX.finditer(_strip_comments(text)):
        h = m.group(1)
        ch = [int(h[i : i + 2], 16) for i in (0, 2, 4)]
        if max(ch) - min(ch) > MAX_CHANNEL_SPREAD:
            out.append(m.group(0))
    return sorted(set(out))


def test_index_html_has_no_hued_colors():
    bad = _hued(INDEX.read_text(encoding="utf-8"))
    assert not bad, (
        f"面板里出现了带色相的颜色：{bad}。"
        "配色是黑白灰的，状态靠形态区分（见 .dot 的注释）——"
        "加回彩色会让那套区分变成多余的装饰。"
    )


def test_favicon_has_no_hued_colors():
    bad = _hued(FAVICON.read_text(encoding="utf-8"))
    assert not bad, f"favicon 里出现了带色相的颜色：{bad}"


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


def test_status_dots_are_distinguishable_without_color():
    """五种状态点必须各有各的形态，不能只靠灰度深浅区分。

    关掉动效之后 .dot.warn 只剩一个静止的环，如果它跟 .dot.attn 一样是
    「实心圆 + 环」，两者就分不出来了——所以 attn 是方的。
    """
    css = INDEX.read_text(encoding="utf-8")
    block = css[css.index(".dot {") : css.index(".state-label")]
    # 实心最亮 = 运行中
    assert re.search(r"\.dot\.ok\s*\{[^}]*background:var\(--hi\)", block)
    # 空心 = 已停止
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


def test_links_are_underlined():
    """没有色相之后，「亮一点」不足以说明这是能点的，必须有下划线。"""
    css = INDEX.read_text(encoding="utf-8")
    link = re.search(r"\.link\s*\{([^}]*)\}", css)
    assert link and "text-decoration:underline" in link.group(1), (
        "链接必须带下划线——单色下颜色已经不能用来标示可点击了"
    )
