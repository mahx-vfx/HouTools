"""HouTools Settings 设置窗口。

主菜单 HouTools -> HouTools Settings 打开（window_manager 单例）：
- 配置目录：只读展示当前生效的 settings 目录与修改方法（HouTools.json
  包清单 env 条目 HOUTOOLS_SETTINGS_DIR，改后重启 Houdini 生效；
  constants.settings_env_override 判定是否处于显式覆盖态）；
- 快捷键：可改键捕获式设置（按 houtools.core.hotkeys.ASSIGNABLE 登记
  表渲染，每条一行：名称 + 捕获框 + 应用/恢复默认；自定义键位持久化
  settings/hotkeys.json，uiready 每次启动应用）。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton,
    QVBoxLayout,
)

from houtools.core import constants
from houtools.core.log import get_logger
from houtools.dev import status
from houtools.ui import fonts as tool_fonts

logger = get_logger("tools.houtools_settings")

TOOL_ID = "houtools_settings"


def run():
    """菜单入口:打开设置窗口(window_manager 单例)。"""
    import hou

    from houtools.ui import window_manager

    parent = None
    try:
        parent = hou.qt.mainWindow()
    except Exception as exc:
        logger.debug("主窗口不可用: %s", exc)
    window_manager.open_window(TOOL_ID,
                               lambda: _HouToolsSettingsDialog(parent))


class _HouToolsSettingsDialog(QDialog):
    """配置目录（只读）+ 可改键快捷键区（ASSIGNABLE 驱动），单窗口两段。

    快捷键区按 hotkeys.ASSIGNABLE 登记表渲染，新增可改键无需改本窗口；
    反馈走共享的 _hint 标签（最近一次应用/恢复的结果，带错误色分支）。
    """

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("HouTools Settings")
        self.setMinimumWidth(520)
        self.setStyleSheet(
            "QDialog { background-color: #1D1D20; }"
            "QLabel { color: #e0e0e0; background: transparent; }"
            "QKeySequenceEdit { background-color: #2d2d2d; color: #e0e0e0;"
            " border: 1px solid #3d3d3d; border-radius: 4px; padding: 5px 8px; }"
            "QPushButton { background-color: #2d2d2d; color: #e0e0e0;"
            " border: 1px solid #3d3d3d; border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #3d3d3d; }"
            "QPushButton#okBtn { background-color: #0d6399; color: white;"
            " font-weight: bold; }"
            "QPushButton#okBtn:hover { background-color: #0e7bc9; }"
        )
        tool_fonts.apply(self)   # 工具统一字体（子树继承 + ChildAdded 兜底）

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # ---- 配置目录（只读展示 + 修改方法） ----
        sec1 = QLabel("配置目录")
        sec1.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(sec1)

        if constants.settings_env_override():
            origin = "（由 HOUTOOLS_SETTINGS_DIR 指定）"
        else:
            origin = "（默认位置）"
        dir_label = QLabel("{} {}".format(constants.SETTINGS_DIR, origin))
        dir_label.setWordWrap(True)
        # 文本可选中复制（排障时直接粘路径）
        dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._dir_label = dir_label  # smoke test 断言生效路径展示
        layout.addWidget(dir_label)

        tip = QLabel(
            "修改方法：编辑 HouTools.json 包清单，把 env 里的 "
            "HOUTOOLS_SETTINGS_DIR 改为目标目录的绝对路径（默认值为 "
            "$HOUDINI_USER_PREF_DIR/../HouTools/settings），重启 Houdini "
            "后生效。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(tip)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("color: #3d3d3d;")
        layout.addWidget(line)

        # ---- 快捷键（ASSIGNABLE 登记，每条一行） ----
        sec2 = QLabel("快捷键")
        sec2.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(sec2)

        from houtools.core import hotkeys

        self._rows = {}  # name -> QKeySequenceEdit（smoke test 断言登记齐全）
        for name, (display, _ctx, _symbol, _default) in \
                hotkeys.ASSIGNABLE.items():
            row = QHBoxLayout()
            label = QLabel(display)
            row.addWidget(label)
            editor = QKeySequenceEdit(hotkeys.hotkey_current_key(name))
            row.addWidget(editor, 1)
            apply_btn = QPushButton("应用")
            apply_btn.setObjectName("okBtn")
            apply_btn.clicked.connect(
                lambda checked=False, n=name: self._on_apply(n))
            row.addWidget(apply_btn)
            reset_btn = QPushButton("恢复默认")
            reset_btn.clicked.connect(
                lambda checked=False, n=name: self._on_reset(n))
            row.addWidget(reset_btn)
            layout.addLayout(row)
            self._rows[name] = editor

        self._hint = QLabel(
            "捕获按键后点「应用」立即生效并持久化；「恢复默认」清除自定义键位。")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(self._hint)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _set_hint(self, text, error=False):
        color = "#d1283e" if error else "#888888"
        self._hint.setText(text)
        self._hint.setStyleSheet("color: {}; font-size: 11px;".format(color))

    def _on_apply(self, name):
        from houtools.core import hotkeys

        key = self._rows[name].keySequence().toString()
        if not key:
            self._set_hint("未捕获到按键。要清除自定义键位请点「恢复默认」。")
            return
        if not hotkeys.hotkey_apply_key(name, key):
            self._set_hint("应用失败：Houdini 拒绝了该键位，或配置目录"
                           "不可写（原因见控制台日志）。", error=True)
            logger.warning("键位应用失败: %s", key)
            return
        display = hotkeys.ASSIGNABLE[name][0]
        logger.info("%s 快捷键已设为: %s", display, key)
        status("{} 快捷键已设为:{}".format(display, key),
               severity="important")
        self._set_hint("已应用:{} → {}".format(display, key))

    def _on_reset(self, name):
        from houtools.core import hotkeys
        from houtools.ui.dialogs import question

        display, _ctx, _sym, default_key = hotkeys.ASSIGNABLE[name]
        if not question(self, "恢复默认",
                        "清除 {} 的自定义键位、恢复默认（{}）？".format(
                            display, default_key)):
            return
        if not hotkeys.hotkey_reset_default(name):
            self._set_hint("恢复失败：Houdini 拒绝了默认键位，或配置目录"
                           "不可写（原因见控制台日志）。", error=True)
            return
        self._rows[name].setKeySequence(QKeySequence(default_key))
        logger.info("%s 自定义键位已清除", display)
        status("{} 快捷键已恢复默认:{}".format(display, default_key),
               severity="important")
        self._set_hint("已恢复默认:{} → {}".format(display, default_key))
