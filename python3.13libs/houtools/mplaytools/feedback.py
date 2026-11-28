"""MPlay 工具集的控制台与错误反馈。

``console`` 立即刷新输出范围诊断，``output_path`` 在成功最后输出纯路径；
``report`` 在无 ``hou.qt`` 时依次退 ``hou.ui.displayMessage`` 与 hscript
``message``，避免不可用链路刷 traceback。普通中间状态保持静默。

* ``report(msg, error=True)`` → 一条 ``logger.error`` + 尽力弹可见框，
  不重复 print；info 调用静默。
* ``output_path(path)`` → 成功时输出纯路径行，无前缀无装饰。

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


def console(message):
    """同步转码前立即输出诊断，避免 stdout 缓冲让日志延后出现。"""
    print(message, flush=True)


def output_path(path):
    """成功出口：纯路径行，无前缀无装饰。"""
    console(path)


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
