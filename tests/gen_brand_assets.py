"""Generate local brand assets for README / docs (offline, no external URLs).

Outputs to ``docs/brand/``:
- ``houtools-wordmark.png`` — transparent two-color wordmark (About page)

``houtools-banner.png``（README 头图）**不再由本脚本生成**：2026-11 起为用户
提供的固定资产（1600×700 主视觉位图，字体已烤进图内），重跑本脚本不会覆盖它；
换图请直接替换该文件并 bump README 引用上的 ``?v=N`` 缓存参数。

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

TEXT = "#f0f0f3"
ACCENT = "#ff5c38"


def _fonts():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    path = ROOT / "python3.13libs" / "houtools" / "fonts" / "DingTalk-JinBuTi.ttf"
    fid = QtGui.QFontDatabase.addApplicationFont(str(path))
    brand = QtGui.QFontDatabase.applicationFontFamilies(fid)[0]
    fams = set(QtGui.QFontDatabase.families())
    ui = next((f for f in ("Microsoft YaHei UI", "Microsoft YaHei",
                           "Segoe UI") if f in fams), "Segoe UI")
    return app, brand, ui


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
        wordmark(),
    ]
    print("done:", all(results))
    if not all(results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
