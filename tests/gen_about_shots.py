"""Generate real-window screenshots for the About page carousel (offline).

Runs headless (QT_QPA_PLATFORM=offscreen) with Houdini's bundled Python.
Instantiates the generated tool windows with stubbed demo data and grabs
each to docs/about_shots/*.png. Never touches the real settings/ (all
stores are patched to temp dirs).

docs/about_shots/hdr.png is a curated, user-supplied screenshot: it is
verified but NEVER regenerated here (see CURATED_SHOTS). Re-run whenever
one of the generated tool windows' UIs changes.

Usage:
    "C:/.../python313/python.exe" tests/gen_about_shots.py
"""

import atexit
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 平台用 QBasicFontDatabase，只认 QT_QPA_FONTDIR 指向的字体目录，
# 缺省路径不存在时私有字体缺字形（如 •）无系统回退、渲染成豆腐块——指到
# 系统字体目录，回退行为贴近真实会话（真实会话同样靠系统字体回退补 •）
os.environ.setdefault("QT_QPA_FONTDIR",
                      os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                                   "Fonts"))

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python3.13libs"))

# settings 目录隔离：必须在首个 houtools import 之前设置（同 smoke_test
# 的约定，constants 在 import 期定值）。此前只有窗口级 _SETTINGS 打了桩，
# 模块级 JsonStore（如 HDR 的 _SCAN_INDEX）会写进真实配置目录——演示库
# 快照覆盖真实 hdr_scan_index.json 后下次打开要全量重扫；异常退出还会
# 留下 _about_shots_*.json 残件
_TMP_SETTINGS = Path(tempfile.mkdtemp(prefix="about_shots_cfg."))
os.environ["HOUTOOLS_SETTINGS_DIR"] = str(_TMP_SETTINGS)
atexit.register(lambda: shutil.rmtree(_TMP_SETTINGS, ignore_errors=True))


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


HOUDINI_ROOT = _find_houdini_root()
_pyside_dir = HOUDINI_ROOT / "python313" / "lib" / "site-packages-forced"
if _pyside_dir.exists():
    sys.path.append(str(_pyside_dir))
_qt_bin = HOUDINI_ROOT / "bin"
if _qt_bin.exists():
    os.add_dll_directory(str(_qt_bin))

OUT_DIR = ROOT / "docs" / "about_shots"
OUT_DIR.mkdir(parents=True, exist_ok=True)
SHOT_W, SHOT_H = 1080, 620

# 用户提供的精修展示图：只校验在位，不参与离屏重生成。
# 删掉或改坏时脚本会失败，提醒从源图/版本库恢复，绝不回退到合成截图。
CURATED_SHOTS = ("hdr.png",)

from PySide6 import QtGui, QtWidgets  # noqa: E402
from unittest.mock import patch  # noqa: E402

import houtools.core.settings  # noqa: E402


def _settle(win, ms=350):
    """Pump the event loop so the window lays out and paints fully."""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        QtWidgets.QApplication.processEvents()
        time.sleep(0.02)
    QtWidgets.QApplication.processEvents()


def _grab(win, name):
    _settle(win)
    pm = win.grab()
    out = OUT_DIR / name
    ok = pm.save(str(out), "PNG")
    print("shot %-14s %sx%s -> %s (%s)" % (
        name, pm.width(), pm.height(), out, "ok" if ok else "FAIL"))
    return ok


# -------------------------------------------------------------------
# 1) Automation — temp config with three demo tasks
# -------------------------------------------------------------------
def shot_automation(tmp: Path):
    from houtools.automation import data_manager as dm
    from houtools.automation.window import AutomationWindow

    cfg_dir = tmp / "HouTools_cfg" / "Automation_json"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    tasks = {
        "tasks": [
            {"type": "BUTTON_CLICK",
             "params": {"node_path": "/obj/cache_ropnet1",
                        "parm_name": "execute"},
             "enabled": True},
            {"type": "FLIPBOOK",
             "params": {"start_frame": "1001", "end_frame": "1080",
                        "output_path": "$HIP/flipbook/$HIPNAME.$F4.jpg",
                        "save_to_disk": True},
             "enabled": True},
            {"type": "OPEN_DW", "params": {}, "enabled": False},
        ]
    }
    (cfg_dir / "Automation.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")

    # 默认配置名 = 计算机名（2026-11 按机器隔离），截图环境固化为
    # "Automation" 才能对上演示数据文件名
    with patch.object(dm.AutomationDataManager, "_hip_base",
                      classmethod(lambda cls: str(tmp))), \
         patch.object(dm.AutomationDataManager, "config_hostname",
                      classmethod(lambda cls: "Automation")):
        win = AutomationWindow()
        win.resize(SHOT_W, SHOT_H)
        win.show()
        _settle(win, 500)
        ok = _grab(win, "automation.png")
        win.deleteLater()
    return ok


# -------------------------------------------------------------------
# 2) Video to Sequence — plain instantiation
# -------------------------------------------------------------------
def shot_videoseq(tmp: Path):
    from houtools.videoseq.window import _VideoToSequenceWindow

    win = _VideoToSequenceWindow()
    win.resize(SHOT_W, SHOT_H)
    win.show()
    _settle(win, 400)
    ok = _grab(win, "videoseq.png")
    win.deleteLater()
    return ok


# -------------------------------------------------------------------
# 3) Recipe Library — stubbed store, gradient thumbnails, metadata
# -------------------------------------------------------------------
def shot_recipe(tmp: Path):
    from houtools.core.settings import JsonStore
    from houtools.recipelib import metadata as rl_meta
    from houtools.recipelib import browser as rl_browser
    from houtools.recipelib import codeblock as rl_code
    from houtools.recipelib import store as rl_store

    store = JsonStore("_about_shots_rl.json", defaults=dict(rl_meta._DEFAULTS))
    thumbs = tmp / "rl_thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    codelib = tmp / "vex_python_lib"

    infos = [
        rl_store.RecipeInfo(name="mahx::karma_pyro_setup",
                            label="Karma Pyro Setup", category="tool",
                            submenu="HouTools", net_category="Sop"),
        rl_store.RecipeInfo(name="mahx::dome_light_rig",
                            label="Dome Light Rig", category="tool",
                            submenu="HouTools", net_category="Lop"),
        rl_store.RecipeInfo(name="mahx::flipbook_cam",
                            label="Flipbook Cam", category="tool",
                            submenu="", net_category="Sop"),
        rl_store.RecipeInfo(name="mahx::vend_frost_material",
                            label="Frost Material", category="node",
                            submenu="Materials", net_category="Sop"),
        rl_store.RecipeInfo(name="mahx::usd_layer_stack",
                            label="USD Layer Stack", category="tool",
                            submenu="USD", net_category="Lop"),
        rl_store.RecipeInfo(name="mahx::cache_wedge",
                            label="Cache Wedge", category="tool",
                            submenu="HouTools", net_category="Sop"),
    ]
    palette = ["#ff5c38", "#e2571b", "#f2a33c", "#45b06e",
               "#2e8b57", "#d16a7a"]

    with patch.object(rl_meta, "_SETTINGS", store), \
         patch.object(rl_meta, "THUMBS_DIR", thumbs), \
         patch.object(rl_meta, "get_lib_dirs",
                      return_value=["X:/demo_lib"]), \
         patch.object(rl_browser.store, "list_recipes",
                      side_effect=lambda lib_dirs=None:
                          list(infos) if lib_dirs else []), \
         patch.object(rl_code, "CODELIB_ROOT", codelib):
        for info, color in zip(infos, palette):
            pm = QtGui.QPixmap(256, 169)
            pm.fill(QtGui.QColor(color))
            painter = QtGui.QPainter(pm)
            painter.setPen(QtGui.QPen(QtGui.QColor(0, 0, 0, 70)))
            for i in range(6):
                painter.setBrush(QtGui.QBrush(QtGui.QColor(
                    255, 255, 255, 18 + i * 6)))
                painter.drawEllipse(i * 40 - 20, 40 + i * 12, 120, 90)
            painter.end()
            rl_meta.set_thumb_from_file(info.name, _save_tmp_png(tmp, pm))
            rl_meta.set_color(info.name, color)
            if info.name.endswith(("pyro_setup", "frost_material")):
                rl_meta.set_favorite(info.name, True)
        rl_meta.set_display_name(infos[1].name, "穹顶灯光装备")
        rl_meta.set_tags(infos[0].name, ["pyro", "常用"])
        rl_meta.set_tags(infos[3].name, ["材质", "预览"])

        # 代码块打桩:两个语言各一块(卡片缩略图 = 代码高亮渲染),
        # 展示侧栏「代码块」段与代码卡片
        rl_code.save_code("packed_normal", "vex", """\
// 把 @N 转成 packed 朝向(@orient)
vector n = normalize(v@N);
vector up = {0, 1, 0};
if (abs(dot(n, up)) > 0.99) up = {1, 0, 0};
vector axis = cross(up, n);
p@orient = quaternion(axis, n);
""")
        rl_code.save_code("batch_rename", "python", """\
# 按前缀批量重命名选中节点
import hou

prefix = "sim_"
for node in hou.selectedNodes():
    node.setName(prefix + node.name())
print("renamed %d nodes" % len(hou.selectedNodes()))
""")
        rl_meta.set_display_name("batch_rename", "批量重命名")
        rl_meta.set_tags("packed_normal", ["vex", "常用"])
        rl_meta.set_tags("batch_rename", ["python"])
        rl_meta.set_color("packed_normal", "#45b06e")

        win = rl_browser._RecipeLibraryWindow()
        win.resize(SHOT_W, SHOT_H)
        win.show()
        win.reload()
        # 选中第一张卡：截图同时展示预览/元信息/标签面板
        if win.list.count():
            win.list.setCurrentRow(0)
        _settle(win, 500)
        ok = _grab(win, "recipe.png")
        win.deleteLater()
    store.path.unlink(missing_ok=True)
    return ok


# -------------------------------------------------------------------
# 4) Package Project — 6 列预览表里塞一组演示引用（真实窗口 UI）
# -------------------------------------------------------------------
def shot_package(tmp: Path):
    from houtools.package import collect as pkg_collect
    from houtools.package import paths as pkg_paths
    from houtools.package.window import PackageProjectWindow

    def ref(kind, node, parm, raw, files, in_hip=False, token="",
            rewrite_dir=False):
        return pkg_collect.Ref(
            kind=kind, node_path=node, parm_name=parm, raw=raw,
            abs_path=raw, files=files, in_hip=in_hip, token=token,
            rewrite_dir=rewrite_dir)

    frames = ["D:/shots/SA01/geo/cache.%04d.bgeo.sc" % n
              for n in range(1001, 1025)]
    udims = ["D:/proj/tex/diffuse.%d.exr" % n for n in range(1001, 1004)]
    refs = [
        ref("input", "/obj/geo1/file1", "file",
            "$HIP/geo/hero_base.bgeo.sc",
            ["D:/shots/SA01/geo/hero_base.bgeo.sc"], in_hip=True),
        ref("input", "/obj/geo1/shop_material1", "texture",
            "D:/proj/tex/albedo.exr", ["D:/proj/tex/albedo.exr"]),
        ref("output", "/out/karma1", "picture",
            "$HIP/render/shot.$F4.exr",
            ["D:/shots/SA01/render/shot.%04d.exr" % n
             for n in range(1001, 1004)],
            in_hip=True, token="$F4"),
        ref("output", "/obj/cache1", "basedir", "$HIP/geo/", frames,
            in_hip=True, token="$F4", rewrite_dir=True),
        ref("input", "/obj/geo1/principled1", "file",
            "D:/proj/tex/diffuse.<UDIM>.exr", udims,
            token=pkg_paths.UDIM_TOKEN),
        ref("input", "/obj/geo1/missing_tex", "file",
            "D:/proj/tex/missing_normal.exr", []),
    ]
    targets = [
        "$HIP/geo/hero_base.bgeo.sc",
        "$HIP/tex/D_proj_tex/albedo.exr",
        "$HIP/render/shot.$F4.exr",
        "$HIP/geo/",
        "$HIP/tex/D_proj_tex/diffuse.<UDIM>.exr",
        "$HIP/tex/D_proj_tex/missing_normal.exr",
    ]

    win = PackageProjectWindow(None)
    # 无 hou 会话：setText 会触发 _on_targets_changed → _base_dir()
    # → import hou 失败，改名期间先屏蔽信号（演示数据随后直接灌入表格）
    win._pkg_edit.blockSignals(True)
    win._pkg_edit.setText("shot_v001-pack")
    win._pkg_edit.blockSignals(False)
    win._refs = refs
    win._targets = targets
    win._checked = set(range(len(refs) - 1))   # 缺失行不勾选
    win._fill_table()
    win._set_status("扫描完成", ok=True)
    win._set_hint("共 6 个引用 · 选中 5 · 待复制 31 个文件（128.4 MB）")
    win._apply_btn.setEnabled(True)
    win._log("[信息] 扫描完成：6 个引用，1 个缺失（打包时跳过）")
    win._log("[信息] 按文件类型归入 geo / tex / render，帧模板 $F4 原样保留")
    win.resize(1080, 680)
    win.show()
    _settle(win, 500)
    ok = _grab(win, "package.png")
    win.deleteLater()
    return ok


def _save_tmp_png(tmp: Path, pm: QtGui.QPixmap) -> str:
    p = tmp / ("thumb_%d.png" % int(time.time() * 1000))
    pm.save(str(p), "PNG")
    return str(p)


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    # 离屏环境没有 Houdini 的全局暗色样式,Qt 默认调色板是浅色的——
    # 未显式铺底的窗口(如 Automation 主背景)会发白,这里模拟品牌暗色
    # 主题（墨黑 + 品牌橙,与 ui/theme.py 同一套 token 色值）
    pal = app.palette()
    pal.setColor(QtGui.QPalette.Window, QtGui.QColor("#0a0a0f"))
    pal.setColor(QtGui.QPalette.WindowText, QtGui.QColor("#f0f0f3"))
    pal.setColor(QtGui.QPalette.Base, QtGui.QColor("#101017"))
    pal.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#15151c"))
    pal.setColor(QtGui.QPalette.Text, QtGui.QColor("#f0f0f3"))
    pal.setColor(QtGui.QPalette.Button, QtGui.QColor("#15151c"))
    pal.setColor(QtGui.QPalette.ButtonText, QtGui.QColor("#f0f0f3"))
    pal.setColor(QtGui.QPalette.PlaceholderText, QtGui.QColor("#5c5c66"))
    pal.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#ff5c38"))
    pal.setColor(QtGui.QPalette.HighlightedText, QtGui.QColor("#05050a"))
    app.setPalette(pal)
    with tempfile.TemporaryDirectory(prefix="about_shots_") as td:
        tmp = Path(td)
        results = [
            shot_automation(tmp),
            shot_videoseq(tmp),
            shot_recipe(tmp),
            shot_package(tmp),
        ]
    # 精修图只读校验：缺失即失败，绝不用合成图兜底覆盖。
    for name in CURATED_SHOTS:
        path = OUT_DIR / name
        kept = path.is_file() and path.stat().st_size > 0
        print("shot %-14s kept (not regenerated): %s" % (
            name, "ok" if kept else "MISS"))
        results.append(kept)
    print("done:", ["ok" if r else "MISS" for r in results])
    # 截图缺失/保存失败必须让自动化感知：此前正常返回退出码 0，发布流程
    # 会拿着旧截图或缺图继续
    if not all(results):
        raise SystemExit("about shots incomplete: %d/%d ok" % (
            sum(1 for r in results if r), len(results)))


if __name__ == "__main__":
    main()
