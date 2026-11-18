"""Generate local brand assets for README / docs (offline, no external URLs).

Outputs to ``docs/brand/``:
- ``houtools-banner.png`` — brand header (ink + blueprint grid + orange rule)
- ``badge-houdini.png`` / ``badge-python.png`` / ``badge-pyside6.png`` /
  ``badge-gpl.png`` — local badges (no shields.io)

Run with Houdini's Python (offscreen):
    "C:/.../python313/python.exe" tests/gen_brand_assets.py
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QPA_FONTDIR",
                      os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                   "Fonts"))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python3.13libs"))


def _find_houdini_root() -> Path:
    env = os.environ.get("HFS")
    if env and (Path(env) / "python313").exists():
        return Path(env)
    for base in (Path(r"C:\Program Files\Side Effects Software"),
                 Path(r"D:\Program Files\Side Effects Software")):
        if base.is_dir():
            for candidate in sorted(base.glob("Houdini 22.0*"), reverse=True):
                if (candidate / "python313").exists():
                    return candidate
    raise SystemExit("Houdini 22.0 python313 not found")


HFS = _find_houdini_root()
sys.path.append(str(HFS / "python313" / "lib" / "site-packages-forced"))
os.add_dll_directory(str(HFS / "bin"))

from PySide6 import QtCore, QtGui, QtWidgets, QtSvg  # noqa: E402

OUT = ROOT / "docs" / "brand"
OUT.mkdir(parents=True, exist_ok=True)

INK_1000 = "#05050a"
INK_950 = "#0a0a0f"
INK_900 = "#101017"
INK_850 = "#15151c"
LINE = "#262631"
LINE_STRONG = "#34343f"
TEXT = "#f0f0f3"
TEXT_2 = "#b8b8c2"
TEXT_3 = "#8a8a92"
ACCENT = "#ff5c38"
WARNING = "#f2a33c"
SUCCESS = "#45b06e"
GRID_LINE = "#2e8b57"


def _fonts():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = ROOT / "python3.13libs" / "houtools" / "fonts" / "DingTalk-JinBuTi.ttf"
    fid = QtGui.QFontDatabase.addApplicationFont(str(path))
    brand = QtGui.QFontDatabase.applicationFontFamilies(fid)[0]
    fams = set(QtGui.QFontDatabase.families())
    ui = next((f for f in ("Microsoft YaHei UI", "Microsoft YaHei",
                           "Segoe UI") if f in fams), "Segoe UI")
    return app, brand, ui


def _draw_grid(p, w, h, step=48, alpha=18):
    pen = QtGui.QPen(QtGui.QColor(GRID_LINE))
    pen.setWidth(1)
    pen.setStyle(QtCore.Qt.PenStyle.DashLine)
    pen.setDashPattern([4, 8])
    c = QtGui.QColor(GRID_LINE)
    c.setAlpha(alpha)
    pen.setColor(c)
    p.setPen(pen)
    for x in range(0, w, step):
        p.drawLine(x, 0, x, h)
    for y in range(0, h, step):
        p.drawLine(0, y, w, y)


def _draw_corners(p, w, h, color=ACCENT, r=3):
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor(color))
    for x, y in ((0, 0), (w, 0), (0, h), (w, h)):
        p.drawEllipse(QtCore.QPointF(x, y), r, r)


def banner():
    w, h = 1400, 360
    pm = QtGui.QPixmap(w * 2, h * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(QtGui.QColor(INK_1000))
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
    _draw_grid(p, w, h, alpha=22)

    # 左侧橙色动作条
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor(ACCENT))
    p.drawRect(0, 0, 6, h)

    # 产品图标（复用 tool 品牌矢量）
    icon = QtSvg.QSvgRenderer(
        str(ROOT / "python3.13libs" / "houtools" / "icons"
            / "brand_product.svg"))
    if icon.isValid():
        icon.render(p, QtCore.QRectF(88, 92, 72, 72))

    brand_font = QtGui.QFont(brand_family)
    brand_font.setPixelSize(86)
    brand_font.setWeight(QtGui.QFont.Weight.DemiBold)
    brand_font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 5.0)
    p.setFont(brand_font)
    fm = QtGui.QFontMetrics(brand_font)
    p.setPen(QtGui.QColor(TEXT))
    p.drawText(QtCore.QRectF(176, 66, 900, 130),
               QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "Hou")
    hou_w = fm.horizontalAdvance("Hou") + brand_font.letterSpacing()
    p.setPen(QtGui.QColor(ACCENT))
    p.drawText(QtCore.QRectF(176 + hou_w, 66, 900 - hou_w, 130),
               QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "Tools")

    sub_font = QtGui.QFont(ui_family)
    sub_font.setPixelSize(25)
    p.setFont(sub_font)
    p.setPen(QtGui.QColor(TEXT_2))
    p.drawText(QtCore.QRectF(180, 200, 1000, 50),
               QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
               "Houdini 21 / 22  ·  Python 3.11 / 3.13  ·  Plugin Suite")

    # 右侧版本胶囊
    ver_font = QtGui.QFont("Consolas")
    ver_font.setPixelSize(22)
    p.setFont(ver_font)
    ver_rect = QtCore.QRectF(1160, 84, 160, 46)
    p.setPen(QtGui.QPen(QtGui.QColor(LINE_STRONG), 1))
    p.setBrush(QtGui.QColor(INK_900))
    p.drawRoundedRect(ver_rect, 23, 23)
    p.setPen(QtGui.QColor(TEXT_3))
    p.drawText(ver_rect, QtCore.Qt.AlignCenter, "v1.10.2")

    # 底部细线 + 四角点
    p.setPen(QtGui.QPen(QtGui.QColor(LINE), 1))
    p.drawLine(88, 312, w - 88, 312)
    _draw_corners(p, w, h, color=ACCENT, r=3)
    p.end()

    path = OUT / "houtools-banner.png"
    ok = pm.save(str(path), "PNG")
    print("banner", path, "ok" if ok else "FAIL")
    return ok


def badge(name, label, dot_color):
    w, h = 260, 56
    pm = QtGui.QPixmap(w * 2, h * 2)
    pm.setDevicePixelRatio(2.0)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    rect = QtCore.QRectF(1, 1, w - 2, h - 2)
    p.setPen(QtGui.QPen(QtGui.QColor(LINE), 1))
    p.setBrush(QtGui.QColor(INK_900))
    p.drawRoundedRect(rect, 8, 8)
    # 语义色点
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor(dot_color))
    p.drawEllipse(QtCore.QPointF(24, h / 2), 4, 4)
    f = QtGui.QFont(ui_family)
    f.setPixelSize(19)
    f.setWeight(QtGui.QFont.Weight.DemiBold)
    p.setFont(f)
    p.setPen(QtGui.QColor(TEXT_2))
    p.drawText(QtCore.QRectF(42, 0, w - 58, h),
               QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, label)
    p.end()
    path = OUT / ("badge-%s.png" % name)
    ok = pm.save(str(path), "PNG")
    print("badge", path, "ok" if ok else "FAIL")
    return ok


def wordmark():
    """Transparent two-color wordmark for the About hero/footer.

    Fixed-size image: the About page no longer waits for the woff2 to lay
    out the wordmark, so the version pill never shifts and there is no
    invisible/fallback flash on refresh.
    """
    scale = 3
    font = QtGui.QFont(brand_family)
    font.setPixelSize(120)
    font.setWeight(QtGui.QFont.Weight.DemiBold)
    font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 6.0)
    fm = QtGui.QFontMetrics(font)
    hou_w = fm.horizontalAdvance("Hou") + 6.0
    total_w = hou_w + fm.horizontalAdvance("Tools")
    pad = 8
    logical_w = total_w + pad * 2
    logical_h = fm.height() + pad * 2
    pm = QtGui.QPixmap(int(logical_w * scale), int(logical_h * scale))
    pm.setDevicePixelRatio(scale)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)
    p.setFont(font)
    baseline = pad + fm.ascent()
    p.setPen(QtGui.QColor(TEXT))
    p.drawText(QtCore.QPointF(pad, baseline), "Hou")
    p.setPen(QtGui.QColor(ACCENT))
    p.drawText(QtCore.QPointF(pad + hou_w, baseline), "Tools")
    p.end()
    path = OUT / "houtools-wordmark.png"
    ok = pm.save(str(path), "PNG")
    print("wordmark", path, "ok" if ok else "FAIL",
          "%dx%d" % (pm.width(), pm.height()))
    return ok


def main():
    global brand_family, ui_family
    app, brand_family, ui_family = _fonts()
    results = [
        banner(),
        wordmark(),
        badge("houdini", "Houdini 21 / 22", ACCENT),
        badge("python", "Python 3.11 / 3.13", WARNING),
        badge("pyside6", "PySide6 6.8", SUCCESS),
        badge("gpl", "GPL-3.0", TEXT_3),
    ]
    print("done:", all(results))
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
