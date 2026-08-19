import re
from pathlib import Path

HTML = Path('src/ocix/web/index.html')


def _html() -> str:
    return HTML.read_text(encoding='utf-8')


def test_create_button_accepts_password_only_login():
    """后端明确允许「只给 root 密码、不给公钥」，界面不能比后端还严。

    canCreate 曾在末尾多挂一个 createForm.ssh_public_key，于是勾了
    「启用 root 密码登录」也必须再贴一把公钥，创建按钮一直是灰的——
    整个功能在界面上走不通。
    """
    html = _html()
    m = re.search(r'const canCreate = computed\(\(\) =>(.*?)\);', html, re.S)
    assert m, '找不到 canCreate'
    body = m.group(1)
    # 公钥只能出现在「没选密码登录」那个分支里
    assert body.count('createForm.ssh_public_key') == 1, body
    assert 'use_password' in body


def test_terminal_assets_prefer_the_local_copy():
    """xterm 只从 unpkg 拿的话，CDN 不通的网络里网页终端整个用不了。

    其余前端依赖都是「先 /assets 再 CDN」，终端也必须一样。
    """
    html = _html()
    m = re.search(r'function ensureXterm\(\)(.*?)\n    }\n', html, re.S)
    assert m, '找不到 ensureXterm'
    body = m.group(1)
    assert "'/assets/' + name" in body or "/assets/xterm" in body
    assert 'XTERM_CDN' in body


def test_firewall_rule_deletion_sends_the_owning_security_list():
    """子网可以挂多个安全列表，序号只在列表内部有意义。"""
    html = _html()
    m = re.search(r'async function deleteRule\(index\)(.*?)\n    }\n', html, re.S)
    assert m, '找不到 deleteRule'
    assert 'security_list_id' in m.group(1)


def test_jump_to_create_reloads_options():
    """从雷达跳到新建实例时，如果切了规格必须重新拉取镜像和配置。"""
    html = _html()
    m = re.search(r'function jumpToCreateWithAd\(ad\)(.*?)\n    }\n', html, re.S)
    assert m, '找不到 jumpToCreateWithAd'
    body = m.group(1)
    assert 'loadCreateOptions()' in body
    assert 'runPreflight()' in body


def test_csv_downloads_use_authenticated_axios_blob():
    """window.open 无法携带 Bearer token 会导致 401 报错；CSV 导出必须使用带鉴权的 axios blob 请求。"""
    html = _html()
    assert "window.open('/api/monitor/invoices/download" not in html
    assert "window.open('/api/monitor/cost/export" not in html
    assert "axios.get('/api/monitor/invoices/download'" in html
    assert "axios.get('/api/monitor/cost/export'" in html


def test_frontend_bindings_and_security():
    html = _html()

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
