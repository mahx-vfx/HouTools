"""HouTools Settings 设置窗口。

主菜单 HouTools -> HouTools Settings 打开（window_manager 单例）。
布局照 ``recipelib/browser.py::_SettingsDialog``：**左分类列表 +
右内容页**（ShareX 式），三类：

- 配置目录：只读展示当前生效的 settings 目录与修改方法（HouTools.json
  包清单 env 条目 HOUTOOLS_SETTINGS_DIR，改后重启 Houdini 生效；
  constants.settings_env_override 判定是否处于显式覆盖态）；
- 快捷键：可改键捕获式设置（按 houtools.core.hotkeys.ASSIGNABLE 登记
  表渲染，每条一行：名称 + 捕获框 + 应用/恢复默认；自定义键位持久化
  settings/hotkeys.json，uiready 每次启动应用）；
- Automation：同一个 ``Automation_Config.json`` 里的 ``dw_exe_path``
  （打开DW 软件路径，输入框 + 浏览 + 保存）与 ``apps``（跨应用执行版本
  列表，QTableWidget 两列增删改）都在此可视化编辑；两者共用
  ``automation.app_settings`` 的 JsonStore，脏键合并 + 跨进程互斥，
  两个保存入口不会互相覆盖。

左侧新增分类只往 ``_PAGES`` 追加名称，并在 ``__init__`` 里按同序
``addWidget`` 对应页面即可。底部反馈行是**全局**的（跨页可见），
沿用共享 ``_hint``；视觉走 ui.theme / ui.qss 品牌 token + tool_fonts。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QHBoxLayout, QKeySequenceEdit, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QStackedWidget,
    QVBoxLayout, QTableWidget, QTableWidgetItem, QAbstractItemView,
    QFileDialog, QWidget,
)

from houtools.automation import app_settings as aps
from houtools.automation import external_run as ext
from houtools.core import constants
from houtools.core.log import get_logger
from houtools.dev import status
from houtools.ui import fonts as tool_fonts
from houtools.ui import qss, theme
from houtools.ui.components import apply_window_surface
from houtools.ui.dialogs import localize_buttons

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
    """设置面板：左分类列表 + 右内容页（与 Recipe Library 设置同构）。

    三类：配置目录 / 快捷键 / Automation。快捷键区按 hotkeys.ASSIGNABLE
    登记表渲染，新增可改键无需改本窗口；Automation 页的 dw_exe_path 与
    apps 共用 app_settings 的 JsonStore，两个保存入口各写自己的脏键。
    反馈走底部全局 _hint（错误色分支），切换分类时不会丢失。
    """

    _PAGES = ("配置目录", "快捷键", "Automation")

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setObjectName("htWindow")
        apply_window_surface(self)
        self.setWindowTitle("HouTools Settings")
        self.setMinimumWidth(640)
        self.resize(720, 460)
        self.setStyleSheet(qss.dialog_qss(qss.table_qss()))

        # ---- 左分类列表 + 右内容页（照 recipelib._SettingsDialog） ----
        self.cats = QListWidget()
        self.cats.setObjectName("settingsCats")
        self.cats.setMinimumWidth(120)
        self.cats.setMaximumWidth(150)
        for label in self._PAGES:
            self.cats.addItem(QListWidgetItem(label))
        self.stack = QStackedWidget()
        self.stack.setObjectName("settingsStack")
        self.stack.addWidget(self._build_general_page())
        self.stack.addWidget(self._build_hotkeys_page())
        self.stack.addWidget(self._build_automation_page())
        self.cats.currentRowChanged.connect(self.stack.setCurrentIndex)

        body = QHBoxLayout()
        body.setSpacing(12)
        body.addWidget(self.cats)
        body.addWidget(self.stack, 1)

        # 全局反馈行：所有页共用，切分类时仍看得到最近一次操作结果
        self._hint = QLabel(
            "设置修改即时保存；部分配置需重开或刷新 Automation 面板后生效。")
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet(
            "color: {}; font-size: 11px; background: transparent;".format(
                theme.color("TEXT_3")))

        bbox = QDialogButtonBox(QDialogButtonBox.Close)
        bbox.rejected.connect(self.reject)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        lay.addLayout(body, 1)
        lay.addWidget(self._hint)
        lay.addWidget(bbox)
        localize_buttons(self)   # Close → 关闭（须在按钮盒挂进布局之后）
        self.cats.setCurrentRow(0)
        tool_fonts.apply(self)   # 工具统一字体（子树继承 + ChildAdded 兜底）

    # ── 右内容页 ─────────────────────────────────────────────

    def _new_page(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(10)
        return page, lay

    @staticmethod
    def _sub_label(text, size=12):
        label = QLabel(text)
        label.setStyleSheet(
            "color: {}; font-size: {}px; background: transparent;".format(
                theme.color("TEXT_2"), size))
        return label

    @staticmethod
    def _hint_label(text, selectable=False):
        label = QLabel(text)
        label.setWordWrap(True)
        if selectable:
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setStyleSheet(
            "color: {}; font-size: 11px; background: transparent;".format(
                theme.color("TEXT_3")))
        return label

    def _build_general_page(self):
        """配置目录页：只读展示生效路径 + 修改方法。"""
        page, lay = self._new_page()

        if constants.settings_env_override():
            origin = "（由 HOUTOOLS_SETTINGS_DIR 指定）"
        else:
            origin = "（默认位置）"
        dir_label = QLabel("{} {}".format(constants.SETTINGS_DIR, origin))
        dir_label.setWordWrap(True)
        # 文本可选中复制（排障时直接粘路径）
        dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dir_label.setStyleSheet(
            "color: {}; background: transparent;".format(theme.color("TEXT_2")))
        self._dir_label = dir_label  # smoke test 断言生效路径展示
        lay.addWidget(dir_label)
        lay.addWidget(self._hint_label(
            "修改方法：编辑 HouTools.json 包清单，把 env 里的 "
            "HOUTOOLS_SETTINGS_DIR 改为目标目录的绝对路径（默认值为 "
            "$HOUDINI_USER_PREF_DIR/../HouTools/settings），重启 Houdini "
            "后生效。"))
        lay.addStretch(1)
        return page

    def _build_hotkeys_page(self):
        """快捷键页：ASSIGNABLE 登记表驱动，每条一行。"""
        from houtools.core import hotkeys

        page, lay = self._new_page()
        self._rows = {}  # name -> QKeySequenceEdit（smoke test 断言登记齐全）
        for name, (display, _ctx, _symbol, _default) in \
                hotkeys.ASSIGNABLE.items():
            row = QHBoxLayout()
            label = QLabel(display)
            label.setStyleSheet(
                "color: {}; background: transparent;".format(
                    theme.color("TEXT_2")))
            row.addWidget(label)
            editor = QKeySequenceEdit(hotkeys.hotkey_current_key(name))
            editor.setMinimumHeight(28)
            row.addWidget(editor, 1)
            apply_btn = QPushButton("应用")
            apply_btn.setProperty("htRole", "primary")
            apply_btn.clicked.connect(
                lambda checked=False, n=name: self._on_apply(n))
            row.addWidget(apply_btn)
            reset_btn = QPushButton("恢复默认")
            reset_btn.setProperty("htRole", "ghost")
            reset_btn.clicked.connect(
                lambda checked=False, n=name: self._on_reset(n))
            row.addWidget(reset_btn)
            lay.addLayout(row)
            self._rows[name] = editor

        lay.addWidget(self._hint_label(
            "捕获按键后点「应用」立即生效并持久化；「恢复默认」清除自定义键位。"))
        lay.addStretch(1)
        return page

    def _build_automation_page(self):
        """Automation 应用级设置页：DW 路径 + 跨应用版本列表。"""
        page, lay = self._new_page()

        lay.addWidget(self._hint_label(
            "打开DW 软件路径与跨应用执行版本都保存在 {}；"
            "改完重开或刷新 Automation 面板即生效，不必重启 Houdini。"
            .format(aps.config_path()), selectable=True))

        # dw_exe_path：输入框 + 系统对话框浏览 + 保存（只走 QFileDialog，
        # 不用 hou.ui.selectFile，避免点关闭后又弹第二次的 bug）
        dw_row = QHBoxLayout()
        dw_row.setSpacing(6)
        dw_row.addWidget(self._sub_label("打开DW 软件路径"))
        self._dw_path_le = QLineEdit()
        self._dw_path_le.setObjectName("dwExePath")
        self._dw_path_le.setPlaceholderText("deadlineworker.exe 绝对路径")
        self._dw_path_le.setText(aps.get_dw_exe_path())
        dw_browse = QPushButton("浏览…")
        dw_browse.setObjectName("dwBrowseBtn")
        dw_browse.clicked.connect(self._on_browse_dw)
        dw_save = QPushButton("保存")
        dw_save.setObjectName("dwSaveBtn")
        dw_save.setProperty("htRole", "primary")
        dw_save.clicked.connect(self._on_save_dw)
        dw_row.addWidget(self._dw_path_le, 1)
        dw_row.addWidget(dw_browse)
        dw_row.addWidget(dw_save)
        lay.addLayout(dw_row)

        lay.addWidget(self._sub_label("跨应用执行版本"))
        self._apps_table = QTableWidget(0, 2)
        self._apps_table.setObjectName("appsTable")
        self._apps_table.setHorizontalHeaderLabels(
            ["版本标识 (id)", "可执行文件 (exe)"])
        self._apps_table.horizontalHeader().setStretchLastSection(True)
        self._apps_table.setColumnWidth(0, 170)
        self._apps_table.verticalHeader().setVisible(False)
        self._apps_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._apps_table.setEditTriggers(
            QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self._apps_table.setMinimumHeight(150)
        lay.addWidget(self._apps_table, 1)
        self._load_apps_table()

        apps_btns = QHBoxLayout()
        apps_btns.setSpacing(6)
        for text, slot, role in (
            ("添加", self._on_add_app, None),
            ("删除", self._on_del_app, None),
            ("浏览…", self._on_browse_app, None),
            ("保存", self._on_save_apps, "primary"),
            ("恢复默认", self._on_reset_apps, "ghost"),
        ):
            btn = QPushButton(text)
            if role:
                btn.setProperty("htRole", role)
            btn.clicked.connect(slot)
            apps_btns.addWidget(btn)
        apps_btns.addStretch()
        lay.addLayout(apps_btns)
        return page

    # ── 反馈与快捷键 ─────────────────────────────────────────

    def _set_hint(self, text, error=False):
        color = theme.color("ERROR_TEXT") if error else theme.color("TEXT_3")
        self._hint.setText(text)
        self._hint.setStyleSheet(
            "color: {}; font-size: 11px; background: transparent;".format(
                color))

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
               severity="success")
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
               severity="success")
        self._set_hint("已恢复默认:{} → {}".format(display, default_key))

    # ── dw_exe_path（打开DW 软件路径） ─────────────────────────

    def _on_browse_dw(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Deadline Worker 可执行文件", "",
            "可执行文件 (*.exe);;所有文件 (*)")
        if path:
            self._dw_path_le.setText(os.path.normpath(path))
            self._set_hint("已选择路径，点「保存」写入配置文件。")

    def _on_save_dw(self):
        if not aps.set_dw_exe_path(self._dw_path_le.text().strip()):
            self._set_hint("保存失败：配置文件不可写（原因见控制台日志）。",
                           error=True)
            return
        # 空值会被统一层回落成出厂默认，回填让用户看到实际生效值
        self._dw_path_le.setText(aps.get_dw_exe_path())
        status("打开DW 软件路径已保存", severity="success")
        self._set_hint("已保存；重开或刷新 Automation 面板后生效。")

    # ── 跨应用执行版本列表 ────────────────────────────────────

    def _load_apps_table(self):
        """从应用级配置的 apps 键重载表格（缺失时按出厂默认补建）。"""
        self._apps_table.setRowCount(0)
        for row, app in enumerate(ext.load_apps()):
            self._apps_table.insertRow(row)
            self._apps_table.setItem(row, 0, QTableWidgetItem(app["id"]))
            self._apps_table.setItem(row, 1, QTableWidgetItem(app["exe"]))

    def _apps_from_table(self):
        """收集表格内容 → (apps, errors)。空行跳过；id/exe 缺一、id 重复报错。"""
        apps, errors, seen = [], [], set()
        for row in range(self._apps_table.rowCount()):
            id_item = self._apps_table.item(row, 0)
            exe_item = self._apps_table.item(row, 1)
            app_id = (id_item.text() if id_item else "").strip()
            exe = (exe_item.text() if exe_item else "").strip()
            if not app_id and not exe:
                continue
            if not app_id or not exe:
                errors.append("第 {} 行 id / exe 都要填".format(row + 1))
                continue
            if app_id in seen:
                errors.append("版本标识重复: {}".format(app_id))
                continue
            seen.add(app_id)
            apps.append({"id": app_id, "exe": exe})
        return apps, errors

    def _on_add_app(self):
        row = self._apps_table.rowCount()
        self._apps_table.insertRow(row)
        self._apps_table.setItem(row, 0, QTableWidgetItem(""))
        self._apps_table.setItem(row, 1, QTableWidgetItem(""))
        self._apps_table.selectRow(row)
        self._apps_table.editItem(self._apps_table.item(row, 0))
        self._set_hint("已添加一行，填写 id 与 exe 后点「保存」。")

    def _on_del_app(self):
        rows = sorted({idx.row()
                       for idx in self._apps_table.selectedIndexes()},
                      reverse=True)
        if not rows:
            self._set_hint("先选中要删除的行。")
            return
        for row in rows:
            self._apps_table.removeRow(row)
        self._set_hint(
            "已删除 {} 行，点「保存」才会写入配置文件。".format(len(rows)))

    def _on_browse_app(self):
        row = self._apps_table.currentRow()
        if row < 0:
            self._set_hint("先选中一行再点「浏览」。")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Houdini 可执行文件", "",
            "可执行文件 (*.exe);;所有文件 (*)")
        if path:
            self._apps_table.setItem(
                row, 1, QTableWidgetItem(os.path.normpath(path)))
            self._set_hint(
                "已设置第 {} 行的 exe，点「保存」生效。".format(row + 1))

    def _on_save_apps(self):
        apps, errors = self._apps_from_table()
        if errors:
            self._set_hint("；".join(errors[:3]), error=True)
            return
        if not ext.save_apps(apps):
            self._set_hint(
                "保存失败：配置文件不可写（原因见控制台日志）。", error=True)
            return
        status("跨应用执行版本已保存", severity="success")
        self._set_hint(
            "已保存 {} 个版本；重开或刷新 Automation 面板后下拉更新。"
            .format(len(apps)))

    def _on_reset_apps(self):
        from houtools.ui.dialogs import question

        if not question(self, "恢复默认",
                        "清除自定义版本，恢复 H21 / H22 两条出厂默认？"):
            return
        if not ext.reset_apps():
            self._set_hint("恢复失败：配置文件不可写。", error=True)
            return
        self._load_apps_table()
        self._set_hint("已恢复出厂默认。")
