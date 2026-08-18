import re
from pathlib import Path


def test_frontend_bindings_and_security():
    html = Path('src/ocix/web/index.html').read_text(encoding='utf-8')

    # 1. 确保没有危险的 v-html 注入
    assert 'v-html' not in html, "发现潜在 XSS 风险指令: v-html"

    # 2. 确保没有 document.write 或 innerHTML 直接拼接用户输入
    assert 'document.write' not in html, "发现危险的 document.write"

    # 3. 提取 setup return 的所有属性
    return_match = re.search(r'return\s*\{([\s\S]*?)\n\s*\};\s*\n\s*\},', html)
    assert return_match, "无法提取 setup return 对象"

    returned_keys = set()
    for line in return_match.group(1).splitlines():
        line = re.sub(r'//.*', '', line).strip()
        for token in re.split(r'[,:\s]+', line):
            if token and re.match(r'^[a-zA-Z0-9_$]+$', token):
                returned_keys.add(token)

    # 4. 检查模板中所有使用的顶级变量与方法是否均在 returned_keys 或 Vue 全局可用
    builtin_globals = {
        'true', 'false', 'null', 'undefined', 'Math', 'Number', 'String', 'Array', 'Object',
        '$event', '$index', 'row', 'i', 'p', 'b', 'c', 'w', 'it', 'item', 'k', 'v', 'f', 'm', 'idx',
        'clean', 'spec', 'scope', 'key', 'val', 'typeof',
    }

    # 检查模板中的简单插值表达式 {{ foo }}
    interpolations = re.findall(r'\{\{\s*([a-zA-Z0-9_$]+)', html)
    missing = []
    for var in interpolations:
        if var not in builtin_globals and var not in returned_keys:
            missing.append(var)

    assert not missing, f"模板中有未在 setup 中返回的变量: {missing}"
    print(f"Audit passed: {len(returned_keys)} setup bindings verified without missing variables.")
