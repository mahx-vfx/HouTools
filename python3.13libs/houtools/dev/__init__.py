"""Development support: hot reloading.

This subpackage is NEVER reloaded by reloader.reload_all(), so the
reload machinery stays stable while everything under it is swapped.
Changes inside houtools.dev therefore require a Houdini restart (rare).
"""

import logging

_logger = logging.getLogger("houtools.dev")


def status(message, error=False, severity=None):
    """Show a message in Houdini's status bar (no-op outside Houdini).

    ``severity`` 取 "message"/"important"/"warning"/"error"（内部映射
    hou.severityType）；缺省按 ``error`` 布尔映射（True→Error，
    False→Message），旧调用点不受影响。入参用字符串而非 hou 枚举：
    调用点无须 import hou，映射集中在此处（dev 永不重载，改映射需重启）。
    """
    try:
        import hou

        if severity is None:
            level = hou.severityType.Error if error else hou.severityType.Message
        else:
            level = {
                "message": hou.severityType.Message,
                "important": hou.severityType.ImportantMessage,
                "warning": hou.severityType.Warning,
                "error": hou.severityType.Error,
            }[severity]
        hou.ui.setStatusMessage(message, severity=level)
    except Exception as exc:
        # 无头/非 GUI 环境属预期；其余失败留 debug 痕迹可检索
        _logger.debug("status message failed: %s", exc)
