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
5. **落写前弹窗确认（2026-11 用户要求）**：run() 先用 ``_survey`` 预演
   一遍（与落写共用 ``_plan`` 计算路径、不写参数），统计将转换/跳过的
   参数量——有实际改动才弹 ``dialogs.question`` 确认（品牌主题弹窗，
   确认按钮=品牌橙主按钮；文案直说改动：将改动的节点/参数量、$F 保留
   与变量写死说明、Ctrl+Z 撤销提示、跳过数）；取消则不动任何参数；
   没有可转换参数不弹窗、维持原汇总通知。
6. 整批一个 undo 槽（``hou.undos.group``）；单参数失败在循环内消化，
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


def _plan(node, parm_name):
    """计算单个节点的一个路径参数的转换结果，**不写参数**，返回 (kind, text)。

    kind ∈ {"converted", "unchanged", "skip"}；converted 的 text 是将写回
    的新值，skip 的 text 是原因说明（确认弹窗与汇总通知里展示）。None =
    节点没有该参数（不同版本参数集不同，如旧 filecache 只有 file）——
    静默忽略，不计任何数。纯计算，供预演（_survey）与落写（_convert）
    共用，保证弹窗看到的数字就是落写会发生的改动。
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
    return "converted", new_path


def _convert(node, parm_name):
    """_plan 的计算结果落写：converted 才 parm.set。

    hou.PermissionError 等写入异常由调用方按参数粒度捕获——单参数失败
    不影响批次里其余参数。
    """
    result = _plan(node, parm_name)
    if result is not None and result[0] == "converted":
        node.parm(parm_name).set(result[1])
    return result


def _survey(nodes):
    """预演当前层级 File Cache 三路径参数（不写参数），供确认弹窗取数。

    返回 (will_convert, skipped_texts)。错误按参数粒度消化成 skip（与
    落写循环同口径；锁定参数在预演里不会失败——PermissionError 只在
    parm.set 时抛，这里没有 set）。只打 log.warning 留痕预期之外的
    计算失败（同 run() 落写循环的口径）。
    """
    will_convert, skipped = 0, []
    for node in nodes:
        for parm_name in _PATH_PARMS:
            try:
                result = _plan(node, parm_name)
            except Exception as exc:
                log.warning("plan %s.%s failed: %s",
                            node.path(), parm_name, exc)
                result = ("skip", "计算失败（{}）".format(exc))
            if result is None:
                continue
            if result[0] == "converted":
                will_convert += 1
            elif result[0] == "skip":
                skipped.append(result[1])
    return will_convert, skipped


def _main_window():
    """Houdini 主窗口 QWidget，作确认弹窗的 parent。

    NetworkEditor 面板页签（_target_editor 的返回值）是 HOM 包装对象而
    非 QWidget，直接传给 QMessageBox 会报 ``__init__`` 签名不符（PySide6
    GUI 实测 TypeError），必须换成真正的 Qt 窗口。优先 ``hou.qt.mainWindow``
    （官方桥，同 automation/window.py 拿 floatingPanelWindow 的手法），
    失败退 ``hou.ui.mainQtWindow``（HOM 文档 API），再退 None——QMessageBox
    无 parent 仍是应用级模态，只是不挂主窗口层级。
    """
    import hou
    try:
        return hou.qt.mainWindow()
    except Exception as exc:
        log.debug("hou.qt.mainWindow failed: %s", exc)
    try:
        return hou.ui.mainQtWindow()
    except Exception as exc:
        log.debug("hou.ui.mainQtWindow failed: %s", exc)
        return None


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

    # 落写前预演一遍并弹窗确认（2026-11 用户要求）：_survey 与落写共用
    # _plan 的计算路径、不写参数，弹窗里的数字就是将要发生的改动；没有
    # 可转换参数不弹窗、维持原汇总通知（无可确认之事）
    from houtools.ui.dialogs import question

    will_convert, preview_skipped = _survey(nodes)
    if will_convert:
        lines = [
            "将把当前层级 {} 个 File Cache 节点的 {} 个路径参数改为绝对路径。"
            .format(len(nodes), will_convert),
            "$F 等帧变量保持不动；其余变量（$HIP、$OS 等）展开成当前的"
            "实际值写死进路径。",
            "改错了按 Ctrl+Z 撤销（整批一步撤回）。",
        ]
        if preview_skipped:
            lines.append("另有 {} 个参数将跳过（表达式驱动、锁定等，"
                         "不做改动）。".format(len(preview_skipped)))
        if not question(_main_window(), "File Cache 路径转绝对",
                        "\n".join(lines)):
            status("HouTools: 已取消，未修改任何 File Cache 路径",
                   severity=None)
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
    # 等级语义：转换了 ≥1 个 → success；一个没转且有跳过 → warning；
    # 什么都没发生（全 unchanged）→ 中性 info
    if converted:
        severity = "success"
    elif skipped:
        severity = "warning"
    else:
        severity = None
    status(message, severity=severity)
