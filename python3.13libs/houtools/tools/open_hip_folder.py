"""Open Hip Folder: 打开当前工程文件所在的文件夹
================================================
主菜单与网络编辑器菜单的 HouTools → Open Hip Folder：用资源管理器打开
当前 hip 文件所在的文件夹。

未保存的会话**同样打开**：hipFile.path() 对未保存会话返回绝对路径
（cwd + untitled.hip，实测 H22.0.429），其 dirname 就是默认的 $HIP
文件夹（2026-11 用户指定；曾有"未保存即拒绝"版本，按 isNewFile 能力
探测实现，已翻案删除——目录守卫 os.path.isdir 保留，路径失效仍拒绝）。

入口 run() 由 MainMenuCommon.xml / NetworkViewMenu.xml 的 scriptItem
（两行薄分发器）调用；菜单结构改动需重启 Houdini，工具代码走热加载。
"""

import os

from houtools.core.log import get_logger
from houtools.dev import status

log = get_logger("tools.open_hip_folder")


def run():
    """菜单入口：资源管理器打开当前 hip 所在文件夹（未保存开默认 $HIP）。"""
    import hou

    try:
        hip_path = hou.hipFile.path()
    except Exception as exc:
        log.warning("hipFile.path failed: %s", exc)
        status("HouTools: 无法获取当前工程路径（{}）".format(exc),
               severity="error")
        return

    folder = os.path.dirname(os.path.normpath(hip_path)) \
        if hip_path.strip() else ""
    if not folder:
        status("HouTools: 无法确定工程文件夹（路径为空）", severity="warning")
        return
    if not os.path.isdir(folder):
        status("HouTools: 文件夹 {} 不存在".format(folder), severity="error")
        return
    try:
        os.startfile(folder)
    except OSError as exc:
        # 目录被删/系统拒绝等：报通知而不是把异常抛回菜单分发器
        log.warning("startfile %s failed: %s", folder, exc)
        status("HouTools: 打开 {} 失败（{}）".format(folder, exc),
               severity="error")
        return
    status("HouTools: 已打开 {}".format(folder))
