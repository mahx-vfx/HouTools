"""Copy Output Path (Ctrl+Shift+C): 复制输出目录到 OS 剪贴板
================================================================
在网络编辑器选中带输出路径参数的节点按 Ctrl+Shift+C，把该节点输出文件
**所在的文件夹**（如 ``...\\render\\TTT``，不含文件名）复制到系统剪贴板。

命中规则与 Open Cache Folder [HT] **完全同步**（2026-11 用户拍板，从
类型基名白名单改为参数命中）：节点携带 ``sopoutput`` / ``vm_picture``
/ ``picture`` / ``outputimage`` 之一即命中——右键能出 Open Cache
Folder 菜单的节点，热键就能复制，两套入口零裂缝。filecache/mantra/
karma/usdrender/rop geometry/fbx 及第三方节点（如携带相应参数的
Arnold）随之全部纳入；旧 filecache（``file`` 参数）仍不命中，与
Open Cache Folder 一致。参数清单直接复用 open_cache_folder 的
_OUTPUT_PARMS（单一事实来源，未来加参数两工具同步生效）。

取值复用其 ``expanded_output_path``（候选求值 + 变量展开 + 相对路径按
$HIP 拼接），但**不做 resolve_folder 的"上溯最近存在祖先"兜底**：目录
尚未落盘时不复制任何内容、直接红色错误通知（2026-11 用户指定）。

多选时把命中节点的目录去重后逐行合并复制，未命中节点忽略。失败反馈
只走通知模块、不打任何控制台日志（houtools 日志器 DEBUG 级，log.debug
也会进控制台）：目录不存在=红色 error，取值失败（参数为空等）=黄色
warning，均含节点路径可定位。

入口 run() 由 NetworkViewMenu.xml 的 scriptItem（含菜单点击与热键触发）
经 ``houtools.dev.dispatcher`` 调用；默认键位 Ctrl+Shift+C 由
``python3.13libs/uiready.py`` 在会话启动时 install_defaults 分配。
"""

import os

from houtools.core.log import get_logger
from houtools.dev import status

log = get_logger("tools.copy_output_path")


def is_copy_target(node) -> bool:
    """节点是否带输出路径参数（与 Open Cache Folder 同规则，smoke 直测）。

    参数存在性命中，**类型名不参与**：同一类型名可因携带参数不同而
    命中与否不同（如 karma 带 picture 命中、裸同名不带参数不命中）。
    懒 import 防热加载持旧引用（同 _output_folder 手法）。
    """
    if node is None:
        return False
    from houtools.tools import open_cache_folder

    return any(node.parm(name) is not None
               for name in open_cache_folder._OUTPUT_PARMS)


def _selected_nodes(editor):
    """编辑器当前网络（pwd）里被选中的节点。

    H22 的 HOM 没有节点级 ``selectedNodes()``（各包装类实测都没有，
    /out 的 pwd 包装甚至没有该方法——直接调即 AttributeError），选区
    只能取全局 ``hou.selectedNodes()`` 再按父级过滤：Houdini 的节点
    选区按网络记忆，父级 == pwd 的即为该编辑器里所见。路径比较不用
    包装对象等值（同路径包装不保证同一实例也不保证 __eq__ 语义）。
    """
    import hou

    pwd = editor.pwd()
    if pwd is None:
        return []
    pwd_path = pwd.path()
    return [n for n in hou.selectedNodes()
            if n.parent() is not None and n.parent().path() == pwd_path]


def _output_folder(node):
    """节点输出文件所在的文件夹（展开后的绝对路径）。

    与 resolve_folder 的差异：**不上溯**最近存在的祖先——目录未落盘
    （缓存还没写出来）按 FileNotFoundError 报错、不复制（用户指定）；
    取值层错误（参数为空/无候选参数）原样透传 RuntimeError。
    """
    from houtools.tools import open_cache_folder

    folder = os.path.dirname(open_cache_folder.expanded_output_path(node))
    if not os.path.isdir(folder):
        raise FileNotFoundError(folder)
    return folder


def run(**kwargs):
    """网络编辑器菜单 / 热键入口：复制选中命中节点的输出目录。"""
    import hou
    # 编辑器定位复用 paste_as_object_merge 的解析（kwargs > 鼠标下方 >
    # 任意一个；懒 import 防热加载持旧引用，同 paste_bbox 手法）
    from houtools.tools.paste_as_object_merge import _target_editor

    editor = _target_editor(kwargs)
    if editor is None:
        status("HouTools: 未找到网络编辑器，无法复制输出目录",
               severity="warning")
        return

    targets = [n for n in _selected_nodes(editor)
               if is_copy_target(n)]
    if not targets:
        status("HouTools: 没有（或选中的不是）带输出路径参数的节点"
               "（与右键 Open Cache Folder 同规则），无法复制输出目录",
               severity="warning")
        return

    folders, failures = [], []   # failures: (message, severity)
    for node in targets:
        try:
            folder = _output_folder(node)
        except RuntimeError as exc:
            # 取值层失败（参数为空等）：反馈只走通知模块，消息已含节点
            # 路径与原因，不是静默吞掉；houtools 日志器是 DEBUG 级、任何
            # log 都会进控制台——用户明确要求这类可自行修复的状态不刷
            # 控制台，故不打日志
            failures.append((str(exc), "warning"))
            continue
        except FileNotFoundError as exc:
            failures.append(("输出目录不存在：{}（节点 {}）".format(
                exc.args[0], node.path()), "error"))
            continue
        if folder not in folders:
            folders.append(folder)

    if folders:
        try:
            hou.ui.copyTextToClipboard("\n".join(folders))
        except Exception as exc:
            # 系统拒绝等：报通知而不是把异常抛回菜单分发器
            log.warning("copyTextToClipboard failed: %s", exc)
            status("HouTools: 写入剪贴板失败（{}）".format(exc),
                   severity="error")
            return

    if not folders:
        # 全部失败：一个都不复制，报第一条（红色=目录不存在，黄色=取值失败）
        message, severity = failures[0]
        if len(failures) > 1:
            message += "（另有 {} 个节点同样失败）".format(len(failures) - 1)
        status("HouTools: " + message, severity=severity)
    elif failures:
        status("HouTools: 已复制 {} 个输出目录，{} 个节点失败（{}）".format(
            len(folders), len(failures), failures[0][0]), severity="warning")
    elif len(folders) == 1:
        status("HouTools: 已复制 {}".format(folders[0]))
    else:
        status("HouTools: 已复制 {} 个输出目录".format(len(folders)))
