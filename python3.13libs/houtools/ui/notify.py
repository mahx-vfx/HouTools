"""Toast 通知（主窗左下角，全仓状态反馈的统一出口）。

结构与定稿决策（2026-11 用户逐项拍板，实机调试数据见 dev-notes/notify.md）：
- 顶层 Tool 无边框小窗挂 hou.qt.mainWindow() 名下：不进任务栏、不抢焦点
  （WA_ShowWithoutActivating）、随主窗最小化隐藏；渐透走 windowOpacity
  （系统合成通道——QGraphicsOpacityEffect 撞 QSS、QSS background 撞
  半透明顶层窗，两者在 Houdini Qt 上都不可靠）
- 底色由 paintEvent 手绘圆角矩形（半透明顶层窗上 QSS background 不渲染）
- 渐透期间槽位保留（原地淡出），动画结束真正消失后整摞才下滑补位——
  补位若在渐透开始时做，上方 toast 会恰好压住正在渐透的 toast，看起来
  像"瞬间消失"（实测：不透明 toast y=1003 盖住 65% 透明的 y=1004）
- 时长：保持 1s（error 3s）+ 渐透固定 1s；hover 暂停倒计时、点击立即渐透
- 动画：入场自下 8px 上滑（无淡入，用户拍板）+ 补位下滑 170ms OutCubic，
  走可重定向的 pos 动画（`_target_y` 记账，连发打断不丢余量）
- 同屏上限 4 条；宽 min 220；文字左对齐（尾随 addStretch 收多余宽度）
- 徽标 SVG 在 icons/toast_*.svg 走 ui.icons.svg_icon 缓存，失败回退文字
  glyph；字体走 ui.fonts.apply()（Houdini Qt 不渲染 COLR 彩色 emoji，
  只出单色轮廓，别再用 emoji 当图标）
- 原生状态栏**不是**通知通道（2026-11 用户拍板：toast 与状态栏文本不
  得同时出现，初版的镜像已删）——statusbar 只在 dev 的"notify 不可用"
  兜底里出现
- 无头（无 QApplication）降级 logger；非主线程经 hou.hdefereval 派发
  主线程；任何一步失败只记日志——通知永不打断宿主操作

houtools.dev.status 是既有调用点的稳定入口：按调用 import_module 取最
新模块对象（dev 永不重载而本模块会被重载，持引用会拿到旧类），签名与
旧 severity 字符串的映射见 _LEGACY_SEVERITY。
"""

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.log import get_logger
from houtools.ui.fonts import apply as _apply_tool_font
from houtools.ui.icons import svg_icon

logger = get_logger("ui.notify")

# ---- 等级配置（总时长 = 保持 + 渐透，渐透恒定 1s）
HOLD_MS = {"info": 1000, "success": 1000, "warning": 1000, "error": 3000}
FADE_MS = 1000
MAX_VISIBLE = 4
MIN_WIDTH = 220

# ---- 动画节奏（入场无淡入只上滑，用户拍板；补位可重定向）
_ENTER_MS = 130        # 入场上滑时长
_ENTER_SLIDE_PX = 8    # 入场上滑距离
_SHIFT_MS = 170        # 补位下滑时长

COLORS = {
    "success": "#285C43",
    "info": "#0d6399",
    "warning": "#80601E",
    "error": "#d1283e",
}
_TEXT_COLOR = "#FFFFFF"

# svg_icon 失败（QtSvg 缺失/坏文件）时的文字 glyph 兜底
_GLYPHS = {"success": "✓", "info": "i", "warning": "⚠", "error": "✕"}

# 旧 dev.status severity 字符串 → 新等级（迁移期映射，调用点后续逐步改
# success()/warning() 等显式语义；漏接的一律落 info）
_LEGACY_SEVERITY = {"message": "info", "important": "info",
                    "warning": "warning", "error": "error"}

# 可见 toast 登记表（bottom→top，渐透中的也占位）——本模块会被 reload，
# 模块体重跑时沿用旧集合（try/except NameError 守卫，window_manager.
# _closing 同款），旧 toast 实例跨重载继续跑自己的动画：方法里引用的
# 模块级常量在同一 module dict 里取到新值，无需迁移
try:
    _ACTIVE
except NameError:
    _ACTIVE = []


def show(message, level="info", duration=None):
    """弹一条 toast；返回实例（可 .dismiss 前手动 _fadeout），失败返回 None。

    duration 为总时长 ms（含渐透），None 用等级默认（2s，error 4s）。
    无头返回 None 只记日志；非主线程派发主线程。
    """
    app = QtWidgets.QApplication.instance()
    if app is None:
        _log_only(level, message)
        return None
    if QtCore.QThread.currentThread() is not app.thread():
        _dispatch_to_main(message, level, duration)
        return None
    try:
        return _Toast(message, level, duration)
    except Exception:
        # toast 链路任何一步失败（无 mainWindow/字体/图标/几何）：只记日
        # 志——通知不能打断它所伴随的工具动作；原生状态栏不是通知通道
        logger.warning("toast 创建失败", exc_info=True)
        return None


def success(message, duration=None):
    return show(message, "success", duration)


def info(message, duration=None):
    return show(message, "info", duration)


def warning(message, duration=None):
    return show(message, "warning", duration)


def error(message, duration=None):
    return show(message, "error", duration)


def show_legacy(message, error=False, severity=None):
    """dev.status 的委托入口：旧签名（error 布尔 + severity 字符串）→ 等级。

    映射表只活在 notify 一处（dev 不留副本，smoke test 固化对照）。
    """
    level = "error" if error else _LEGACY_SEVERITY.get(severity, "info")
    return show(message, level=level)


def clear():
    """立即收掉当前全部可见 toast（各自走渐透收尾）。"""
    for t in list(_ACTIVE):
        t._fadeout()


def _log_only(level, message):
    """无 GUI 时的降级出口：等级映射到日志级别。"""
    {"info": logger.info, "success": logger.info,
     "warning": logger.warning, "error": logger.error
     }.get(level, logger.info)("%s", message)


def _dispatch_to_main(message, level, duration):
    """后台线程调用：经 hdefereval 派发主线程重建 toast（Qt 单线程约束）。"""
    try:
        import hou
        hou.hdefereval.executeDeferred(
            lambda: show(message, level, duration))
    except Exception:
        # 无 hou 的嵌入/测试场景：退日志，与无头同款
        _log_only(level, message)


def _main_window():
    """取主窗；无头/异常环境抛 RuntimeError，由 show() 的兜底接住。"""
    import hou
    win = hou.qt.mainWindow()
    if win is None:
        raise RuntimeError("hou.qt.mainWindow() is None (headless?)")
    return win


def _hold_ms(level, duration):
    """渐透前的保持时长；duration 语义是总时长（含渐透），下限 500ms。"""
    total = duration if duration is not None else (
        HOLD_MS.get(level, 1000) + FADE_MS)
    return max(total - FADE_MS, 500)


def _level_icon(level):
    """16px 逻辑尺寸的等级徽标（32 档位按 2x 缩）；渲染失败返回 None。"""
    icon = svg_icon("toast_%s.svg" % level)
    if icon is None:
        return None
    pm = icon.pixmap(32, 32)
    pm.setDevicePixelRatio(2.0)
    return pm


def _toast_cap_victims():
    """同屏上限的强收名单：只数"存活"（未进入渐透）的 toast。

    渐透中的 toast 槽位保留约 1s，期间仍留在 _ACTIVE 里——若用
    "while len(_ACTIVE) >= MAX_VISIBLE: _ACTIVE[0]._fadeout()" 推进，
    撞上已在渐透（_fadeout 对其是 no-op）的栈底就永不推进，主线程死循
    环卡死 Houdini（2026-11 连发"剪贴板文本失效"提示实测）。名单语义：
    新 toast 入栈后存活数 ≤ MAX_VISIBLE。
    """
    alive = [t for t in _ACTIVE if not getattr(t, "_closing", False)]
    excess = len(alive) - (MAX_VISIBLE - 1)
    return alive[:excess] if excess > 0 else []


class _Toast(QtWidgets.QFrame):
    """单条 toast。生命期：保持（可 hover 暂停/点击跳过）→ 原地渐透
    （槽位保留）→ _gone 真正消失后整摞补位 + deleteLater。"""

    def __init__(self, message, level="info", duration=None):
        super().__init__(_main_window(),
                         QtCore.Qt.WindowType.Tool
                         | QtCore.Qt.WindowType.FramelessWindowHint
                         | QtCore.Qt.WindowType.WindowStaysOnTopHint)
        self._bg = COLORS.get(level, COLORS["info"])
        self._level = level if level in COLORS else "info"
        self.setObjectName("HtToast")
        # WA_TranslucentBackground：圆角外透明（逐像素 alpha 走 DWM 合成）
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QLabel { color: %s; background: transparent; }"
                           % _TEXT_COLOR)

        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(10, 5, 12, 5)
        lay.setSpacing(8)
        pm = _level_icon(self._level)
        ic = QtWidgets.QLabel()
        if pm is not None:
            ic.setPixmap(pm)
            ic.setFixedSize(16, 16)
        else:
            ic.setText(_GLYPHS.get(self._level, "i"))
            ic.setStyleSheet("font-weight: bold;")
        lay.addWidget(ic)
        self._text = QtWidgets.QLabel(message)
        self._text.setAlignment(QtCore.Qt.AlignmentFlag.AlignLeft
                                | QtCore.Qt.AlignmentFlag.AlignVCenter)
        lay.addWidget(self._text)
        lay.addStretch(1)         # 多余宽度收在右侧 → 文字紧跟图标、左对齐

        # 项目字体（换族保字号，见 fonts.apply）；正文固定 9pt ≈ 定稿的
        # 12px 观感，在 apply 之后覆写以免 QSS/QFont 相互覆盖
        _apply_tool_font(self)
        f = self._text.font()
        f.setPointSize(9)
        self._text.setFont(f)

        self.adjustSize()
        self.setMinimumWidth(MIN_WIDTH)
        self.setFixedHeight(self.height())

        self._closing = False
        self._target_y = None      # 逻辑目标位：位移账本（连发重定向不丢余量）
        self._pos_anim = None      # 在飞的 pos 动画（入场/补位共用一条通道）

        # 同屏上限（见 _toast_cap_victims：渐透中的不参与强收）
        for t in _toast_cap_victims():
            t._fadeout()
        _ACTIVE.append(self)
        self._place()
        # 入场：自下 8px 上滑（无淡入）；show 前先挪到起点，避免闪跳
        self.move(self.x(), self.y() + _ENTER_SLIDE_PX)
        self.show()
        self.raise_()
        self._slide_to(self._target_y, _ENTER_MS)

        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fadeout)
        self._timer.start(_hold_ms(self._level, duration))

    # ---- 手绘底色（半透明顶层窗上 QSS background 不渲染，必须 paintEvent）
    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(self._bg))
        # 右/下各收 1px：抗锯齿边不贴窗口边界被裁
        p.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 3, 3)

    # ---- 布局：左下角第一条贴状态栏，向上堆叠（顶层窗用全局坐标）；
    #      终点记入 _target_y（入场动画的落点、后续补位的记账基准）
    def _place(self):
        y = _main_window().height()
        for t in _ACTIVE:
            y -= t.height() + 6
        self._target_y = y + 6
        self.move(_main_window().mapToGlobal(QtCore.QPoint(0, self._target_y)))

    # ---- 位移通道：入场与补位共用，可重定向。逻辑目标位先记账、动画
    #      停旧起新从当前插值位置续飞——连发打断不丢余量（坑表 #14）
    def _slide_to(self, end_y, duration):
        self._target_y = end_y
        if self._pos_anim is not None:
            self._pos_anim.stop()
        anim = QtCore.QPropertyAnimation(self, b"pos", self)
        anim.setDuration(duration)
        anim.setStartValue(self.pos())
        anim.setEndValue(QtCore.QPoint(self.x(), end_y))
        anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        anim.start()
        self._pos_anim = anim

    def _shift_down(self, delta):
        """补位：逻辑目标位下移 delta 并平滑续飞。"""
        if self._target_y is None:    # 无账本（理论不可达）：退瞬移
            self.move(self.x(), self.y() + delta)
            return
        self._slide_to(self._target_y + delta, _SHIFT_MS)

    # ---- 渐透：原地淡出，不动槽位、不补位（补位在 _gone）
    def _fadeout(self):
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        anim = QtCore.QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(FADE_MS)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.finished.connect(self._gone)
        anim.start()

    # ---- 收尾：真正消失后才从登记表摘除，上方平滑下滑补位
    def _gone(self):
        if self in _ACTIVE:
            i, h = _ACTIVE.index(self), self.height()
            _ACTIVE.remove(self)
            for t in _ACTIVE[i:]:
                t.raise_()    # 动画期间保持在顶（move/pos 动画可能压 z 序）
                shift = getattr(t, "_shift_down", None)
                if shift is not None:
                    shift(h + 6)
                else:         # reload 过渡期的旧实例无此方法：退瞬移，1-3s 自愈
                    t.move(t.x(), t.y() + h + 6)
        self.deleteLater()

    # ---- hover 暂停倒计时（渐透开始后不再响应）；点击立即进入渐透
    def enterEvent(self, event):
        if self._closing:
            return
        self._left = self._timer.remainingTime() if self._timer.isActive() else None
        self._timer.stop()

    def leaveEvent(self, event):
        if self._closing:
            return
        if getattr(self, "_left", None) is not None:
            # 离开后至少再给 800ms，避免误触即走立刻消失
            self._timer.start(max(self._left, 800))

    def mousePressEvent(self, event):
        self._fadeout()
