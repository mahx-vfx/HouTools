"""MPlay Tools 菜单分发器（与 houtools.dev.dispatcher 相互独立）。

MPlay 是独立进程，主菜单那个 Reload Modules 按钮点不到；这里改为
**每次点击按依赖序重载本工具集**，开发期改完代码直接再点菜单即可生效，
不用重启 MPlay。重载范围严格限于 mplaytools 业务模块：

    feedback → session → export → tools.<tool_id>

``houtools.videoseq.ffmpeg`` 保持稳定不重载（共用查找链）；
``houtools.dev`` 在 MPlay 里不参与反馈（无 hou.qt）。
任一模块重载失败就从 sys.modules 摘掉，下次点击重新导入。
注意 dispatcher 自身**不会**被重载，改本文件仍需重启 MPlay。
"""

import importlib
import sys

from houtools.core.log import get_logger

logger = get_logger("mplaytools.dispatcher")

_DEPENDENCIES = (
    "houtools.mplaytools.feedback",
    "houtools.mplaytools.session",
    "houtools.mplaytools.export",
)


def _fresh(module_name):
    module = sys.modules.get(module_name)
    if module is None:
        return importlib.import_module(module_name)
    try:
        return importlib.reload(module)
    except Exception as exc:
        logger.warning("重载 %s 失败，改为重新导入：%s", module_name, exc)
        sys.modules.pop(module_name, None)
        return importlib.import_module(module_name)


def run(tool_id, **kwargs):
    feedback = importlib.import_module("houtools.mplaytools.feedback")
    for dependency in _DEPENDENCIES:
        try:
            _fresh(dependency)
        except Exception as exc:
            logger.warning("加载 %s 失败：%s", dependency, exc)

    module_name = "houtools.mplaytools.tools." + tool_id
    try:
        module = _fresh(module_name)
    except Exception as exc:
        logger.error("无法加载 %s", module_name, exc_info=True)
        feedback.report("HouTools: 无法加载 %s (%s)" % (module_name, exc),
                        error=True)
        return

    entry = getattr(module, "run", None)
    if not callable(entry):
        feedback.report("HouTools: %s 没有 run() 入口" % module_name,
                        error=True)
        return
    try:
        entry(**kwargs)
    except Exception as exc:
        logger.error("MPlay tool %s crashed", tool_id, exc_info=True)
        feedback.report("HouTools: %s 执行失败 (%s) - 详见日志"
                        % (tool_id, exc), error=True)
