"""Font role tests: bundled brand font + role fallbacks + stamping.

Run with Houdini's Python (offscreen, no GUI session):

    "C:/.../Houdini 22.0.429/python313/python.exe" tests/test_fonts.py
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

from PySide6 import QtGui, QtWidgets  # noqa: E402

from houtools.ui import fonts  # noqa: E402

FONT_DIR = ROOT / "python3.13libs" / "houtools" / "fonts"


def check_files():
    required = (
        "DingTalk-JinBuTi.ttf",
        "AlimamaShuHeiTi-Bold.ttf",
        "AlimamaFangYuanTiVF-Regular.ttf",
    )
    for name in required:
        path = FONT_DIR / name
        assert path.is_file(), "missing font file: %s" % path
        assert path.stat().st_size > 1024, "suspiciously small: %s" % path
    print("font files OK")


def check_loading(app):
    brand = QtGui.QFontDatabase.addApplicationFont(
        str(FONT_DIR / "DingTalk-JinBuTi.ttf"))
    assert brand >= 0, "brand font failed to load"
    families = QtGui.QFontDatabase.applicationFontFamilies(brand)
    assert families, "brand font has no family"
    assert "DingTalk JinBuTi" in families, families

    assert fonts.brand_family() == "DingTalk JinBuTi", fonts.brand_family()
    # 面板 UI 字体沿用品牌统一前的阿里妈妈数黑体 Bold（功能优先）
    assert fonts.ui_family() == "Alimama ShuHeiTi", fonts.ui_family()
    assert fonts.mono_family() == "Consolas", fonts.mono_family()
    # Recipe 文档阅读沿用方圆体 VF
    assert fonts.doc_family() == "Alimama FangYuanTi VF", fonts.doc_family()
    print("role families:", {
        "brand": fonts.brand_family(),
        "ui": fonts.ui_family(),
        "mono": fonts.mono_family(),
        "doc": fonts.doc_family(),
    })


def check_stamping(app):
    root = QtWidgets.QWidget()
    root.setObjectName("htWindow")
    label = QtWidgets.QLabel("HouTools", root)
    fonts.apply(root)
    assert root.font().family() == fonts.ui_family(), root.font().family()
    assert label.font().family() == fonts.ui_family(), label.font().family()

    fonts.apply_role(label, fonts.ROLE_BRAND)
    assert label.font().family() == fonts.brand_family(), label.font().family()

    # ChildAdded enforcer: dynamically created child inherits nearest role.
    dynamic = QtWidgets.QLabel("dynamic", root)
    app.processEvents()
    assert dynamic.font().family() == fonts.ui_family(), \
        dynamic.font().family()

    mono = QtWidgets.QLabel("C:/cache/frames.$F4.exr", root)
    fonts.apply_role(mono, fonts.ROLE_MONO)
    assert mono.font().family() == fonts.mono_family(), mono.font().family()

    brand_font = fonts.role_font(fonts.ROLE_BRAND)
    assert brand_font.weight() == QtGui.QFont.Weight.DemiBold
    spaced = fonts.set_letter_spacing(QtGui.QFont(brand_font), 0.06, 13)
    assert spaced.letterSpacing() > 0
    print("font stamping/enforcer OK")


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    check_files()
    check_loading(app)
    check_stamping(app)
    print("font tests: all OK")


if __name__ == "__main__":
    main()
