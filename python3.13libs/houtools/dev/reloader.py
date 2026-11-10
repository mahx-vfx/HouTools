"""Hot-reload every houtools module.

Invoked from the main menu (HouTools -> Reload Modules (Dev)).

Modules are reloaded in sys.modules insertion order, which is dependency
order: an import is registered before the module that imports it, so
reloading in this sequence re-executes every module against already
refreshed dependencies (constants before settings, core before tools).
"""

import importlib
import logging
import os
import sys
from pathlib import Path

from houtools.dev import status

log = logging.getLogger("houtools.dev.reloader")


def _is_dev_install(root=None) -> bool:
    """当前插件目录是否为开发环境（决定 Reload 是否可用）。

    任一满足即开发环境：

    - 插件根目录带 ``.git``：git 仓库工作树必然存在；发行 zip 的打包
      白名单永不包含 ``.git``（GitCode 页面下载的 zip 同样没有）
    - 环境变量 ``HOUTOOLS_DEV`` 置真值（``1``/``true``/``on``/``yes``，
      大小写不敏感）：手动开关，供"仓库目录被拷贝安装丢失 .git"的
      场景。**唯一设置入口是 houdini.env**（Documents/houdiniXX.X/
      houdini.env，每用户文件、不随包分发，仅启动时读取——改完需
      重启 Houdini 生效）；不支持 Windows 用户环境变量（用户拍板：
      设置路径唯一化，多入口反而难排查）。也**不允许**写进随包分发
      的 HouTools.json env 块——那等于发给所有用户、闸门失效。

    root 参数供测试注入：None 时按 ``__file__`` 推导插件根（dev/
    reloader.py 的 parents[3]，同 constants.PROJECT_ROOT 公式，但不
    import core——dev 框架不依赖业务代码）。
    """
    try:
        value = os.environ.get("HOUTOOLS_DEV", "").strip().lower()
        if value in ("1", "true", "on", "yes"):
            return True
        base = (Path(root) if root is not None
                else Path(__file__).resolve().parents[3])
        return (base / ".git").exists()
    except Exception:
        return False


def _candidates():
    """(name, module) pairs to reload, in dependency order."""
    found = []
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        if not (name == "houtools" or name.startswith("houtools.")):
            continue
        # The reload machinery must not reload itself mid-run.
        if name == "houtools.dev" or name.startswith("houtools.dev."):
            continue
        found.append((name, module))
    return found


def reload_all():
    """Reload all houtools modules. Returns a one-line summary.

    One failing module does not block the rest; a failed module is
    dropped from sys.modules so the next reload (or the next menu click
    through the dispatcher) re-imports it fresh from disk.

    发行版闸门：非开发环境（无 .git 且未设 HOUTOOLS_DEV）直接提示并
    返回，不关闭任何窗口——热加载是开发期功能，发行版点击不应产生
    任何副作用。

    有窗口拒绝关闭且未走延迟关闭协议（如文档编辑器里用户选择保留草稿）
    时整体中止重载：此时继续 reload 会让旧窗口持有旧类引用、新窗口由
    新代码重建，两套代码并存。
    """
    if not _is_dev_install():
        message = ("HouTools reload 已跳过：仅开发环境可用"
                   "（拷贝目录安装可在 houdini.env 设 HOUTOOLS_DEV=1 开启）")
        status(message, severity="warning")
        log.info(message)
        return message

    if not _close_windows():
        message = "HouTools reload 取消：有窗口拒绝关闭（未保存的编辑等），已保留现场"
        status(message, error=True)
        log.info(message)
        return message

    reloaded = []
    failures = []

    for name, module in _candidates():
        try:
            importlib.reload(module)
            reloaded.append(name)
            log.debug("reloaded %s", name)
        except Exception as exc:
            log.error("reload failed for %s", name, exc_info=True)
            failures.append((name, str(exc)))
            sys.modules.pop(name, None)

    if failures:
        detail = "; ".join("{} ({})".format(n, e) for n, e in failures)
        message = "HouTools reload: {} ok, {} FAILED - {}".format(
            len(reloaded), len(failures), detail
        )
        status(message, error=True)
    else:
        message = "HouTools reload OK ({} modules)".format(len(reloaded))
        status(message)
    log.info(message)
    return message


def _close_windows():
    """Close registered tool windows so they cannot keep pre-reload code alive.

    Deliberately imports the *currently loaded* window_manager (not a
    fresh import): its registry holds the live windows.

    返回 False 表示有窗口拒绝关闭且未走延迟关闭协议——reload_all 据
    此中止本次重载，保留窗口现场。
    """
    try:
        import houtools.ui.window_manager as window_manager
    except Exception:
        return True
    try:
        return window_manager.close_all()
    except Exception as exc:
        log.warning("closing windows failed: %s", exc)
        return False
