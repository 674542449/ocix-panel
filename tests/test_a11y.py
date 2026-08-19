"""界面的结构与无障碍红线。

这些都是拿 ui-ux-pro-max 的规则去量真实页面之后发现的实际问题，
不是照着清单空想的：

  * 全站 0 个标题元素——20 个卡片标题是 <div>，读屏器没法按标题跳转
  * 卡片标题 13px，正文 14px——标题比它领起的正文还小，层级是反的
  * 没有「跳到主内容」，键盘用户每换一页都要 Tab 过顶栏和 11 项导航

顺带说明审下来**没**动的地方，免得以后有人以为漏了：空状态每页都有
说明和下一步动作、lang/main/nav 齐全、真键盘 Tab 下焦点环是 2px 主色、
图表虚实区分、表格有横向滚动容器——这些本来就合格。
"""

import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "src" / "ocix" / "web"


def _bundle() -> str:
    parts = []
    for p in WEB.rglob("*"):
        if p.is_file() and p.suffix in (".html", ".css", ".js"):
            parts.append(p.read_text(encoding="utf-8"))
    return "\n".join(parts)


def test_card_titles_are_headings_not_divs():
    """卡片标题必须是标题元素，读屏器靠它在页面里跳转。"""
    html = _bundle()
    assert '<div class="card-title"' not in html, "卡片标题不能再是 <div>"
    assert '<span class="card-title">' not in html, "卡片标题不能是 <span>"
    n = len(re.findall(r'<h2 class="card-title"', html))
    assert n >= 15, f"只找到 {n} 个卡片标题，应当每张卡片都有"


def test_every_page_has_a_heading():
    """每页一个 h1。左侧导航的高亮只有看得见的人用得上。"""
    html = _bundle()
    assert re.search(r'<h1 class="sr-only">\{\{ currentTabLabel \}\}</h1>', html), (
        "主内容区要有一个跟随当前页的 h1"
    )
    assert "const currentTabLabel = computed(" in html, "currentTabLabel 得真的算出来"


def test_heading_levels_do_not_skip():
    """h1 -> h2 -> h3，中间不能跳级（原来抽屉里直接是 h4）。"""
    html = _bundle()
    assert "<h4" not in html, "h4 之上没有 h3，属于跳级"
    assert len(re.findall(r"<h3[ >]", html)) >= 3, "抽屉小节应当是 h3"


def test_screen_reader_only_text_is_not_display_none():
    """display:none 连读屏器也读不到，那就白藏了。"""
    css = _bundle()
    rule = re.search(r"\.sr-only\s*\{([^}]*)\}", css)
    assert rule, "缺少 .sr-only"
    body = rule.group(1)
    assert "display:none" not in body.replace(" ", "")
    assert "clip:" in body or "clip-path" in body, "要用 clip 的方式藏"


def test_skip_link_is_the_first_focusable_element():
    """「跳到主内容」必须是整个 body 里第一个可聚焦元素。

    否则键盘用户 Tab 进来先踩到的是顶栏按钮，等于没装。
    """
    html = (WEB / "index.html").read_text(encoding="utf-8")
    m = re.search(r"<body[^>]*>(.*?)<main", html, flags=re.S)
    assert m, "找不到 body 到 main 之间的内容"
    header = m.group(1)
    skip = re.search(r'<a\s+class="skip-link"[^>]*href="#main-content"', header)
    assert skip, "缺少 skip link"
    focusable_before_skip = re.findall(
        r"<(?:a|button|input|select|textarea|el-button|el-switch)[ >]",
        header[: skip.start()],
    )
    assert not focusable_before_skip, (
        f"skip link 前面有其他可聚焦元素: {focusable_before_skip}"
    )


def test_card_title_is_not_smaller_than_body_text():
    """卡片标题 14.5px+，正文 14px。标题不能比它下面的字还小。"""
    css = _bundle()
    body_m = re.search(r"body\s*\{[^}]*font-size:\s*(\d+)px", css)
    title_m = re.search(r"\.card-title\s*\{[^}]*font-size:\s*([\d.]+)px", css)
    assert body_m and title_m, "找不到 body 或 .card-title 的字号"
    assert float(title_m.group(1)) > float(body_m.group(1)), (
        f"卡片标题字号 ({title_m.group(1)}px) 必须大于正文 ({body_m.group(1)}px)"
    )
