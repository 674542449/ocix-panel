from pathlib import Path


def test_frontend_bindings_and_security():
    web_dir = Path("src/ocix/web")
    files = list(web_dir.rglob("*"))
    html_and_js = [f for f in files if f.is_file() and f.suffix in (".html", ".js")]

    # 1. 确保所有前端文件没有危险的 v-html 注入
    for f in html_and_js:
        content = f.read_text(encoding="utf-8")
        assert "v-html" not in content, f"{f.name} 中发现潜在 XSS 风险指令: v-html"

    # 2. 确保没有 document.write
    for f in html_and_js:
        content = f.read_text(encoding="utf-8")
        assert "document.write" not in content, f"{f.name} 中发现危险的 document.write"

    # 3. 确保没有任何 Emoji 字符
    emoji_pattern = (
        "[\U00010000-\U0010ffff\u2600-\u26ff\u2700-\u27bf\u2300-\u23ff"
        "\u2b50\u2b55\u2934\u2935\u25aa\u25ab\u25fe\u25fd\u25fc\u25fb"
        "\u2b1b\u2b1c\u203c\u2049\u3030\u303d\u3297\u3299]"
    )
    import re
    for f in html_and_js:
        content = f.read_text(encoding="utf-8")
        emojis = re.findall(emoji_pattern, content)
        assert not emojis, f"{f.name} 中存在 Emoji 字符: {emojis}"
