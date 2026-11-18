"""打包工程：把工程用到的文件收进包目录、参数改成相对写法，并生成 zip。

流程（对应渲染农场提交前的"整理工程"）：

1. **扫描**：遍历已启用节点上的文件参数，收外部引用 / 节点缓存 /
   `$HIP` 内相对引用——凡是场景真正用到的、磁盘上存在的文件；
2. **预览**：逐行「类型 / 节点 / 参数 / 原路径 / 目标」，可勾选；
3. **打包**：把勾选的文件**全部**复制到 `<包根>/<包名>/assets/<展平源目录>/`，
   参数改写成 `$HIP/…` 或 `$JOB/…`（帧模板 `$F4` 原样保留）；
4. **生成方式**（二选一）：
   - **覆盖当前工程**：改写后保存当前 hip（文件仍在原地）；
   - **另存到包目录**（默认）：改写后另存为 `<包名>/<hip>_packed.hip`，
     磁盘上的原工程不动；
5. **生成 zip**：把包目录整体压成 `<包根>/<包名>.zip`；
6. **结果面板**：完成/失败 + 复制、改写、保存的逐条失败原因。
"""

from __future__ import annotations

import os
import shutil
import zipfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
    QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from houtools.core.log import get_logger
from houtools.package import collect, paths
from houtools.ui import dialogs, fonts as tool_fonts, notify, reveal, taskbar

log = get_logger("tools.package_project")

TOOL_ID = "package_project"

_COL_CHECK = 0
_COL_KIND = 1
_COL_NODE = 2
_COL_PARM = 3
_COL_SRC = 4
_COL_DST = 5
_COL_STATE = 6

_COLOR_OK = "#e0e0e0"
_COLOR_DIM = "#8a8a8a"
_COLOR_GOOD = "#4ade80"
_COLOR_BAD = "#ff6b6b"


class PackageProjectWindow(QDialog):
    """工程打包对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setWindowTitle("Package Project")
        self.setMinimumSize(1040, 680)
        self.resize(1260, 840)

        self._refs = []
        self._targets = []
        self._checked = set()
        self._filling = False
        self._hip_dir = ""
        # 打包时算定的目录：另存会使 $HIP 变成包目录，之后再按 $HIP 推算
        # 会得到 <包>/<包> 这种嵌套错路径，所以 zip 用这里的快照
        self._last_base = ""
        self._last_pkg = ""
        self._last_package_dir = ""

        self._build_ui()
        self._apply_styles()
        tool_fonts.apply(self)
        taskbar.apply_appwindow_flags(self)
        self._set_hint("点「扫描」收集当前工程用到的文件。")
        self._log("等待扫描…")

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)

        # 包根 + 包名
        top = QHBoxLayout()
        top.addWidget(QLabel("包根目录"))
        self._root_combo = QComboBox()
        self._root_combo.addItem("$HIP", "HIP")
        self._root_combo.addItem("$JOB", "JOB")
        self._root_combo.setToolTip(
            "$HIP = 工程目录；$JOB = 项目根（需在 houdini.env / 环境变量里设置）")
        self._root_combo.currentIndexChanged.connect(self._on_targets_changed)
        top.addWidget(self._root_combo)
        top.addSpacing(12)
        top.addWidget(QLabel("包名"))
        self._pkg_edit = QLineEdit(paths.PACKAGE_DIR)
        self._pkg_edit.setFixedWidth(180)
        self._pkg_edit.textChanged.connect(self._on_targets_changed)
        top.addWidget(self._pkg_edit)
        top.addStretch()
        self._include_output_check = QCheckBox("含空输出路径")
        self._include_output_check.setToolTip(
            "默认只收磁盘上已有文件的输出（filecache 的 geo 缓存这类）；"
            "勾上才把还没落盘的输出参数也列出来")
        self._include_output_check.toggled.connect(self._on_scan)
        top.addWidget(self._include_output_check)
        top.addSpacing(8)
        self._scan_btn = QPushButton("扫描")
        self._scan_btn.setToolTip("收集用户手动填过的文件引用与已落盘的缓存，"
                                  "缺失的也会列出来")
        self._scan_btn.clicked.connect(self._on_scan)
        top.addWidget(self._scan_btn)
        layout.addLayout(top)

        # 生成方式
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("生成方式"))
        self._save_copy_radio = QRadioButton("另存到包目录（原工程不动）")
        self._save_copy_radio.setChecked(True)
        self._save_copy_radio.setToolTip(
            "包内生成 <hip>_packed.hip；磁盘上的原 hip 不会被改。"
            "注意：保存后 hip 的 $HIP 就是包目录，所以参数写成 $HIP/assets/…")
        self._overwrite_radio = QRadioButton("覆盖当前工程")
        self._overwrite_radio.setToolTip(
            "把改写后的结果保存回当前 hip（文件仍在原地，素材已进包目录），"
            "参数写成 $HIP/<包名>/assets/… 或 $JOB/<包名>/assets/…")
        self._save_copy_radio.toggled.connect(self._on_targets_changed)
        self._overwrite_radio.toggled.connect(self._on_targets_changed)
        mode_row.addWidget(self._save_copy_radio)
        mode_row.addWidget(self._overwrite_radio)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # 预览表
        self._table = QTableWidget(0, 7, self)
        self._table.setHorizontalHeaderLabels(
            ["", "类型", "节点", "参数", "原路径", "目标", "状态"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.doubleClicked.connect(self._on_row_activated)
        self._table.setToolTip("双击跳转到节点；右键可打开原路径 / 复制路径")
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_CHECK, QHeaderView.Fixed)
        self._table.setColumnWidth(_COL_CHECK, 34)
        for column in (_COL_KIND, _COL_NODE, _COL_PARM, _COL_STATE):
            header.setSectionResizeMode(column, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(_COL_SRC, QHeaderView.Stretch)
        header.setSectionResizeMode(_COL_DST, QHeaderView.Stretch)
        layout.addWidget(self._table, 1)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        # 结果面板
        result_row = QHBoxLayout()
        result_row.addWidget(QLabel("执行结果"))
        self._status_label = QLabel("—")
        self._status_label.setStyleSheet("color: #888888; font-weight: bold;")
        result_row.addWidget(self._status_label)
        result_row.addStretch()
        layout.addLayout(result_row)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumHeight(120)
        self._log_view.setPlaceholderText("打包完成后在这里显示逐条结果")
        layout.addWidget(self._log_view)

        # 按钮
        btn_row = QHBoxLayout()
        self._select_all_btn = QPushButton("全选")
        self._select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        btn_row.addWidget(self._select_all_btn)
        self._clear_btn = QPushButton("清空")
        self._clear_btn.clicked.connect(lambda: self._set_all_checked(False))
        btn_row.addWidget(self._clear_btn)
        btn_row.addSpacing(16)
        self._apply_btn = QPushButton("打包")
        self._apply_btn.setObjectName("okBtn")
        self._apply_btn.setEnabled(False)
        self._apply_btn.setToolTip(
            "把勾选的文件复制到包目录，改写参数，并按所选方式保存 hip")
        self._apply_btn.clicked.connect(self._on_apply)
        btn_row.addWidget(self._apply_btn)
        self._zip_btn = QPushButton("生成 zip")
        self._zip_btn.setEnabled(False)
        self._zip_btn.setToolTip("把包目录整体压成 <包根>/<包名>.zip")
        self._zip_btn.clicked.connect(self._on_zip)
        btn_row.addWidget(self._zip_btn)
        btn_row.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _apply_styles(self):
        self.setStyleSheet(
            "QDialog { background-color: #1D1D20; }"
            "QLabel { color: #e0e0e0; background: transparent; }"
            "QLineEdit, QComboBox { background-color: #2d2d2d; color: #e0e0e0;"
            " border: 1px solid #3d3d3d; border-radius: 4px; padding: 5px 8px; }"
            "QLineEdit:focus, QComboBox:focus { border-color: #0d6399; }"
            "QComboBox QAbstractItemView { background-color: #2d2d2d;"
            " color: #e0e0e0; selection-background-color: #0d6399; }"
            "QRadioButton { color: #e0e0e0; }"
            "QPushButton { background-color: #2d2d2d; color: #e0e0e0;"
            " border: 1px solid #3d3d3d; border-radius: 4px; padding: 6px 16px; }"
            "QPushButton:hover { background-color: #3d3d3d; }"
            "QPushButton:disabled { color: #6a6a6a; }"
            "QPushButton#okBtn { background-color: #0d6399; color: white;"
            " font-weight: bold; }"
            "QPushButton#okBtn:hover { background-color: #0e7bc9; }"
            "QTableWidget { background-color: #202024; color: #e0e0e0;"
            " gridline-color: #333338; border: 1px solid #3d3d3d;"
            " border-radius: 4px; }"
            "QPlainTextEdit { background-color: #16161a; color: #c8c8c8;"
            " border: 1px solid #3d3d3d; border-radius: 4px;"
            " font-family: Consolas, monospace; font-size: 11px; }"
            "QHeaderView::section { background-color: #2a2a2e; color: #c8c8c8;"
            " border: none; border-right: 1px solid #3d3d3d; padding: 5px 8px; }"
        )

    # ------------------------------------------------------------------
    # 包目录
    # ------------------------------------------------------------------
    def _root_var(self):
        return self._root_combo.currentData() or "HIP"

    def _pkg_name(self):
        return self._pkg_edit.text().strip() or paths.PACKAGE_DIR

    def _root_var_value(self):
        """（变量名, 展开值）——给界面与错误提示用，不做可用性判断。"""
        import hou
        name = "$" + self._root_var()
        value = hou.text.expandString(name).strip()
        if len(value) > 1 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].strip()
        return name, value

    def _base_dir(self):
        """包根目录的绝对路径；$JOB 未设置/不可用时返回空串。"""
        name, value = self._root_var_value()
        if not value or value == name or not os.path.isabs(value):
            return ""
        return os.path.normpath(value.rstrip("\\/"))

    def _package_dir(self):
        return paths.package_dir_abs(self._base_dir(), self._pkg_name())

    def _check_job(self):
        """$JOB 不可用时禁用选项，避免用户选了却打包失败。

        **注意**：提示文字必须写 ToolTipRole。``setItemData(index, text)`` 默认
        写的是 UserRole，会把 ``addItem("$JOB", "JOB")`` 存的 userData 覆盖成
        一串提示文字，``currentData()`` 随后返回它，拼出的变量就成了
        ``$$JOB = D:/...``（实测踩过）。
        """
        job = self._job_value()
        usable = bool(job) and os.path.isabs(job)
        model = self._root_combo.model()
        item = model.item(1, 0) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(usable)
        if not usable and self._root_combo.currentData() == "JOB":
            self._root_combo.setCurrentIndex(0)
        tip = "未设置 $JOB" if not usable else "$JOB = {}".format(job)
        self._root_combo.setItemData(1, tip, Qt.ToolTipRole)

    def _job_value(self):
        import hou
        value = hou.text.expandString("$JOB").strip()
        if len(value) > 1 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].strip()
        return value

    # ------------------------------------------------------------------
    def _set_hint(self, text, error=False):
        color = _COLOR_BAD if error else _COLOR_DIM
        self._hint_label.setText(text)
        self._hint_label.setStyleSheet(
            "color: {}; font-size: 11px;".format(color))

    def _log(self, text):
        self._log_view.appendPlainText(text)

    def _set_status(self, text, ok=True):
        self._status_label.setText(text)
        self._status_label.setStyleSheet(
            "color: {}; font-weight: bold;".format(
                _COLOR_GOOD if ok else _COLOR_BAD))

    # ------------------------------------------------------------------
    def _on_scan(self):
        import hou

        if hou.hipFile.isNewFile():
            self._set_hint("工程还没保存：先保存 hip，$HIP 才有确定的目录。",
                           error=True)
            self._log("[失败] 工程未保存，无法扫描（$HIP 未确定）")
            return
        self._check_job()
        self._hip_dir = os.path.normpath(hou.text.expandString("$HIP"))
        self._refs, skipped = collect.collect(
            self._hip_dir, self._include_output_check.isChecked())
        self._checked = set(range(len(self._refs)))
        self._refresh_targets()
        self._update_status(skipped)
        self._report_scan(skipped)

    def _report_scan(self, skipped):
        if not self._refs:
            self._log("[失败] 没有扫到可打包的引用"
                      "（只收用户填过的文件引用与已落盘的缓存）")
            return
        missing = [ref for ref in self._refs if ref.missing]
        mode_text = "输入 + 输出" if self._include_output_check.isChecked() \
            else "输入 + 已落盘输出"
        self._log("[{}] 扫描到 {} 个引用（{}），{} 个文件".format(
            "失败" if missing else "完成", len(self._refs), mode_text,
            sum(len(ref.files) for ref in self._refs)))
        for ref in missing:
            self._log("[失败] 缺失 {}:{} → {}（手动引用了但文件不存在，"
                      "打包时跳过）".format(ref.node_path, ref.parm_name,
                                            ref.abs_path))
        if skipped:
            self._log("    （{} 个参数不是文件引用，未列出）".format(skipped))

    def _on_targets_changed(self, *_args):
        if self._filling:
            return
        self._refresh_targets()
        if self._refs:
            self._update_status()

    def _target_prefix(self):
        """包内目标前缀——取决于生成方式（写错就多一层目录）。"""
        if self._save_copy_radio.isChecked():
            # hip 会存进包目录：保存后 $HIP 就是包目录，不能再带包名
            return "$HIP"
        return "${}/{}".format(self._root_var(), self._pkg_name())

    def _refresh_targets(self):
        prefix = self._target_prefix()
        self._targets = [collect.target_for(ref, prefix, self._hip_dir)
                         for ref in self._refs]
        self._fill_table()

    def _fill_table(self):
        self._filling = True
        self._table.setUpdatesEnabled(False)
        self._table.setRowCount(len(self._refs))
        for row, ref in enumerate(self._refs):
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                           | Qt.ItemIsSelectable)
            check.setCheckState(
                Qt.Checked if row in self._checked else Qt.Unchecked)
            if ref.missing:
                state_text = "缺失"
                color = QColor(_COLOR_BAD)
            elif ref.rewrite_dir:
                state_text = "缓存目录 · {} 帧".format(len(ref.files))
                color = QColor(_COLOR_OK)
            else:
                if ref.is_sequence:
                    source_text = "序列 {} 帧".format(len(ref.files))
                else:
                    source_text = "{} 个文件".format(len(ref.files))
                state_text = "{} · {}".format(
                    "$HIP 内" if ref.in_hip else "包外", source_text)
                color = QColor(_COLOR_DIM if ref.in_hip else _COLOR_OK)
            cells = [
                check,
                QTableWidgetItem(ref.label),
                QTableWidgetItem(ref.node_path),
                QTableWidgetItem(ref.parm_name),
                QTableWidgetItem(ref.raw),
                QTableWidgetItem(self._targets[row]
                                 if row < len(self._targets) else ""),
                QTableWidgetItem(state_text),
            ]
            color = QColor(color)
            for cell in cells[1:]:
                cell.setForeground(color)
            for column, cell in enumerate(cells):
                self._table.setItem(row, column, cell)
        self._table.setUpdatesEnabled(True)
        self._filling = False

    def _on_item_changed(self, item):
        if self._filling or item.column() != _COL_CHECK:
            return
        row = item.row()
        if row < 0 or row >= len(self._refs):
            return
        if item.checkState() == Qt.Checked:
            self._checked.add(row)
        else:
            self._checked.discard(row)
        self._update_status()

    def _set_all_checked(self, checked):
        self._checked = set(range(len(self._refs))) if checked else set()
        self._filling = True
        for row in range(self._table.rowCount()):
            cell = self._table.item(row, _COL_CHECK)
            if cell is not None:
                cell.setCheckState(Qt.Checked if checked else Qt.Unchecked)
        self._filling = False
        self._update_status()

    def _selected_refs(self):
        return [ref for index, ref in enumerate(self._refs)
                if index in self._checked]

    def _update_status(self, skipped=None):
        refs = self._selected_refs()
        base = self._last_base or self._base_dir() or os.path.expanduser("~")
        plan = (collect.copy_plan(refs, base,
                                  self._last_pkg or self._pkg_name(),
                                  self._hip_dir)
                if refs else {})
        total_size = 0
        for path in plan:
            try:
                total_size += os.path.getsize(path)
            except OSError:
                pass
        parts = ["共 {} 个引用".format(len(self._refs)),
                 "选中 {}".format(len(refs)),
                 "待复制 {} 个文件（{}）".format(len(plan),
                                                paths.human_size(total_size))]
        if skipped:
            parts.append("跳过 {} 个（非文件/缺失）".format(skipped))
        self._set_hint(" · ".join(parts))
        self._apply_btn.setEnabled(bool(refs))
        self._zip_btn.setEnabled(bool(self._refs))

    # ------------------------------------------------------------------
    # 行操作
    # ------------------------------------------------------------------
    def _ref_at(self, index):
        row = index.row() if index is not None else -1
        if 0 <= row < len(self._refs):
            return self._refs[row]
        return None

    def _on_row_activated(self, index):
        ref = self._ref_at(index)
        if ref is not None:
            self._jump_to_node(ref)

    def _on_context_menu(self, pos):
        index = self._table.indexAt(pos)
        ref = self._ref_at(index)
        if ref is None:
            return
        menu = QMenu(self)
        menu.addAction("跳转到节点", lambda: self._jump_to_node(ref))
        menu.addAction("在资源管理器中打开原路径",
                       lambda: self._reveal_source(ref))
        menu.exec(self._table.viewport().mapToGlobal(pos))

    def _jump_to_node(self, ref):
        from houtools.ui import networknav
        networknav.jump_to_node(ref.node_path, prefix="Package Project")

    def _source_path(self, ref):
        if ref.files:
            return ref.files[0]
        return ref.abs_path

    def _reveal_source(self, ref):
        path = self._source_path(ref)
        if not path:
            notify.warning("该行没有可打开的磁盘路径")
            return
        try:
            reveal.reveal(path)
        except OSError as exc:
            log.warning("打开原路径失败 %s: %s", path, exc)
            notify.error("打开失败：{}".format(exc))
            return
        self._set_hint("已在资源管理器中定位：{}".format(path))

    # ------------------------------------------------------------------
    # 打包
    # ------------------------------------------------------------------
    def _on_apply(self):
        import hou

        base = self._base_dir()
        if not base:
            name, value = self._root_var_value()
            reason = "未设置" if not value else "不是绝对路径"
            self._set_hint("包根目录不可用：{} = {!r}（{}）。".format(
                name, value, reason), error=True)
            self._set_status("失败", ok=False)
            self._log("[失败] 包根目录不可用：{} = {!r}（{}）".format(
                name, value, reason))
            return
        refs = self._selected_refs()
        if not refs:
            self._set_hint("没有勾选的引用。", error=True)
            return
        if hou.hipFile.isNewFile() and self._overwrite_radio.isChecked():
            self._set_hint("工程还没保存，无法「覆盖当前工程」。", error=True)
            return
        pkg = self._pkg_name()
        root = self._root_var()
        package_dir = paths.package_dir_abs(base, pkg)
        self._last_base, self._last_pkg = base, pkg
        self._last_package_dir = package_dir
        plan = collect.copy_plan(refs, base, pkg, self._hip_dir)
        prefix = self._target_prefix()
        targets = [collect.target_for(ref, prefix, self._hip_dir)
                   for ref in refs]
        if not dialogs.question(
                self, "打包工程",
                "将把 {} 个文件复制到\n{}\n\n并把 {} 个参数改写成 ${} 相对路径。\n"
                "生成方式：{}".format(
                    len(plan), package_dir, len(refs), root,
                    "另存到包目录（原工程不动）" if self._save_copy_radio.isChecked()
                    else "覆盖当前工程")):
            return

        self._log_view.clear()
        self._log("[开始] 包目录：{}".format(package_dir))

        missing = [ref for ref in refs if ref.missing]
        for ref in missing:
            self._log("[失败] 缺失 {}:{} → {}（手动引用了但文件不存在，"
                      "已跳过）".format(ref.node_path, ref.parm_name,
                                        ref.abs_path))

        copied, copy_failures = self._copy_files(plan)
        for source, reason in copy_failures:
            self._log("[失败] 复制 {}：{}".format(source, reason))
        failed_sources = {source for source, _reason in copy_failures}

        rewritten, rewrite_failures, skipped = self._rewrite_parms(
            refs, targets, failed_sources)
        for text in rewrite_failures:
            self._log("[失败] 改写 {}".format(text))

        saved_path, save_error = self._save_scene(base, pkg)
        if save_error:
            self._log("[失败] 保存工程：{}".format(save_error))
        elif saved_path:
            self._log("[完成] 工程已保存：{}".format(saved_path))

        ok = not (copy_failures or rewrite_failures or save_error or missing)
        summary = "复制 {}/{} 个文件，改写 {}/{} 个参数".format(
            copied, len(plan), rewritten, len(refs))
        if missing:
            summary += "，缺失 {} 个".format(len(missing))
        if skipped:
            summary += "，跳过 {} 个（其文件复制失败）".format(skipped)
        if ok:
            self._set_status("完成", ok=True)
            self._log("[完成] " + summary)
            notify.success("打包完成：" + summary)
        else:
            self._set_status("失败", ok=False)
            self._log("[失败] " + summary + "（原因见上）")
            notify.error("打包有失败项：" + summary)
        self._set_hint(summary + " · 详见下方执行结果")

    def _copy_files(self, plan):
        copied = 0
        failures = []
        for source, target in plan.items():
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
                copied += 1
            except OSError as exc:
                log.warning("复制失败 %s -> %s: %s", source, target, exc)
                failures.append((source, str(exc)))
        self._log("[信息] 复制完成 {}/{}".format(copied, len(plan)))
        return copied, failures

    def _rewrite_parms(self, refs, targets, failed_sources):
        import hou

        rewritten = 0
        failures = []
        skipped = 0
        with hou.undos.group("HouTools: 打包工程"):
            for ref, target in zip(refs, targets):
                if ref.missing or (failed_sources & set(ref.files)):
                    skipped += 1
                    continue
                node = hou.node(ref.node_path)
                parm = node.parm(ref.parm_name) if node is not None else None
                if parm is None:
                    failures.append("{}:{}（节点或参数已不存在）".format(
                        ref.node_path, ref.parm_name))
                    continue
                try:
                    parm.set(target)
                    rewritten += 1
                except Exception as exc:          # noqa: BLE001 - 逐条记录
                    failures.append("{}:{}（{}）".format(
                        ref.node_path, ref.parm_name, exc))
        return rewritten, failures, skipped

    def _save_scene(self, base, pkg):
        """按所选方式保存；返回 (保存后的路径, 错误文本)。"""
        import hou

        try:
            if self._save_copy_radio.isChecked():
                hip_path = hou.hipFile.path()
                stem = os.path.splitext(os.path.basename(hip_path))[0] or "untitled"
                target = os.path.join(base, pkg, stem + "_packed.hip")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                hou.hipFile.save(file_name=target, save_to_recent_files=False)
                return target, None
            hou.hipFile.save()
            return hou.hipFile.path(), None
        except Exception as exc:                  # noqa: BLE001 - 回面板展示
            log.exception("保存工程失败")
            return "", str(exc)

    # ------------------------------------------------------------------
    def _on_zip(self):
        import hou

        base = self._last_base or self._base_dir()
        if not base:
            name, value = self._root_var_value()
            reason = "未设置" if not value else "不是绝对路径"
            self._set_hint("包根目录不可用：{} = {!r}（{}）。".format(
                name, value, reason), error=True)
            return
        pkg = self._last_pkg or self._pkg_name()
        package_dir = self._last_package_dir or paths.package_dir_abs(base, pkg)
        if not os.path.isdir(package_dir):
            self._set_hint("包目录还不存在，先点「打包」。", error=True)
            return
        zip_path = os.path.join(base, pkg + ".zip")
        hip_path = hou.hipFile.path() if not hou.hipFile.isNewFile() else ""
        try:
            count = self._write_zip(zip_path, base, package_dir, hip_path)
        except Exception as exc:                  # noqa: BLE001 - 回面板展示
            log.exception("打包 zip 失败")
            self._set_status("失败", ok=False)
            self._log("[失败] 生成 zip：{}".format(exc))
            notify.error("打包失败：{}".format(exc))
            return
        size = paths.human_size(os.path.getsize(zip_path))
        self._set_status("完成", ok=True)
        self._log("[完成] 已生成 {}（{}，{} 个文件）".format(
            zip_path, size, count))
        notify.success("已生成 {}".format(os.path.basename(zip_path)))
        self._set_hint("已生成：{}（{}）".format(zip_path, size))

    def _write_zip(self, zip_path, base, package_dir, hip_path):
        count = 0
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for root, _dirs, files in os.walk(package_dir):
                for name in files:
                    full = os.path.join(root, name)
                    arc = os.path.relpath(full, base).replace("\\", "/")
                    archive.write(full, arc)
                    count += 1
            # 覆盖模式时 hip 不在包目录里，单独带上
            if hip_path and os.path.isfile(hip_path) and \
                    not paths.is_within(hip_path, package_dir):
                archive.write(hip_path, os.path.basename(hip_path))
                count += 1
        return count
