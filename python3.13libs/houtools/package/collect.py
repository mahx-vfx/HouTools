"""扫描当前 hip 的文件引用：手动引用的输入路径 + 节点缓存（filecache 等）。

规则（2026-11 用户定稿）：

- **输入引用**：`file` / `fileName` / 贴图路径这类输入参数，**只要手动填了值就收**，
  文件不存在也列出来（标"缺失"，打包时跳过不复制不改写）。
- **节点缓存**：输出参数（`sopoutput` / `vm_picture` / `picture` / `outputimage`）
  默认只收**磁盘上已有文件**的（filecache 的 geo 缓存就靠这条）；
  勾「含空输出路径」才把还没落盘的输出也列出来。
- **filecache 特例**：它的真实路径由 `basedir` + `basename` + 版本子目录拼成，
  `file` 参数里的 `$HIP/geo/$HIPNAME.$OS.$F.bgeo.sc` 不是实际文件。所以按
  `sopoutput` 求值拿到真实首帧路径、glob 出整段帧，并把要改写的参数定为
  `basedir`（改目录，而不是改那个没用的 `file`）。
- 过滤非素材：节点路径、目录值、`$HFS` 安装目录、`$HIP/galleries`、`$HIP/backup`、
  反引号表达式。

本模块的 ``import hou`` 全放在函数里：模块本身能在 Houdini 外导入，
窗口才能在无头环境实例化（冒烟测试只测 paths + 菜单接线）。
"""

from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field

from houtools.core.log import get_logger
from houtools.package import paths
from houtools.tools.open_cache_folder import _OUTPUT_PARMS

log = get_logger("package.collect")

# 具体帧文件名：<主体><数字><.扩展名链>，扩展名链至少以字母开头，
# 这样 sim_xxx_v1.0001.bgeo.sc 里只会匹配 0001 + .bgeo.sc（不会把 v1 当帧号）
_FRAME_NAME_RE = re.compile(
    r"^(?P<stem>.*?)(?P<digits>\d+)(?P<ext>\.[A-Za-z][A-Za-z0-9.]*)$")

# 明显不是文件路径的参数名，省掉无谓的 expandString
_SKIP_PARM_NAMES = {"label", "name", "comment", "group", "tags",
                    "spare_label", "parmtag",
                    # /stage 的渲染画廊库（Houdini 记账文件，不是用户素材）
                    "rendergallerysource"}

# $HIP 下这些一级目录是 Houdini 自己的记账/备份目录，不打包
_SKIP_HIP_DIRS = {"galleries", "backup"}

# 名字不在 _OUTPUT_PARMS 里、但语义是"即将写出"的参数（默认只收已落盘的）
_EXTRA_OUTPUT_PARMS = {
    "dcmfilename", "vm_dcmfilename", "vm_dsmfilename",   # 深度/DSM 输出
    "productname", "savepath", "soho_diskfile",          # USD / IFD 输出
}


@dataclass
class Ref:
    """一个参数位置上的文件引用。"""

    kind: str                  # "input" / "output"
    node_path: str
    parm_name: str             # 要改写的参数（filecache 是 basedir）
    raw: str                   # 展示用原始值（filecache 用真实输出路径）
    abs_path: str = ""         # 具体文件；序列则为模板路径（含帧 token）
    token: str = ""            # 序列模板的帧 token
    files: list = field(default_factory=list)   # 实际存在的文件（绝对路径）
    in_hip: bool = False       # 位于当前 $HIP 内（展示/统计用）
    absolute_raw: bool = False  # 参数本身是绝对路径或 $VAR 写法
    rewrite_dir: bool = False  # True = 改的是目录参数（filecache basedir）
    basedir: str = ""          # rewrite_dir 时的目录绝对路径

    @property
    def is_sequence(self):
        return bool(self.token)

    @property
    def missing(self):
        return not self.files

    @property
    def label(self):
        base = "缓存" if self.kind == "output" else "引用"
        if not self.files:
            return base + "（缺失）"
        return base + ("（序列 {} 帧）".format(len(self.files))
                       if self.is_sequence else "")


def _hou():
    import hou
    return hou


def enabled_nodes():
    """遍历所有已启用节点（跳过 bypass 与 locked HDA 内部）。

    注意：``isBypassed`` 只有 SOP 等子类才有，网络容器（ObjNode/OpNode）
    没有这个方法——必须 getattr 探测，否则容器一抛异常会把整棵子树带死。
    """
    hou = _hou()
    for node in hou.node("/").allSubChildren():
        try:
            bypass = getattr(node, "isBypassed", None)
            if callable(bypass) and bypass():
                continue
            locked = getattr(node, "isInsideLockedHDA", None)
            if callable(locked) and locked():
                continue
        except Exception as exc:
            log.debug("skip node %s: %s", node.path(), exc)
            continue
        yield node


def _iter_string_parms(node):
    hou = _hou()
    for parm in node.parms():
        try:
            if parm.parmTemplate().type() != hou.parmTemplateType.String:
                continue
        except Exception:
            continue
        yield parm


def _is_noise(expanded, hip_dir):
    """节点路径 / 目录 / $HIP 记账目录 → 不是可打包的文件。

    注意：``$HFS``（Houdini 安装目录）里的引用**不再排除**——用户要求
    "满足条件的都要出现在列表里"，由勾选决定要不要打进去。
    """
    if expanded.startswith("/") and not expanded.startswith("//"):
        return True                     # 节点路径 /obj/geo1/file1
    parent = os.path.dirname(expanded)
    hip = os.path.normpath(hip_dir)
    if paths.is_within(parent, hip):
        first = os.path.relpath(parent, hip).split(os.sep)[0].lower()
        if first in _SKIP_HIP_DIRS:
            return True
    return os.path.isdir(expanded)


def _looks_like_file_path(raw):
    """值像不像"人手动填的文件路径"——挡住菜单值、USD 路径、token 文本。

    要求：没有空白/百分号通配；带帧模板，或者"有扩展名且带路径分隔符/变量"。
    """
    value = (raw or "").strip()
    if not value or any(ch in value for ch in "\n\r\t"):
        return False
    if " " in value and not (value.startswith('"') and value.endswith('"')):
        return False
    if "%" in value or value.startswith("*"):
        return False
    # shfs:/houdini/...、op:/... 这类协议路径不是普通文件
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]+:", value) and \
            not re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    if paths.has_frame_token(value):
        return True
    base = os.path.basename(value.replace("\\", "/"))
    _stem, dot, ext = base.rpartition(".")
    if not dot or not ext or len(ext) > 8 or not ext.isalnum():
        return False
    return ("/" in value) or ("\\" in value) or value.startswith("$")


def _frames_from_path(path):
    """一个具体帧文件名 → glob 出同目录整段序列（filecache 缓存用）。"""
    directory, name = os.path.split(path)
    match = _FRAME_NAME_RE.match(name)
    if match is None:
        return [path] if os.path.isfile(path) else []
    pattern = os.path.join(directory,
                           "{}*{}".format(match.group("stem"),
                                          match.group("ext")))
    files = sorted(f for f in glob.glob(pattern) if os.path.isfile(f))
    return files or ([path] if os.path.isfile(path) else [])


def _build_filecache_ref(node, parm, hip_dir, include_output):
    """filecache：真实路径由 basedir/basename/version 拼，改 basedir 最稳。"""
    import hou

    out_parm = node.parm("sopoutput")
    if out_parm is None:
        return None
    try:
        produced = out_parm.evalAsString()
    except Exception:
        return None
    if not produced:
        return None
    produced = os.path.normpath(hou.text.expandString(produced))
    files = _frames_from_path(produced)
    if not files and not include_output:
        return None
    try:
        basedir = os.path.normpath(hou.text.expandString(
            parm.evalAsString()))
    except Exception:
        return None
    if not basedir:
        return None
    token = ""
    if _FRAME_NAME_RE.match(os.path.basename(produced)):
        token = "$F"
    try:
        basedir_raw = parm.unexpandedString()
    except Exception:
        basedir_raw = parm.evalAsString()
    return Ref(
        kind="output",
        node_path=node.path(),
        parm_name=parm.name(),
        raw=produced,
        abs_path=produced,
        token=token,
        files=files,
        in_hip=paths.is_within(produced, os.path.normpath(hip_dir)),
        absolute_raw=paths.needs_packaging(basedir_raw),
        rewrite_dir=True,
        basedir=basedir,
    )


def _resolve(parm, raw):
    """展开 + 帧模板保护；返回 (expanded_with_marker, token, files, abs_path)。

    文件不存在时 files 为空（调用方决定是否列出）。
    """
    hou = _hou()
    masked = paths.mask_frame_tokens(raw)
    try:
        expanded = hou.text.expandString(masked)
    except Exception:
        return None
    if not expanded:
        return None
    if not paths.is_absolute_like(expanded):
        expanded = os.path.normpath(os.path.join(
            hou.text.expandString("$HIP"), expanded))
    token = paths.first_frame_token(raw) if paths.MARKER in expanded else ""
    if token:
        pattern = expanded.replace(paths.MARKER, "*")
        files = sorted(f for f in glob.glob(pattern) if os.path.isfile(f))
        abs_path = paths.unmask(expanded, token)
    else:
        files = [expanded] if os.path.isfile(expanded) else []
        abs_path = expanded
    return expanded, token, files, abs_path


def _build_ref(node, parm, hip_dir, include_output=False):
    """单个参数 → Ref；不是文件引用时返回 None。"""
    hou = _hou()
    name = parm.name()
    if name in _SKIP_PARM_NAMES:
        return None
    kind = ("output" if name in _OUTPUT_PARMS
            or name.lower() in _EXTRA_OUTPUT_PARMS else "input")

    if node.type().name().startswith("filecache"):
        # filecache 的 file/basename 只是拼路径的片段，真实路径在 sopoutput；
        # 只按 basedir 建一条引用（改写 basedir 最稳，下游 file 节点跟着走）
        if name == "basedir":
            return _build_filecache_ref(node, parm, hip_dir, include_output)
        return None

    try:
        raw = parm.unexpandedString().strip()
    except Exception:
        return None
    if not raw or "\n" in raw or raw.startswith("`") or "\x00" in raw:
        return None

    # 只保留"用户改过的"参数：默认值（如 karma/hoseksolarmap.rat 这种
    # Houdini 内置默认路径）不算用户引用。filecache 的 basedir 即便处于默认
    # 也要收——它代表用户实际写出的缓存目录。
    if not node.type().name().startswith("filecache"):
        try:
            if parm.isAtDefault():
                return None
        except Exception:
            pass

    if not _looks_like_file_path(raw):
        return None

    resolved = _resolve(parm, raw)
    if resolved is None:
        return None
    expanded, token, files, abs_path = resolved
    if _is_noise(expanded, hip_dir):
        return None
    if not files and kind == "output" and not include_output:
        return None                     # 输出路径还没落盘 → 不是缓存
    hip = os.path.normpath(hip_dir)
    return Ref(
        kind=kind,
        node_path=node.path(),
        parm_name=name,
        raw=raw,
        abs_path=abs_path,
        token=token,
        files=files,
        in_hip=paths.is_within(os.path.dirname(expanded), hip),
        absolute_raw=paths.needs_packaging(raw),
    )


def collect(hip_dir="", include_output=False):
    """扫描整个场景，返回 (refs, 跳过数)。"""
    hou = _hou()
    hip_dir = hip_dir or hou.text.expandString("$HIP")
    refs = []
    skipped = 0
    for node in enabled_nodes():
        for parm in _iter_string_parms(node):
            try:
                ref = _build_ref(node, parm, hip_dir, include_output)
            except Exception as exc:
                log.debug("scan %s/%s failed: %s", node.path(), parm.name(), exc)
                continue
            if ref is None:
                skipped += 1
                continue
            refs.append(ref)
    log.info("扫描完成：%d 个引用，%d 个参数跳过", len(refs), skipped)
    return refs, skipped


def copy_plan(refs, base_dir, package_name=paths.PACKAGE_DIR, hip_dir=""):
    """{源绝对路径: 包内目标绝对路径}；同一文件只复制一次，缺失的不进计划。"""
    plan = {}
    for ref in refs:
        for path in ref.files:
            plan[path] = paths.package_abs_target(
                path, base_dir, package_name, hip_dir)
    return plan


def target_for(ref, prefix, hip_dir=""):
    """改写后的参数值。

    - 普通引用：``<prefix>/assets/<子目录>/<文件名>``；
    - filecache：改 ``basedir`` 目录 → ``<prefix>/assets/<相对 $HIP 的目录>``。
    """
    if ref.rewrite_dir and ref.basedir:
        hip = os.path.normpath(hip_dir) if hip_dir else ""
        if hip and paths.is_within(ref.basedir, hip):
            sub = paths.rel_posix(os.path.relpath(ref.basedir, hip))
        else:
            sub = paths.flatten_dir(ref.basedir)
        return "{}/{}/{}".format(prefix, paths.PACKAGE_ASSETS, sub)
    return paths.package_target(ref.abs_path, prefix, hip_dir)
