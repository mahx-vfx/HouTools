"""Registry for top-level tool windows.

Tools must create their windows through open_window() so the hot
reloader can close everything before reloading modules; otherwise open
windows would keep running pre-reload class code against stale state.
"""

from functools import partial

from houtools.core.log import get_logger

log = get_logger("ui.window_manager")

# tool_id -> QWidget
_windows = {}
# closeEvent 被 ignore（延迟关闭，如 videoseq 转换进行中）的窗口。必须
# 持有引用：registry 已清空后若无人引用，Python 侧回收会连带析构仍在
# 运行的 QThread —— Qt6 下直接 qFatal 崩溃。窗口在延迟关闭收尾时自行
# deleteLater，destroyed 信号再把条目摘掉。
#
# NameError 守卫：本模块会被 Reload Modules 重载，模块体重跑会把绑定
# 换成新空集合、让旧集合失去唯一的外部根（运行中的延迟关闭窗口随之
# 可被 GC 析构）。reload 在同一 module dict 里执行、已有名字可见，
# 守卫让重载后沿用旧集合。_windows 不需要守卫：reload 前 close_all()
# 已把它清空，重造空 dict 等价。
try:
    _closing
except NameError:
    _closing = set()

# close_all() 执行期间的标志：窗口的 closeEvent 据此区分"Reload 触发的
# 关闭"与用户手动关窗——Reload 路径不弹模态询问（模态会卡住 reload 进程，
# 交互上也是错的），直接取消在跑的工作并走延迟关闭语义（见 videoseq
# closeEvent）。普通 bool，无需 NameError 守卫：close_all 不在运行时
# 本模块才可能被重载，重跑 False 即正确初值。
_closing_all = False


def open_window(tool_id, factory):
    """Show the window registered for tool_id, building it via factory()
    when absent. Repeated calls raise the existing window instead of
    duplicating it.
    """
    window = _windows.get(tool_id)
    if window is not None:
        try:
            _show(window)
            return window
        except RuntimeError:
            # The underlying C++ widget was already deleted.
            log.debug("window %r was deleted, rebuilding", tool_id)

    window = factory()
    _windows[tool_id] = window
    _show(window)
    return window


def close_all():
    """Close and delete every registered window (used before hot reload).

    返回 False = 有窗口拒绝关闭且未声明延迟关闭协议（如 Recipe 文档
    编辑器里用户在"放弃修改"确认框选了取消）——此类窗口**不得**
    deleteLater（会销毁窗口与用户明确选择保留的草稿），留在登记表里；
    调用方（reload_all）应据此中止本次重载。

    close() 返回 False = closeEvent 被 ignore。仅当窗口显式声明
    ``is_close_deferred()``（自己安排了延迟关闭，如 videoseq 转换进行中
    先隐藏再轮询线程退出）时才移出登记表并转入 _closing——直接删会
    穿透延迟关闭、析构运行中的 QThread（Qt6 下 qFatal 崩溃）。此类
    窗口移入 _closing 持引用，由其延迟关闭收尾时自行 deleteLater。
    """
    global _closing_all
    _closing_all = True
    refused = False
    try:
        for tool_id, window in list(_windows.items()):
            try:
                if window.close():
                    window.deleteLater()
                    _windows.pop(tool_id, None)
                else:
                    deferred = getattr(window, "is_close_deferred", None)
                    if callable(deferred) and deferred():
                        _closing.add(window)
                        window.destroyed.connect(
                            partial(_closing.discard, window))
                        _windows.pop(tool_id, None)
                    else:
                        # 拒绝关闭且无延迟协议：保留窗口与登记表条目，
                        # 由 reload_all 中止重载（旧代码继续可用）
                        refused = True
            except RuntimeError:
                _closing.discard(window)
                _windows.pop(tool_id, None)
            except Exception as exc:
                log.warning("cannot close window %r: %s", tool_id, exc)
                refused = True
    finally:
        _closing_all = False
    return not refused


def _show(window):
    window.show()
    window.raise_()
    window.activateWindow()
