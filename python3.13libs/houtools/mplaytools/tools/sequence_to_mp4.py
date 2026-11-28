"""MPlay → 序列图转 MP4：中文设置面板、后台进度与取消。"""

import os
import threading

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.mplaytools import export, feedback, session
from houtools.ui import dialogs, fonts, icons, qss

try:
    _LIVE_WORKERS
except NameError:
    _LIVE_WORKERS = set()
try:
    _LIVE_CONTROLLERS
except NameError:
    _LIVE_CONTROLLERS = set()


def _main_window():
    app = QtWidgets.QApplication.instance()
    if app is None:
        return None
    windows = []
    for widget in app.topLevelWidgets():
        try:
            if (widget.isWindow() and widget.isVisible()
                    and not isinstance(widget, QtWidgets.QLabel)):
                windows.append(widget)
        except RuntimeError:
            continue
    return max(windows, key=lambda widget: widget.width() * widget.height(),
               default=None)


def _ffmpeg_tooltip():
    from houtools.videoseq.ffmpeg import find_ffmpeg
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return ("FFmpeg：未找到（转换不可用）\n"
                "请把 ffmpeg.exe 放到 HouTools 配置目录"
                "（HOUTOOLS_FFMPEG 指向处）或加入系统 PATH")
    return "FFmpeg：" + os.path.realpath(ffmpeg)


def _show_ffmpeg_tooltip(button):
    QtWidgets.QToolTip.showText(
        button.mapToGlobal(button.rect().center()), button.toolTip(), button)


def _info_button():
    button = QtWidgets.QToolButton()
    icon = icons.svg_icon("info.svg")
    if icon is not None:
        button.setIcon(icon)
    else:
        button.setText("i")
    button.setAutoRaise(True)
    button.setCursor(QtCore.Qt.PointingHandCursor)
    button.setToolTip(_ffmpeg_tooltip())
    button.clicked.connect(lambda: _show_ffmpeg_tooltip(button))
    return button


def _pair_spinboxes(defaults, minimum=-1000000000, special_text=None):
    row = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    spins = []
    for value in defaults:
        spin = QtWidgets.QSpinBox()
        spin.setButtonSymbols(QtWidgets.QAbstractSpinBox.NoButtons)
        spin.setRange(minimum, 1000000000)
        if special_text:
            spin.setSpecialValueText(special_text)
        spin.setValue(int(value))
        spin.setMinimumWidth(150)
        layout.addWidget(spin)
        spins.append(spin)
    return row, spins


def _default_resolution():
    try:
        sequence, _warnings, _cleanup = session.current_sequence(allow_dump=False)
    except session.MPlaySessionError:
        return None
    return _sequence_resolution(sequence)


class _SettingsDialog(QtWidgets.QDialog):
    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            # 子控件点击会冒泡到对话框，按全局坐标识别真正的空白落点。
            local_pos = self.mapFromGlobal(event.globalPosition().toPoint())
            try:
                clicked = self.childAt(local_pos)
                while (clicked is not None and clicked is not self
                       and clicked.focusPolicy() == QtCore.Qt.NoFocus):
                    clicked = clicked.parentWidget()
            except RuntimeError:
                # 延迟删除交接时，childAt 可能返回 C++ 端已失效的包装。
                clicked = None
            if clicked is None or clicked is self:
                focused = self.focusWidget()
                if focused is not None:
                    focused.clearFocus()
        super().mousePressEvent(event)


def _ask_range(parent=None):
    """返回设置字典；取消返回 None。"""
    defaults = session.global_frame_range() or (1, 240)
    resolution = _default_resolution()
    dialog = _SettingsDialog(parent)
    dialog.setWindowTitle("序列图转 MP4")
    dialog.setObjectName("htWindow")
    outer = QtWidgets.QVBoxLayout(dialog)
    group = QtWidgets.QGroupBox("额外编码设置")
    form = QtWidgets.QFormLayout(group)
    form.setSpacing(10)
    range_row, (start, end) = _pair_spinboxes(defaults)
    form.addRow("起始/结束", range_row)
    resolution_row, (width, height) = _pair_spinboxes(
        resolution or (0, 0), minimum=0, special_text="原始")
    width.setToolTip("0 表示保持原始宽度")
    height.setToolTip("0 表示保持原始高度")
    form.addRow("分辨率", resolution_row)
    codec_label = QtWidgets.QWidget()
    codec_layout = QtWidgets.QHBoxLayout(codec_label)
    codec_layout.setContentsMargins(0, 0, 0, 0)
    codec_layout.setSpacing(4)
    codec_layout.addStretch()
    codec_layout.addWidget(_info_button())
    codec_layout.addWidget(QtWidgets.QLabel("编码器"))
    form.addRow(codec_label, QtWidgets.QLabel("H.264"))
    outer.addWidget(group)
    buttons = QtWidgets.QDialogButtonBox(
        QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel,
        parent=dialog)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    outer.addWidget(buttons)
    fonts.apply(dialog)
    dialogs.localize_buttons(dialog)
    ok_button = _rewrap_button(buttons.button(QtWidgets.QDialogButtonBox.Ok))
    cancel_button = _rewrap_button(
        buttons.button(QtWidgets.QDialogButtonBox.Cancel))
    if ok_button is not None:
        ok_button.setText("转换")
        ok_button.setProperty("htRole", "primary")
        ok_button.setFocusPolicy(QtCore.Qt.NoFocus)
    if cancel_button is not None:
        cancel_button.setText("取消")
    dialog.setStyleSheet(qss.dialog_qss())
    start.setFocus()
    start.selectAll()
    if dialog.exec_() != QtWidgets.QDialog.Accepted:
        dialog.deleteLater()
        return None
    chosen_resolution = (width.value(), height.value())
    if chosen_resolution == (0, 0):
        chosen_resolution = None
    else:
        chosen_resolution = tuple(value if value > 0 else -1
                                  for value in chosen_resolution)
    value = {
        "frame_range": tuple(sorted((start.value(), end.value()))),
        "resolution": chosen_resolution,
    }
    dialog.deleteLater()
    return value


def _rewrap_button(button):
    return dialogs._rewrap(button, QtWidgets.QAbstractButton)


def _sequence_resolution(sequence=None):
    if sequence is None:
        return None
    if not sequence.files:
        return None
    size = QtGui.QImage(str(sequence.files[0])).size()
    if size.width() <= 0 or size.height() <= 0:
        return None
    return size.width(), size.height()


class _ExportWorker(QtCore.QThread):
    progress = QtCore.Signal(int)
    completed = QtCore.Signal(object)
    failed = QtCore.Signal(str)
    cancelled = QtCore.Signal()

    def __init__(self, sequence, output, fps, ffmpeg, resolution, cancel_event):
        super().__init__()
        self.sequence = sequence
        self.output = output
        self.fps = fps
        self.ffmpeg = ffmpeg
        self.resolution = resolution
        self.cancel_event = cancel_event

    def run(self):
        try:
            result, _codec = export.run_export(
                self.sequence, self.output, self.fps, self.ffmpeg,
                progress=self.progress.emit, resolution=self.resolution,
                cancel_event=self.cancel_event)
        except export.MPlayExportCancelled:
            self.cancelled.emit()
            return
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        self.completed.emit(result)


class _ExportController(QtCore.QObject):
    """主线程接收 worker 信号，避免后台线程直接操作 Qt 窗口。"""

    def __init__(self, worker, progress_dialog, cleanup, cancel_event):
        super().__init__()
        self.worker = worker
        self.progress_dialog = progress_dialog
        self.cleanup = cleanup
        self.cancel_event = cancel_event

    def _cleanup_sequence(self):
        if self.cleanup is not None:
            cleanup, self.cleanup = self.cleanup, None
            cleanup()

    @QtCore.Slot()
    def cancel(self):
        if self.worker.isFinished():
            return
        self.cancel_event.set()
        self.progress_dialog.setCancelButtonText("正在取消…")
        self.progress_dialog.setLabelText("正在取消转换…")

    @QtCore.Slot(object)
    def succeeded(self, result):
        self._cleanup_sequence()
        self.progress_dialog.close()
        export.reveal_file(result)
        feedback.output_path(result)

    @QtCore.Slot(str)
    def failed(self, message):
        self._cleanup_sequence()
        self.progress_dialog.close()
        feedback.report("序列图转 MP4 失败：%s" % message, error=True)

    @QtCore.Slot()
    def cancelled(self):
        self._cleanup_sequence()
        self.progress_dialog.close()

    @QtCore.Slot()
    def retired(self):
        _LIVE_WORKERS.discard(self.worker)
        _LIVE_CONTROLLERS.discard(self)
        self.worker.deleteLater()
        self.progress_dialog.deleteLater()
        self.deleteLater()


def _run_export(settings):
    """主线程准备序列，后台编码；完成打开目录并打印纯路径。"""
    from houtools.videoseq.ffmpeg import find_ffmpeg

    frame_range = settings["frame_range"]
    resolution = settings.get("resolution")
    hip = session.resolve_hip()
    hipname = session.resolve_hip_name()
    fps = session.session_fps()
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise export.MPlayExportError(
            "未找到 FFmpeg：请把 ffmpeg.exe 放到 HouTools 配置目录，"
            "或确保 hffmpeg 在 PATH / $HFS/bin。")
    output = export.versioned_output_path(hip, hipname)

    cancel_event = threading.Event()
    progress_dialog = QtWidgets.QProgressDialog(
        "正在准备 MPlay 序列…", "取消", 0, 100, _main_window())
    progress_dialog.setWindowTitle("序列图转 MP4")
    progress_dialog.setObjectName("htWindow")
    progress_dialog.setCancelButtonText("取消")
    progress_dialog.setAutoClose(False)
    progress_dialog.setAutoReset(False)
    progress_dialog.setMinimumDuration(0)
    progress_dialog.setMinimumWidth(420)
    fonts.apply(progress_dialog)
    progress_dialog.setStyleSheet(qss.dialog_qss())
    progress_dialog.canceled.connect(lambda: cancel_event.set())
    progress_dialog.show()
    QtWidgets.QApplication.processEvents()

    worker = None
    cleanup = None
    try:
        sequence, _warnings, cleanup = session.current_sequence(
            frame_range=frame_range)
        if cancel_event.is_set():
            cleanup()
            progress_dialog.close()
            progress_dialog.deleteLater()
            return
        resolution = resolution or _sequence_resolution(sequence)
        feedback.console(
            "[MPlay 输出帧范围] 请求=%s 实际=%s"
            % (frame_range, session.format_sequence_range(sequence)))
        progress_dialog.setLabelText("正在转换 MP4…")
        worker = _ExportWorker(sequence, output, fps, ffmpeg, resolution,
                               cancel_event)
        _LIVE_WORKERS.add(worker)
        controller = _ExportController(worker, progress_dialog, cleanup,
                                       cancel_event)
        _LIVE_CONTROLLERS.add(controller)
        progress_dialog.canceled.disconnect()
        progress_dialog.canceled.connect(controller.cancel)
        worker.progress.connect(progress_dialog.setValue)
        worker.completed.connect(controller.succeeded)
        worker.failed.connect(controller.failed)
        worker.cancelled.connect(controller.cancelled)
        worker.finished.connect(controller.retired)
        worker.start()
    except Exception:
        if cleanup is not None:
            cleanup()
        progress_dialog.close()
        progress_dialog.deleteLater()
        raise


def run(**_kwargs):
    if os.environ.get("HOUTOOLS_MPLAY_DEBUG_PATH"):
        feedback.console(session.diagnose_hip())
    try:
        settings = _ask_range(_main_window())
        if settings is None:
            return
        _run_export(settings)
    except Exception as exc:
        feedback.logger.exception("序列图转 MP4 执行失败")
        feedback.report("序列图转 MP4：%s" % exc, error=True)
