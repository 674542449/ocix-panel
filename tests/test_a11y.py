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

INDEX = Path(__file__).resolve().parents[1] / "src" / "ocix" / "web" / "index.html"


def _html() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_card_titles_are_headings_not_divs():
    """卡片标题必须是标题元素，读屏器靠它在页面里跳转。"""
    html = _html()
    assert '<div class="card-title"' not in html, "卡片标题不能再是 <div>"
    assert '<span class="card-title">' not in html, "卡片标题不能是 <span>"
    n = len(re.findall(r'<h2 class="card-title"', html))
    assert n >= 15, f"只找到 {n} 个卡片标题，应当每张卡片都有"


def test_every_page_has_a_heading():
    """每页一个 h1。左侧导航的高亮只有看得见的人用得上。"""
    html = _html()
    assert re.search(r'<h1 class="sr-only">\{\{ currentTabLabel \}\}</h1>', html), (
        "主内容区要有一个跟随当前页的 h1"
    )
    assert "const currentTabLabel = computed(" in html, "currentTabLabel 得真的算出来"


def test_heading_levels_do_not_skip():
    """h1 -> h2 -> h3，中间不能跳级（原来抽屉里直接是 h4）。"""
    html = _html()
    assert "<h4" not in html, "h4 之上没有 h3，属于跳级"
    assert len(re.findall(r"<h3[ >]", html)) >= 3, "抽屉小节应当是 h3"


def test_screen_reader_only_text_is_not_display_none():
    """display:none 连读屏器也读不到，那就白藏了。"""
    css = _html()
    rule = re.search(r"\.sr-only \{([^}]*)\}", css)
    assert rule, "缺少 .sr-only"
    body = rule.group(1)
    assert "display:none" not in body.replace(" ", "")
    assert "clip:" in body or "clip-path" in body, "要用 clip 的方式藏"


def test_skip_link_is_the_first_focusable_element():
    """跳转链接必须排在顶栏之前，否则第一个 Tab 会落到顶栏的开关上。

    踩过：一开始放在 .shell 前面，看着挺靠前，其实顶栏的自动刷新开关
    先拿到焦点，链接等于没用。
    """
    html = _html()
    skip = html.index('class="skip-link"')
    topbar = html.index('<div class="topbar">')
    assert skip < topbar, "跳转链接要排在顶栏之前"

    target = re.search(r'<a class="skip-link" href="#([\w-]+)"', html)
    assert target, "跳转链接要指向一个锚点"
    anchor = target.group(1)
    assert f'id="{anchor}"' in html, f"锚点 #{anchor} 不存在"
    # 目标要能接住焦点，否则点了不会真的把焦点挪过去
    assert re.search(rf'id="{anchor}"[^>]*tabindex="-1"', html), (
        "跳转目标要有 tabindex=-1，不然焦点不会落进去"
    )


def test_card_title_is_not_smaller_than_body_text():
    """标题不能比它领起的正文还小——原来是 13px 标题配 14px 正文。"""
    css = _html()
    body = re.search(r"body \{[^}]*font-size:(\d+(?:\.\d+)?)px", css, flags=re.S)
    title = re.search(r"\.card-title \{[^}]*font-size:(\d+(?:\.\d+)?)px", css, flags=re.S)
    assert body and title, "取不到字号"
    body_px, title_px = float(body.group(1)), float(title.group(1))
    assert title_px >= body_px, (
        f"卡片标题 {title_px}px 小于正文 {body_px}px，层级是反的"
    )


def test_landmarks_and_lang_are_present():
    """审下来这几项本来就合格，钉住别退化。"""
    html = _html()
    assert 'lang="zh-CN"' in html
    assert "<main class=\"main\"" in html
    assert "<nav>" in html
    assert 'aria-current' in html, "当前导航项要标出来"
