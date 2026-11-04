"""工具窗口通用 SVG 图标（QSvgRenderer 离屏渲染，模块级缓存）
=========================================
Automation 面板的 jump/folder 图标走自己的私有缓存（automation/window.py
的 _SLOT_ICON_CACHE——100 槽配置每次装载复用一份渲染结果，详见该处注释）；
本模块把同一手法提炼给其他工具窗口用（Video to Sequence 的文件夹/跳转/
节点选择按钮）。

QSvgRenderer 离屏渲染成 16/32 两档位图组装 QIcon：不依赖 Qt 的 svg icon
插件，32 档供 2x DPR 取用。素材一律取自 ``houtools/icons/``（官方
``$HFS/houdini/config/Icons/icons.zip`` 的提取件，文件名不含空格）。
QtSvg 缺失或 SVG 损坏返回 None，调用点回退文字按钮。
"""

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap

try:
    from PySide6 import QtSvg
except ImportError:  # 正常环境都有；缺失时按钮回退文字
    QtSvg = None

from houtools.core.log import get_logger

logger = get_logger("ui.icons")

_ICONS_DIR = Path(__file__).resolve().parent.parent / "icons"

# 纯渲染缓存，Reload 后重建一次即可，无需 NameError 守卫（automation 同款注释）
_SVG_ICON_CACHE: dict = {}

# 渲染失败占位：同样进缓存，防坏文件/缺依赖时每次调用重复告警
_MISS = object()


def svg_icon(rel_name, sizes=(16, 32)):
    """渲染 ``houtools/icons/<rel_name>`` 为多档位 QIcon（缓存；失败返回 None）。"""
    icon = _SVG_ICON_CACHE.get(rel_name)
    if icon is not None:
        return icon if icon is not _MISS else None
    if QtSvg is None:
        _SVG_ICON_CACHE[rel_name] = _MISS
        return None
    svg_path = _ICONS_DIR / rel_name
    renderer = QtSvg.QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        logger.warning("%s 无法渲染: %s", rel_name, svg_path)
        _SVG_ICON_CACHE[rel_name] = _MISS
        return None
    icon = QIcon()
    for size in sizes:
        pm = QPixmap(size, size)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()
        icon.addPixmap(pm)
    _SVG_ICON_CACHE[rel_name] = icon
    return icon
