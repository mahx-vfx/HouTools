"""MPlay 工具集的控制台反馈（2026-11 用户拍板：**成功只输出文件路径**）。

实机（H21 21.0.512 MPlay）确认 MPlay 的 ``hou`` 既没有 ``hou.qt`` 也没有
可用 ``hou.ui``；主程序 toast 链路在这里必然抛 AttributeError 并刷
traceback，所以 MPlay 分支绝不调用 ``houtools.dev.status``。

行为：

* ``report(msg)``（info）→ **完全静默**：中间状态不进控制台。
* ``report(msg, error=True)`` → 一条 ``logger.error`` + 尽力弹可见框
  （主程序 toast / hou.ui / hscript ``message``），不重复 print。
* ``output_path(path)`` → 成功时**只**输出这一行纯路径，无前缀无装饰。

任何一级失败都不能掀翻工具本身。
"""

from houtools.core.log import get_logger

logger = get_logger("mplaytools.feedback")


def _has_hou_qt():
    """主程序才有 hou.qt；MPlay 没有。"""
    try:
        import hou
        return getattr(hou, "qt", None) is not None
    except Exception:
        return False


def output_path(path):
    """成功出口：控制台唯一一行输出，就是成片的完整路径。"""
    print(path)


def start_notice(message):
    """左下角 toast：提示「开始转换，请稍候」（同步转码期间保持显示）。

    MPlay 没有 ``hou.qt``，但 ``ui.notify._main_window()`` 已加 Qt
    topLevelWidgets 回退；``show()`` 之后**必须** ``processEvents()``，
    否则同步转码阻塞 Qt 事件循环，toast 根本画不出来。转码结束后事件
    循环恢复，toast 按自己的定时器自然淡出。
    任何一步失败都静默跳过（不进控制台、不打断工具）。
    """
    try:
        from PySide6 import QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is None:
            return None
        hosts = [w for w in app.topLevelWidgets()
                 if w.isVisible() and w.isWindow()
                 and w.width() >= 400 and w.height() >= 300]
        if not hosts:
            return None
        import importlib
        toast = importlib.import_module("houtools.ui.notify").show(
            message, "info")
        app.processEvents()
        return toast
    except Exception:
        return None


def report(message, error=False):
    """错误反馈；info 调用静默。返回是否走了可见 UI 通道。"""
    if not error:
        return False
    logger.error(message)

    if _has_hou_qt():
        try:
            import importlib
            importlib.import_module("houtools.dev").status(
                message, error=True, severity="error")
            return True
        except Exception as exc:
            logger.debug("toast 反馈不可用：%s", exc)

    try:
        import hou
        ui = getattr(hou, "ui", None)
        if ui is not None and hasattr(ui, "displayMessage"):
            ui.displayMessage(message)
            return True
    except Exception as exc:
        logger.debug("hou.ui 反馈不可用：%s", exc)

    # MPlay 的 hscript 命令表里有 message；这是唯一可靠的可见报错通道
    try:
        import hou
        # message 取「本行余下全部文本」；$ 会被 hscript 当变量展开
        safe = (message.replace("\\", " ").replace('"', "'")
                .replace("\n", " ").replace("$", ""))
        hou.hscript("message " + safe)
        return True
    except Exception as exc:
        logger.debug("hscript message 反馈不可用：%s", exc)
    return False
