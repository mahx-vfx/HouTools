"""在资源管理器中定位文件。

优先用 Shell API ``SHOpenFolderAndSelectItems``：直接给“文件路径”，由
shell 自己打开父目录并选中该项，路径里有空格/中文都不受影响。

**不要**用 ``subprocess.Popen(["explorer", "/select,<path>"])`` 的列表写法：
explorer 不按标准 argv 解析，Python 给整段参数加引号后它会解析成
``"/select,C:\\x y\\a.jpg"`` 一个整体，结果什么都不选中（甚至不打开）。
退路是命令行字符串形式 ``explorer /select,"<path>"``，再退就是打开目录。
"""

import os
import subprocess

from houtools.core.log import get_logger

log = get_logger("ui.reveal")


def reveal(path):
    """打开资源管理器并选中 path；path 是目录时直接打开目录。

    失败抛 OSError（调用方负责提示）。
    """
    path = os.path.normpath(path)
    if not os.path.exists(path):
        raise OSError("路径不存在：{}".format(path))
    if os.path.isdir(path):
        os.startfile(path)
        return
    try:
        _shell_select(path)
        return
    except Exception as exc:                       # noqa: BLE001 - 退路继续
        log.warning("SHOpenFolderAndSelectItems 失败：%s", exc)
    try:
        # 字符串形式（不是列表）：引号由我们自己拼，explorer 才认 /select,
        subprocess.Popen('explorer /select,"{}"'.format(path))
        return
    except OSError as exc:
        log.warning("explorer /select 失败：%s", exc)
    os.startfile(os.path.dirname(path))


def _shell_select(path):
    """Shell API 选择文件；ctypes 里所有指针参数都声明成 c_void_p 防截断。"""
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.windll.shell32
    ole32 = ctypes.windll.ole32
    ole32.CoInitialize(None)

    shell32.SHParseDisplayName.argtypes = [
        wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD, ctypes.c_void_p]
    shell32.SHParseDisplayName.restype = ctypes.c_long
    shell32.SHOpenFolderAndSelectItems.argtypes = [
        ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p, wintypes.DWORD]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

    pidl = ctypes.c_void_p()
    result = shell32.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None)
    if result != 0 or not pidl:
        raise OSError("SHParseDisplayName 失败：0x{:08X}".format(
            result & 0xFFFFFFFF))
    try:
        result = shell32.SHOpenFolderAndSelectItems(pidl, 0, None, 0)
        if result != 0:
            raise OSError("SHOpenFolderAndSelectItems 失败：0x{:08X}".format(
                result & 0xFFFFFFFF))
    finally:
        ole32.CoTaskMemFree(pidl)
