"""Automation 样式常量（品牌 token 生成）。

集中管理主窗口 ``STYLE_SHEET``：墨黑工作区 + 品牌橙动作色（唯一主按钮
= 执行），任务槽为 INK_900 卡片、选中态橙色淡底 + 左侧 2px 橙条。
颜色/圆角/字号全部来自 ``houtools.ui.theme``，本文件不留裸 hex。

关键 objectName（``startBtn`` / ``taskSlot``）在 QSS 里用 ``#name``
选择器特化；pane 内 Houdini 全局样式表会压过窗口级类型选择器，工具栏
按钮另有部件级内联样式（见 window.py 的 ``_TOOLBAR_BTN_STYLE``）。
"""

from houtools.ui import qss, theme

_EXTRA = """
QWidget#taskSlot {{
    background-color: {INK_900};
    border: 1px solid {LINE};
    border-radius: 8px;
}}
QWidget#taskSlot[selected="true"] {{
    background-color: {ACCENT_SOFT};
    border: 1px solid {ACCENT_LINE};
    border-left: 2px solid {ACCENT};
}}
QWidget#taskSlot[dragging="true"] {{
    background-color: {ACCENT_SOFT};
    border: 1px solid {ACCENT};
}}
QWidget#taskSlot[selected="true"] QLabel#taskSlotHandle {{
    color: {ACCENT};
}}
QLabel#taskSlotHandle {{
    background-color: transparent;
    padding: 2px 4px;
    color: {TEXT_3};
}}
QLabel#taskSlotHandle:hover {{
    background-color: {INK_800};
    border-radius: 4px;
    color: {TEXT};
}}
QFrame#dropIndicator {{
    background-color: {ACCENT};
    border: none;
}}
QLabel#configLabel {{
    color: {ACCENT};
    background: transparent;
}}
QLabel#taskCountLabel {{
    color: {TEXT_2};
    background: transparent;
}}
""".format(**theme.TOKENS)

STYLE_SHEET = qss.window_qss(_EXTRA)
