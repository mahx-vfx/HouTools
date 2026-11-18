"""Package Project —— 收集工程依赖、改成相对路径并打包 zip。

入口：HouTools -> Package Project（主菜单与网络编辑器菜单各一条）。
窗口在 :mod:`houtools.package.window`，扫描在
:mod:`houtools.package.collect`；机制与坑表见 ``dev-notes/package_project.md``。
"""

from houtools.core.log import get_logger
from houtools.ui import window_manager

TOOL_ID = "package_project"

log = get_logger("tools.package_project")


def run():
    """菜单入口：打开打包窗口（window_manager 单例）。"""
    import hou

    from houtools.package.window import PackageProjectWindow

    parent = None
    try:
        parent = hou.qt.mainWindow()
    except Exception as exc:
        log.debug("主窗口不可用: %s", exc)

    window_manager.open_window(
        TOOL_ID, lambda: PackageProjectWindow(parent))
