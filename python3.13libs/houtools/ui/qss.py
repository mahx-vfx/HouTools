"""按品牌 token 生成 Qt 样式表。

所有颜色/字号/圆角来自 ``houtools.ui.theme``，本模块只做拼接；窗口模块
通过 ``window_qss(extra)`` 取基础表再叠加自己的 objectName 规则。选择器
优先 ``objectName`` / 动态属性（``[htRole="primary"]``），不依赖类型选择器
压 Houdini 全局样式。
"""

from pathlib import Path

from houtools.ui import theme

_ICONS = Path(__file__).resolve().parent.parent / "icons"
_CHECK_OFF_ICON = (_ICONS / "check_off_28.png").as_posix()
_CHECK_ON_ICON = (_ICONS / "check_on_28.png").as_posix()


def _tokens():
    tokens = dict(theme.TOKENS)
    tokens["CHECK_OFF_ICON"] = _CHECK_OFF_ICON
    tokens["CHECK_ON_ICON"] = _CHECK_ON_ICON
    return tokens


def _fmt(template):
    """``str.format`` 注入 token（模板里的 CSS 花括号写成 ``{{``/``}}``）。"""
    return template.format(**_tokens())


# ---------------------------------------------------------------------------
# 基础窗口
# ---------------------------------------------------------------------------
_BASE_TEMPLATE = """
QWidget {{
    color: {TEXT};
}}
QWidget#htWindow, QDialog {{
    background-color: {INK_950};
}}
QLabel {{
    background: transparent;
    color: {TEXT};
    border: none;
}}
QLabel[htTextRole="secondary"] {{ color: {TEXT_2}; }}
QLabel[htTextRole="muted"]     {{ color: {TEXT_3}; }}
QLabel[htTextRole="disabled"]  {{ color: {TEXT_4}; }}
QLabel[htTextRole="mono"]      {{ color: {TEXT_2}; }}
QLabel[htTextRole="success"]   {{ color: {SUCCESS_TEXT}; }}
QLabel[htTextRole="warning"]   {{ color: {WARNING_TEXT}; }}
QLabel[htTextRole="error"]     {{ color: {ERROR_TEXT}; }}

QPushButton {{
    background-color: {INK_850};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 0 12px;
    min-height: 28px;
    font-size: 13px;
}}
QPushButton:hover {{
    background-color: {INK_800};
    border-color: {LINE_STRONG};
}}
QPushButton:pressed {{ background-color: {INK_900}; }}
QPushButton:disabled {{
    background-color: {INK_900};
    color: {TEXT_4};
    border-color: {LINE};
}}
QPushButton[htRole="primary"], QPushButton#startBtn, QPushButton#convertBtn,
QPushButton#okBtn, QPushButton#confirmBtn {{
    background-color: {ACCENT_FILL};
    color: {ON_ACCENT};
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 0 14px;
    min-height: 28px;
    font-size: 13px;
    font-weight: 600;
}}
QPushButton[htRole="primary"]:hover, QPushButton#startBtn:hover,
QPushButton#convertBtn:hover, QPushButton#okBtn:hover,
QPushButton#confirmBtn:hover {{ border-color: {ACCENT}; }}
QPushButton[htRole="primary"]:pressed, QPushButton#startBtn:pressed,
QPushButton#convertBtn:pressed, QPushButton#okBtn:pressed,
QPushButton#confirmBtn:pressed {{ background-color: {ACCENT_FILL_PRESSED}; }}
QPushButton[htRole="primary"]:disabled, QPushButton#startBtn:disabled,
QPushButton#convertBtn:disabled, QPushButton#okBtn:disabled,
QPushButton#confirmBtn:disabled {{
    background-color: {INK_850};
    color: {TEXT_4};
}}
QPushButton[htRole="primary"]:focus, QPushButton#startBtn:focus,
QPushButton#convertBtn:focus, QPushButton#okBtn:focus,
QPushButton#confirmBtn:focus {{ border: 2px solid {FOCUS_RING}; }}

QPushButton[htRole="ghost"] {{
    background: transparent;
    border: none;
    color: {TEXT_2};
}}
QPushButton[htRole="ghost"]:hover {{
    background-color: rgba(255,255,255,0.05);
    color: {TEXT};
}}
QPushButton[htRole="danger"], QPushButton#cancelBtn,
QPushButton#configDeleteBtn {{
    background-color: transparent;
    color: {ERROR_TEXT};
    border: 1px solid {ERROR};
}}
QPushButton[htRole="danger"]:hover, QPushButton#cancelBtn:hover,
QPushButton#configDeleteBtn:hover {{
    background-color: rgba(209,40,62,0.12);
    border-color: {ERROR};
}}
QPushButton[htRole="danger"]:pressed, QPushButton#cancelBtn:pressed,
QPushButton#configDeleteBtn:pressed {{
    background-color: rgba(209,40,62,0.22);
}}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox, QKeySequenceEdit {{
    background-color: {INK_900};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 4px;
    padding: 4px 8px;
    min-height: 28px;
    font-size: 12px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover, QKeySequenceEdit:hover {{
    border-color: {LINE_STRONG};
}}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus,
QKeySequenceEdit:focus {{
    border: 2px solid {ACCENT};
    padding: 3px 7px;
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
    background: transparent;
}}
QComboBox QAbstractItemView {{
    background-color: {INK_850};
    color: {TEXT_2};
    border: 1px solid {LINE};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
    outline: 0;
}}
QComboBox#configCombo {{
    background-color: {INK_900};
    color: {TEXT};
    border: 1px solid {LINE};
    padding: 4px 8px;
}}
QComboBox#configCombo:hover {{
    border-color: {LINE_STRONG};
    background-color: {INK_850};
}}
QComboBox#configCombo:focus {{
    border: 1px solid {LINE_STRONG};
    padding: 4px 8px;
    background-color: {INK_850};
}}
QComboBox#configCombo::drop-down:hover {{
    background-color: rgba(255,92,56,0.12);
}}
QComboBox#configCombo::drop-down:on {{
    background-color: rgba(255,92,56,0.22);
}}
QLineEdit[htMono="true"], QLabel[htMono="true"] {{
    font-family: "Consolas", "Courier New", monospace;
}}

QCheckBox {{
    color: {TEXT_2};
    spacing: 6px;
    background: transparent;
}}
QCheckBox::indicator {{
    width: 14px;
    height: 14px;
    image: url("{CHECK_OFF_ICON}");
}}
QCheckBox::indicator:checked {{
    image: url("{CHECK_ON_ICON}");
}}
QGroupBox {{
    background-color: {INK_900};
    border: 1px solid {LINE};
    border-radius: 8px;
    margin-top: 14px;
    padding: 8px 8px 6px 8px;
    font-size: 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: -2px;
    padding: 0 6px;
    color: {TEXT_2};
    background-color: {INK_950};
}}

QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollArea > QWidget > QWidget > QWidget {{ background: transparent; }}

QMenu {{
    background-color: {INK_850};
    color: {TEXT_2};
    border: 1px solid {LINE};
    border-radius: 8px;
    padding: 4px;
}}
QMenu::item {{
    color: {TEXT_2};
    padding: 5px 28px 5px 14px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {ACCENT_SOFT};
    color: {TEXT};
}}
QMenu::item:disabled {{ color: {TEXT_4}; }}
QMenu::separator {{
    height: 1px;
    background: {LINE};
    margin: 4px 6px;
}}

QListWidget, QTreeWidget, QListView, QTreeView, QPlainTextEdit, QTextBrowser,
QTextEdit {{
    background-color: {INK_900};
    color: {TEXT_2};
    border: 1px solid {LINE};
    border-radius: 8px;
    outline: 0;
}}
QListWidget::item, QListView::item, QTreeWidget::item, QTreeView::item {{
    color: {TEXT_2};
}}
QListWidget::item:selected, QListView::item:selected,
QTreeWidget::item:selected, QTreeView::item:selected {{
    background-color: {ACCENT_SOFT};
    color: {TEXT};
}}

QSplitter::handle:horizontal {{
    background: {LINE};
    width: 2px;
}}
QSlider::groove:horizontal {{
    height: 3px;
    background: {LINE};
    border-radius: 2px;
}}
QSlider::sub-page:horizontal {{
    background: {ACCENT};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    background: {TEXT};
    width: 12px;
    height: 12px;
    margin: -5px 0;
    border-radius: 6px;
}}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}
QSlider::handle:horizontal:pressed {{ background: {ACCENT_PRESSED}; }}

QProgressBar {{
    border: 1px solid {LINE};
    border-radius: 4px;
    text-align: center;
    background-color: {INK_900};
    color: {TEXT_2};
    min-height: 18px;
    font-size: 11px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 3px;
}}

QTabWidget::pane {{
    border: 1px solid {LINE};
    border-radius: 8px;
    background: {INK_950};
}}
QTabBar::tab {{
    background: transparent;
    color: {TEXT_3};
    padding: 6px 14px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {TEXT};
    border-bottom: 2px solid {ACCENT};
}}

QToolTip {{
    background-color: {INK_850};
    color: {TEXT};
    border: 1px solid {LINE};
    padding: 4px 8px;
}}

QLabel#statusLabel {{
    color: {TEXT_3};
    padding: 4px 8px;
    font-size: 11px;
}}
QFrame[htFrame="line"] {{ background: {LINE}; max-height: 1px; border: none; }}
QFrame[htFrame="vline"] {{ background: {LINE}; max-width: 1px; border: none; }}

QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {LINE_STRONG};
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {LINE_STRONG};
    border-radius: 4px;
    min-width: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
"""


def window_qss(extra=""):
    """窗口/面板基础 QSS；``extra`` 追加模块自己的 objectName 规则。"""
    return _fmt(_BASE_TEMPLATE) + ("\n" + extra if extra else "")


def dialog_qss(extra=""):
    """对话框基础 QSS（内容底 INK_950、12 圆角由窗口设置）。"""
    return window_qss(extra)


def table_qss():
    """QTableWidget 样式片段（设置窗口的版本列表按需追加）。

    与基础表共用同一套 token，不写裸 hex；``window_qss(extra)`` 的 extra
    不再过 ``_fmt``，所以这里自己格式化后返回。
    """
    return _fmt("""
QTableWidget {{
    background-color: {INK_900};
    color: {TEXT};
    border: 1px solid {LINE};
    border-radius: 6px;
    gridline-color: {LINE};
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 3px 6px; }}
QTableWidget::item:selected {{
    background-color: {ACCENT_SOFT};
    color: {TEXT};
}}
QHeaderView::section {{
    background-color: {INK_850};
    color: {TEXT_2};
    border: none;
    border-right: 1px solid {LINE};
    border-bottom: 1px solid {LINE};
    padding: 4px 6px;
}}
""")


def menu_qss():
    """独立菜单 QSS（QMenu 顶层弹层用）。"""
    return window_qss()


def card_qss():
    """卡片面板 QSS 片段。"""
    return _fmt("""
QFrame[htCard="true"], QWidget[htCard="true"] {{
    background-color: {INK_900};
    border: 1px solid {LINE};
    border-radius: 8px;
}}
QFrame[htCard="true"]:hover, QWidget[htCard="true"]:hover {{
    background-color: {INK_850};
    border-color: {LINE_STRONG};
}}
""")


def status_qss():
    """状态栏/提示文本 QSS 片段。"""
    return _fmt("""
QLabel[htTextRole="success"] {{ color: {SUCCESS_TEXT}; }}
QLabel[htTextRole="warning"] {{ color: {WARNING_TEXT}; }}
QLabel[htTextRole="error"]   {{ color: {ERROR_TEXT}; }}
""")
