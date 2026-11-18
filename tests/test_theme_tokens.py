"""Token lint: theme.py is the only source of raw hex in the Python UI.

Run standalone (no Qt needed):

    python tests/test_theme_tokens.py

Checks:
- required brand tokens exist and are valid colors;
- no ``#rrggbb`` in python3.13libs/houtools/**/*.py outside ui/theme.py;
- docs/index.html uses CSS vars / token values only in :root and inline SVG
  (inline SVG illustrations are historical content assets);
- key text/background and primary-button contrast ratios.
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python3.13libs"))

from houtools.ui import theme  # noqa: E402

REQUIRED = (
    "INK_1000", "INK_950", "INK_900", "INK_850", "INK_800",
    "LINE", "LINE_STRONG",
    "TEXT", "TEXT_2", "TEXT_3", "TEXT_4",
    "ACCENT", "ACCENT_HOVER", "ACCENT_PRESSED", "ACCENT_SOFT",
    "ACCENT_LINE",
    "SUCCESS", "SUCCESS_TEXT", "WARNING", "WARNING_TEXT",
    "ERROR", "ERROR_TEXT", "GRID_LINE", "GRID_DOT",
)

_HEX6 = re.compile(r"#[0-9a-fA-F]{6}\b")
_HEX3 = re.compile(r"(?<![\w#])#[0-9a-fA-F]{3}\b")


def _valid_color(value):
    if value.startswith("rgba(") or value.startswith("rgb("):
        return True
    return bool(re.fullmatch(r"#[0-9a-fA-F]{6}", value))


def _lum(hex_color):
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))

    def f(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = f(r), f(g), f(b)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ratio(fg, bg):
    l1, l2 = sorted((_lum(fg), _lum(bg)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def check_tokens():
    for name in REQUIRED:
        assert name in theme.TOKENS, "missing token: %s" % name
        assert _valid_color(theme.TOKENS[name]), \
            "invalid token %s=%r" % (name, theme.TOKENS[name])
    for role in ("brand", "ui", "mono", "doc"):
        assert role in theme.FONT_ROLES, "missing font role: %s" % role
    print("tokens OK (%d)" % len(theme.TOKENS))


def check_python_hex():
    pkg = ROOT / "python3.13libs" / "houtools"
    theme_path = pkg / "ui" / "theme.py"
    offenders = []
    for path in sorted(pkg.rglob("*.py")):
        if path == theme_path:
            continue
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if _HEX6.search(line):
                offenders.append("%s:%d: %s" % (
                    path.relative_to(ROOT), i, line.strip()))
    assert not offenders, "raw hex outside theme.py:\n" + "\n".join(offenders)
    print("python hex lint OK")


def check_docs():
    doc_path = ROOT / "docs" / "index.html"
    src = doc_path.read_text(encoding="utf-8")
    # :root token declarations are the document mirror of theme.py;
    # inline SVG is historical content art (not UI chrome).
    stripped = re.sub(r":root\s*\{.*?\}", "", src, flags=re.S)
    stripped = re.sub(r"<svg\b.*?</svg>", "", stripped, flags=re.S | re.I)
    offenders = []
    for i, line in enumerate(stripped.splitlines(), 1):
        if _HEX6.search(line) or _HEX3.search(line):
            offenders.append("docs/index.html:%d: %s" % (i, line.strip()))
    assert not offenders, (
        "raw hex outside :root/inline SVG in docs/index.html:\n"
        + "\n".join(offenders))
    # 离线：不得通过 src / link 加载外部资源
    assert 'src="http' not in src
    assert "<link" not in src
    assert "fonts/DingTalk-JinBuTi.woff2" in src
    print("docs token/offline lint OK")


def check_contrast():
    pairs = [
        ("正文/工作区", "TEXT", "INK_950", 12.0),
        ("次要文字/工作区", "TEXT_2", "INK_950", 7.0),
        ("提示文字/工作区", "TEXT_3", "INK_950", 4.5),
        ("正文/卡片", "TEXT", "INK_900", 12.0),
        # 2026-11-19 用户拍板：主按钮常态 = ACCENT_FILL 暗橙底白字；悬停
        # 不改底色（任何底色变亮都压低白字对比，实测否决），点亮感由边缘
        # 描边/光晕表达；按下压深 ACCENT_FILL_PRESSED（对比度更高，方向安全）
        ("主按钮文字/暗橙", "ON_ACCENT", "ACCENT_FILL", 4.5),
        ("错误文字/浮层", "ERROR_TEXT", "INK_850", 4.5),
        ("成功文字/浮层", "SUCCESS_TEXT", "INK_850", 7.0),
        ("警告文字/浮层", "WARNING_TEXT", "INK_850", 7.0),
    ]
    for label, fg, bg, minimum in pairs:
        ratio = _ratio(theme.color(fg), theme.color(bg))
        assert ratio >= minimum, "%s contrast %.2f < %.2f" % (
            label, ratio, minimum)
        print("contrast %-16s %.2f" % (label, ratio))


def main():
    check_tokens()
    check_python_hex()
    check_docs()
    check_contrast()
    print("theme token lint: all OK")


if __name__ == "__main__":
    main()
