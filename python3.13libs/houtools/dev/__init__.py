"""Development support: hot reloading.

This subpackage is NEVER reloaded by reloader.reload_all(), so the
reload machinery stays stable while everything under it is swapped.
Changes inside houtools.dev therefore require a Houdini restart (rare).
"""

import importlib
import logging

_logger = logging.getLogger("houtools.dev")


def status(message, error=False, severity=None):
    """状态反馈：委托 toast 通知（houtools.ui.notify），签名保持不变。

    全仓既有调用点零改动切换新样式；旧 severity 字符串的等级映射在
    notify.show_legacy（表只活在 notify 一处）。按调用 import_module
    取最新模块对象——dev 永不重载而 notify 会被重载，模块级 import
    会持旧引用（Anti-Pattern：dev 内 import 被重载代码）。notify 整
    条链路自带兜底（无头降级 logger），这里只兜"notify 模块本身不可
    用"：退回原生状态栏，行为与 2026-11 之前的旧实现一致。
    """
    try:
        importlib.import_module("houtools.ui.notify").show_legacy(
            message, error=error, severity=severity)
    except Exception as exc:
        _logger.warning("notify unavailable (%s), fallback to status bar", exc)
        try:
            import hou
            hou.ui.setStatusMessage(
                message,
                severity=hou.severityType.Error if error
                else hou.severityType.Message)
        except Exception:
            # 无头环境属预期；有痕可查即可
            _logger.debug("status fallback failed: %s", exc)
