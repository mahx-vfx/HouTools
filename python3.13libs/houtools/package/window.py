"""打包工程：把工程用到的文件收进包目录、参数改成相对写法，并生成 zip。

流程（对应渲染农场提交前的"整理工程"）：

1. **扫描**：遍历已启用节点上的文件参数，收外部引用 / 节点缓存 /
   `$HIP` 内相对引用——凡是场景真正用到的、磁盘上存在的文件；
2. **预览**：逐行「节点 / 参数 / 参数现值 / 打包后参数值 / 状态」，可勾选；
3. **打包**：后台线程把勾选的文件复制到 `<包根>/<包名>/<类型>/…`，
   完成后主线程按**所选包根**改写参数（$HIP / $JOB / 自定义路径，
   见 `_target_prefix`；帧模板 `$F4` 原样保留）；
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

from PySide6.QtCore import Qt, QEvent, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMenu, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from houtools.package import collect, paths
from houtools.ui import (
    components, dialogs, fonts as tool_fonts, notify, qss, qtcompat, reveal,
    taskbar, theme,
)

# 失败反馈一律走结果面板 + notify，不打控制台日志（houtools 日志器 DEBUG
# 级、任何 log 都会刷控制台——同 copy_output_path 的用户拍板）。

TOOL_ID = "package_project"

_COL_CHECK = 0
_COL_NODE = 1
_COL_PARM = 2
_COL_SRC = 3
_COL_DST = 4
_COL_STATE = 5
# 类型列已并入状态列（2026-11-20 用户拍板：两列信息重复——
# 「缓存（序列 50 帧）」vs「缓存目录 · 50 帧」），表格 7 列 → 6 列。

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


def _scrim_color():
    """半透明遮罩底色：来自 token，仅设 alpha（不引入裸 hex）。"""
    color = QColor(theme.color("INK_950"))
    color.setAlpha(205)
    return color


# 扫描等待遮罩的样式（token 化，无裸 hex）：半透明盖住整个窗口，中央一条
# marquee 进度条 + 提示，避免"扫描中…"文字太小让人以为卡死（用户反馈）。
_scrim = _scrim_color()
_BUSY_STYLE = """
QWidget#htScanBusy {{
    background-color: rgba({r}, {g}, {b}, {a});
}}
QLabel#htScanBusyLabel {{
    color: {TEXT};
    font-size: 13px;
}}
QProgressBar#htScanBusyBar {{
    min-height: 8px;
    max-height: 8px;
    border: none;
    background-color: {INK_800};
}}
""".format(r=_scrim.red(), g=_scrim.green(), b=_scrim.blue(),
           a=_scrim.alpha(), **theme.TOKENS)


class _ScanBusyOverlay(QWidget):
    """覆盖整个窗口的扫描等待遮罩：半透明底 + 滚动条 + 提示文字。

    挂在窗口上、随窗口尺寸变化；只在扫描阶段显示，扫描结束（完成/失败/
    取消）即隐藏。鼠标事件被遮罩吞掉，避免扫描中误触底部控件。
    """

    def __init__(self, host):
        super().__init__(host)
        self.setObjectName("htScanBusy")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setStyleSheet(_BUSY_STYLE)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignCenter)
        layout.setContentsMargins(0, 0, 0, 120)
        label = QLabel("正在扫描场景文件引用，请稍候…")
        label.setObjectName("htScanBusyLabel")
        label.setAlignment(Qt.AlignCenter)
        bar = QProgressBar(self)
        bar.setObjectName("htScanBusyBar")
        bar.setRange(0, 0)          # 0/0 = indeterminate marquee 动画
        bar.setTextVisible(False)
        bar.setFixedWidth(360)
        layout.addStretch(1)
        layout.addWidget(label)
        layout.addSpacing(14)
        layout.addWidget(bar, 0, Qt.AlignCenter)
        layout.addStretch(1)
        self.hide()

    def show_over(self):
        self.setGeometry(0, 0, self.parentWidget().width(),
                         self.parentWidget().height())
        self.raise_()
        self.show()

    def hide_over(self):
        self.hide()


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
        # 本次会话指定的 JOB 目录：未显式设置 JOB 时由 _ensure_job_root()
        # 弹窗让用户选/建，再 hou.putenv 设为会话 $JOB。窗口 Reload 重建后
        # 该字段为空，靠 hou.getenv("$JOB") 的展开值兜底识别。
        self._job_override = ""
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
        # 场景指纹：new/open 工程后窗口复用，指纹变化时清空上一工程残留
        self._scene_sig = None
        # 勾选列批量操作：鼠标按下时记录选区快照（点框会改变 selection）
        self._check_batch_rows = set()
        # 切换工程轮询（parent=本窗口，随窗口销毁；1.2s 一拍，空闲才检查）
        self._scene_poll = QTimer(self)
        self._scene_poll.setInterval(1200)
        self._scene_poll.timeout.connect(self._poll_scene)

        self._build_ui()
        self.setStyleSheet(STYLE_SHEET)
        tool_fonts.apply(self)
        taskbar.apply_appwindow_flags(self)
        # 表格字号缩放基线（2026-11-20 用户要求：长列表可放大缩小查看）
        self._table_default_pt = self._table.font().pointSize() or 9
        self._scan_busy = _ScanBusyOverlay(self)   # 扫描等待遮罩（最后加，置顶）
        self._set_default_package_name()
        self._set_hint("点「扫描」收集当前工程用到的文件。")
        self._set_status("—")
        self._log("等待扫描…")
        self._scene_poll.start()

    def _current_scene_sig(self):
        """当前场景指纹（未保存 / hip 路径）——new/open 后用于清空残留。"""
        import hou
        try:
            is_new = bool(hou.hipFile.isNewFile())
            path = "" if is_new else hou.hipFile.path()
        except Exception:
            is_new, path = True, ""
        return paths.scene_signature(path, is_new)

    def _sync_scene(self):
        """场景切换检测：工程切换完成后清空上一工程的表格/日志/扫描结果。

        window_manager 让工具窗口跨工程复用；若不清理，用户打开另一个 hip
        后仍看到上一工程的引用列表与路径（2026-11-20 用户实测反馈）。
        触发时机：显示时、点扫描时、以及 `_scene_poll` 定时轮询（切工程
        完成时窗口仍开着，光靠前两者不够）。worker 运行中不复位，避免打断
        正在进行的复制/改写。
        """
        if self._active_worker() is not None:
            return
        sig = self._current_scene_sig()
        if self._scene_sig is None:
            self._scene_sig = sig
            return
        if sig == self._scene_sig:
            return
        previous = self._scene_sig
        self._scene_sig = sig
        dropped = len(self._refs)
        self._refs = []
        self._targets = []
        self._plan = {}
        self._layout_notes = []
        self._conflicts = []
        self._checked = set()
        self._pack_session = None
        self._hip_dir = ""
        # 真正清掉表格里的旧行——之前只清了内部状态，界面仍显示上一工程
        # 的「参数现值 / 打包后参数值」（用户反馈的根因）。
        self._filling = True
        self._table.setRowCount(0)
        self._filling = False
        self._set_default_package_name()
        self._log_view.clear()
        self._set_hint("已切换工程：点「扫描」收集当前工程用到的文件。")
        self._set_status("—")
        self._log("[信息] 检测到工程切换：{} → {}".format(
            self._scene_label(previous), self._scene_label(sig)))
        self._log("[信息] 已清空上一工程的 {} 条引用、复制计划、打包会话"
                  "与包目录路径；zip 已失效，请重新点「扫描」。".format(dropped))

    @staticmethod
    def _scene_label(sig):
        """场景指纹 → 可读文字（日志用）。"""
        if not sig:
            return "（未知）"
        kind, path = sig
        return "未保存的新工程" if kind == "new" else (path or "（空路径）")

    def _poll_scene(self):
        """`_scene_poll` 回调：窗口可见且空闲时比对场景指纹。

        用窗口内 QTimer 而非 hou.hipFile 全局事件回调——后者在 Reload /
        窗口销毁后会残留对已删除 Qt 对象的引用（RuntimeError）。QTimer 的
        parent 是本窗口，随窗口一起销毁，零生命周期风险；「切换工程完成后」
        由加载结束后的下一拍检测到，符合用户要求。
        """
        if not self.isVisible() or self._active_worker() is not None:
            return
        try:
            self._sync_scene()
        except Exception:
            # Houdini 正在加载 hip 的瞬间 HOM 可能暂不可用；忽略这一拍，
            # 下一拍（1.2s 后）再比，不要在 UI 事件循环里抛异常。
            pass

    def _set_default_package_name(self, force=False):
        """包名默认取「工程名-pack」（用户要求）；已手改则不覆盖，除非 force。

        无 hou 环境（冒烟测试）回落默认包名，不 import 失败。
        """
        current = self._pkg_edit.text().strip() if hasattr(self, "_pkg_edit") else ""
        default_like = current in (paths.PACKAGE_DIR, "") or current.endswith("-pack")
        if not force and current and not default_like:
            return
        hip_path = ""
        try:
            import hou
            if not hou.hipFile.isNewFile():
                hip_path = hou.hipFile.path()
        except Exception:
            pass
        self._pkg_edit.setText(paths.default_package_name(hip_path))

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
        self._root_combo.addItem("自定义路径", "CUSTOM")
        self._root_combo.setToolTip(
            "$HIP = 工程目录；$JOB = 项目根；自定义路径 = 任意绝对目录"
            "（包将写到该目录下）")
        self._root_combo.currentIndexChanged.connect(self._on_root_changed)
        top.addWidget(self._root_combo)
        # 自定义路径输入（选中"自定义路径"时启用）
        self._custom_root_edit = QLineEdit()
        self._custom_root_edit.setPlaceholderText("输入绝对目录，如 D:/projects/packs")
        self._custom_root_edit.setMinimumWidth(220)
        self._custom_root_edit.setEnabled(False)
        self._custom_root_edit.textChanged.connect(self._on_targets_changed)
        top.addWidget(self._custom_root_edit)
        self._custom_root_btn = QPushButton("浏览…")
        self._custom_root_btn.setEnabled(False)
        self._custom_root_btn.setToolTip("选择一个已存在的绝对目录作为包根")
        self._custom_root_btn.clicked.connect(self._on_browse_root)
        top.addWidget(self._custom_root_btn)
        top.addSpacing(12)
        top.addWidget(QLabel("包名"))
        self._pkg_edit = QLineEdit(paths.PACKAGE_DIR)
        self._pkg_edit.setFixedWidth(180)
        self._pkg_edit.textChanged.connect(self._on_targets_changed)
        top.addWidget(self._pkg_edit)
        top.addStretch()
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
            "打包后参数按所选包根开头：$HIP → $HIP/…（不带包名，保存后 "
            "$HIP 就是包目录）；$JOB → $JOB/<包名>/…；自定义路径 → <路径>/<包名>/…")
        self._overwrite_radio = QRadioButton("覆盖当前工程")
        self._overwrite_radio.setToolTip(
            "把改写后的结果保存回当前 hip（文件仍在原地，素材已进包目录），"
            "参数按所选包根开头：$HIP/<包名>/…、$JOB/<包名>/… "
            "或 <自定义路径>/<包名>/…")
        self._save_copy_radio.toggled.connect(self._on_targets_changed)
        self._overwrite_radio.toggled.connect(self._on_targets_changed)
        mode_row.addWidget(self._save_copy_radio)
        mode_row.addWidget(self._overwrite_radio)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # 预览表
        self._table = QTableWidget(0, 6, self)
        self._table.setHorizontalHeaderLabels(
            ["", "节点", "参数", "参数现值", "打包后参数值", "状态"])
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Windows 资源管理器风格多选（2026-11-20 用户拍板）：Ctrl 加选 /
        # Shift 连选 / 拖拽框选；选中多行后点任一勾选框整批一起切换。
        self._table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.itemChanged.connect(self._on_item_changed)
        self._table.setContextMenuPolicy(Qt.CustomContextMenu)
        self._table.customContextMenuRequested.connect(self._on_context_menu)
        self._table.doubleClicked.connect(self._on_row_activated)
        self._table.horizontalHeader().sectionClicked.connect(
            self._on_header_clicked)
        # viewport 接鼠标（记录点勾选框时的选区快照 + Ctrl+滚轮缩放字号）；
        # table 本身接键盘（空格 / Ctrl+A）。表格级 tooltip 仍不设——
        # 用户反馈空白区弹 tooltip 干扰视线，说明改挂在「全选」按钮上。
        self._table.viewport().installEventFilter(self)
        self._table.installEventFilter(self)
        # tooltip 只挂在具体单元项上（见 _fill_table）：鼠标停在打包项上才
        # 弹出操作说明，停在表格空白区域不再弹（用户反馈）。
        # H21 / PySide6 6.5 会把表头指针粘连成 QAbstractScrollArea 包装，
        # 取派生 API 前按 C++ 指针重包（见 ui/qtcompat）。重包失败也要
        # 保住窗口可用：只是少几列自适应策略，不能让整个工具打不开。
        header = qtcompat.rewrap(self._table.horizontalHeader(), QHeaderView)
        self._table.setColumnWidth(_COL_CHECK, 34)
        # 全部数据列 Interactive：鼠标拖列边界直接调宽（2026-11-20 用户要求）。
        # ResizeToContents / Stretch 模式 Qt 不响应拖动，之前拖不动就是它们。
        # 勾选列保持固定 34px；末列吸收余宽，其余列宽保持用户拖过的值。
        if hasattr(header, "setSectionResizeMode"):
            header.setSectionResizeMode(_COL_CHECK, QHeaderView.Fixed)
            for column in (_COL_NODE, _COL_PARM,
                           _COL_SRC, _COL_DST, _COL_STATE):
                header.setSectionResizeMode(column, QHeaderView.Interactive)
            header.setStretchLastSection(True)
        for column, width in ((_COL_NODE, 200), (_COL_PARM, 90),
                              (_COL_SRC, 300), (_COL_DST, 300)):
            self._table.setColumnWidth(column, width)
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
        self._select_all_btn.setToolTip(
            "勾选全部行。表格支持 Windows 文件夹式多选：Ctrl 加选 / "
            "Shift 连选 / 拖拽框选，选中多行后点任一勾选框或按空格可整批"
            "一起切换，Ctrl+A 全选行，右键可批量勾选/取消/反选，"
            "点表头勾选列也能全选/全不选")
        self._select_all_btn.clicked.connect(lambda: self._set_all_checked(True))
        btn_row.addWidget(self._select_all_btn)
        self._clear_btn = QPushButton("清空")
        self._clear_btn.setToolTip("取消勾选全部行")
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

    def _on_root_changed(self, *_args):
        """切换包根选项：仅"自定义路径"时启用输入框/浏览按钮。"""
        custom = self._root_var() == "CUSTOM"
        self._custom_root_edit.setEnabled(custom)
        self._custom_root_btn.setEnabled(custom)
        self._job_warned = False    # 换包根选项后可重新提醒一次
        self._on_targets_changed()

    def _on_browse_root(self):
        start = self._custom_root_edit.text().strip() or os.path.expanduser("~")
        folder = QFileDialog.getExistingDirectory(
            self, "选择包根目录", start, QFileDialog.Option.ShowDirsOnly)
        if folder:
            self._custom_root_edit.setText(os.path.normpath(folder))

    def _custom_root_value(self):
        # 右键复制路径常带首尾引号；strip('"') 已覆盖任意多对引号，无须再剥
        return self._custom_root_edit.text().strip().strip('"')

    def _root_var_value(self):
        """（变量名, 展开值）——给界面与错误提示用，不做可用性判断。"""
        import hou
        if self._root_var() == "CUSTOM":
            return "自定义路径", self._custom_root_value()
        name = "$" + self._root_var()
        value = hou.text.expandString(name).strip()
        if len(value) > 1 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].strip()
        return name, value

    def _job_state(self):
        """（实际 $JOB 路径, 是否未设置, 原因）——优先用本次会话指定的路径。

        未显式设置 JOB 时 Houdini 的展开值不可信（GUI 多为 $HOME，hython
        实测为当前工作目录），由 ``paths.resolve_job_root`` 统一判定。窗口
        不再自动回退 $HIP，而是要求用户指定 JOB 目录（2026-11-20 二次反馈）。
        """
        import hou

        override = getattr(self, "_job_override", "") or ""
        if override:
            return os.path.normpath(override), False, ""

        def expand(var):
            try:
                return hou.text.expandString(var).strip().strip('"')
            except Exception:
                return ""

        return paths.resolve_job_root(
            expand("$JOB"), expand("$HIP"), expand("$HOME"),
            os.getcwd(), os.environ.get("JOB", ""))

    def _job_needs_setup(self):
        """当前选 $JOB 且确实未设置？→ (是否需要指定, 原因)。"""
        if self._root_var() != "JOB":
            return False, ""
        _actual, needs, reason = self._job_state()
        return needs, reason

    def _base_dir(self):
        """包根目录的绝对路径；$JOB 未指定 / 自定义路径非法时返回空串。"""
        if self._root_var() == "JOB":
            actual, _needs, _reason = self._job_state()
            return os.path.normpath(actual.rstrip("\\/")) \
                if actual and os.path.isabs(actual) else ""
        name, value = self._root_var_value()
        if not value or value == name or not os.path.isabs(value):
            return ""
        return os.path.normpath(value.rstrip("\\/"))

    def _package_dir(self):
        return paths.package_dir_abs(self._base_dir(), self._pkg_name())

    def _check_job(self):
        """刷新 `$JOB` 选项的 tooltip；**始终可用**——未设置时打包前会弹窗。

        **注意**：提示文字必须写 ToolTipRole。``setItemData(index, text)`` 默认
        写的是 UserRole，会把 ``addItem("$JOB", "JOB")`` 存的 userData 覆盖成
        一串提示文字，``currentData()`` 随后返回它，拼出的变量就成了
        ``$$JOB = D:/...``（实测踩过）。
        """
        job = self._job_value()
        actual, needs, _reason = self._job_state()
        model = self._root_combo.model()
        item = model.item(1, 0) if hasattr(model, "item") else None
        if item is not None:
            item.setEnabled(True)
        if needs:
            tip = ("未显式设置 $JOB（当前展开为 {0}）；点「打包」时会要求"
                   "指定 JOB 目录".format(job))
        elif getattr(self, "_job_override", ""):
            tip = ("$JOB = {0}（本次会话指定；重启 Houdini 前请在 "
                   "houdini.env 写入 JOB = {0}）".format(actual))
        else:
            tip = "$JOB = {}".format(actual or job)
        self._root_combo.setItemData(1, tip, Qt.ToolTipRole)

    def _job_value(self):
        import hou
        value = hou.text.expandString("$JOB").strip()
        if len(value) > 1 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1].strip()
        return value

    def _ensure_job_root(self):
        """选 $JOB 且未显式设置时，弹三选一让用户指定路径；返回能否继续。

        用户拍板（2026-11-20 二次反馈）：不要静默回退 $HIP —— 直接提示
        「选择已有目录 / 创建 JOB 目录 / 取消打包」；选「创建」时手输绝对
        路径，工具只负责建目录（不让工具替用户决定位置）。确定后用
        ``hou.putenv("JOB", path)`` 让会话 $JOB 真的指向它，参数继续写
        ``$JOB/<包名>/…``。仅当前会话生效，弹窗与日志都提示重启前要写
        houdini.env（工具不擅自改用户的配置文件）。
        """
        if self._root_var() != "JOB":
            return True
        _actual, needs, _reason = self._job_state()
        if not needs:
            return True
        import hou
        pick = dialogs.choice(
            self, "未设置 $JOB",
            "本机没有显式设置 JOB 环境变量，$JOB 当前展开为 {0}。\n"
            "Houdini 会把 $JOB/… 解析到用户主目录，包会打错位置。\n\n"
            "请指定一个 JOB 目录：\n"
            "· 选择已有目录：用现有文件夹作为 JOB 根\n"
            "· 创建 JOB 目录：手输绝对路径，工具创建它\n"
            "· 取消：中止打包\n\n"
            "指定后仅对当前 Houdini 会话生效；重启前请在 houdini.env 写入 "
            "JOB = <该路径>。".format(self._job_value()),
            ["选择已有目录…", "创建 JOB 目录…", "取消"])
        if pick == 2:
            return False
        path = ""
        if pick == 0:
            start = self._hip_dir or os.path.expanduser("~")
            folder = QFileDialog.getExistingDirectory(
                self, "选择 JOB 目录", start,
                QFileDialog.Option.ShowDirsOnly)
            if folder:
                path = os.path.normpath(folder)
        else:
            text, ok = dialogs.prompt_text(
                self, "创建 JOB 目录",
                "JOB 目录绝对路径（例如 D:/VFX/job）：", "")
            if ok and text.strip():
                path = os.path.normpath(text.strip().strip('"'))
        if not path:
            return False
        if not os.path.isabs(path):
            dialogs.warn(self, "路径无效",
                         "JOB 目录必须是绝对路径：{!r}".format(path))
            return False
        try:
            os.makedirs(path, exist_ok=True)
        except OSError as exc:
            dialogs.warn(self, "创建目录失败", "{}\n{}".format(path, exc))
            return False
        try:
            hou.putenv("JOB", path.replace("\\", "/"))
        except Exception as exc:
            dialogs.warn(self, "设置会话 $JOB 失败", str(exc))
            return False
        self._job_override = path
        self._log("[信息] 已指定 JOB 目录：{}".format(path))
        self._log("[信息] 会话 $JOB 已设为该目录；仅本次 Houdini 会话有效，"
                  "重启前请在 houdini.env 写入 JOB = {}".format(path))
        self._check_job()
        self._on_targets_changed()
        return True

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
        self._sync_scene()
        if hou.hipFile.isNewFile():
            self._set_hint("工程还没保存：先保存 hip，$HIP 才有确定的目录。",
                           error=True)
            self._log("[失败] 工程未保存，无法扫描（$HIP 未确定）")
            return
        self._check_job()
        self._hip_dir = os.path.normpath(hou.text.expandString("$HIP"))
        self._drop_stale_session()
        # 「含空输出路径」勾选已移除（2026-11-20 用户拍板）：未落盘的输出
        # 既不会被复制也不会被改写，列出来只是一行缺失、无可操作价值。
        # 扫描固定只收已落盘输出；collect 的 include_output 参数保留给
        # 无窗口调用/测试。
        include_output = False
        try:
            candidates, skip_stats = collect.gather(self._hip_dir, include_output)
        except Exception as exc:                  # HOM 读取失败直接报
            self._set_status("失败", ok=False)
            self._log("[失败] 扫描（读取场景）：{}".format(exc))
            return
        self._pending_skip_stats = skip_stats
        self._set_busy(True)
        self._set_status("扫描中…")
        # 第 8 项：全窗口等待遮罩（marquee 进度条），文件检查在后台线程，
        # 主线程事件循环空闲，动画正常滚动，用户不会误以为卡死。
        self._scan_busy.show_over()
        QApplication.processEvents()
        worker = _ScanWorker(candidates, self._hip_dir, include_output)
        worker.done.connect(self._on_scan_done)
        self._retire_worker(worker)
        self._scan_worker = worker
        worker.start()

    def _on_scan_done(self, refs, skip_stats):
        worker = self._scan_worker
        self._scan_worker = None
        self._scan_busy.hide_over()
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
        self._log("[{}] 扫描到 {} 个引用（输入 + 已落盘输出），{} 个文件".format(
            "失败" if missing else "完成", len(self._refs),
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
        """包内目标前缀——由「包根选项」决定，生成方式只影响是否带包名。

        （2026-11-20 用户拍板：包根选 $HIP/$JOB/自定义路径，打包后参数就
        得分别以 $HIP/$JOB/该路径开头，不能一律写 $HIP。）

        - 包根 ``$JOB`` → ``$JOB/<包名>``；自定义路径 → ``<路径>/<包名>``：
          包目录是 ``<包根>/<包名>``，两种生成方式都要带包名才指向包内。
        - 包根 ``$HIP`` + 另存到包目录 → **不带包名**：hip 存进
          ``<原 $HIP>/<包名>/`` 后，会话的 $HIP 就变成包目录，再带一层
          包名会得到 ``<包目录>/<包名>/…``（多一级、读取不到）。
        - 包根 ``$HIP`` + 覆盖当前工程 → ``$HIP/<包名>``（hip 不动）。
        """
        root = self._root_var()
        # $JOB 未设置时不再改写成 $HIP：打包前 _ensure_job_root() 会把会话
        # $JOB 指向用户指定/创建的目录，参数写 $JOB/<包名>/… 才与实际包根
        # 同源；没指定就在 _on_apply 开头中止，不会拿 $HOME 顶替。
        base = self._base_dir() if root == "CUSTOM" else ""
        return paths.target_prefix(
            root, self._save_copy_radio.isChecked(), self._pkg_name(), base)

    def _refresh_targets(self):
        prefix = self._target_prefix()
        base = self._base_dir()
        self._targets, self._plan, self._layout_notes, self._conflicts = \
            collect.build_layout(
                self._refs, prefix, base, self._pkg_name(), self._hip_dir,
                by_category=True)
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
                # 类型列并入状态列后，缺失行保留 缓存/引用 前缀（kind 信息）
                state_text = "{}缺失".format(
                    "缓存" if ref.kind == "output" else "引用")
                color = QColor(theme.color("ERROR_TEXT"))
            elif ref.rewrite_dir:
                # 缓存目录行同样按 in_hip 分层：$HIP/geo 本就可移植，与序列行
                # 一样弱化 + 标「$HIP 内」（2026-11-20 用户反馈：basedir 是
                # $HIP/geo 却恒亮，看不出和其它行的区别）。
                state_text = ("$HIP 内 · " if ref.in_hip else "") + \
                    "缓存目录 · {} 帧".format(len(ref.files))
                color = QColor(theme.color("TEXT_3" if ref.in_hip else "TEXT"))
            else:
                if ref.token == paths.UDIM_TOKEN:
                    source_text = "UDIM {} 块".format(len(ref.files))
                elif ref.is_sequence:
                    source_text = "序列 {} 帧".format(len(ref.files))
                else:
                    source_text = "{} 个文件".format(len(ref.files))
                state_text = "{} · {}".format(
                    "$HIP 内" if ref.in_hip else "包外", source_text)
                color = QColor(theme.color("TEXT_3" if ref.in_hip else "TEXT"))
            # $HH/$HFS 指向 Houdini 安装目录的内置资源（如 $HH/geo/defgeo.bgeo
            # 默认几何体），不是工程素材；按用户要求仍列出，但状态列标「内置」
            # 便于识别，避免误以为需要随包搬运。
            if getattr(ref, "builtin", False):
                state_text = "内置 · " + state_text
            cells = [
                check,
                QTableWidgetItem(ref.node_path),
                QTableWidgetItem(ref.parm_name),
                QTableWidgetItem(ref.raw),
                QTableWidgetItem(self._targets[row]
                                 if row < len(self._targets) else ""),
                QTableWidgetItem(state_text),
            ]
            for cell in cells[1:]:
                cell.setForeground(color)
            tip = "双击跳转到节点；右键可打开原路径"
            if getattr(ref, "builtin", False):
                tip = ("Houdini 安装目录内置资源（$HH/$HFS），非工程素材；"
                       "取消勾选即可不打包。\n" + tip)
            for column, cell in enumerate(cells):
                # tooltip 按单元项挂载，只在悬停打包项时显示（用户反馈：
                # 表格级 tooltip 会在空白区域也弹，干扰视线）
                cell.setToolTip(tip)
                self._table.setItem(row, column, cell)
        self._table.setUpdatesEnabled(True)
        # 行高跟随当前字号（缩放后重扫的场景行数会变，新行也要套用）
        self._table.resizeRowsToContents()
        self._filling = False

    def _on_item_changed(self, item):
        if self._filling or item.column() != _COL_CHECK:
            return
        row = item.row()
        if row < 0 or row >= len(self._refs):
            return
        checked = item.checkState() == Qt.Checked
        # Windows 文件夹语义：选中多行后点其中一行的勾选框，整批一起切换。
        # 选区快照在鼠标按下时记录（点勾选框会改变 selection，不能在这里
        # 读 selectionModel——那时通常只剩刚点的那一行）。
        batch = getattr(self, "_check_batch_rows", None)
        self._check_batch_rows = None
        if batch and len(batch) > 1 and row in batch:
            self._set_rows_checked(batch, checked)
            return
        if checked:
            self._checked.add(row)
        else:
            self._checked.discard(row)
        self._update_status()

    def _selected_rows(self):
        """当前选中的行号集合（ExtendedSelection 下可多行）。"""
        model = self._table.selectionModel()
        if model is None:
            return set()
        return {index.row() for index in model.selectedIndexes()
                if 0 <= index.row() < self._table.rowCount()}

    def _set_rows_checked(self, rows, checked):
        """批量设置勾选状态；_filling 屏蔽 itemChanged 避免逐行回灌。"""
        self._filling = True
        for row in rows:
            if row < 0 or row >= len(self._refs):
                continue
            cell = self._table.item(row, _COL_CHECK)
            if cell is not None:
                cell.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            if checked:
                self._checked.add(row)
            else:
                self._checked.discard(row)
        self._filling = False
        self._update_status()

    def _toggle_selected_rows(self):
        """空格键：选中行全勾则整批取消，否则整批勾选（Windows 语义）。"""
        rows = self._selected_rows()
        if not rows:
            return
        self._set_rows_checked(rows, not rows.issubset(self._checked))

    def _on_header_clicked(self, section):
        """点表头勾选列：全选 / 全不选（Windows 详细信息视图的表头复选框）。"""
        if section != _COL_CHECK:
            return
        rows = set(range(self._table.rowCount()))
        self._set_rows_checked(rows, not rows.issubset(self._checked))

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
        # 用纯函数按当前勾选重算：取消勾选某项后，它的文件立刻从"待复制
        # 文件数/体积"里消失（用户反馈该项曾不更新）。plan 的键可能与
        # ref.files 大小写/分隔符不同，用 normcase 匹配兜底。
        count, total_size = collect.selection_summary(
            self._refs, self._checked, self._plan)
        parts = ["共 {} 个引用".format(len(self._refs)),
                 "选中 {}".format(len(refs)),
                 "待复制 {} 个文件（{}）".format(count,
                                                paths.human_size(total_size))]
        builtin_count = sum(1 for ref in self._refs
                            if getattr(ref, "builtin", False))
        if builtin_count:
            parts.append("内置 {} 个（$HH/$HFS，非工程素材）".format(
                builtin_count))
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
        rows = self._selected_rows()
        if not rows:
            return
        menu = QMenu(self)
        label = "所选 {} 项".format(len(rows)) if len(rows) > 1 else "该项"
        menu.addAction("勾选" + label,
                       lambda: self._set_rows_checked(rows, True))
        menu.addAction("取消勾选" + label,
                       lambda: self._set_rows_checked(rows, False))
        menu.addAction("反选" + label,
                       lambda: self._invert_rows(rows))
        if ref is not None:
            menu.addSeparator()
            menu.addAction("跳转到节点", lambda: self._jump_to_node(ref))
            menu.addAction("在资源管理器中打开原路径",
                           lambda: self._reveal_source(ref))
        menu.exec(self._table.viewport().mapToGlobal(pos))
        menu.deleteLater()

    def _invert_rows(self, rows):
        """反选指定行（右键批量操作）。"""
        self._filling = True
        for row in rows:
            if row < 0 or row >= len(self._refs):
                continue
            checked = row not in self._checked
            cell = self._table.item(row, _COL_CHECK)
            if cell is not None:
                cell.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            if checked:
                self._checked.add(row)
            else:
                self._checked.discard(row)
        self._filling = False
        self._update_status()

    # ---- 表格字号缩放（Ctrl+滚轮，仅本窗口会话内生效） ----
    _FONT_MIN_PT = 7
    _FONT_MAX_PT = 24

    def _table_pt(self):
        """当前表格字号（字体对象是唯一事实源，不另设缓存）。"""
        pt = self._table.font().pointSize()
        return pt if pt > 0 else self._table_default_pt

    def _set_table_pt(self, pt):
        pt = max(self._FONT_MIN_PT, min(self._FONT_MAX_PT, int(pt)))
        if pt == self._table.font().pointSize():
            return
        font = self._table.font()
        font.setPointSize(pt)
        self._table.setFont(font)
        self._table.resizeRowsToContents()
        self._set_hint("表格字号：{}pt（7–24，Ctrl+滚轮或右键调整）".format(pt))

    def eventFilter(self, obj, event):  # noqa: N802 — Qt 命名约定
        if obj is self._table.viewport():
            if event.type() == QEvent.Wheel \
                    and event.modifiers() & Qt.ControlModifier:
                delta = event.angleDelta().y()
                if delta:
                    self._set_table_pt(self._table_pt() +
                                       (1 if delta > 0 else -1))
                return True
            # 点勾选框「按下」时先记下选区：Qt 会在点击时把 selection 收
            # 到当前行，itemChanged 触发时再读就只剩一行了。
            if event.type() == QEvent.MouseButtonPress \
                    and event.button() == Qt.LeftButton:
                hit = self._table.indexAt(event.pos())
                self._check_batch_rows = (
                    self._selected_rows()
                    if hit.isValid() and hit.column() == _COL_CHECK else set())
            return super().eventFilter(obj, event)
        if obj is self._table and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                self._toggle_selected_rows()
                return True
            if event.key() == Qt.Key_A \
                    and event.modifiers() & Qt.ControlModifier:
                self._table.selectAll()
                return True
        return super().eventFilter(obj, event)

    def _jump_to_node(self, ref):
        from houtools.ui import networknav
        # 跳到重节点（filecache/sim/karma 等）时，Auto Update / OnMouseUp
        # 模式都会当场重烤、界面卡住（2026-11-20 用户实测）——跳转前无条件
        # 切 Manual。**setter 是 hou.setUpdateMode**：updateModeSetting 是
        # 只读 getter，传参会 TypeError——一版误当 setter 且 try/except
        # 吞错，真机才暴露（hython 验证 setUpdateMode 读回正确）。
        try:
            import hou
            if hou.updateModeSetting() != hou.updateMode.Manual:
                hou.setUpdateMode(hou.updateMode.Manual)
                self._log("[信息] 更新模式已切到 Manual（避免跳转后自动重烤"
                          "卡顿），需要恢复在播放条选回")
        except Exception as exc:
            self._log("[失败] 切 Manual 更新模式失败：{}".format(exc))
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
        # 未显式设置 $JOB 时先让用户指定/创建 JOB 目录（三选一）；取消则中止，
        # 绝不静默拿 $HIP 或 $HOME 顶替（2026-11-20 二次反馈）。
        if not self._ensure_job_root():
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
        # 复用扫描时算定的"目标唯一 + 类型分类"布局；包根/包名变化后按当前值
        # 重算，保证复制目标与参数改写目标来自同一份映射（报告 P1-01）
        all_targets, all_plan, _notes, conflicts = collect.build_layout(
            self._refs, prefix, base, pkg, self._hip_dir, by_category=True)
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
        mode_text = ("另存到包目录（原工程不动）" if self._save_copy_radio.isChecked()
                     else "覆盖当前工程")
        root_label = {"HIP": "$HIP", "JOB": "$JOB"}.get(root, base)
        # 2026-11-20 用户拍板：前缀一律跟随包根选项，不再"另存模式固定 $HIP"。
        # $HIP/$JOB 是变量写法（随包可移植），自定义包根只能写绝对路径——
        # 明确告诉用户不随包移植，不一律说"相对路径"误导。
        if root == "CUSTOM":
            path_text = "改写 {} 个参数为绝对路径（{} 下，不随包移植）".format(
                len(refs), root_label)
        else:
            path_text = "改写 {} 个参数为 {} 下的相对路径".format(
                len(refs), root_label)
        job_note = ""
        if getattr(self, "_job_override", ""):
            job_note = ("\n会话 $JOB = {}（仅本次 Houdini 会话有效；"
                        "重启前请在 houdini.env 写入 JOB = {}）".format(
                            self._job_override, self._job_override))
        if not dialogs.question(
                self, "打包工程",
                "将把 {} 个文件复制到\n{}\n\n并按文件类型归入官方工程目录"
                "（geo/tex/render/…），{}。\n"
                "生成方式：{}{}".format(
                    len(plan), package_dir, path_text, mode_text, job_note)):
            return

        self._log_view.clear()
        self._log("[开始] 包目录：{}".format(package_dir))
        if getattr(self, "_job_override", ""):
            self._log("[信息] 本次会话 $JOB = {}，参数前缀 {} 与之同源".format(
                self._job_override, prefix))
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
                           "source_hip": source_hip,
                           "mode_text": mode_text, "prefix": prefix,
                           "plan": dict(plan), "notes": list(_notes)}
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
        # 第 2 项：把本次打包明细写成日志文件放进包目录（随包一起交付）
        all_failures = (["复制 {}：{}".format(s, r) for s, r in failures]
                        + ["改写 " + t for t in rewrite_failures])
        if save_error:
            all_failures.append("保存工程：" + save_error)
        self._write_package_log(ctx, refs, targets, summary, all_failures)

    def _write_package_log(self, ctx, refs, targets, summary, failures):
        """把打包明细写到包目录内的 package_log.txt（失败不影响打包结果）。

        日志随包目录一起存在，zip 生成时会被一并归档；写入失败只在结果面板
        记一条，不改变打包成功/失败的判定。
        """
        package_dir = ctx.get("package_dir", "")
        if not package_dir or not os.path.isdir(package_dir):
            return
        try:
            from datetime import datetime
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            text = collect.build_package_log(
                refs=refs, plan=ctx.get("plan", {}), targets=targets,
                stats="[{}] {}".format("完成" if self._pack_session else "失败",
                                       summary),
                package_dir=package_dir, package_name=ctx.get("pkg", ""),
                mode_text=ctx.get("mode_text", ""), prefix=ctx.get("prefix", ""),
                failures=failures, notes=ctx.get("notes", []))
            text = stamp + " 生成\n" + text
            log_path = os.path.join(package_dir, "package_log.txt")
            with open(log_path, "w", encoding="utf-8") as handle:
                handle.write(text)
            self._log("[信息] 打包日志已写入：{}".format(log_path))
        except OSError as exc:
            self._log("[失败] 写打包日志失败：{}".format(exc))

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
        """按所选方式保存；返回 (保存后的路径, 错误文本)。

        另存模式会把会话切到 `<包>/<hip>_packed.hip`（hipFile.path 变化），
        必须同步 `_scene_sig`：否则 `_scene_poll` 会把这次打包内部产生的
        save-as 误判成「用户切换工程」，清空列表 + 作废 `_pack_session`，
        导致紧接着的「生成 zip」不可用。
        """
        import hou

        try:
            if self._save_copy_radio.isChecked():
                hip_path = hou.hipFile.path()
                stem = os.path.splitext(os.path.basename(hip_path))[0] or "untitled"
                target = os.path.join(base, pkg, stem + "_packed.hip")
                os.makedirs(os.path.dirname(target), exist_ok=True)
                hou.hipFile.save(file_name=target, save_to_recent_files=False)
                self._scene_sig = self._current_scene_sig()
                return target, None
            hou.hipFile.save()
            self._scene_sig = self._current_scene_sig()
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
                       self._save_copy_radio, self._overwrite_radio):
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

    def resizeEvent(self, event):  # noqa: N802 — Qt 命名约定
        # 等待遮罩覆盖整个窗口：窗口缩放时同步尺寸
        overlay = getattr(self, "_scan_busy", None)
        if overlay is not None and not overlay.isHidden():
            overlay.setGeometry(0, 0, self.width(), self.height())
        super().resizeEvent(event)

    def showEvent(self, event):  # noqa: N802 — Qt 命名约定
        # 重开窗口即撤销"取消并关闭"的决定（取消已在进入 pending 时请求）：
        # 清推迟标记、停轮询，窗口保持打开（同 videoseq 语义）
        if getattr(self, "_pending_close", False):
            self._pending_close = False
            if self._close_poll is not None:
                self._close_poll.stop()
        self._sync_scene()
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
