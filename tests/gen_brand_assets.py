"""Generate local brand assets for README / docs (offline, no external URLs).

Outputs to ``docs/brand/``:
- ``houtools-banner.png`` — README brand header (ink + blueprint grid +
  orange rule + two-color wordmark; no icon, no version pill)
- ``houtools-wordmark.png`` — transparent two-color wordmark (About page)

README 的环境徽章走 shields.io（仓库最初的文字+徽章形态），不在本地生成。

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

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

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


def _draw_network_motif(p):
    """右侧蓝图网络母题：抽象节点连线，呼应网络编辑器。

    纯图形装饰——无文字、无版本号，发版不需要重画；绿色走蓝图网格色，
    其中一个节点用品牌橙当"动作节点"，呼应单屏 ≤~10% 的动作色占比。
    """
    line = QtGui.QColor(GRID_LINE); line.setAlpha(150)
    dot = QtGui.QColor(SUCCESS); dot.setAlpha(180)
    ink = QtGui.QColor(INK_900)
    accent = QtGui.QColor(ACCENT)
    accent_soft = QtGui.QColor(ACCENT); accent_soft.setAlpha(34)
    accent_bar = QtGui.QColor(ACCENT); accent_bar.setAlpha(200)
    bar = QtGui.QColor(TEXT_2); bar.setAlpha(120)

    def node(x, y, w, h, accent_node=False):
        p.setPen(QtGui.QPen(accent if accent_node else line,
                            1.5 if accent_node else 1.0))
        p.setBrush(accent_soft if accent_node else ink)
        p.drawRoundedRect(QtCore.QRectF(x, y, w, h), 7, 7)
        # 节点内部：语义点 + 名称条（抽象参数行，不写字）
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(accent if accent_node else dot)
        p.drawEllipse(QtCore.QPointF(x + 13, y + h / 2), 3.2, 3.2)
        p.setBrush(accent_bar if accent_node else bar)
        p.drawRoundedRect(QtCore.QRectF(x + 26, y + h / 2 - 2, w - 48, 4),
                          2, 2)

    def wire(points, color, width):
        pen = QtGui.QPen(color, width)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        path = QtGui.QPainterPath(QtCore.QPointF(*points[0]))
        for x, y in points[1:]:
            path.lineTo(x, y)
        p.drawPath(path)

    def entry(x, y, color):
        """Houdini 节点左缘的输入三角。"""
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(QtGui.QPolygonF([
            QtCore.QPointF(x - 7, y - 4.5),
            QtCore.QPointF(x - 7, y + 4.5),
            QtCore.QPointF(x - 1, y)]))

    # 连线先画（端点落在节点边缘上，节点后画盖住接缝）
    wire([(1110, 109), (1143, 109), (1143, 159), (1176, 159)], line, 1.4)
    wire([(1036, 130), (1036, 214)], line, 1.4)
    wire([(1174, 235), (1207, 235), (1207, 250), (1240, 250)],
         accent, 1.6)

    node(962, 88, 148, 42)
    node(1176, 138, 136, 42)
    node(1006, 214, 168, 42, accent_node=True)
    node(1240, 232, 72, 36)

    entry(1176, 159, line)
    entry(1240, 250, accent)


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

    # 右侧蓝图网络母题（装饰层，先画在字标底下）
    _draw_network_motif(p)

    # 两色字标（无图标、无版本胶囊——版本号走 README 文字，避免每次发版重画头图）
    brand_font = QtGui.QFont(brand_family)
    brand_font.setPixelSize(86)
    brand_font.setWeight(QtGui.QFont.Weight.DemiBold)
    brand_font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing, 5.0)
    p.setFont(brand_font)
    fm = QtGui.QFontMetrics(brand_font)
    p.setPen(QtGui.QColor(TEXT))
    p.drawText(QtCore.QRectF(88, 66, 1000, 130),
               QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "Hou")
    hou_w = fm.horizontalAdvance("Hou") + brand_font.letterSpacing()
    p.setPen(QtGui.QColor(ACCENT))
    p.drawText(QtCore.QRectF(88 + hou_w, 66, 1000 - hou_w, 130),
               QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, "Tools")

    sub_font = QtGui.QFont(ui_family)
    sub_font.setPixelSize(25)
    p.setFont(sub_font)
    p.setPen(QtGui.QColor(TEXT_2))
    p.drawText(QtCore.QRectF(92, 200, 1000, 50),
               QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
               "Houdini 21 / 22  ·  Python 3.11 / 3.13  ·  Plugin Suite")

    # 底部细线 + 四角点
    p.setPen(QtGui.QPen(QtGui.QColor(LINE), 1))
    p.drawLine(88, 312, w - 88, 312)
    _draw_corners(p, w, h, color=ACCENT, r=3)
    p.end()

    path = OUT / "houtools-banner.png"
    ok = pm.save(str(path), "PNG")
    print("banner", path, "ok" if ok else "FAIL")
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
    ]
    print("done:", all(results))
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
