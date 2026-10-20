"""Windows 任务栏常驻：让独立工具窗口出现在任务栏，与 Houdini 主窗并列。

只做窗口级的 WS_EX_APPWINDOW（Get 取原扩展样式再 OR，不整盖，
保留其他扩展样式位）。不做 SetCurrentProcessExplicitAppUserModelID——
那是进程级设置，会连 Houdini 主窗口的任务栏分组一起改掉。
失败走 debug 日志（无 GUI / 非 Windows 环境静默跳过）。
"""

from houtools.core.log import get_logger

log = get_logger("ui.taskbar")

_GWL_EXSTYLE = -20
_WS_EX_APPWINDOW = 0x00040000


def apply_appwindow_flags(window):
    """给顶层窗口加 WS_EX_APPWINDOW；首次调用 winId() 会创建原生句柄。

    显式声明 argtypes：无声明时 ctypes 按平台 int 传参，句柄值能否转换
    取决于 PySide6 给的是无符号还是有符号解释（实测 >2^31 的正值能过、
    负值解释直接 ArgumentError 被 except 吞成静默失效）——c_void_p 对
    任何解释都正确；样式值本身是 32 位 LONG，W 版 API 足够。
    """
    import os
    if os.name != "nt":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
        user32.GetWindowLongW.restype = ctypes.c_long
        user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_long]
        user32.SetWindowLongW.restype = ctypes.c_long
        hwnd = ctypes.c_void_p(int(window.winId()))
        style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, style | _WS_EX_APPWINDOW)
    except Exception as exc:
        log.debug("apply_appwindow_flags failed: %s", exc)
