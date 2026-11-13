"""File Cache 路径转绝对路径（网络编辑器菜单 HouTools → File Cache Paths to Absolute）
================================================================
把**当前层级**所有 File Cache 节点（类型名按 ``::`` 切分含 ``filecache``
段——filecache / filecache::2.0 / labs::filecache 等）的路径参数展开成
绝对路径并写回。工程拷给别人、换机继续跑缓存时，摆脱"必须在同一 $HIP
下才能续写"的路径依赖：
``$HIP/geo/$HIPNAME.$OS.$F.bgeo.sc`` →
``D:/shots/SA01/geo/untitled.filecache1.$F.bgeo.sc``。

**帧变量原样保留**（2026-11 用户拍板 ``$F 不转``）：帧编号仍随播放推进；
其余变量全部冻结成字面值。

转换对象（H22.0.429 实测 filecache::2.0 的三个路径参数，``_PATH_PARMS``）：

- ``basedir``（Base Folder）：目录段，默认 ``$HIP/geo``；
- ``basename``（Base Name）：名称段，默认 ``$HIPNAME.$OS``；
- ``file``（Geometry File）：完整路径段，默认 ``$HIP/geo/$HIPNAME.$OS.$F.bgeo.sc``。

三者**相互独立**（实测 file 不由 basedir/basename 通道驱动，手工 set 后
keyframes 仍为空）；``filemethod`` 菜单只决定界面显示"组合模式
（basedir+basename+版本）"还是"直接模式（file）"，工具三个都转，哪种
模式生效都覆盖到。内部缓存 ROP 的 ``sopoutput`` 是表达式驱动、随 file
侧参数联动，不在转换之列。

各参数的转换语义：

1. **$F 家族先摘出再展开**：``hou.text.expandString`` 会按当前帧展开 $F
   （实测把 $F 冻成 1），先把 ``$F``/``$F1-9``/``$FF``/``$FF1-9``（含
   ``${F4}`` 花括号形态）换成无 $ 占位符，展开后还原；正则负向环视保证
   ``$FPS`` 这类以 F 开头的变量不被误保护、正常展开。
2. **$OS 必须先按节点名替换**：$OS 是节点局部变量，全局 expandString 对
   它展开出的是无关值（H22.0.429 实测展开成 ``Director``）而非节点名，
   直接展开会写坏路径——先用 ``node.name()`` 替换 ``$OS``/``${OS}``
   （负向环视下 ``$OSNAME`` 等带后缀形态不受影响）。$HIPNAME 等全局
   变量 expandString 展开正确（实测 untitled）。
3. **basedir/file 按完整路径处理**：展开后不是绝对路径时按 $HIP 拼接
   （与 Houdini 读文件参数的相对路径语义一致），normpath 归一并换回
   正斜杠（os.path.normpath 在 Windows 产出反斜杠），**尾斜杠按原文
   保留**（目录段与 basename/version 拼接时充当分隔符，normpath 会剥）。
   **basename 是名称段**：只展开变量成字面名（``$HIPNAME.$OS`` →
   ``untitled.filecache1``），不拼 $HIP——它本就不是路径。
4. **跳过并计入汇总（不转换）**：路径为空 / 表达式驱动（关键帧）/ 含
   反引号内联表达式（expandString 会当场求值，写入静态值等于冻结表达式
   结果）/ op: 等协议路径 / 参数锁定（PermissionError）/ 参数不存在
   （不同版本参数集不同——旧 filecache 只有 file，静默忽略不计数）。
   表达式驱动与反引号属"写静态值破坏表达式"的破坏性改写，必须拒绝。
5. 整批一个 undo 槽（``hou.undos.group``）；单参数失败在循环内消化，
   不把异常抛出 undo 组（异常穿出 group 会整组回滚），已转换的其余
   参数保持不动。

入口 run() 由 NetworkViewMenu.xml 的 scriptItem 经 ``houtools.dev.dispatcher``
调用（无独立快捷键，item id 用 houtools.networkview.* 前缀）；反馈只走
通知模块、不打控制台日志（houtools 日志器 DEBUG 级，任何 log 都会刷控制台
——自行修复的状态不刷控制台，同 copy_output_path 的用户拍板）。
"""

import os
import re

from houtools.core.log import get_logger
from houtools.dev import status

log = get_logger("tools.filecache_abs_path")

# File Cache 家族的类型段名（filecache / filecache::2.0；HOM type().name()
# 是 "基名::版本" 形态，按段判断，名字空间前缀（labs::filecache::2.0）
# 也能命中）
_TYPE_SEGMENT = "filecache"
# 路径参数三件套（filecache::2.0 的 File Path 两种模式：组合模式用
# basedir+basename、直接模式用 file；三个相互独立、都转才两种模式全覆盖）
_PATH_PARMS = ("basedir", "basename", "file")
# 名称段（basename）：不是路径，只展开变量、不按 $HIP 拼接
_BASENAME_PARM = "basename"

# 帧变量：$F / $F1-9 / $FF / $FF1-9（及 ${F4} 花括号形态）。负向环视
# (?![0-9A-Za-z_]) 防止 $FPS / $FILE 等以 F 开头的变量被误认成 $F
_FRAME_RE = re.compile(r"\$(?:F{1,2}\d?|\{F{1,2}\d?\})(?![0-9A-Za-z_])")

# 节点局部变量 $OS（\b 负边界防止 $OSNAME 等带后缀形态被误换）
_OS_RE = re.compile(r"\$(?:OS\b|\{OS\})")

# $F 占位符：无 $ 无特殊字符，expandString 原样放行；还原按序号 replace
_PLACEHOLDER = "_HTFRAME{}_"  # .format(index)


def _is_abs(path):
    """绝对路径判定，兼容 POSIX 形态（/ 开头）。

    Python 3.13 起的 os.path.isabs 在 Windows 对 "/foo" 这类单斜杠开头
    返回 False（nt 绝对路径要求盘符），而 Houdini 的文件参数把
    "/proj/..." 视为绝对路径（解析到当前盘根）——不能直接用 os.path.isabs。
    """
    return path.startswith(("/", "\\")) or os.path.isabs(path)


def _expand_keep_frames(raw):
    """变量全展开但帧变量原样保留。

    $F 家族先换成占位符再交给 hou.text.expandString（否则按当前帧展开、
    帧号被冻成常数），展开后按占位符还原。
    """
    import hou

    tokens = []

    def _stash(match):
        tokens.append(match.group(0))
        return _PLACEHOLDER.format(len(tokens) - 1)

    expanded = hou.text.expandString(_FRAME_RE.sub(_stash, raw))
    for index, token in enumerate(tokens):
        expanded = expanded.replace(_PLACEHOLDER.format(index), token)
    return expanded


def _substitute_os(raw, node):
    """把 $OS / ${OS} 替换成当前节点名的字面量。

    $OS 是节点局部变量，全局 expandString 对它展开出的是无关值（H22
    实测 "Director"）而非节点名，必须先行替换；lambda 作替换串避免
    re.sub 把节点名里的 \\ 当转义序列。
    """
    try:
        name = node.name()
    except Exception as exc:
        log.debug("node.name() failed: %s", exc)
        return raw
    return _OS_RE.sub(lambda _m: name, raw)


def _to_absolute(raw):
    """目录/完整路径段展开成绝对路径（normpath 归一 + 强制正斜杠）。

    展开后不是绝对路径时按 $HIP 拼接，与 Houdini 读文件参数的相对路径
    语义一致（同 open_cache_folder.expanded_output_path 的取值层）。
    """
    import hou

    path = _expand_keep_frames(raw)
    if not _is_abs(path):
        path = os.path.join(hou.text.expandString("$HIP"), path)
    # os.path.normpath 在 Windows 产出反斜杠；写进参数的是 Houdini 风格
    # 路径，统一换回正斜杠
    result = os.path.normpath(path).replace("\\", "/")
    # 目录段可能带尾斜杠（与 basename/version 拼接时充当分隔符）——
    # normpath 会剥掉，按原文保留
    if raw.endswith(("/", "\\")) and not result.endswith("/"):
        result += "/"
    return result


def _is_filecache(node):
    """节点类型是否属于 File Cache 家族（类型名含 filecache 段）。"""
    return _TYPE_SEGMENT in node.type().name().split("::")


def _convert(node, parm_name):
    """转换单个节点的一个路径参数，返回 (kind, text) 或 None。

    kind ∈ {"converted", "unchanged", "skip"}；converted 的 text 是写回的
    新值，skip 的 text 是原因说明（汇总通知里带节点路径与参数名展示）。
    None = 节点没有该参数（不同版本参数集不同，如旧 filecache 只有
    file）——静默忽略，不计任何数。hou.PermissionError 等写入异常由
    调用方按参数粒度捕获——单参数失败不影响批次里其余参数。
    """
    parm = node.parm(parm_name)
    if parm is None:
        return None
    if parm.keyframes():
        # 表达式驱动（关键帧/chs 链接）：写静态值会破坏表达式，跳过
        return "skip", "路径参数是表达式驱动"
    raw = parm.unexpandedString().strip()
    if not raw:
        return "skip", "路径为空"
    if "`" in raw:
        # 内联 hscript 表达式：expandString 会当场求值，写入静态值等于
        # 冻结表达式结果，跳过
        return "skip", "含内联表达式（反引号）"
    if parm_name != _BASENAME_PARM:
        if "://" in raw or raw.lower().startswith(("op:", "opdef:", "oplib:")):
            return "skip", "不是文件系统路径（op: 等协议路径）"
        if _is_abs(raw):
            return "unchanged", "已是绝对路径"

    original = raw
    raw = _substitute_os(raw, node)
    if parm_name == _BASENAME_PARM:
        # 名称段不是路径：只展开变量成字面名，不按 $HIP 拼接
        new_path = _expand_keep_frames(raw)
    else:
        new_path = _to_absolute(raw)
    # 与替换前的原值对比：$OS 被换成节点名本身就算一次有效转换
    if new_path == original:
        return "unchanged", "无需转换"
    parm.set(new_path)
    return "converted", new_path


def run(**kwargs):
    """网络编辑器菜单入口：当前层级所有 File Cache 路径转绝对路径。"""
    import hou
    # 编辑器定位复用 paste_as_object_merge 的解析（kwargs > 鼠标下方 >
    # 任意一个；懒 import 防热加载持旧引用，同 copy_output_path 手法）
    from houtools.tools.paste_as_object_merge import _target_editor

    editor = _target_editor(kwargs)
    if editor is None:
        status("HouTools: 未找到网络编辑器，无法转换 File Cache 路径",
               severity="warning")
        return
    pwd = editor.pwd()
    if pwd is None:
        status("HouTools: 网络编辑器没有可用的当前层级", severity="warning")
        return

    nodes = [node for node in pwd.children() if _is_filecache(node)]
    if not nodes:
        status("HouTools: 当前层级没有 File Cache 节点", severity="warning")
        return

    converted, unchanged, skipped = 0, 0, []
    # 整批一个 undo 槽；单参数失败在循环内消化，不把异常抛出 undo 组
    # （异常穿出 hou.undos.group 会整组回滚）
    with hou.undos.group("filecachePathsToAbsolute"):
        for node in nodes:
            for parm_name in _PATH_PARMS:
                try:
                    result = _convert(node, parm_name)
                except hou.PermissionError:
                    # 参数锁定：解锁后重试可解的自修复状态，反馈走通知不打日志
                    result = ("skip", "{} 参数被锁定".format(parm_name))
                except Exception as exc:
                    # 预期之外的写入失败：留痕（log.warning）+ 该参数跳过，
                    # 批次其余参数继续
                    log.warning("convert %s.%s failed: %s",
                                node.path(), parm_name, exc)
                    result = ("skip", "{} 写入失败（{}）".format(parm_name, exc))
                if result is None:  # 该版本没有此参数，忽略不计
                    continue
                kind, text = result
                if kind == "converted":
                    converted += 1
                elif kind == "unchanged":
                    unchanged += 1
                else:
                    skipped.append("{} {}：{}".format(
                        node.path(), parm_name, text))

    parts = []
    if converted:
        parts.append("转换 {} 个路径参数".format(converted))
    if unchanged:
        parts.append("{} 个无需转换".format(unchanged))
    if skipped:
        parts.append("跳过 {} 个".format(len(skipped)))
    summary = "、".join(parts)

    if converted:
        message = "HouTools: File Cache 路径转绝对完成，{} 个节点（{}）".format(
            len(nodes), summary)
    else:
        message = "HouTools: 未转换任何 File Cache 路径，{} 个节点（{}）".format(
            len(nodes), summary)
    if skipped:
        # 首条跳过原因含节点路径与参数名，可定位（同 copy_output_path 的文案样式）
        message += "；首个跳过：{}".format(skipped[0])
    status(message, severity="warning" if (not converted and skipped) else None)
