"""打包工程：把工程用到的文件收进包目录、参数改成相对写法，并生成 zip。

流程（对应渲染农场提交前的"整理工程"）：

1. **扫描**：遍历已启用节点上的文件参数，收外部引用 / 节点缓存 /
   `$HIP` 内相对引用——凡是场景真正用到的、磁盘上存在的文件；
2. **预览**：逐行「类型 / 节点 / 参数 / 原路径 / 目标」，可勾选；
3. **打包**：后台线程把勾选的文件复制到 `<包根>/<包名>/assets/<展平源目录>/`，
   完成后主线程改写成 `$HIP/…` 或 `$JOB/…`（帧模板 `$F4` 原样保留）；
4. **生成方式**（二选一）：
   - **覆盖当前工程**：改写后保存当前 hip（文件仍在原地）；
   - **另存到包目录**（默认）：改写后另存为 `<包名>/<hip>_packed.hip`，
     磁盘上的原工程不动；
5. **生成 zip**：后台线程把包目录整体压成 `<包根>/<包名>.zip`；
6. **结果面板**：完成/失败 + 复制、改写、保存的逐条失败原因。

线程模型：复制/压缩是纯文件 IO，放 `_CopyWorker`/`_ZipWorker`（QThread，
协作式取消——文件边界检查标志，对齐 Automation 的取消语义）；参数改写
与 hipFile.save 必须主线程（HOM 非线程安全），由 worker 的 done 信号
（队列连接自动回主线程）触发。改写/保存不进 worker。

样式：品牌 token 体系——`qss.window_qss(_EXTRA_QSS)`（表格/单选钮等基础
表没有的控件在 _EXTRA_QSS 里按 token 补），根 objectName=`htWindow`，
主按钮走 components.PrimaryButton（htRole=primary）。
"""

from __future__ import annotations

import os
import shutil
import zipfile

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit, QPushButton,
    QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
)

from houtools.package import collect, paths
from houtools.ui import (
    components, dialogs, fonts as tool_fonts, notify, qss, reveal, taskbar,
    theme,
)

# 失败反馈一律走结果面板 + notify，不打控制台日志（houtools 日志器 DEBUG
# 级、任何 log 都会刷控制台——同 copy_output_path 的用户拍板）。

TOOL_ID = "package_project"

_COL_CHECK = 0
_COL_KIND = 1
_COL_NODE = 2
_COL_PARM = 3
_COL_SRC = 4
_COL_DST = 5
_COL_STATE = 6

# 扫描排除原因 → 展示文案（collect.collect 返回的统计键）
_SKIP_LABELS = {
    "noise": "目录/记账路径",
    "not_written": "未落盘输出",
    "expr": "表达式驱动",
}

# ---------------------------------------------------------------------------
# 样式：基础表（qss.window_qss）没有的控件按 token 补在这里
# ---------------------------------------------------------------------------
_EXTRA_QSS = """
QRadioButton {{
    color: {TEXT_2};
    spacing: 6px;
    background: transparent;
}}
QTableWidget {{
    background-color: {INK_900};
    alternate-background-color: {INK_850};
    color: {TEXT_2};
    gridline-color: {LINE};
    border: 1px solid {LINE};
    border-radius: 8px;
    selection-background-color: {ACCENT_SOFT};
    selection-color: {TEXT};
}}
QHeaderView::section {{
    background-color: {INK_850};
    color: {TEXT_3};
    border: none;
    border-right: 1px solid {LINE};
    border-bottom: 1px solid {LINE};
    padding: 5px 8px;
}}
QTableCornerButton::section {{
    background-color: {INK_850};
    border: none;
}}
QLabel#pkgHintLabel, QLabel#pkgStatusLbl {{
    font-size: 11px;
}}
QPlainTextEdit#pkgLogView {{
    font-family: "Consolas", "Courier New", monospace;
}}
""".format(**theme.TOKENS)

STYLE_SHEET = qss.window_qss(_EXTRA_QSS)


def _repolish(widget):
    """动态属性（htTextRole）改过后让 QSS 属性选择器重新解析。"""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)


class _Cancelled(Exception):
    """_ZipWorker 的协作式取消信号（zip 写到一半，只在文件边界抛）。"""


# 运行中的复制/压缩 worker 的进程级保留引用。必须活过 Reload：关闭路径
# 会把窗口 deleteLater，实例属性随之失去引用根，运行中的 QThread 被 GC
# 析构即 Qt6 qFatal（同 videoseq/window_manager._closing 的守卫语义）。
# worker 的内建 finished 到达后自动移出。
try:
    _LIVE_WORKERS
except NameError:
    _LIVE_WORKERS = set()


class _CopyWorker(QThread):
    """后台线程：按计划复制素材进包目录（纯文件 IO，不碰 hou）。

    协作式取消：单个 copy2 不可打断，只能在文件边界检查 cancel 标志
    （对齐 Automation 的取消语义）；每个文件发一次 progress，结束发
    done（取消/失败同样发 done，由窗口按 worker.cancelled 分流）。
    """

    progress = Signal(int, int)       # 已复制数, 总数
    # 注意：完成信号不可叫 finished——会覆盖 QThread 内建的 finished
    # （retire 兜底依赖内建信号区分"线程收尾"与"业务完成"）
    done = Signal(int, list)          # 已复制数, [(源路径, 原因), ...]

    def __init__(self, plan):
        super().__init__()
        self._items = list(plan.items())
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        copied = 0
        failures = []
        for source, target in self._items:
            if self.cancelled:
                break
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(source, target)
                copied += 1
            except OSError as exc:
                failures.append((source, str(exc)))
            self.progress.emit(copied, len(self._items))
        self.done.emit(copied, failures)


class _ScanWorker(QThread):
    """后台线程：对 gather() 的候选做文件系统检查（不碰 hou）。

    扫描拆两段：主线程 gather（HOM），本线程 resolve（isfile/glob/isdir），
    大工程或网络盘扫描不再卡住 GUI。
    """

    done = Signal(object, object)     # refs, skip_stats（error 时 stats 带 error）

    def __init__(self, candidates, hip_dir, include_output):
        super().__init__()
        self._candidates = candidates
        self._hip_dir = hip_dir
        self._include_output = include_output
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        try:
            refs, stats = collect.resolve(
                self._candidates, self._hip_dir, self._include_output,
                should_cancel=lambda: self.cancelled)
        except Exception as exc:                  # 错误文本回结果面板展示
            self.done.emit([], {"error": str(exc)})
            return
        self.done.emit(refs, stats)


class _ZipWorker(QThread):
    """后台线程：包目录整体压成 zip。

    先写 ``<zip>.part``、成功后 ``os.replace`` 原子替换——取消/失败不会
    在正式路径留半截 zip 被下次误当成品（os.replace 同盘原子）。
    归档前用 realpath 检查边界：包内指向包外的符号链接/junction 跳过，
    不把本机私有文件卷进提交农场的 zip（报告 P2-02）。
    """

    done = Signal(int, int, str)      # 文件数, 跳过的链接数, 错误文本

    def __init__(self, zip_path, base, package_dir, hip_path):
        super().__init__()
        self.zip_path = zip_path
        self._base = base
        self._package_dir = package_dir
        self._hip_path = hip_path
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def run(self):
        tmp_path = self.zip_path + ".part"
        count = 0
        skipped = 0
        real_pkg = os.path.realpath(self._package_dir)
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
                for root, dirs, files in os.walk(self._package_dir):
                    if self.cancelled:
                        raise _Cancelled()
                    # 目录级链接：os.walk 默认不跟随，但要计数并显式摘掉，
                    # 否则用户看不到「跳过」，且 followlinks 语义一变就泄漏
                    for name in list(dirs):
                        full_dir = os.path.join(root, name)
                        if not collect.archivable(full_dir, real_pkg):
                            skipped += 1
                            dirs.remove(name)
                    for name in files:
                        if self.cancelled:
                            raise _Cancelled()
                        full = os.path.join(root, name)
                        if not collect.archivable(full, real_pkg):
                            skipped += 1       # 指向包外的链接：跳过，不泄漏
                            continue
                        arc = os.path.relpath(full, self._base).replace("\\", "/")
                        archive.write(full, arc)
                        count += 1
                # 覆盖模式时 hip 不在包目录里，单独带上：hip 是用户当前工程
                # 本身（不是包内链接），由 hou.hipFile.path() 给出，故意不做
                # 边界检查；否则覆盖模式打包必然缺工程文件。
                hip = self._hip_path
                if hip and os.path.isfile(hip) and \
                        not paths.is_within(hip, self._package_dir):
                    if self.cancelled:
                        raise _Cancelled()
                    archive.write(hip, os.path.basename(hip))
                    count += 1
        except _Cancelled:
            self._discard(tmp_path)
            self.done.emit(0, 0, "")
            return
        except Exception as exc:                  # 错误文本回结果面板展示
            self._discard(tmp_path)
            self.done.emit(0, 0, str(exc))
            return
        try:
            os.replace(tmp_path, self.zip_path)
        except OSError as exc:
            self._discard(tmp_path)
            self.done.emit(0, 0, str(exc))
            return
        self.done.emit(count, skipped, "")

    @staticmethod
    def _discard(path):
        # 清理 .part 临时文件：已不存在/被占用时留着无碍（.part 不是正式名）
        try:
            os.remove(path)
        except OSError:
            pass


class PackageProjectWindow(QDialog):
    """工程打包对话框。"""

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Window)
        self.setObjectName("htWindow")
        self.setWindowTitle("Package Project")
        self.setMinimumSize(1040, 680)
        self.resize(1260, 840)

        self._refs = []
        self._targets = []
        self._plan = {}
        self._layout_notes = []
        self._conflicts = []
        self._checked = set()
        self._filling = False
        self._hip_dir = ""
        # 打包会话记录：只有"复制+改写+保存"全部成功才登记，zip 只认它
        # （防止换 hip 后沿用上一个工程的快照，报告 P1-02）
        self._pack_session = None
        # 扫描/复制/压缩 worker（QThread）：None = 空闲；_apply_ctx 是复制
        # 完成后主线程继续改写/保存所需的上下文
        self._scan_worker = None
        self._copy_worker = None
        self._zip_worker = None
        self._apply_ctx = None
        # 延迟关闭协议（window_manager）：worker 运行中关窗先取消、隐藏，
        # 线程退出后再真正 close + 自删（见 is_close_deferred/closeEvent）
        self._pending_close = False
        self._close_poll = None

        self._build_ui()
        self.setStyleSheet(STYLE_SHEET)
        tool_fonts.apply(self)
        taskbar.apply_appwindow_flags(self)
        self._set_hint("点「扫描」收集当前工程用到的文件。")
        self._set_status("—")
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
        self._hint_label.setObjectName("pkgHintLabel")
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

        # 结果面板
        result_row = QHBoxLayout()
        result_row.addWidget(QLabel("执行结果"))
        self._status_label = QLabel("—")
        self._status_label.setObjectName("pkgStatusLbl")
        result_row.addWidget(self._status_label)
        result_row.addStretch()
        layout.addLayout(result_row)
        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("pkgLogView")
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
        self._apply_btn = components.PrimaryButton("打包")
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

    # ------------------------------------------------------------------
    # 包目录
    # ------------------------------------------------------------------
    def _root_var(self):
        return self._root_combo.currentData() or "HIP"

    def _pkg_name(self):
        # 包名是用户输入、直接进 os.path.join：必须净化（非法字符换 _、
        # 防 ".." 穿越），否则会写出包根之外（见 paths.sanitize_pkg_name）
        return paths.sanitize_pkg_name(self._pkg_edit.text())

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
        self._hint_label.setText(text)
        self._hint_label.setProperty(
            "htTextRole", "error" if error else "muted")
        _repolish(self._hint_label)

    def _log(self, text):
        self._log_view.appendPlainText(text)

    def _set_status(self, text, ok=None):
        """执行状态：ok=True 完成 / ok=False 失败 / None 进行中或中性。"""
        self._status_label.setText(text)
        self._status_label.setProperty(
            "htTextRole", {True: "success", False: "error"}.get(ok, "muted"))
        _repolish(self._status_label)

    # ------------------------------------------------------------------
    def _on_scan(self):
        import hou

        if self._active_worker() is not None:
            return
        if hou.hipFile.isNewFile():
            self._set_hint("工程还没保存：先保存 hip，$HIP 才有确定的目录。",
                           error=True)
            self._log("[失败] 工程未保存，无法扫描（$HIP 未确定）")
            return
        self._check_job()
        self._hip_dir = os.path.normpath(hou.text.expandString("$HIP"))
        self._drop_stale_session()
        include_output = self._include_output_check.isChecked()
        try:
            candidates, skip_stats = collect.gather(self._hip_dir, include_output)
        except Exception as exc:                  # HOM 读取失败直接报
            self._set_status("失败", ok=False)
            self._log("[失败] 扫描（读取场景）：{}".format(exc))
            return
        self._pending_skip_stats = skip_stats
        self._set_busy(True)
        self._set_status("扫描中…")
        worker = _ScanWorker(candidates, self._hip_dir, include_output)
        worker.done.connect(self._on_scan_done)
        self._retire_worker(worker)
        self._scan_worker = worker
        worker.start()

    def _on_scan_done(self, refs, skip_stats):
        worker = self._scan_worker
        self._scan_worker = None
        self._set_busy(False)
        pending = getattr(self, "_pending_skip_stats", {}) or {}
        self._pending_skip_stats = {}
        if worker is not None and worker.cancelled:
            self._set_status("已取消", ok=False)
            self._log("[失败] 已取消：扫描未完成")
            return
        if isinstance(skip_stats, dict) and skip_stats.get("error"):
            self._set_status("失败", ok=False)
            self._log("[失败] 扫描（检查文件）：{}".format(skip_stats["error"]))
            return
        stats = dict(pending)
        for key, count in (skip_stats or {}).items():
            stats[key] = stats.get(key, 0) + count
        self._refs = list(refs or [])
        self._checked = set(range(len(self._refs)))
        self._refresh_targets()
        self._update_status()
        self._report_scan(stats)

    def _session_matches(self, session):
        """打包会话是否仍属于当前 hip（P1-02：换工程后旧快照必须失效）。"""
        import hou

        return collect.session_matches_hip(
            session, hou.hipFile.path(), hou.hipFile.isNewFile())

    def _drop_stale_session(self):
        if self._pack_session is not None and \
                not self._session_matches(self._pack_session):
            self._pack_session = None

    def _report_scan(self, skip_stats):
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
        if skip_stats:
            detail = "、".join(
                "{} {} 个".format(_SKIP_LABELS.get(key, key), count)
                for key, count in sorted(skip_stats.items()))
            self._log("    （另有 {} 个疑似文件路径未列出：{}）".format(
                sum(skip_stats.values()), detail))

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
        base = self._base_dir()
        self._targets, self._plan, self._layout_notes, self._conflicts = \
            collect.build_layout(
                self._refs, prefix, base, self._pkg_name(), self._hip_dir)
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
                color = QColor(theme.color("ERROR_TEXT"))
            elif ref.rewrite_dir:
                state_text = "缓存目录 · {} 帧".format(len(ref.files))
                color = QColor(theme.color("TEXT"))
            else:
                if ref.is_sequence:
                    source_text = "序列 {} 帧".format(len(ref.files))
                else:
                    source_text = "{} 个文件".format(len(ref.files))
                state_text = "{} · {}".format(
                    "$HIP 内" if ref.in_hip else "包外", source_text)
                color = QColor(theme.color("TEXT_3" if ref.in_hip else "TEXT"))
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

    def _update_status(self):
        refs = self._selected_refs()
        plan = {}
        for index, ref in enumerate(self._refs):
            if index not in self._checked:
                continue
            for source in ref.files:
                target = self._plan.get(source)
                if target:
                    plan[source] = target
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
        if self._layout_notes:
            parts.append("重名已改名 {} 处".format(len(self._layout_notes)))
        if self._conflicts:
            parts.append("布局冲突 {} 处（禁止打包）".format(len(self._conflicts)))
        self._set_hint(" · ".join(parts), error=bool(self._conflicts))
        self._apply_btn.setEnabled(bool(refs) and not self._conflicts)
        # zip 只对"本次成功打包"的会话开放（换 hip / 没打包过都不行）
        self._zip_btn.setEnabled(self._pack_session is not None)
        self._zip_btn.setToolTip(
            "把包目录整体压成 <包根>/<包名>.zip"
            if self._pack_session is not None
            else "先完成一次打包，才能生成 zip")

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
            notify.error("打开失败：{}".format(exc))
            return
        self._set_hint("已在资源管理器中定位：{}".format(path))

    # ------------------------------------------------------------------
    # 打包（复制在后台线程；改写/保存回主线程）
    # ------------------------------------------------------------------
    def _on_apply(self):
        import hou

        if self._active_worker() is not None:
            return
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
        prefix = self._target_prefix()
        # 复用扫描时算定的"目标唯一"布局；包根/包名变化后按当前值重算，
        # 保证复制目标与参数改写目标来自同一份映射（报告 P1-01）
        all_targets, all_plan, _notes, conflicts = collect.build_layout(
            self._refs, prefix, base, pkg, self._hip_dir)
        if conflicts:
            self._log_view.clear()
            self._log("[失败] 打包布局有冲突，已中止（未复制/改写任何文件）：")
            for text in conflicts:
                self._log("  - " + text)
            self._set_status("失败", ok=False)
            self._set_hint("布局冲突 {} 处：详见下方执行结果".format(
                len(conflicts)), error=True)
            return
        refs = self._selected_refs()
        selected = [(i, ref) for i, ref in enumerate(self._refs)
                    if i in self._checked]
        targets = [all_targets[i] for i, _ref in selected]
        plan = {}
        for _i, ref in selected:
            for source in ref.files:
                if source in all_plan:
                    plan[source] = all_plan[source]
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

        self._set_busy(True)
        self._set_status("打包中…")
        # 开始新的打包：旧的会话记录立即作废（失败/取消后 zip 不可用）
        self._pack_session = None
        source_hip = "" if hou.hipFile.isNewFile() else hou.hipFile.path()
        self._apply_ctx = {"refs": refs, "targets": targets,
                           "missing": missing, "total": len(plan),
                           "base": base, "pkg": pkg,
                           "package_dir": package_dir,
                           "source_hip": source_hip}
        worker = _CopyWorker(plan)
        worker.progress.connect(self._on_copy_progress)
        worker.done.connect(self._on_copy_done)
        self._retire_worker(worker)
        self._copy_worker = worker
        worker.start()

    def _on_copy_progress(self, copied, total):
        self._set_hint("复制 {}/{} …".format(copied, total))

    def _on_copy_done(self, copied, failures):
        worker = self._copy_worker
        self._copy_worker = None
        ctx = self._apply_ctx or {}
        self._apply_ctx = None
        refs = ctx.get("refs", [])
        targets = ctx.get("targets", [])
        total = ctx.get("total", 0)
        self._set_busy(False)
        self._update_status()

        if worker is not None and worker.cancelled:
            self._set_status("已取消", ok=False)
            self._log("[失败] 已取消：复制 {}/{}".format(copied, total))
            notify.info("打包已取消")
            return

        for source, reason in failures:
            self._log("[失败] 复制 {}：{}".format(source, reason))
        self._log("[信息] 复制完成 {}/{}".format(copied, total))
        failed_sources = {source for source, _reason in failures}

        rewritten, rewrite_failures, skipped = self._rewrite_parms(
            refs, targets, failed_sources)
        for text in rewrite_failures:
            self._log("[失败] 改写 {}".format(text))

        saved_path, save_error = self._save_scene(
            ctx.get("base", ""), ctx.get("pkg", ""))
        if save_error:
            self._log("[失败] 保存工程：{}".format(save_error))
        elif saved_path:
            self._log("[完成] 工程已保存：{}".format(saved_path))

        missing = ctx.get("missing", [])
        ok = not (failures or rewrite_failures or save_error or missing)
        summary = "复制 {}/{} 个文件，改写 {}/{} 个参数".format(
            copied, total, rewritten, len(refs))
        if missing:
            summary += "，缺失 {} 个".format(len(missing))
        if skipped:
            summary += "，跳过 {} 个（其文件复制失败）".format(skipped)
        if ok:
            # 只有整条链路成功才登记会话；zip 只认这份记录（P1-02）
            self._pack_session = {
                "base": ctx.get("base", ""),
                "pkg": ctx.get("pkg", ""),
                "package_dir": ctx.get("package_dir", ""),
                "source_hip": ctx.get("source_hip", ""),
                "saved_hip": saved_path or ctx.get("source_hip", ""),
            }
            self._set_status("完成", ok=True)
            self._log("[完成] " + summary)
            notify.success("打包完成：" + summary)
        else:
            self._pack_session = None
            self._set_status("失败", ok=False)
            self._log("[失败] " + summary + "（原因见上）")
            notify.error("打包有失败项：" + summary)
        self._update_status()
        self._set_hint(summary + " · 详见下方执行结果")

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
                except Exception as exc:          # 逐条记录，批次继续
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
        except Exception as exc:                  # 原因回结果面板展示
            # 失败反馈不打控制台：结果面板已含原因（houtools 日志器 DEBUG
            # 级、任何 log 都刷控制台——同 copy_output_path 的用户拍板）
            return "", str(exc)

    # ------------------------------------------------------------------
    def _on_zip(self):
        import hou

        if self._active_worker() is not None:
            return
        session = self._pack_session
        if session is None:
            self._set_hint("还没有成功打包过（或已换工程），先点「打包」。",
                           error=True)
            return
        if not self._session_matches(session):
            self._pack_session = None
            self._set_hint("当前工程已切换，旧打包结果作废：请重新打包。",
                           error=True)
            self._update_status()
            return
        base = session.get("base", "")
        pkg = session.get("pkg", "")
        package_dir = session.get("package_dir", "")
        if not base or not os.path.isdir(package_dir):
            self._set_hint("包目录还不存在，先点「打包」。", error=True)
            return
        zip_path = os.path.join(base, pkg + ".zip")
        hip_path = hou.hipFile.path() if not hou.hipFile.isNewFile() else ""
        self._set_busy(True)
        self._set_status("压缩中…")
        worker = _ZipWorker(zip_path, base, package_dir, hip_path)
        worker.done.connect(self._on_zip_done)
        self._retire_worker(worker)
        self._zip_worker = worker
        worker.start()

    def _on_zip_done(self, count, skipped, error):
        worker = self._zip_worker
        self._zip_worker = None
        self._set_busy(False)
        self._update_status()
        zip_path = worker.zip_path if worker is not None else ""
        if worker is not None and worker.cancelled:
            self._set_status("已取消", ok=False)
            self._log("[失败] 已取消：zip 未生成")
            return
        if error:
            self._set_status("失败", ok=False)
            self._log("[失败] 生成 zip：{}".format(error))
            notify.error("打包失败：{}".format(error))
            return
        try:
            size = paths.human_size(os.path.getsize(zip_path))
        except OSError:
            size = "?"
        if count == 0:
            self._set_status("空包", ok=False)
            self._log("[警告] 已生成 {}，但没有归档任何文件（跳过 {} 个指向"
                      "包外的链接）".format(zip_path, skipped))
            self._set_hint("zip 为空，请检查包目录内容与链接边界", error=True)
            return
        self._set_status("完成", ok=True)
        text = "[完成] 已生成 {}（{}，{} 个文件）".format(zip_path, size, count)
        if skipped:
            text += "，跳过 {} 个指向包外的链接".format(skipped)
        self._log(text)
        notify.success("已生成 {}".format(os.path.basename(zip_path)))
        self._set_hint("已生成：{}（{}）".format(zip_path, size))

    # ------------------------------------------------------------------
    # 忙态与生命周期（worker 运行中锁界面 + 延迟关闭协议）
    # ------------------------------------------------------------------
    def _set_busy(self, busy):
        """复制/压缩进行中锁界面：计划已固定，中途改勾选/包根/生成方式会
        与在跑的复制错位。解锁后由调用方 _update_status 恢复按钮态。"""
        for widget in (self._scan_btn, self._apply_btn, self._zip_btn,
                       self._select_all_btn, self._clear_btn, self._table,
                       self._root_combo, self._pkg_edit,
                       self._include_output_check, self._save_copy_radio,
                       self._overwrite_radio):
            widget.setEnabled(not busy)

    def _active_worker(self):
        for worker in (self._scan_worker, self._copy_worker, self._zip_worker):
            if worker is not None and worker.isRunning():
                return worker
        return None

    def _retire_worker(self, worker):
        """给运行中的 worker 保引用，直到其线程真正退出。

        引用一旦无人持有，Python 回收会析构运行中的 QThread（Qt6 下
        qFatal 崩溃）。保留集合在模块级（_LIVE_WORKERS）：Reload 关闭
        路径会把本窗口 deleteLater，实例属性保不住引用——必须活过
        Reload。worker 的内建 finished 到达后自动移出保留集合。
        """
        if worker is None or worker in _LIVE_WORKERS:
            return
        _LIVE_WORKERS.add(worker)
        worker.finished.connect(
            lambda w=worker: _LIVE_WORKERS.discard(w))

    def is_close_deferred(self):
        """window_manager.close_all 用：closeEvent 是否被推迟。

        worker 运行中一律算延迟关闭——无论用户已确认取消（_pending_close）
        还是刚在 Reload 的 close_all 里被关闭。close_all 对返回 True 的
        窗口移入 _closing 持引用、不做 deleteLater：本窗口是运行线程的
        信号接收方（done 回调做改写/保存），删了它运行中的 QThread 会被
        GC 析构（Qt6 qFatal，Houdini 直接崩）。
        """
        if bool(getattr(self, "_pending_close", False)):
            return True
        return self._active_worker() is not None

    def showEvent(self, event):  # noqa: N802 — Qt 命名约定
        # 重开窗口即撤销"取消并关闭"的决定（取消已在进入 pending 时请求）：
        # 清推迟标记、停轮询，窗口保持打开（同 videoseq 语义）
        if getattr(self, "_pending_close", False):
            self._pending_close = False
            if self._close_poll is not None:
                self._close_poll.stop()
        super().showEvent(event)

    def closeEvent(self, event):  # noqa: N802 — Qt 命名约定
        # Reload 的 close_all 触发的关闭不弹确认（模态框会卡住 reload，
        # 交互上也是错的）：直接取消 worker 走延迟关闭（同 videoseq）
        from houtools.ui import window_manager as _wm
        reload_closing = bool(getattr(_wm, "_closing_all", False))
        worker = self._active_worker()
        if worker is None:
            super().closeEvent(event)
            return
        if self._pending_close:
            # 已处于延迟关闭中（隐藏窗口再被 close_all 触发）：维持推迟
            event.ignore()
            return
        if not reload_closing and not dialogs.question(
                self, "打包进行中",
                "打包仍在进行，取消并关闭窗口？",
        ):
            event.ignore()
            return
        # 协作式取消（文件边界生效），随后隐藏窗口、QTimer 轮询线程退出
        # 再真正关——直接 wait() 会卡 UI，线程对象也不能先于运行中的
        # 线程销毁
        worker.cancel()
        self._pending_close = True
        self.hide()
        event.ignore()
        if self._close_poll is None:
            self._close_poll = QTimer(self)
            self._close_poll.timeout.connect(self._poll_worker_done)
        self._close_poll.start(200)

    def _poll_worker_done(self):
        """worker 线程退出后完成延迟关闭。"""
        if self._active_worker() is not None:
            return
        if getattr(self, "_pending_close", False):
            self._pending_close = False
            if self._close_poll is not None:
                self._close_poll.stop()
            self.close()
            # 延迟关闭的窗口必须自删：window_manager.close_all 对声明了
            # is_close_deferred 的窗口移入 _closing 持引用、不再兜底
            # deleteLater（防 GC 析构运行中的 QThread），收尾在这里销毁
            self.deleteLater()
