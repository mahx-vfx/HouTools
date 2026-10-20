"""状态栏信号闪烁：成功绿 / 失败红的背景脉冲反馈（Recipe Library 与
HDR Library 共用，2026-10-21 从 recipelib/browser.py 提取）。

QVariantAnimation 关键帧 0→0.12 峰值→0 共 900ms 淡出，背景 rgba 色 +
白字圆角（样式选择器要求目标 QLabel 的 objectName 为 statusLabel），
finished 回落窗口级样式。同色且脉冲还在进行中不重启：连发动作/平台级
重复投递只刷新文本、脉冲不中断——淡出中途重启正是「突然又亮一次再快
速消失」的病根（去重闸对失败路径不占闸，拦不住这类重复投递）；换色
（绿↔红）或动画结束才重新起脉冲。动画对象挂在自身（以传入 parent 为
父），随窗口销毁，无模块级状态，热加载安全。"""

from PySide6 import QtCore, QtGui

SUCCESS_COLOR = "#3aa655"
ERROR_COLOR = "#d1283e"


class StatusFlash(QtCore.QObject):
    """绑定一个 QLabel 状态栏的脉冲反馈器：success/error = 文本 + 闪烁。"""

    def __init__(self, label, parent=None):
        super().__init__(parent)
        self._label = label
        if not self._label.objectName():
            self._label.setObjectName("statusLabel")   # 样式选择器依赖
        self._color = None
        self._anim = None

    def success(self, text):
        """成功反馈：文本 + 绿色脉冲。"""
        self.show(text, SUCCESS_COLOR)

    def error(self, text):
        """失败反馈：文本 + 红色脉冲（只用于真实失败；前置未满足的引导
        文案不闪，由调用方保持纯文字）。"""
        self.show(text, ERROR_COLOR)

    def show(self, text, color):
        self._label.setText(text)
        self.flash(color)

    def flash(self, color, duration_ms=900):
        anim = self._anim
        if anim is not None \
                and anim.state() == QtCore.QAbstractAnimation.Running \
                and self._color == QtGui.QColor(color):
            return
        if anim is None:
            anim = QtCore.QVariantAnimation(self)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.setKeyValues([(0.0, 0.0), (0.12, 1.0), (1.0, 0.0)])
            anim.valueChanged.connect(self._on_value)
            anim.finished.connect(self._on_finished)
            self._anim = anim
        anim.stop()
        self._color = QtGui.QColor(color)
        anim.setDuration(duration_ms)
        anim.start()

    def _on_value(self, value):
        c = self._color or QtGui.QColor(SUCCESS_COLOR)
        alpha = int(200 * float(value))
        self._label.setStyleSheet(
            "QLabel#statusLabel {{ background-color: rgba({}, {}, {}, {});"
            " border-radius: 4px; color: #ffffff; }}".format(
                c.red(), c.green(), c.blue(), alpha))

    def _on_finished(self):
        self._label.setStyleSheet("")   # 回落窗口级样式（灰字无底色）
