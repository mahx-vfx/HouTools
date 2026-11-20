"""About page sync lint: keep docs/index.html tied to its real sources.

Runs standalone with any Python (no Qt / Houdini needed):

    python tests/test_about_sync.py

Background: docs/index.html is a single offline HTML manual. Its content is
distilled from README, its :root colors mirror theme.TOKENS, and its version,
nav anchors and tool list are kept in sync by hand. That model already drifted
(the README tool table still described an old packing layout, AGENTS.md said
"two inline SVG illustrations" when there were six, and a 1.9 MB hero.png was
orphaned). This check turns those sync points into assertions: adding a menu
item, bumping __version__, changing a token, or leaving an unreferenced asset
now fails here.

Division of labour with test_theme_tokens.py: that one checks "no raw hex
outside :root/SVG, no external resources"; this one checks "values equal the
source of truth, anchors and assets complete, no orphans".
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python3.13libs"))

from houtools.ui import theme  # noqa: E402

ABOUT = ROOT / "docs" / "index.html"
DOCS = ROOT / "docs"
MENU_FILES = ("MainMenuCommon.xml", "NetworkViewMenu.xml", "OPmenu.xml")

# docs/index.html :root declarations -> theme.TOKENS keys.
# Kept explicit on purpose: a new token must be registered here before the
# About page is allowed to use it.
CSS_TOKEN_MAP = {
    "ink-1000": "INK_1000",
    "ink-950": "INK_950",
    "ink-900": "INK_900",
    "ink-850": "INK_850",
    "ink-800": "INK_800",
    "line": "LINE",
    "line-strong": "LINE_STRONG",
    "text": "TEXT",
    "text-2": "TEXT_2",
    "text-3": "TEXT_3",
    "text-4": "TEXT_4",
    "accent": "ACCENT",
    "accent-hover": "ACCENT_HOVER",
    "accent-pressed": "ACCENT_PRESSED",
    "accent-soft": "ACCENT_SOFT",
    "accent-fill": "ACCENT_FILL",
    "accent-fill-pressed": "ACCENT_FILL_PRESSED",
    "on-accent": "ON_ACCENT",
    "success": "SUCCESS",
    "warning": "WARNING",
    "error": "ERROR",
}

# Local aliases / font stacks that are not token mirrors.
CSS_ALIASES = {"bg", "card", "border", "muted", "mono", "brand", "sans"}

# Every <scriptItem id> in the three menu XMLs -> the manual section id that
# documents it. None means "the About page itself", no section needed.
# New menu items must be registered here, which is the point of this check.
MENU_ITEM_TO_SECTION = {
    "houtools.dev.reload": "reload-dev",
    "houtools.automation": "automation",
    "houtools.video_to_sequence": "video-to-sequence",
    "houtools.hdr_library": "hdr-library",
    "houtools.recipe_library": "recipe-library",
    "houtools.package_project": "package-project",
    "houtools.houtools_settings": "houtools-settings",
    "houtools.about": None,
    "houtools.networkview.automation": "automation",
    "houtools.networkview.video_to_sequence": "video-to-sequence",
    "houtools.networkview.hdr_library": "hdr-library",
    "houtools.networkview.recipe_library": "recipe-library",
    "houtools.networkview.filecache_abs_path": "filecache-absolute",
    "houtools.networkview.package_project": "package-project",
    "pane.wsheet.houtools_paste_as_object_merge": "paste-as-object-merge",
    "pane.wsheet.houtools_copy_output_path": "copy-output-path",
    "houtools_open_cache_folder": "open-cache-folder",
    "houtools_capture_viewport_embed": "capture-viewport",
    "houtools_delete_viewport_capture": "capture-viewport",
    "houtools_paste_bbox_absolute": "paste-bounding-box",
    "houtools_paste_bbox_expressions": "paste-bounding-box",
    "houtools_paste_center_absolute": "paste-center",
    "houtools_paste_center_expressions": "paste-center",
}

# Manual sections with no menu entry (page-only content / externaldragdrop hook).
PAGE_ONLY_SECTIONS = {"install", "dragdrop-import"}

# docs/ files consumed elsewhere, not referenced by index.html.
# value = files that must mention this relative path.
OTHER_CONSUMERS = {
    "brand.md": ("README.md", "AGENTS.md"),
    "brand/houtools-banner.png": ("README.md",),
    "brand/houtools-banner.svg": ("AGENTS.md",),
}


def _norm(value):
    """Compare CSS color values ignoring whitespace (rgba spacing differs)."""
    return value.strip().lower().replace(" ", "")


def _read():
    return ABOUT.read_text(encoding="utf-8")


def check_version(src):
    pkg = ROOT / "python3.13libs" / "houtools" / "__init__.py"
    found = re.search(r'^__version__\s*=\s*"([^"]+)"',
                      pkg.read_text(encoding="utf-8"), re.M)
    assert found, "cannot read __version__ from houtools/__init__.py"
    version = found.group(1)
    shown = re.findall(r"v(\d+\.\d+\.\d+)\b", src)
    assert shown, "docs/index.html shows no version string"
    wrong = sorted({v for v in shown if v != version})
    assert not wrong, ("About page version %s != __version__ %s"
                       % (wrong, version))
    print("version OK (v%s x%d)" % (version, len(shown)))


def check_css_tokens(src):
    root = re.search(r":root\s*\{(.*?)\}", src, re.S)
    assert root, "docs/index.html has no :root block"
    decl = dict(re.findall(r"--([\w-]+):\s*([^;]+);", root.group(1)))
    expected = set(CSS_TOKEN_MAP) | CSS_ALIASES
    assert set(decl) == expected, (
        ":root variable set changed (new=%s, missing=%s); register it in "
        "tests/test_about_sync.py" % (
            sorted(set(decl) - expected), sorted(expected - set(decl))))
    for css_name, token in CSS_TOKEN_MAP.items():
        got = _norm(decl[css_name])
        want = _norm(theme.color(token))
        assert got == want, (
            "--%s = %s but theme.%s = %s (About page colors drifted)"
            % (css_name, decl[css_name].strip(), token, theme.color(token)))
    print("css tokens OK (%d mirrored)" % len(CSS_TOKEN_MAP))


def check_svg_hex(src):
    """Inline SVG illustrations are content art, but their colors must still
    come from theme.TOKENS (test_theme_tokens.py deliberately exempts SVG)."""
    allowed = {v.lower() for v in theme.TOKENS.values()
               if isinstance(v, str) and v.startswith("#")}
    root = re.search(r":root\s*\{(.*?)\}", src, re.S).group(1)
    allowed |= {h.lower() for h in re.findall(r"#[0-9a-fA-F]{6}\b", root)}
    svgs = re.findall(r"<svg\b.*?</svg>", src, re.S | re.I)
    assert svgs, "no inline SVG found (tool illustrations missing?)"
    offenders = []
    for svg in svgs:
        for h in re.findall(r"#[0-9a-fA-F]{6}\b", svg):
            if h.lower() not in allowed:
                offenders.append(h)
    assert not offenders, (
        "inline SVG uses colors absent from theme.TOKENS: %s"
        % sorted(set(offenders)))
    print("svg colors OK (%d illustrations)" % len(svgs))


def check_anchors(src):
    nav = re.search(r"<nav>.*?</nav>", src, re.S)
    assert nav, "tool directory <nav> missing"
    nav_links = re.findall(r'href="#([^"]+)"', nav.group(0))
    sections = re.findall(r'<section id="([^"]+)"', src)
    ids = set(re.findall(r'\bid="([^"]+)"', src))
    assert len(set(sections)) == len(sections), "duplicate section ids"
    assert nav_links == sections, (
        "nav order/content != sections (nav=%d, sections=%d)"
        % (len(nav_links), len(sections)))
    for target in re.findall(r'href="#([^"]+)"', src):
        assert target in ids, "dead anchor: #%s" % target
    print("anchors OK (%d nav / %d sections / %d slides)"
          % (len(nav_links), len(sections),
             len(re.findall(r'<a class="slide" href="#', src))))


def check_menu_coverage(src):
    menu_ids = []
    for name in MENU_FILES:
        xml = (ROOT / name).read_text(encoding="utf-8")
        menu_ids += re.findall(r'<scriptItem id="([^"]+)"', xml)
    missing = sorted(set(menu_ids) - set(MENU_ITEM_TO_SECTION))
    assert not missing, (
        "new menu item(s) not registered in MENU_ITEM_TO_SECTION "
        "(add the tool to the About page too): %s" % missing)
    stale = sorted(set(MENU_ITEM_TO_SECTION) - set(menu_ids))
    assert not stale, "stale menu mapping (item removed from XML): %s" % stale
    sections = set(re.findall(r'<section id="([^"]+)"', src))
    mapped = {v for v in MENU_ITEM_TO_SECTION.values() if v}
    assert mapped <= sections, (
        "mapped section(s) missing from About page: %s"
        % sorted(mapped - sections))
    orphan = sections - mapped - PAGE_ONLY_SECTIONS
    assert not orphan, (
        "section(s) with no tool registration: %s" % sorted(orphan))
    print("menu coverage OK (%d items / %d sections)"
          % (len(menu_ids), len(sections)))


def check_resources(src):
    refs = (re.findall(r'\bsrc="([^"]+)"', src)
            + re.findall(r"url\('([^']+)'\)", src))
    local = [r for r in refs if not r.startswith(
        ("http://", "https://", "data:"))]
    for rel in local:
        assert (DOCS / rel).is_file(), (
            "About page references a missing local asset: %s" % rel)
    for path in sorted(DOCS.rglob("*")):
        if not path.is_file() or path == ABOUT:
            continue
        rel = path.relative_to(DOCS).as_posix()
        if rel in src:
            continue
        consumers = OTHER_CONSUMERS.get(rel)
        assert consumers, (
            "orphan asset %s (not referenced by index.html or registered "
            "in OTHER_CONSUMERS); delete it or wire it up" % rel)
        for consumer in consumers:
            text = (ROOT / consumer).read_text(encoding="utf-8")
            assert rel in text, (
                "OTHER_CONSUMERS says %s uses %s, but it does not"
                % (consumer, rel))
    print("assets OK (%d local refs, no orphans)" % len(local))


def check_curated_shots():
    """hdr.png 是用户精修展示图：生成脚本只能校验在位，不能重生成。"""
    gen = (ROOT / "tests" / "gen_about_shots.py").read_text(encoding="utf-8")
    match = re.search(r"CURATED_SHOTS\s*=\s*\(([^)]*)\)", gen)
    assert match, "gen_about_shots.py must declare CURATED_SHOTS"
    assert '"hdr.png"' in match.group(1), (
        "hdr.png must be declared as a curated (never regenerated) shot")
    assert not re.search(r"_grab\([^)]*hdr\.png", gen), (
        "gen_about_shots.py must not grab/regenerate hdr.png")
    assert (DOCS / "about_shots" / "hdr.png").is_file(), (
        "curated hdr.png is missing; restore it from the source image")
    print("curated shots OK (hdr.png kept, not regenerated)")


def check_offline(src):
    assert "<style>" in src, "styles must stay inlined"
    assert "<link" not in src, "no external stylesheets (offline page)"
    assert 'src="http' not in src, "no remote src"
    assert "@import" not in src, "no @import (offline page)"
    assert "url(http" not in src, "no remote url()"
    print("offline OK")


def check_a11y(src):
    """Guard the WCAG fixes so a future rewrite does not drop them."""
    assert 'class="car-toggle"' in src, "carousel needs an explicit pause control"
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in src, (
        "autoplay must respect prefers-reduced-motion in JS")
    assert "aria-pressed" in src and "aria-current" in src, (
        "carousel state needs aria-pressed / aria-current")
    assert 'role="dialog"' in src and 'aria-modal="true"' in src, (
        "donate modal needs dialog semantics")
    assert "document.activeElement" in src and "opener.focus" in src, (
        "donate modal must move focus in and restore it on close")
    assert src.count('class="table-wrap"') == src.count("<table>"), (
        "every table needs an overflow-x wrapper for narrow screens")
    assert "max-width: 640px" in src, "narrow-screen breakpoint missing"
    print("a11y guards OK")


def check_toc_derivation(src):
    """右栏只列当前章节的小节，链接必须由正文 h3[id] 运行时派生。

    防止改回第三份手工清单（曾与左栏目录重复 16 个章节链接并漂移）。
    """
    toc = re.search(r'<nav class="ds-toc-nav"[^>]*>(.*?)</nav>', src, re.S)
    assert toc, "right-hand subsection nav missing"
    assert "href=\"#" not in toc.group(1), (
        "subsection links must not be hardcoded (derive from h3[id] in JS)")
    assert 'querySelectorAll("h3[id]")' in src, (
        "subsection nav must derive from section h3[id]")
    for anchor in ("tocCur", "tocSub", "tocNone"):
        assert 'id="%s"' % anchor in src, "missing TOC node: %s" % anchor
    assert "ds-sec-sub" in src, "narrow-screen in-section subsection bar missing"
    print("toc derivation OK (right nav is runtime-derived, no duplicate list)")


def check_dialog_structure(src):
    """打赏弹窗必须在 </figure> 后、内联脚本前闭合，且全页 div 配平。

    历史缺陷：donate-modal 漏写 </div>，浏览器容错后把两段 <script> 收进了
    role="dialog" aria-modal="true" 容器里；功能看似正常，但语义错误且难以发现。
    """
    opens = len(re.findall(r"<div\b", src))
    closes = src.count("</div>")
    assert opens == closes, (
        "unbalanced div tags (%d open / %d close)" % (opens, closes))
    assert re.search(r"</figure>\s*</div>\s*<script>", src), (
        "donate-modal must close after </figure> and before inline scripts")
    print("dialog structure OK (%d divs balanced, closed before scripts)" % opens)


def main():
    src = _read()
    check_version(src)
    check_css_tokens(src)
    check_svg_hex(src)
    check_anchors(src)
    check_menu_coverage(src)
    check_resources(src)
    check_curated_shots()
    check_offline(src)
    check_a11y(src)
    check_toc_derivation(src)
    check_dialog_structure(src)
    print("about sync lint: all OK")


if __name__ == "__main__":
    main()
