"""侧栏行绘制共享组件（Recipe Library / HDR Library 分类板块同款）。

行结构：行首图标 + 名称 + 右对齐计数；选中行蓝底圆角高亮；树形视图的
段头行带折叠箭头（收拢态整行调暗）。图标默认 QPainter 手绘的扁平线稿
（grid/star/folder/tag/level/sliders/code），toolbox 为矢量文件渲染
（icons/toolbox.svg，QSvgRenderer，同 favorite_badge 先例），按
(种类, 尺寸, DPR, tint) 缓存。

数据协议（条目经 ItemRole 携带，QListWidget / QTreeWidget 通用）：
- SIDEBAR_HEADER_ROLE（bool）：段头行（画折叠箭头；不可选中由条目 flags 保证）
- SIDEBAR_COUNT_ROLE（int）：行尾计数
- SIDEBAR_ICON_ROLE（str）：行首图标种类
- SIDEBAR_COLOR_ROLE（str）：行着色 "#rrggbb"（可选，标签颜色等场景；
  行文字用可读化后的该色，图标线稿整体着色）
"""

import math
import re
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.log import get_logger

log = get_logger("ui.sidebar")

# ItemRole 协议（Qt.UserRole 之上，QListWidget / QTreeWidget 通用）
SIDEBAR_HEADER_ROLE = QtCore.Qt.UserRole + 1   # True=段头行
SIDEBAR_COUNT_ROLE = QtCore.Qt.UserRole + 2    # 行尾计数
SIDEBAR_ICON_ROLE = QtCore.Qt.UserRole + 3     # 行首图标种类
SIDEBAR_COLOR_ROLE = QtCore.Qt.UserRole + 4    # 行着色 "#rrggbb"（可选）

_FAV_SVG_PATH = (Path(__file__).resolve().parent.parent / "icons"
                 / "favorite_badge.svg")
_FAV_SVG_RENDERER = False   # False=未初始化；None=不可用
_SIDEBAR_ICON_CACHE = {}    # (kind, size, dpr, tint) -> QPixmap

# 工具箱图标（用户矢量图定稿 D1 配色，去 iconfont 装饰圆）；渲染器按
# tint 缓存（tint 语义 = 全部 fill 替换成单色，开关关闭态整体调暗）
_TOOLBOX_SVG_PATH = (Path(__file__).resolve().parent.parent / "icons"
                     / "toolbox.svg")
_TOOLBOX_SVG_TEXT = None        # False=不可用；str=文件文本
_TOOLBOX_SVG_RENDERERS = {}     # tint("")=本色 -> QSvgRenderer
# 内容实际占 1024 画布的 x 78.9..926.3 / y 126.2..886.2（四边留白约
# 13%），直接整画布渲染进 14px 格子只有 ~11.6px——viewBox 裁到内容
# 外扩 6 单位，工具箱铺满格子（与线稿图标的视觉尺寸拉平）
_TOOLBOX_VIEWBOX = QtCore.QRectF(72.0, 120.0, 861.0, 772.0)
# 工具箱是实心填充图形、细节碎，同 px 下比线稿显小：行/开关渲染再
# 放大 2px（2026-10 用户反馈"图标小了看不清"）
TOOLBOX_ICON_BOOST = 2


def _toolbox_svg_text():
    """工具箱 SVG 文本；文件缺失/读失败返回 False（降级 folder 线稿）。"""
    global _TOOLBOX_SVG_TEXT
    if _TOOLBOX_SVG_TEXT is None:
        try:
            text = _TOOLBOX_SVG_PATH.read_text(encoding="utf-8")
            _TOOLBOX_SVG_TEXT = text if text.strip() else False
        except OSError as exc:
            log.warning("toolbox svg unreadable: %s", exc)
            _TOOLBOX_SVG_TEXT = False
    return _TOOLBOX_SVG_TEXT


def _toolbox_renderer(tint):
    """tint 键控的 QSvgRenderer；QtSvg/文件不可用返回 None。"""
    try:
        from PySide6 import QtSvg
    except ImportError:
        log.warning("QtSvg unavailable, toolbox icon falls back to folder")
        return None
    key = tint or ""
    renderer = _TOOLBOX_SVG_RENDERERS.get(key)
    if renderer is None:
        text = _toolbox_svg_text()
        if not text:
            return None
        if tint:
            text = re.sub(r'fill="#[0-9A-Fa-f]{6}"', 'fill="%s"' % tint,
                          text)
        candidate = QtSvg.QSvgRenderer(
            QtCore.QByteArray(text.encode("utf-8")))
        if not candidate.isValid():
            log.warning("toolbox svg invalid: %s", _TOOLBOX_SVG_PATH)
            return None
        candidate.setViewBox(_TOOLBOX_VIEWBOX)   # 裁掉画布留白
        _TOOLBOX_SVG_RENDERERS[key] = renderer = candidate
    return renderer


def _draw_folder_icon(p, color):
    """folder 线稿（子菜单分类行；toolbox 的降级 fallback 同用）。"""
    path = QtGui.QPainterPath()
    path.moveTo(1.5, 11.0)
    path.lineTo(1.5, 4.2)
    path.quadTo(1.5, 3.2, 2.5, 3.2)
    path.lineTo(4.8, 3.2)
    path.quadTo(5.7, 3.2, 6.2, 3.9)
    path.lineTo(7.1, 5.0)
    path.lineTo(11.5, 5.0)
    path.quadTo(12.5, 5.0, 12.5, 6.0)
    path.lineTo(12.5, 11.0)
    path.quadTo(12.5, 12.0, 11.5, 12.0)
    path.lineTo(2.5, 12.0)
    path.quadTo(1.5, 12.0, 1.5, 11.0)
    pen = QtGui.QPen(QtGui.QColor(color), 1.2)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    p.strokePath(path, pen)


def readable_on_dark(color):
    """深色底上的可读色：颜色过暗时提亮（侧栏标签行/卡片标签行共用）。"""
    c = QtGui.QColor(color)
    if c.lightness() < 90:
        c = c.lighter(180)
    return c


def _favorite_svg_renderer():
    """收藏星图标 SVG 渲染器（QtSvg 缺失或文件损坏时返回 None）。"""
    global _FAV_SVG_RENDERER
    if _FAV_SVG_RENDERER is False:
        renderer = None
        try:
            from PySide6 import QtSvg
            if _FAV_SVG_PATH.is_file():
                candidate = QtSvg.QSvgRenderer(str(_FAV_SVG_PATH))
                if candidate.isValid():
                    renderer = candidate
                else:
                    log.warning("favorite badge svg invalid: %s",
                                _FAV_SVG_PATH)
            else:
                log.warning("favorite badge svg missing: %s", _FAV_SVG_PATH)
        except ImportError:
            log.warning("QtSvg unavailable, sidebar star falls back to draw")
        _FAV_SVG_RENDERER = renderer
    return _FAV_SVG_RENDERER


def sidebar_icon_pixmap(kind, size, dpr, tint=None):
    """侧栏行首图标（扁平线稿风，参考外部蓝图管理面板）：grid/star/folder/tag/level。

    QPainter 手绘而非 SVG 文件：颜色随主题写死在函数里、不新增资源文件；
    统一在 14×14 逻辑坐标绘制后缩放，按 (种类, 尺寸, DPR, tint) 缓存保证
    高分屏不糊。tint 非空时线稿整体改用该色（标签色等场景），None 用
    各 kind 的默认色。
    """
    key = (kind, size, round(dpr, 2), tint)
    pm = _SIDEBAR_ICON_CACHE.get(key)
    if pm is not None:
        return pm
    pm = QtGui.QPixmap(max(1, int(size * dpr)), max(1, int(size * dpr)))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QtCore.Qt.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.Antialiasing, True)
    p.scale(size / 14.0, size / 14.0)
    color = {"grid": "#b4b4bc", "star": "#d9a544",
             "folder": "#a4a4ac", "tag": "#5b9bd5",
             "level": "#9b8cd9", "code": "#5aa885"}.get(kind, "#a4a4ac")
    if tint:
        color = tint
    if kind == "grid":
        pen = QtGui.QPen(QtGui.QColor(color), 1.2)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        for gx in (1.0, 8.0):
            for gy in (1.0, 8.0):
                p.drawRoundedRect(QtCore.QRectF(gx, gy, 5.0, 5.0), 1.2, 1.2)
    elif kind == "folder":
        _draw_folder_icon(p, color)
    elif kind == "toolbox":
        # 工具箱矢量图（QSvgRenderer 直渲染，随 viewBox 缩放无损）；
        # QtSvg/文件缺失时降级 folder 线稿，不空手
        renderer = _toolbox_renderer(tint)
        if renderer is not None:
            renderer.render(p, QtCore.QRectF(0, 0, 14, 14))
        else:
            _draw_folder_icon(p, color)
    elif kind == "sliders":
        # 参数滑块：两行滑轨，左端实心方点 + 滑轨 + 错位的空心方滑块
        # （节点参数语义，参考用户提供的参考图）
        pen = QtGui.QPen(QtGui.QColor(color), 1.2)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        for yy, knob_x in ((4.5, 8.0), (10.0, 5.0)):
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(color))
            p.drawRect(QtCore.QRectF(1.5, yy - 1.25, 2.5, 2.5))   # 左端实心点
            p.setPen(pen)
            p.setBrush(QtCore.Qt.NoBrush)
            p.drawLine(QtCore.QPointF(4.5, yy), QtCore.QPointF(12.0, yy))
            p.drawRect(QtCore.QRectF(knob_x, yy - 1.75, 3.5, 3.5))  # 空心滑块
    elif kind == "tag":
        path = QtGui.QPainterPath()
        path.moveTo(3.0, 2.5)
        path.lineTo(7.0, 2.5)
        path.quadTo(7.6, 2.5, 8.0, 2.9)
        path.lineTo(12.1, 6.3)
        path.quadTo(12.5, 7.0, 12.1, 7.7)
        path.lineTo(8.0, 11.1)
        path.quadTo(7.6, 11.5, 7.0, 11.5)
        path.lineTo(3.0, 11.5)
        path.quadTo(2.0, 11.5, 2.0, 10.5)
        path.lineTo(2.0, 3.5)
        path.quadTo(2.0, 2.5, 3.0, 2.5)
        pen = QtGui.QPen(QtGui.QColor(color), 1.2)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.strokePath(path, pen)
        p.drawEllipse(QtCore.QRectF(4.0, 5.9, 2.2, 2.2))
    elif kind == "level":
        # 迷你节点图：两个圆角方节点 + 折线连线（网络层级语义）
        pen = QtGui.QPen(QtGui.QColor(color), 1.2)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawRoundedRect(QtCore.QRectF(1.0, 8.0, 5.0, 5.0), 1.2, 1.2)
        p.drawRoundedRect(QtCore.QRectF(8.0, 1.0, 5.0, 5.0), 1.2, 1.2)
        path = QtGui.QPainterPath()
        path.moveTo(6.0, 10.5)
        path.lineTo(10.5, 10.5)
        path.lineTo(10.5, 6.0)
        p.strokePath(path, pen)
    elif kind == "code":
        # 尖括号斜杠 </>（代码语义）
        pen = QtGui.QPen(QtGui.QColor(color), 1.2)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(QtCore.Qt.NoBrush)
        p.drawLine(QtCore.QPointF(4.8, 3.6), QtCore.QPointF(1.8, 7.0))
        p.drawLine(QtCore.QPointF(1.8, 7.0), QtCore.QPointF(4.8, 10.4))
        p.drawLine(QtCore.QPointF(9.2, 3.6), QtCore.QPointF(12.2, 7.0))
        p.drawLine(QtCore.QPointF(12.2, 7.0), QtCore.QPointF(9.2, 10.4))
        p.drawLine(QtCore.QPointF(8.0, 2.8), QtCore.QPointF(6.0, 11.2))
    elif kind == "star":
        # 与卡片收藏角标同一素材（favorite_badge.svg）；QtSvg 缺失或
        # 文件损坏时回退 QPainter 手绘星
        renderer = _favorite_svg_renderer()
        if renderer is not None:
            renderer.render(p, QtCore.QRectF(0, 0, 14, 14))
        else:
            poly = QtGui.QPolygonF()
            for i in range(10):
                ang = -math.pi / 2 + i * math.pi / 5
                rad = 5.6 if i % 2 == 0 else 2.3
                poly.append(QtCore.QPointF(7.0 + rad * math.cos(ang),
                                           7.0 + rad * math.sin(ang)))
            p.setPen(QtCore.Qt.NoPen)
            p.setBrush(QtGui.QColor(color))
            p.drawPolygon(poly)
    p.end()
    _SIDEBAR_ICON_CACHE[key] = pm
    return pm


def draw_chevron(painter, cx, cy, expanded, color):
    """段头折叠箭头（展开▼ / 折叠▶），圆帽细线。"""
    pen = QtGui.QPen(QtGui.QColor(color), 1.4)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    path = QtGui.QPainterPath()
    if expanded:
        path.moveTo(cx - 2.7, cy - 1.7)
        path.lineTo(cx, cy + 1.7)
        path.lineTo(cx + 2.7, cy - 1.7)
    else:
        path.moveTo(cx - 1.7, cy - 2.7)
        path.lineTo(cx + 1.7, cy)
        path.lineTo(cx - 1.7, cy + 2.7)
    painter.strokePath(path, pen)


class SidebarDelegate(QtWidgets.QStyledItemDelegate):
    """侧栏行全自绘（不调父类 paint）：普通行画种类图标 + 名称 + 右对齐
    计数；树形视图的段头行画折叠箭头（收拢态整行调暗）；选中行画蓝底
    圆角高亮。行高统一 ROW_H；行底色统一 PANEL_BG（与所属面板侧栏底色
    一致）。数据经 ItemRole 携带（见模块 docstring），列表/树通用。"""

    ROW_H = 26
    ICON_PX = 14
    INDENT = 14      # 树形子项视觉缩进（按层级深度，扁平列表为 0）
    PAD_L = 8        # 行左内边距
    PAD_R = 10       # 计数距右缘
    GAP_ICON = 6     # 图标与文字间距
    PANEL_BG = "#26262b"   # 行底色（与所属面板侧栏底色一致）

    def sizeHint(self, option, index):
        return QtCore.QSize(60, self.ROW_H)

    @staticmethod
    def _depth(index):
        """条目在树中的层级深度（扁平列表 = 0）。"""
        depth = 0
        parent = index.parent()
        while parent.isValid():
            depth += 1
            parent = parent.parent()
        return depth

    @staticmethod
    def _is_expanded(option, index):
        """段头展开态（树形视图专用；扁平视图无段头，不会走到这里）。"""
        view = option.widget
        is_expanded = getattr(view, "isExpanded", None)
        if is_expanded is None:
            return True
        try:
            return is_expanded(index)
        except RuntimeError:
            return True

    def paint(self, painter, option, index):
        is_header = bool(index.data(SIDEBAR_HEADER_ROLE))
        count = index.data(SIDEBAR_COUNT_ROLE)
        kind = index.data(SIDEBAR_ICON_ROLE)
        color_hint = index.data(SIDEBAR_COLOR_ROLE) or None
        selected = bool(option.state & QtWidgets.QStyle.State_Selected)
        rect = QtCore.QRectF(option.rect)

        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        # 整行铺侧栏底色：行区由 Qt 用 palette base 填充、色值可能与
        # viewport 不一致（亮暗分界），delegate 亲自铺底保证全栏同色
        painter.fillRect(rect, QtGui.QColor(self.PANEL_BG))

        # 选中态：半透明蓝底 + 描边圆角（段头不可选中，理论不达）
        if selected:
            path = QtGui.QPainterPath()
            path.addRoundedRect(rect.adjusted(2.0, 1.5, -2.0, -1.5), 4, 4)
            painter.fillPath(path, QtGui.QColor(13, 99, 153, 110))
            painter.setPen(QtGui.QPen(QtGui.QColor("#2e7cb8"), 1))
            painter.drawPath(path)

        # 整栏文字加粗（用户指定，与网格卡片文字全加粗同一口味）；拷贝
        # option.font 再改，不动调用方的字体对象
        font = QtGui.QFont(option.font)
        font.setBold(True)
        painter.setFont(font)
        fm = QtGui.QFontMetrics(font)

        x = rect.left() + self.PAD_L + self.INDENT * self._depth(index)
        # 段头收拢态整行调暗（"已折叠"的视觉反馈；背景不动作保持全栏统一）
        collapsed = is_header and not self._is_expanded(option, index)
        if is_header:
            draw_chevron(painter, x + 4, rect.center().y(),
                         not collapsed,
                         "#6a6a72" if collapsed else "#8a8a92")
            x += 14
        if kind:
            widget = option.widget
            dpr = widget.devicePixelRatioF() if widget is not None else 1.0
            # toolbox 实心图形显小，渲染放大 2px（TOOLBOX_ICON_BOOST），
            # 占位与推进量同步用实际尺寸，防 16px 图标压住文字
            icon_px = self.ICON_PX + (TOOLBOX_ICON_BOOST
                                      if kind == "toolbox" else 0)
            pm = sidebar_icon_pixmap(kind, icon_px, dpr, tint=color_hint)
            painter.drawPixmap(
                int(round(x)),
                int(round(rect.center().y() - icon_px / 2)), pm)
            x += icon_px + self.GAP_ICON

        count_text = "" if count is None else str(count)
        count_w = fm.horizontalAdvance(count_text)
        text_w = rect.right() - self.PAD_R - count_w - 8 - x
        text = fm.elidedText(index.data(QtCore.Qt.DisplayRole) or "",
                             QtCore.Qt.ElideRight,
                             max(0, int(text_w)))
        if is_header:
            if collapsed:
                text_color, count_color = "#8f8f97", "#a8824e"
            else:
                text_color, count_color = "#d8d8de", "#d29a55"
        else:
            text_color, count_color = "#e2e2e8", "#a8a8b0"
        if color_hint and not is_header:
            # 带色的行（标签颜色）：文字用可读化后的标签色，深色过暗
            # 会提亮（readable_on_dark），选中态仍白字覆盖
            text_color = readable_on_dark(color_hint).name()
        if selected:
            text_color = count_color = "#ffffff"
        painter.setPen(QtGui.QColor(text_color))
        painter.drawText(
            QtCore.QRectF(x, rect.top(),
                          rect.right() - self.PAD_R - count_w - 8 - x,
                          rect.height()),
            QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text)
        if count_text:
            painter.setPen(QtGui.QColor(count_color))
            painter.drawText(
                QtCore.QRectF(rect.right() - self.PAD_R - count_w,
                              rect.top(), count_w, rect.height()),
                QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, count_text)
        painter.restore()
