"""Development support: hot reloading.

This subpackage is NEVER reloaded by reloader.reload_all(), so the
reload machinery stays stable while everything under it is swapped.
Changes inside houtools.dev therefore require a Houdini restart (rare).
"""

import logging

_logger = logging.getLogger("houtools.dev")


def status(message, error=False):
    """Show a message in Houdini's status bar (no-op outside Houdini)."""
    try:
        import hou

        hou.ui.setStatusMessage(
            message,
            severity=hou.severityType.Error if error else hou.severityType.Message,
        )
    except Exception as exc:
        # 无头/非 GUI 环境属预期；其余失败留 debug 痕迹可检索
        _logger.debug("status message failed: %s", exc)
