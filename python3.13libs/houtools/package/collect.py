"""扫描当前 hip 的文件引用：手动引用的输入路径 + 节点缓存（filecache 等）。

规则（2026-11 用户定稿）：

- **输入引用**：`file` / `fileName` / 贴图路径这类输入参数，**只要手动填了值就收**，
  文件不存在也列出来（标"缺失"，打包时跳过不复制不改写）。
- **节点缓存**：输出参数（`sopoutput` / `vm_picture` / `picture` / `outputimage`）
  默认只收**磁盘上已有文件**的（filecache 的 geo 缓存就靠这条）；
  勾「含空输出路径」才把还没落盘的输出也列出来。
- **filecache 特例**：真实路径由 `basedir` + `basename` + 版本子目录拼成，
  `file` 参数里的 `$HIP/geo/$HIPNAME.$OS.$F.bgeo.sc` 不是实际文件。按
  `sopoutput` 求值拿真实路径、严格 glob 整段帧，改写的参数定为 `basedir`。
- **UDIM**：`texture.<UDIM>.exr` 这类贴图模板按 4 位 Tile 号（1001–1999）
  枚举复制，参数里保留 `<UDIM>` 模板（不同语义，不与 `$F` 混用）。
- 过滤非素材：节点路径、目录值、`$HIP/galleries`、`$HIP/backup`、反引号
  表达式；表达式驱动（keyframes 非空）的参数也跳过。
- **两阶段扫描**：``gather()`` 只在主线程读 HOM（节点/参数/展开），
  ``resolve()`` 只做文件系统检查（isfile/glob/isdir）——后者可以放
  QThread，扫描大型工程/网络盘时不阻塞 GUI。``collect()`` 是两者串联，
  保留给无窗口调用。
- **目标唯一性**：``build_layout()`` 统一生成「每条引用的改写目标 + 复制
  计划」，同一个源目录只分配一个 assets 子目录；不同源目录展平后重名时
  自动加 ``_2/_3``，复制与改写共用这一份映射（不再各算各的）。
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
    token: str = ""            # 序列模板的帧 token（$F4 / <UDIM> / ...）
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
        if self.token == paths.UDIM_TOKEN:
            return "{}（UDIM {} 块）".format(base, len(self.files))
        if self.is_sequence:
            return "{}（序列 {} 帧）".format(base, len(self.files))
        return base


@dataclass
class Candidate:
    """gather 阶段产物：只含 HOM 读到的内容，文件系统检查留给 resolve。"""

    kind: str
    node_path: str
    parm_name: str
    raw: str
    expanded: str
    abs_path: str
    token: str = ""
    absolute_raw: bool = False
    rewrite_dir: bool = False
    basedir: str = ""


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


def _looks_like_node_path(expanded):
    """/ 开头且末段无扩展名 → Houdini 节点路径（/obj/geo1/file1）。

    POSIX 形态绝对路径（/mnt/proj/a.jpg——Windows 上 os.path.isabs 返
    False、Houdini 文件参数视为绝对）末段带扩展名，不能误判成节点路径。
    """
    tail = expanded.rstrip("/").rpartition("/")[2]
    return bool(tail) and "." not in tail


def _is_noise(expanded, hip_dir):
    """节点路径 / 目录 / $HIP 记账目录 → 不是可打包的文件。

    注意：``$HFS``（Houdini 安装目录）里的引用**不再排除**——用户要求
    "满足条件的都要出现在列表里"，由勾选决定要不要打进去。
    """
    if expanded.startswith("/") and not expanded.startswith("//") \
            and _looks_like_node_path(expanded):
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

    要求：没有空白/百分号通配；带帧模板/UDIM，或者"有扩展名且带路径分隔符/变量"。
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
    if paths.has_frame_token(value) or paths.has_udim_token(value):
        return True
    base = os.path.basename(value.replace("\\", "/"))
    _stem, dot, ext = base.rpartition(".")
    if not dot or not ext or len(ext) > 8 or not ext.isalnum():
        return False
    return ("/" in value) or ("\\" in value) or value.startswith("$")


def _digit_files(prefix, suffix):
    """严格数字序列：glob 之后再用 ``^prefix\\d+suffix$`` 二次过滤。

    只用 ``prefix*suffix`` 会把 ``sim.backup.bgeo.sc`` 这类文件一起收进来
    （报告 P3-01）。
    """
    pattern = prefix + "*" + suffix
    rx = re.compile("^" + re.escape(prefix) + r"[0-9]+" +
                    re.escape(suffix) + "$", re.IGNORECASE)
    return sorted(f for f in glob.glob(pattern)
                  if os.path.isfile(f) and rx.match(f))


def _frames_from_path(path):
    """一个具体帧文件名 → 严格 glob 出同目录整段数字帧（filecache 用）。"""
    directory, name = os.path.split(path)
    match = _FRAME_NAME_RE.match(name)
    if match is None:
        return [path] if os.path.isfile(path) else []
    files = _digit_files(os.path.join(directory, match.group("stem")),
                         match.group("ext"))
    return files or ([path] if os.path.isfile(path) else [])


def _udim_files(expanded):
    """``texture.<UDIM>.exr`` → 枚举同目录 1001–1999 的 4 位 Tile。"""
    lowered = expanded.lower()
    index = lowered.find(paths.UDIM_TOKEN.lower())
    if index < 0:
        return []
    prefix = expanded[:index]
    suffix = expanded[index + len(paths.UDIM_TOKEN):]
    pattern = prefix + "*" + suffix
    # 用 [0-9] 而不是 \d：后者会匹配 Unicode 数字，帧/Tile 号只认 ASCII
    rx = re.compile("^" + re.escape(prefix) + r"([0-9]{4})" +
                    re.escape(suffix) + "$", re.IGNORECASE)
    found = []
    for candidate in glob.glob(pattern):
        if not os.path.isfile(candidate):
            continue
        match = rx.match(candidate)
        # UDIM 规范只允许 1001–1999；只匹配 \d{4} 会把 0001/2001/9999
        # 这类别的 4 位数字序列误收进包（2026-11-19 合并后复审）
        if match and 1001 <= int(match.group(1)) <= 1999:
            found.append(candidate)
    return sorted(found)


def _masked_files(expanded):
    """``foo.__HTFRAME__.exr`` → 只接受帧号位是纯数字的文件。"""
    prefix, sep, suffix = expanded.partition(paths.MARKER)
    if not sep:
        return []
    return _digit_files(prefix, suffix)


def archivable(full, real_pkg):
    """zip 归档边界：``realpath`` 后仍落在包目录内才允许写入。

    包内符号链接/junction 指向包外时返回 False（P2-02 复审）。抽成纯函数
    便于无头回归，也让文件与目录两种链接共用同一条判定。
    """
    try:
        return paths.is_within(os.path.realpath(full), real_pkg)
    except OSError:
        return False


def _gather_filecache(node, parm, include_output):
    """filecache：只取 basedir 一条引用（真实路径在 sopoutput）。"""
    import hou

    out_parm = node.parm("sopoutput")
    if out_parm is None:
        return None, None
    try:
        produced = out_parm.evalAsString()
    except Exception:
        return None, None
    if not produced:
        return None, None
    produced = os.path.normpath(hou.text.expandString(produced))
    try:
        if parm.keyframes():
            # basedir 表达式驱动：改写静态值破坏表达式（同 filecache_abs_path）
            return None, "expr"
    except Exception:
        pass
    try:
        basedir = os.path.normpath(hou.text.expandString(
            parm.evalAsString()))
    except Exception:
        return None, None
    if not basedir:
        return None, None
    token = ""
    if _FRAME_NAME_RE.match(os.path.basename(produced)):
        token = "$F"
    try:
        basedir_raw = parm.unexpandedString()
    except Exception:
        basedir_raw = parm.evalAsString()
    return Candidate(
        kind="output",
        node_path=node.path(),
        parm_name=parm.name(),
        raw=produced,
        expanded=produced,
        abs_path=produced,
        token=token,
        absolute_raw=paths.needs_packaging(basedir_raw),
        rewrite_dir=True,
        basedir=basedir,
    ), None


def _expand(parm, raw):
    """展开变量并保护帧模板；返回 (expanded, token, abs_path) 或 None。"""
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
    if paths.has_udim_token(raw):
        # <UDIM> 与帧模板同现时无法同时保留两套模板：直接拒绝，避免把
        # __HTFRAME__ 标记写进参数改写目标（2026-11-19 合并后复审）
        if paths.has_frame_token(raw):
            return None
        return expanded, paths.UDIM_TOKEN, expanded
    if paths.MARKER in expanded:
        token = paths.first_frame_token(raw)
        return expanded, token, paths.unmask(expanded, token)
    return expanded, "", expanded


def _gather_ref(node, parm, include_output=False):
    """单个参数 → (Candidate|None, 排除原因|None)。"""
    name = parm.name()
    if name in _SKIP_PARM_NAMES:
        return None, None
    kind = ("output" if name in _OUTPUT_PARMS
            or name.lower() in _EXTRA_OUTPUT_PARMS else "input")

    if node.type().name().startswith("filecache"):
        # filecache 的 file/basename 只是拼路径的片段，真实路径在 sopoutput；
        # 只按 basedir 建一条引用（改写 basedir 最稳，下游 file 节点跟着走）
        if name == "basedir":
            return _gather_filecache(node, parm, include_output)
        return None, None

    try:
        if parm.keyframes():
            # 表达式驱动（关键帧/chs 链接）：改写静态值等于破坏表达式
            return None, "expr"
    except Exception:
        pass

    try:
        raw = parm.unexpandedString().strip()
    except Exception:
        return None, None
    if not raw or "\n" in raw or raw.startswith("`") or "\x00" in raw:
        return None, None

    # 只保留"用户改过的"参数：默认值（如 karma/hoseksolarmap.rat 这种
    # Houdini 内置默认路径）不算用户引用。filecache 的 basedir 即便处于默认
    # 也要收——它代表用户实际写出的缓存目录（上面已分流）。
    try:
        if parm.isAtDefault():
            return None, None
    except Exception:
        pass

    if not _looks_like_file_path(raw):
        return None, None

    resolved = _expand(parm, raw)
    if resolved is None:
        return None, None
    expanded, token, abs_path = resolved
    # 与 _is_noise 同口径：UNC 网络路径（//server/...）不是 Houdini 节点路径，
    # 不能在这里提前判 noise（2026-11-19 合并后复审）
    if expanded.startswith("/") and not expanded.startswith("//") \
            and _looks_like_node_path(expanded):
        return None, "noise"
    return Candidate(
        kind=kind,
        node_path=node.path(),
        parm_name=name,
        raw=raw,
        expanded=expanded,
        abs_path=abs_path,
        token=token,
        absolute_raw=paths.needs_packaging(raw),
    ), None


def gather(hip_dir="", include_output=False):
    """主线程阶段：遍历节点/参数并展开，返回 (candidates, skip_stats)。

    ``hip_dir`` 仅为兼容旧签名保留：HOM 阶段不用它，文件系统归一由
    ``resolve()`` 负责；``collect()`` 串联时会先展开 `$HIP` 再同时传给两
    个阶段（2026-11-19 合并后复审，原实现展开后丢弃导致兼容回归）。
    """
    candidates = []
    skip_stats = {}
    for node in enabled_nodes():
        for parm in _iter_string_parms(node):
            try:
                cand, reason = _gather_ref(node, parm, include_output)
            except Exception as exc:
                log.debug("gather %s/%s failed: %s", node.path(), parm.name(), exc)
                continue
            if cand is not None:
                candidates.append(cand)
            elif reason:
                skip_stats[reason] = skip_stats.get(reason, 0) + 1
    return candidates, skip_stats


def resolve(candidates, hip_dir, include_output=False, should_cancel=None):
    """文件系统阶段：对候选做 isfile/glob/isdir，返回 (refs, skip_stats)。

    可以在 QThread 里跑（只碰文件系统，不碰 HOM）。
    """
    hip = os.path.normpath(hip_dir) if hip_dir else ""
    refs = []
    skip_stats = {}
    for cand in candidates:
        if should_cancel is not None and should_cancel():
            break
        expanded = cand.expanded
        if _is_noise(expanded, hip):
            skip_stats["noise"] = skip_stats.get("noise", 0) + 1
            continue
        if cand.rewrite_dir:
            files = _frames_from_path(cand.abs_path)
        elif cand.token == paths.UDIM_TOKEN:
            files = _udim_files(expanded)
        elif cand.token:
            files = _masked_files(expanded)
        else:
            files = [expanded] if os.path.isfile(expanded) else []
        if not files and cand.kind == "output" and not include_output:
            skip_stats["not_written"] = skip_stats.get("not_written", 0) + 1
            continue
        refs.append(Ref(
            kind=cand.kind,
            node_path=cand.node_path,
            parm_name=cand.parm_name,
            raw=cand.raw,
            abs_path=cand.abs_path,
            token=cand.token,
            files=files,
            in_hip=paths.is_within(os.path.dirname(expanded), hip),
            absolute_raw=cand.absolute_raw,
            rewrite_dir=cand.rewrite_dir,
            basedir=cand.basedir if cand.rewrite_dir else "",
        ))
    return refs, skip_stats


def collect(hip_dir="", include_output=False):
    """扫描整个场景，返回 (refs, skip_stats)（无窗口调用用）。"""
    hou = _hou()
    hip_dir = hip_dir or hou.text.expandString("$HIP")
    candidates, stats = gather(hip_dir, include_output)
    refs, more = resolve(candidates, hip_dir, include_output)
    for key, count in more.items():
        stats[key] = stats.get(key, 0) + count
    log.info("扫描完成：%d 个引用", len(refs))
    return refs, stats


def session_matches_hip(session, current_hip, is_new_file=False):
    """打包会话是否属于当前 hip（P1-02；纯函数便于无头回归）。

    只认打包时登记的 ``source_hip`` / ``saved_hip`` 精确匹配——不能因为
    当前 hip 恰好位于包目录内就放行，否则在包目录里另存/打开别的 hip
    仍会把旧工程内容打进 zip（2026-11-19 合并后复审）。
    """
    if not session or is_new_file or not current_hip:
        return False
    current = os.path.normcase(os.path.normpath(current_hip))
    for key in ("source_hip", "saved_hip"):
        value = session.get(key) or ""
        if value and current == os.path.normcase(os.path.normpath(value)):
            return True
    return False


def copy_plan(refs, base_dir, package_name=paths.PACKAGE_DIR, hip_dir=""):
    """{源绝对路径: 包内目标绝对路径}；同一文件只复制一次，缺失的不进计划。

    注意：窗口实际用 ``build_layout()``（带目标唯一性 + 越界/冲突检测）；
    这个函数保留给无窗口调用/测试，不处理展平碰撞，**不要用于打包**。
    """
    plan = {}
    for ref in refs:
        for path in ref.files:
            plan[path] = paths.package_abs_target(
                path, base_dir, package_name, hip_dir)
    return plan


def _target(prefix, sub, name):
    """``<prefix>/assets[/<sub>][/<name>]``（Houdini 写法）。"""
    base = prefix + "/" + paths.PACKAGE_ASSETS
    if sub:
        base += "/" + sub
    if name:
        base += "/" + name
    return base


def build_layout(refs, prefix, base_dir="", package_name=paths.PACKAGE_DIR,
                 hip_dir=""):
    """统一生成目标路径与复制计划，保证目标唯一。

    返回 ``(targets, plan, notes, conflicts)``：

    - ``targets`` 与 ``refs`` 一一对应（Houdini 写法，序列/UDIM 保留模板）；
    - ``plan`` 是 ``{源绝对路径: 包内绝对路径}``；
    - 不同源目录展平后重名时，后来的目录加 ``_2/_3``，并写一条 note
      （复制与改写共用这份映射，杜绝"复制覆盖、参数指同一文件"）；
    - ``conflicts`` 收集「目标越出包目录」与「同一源文件被映射到多个目标」，
      窗口据此中止打包——不再静默产出缺文件的包（2026-11-19 合并后复审）。
    """
    assigned = {}
    used = {}
    reported = set()
    plan = {}
    notes = []
    conflicts = []
    package_root = paths.package_dir_abs(base_dir, package_name) if base_dir else ""

    def assign(source_dir, base_sub):
        key = os.path.normcase(os.path.normpath(source_dir))
        if key in assigned:
            return assigned[key]
        candidate = base_sub
        index = 2
        while candidate.casefold() in used and used[candidate.casefold()] != key:
            candidate = "{}_{}".format(base_sub, index) if base_sub \
                else "dir_{}".format(index)
            index += 1
        assigned[key] = candidate
        used[candidate.casefold()] = key
        return candidate

    def note_renamed(source_dir, sub, base_sub):
        if sub == base_sub:
            return
        key = os.path.normcase(os.path.normpath(source_dir))
        if key in reported:          # 同一源目录的多条引用只报一次
            return
        reported.add(key)
        notes.append("{} 的目标目录重名，已改为 {}".format(
            source_dir, sub or "(assets 根)"))

    def add_plan(source, target):
        if os.path.normcase(target) == os.path.normcase(source):
            return                   # 源=目标：跳过复制（二次打包）
        if package_root and not paths.is_within(target, package_root):
            conflicts.append("{} → {}（目标越出包目录）".format(source, target))
            return
        previous = plan.get(source)
        if previous is not None and \
                os.path.normcase(previous) != os.path.normcase(target):
            conflicts.append("{} → {} / {}（多个目标）".format(
                source, previous, target))
            return
        plan[source] = target

    targets = []
    for ref in refs:
        if ref.rewrite_dir and ref.basedir:
            base_sub = paths.subdir_for_dir(ref.basedir, hip_dir)
            sub = assign(ref.basedir, base_sub)
            note_renamed(ref.basedir, sub, base_sub)
            targets.append(_target(prefix, sub, ""))
            if not base_dir:
                continue
            for file_path in ref.files:
                try:
                    rel_dir = os.path.relpath(os.path.dirname(file_path),
                                              ref.basedir)
                except ValueError:
                    # Windows 跨盘符时 relpath 会抛 ValueError：无法表示成
                    # 包内相对路径，一律按越界处理（不能让 Qt 槽炸掉）
                    rel_dir = ".."
                # sopoutput 被指到 basedir 之外时 rel_dir 带 ".."，拼进
                # file_sub 后 normpath 会把复制目标写出包目录
                if rel_dir == ".." or rel_dir.startswith(".." + os.sep) \
                        or os.path.isabs(rel_dir):
                    conflicts.append("{} 不在 basedir {} 内，已跳过".format(
                        file_path, ref.basedir))
                    continue
                if rel_dir in (".", ""):
                    file_sub = sub
                else:
                    rel = paths.rel_posix(rel_dir)
                    file_sub = "{}/{}".format(sub, rel) if sub else rel
                target = paths.package_abs_path(
                    base_dir, package_name, file_sub,
                    os.path.basename(file_path))
                add_plan(file_path, target)
            continue

        src_dir = os.path.dirname(ref.abs_path)
        base_sub = paths.asset_subdir(ref.abs_path, hip_dir)
        sub = assign(src_dir, base_sub)
        note_renamed(src_dir, sub, base_sub)
        targets.append(_target(prefix, sub, os.path.basename(ref.abs_path)))
        if not base_dir:
            continue
        for file_path in ref.files:
            target = paths.package_abs_path(
                base_dir, package_name, sub, os.path.basename(file_path))
            add_plan(file_path, target)
    return targets, plan, notes, conflicts


def target_for(ref, prefix, hip_dir=""):
    """单条引用的改写目标（兼容旧调用；窗口用 build_layout 统一算）。"""
    if ref.rewrite_dir and ref.basedir:
        sub = paths.subdir_for_dir(ref.basedir, hip_dir)
        return _target(prefix, sub, "")
    return paths.package_target(ref.abs_path, prefix, hip_dir)
