"""HouTools Settings 设置窗口。

主菜单 HouTools -> HouTools Settings 打开（window_manager 单例）：
- 配置目录：只读展示当前生效的 settings 目录与修改方法（HouTools.json
  包清单 env 条目 HOUTOOLS_SETTINGS_DIR，改后重启 Houdini 生效；
  constants.settings_env_override 判定是否处于显式覆盖态）；
- 快捷键：Paste as Object Merge 的捕获式改键（原独立菜单项
  Paste Hotkey Settings 迁入；逻辑在 houtools.core.hotkeys，自定义键位
  持久化 settings/hotkeys.json，uiready 每次启动应用）。
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QKeySequenceEdit, QLabel, QPushButton,
    QVBoxLayout,
)

from houtools.core import constants
from houtools.core.log import get_logger
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
    """配置目录（只读）+ Paste as Object Merge 快捷键捕获，单窗口两段。

    与旧独立改键对话框的差异：设置窗口常驻（应用后不自动关闭），
    「恢复默认」是显式按钮（旧版空序列=确认清除的暗语义不再需要）。
    """

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("HouTools Settings")
        self.setMinimumWidth(460)
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

        # ---- 快捷键（原 Paste Hotkey Settings 迁入） ----
        sec2 = QLabel("快捷键 · Paste as Object Merge")
        sec2.setStyleSheet("font-weight: bold; font-size: 13px;")
        layout.addWidget(sec2)

        from houtools.core import hotkeys

        self._editor = QKeySequenceEdit(hotkeys.paste_merge_current_key())
        layout.addWidget(self._editor)

        self._hint = QLabel(
            "捕获按键后点「应用」立即生效并持久化；「恢复默认」清除自定义键位。")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #888888; font-size: 11px;")
        layout.addWidget(self._hint)

        btn_row = QHBoxLayout()
        reset_btn = QPushButton("恢复默认")
        reset_btn.clicked.connect(self._on_reset)
        btn_row.addWidget(reset_btn)
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        apply_btn = QPushButton("应用")
        apply_btn.setObjectName("okBtn")
        apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(close_btn)
        btn_row.addWidget(apply_btn)
        layout.addLayout(btn_row)

    def _set_hint(self, text, error=False):
        color = "#d1283e" if error else "#888888"
        self._hint.setText(text)
        self._hint.setStyleSheet("color: {}; font-size: 11px;".format(color))

    def _on_apply(self):
        import hou

        from houtools.core import hotkeys

        key = self._editor.keySequence().toString()
        if not key:
            self._set_hint("未捕获到按键。要清除自定义键位请点「恢复默认」。")
            return
        if not hotkeys.paste_merge_apply_key(key):
            self._set_hint("Houdini 拒绝了该键位:{}".format(key), error=True)
            logger.warning("Houdini 拒绝了键位: %s", key)
            return
        logger.info("Paste as Object Merge 快捷键已设为: %s", key)
        try:
            hou.ui.setStatusMessage(
                "Paste as Object Merge 快捷键已设为:{}".format(key),
                hou.severityType.ImportantMessage)
        except Exception as exc:
            logger.debug("状态栏提示写入失败: %s", exc)
        self._set_hint("已应用:{}".format(key))

    def _on_reset(self):
        import hou

        from houtools.core import hotkeys
        from houtools.ui.dialogs import question

        default_key = hotkeys.PASTE_MERGE_DEFAULT_KEY
        if not question(self, "恢复默认",
                        "清除自定义键位、恢复默认（{}）？".format(default_key)):
            return
        if not hotkeys.paste_merge_clear_custom():
            self._set_hint("Houdini 拒绝了默认键位。", error=True)
            return
        self._editor.setKeySequence(QKeySequence(default_key))
        logger.info("Paste as Object Merge 自定义键位已清除")
        try:
            hou.ui.setStatusMessage(
                "Paste as Object Merge 快捷键已恢复默认:{}".format(default_key),
                hou.severityType.ImportantMessage)
        except Exception as exc:
            logger.debug("状态栏提示写入失败: %s", exc)
        self._set_hint("已恢复默认:{}".format(default_key))
