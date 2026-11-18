"""品牌共享组件：品牌头、区块标题、按钮、空状态、胶囊、状态点。

只做组合与样式，不接管业务逻辑。所有颜色/间距来自 ``ui.theme``，字体
角色经 ``ui.fonts`` 设置。窗口模块可把右侧控件（置顶/设置等）传给
``BrandHeader``，保持工具既有功能。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.ui import fonts, theme
from houtools.ui.icons import svg_icon


def _pixmap(name, size, dpr=2.0):
    """SVG 图标 → 指定逻辑尺寸的 QPixmap（高分屏按 dpr 渲染）。"""
    icon = svg_icon(name, sizes=(int(size * dpr),))
    if icon is None:
        return None
    pm = icon.pixmap(int(size * dpr), int(size * dpr))
    pm.setDevicePixelRatio(dpr)
    return pm


def apply_window_surface(widget):
    """工具窗口/面板根部件统一暗色表面。

    Houdini 的 pane 底色（灰色）会从透明区域透出；QWidget 子类还要
    ``WA_StyledBackground`` 才画 QSS 背景。这里同时设置 palette 兜底，
    让未写样式的子控件也落在品牌暗色上（Python Panel 尤其需要）。
    """
    widget.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
    widget.setAutoFillBackground(True)
    pal = widget.palette()
    pal.setColor(QtGui.QPalette.ColorRole.Window, QtGui.QColor(theme.color("INK_950")))
    pal.setColor(QtGui.QPalette.ColorRole.Base, QtGui.QColor(theme.color("INK_950")))
    pal.setColor(QtGui.QPalette.ColorRole.AlternateBase, QtGui.QColor(theme.color("INK_900")))
    pal.setColor(QtGui.QPalette.ColorRole.WindowText, QtGui.QColor(theme.color("TEXT")))
    pal.setColor(QtGui.QPalette.ColorRole.Text, QtGui.QColor(theme.color("TEXT")))
    pal.setColor(QtGui.QPalette.ColorRole.Button, QtGui.QColor(theme.color("INK_850")))
    pal.setColor(QtGui.QPalette.ColorRole.ButtonText, QtGui.QColor(theme.color("TEXT")))
    pal.setColor(QtGui.QPalette.ColorRole.PlaceholderText,
                 QtGui.QColor(theme.color("TEXT_4")))
    pal.setColor(QtGui.QPalette.ColorRole.Highlight, QtGui.QColor(theme.color("ACCENT")))
    pal.setColor(QtGui.QPalette.ColorRole.HighlightedText,
                 QtGui.QColor(theme.color("INK_1000")))
    widget.setPalette(pal)


class BrandHeader(QtWidgets.QWidget):
    """品牌头：产品图标 + “HouTools” 字标 + 工具名 + 版本胶囊。

    高度 40；底部 1px LINE。右侧可挂置顶/设置等既有控件；版本胶囊固定
    在最右。只做展示，不连接任何业务信号。
    """

    HEIGHT = 40

    def __init__(self, tool_name, version=None, right_widgets=None,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("htBrandHeader")
        self.setFixedHeight(self.HEIGHT)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            "QWidget#htBrandHeader {{ background: {ink}; }}".format(
                ink=theme.color("INK_950")))

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setFixedSize(20, 20)
        pm = _pixmap("brand_product.svg", 20)
        if pm is not None:
            icon_lbl.setPixmap(pm)
        lay.addWidget(icon_lbl)
        lay.addSpacing(2)

        self.wordmark = QtWidgets.QLabel(
            '<span style="color:{text}">Hou</span>'
            '<span style="color:{accent}">Tools</span>'.format(
                text=theme.color("TEXT"), accent=theme.color("ACCENT")))
        self.wordmark.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.wordmark.setObjectName("htBrandWordmark")
        f = self.wordmark.font()
        f.setPixelSize(13)
        fonts.set_letter_spacing(f, 0.06, 13)
        self.wordmark.setFont(f)
        fonts.apply_role(self.wordmark, fonts.ROLE_BRAND)
        lay.addWidget(self.wordmark)

        lay.addWidget(self._separator())

        self.tool_label = QtWidgets.QLabel(tool_name)
        self.tool_label.setObjectName("htBrandTool")
        f = self.tool_label.font()
        f.setPixelSize(13)
        f.setWeight(QtGui.QFont.Weight.Medium)
        self.tool_label.setFont(f)
        fonts.apply_role(self.tool_label, fonts.ROLE_UI)
        lay.addWidget(self.tool_label)

        lay.addStretch(1)

        for widget in right_widgets or ():
            lay.addWidget(widget)
            lay.addSpacing(4)

        if version:
            pill = Pill("v{}".format(str(version).lstrip("v")), mono=True)
            lay.addWidget(pill)

        # 子控件颜色（QSS 类型选择器会被 Houdini 全局样式压过，这里按
        # objectName 明确声明）
        self.setStyleSheet(self.styleSheet() + """
            QLabel#htBrandWordmark {{ color: %s; background: transparent; }}
            QLabel#htBrandTool {{ color: %s; background: transparent; }}
        """ % (theme.color("TEXT"), theme.color("TEXT_2")))

    def _separator(self):
        line = QtWidgets.QWidget()
        line.setFixedSize(1, 16)
        line.setStyleSheet(
            "background: %s;" % theme.color("LINE_STRONG"))
        return line

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QPen(
            QtGui.QColor(theme.color("LINE")), 1))
        painter.drawLine(0, self.height() - 1, self.width(),
                         self.height() - 1)
        painter.end()


class SectionHeader(QtWidgets.QWidget):
    """区块标题：13/600 + 可选 2px 品牌橙左条 + 底部 1px LINE。"""

    HEIGHT = 30

    def __init__(self, text, accent=True, parent=None):
        super().__init__(parent)
        self.setFixedHeight(self.HEIGHT)
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        if accent:
            bar = QtWidgets.QWidget()
            bar.setFixedSize(2, 14)
            bar.setStyleSheet(
                "background: %s; border-radius: 1px;"
                % theme.color("ACCENT"))
            lay.addWidget(bar)
        label = QtWidgets.QLabel(text)
        label.setObjectName("htSectionTitle")
        f = label.font()
        f.setPixelSize(13)
        f.setWeight(QtGui.QFont.Weight.DemiBold)
        label.setFont(f)
        fonts.apply_role(label, fonts.ROLE_UI)
        label.setStyleSheet(
            "color: %s; background: transparent;"
            % theme.color("TEXT"))
        lay.addWidget(label)
        lay.addStretch(1)

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QtGui.QPainter(self)
        painter.setPen(QtGui.QPen(QtGui.QColor(theme.color("LINE")), 1))
        painter.drawLine(0, self.height() - 1, self.width(),
                         self.height() - 1)
        painter.end()


class PrimaryButton(QtWidgets.QPushButton):
    """主按钮：品牌橙底 + 墨黑字（每个窗口只应有一个）。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setProperty("htRole", "primary")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)


class SecondaryButton(QtWidgets.QPushButton):
    """次按钮：INK_850 底 + TEXT + 1px LINE。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)


class DangerButton(QtWidgets.QPushButton):
    """危险按钮：透明底 + ERROR_TEXT + 1px ERROR。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setProperty("htRole", "danger")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)


class GhostButton(QtWidgets.QPushButton):
    """幽灵按钮：透明底 + TEXT_2。"""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setProperty("htRole", "ghost")
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)


class Pill(QtWidgets.QLabel):
    """胶囊标签：11px / 999 圆角；语义色用 14% 透明底 + 同色文字。"""

    def __init__(self, text="", color=None, mono=False, parent=None):
        super().__init__(text, parent)
        self.setObjectName("htPill")
        f = self.font()
        f.setPixelSize(11)
        self.setFont(f)
        fonts.apply_role(self, fonts.ROLE_MONO if mono else fonts.ROLE_UI)
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        if color:
            qc = QtGui.QColor(color)
            self.setStyleSheet(
                "QLabel#htPill {{ background: rgba({r},{g},{b},0.14);"
                " color: {c}; border-radius: 999px; padding: 1px 9px; }}"
                .format(r=qc.red(), g=qc.green(), b=qc.blue(),
                        c=color))
        else:
            self.setStyleSheet(
                "QLabel#htPill {{ background: rgba(255,255,255,0.06);"
                " color: {c}; border-radius: 999px; padding: 1px 9px; }}"
                .format(c=theme.color("TEXT_3")))


class StatusDot(QtWidgets.QWidget):
    """状态点：8px 实心圆；颜色用语义 token（同时配文字/图标使用）。"""

    def __init__(self, color=None, size=8, parent=None):
        super().__init__(parent)
        self._color = QtGui.QColor(color or theme.color("SUCCESS"))
        self._size = size
        self.setFixedSize(size, size)

    def set_color(self, color):
        self._color = QtGui.QColor(color)
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(self.rect())
        painter.end()


class EmptyState(QtWidgets.QWidget):
    """空状态：48px 线稿图标 + 15/600 标题 + 12 TEXT_3 提示。

    可选 6% 品牌蓝图网格 + 四角标记点（半内半外 2px），只用于空状态/
    About 这类低密度区域，不铺进参数/列表。
    """

    def __init__(self, title, hint="", icon_name="empty_grid.svg",
                 show_grid=True, parent=None):
        super().__init__(parent)
        self._show_grid = show_grid
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(8)
        lay.addStretch(1)

        icon_lbl = QtWidgets.QLabel()
        icon_lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        pm = _pixmap(icon_name, 48)
        if pm is not None:
            icon_lbl.setPixmap(pm)
        lay.addWidget(icon_lbl)

        self.title = QtWidgets.QLabel(title)
        self.title.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        f = self.title.font()
        f.setPixelSize(15)
        f.setWeight(QtGui.QFont.Weight.DemiBold)
        self.title.setFont(f)
        fonts.apply_role(self.title, fonts.ROLE_UI)
        self.title.setStyleSheet(
            "color: %s; background: transparent;" % theme.color("TEXT"))
        lay.addWidget(self.title)

        if hint:
            self.hint = QtWidgets.QLabel(hint)
            self.hint.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            self.hint.setWordWrap(True)
            f = self.hint.font()
            f.setPixelSize(12)
            self.hint.setFont(f)
            fonts.apply_role(self.hint, fonts.ROLE_UI)
            self.hint.setStyleSheet(
                "color: %s; background: transparent;"
                % theme.color("TEXT_3"))
            lay.addWidget(self.hint)
        lay.addStretch(1)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self._show_grid:
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        # 6% 蓝图网格（48px 虚线格），只在空状态轻量铺底
        grid = QtGui.QColor(theme.color("GRID_LINE"))
        grid.setAlphaF(0.06)
        pen = QtGui.QPen(grid, 1, QtCore.Qt.PenStyle.DashLine)
        pen.setDashPattern([4, 8])
        painter.setPen(pen)
        step = 48
        for x in range(0, self.width(), step):
            painter.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            painter.drawLine(0, y, self.width(), y)
        # 四角标记点：2px，半内半外
        dot = QtGui.QColor(theme.color("GRID_DOT"))
        dot.setAlphaF(0.55)
        painter.setPen(QtCore.Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        for px, py in ((0, 0), (self.width() - 1, 0),
                       (0, self.height() - 1),
                       (self.width() - 1, self.height() - 1)):
            painter.drawEllipse(QtCore.QPointF(px, py), 2, 2)
        painter.end()
