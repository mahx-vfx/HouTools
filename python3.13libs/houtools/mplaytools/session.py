"""读取 MPlay 会话：已加载序列、$HIP/$HIPNAME、帧率。

设计约束（改前必读 dev-notes/mplaytools.md）：

* 本模块 import 期不接触 hou / PySide6 —— MPlay 的 HOM 能力面比主程序窄，
  且无头冒烟测试要能直接 import。hou 一律按需导入，并允许 ``hou_module``
  注入（``NO_HOU`` 明确禁用）。
* 序列来源分两种（2026-11 实机反馈修正）：
  - **磁盘序列**：``seqls -f`` 打印完整路径 + 文件模式，直接读原帧，
    不产生副本；
  - **内存序列（拍屏 / ip 渲染）**：``seqls -f`` 只返回序列名
    （实机是 ``'Flipbook Images\\n'``），**根本没有磁盘源帧**。
    此时走 ``imgsave -a`` 把当前序列临时落盘再转码，与 mplay_batch
    同款做法；由 ``current_sequence`` 返回 cleanup，调用方必须在
    ``finally`` 里调用，避免临时帧残留。
* ``seqls -f`` 的模式串没有官方格式承诺，因此解析器同时接受
  ``$F4`` / ``$F`` / ``####`` / ``@@@@`` / ``%04d`` / ``*`` / ``?`` /
  具体帧号 / 无编号单帧，
  并以「扫盘得到的真实帧文件」为唯一事实来源（解析出的 padding 仅作展示）。
"""

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, replace
from typing import Optional

DEFAULT_FPS = 24.0
DUMP_STEM = "frame"

# 显式禁用 hou 自动探测（无头测试用）；None 表示自动探测。
NO_HOU = object()

# seqls -f 的模式串没有官方格式承诺：$F4 / #### / %04d / * / ? / 具体帧号
# 都可能出现；一律以扫盘得到的真实帧文件为准。
_FRAME_TOKEN_RE = re.compile(r"(\$F\d*|#+|@+|%0?\d*d|\*+|\?+|\d+)$",
                             re.IGNORECASE)
_INDEX_PREFIX_RE = re.compile(r"^\d+\s*[:.)]\s*(.+)$")
_UNSAFE_NAME_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_HIP_EXTS = (".hip", ".hiplc", ".hipnc")


class MPlaySessionError(RuntimeError):
    """MPlay 会话信息不足或 hscript 调用失败。"""


@dataclass(frozen=True)
class MPlaySequence:
    """一条 MPlay 已加载序列在磁盘上的真实帧集合。"""

    directory: str
    stem: str
    ext: str
    padding: int
    files: tuple
    frame_numbers: tuple
    source: str

    @property
    def frame_count(self):
        return len(self.files)

    @property
    def has_gaps(self):
        if len(self.frame_numbers) < 2:
            return False
        span = self.frame_numbers[-1] - self.frame_numbers[0] + 1
        return span != len(self.frame_numbers)

    @property
    def is_exr(self):
        return self.ext.lower() == "exr"


def _hou(hou_module):
    """按需取 hou；hou_module=NO_HOU 时强制返回 None。"""
    if hou_module is NO_HOU:
        return None
    if hou_module is not None:
        return hou_module
    try:
        import hou
        return hou
    except Exception:
        return None


def _expand_with_hou(text, hou_module, require_path=True):
    """优先 hou.text.expandString（hou.expandString 已废弃），失败返回空串。

    ``require_path=True``（默认，用于 $HIP）只接受像路径的值，防假 hou
    污染；取标量变量（$HIPNAME）时传 False。
    """
    mod = _hou(hou_module)
    if mod is None:
        return ""
    try:
        text_module = getattr(mod, "text", None)
        fn = getattr(text_module, "expandString", None)
        if fn is None:
            fn = getattr(mod, "expandString", None)
        if fn is None:
            return ""
        value = (fn(text) or "").strip()
        if not value or "$" in value:
            return ""
        if require_path and not ("/" in value or "\\" in value):
            return ""
        return value
    except Exception:
        return ""


def expand_houdini_path(path, hou_module=None):
    """只展开路径的**目录部分**，basename 原样保留。

    与 videoseq 的同名函数同口径：整串 expandString 会把 ``$F4`` 展开成
    当前帧号，序列退化成单帧。无 hou 时退回 os.path.expandvars。
    """
    directory, basename = os.path.split(path)
    if not directory:
        return path
    expanded = _expand_with_hou(directory, hou_module)
    if not expanded or "$" in expanded:
        expanded = os.path.expandvars(directory)
    return os.path.join(expanded, basename)


def hscript_output(command, hou_module=None):
    """执行 MPlay hscript 命令并返回 stdout（hou.hscript 返回 (out, err)）。"""
    mod = _hou(hou_module)
    if mod is None:
        raise MPlaySessionError("当前进程没有 hou 模块（需在 MPlay 内运行）")
    try:
        result = mod.hscript(command)
    except Exception as exc:
        raise MPlaySessionError("hscript %r 失败：%s" % (command, exc))
    if isinstance(result, (tuple, list)):
        return result[0] or ""
    return result or ""


def session_fps(hou_module=None):
    """MPlay 播放帧率；取不到或异常时落 24。"""
    mod = _hou(hou_module)
    if mod is None:
        return DEFAULT_FPS
    try:
        value = float(mod.fps())
    except Exception:
        return DEFAULT_FPS
    return value if 0.0 < value <= 240.0 else DEFAULT_FPS


def _hipfile_path(hou_module):
    mod = _hou(hou_module)
    if mod is None:
        return ""
    try:
        return (mod.hipFile.path() or "").strip()
    except Exception:
        return ""


def _hscript_echo(variable, hou_module):
    """读 **MPlay 自己的 hscript 变量表**。

    实机（H21 MPlay，工程已保存）证明：``os.environ['HIP']`` 只是 MPlay
    启动时继承的旧快照（= HOME），而 MPlay 的 File ▸ Save Sequence As
    输入 ``$HIP/...`` 能正确落到工程目录 —— 说明它用的是 hscript 变量表。
    ``hou.hscript('echo $HIP')`` 是同一张表，必须优先读它。
    """
    mod = _hou(hou_module)
    if mod is None:
        return ""
    try:
        out = mod.hscript("echo $" + variable)
        text = out[0] if isinstance(out, (tuple, list)) else out
        lines = (text or "").strip().splitlines()
        return lines[0].strip() if lines else ""
    except Exception:
        return ""


def _hou_getenv(variable, hou_module):
    """Houdini 变量表（可能与 OS env 不同）。"""
    mod = _hou(hou_module)
    if mod is None:
        return ""
    try:
        return (mod.getenv(variable) or "").strip()
    except Exception:
        return ""


def hip_sources(hou_module=None, environ=None):
    """所有可能的工程目录来源，按优先级返回 ``[(label, value), ...]``。"""
    env = environ if environ is not None else os.environ
    sources = [
        ("HOUTOOLS_MPLAY_HIP", (env.get("HOUTOOLS_MPLAY_HIP") or "").strip()),
        ("hscript $HIP", _hscript_echo("HIP", hou_module)),
        ("hou.getenv HIP", _hou_getenv("HIP", hou_module)),
        ("hou.expandString $HIP", _expand_with_hou("$HIP", hou_module)),
    ]
    for label, value in (
        ("hscript $HIPFILE", _hscript_echo("HIPFILE", hou_module)),
        ("hou.getenv HIPFILE", _hou_getenv("HIPFILE", hou_module)),
        ("hipFile.path", _hipfile_path(hou_module)),
        ("env HIPFILE", (env.get("HIPFILE") or "").strip()),
    ):
        if value and "$" not in value:
            base = os.path.basename(value.replace("\\", "/"))
            if not base.lower().startswith("untitled"):
                directory = os.path.dirname(value)
                if directory:
                    sources.append((label + " dir", directory))
    sources.append(("env HIP", (env.get("HIP") or "").strip()))
    return sources


def hip_name_sources(hou_module=None, environ=None):
    """所有可能的工程名来源（含标签，供诊断）。"""
    env = environ if environ is not None else os.environ
    return [
        ("HOUTOOLS_MPLAY_HIPNAME",
         (env.get("HOUTOOLS_MPLAY_HIPNAME") or "").strip()),
        ("hscript $HIPNAME", _hscript_echo("HIPNAME", hou_module)),
        ("hou.getenv HIPNAME", _hou_getenv("HIPNAME", hou_module)),
        ("hou.expandString $HIPNAME",
         _expand_with_hou("$HIPNAME", hou_module, require_path=False)),
        ("hipFile.basename", os.path.basename(_hipfile_path(hou_module))),
        ("env HIPNAME", (env.get("HIPNAME") or "").strip()),
    ]


def diagnose_hip(hou_module=None, environ=None):
    """调试开关用：把每个来源的解析值列出来，便于定位 MPlay 变量表。"""
    lines = []
    for label, value in hip_sources(hou_module, environ):
        lines.append("  HIP  %-28s = %r" % (label, value))
    for label, value in hip_name_sources(hou_module, environ):
        lines.append("  NAME %-28s = %r" % (label, value))
    return "\n".join(lines)


def resolve_hip(hou_module=None, environ=None, isdir=None):
    """定位工程目录（$HIP）；优先非 HOME 的真实工程，找不到才退回 HOME。

    来源顺序：显式覆盖 → **hscript ``echo $HIP``** → ``hou.getenv`` →
    HOM ``expandString`` → 已保存 ``$HIPFILE`` 的目录 → OS env ``HIP``。
    即使全部只指向 HOME 也返回一个可写目录，保证工具可用（上一版在这
    里硬报错，导致未同步变量时工具完全不能用）。
    """
    env = environ if environ is not None else os.environ
    check = isdir if isdir is not None else os.path.isdir
    valid = []
    for label, value in hip_sources(hou_module, env):
        if value and "$" not in value and check(value):
            valid.append((label, os.path.normpath(value)))
    for label, value in valid:
        if label == "HOUTOOLS_MPLAY_HIP" or not hip_is_home(value, env):
            return value
    return valid[0][1] if valid else ""


def hip_is_home(hip, environ=None):
    """解析出的目录是否只是 HOME / 用户主目录（= 没拿到真实工程）。"""
    if not hip:
        return False
    env = environ if environ is not None else os.environ
    homes = {os.path.normcase(os.path.abspath(os.path.expanduser("~")))}
    for key in ("HOME", "USERPROFILE"):
        value = env.get(key)
        if value:
            homes.add(os.path.normcase(os.path.abspath(value)))
    return os.path.normcase(os.path.abspath(hip)) in homes


def _safe_name(value):
    text = (value or "").strip()
    if not text or "$" in text:
        return ""
    base = os.path.basename(text.replace("\\", "/"))
    if base.lower().endswith(_HIP_EXTS):
        base = os.path.splitext(base)[0]
    base = _UNSAFE_NAME_RE.sub("_", base).strip(" .")
    return base


def resolve_hip_name(hou_module=None, environ=None):
    """定位 $HIPNAME（不带扩展名的工程名），取不到返回空串。

    优先跳过 ``untitled``：env 里的 HIPNAME 常是 MPlay 启动时的旧快照，
    hscript ``echo $HIPNAME`` / ``hou.getenv`` 才是当前工程名。
    """
    names = [(label, _safe_name(value))
             for label, value in hip_name_sources(hou_module, environ)]
    for _label, name in names:
        if name and name.lower() != "untitled":
            return name
    for _label, name in names:
        if name:
            return name
    return ""


def _declared_padding(token):
    if token.startswith("$F"):
        digits = token[2:]
        return int(digits) if digits.isdigit() else 0
    if token[0] in "#@":
        return len(token)
    if token.startswith("%"):
        digits = re.sub(r"[^0-9]", "", token)
        return int(digits) if digits else 0
    return 0


def parse_sequence_line(line, listdir=None, hou_module=None):
    """把 ``seqls -f`` 的一行解析成磁盘真实帧集合；无效行返回 None。

    ``listdir`` 可注入（默认 os.listdir）：返回目录中的文件名列表。
    不接受 ``%`` 之外的路径特例——帧文件一律通过 concat 显式列表传给
    ffmpeg，路径里的 ``%`` 不再被 image2 当 printf 模板展开。
    """
    text = (line or "").strip().strip("\"'")
    if not text:
        return None
    text = expand_houdini_path(text, hou_module)
    directory, basename = os.path.split(text)
    if not directory or "." not in basename:
        return None

    stem_part, ext = basename.rsplit(".", 1)
    match = _FRAME_TOKEN_RE.search(stem_part)
    if match:
        token = match.group(1)
        head = stem_part[:match.start()]
        if head and head[-1] in "._-":
            sep, stem = head[-1], head[:-1]
        else:
            sep, stem = "", head
        declared_pad = _declared_padding(token)
        is_pattern = token[0] in "$#@%*?"
    else:
        sep, stem, declared_pad, is_pattern = "", stem_part, 0, False

    num_re = re.compile(
        r"^" + re.escape(stem + sep) + r"(\d+)\." + re.escape(ext) + r"$",
        re.IGNORECASE)
    try:
        entries = listdir(directory) if listdir is not None else os.listdir(directory)
    except OSError:
        return None

    found = {}
    for name in entries:
        matched = num_re.match(name)
        if matched:
            number = int(matched.group(1))
            found.setdefault(number, (name, len(matched.group(1))))
    if found:
        numbers = tuple(sorted(found))
        files = tuple(os.path.join(directory, found[n][0]) for n in numbers)
        padding = declared_pad or max(width for _name, width in found.values())
        return MPlaySequence(directory, stem, ext, max(padding, 1),
                             files, numbers, text)

    # 无编号单帧（如直接打开一张 still）：仍允许导出 1 帧视频
    if not is_pattern and basename in entries:
        return MPlaySequence(directory, stem, ext, 0, (text,), (1,), text)
    return None


def _parse_range_text(text):
    """解析 ``"Frame range: 1 to 50"`` / ``"1 50"`` → (1, 50)。"""
    if isinstance(text, (tuple, list)):
        text = " ".join(str(item) for item in text)
    matched = re.search(
        r"(-?\d+(?:\.\d+)?)\s+to\s+(-?\d+(?:\.\d+)?)", text or "",
        re.IGNORECASE)
    if matched:
        return (int(float(matched.group(1))), int(float(matched.group(2))))
    numbers = re.findall(r"-?\d+(?:\.\d+)?", text or "")
    if len(numbers) >= 2:
        return (int(float(numbers[0])), int(float(numbers[1])))
    return None


def _numeric_text(widget):
    for method_name in ("text", "getText"):
        method = getattr(widget, method_name, None)
        if method is None:
            continue
        try:
            value = method()
        except Exception:
            continue
        if isinstance(value, (int, float)):
            return str(value)
        if value is not None:
            return str(value)
    method = getattr(widget, "value", None)
    if method is not None:
        try:
            return str(method())
        except Exception:
            return ""
    return ""


_QT_PLAYBACK_NAME_TOKENS = (
    "rangestartfield", "rangeendfield", "rangeslider",
    "framefield", "currentframe", "frameslidergad",
    "si_playback", "playbarcore", "playbar_buttons", "framefieldcore",
    "startframe", "endframe",
)


def _diagnostic_value(value):
    try:
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        if isinstance(value, (tuple, list)):
            return [str(_diagnostic_value(item)) for item in value]
        return str(value)
    except Exception:
        return "<unprintable>"


def _qt_property_values(obj):
    """只读收集动态属性与帧范围相关 meta 属性。"""
    values = {}
    try:
        dynamic_names = list(obj.dynamicPropertyNames())
    except Exception:
        dynamic_names = []
    for raw_name in dynamic_names:
        try:
            name = (raw_name.decode("utf-8", "replace")
                    if isinstance(raw_name, bytes) else str(raw_name))
            values[name] = _diagnostic_value(obj.property(name))
        except Exception:
            continue
    try:
        meta = obj.metaObject()
        for index in range(meta.propertyCount()):
            prop = meta.property(index)
            name = prop.name()
            if not any(token in name.lower() for token in
                       ("range", "frame", "value", "text", "limit", "start", "end")):
                continue
            try:
                values.setdefault(name, _diagnostic_value(prop.read(obj)))
            except Exception:
                continue
    except Exception:
        pass
    return values


def _qt_accessible_text(widget):
    try:
        from PySide6.QtGui import QAccessible
        interface = QAccessible.queryAccessibleInterface(widget)
        if interface is None:
            return {}
        return {
            label: str(interface.text(kind))
            for label, kind in (
                ("name", QAccessible.Text.Name),
                ("value", QAccessible.Text.Value),
                ("description", QAccessible.Text.Description),
            )
        }
    except Exception:
        return {}


def _qt_object_info(obj, widget=None):
    info = {
        "objectName": "",
        "className": "",
        "parentName": "",
        "windowName": "",
        "text": "",
        "geometry": "",
        "properties": {},
        "accessible": {},
    }
    try:
        info["objectName"] = str(obj.objectName() or "")
        info["className"] = str(obj.metaObject().className())
        parent = obj.parent()
        info["parentName"] = str(parent.objectName() or "") if parent else ""
        info["text"] = _numeric_text(obj).strip()
        if widget is not None:
            window = widget.window()
            info["windowName"] = str(window.objectName() or "")
            if widget.isVisible():
                rect = widget.rect()
                info["geometry"] = "%dx%d@%d,%d" % (
                    rect.width(), rect.height(), rect.x(), rect.y())
            info["accessible"] = _qt_accessible_text(widget)
        info["properties"] = _qt_property_values(obj)
    except Exception:
        pass
    return info


def _qt_playback_objects(app, qt_core):
    """找 MPlay 自定义播放字段；它们通常不是标准 Qt SpinBox。"""
    objects = []
    seen = set()
    for window in app.topLevelWidgets():
        try:
            if not window.isVisible():
                continue
        except Exception:
            continue
        try:
            candidates = [window]
            candidates.extend(window.findChildren(qt_core.QObject))
        except Exception:
            candidates = [window]
        for obj in candidates:
            try:
                name = str(obj.objectName() or "").lower()
                class_name = str(obj.metaObject().className()).lower()
                matched = (
                    any(token in name for token in _QT_PLAYBACK_NAME_TOKENS)
                    or any(token in class_name for token in
                           ("si_playback", "si_range", "si_dopesheet",
                            "si_simpleplaybar", "rangefield", "playbackfield",
                            "timeslider"))
                )
                if not matched or id(obj) in seen:
                    continue
                seen.add(id(obj))
                objects.append(_qt_object_info(
                    obj, obj if hasattr(obj, "isVisible") else None))
            except Exception:
                continue
    return objects


def _named_field_number(info):
    candidates = [info.get("text", "")]
    properties = info.get("properties", {})
    candidates.extend(properties.get(name, "") for name in
                      ("value", "text", "currentFrame", "startFrame.val",
                       "endFrame.val", "startFrame", "endFrame"))
    for raw in candidates:
        text = str(raw if raw is not None else "").strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            return int(float(text))
    return None


def _property_number(properties, names):
    for name in names:
        raw = properties.get(name, "")
        text = str(raw if raw is not None else "").strip()
        if re.fullmatch(r"-?\d+(?:\.\d+)?", text):
            return int(float(text))
    return None


def _range_from_named_qt_objects(objects):
    """MPlay UI 定义里的 rangeStartField/rangeEndField 是内层播放范围。"""
    grouped = {}
    property_ranges = set()
    property_pairs = set()
    for info in objects:
        key = info.get("windowName", "")
        name = info.get("objectName", "").lower()
        if name in ("rangestartfield", "startframe"):
            value = _named_field_number(info)
            if value is not None:
                grouped.setdefault(key, {})["start"] = value
        if name in ("rangeendfield", "endframe"):
            value = _named_field_number(info)
            if value is not None:
                grouped.setdefault(key, {})["end"] = value
        properties = info.get("properties", {})
        raw = properties.get("subRange")
        parsed = _parse_range_text(raw) if raw else None
        if parsed is not None:
            property_ranges.add(parsed)
        start = _property_number(properties, ("startFrame.val", "startFrame"))
        end = _property_number(properties, ("endFrame.val", "endFrame"))
        if start is not None and end is not None and start <= end:
            property_pairs.add((start, end))
    matches = {
        (fields["start"], fields["end"])
        for fields in grouped.values()
        if "start" in fields and "end" in fields
        and fields["start"] <= fields["end"]
    }
    if len(matches) == 1:
        return next(iter(matches)), "Qt rangeStartField/rangeEndField"
    if len(property_pairs) == 1:
        return next(iter(property_pairs)), "Qt startFrame/endFrame 属性"
    if len(property_ranges) == 1:
        return next(iter(property_ranges)), "Qt subRange 属性"
    return None


def _range_from_widget_rows(rows, global_range):
    """从同一行的四个数字框中取「全局起 / 播放起 / 播放止 / 全局止」。"""
    if not global_range:
        return None
    outer_start, outer_end = sorted(int(value) for value in global_range)
    matches = set()
    for row in rows:
        if len(row) != 4:
            continue
        first, play_start, play_end, last = sorted(
            row, key=lambda field: field["x"])
        values = [field["value"] for field in
                  (first, play_start, play_end, last)]
        if values[0] != outer_start or values[3] != outer_end:
            continue
        if not outer_start <= values[1] <= values[2] <= outer_end:
            continue
        if first["x"] >= play_start["x"] or play_start["x"] >= play_end["x"]:
            continue
        if play_end["x"] >= last["x"]:
            continue
        matches.add((values[1], values[2]))
    return next(iter(matches)) if len(matches) == 1 else None


def _visible_qt_playback_range(global_range, diagnostics=None):
    """读取 MPlay 自定义命名字段，再回退可见四字段；不唯一则返回 None。"""
    numeric_count = 0
    classes = {}
    qt_objects = []
    try:
        from PySide6 import QtCore, QtWidgets
        app = QtWidgets.QApplication.instance()
        if app is None:
            if diagnostics is not None:
                diagnostics["qt_app"] = None
            return None
        if diagnostics is not None:
            top_levels = []
            for window in app.topLevelWidgets():
                try:
                    top_levels.append({
                        "className": str(window.metaObject().className()),
                        "objectName": str(window.objectName() or ""),
                        "title": str(window.windowTitle() or ""),
                        "visible": bool(window.isVisible()),
                    })
                except Exception:
                    continue
            diagnostics["qt_app"] = {
                "className": str(app.metaObject().className()),
                "topLevels": top_levels,
                "allWidgets": len(app.allWidgets()),
            }
        qt_objects = _qt_playback_objects(app, QtCore)
        if diagnostics is not None:
            diagnostics["qt_objects"] = qt_objects
        named = _range_from_named_qt_objects(qt_objects)
        if named is not None:
            if diagnostics is not None:
                diagnostics["qt_range_source"] = named[1]
            return named[0]
        grouped = {}
        for widget in app.allWidgets():
            try:
                if not widget.isVisible():
                    continue
                class_name = widget.metaObject().className()
                text = _numeric_text(widget).strip()
                if not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
                    continue
                numeric_count += 1
                classes[class_name] = classes.get(class_name, 0) + 1
                value = int(float(text))
                rect = widget.rect()
                center = widget.mapToGlobal(QtCore.QPoint(
                    rect.width() // 2, rect.height() // 2))
                window = widget.window()
                key = (id(window), round(center.y() / 4.0))
                grouped.setdefault(key, []).append({
                    "value": value, "x": center.x(),
                })
            except Exception:
                continue
        visible = _range_from_widget_rows(grouped.values(), global_range)
        if diagnostics is not None:
            diagnostics["qt_range_source"] = (
                "Qt 可见时间线四字段" if visible is not None else "Qt 未识别")
        return visible
    except Exception as exc:
        if diagnostics is not None:
            diagnostics["qt_error"] = str(exc)
        return None
    finally:
        if diagnostics is not None:
            diagnostics.update(qt_numeric_count=numeric_count,
                               qt_numeric_classes=classes,
                               qt_objects=qt_objects)


def global_frame_range(hou_module=None):
    """MPlay 弹窗的默认范围：优先 frange，再回退 $FSTART/$FEND。

    独立 MPlay 的 ``$RFSTART/$RFEND`` 与 ``hou.playbar`` 会返回旧的全局
    范围，不作为内层播放范围来源；``frange`` 与当前加载序列一致。
    """
    try:
        selected = _parse_range_text(hscript_output("frange", hou_module))
    except MPlaySessionError:
        selected = None
    if selected is not None:
        return tuple(sorted(selected))
    start = _hscript_echo("FSTART", hou_module)
    end = _hscript_echo("FEND", hou_module)
    try:
        return (int(start), int(end))
    except (TypeError, ValueError):
        return None


def playback_range(hou_module=None, raw=None, diagnostics=None):
    """读取 MPlay 当前播放范围，返回 ``(start, end)``。

    可见时间线的自定义字段与 hscript 变量在独立 MPlay 中不一定同步：先用
    ``rangeStartField/rangeEndField`` 或 ``subRange`` 取内层播放范围，再回退
    可见四字段、``$RFSTART/$RFEND``，最后才用 ``frange``。
    ``diagnostics`` 可注入字典，接收原始值与最终来源。
    """
    details = diagnostics if diagnostics is not None else {}
    if raw is not None:
        details.update(source="raw", selected=_parse_range_text(raw), raw=raw)
        return details["selected"]

    errors = []

    def _echo(variable):
        try:
            return _hscript_echo(variable, hou_module)
        except Exception as exc:
            errors.append("%s: %s" % (variable, exc))
            return ""

    def _frange():
        try:
            return hscript_output("frange", hou_module)
        except Exception as exc:
            errors.append("frange: %s" % exc)
            return ""

    rf_start = _echo("RFSTART")
    rf_end = _echo("RFEND")
    frange_raw = _frange()
    frange = _parse_range_text(frange_raw)
    rf = None
    if (re.fullmatch(r"-?\d+(?:\.\d+)?", rf_start or "")
            and re.fullmatch(r"-?\d+(?:\.\d+)?", rf_end or "")):
        rf = (int(float(rf_start)), int(float(rf_end)))
    visible = _visible_qt_playback_range(frange, diagnostics=details)
    if visible is not None:
        selected = visible
        source = details.get("qt_range_source", "Qt 可见时间线四字段")
    elif rf is not None:
        selected, source = rf, "$RFSTART/$RFEND"
    elif frange is not None:
        selected, source = frange, "frange"
    else:
        selected, source = None, "unavailable"
    if details.get("qt_error"):
        errors.append("Qt: " + details["qt_error"])
    details.update(source=source, selected=selected, rf_start=rf_start,
                   rf_end=rf_end, frange_raw=frange_raw,
                   visible_range=visible, errors=errors)
    return selected


def diagnose_playback_range(hou_module=None, diagnostics=None):
    """只读范围探针：汇总变量、查询命令、playbar 与 Qt 自定义字段。"""
    details = diagnostics if diagnostics is not None else {}
    selected = playback_range(hou_module=hou_module, diagnostics=details)
    variables = {}
    for name in ("FSTART", "FEND", "RFSTART", "RFEND", "F", "FPS",
                 "NFRAMES", "TSTART", "TEND", "TLENGTH",
                 "CSTART", "CEND", "CFRAMES"):
        try:
            variables[name] = _hscript_echo(name, hou_module)
        except Exception as exc:
            variables[name] = "<%s>" % exc
    queries = {}
    for command in ("frange", "fset", "fplayback"):
        try:
            queries[command] = hscript_output(command, hou_module)
        except Exception as exc:
            queries[command] = "<%s>" % exc
    variable_lines = []
    try:
        raw_variables = hscript_output("set -s", hou_module)
        variable_lines = [
            line for line in raw_variables.splitlines()
            if any(token in line.upper() for token in
                   ("START", "END", "RANGE", "PLAY", "FRAME", "FPS",
                    "TIME", "NFRAMES"))
        ]
    except Exception as exc:
        variable_lines = ["<set -s: %s>" % exc]
    playbar = {}
    mod = _hou(hou_module)
    if mod is not None:
        bar = getattr(mod, "playbar", None)
        if bar is not None:
            for name in ("frameRange", "playbackRange", "currentFrame"):
                method = getattr(bar, name, None)
                if method is None:
                    continue
                try:
                    playbar[name] = _diagnostic_value(method())
                except Exception as exc:
                    playbar[name] = "<%s>" % exc
    details.update(variables=variables, queries=queries, playbar=playbar,
                   variable_lines=variable_lines,
                   hou_play_attrs=[
                       name for name in dir(mod)
                       if any(token in name.lower() for token in
                              ("play", "mplay", "frame", "range", "sequence"))
                   ] if mod is not None else [])
    return selected


def format_playback_diagnostics(details):
    """范围原始值与来源的一行诊断，供真实 MPlay 日志判断读取是否一致。"""
    return (
        "[MPlay 范围读取] RFSTART=%r RFEND=%r frange=%r 可见界面=%r "
        "Qt数字框=%s 采用=%s 请求=%s%s"
        % (details.get("rf_start", ""), details.get("rf_end", ""),
           details.get("frange_raw", ""), details.get("visible_range"),
           details.get("qt_numeric_classes")
           or "0（未识别到可见数字控件）",
           details.get("source", "unavailable"), details.get("selected"),
           (" 错误=" + "; ".join(details["errors"]))
           if details.get("errors") else ""))


def format_playback_probe(details):
    """诊断模式的多行只读探针，不触碰序列、临时文件或 ffmpeg。"""
    lines = ["[MPlay 范围探针]"]
    variables = details.get("variables", {})
    if variables:
        lines.append("  变量 " + " ".join(
            "%s=%r" % (name, value) for name, value in variables.items()))
    queries = details.get("queries", {})
    if queries:
        lines.append("  hscript " + " ".join(
            "%s=%r" % (name, value) for name, value in queries.items()))
    if details.get("playbar"):
        lines.append("  hou.playbar（仅参考） %r" % details["playbar"])
    if details.get("hou_play_attrs"):
        lines.append("  hou 播放相关属性 %r" % details["hou_play_attrs"])
    if details.get("variable_lines"):
        lines.append("  set -s 过滤 " + " | ".join(details["variable_lines"]))
    if "qt_app" in details:
        lines.append("  QtApplication %r" % details["qt_app"])
    objects = details.get("qt_objects", [])
    if not objects:
        lines.append("  Qt 自定义播放字段：未找到")
    for info in objects:
        line = "  Qt %s class=%s text=%r" % (
            info.get("objectName") or "<未命名>",
            info.get("className") or "?", info.get("text") or "")
        if info.get("geometry"):
            line += " geometry=" + info["geometry"]
        properties = info.get("properties", {})
        if properties:
            line += " props=%r" % properties
        accessible = {
            key: value for key, value in info.get("accessible", {}).items()
            if value
        }
        if accessible:
            line += " accessible=%r" % accessible
        lines.append(line)
    return "\n".join(lines)


def format_sequence_range(sequence):
    """实际交给 ffmpeg 的帧号范围、数量与缺口诊断。"""
    numbers = tuple(sequence.frame_numbers)
    if not numbers:
        return "空，共 0 帧"
    ordered = tuple(sorted(numbers))
    span = "%d–%d" % (ordered[0], ordered[-1])
    if len(numbers) <= 20:
        frames = ", ".join(str(number) for number in numbers)
    else:
        frames = "%s … %s" % (
            ", ".join(str(number) for number in numbers[:10]),
            ", ".join(str(number) for number in numbers[-10:]))
    gaps = ""
    if getattr(sequence, "has_gaps", False):
        gaps = "；存在缺帧"
    return "%s，共 %d 帧；实际帧号 [%s]%s" % (
        span, len(numbers), frames, gaps)


def filter_sequence_range(spec, frame_range):
    """把磁盘序列按帧号限制到 [start, end]；无交集时明确报错。"""
    if not frame_range:
        return spec
    start, end = int(frame_range[0]), int(frame_range[1])
    if end < start:
        start, end = end, start
    pairs = [(number, path)
             for number, path in zip(spec.frame_numbers, spec.files)
             if start <= number <= end]
    if not pairs:
        raise MPlaySessionError(
            "MPlay 设定范围 %d–%d 与序列帧号 %d–%d 无交集"
            % (start, end, spec.frame_numbers[0], spec.frame_numbers[-1]))
    return replace(spec,
                   files=tuple(path for _number, path in pairs),
                   frame_numbers=tuple(number for number, _path in pairs))


def _clip_in_memory_sequence(spec, frame_range, use_range):
    """校验 imgsave 落盘结果；无法可靠对应时间线时拒绝导出。"""
    start, end = int(frame_range[0]), int(frame_range[1])
    if end < start:
        start, end = end, start
    try:
        return filter_sequence_range(spec, (start, end)), "按帧号裁剪"
    except MPlaySessionError:
        expected = max(1, end - start + 1)
        # -f 已只写请求长度时，允许内存序列使用另一套帧号体系。
        if use_range and spec.frame_count == expected:
            return spec, "-f 帧数吻合（内存帧号体系不同）"
        # -a 回退落盘完整序列时，同体系帧号可按起点偏移截取；
        # 偏移体系没有全局范围可换算，不能猜前 N 帧。
        offset = start - spec.frame_numbers[0]
        if offset >= 0 and offset + expected <= spec.frame_count:
            clipped = replace(
                spec, files=spec.files[offset:offset + expected],
                frame_numbers=spec.frame_numbers[offset:offset + expected])
            return clipped, "按范围起点偏移裁剪"
        raise MPlaySessionError(
            "imgsave 落盘帧 %d–%d（%d 帧）无法可靠对应请求范围 %d–%d；"
            "已停止，避免导出错误帧"
            % (spec.frame_numbers[0], spec.frame_numbers[-1],
               spec.frame_count, start, end))


def dump_current_sequence(hou_module=None, listdir=None, frame_range=None,
                         extensions=("jpg", "png"), diagnostics=None):
    """把 MPlay 的**内存序列**（拍屏 / ip 渲染）用 ``imgsave`` 临时落盘。

    ``seqls -f`` 对这类序列只返回名字（实机 ``'Flipbook Images\\n'``），
    磁盘上没有源帧。``\\$`` 阻止 hscript 把 ``$F`` 展开成当前帧号；
    路径转正斜杠且不用 os.path.join，避免 Windows 吃掉转义。

    **画面一致性（2026-11 实机反馈）**：ip flipbook 带多 plane，且 MPlay
    显示时应用了 display 色彩空间。裸 ``imgsave -a`` 写出的 PNG 会变成
    白底 + 粗黑网格、与 MPlay 所见不符（SideFX 论坛记载同类问题是
    plane / gamma）。因此显式 ``-p C``（只写颜色平面）+ ``-g auto``
    （显示空间转换），并优先写 **8-bit jpg**（mplay_batch 的默认格式），
    png 仅作回退；参数不被接受时自动降级到裸命令。
    有 ``frame_range`` 时用 ``-f start end`` 只写用户设定范围。

    返回 ``(MPlaySequence, cleanup)``；调用方必须在 finally 调 cleanup。
    """
    mod = _hou(hou_module)
    if mod is None:
        raise MPlaySessionError("当前进程没有 hou 模块，无法导出内存序列")
    tmp = tempfile.mkdtemp(prefix="houtools_mplay_flipbook.")

    def cleanup():
        shutil.rmtree(tmp, ignore_errors=True)

    try:
        last_error = ""
        start = end = None
        if frame_range:
            start, end = int(frame_range[0]), int(frame_range[1])
            if end < start:
                start, end = end, start
        # 先试全部 ``-f`` 组合；都失败才 ``-a``，且落盘后**必须**按范围
        # 裁剪。旧版 -f 失败就静默退回 -a 导出全部帧（实机反馈「没按
        # 用户设定范围保存」的根因之一）。
        range_modes = (True, False) if start is not None else (False,)
        for use_range in range_modes:
            for ext in extensions:
                pattern = (tmp.replace("\\", "/") + "/%s.\\$\\F.%s"
                           % (DUMP_STEM, ext))
                for full_options in (True, False):
                    options = ["-g", "auto", "-p", "C"] if full_options else []
                    if use_range:
                        options += ["-f", str(start), str(end)]
                    else:
                        options += ["-a"]
                    command = " ".join(["imgsave"] + options + [pattern])
                    try:
                        result = mod.hscript(command)
                    except Exception as exc:
                        last_error = "%s 失败：%s" % (command, exc)
                        continue
                    stderr = ""
                    if isinstance(result, (tuple, list)) and len(result) > 1:
                        stderr = result[1] or ""
                    spec = parse_sequence_line(
                        os.path.join(tmp, "%s.$F.%s" % (DUMP_STEM, ext)),
                        listdir=listdir, hou_module=NO_HOU)
                    if spec is None or not spec.frame_count:
                        last_error = "%s 未写出帧（hscript：%r）" % (
                            command, (stderr or "")[:200])
                        continue
                    if frame_range:
                        clipped, clip_source = _clip_in_memory_sequence(
                            spec, frame_range, use_range)
                        mode = "imgsave -f" if use_range else "imgsave -a"
                        if diagnostics is not None:
                            diagnostics.update(
                                source=mode + " 后" + clip_source,
                                requested=frame_range)
                        return clipped, cleanup
                    if diagnostics is not None:
                        diagnostics.update(
                            source="imgsave -f" if use_range else "imgsave -a",
                            requested=frame_range)
                    return spec, cleanup
        raise MPlaySessionError(
            "imgsave 未写出任何帧（序列为空或尚未渲染完成）：%s"
            % last_error)
    except Exception:
        cleanup()
        raise


def current_sequence(hou_module=None, listdir=None, output=None,
                     allow_dump=True, frame_range=None, diagnostics=None):
    """返回 ``(MPlaySequence, warnings, cleanup)``。

    1. 按 ``seqls -f`` 找磁盘序列，取第一条有效行；``frame_range`` 非空时
       按帧号过滤到用户设定范围（cleanup 为 None）。
    2. 解析不出路径时（拍屏/渲染是内存序列）走 ``dump_current_sequence``，
       把 ``frame_range`` 交给 ``imgsave -f`` 限定；cleanup 必须执行。
    """
    raw = output if output is not None else hscript_output("seqls -f", hou_module)
    specs = []
    for line in (raw or "").splitlines():
        cleaned = line.strip()
        if not cleaned:
            continue
        matched = _INDEX_PREFIX_RE.match(cleaned)
        if matched:
            cleaned = matched.group(1).strip()
        spec = parse_sequence_line(cleaned, listdir=listdir,
                                   hou_module=hou_module)
        if spec is not None:
            specs.append(spec)

    if specs:
        warnings = []
        if len(specs) > 1:
            warnings.append(
                "MPlay 载入了 %d 条序列，本次只转换第一条：%s"
                % (len(specs), specs[0].source))
        chosen = filter_sequence_range(specs[0], frame_range)
        if chosen.has_gaps:
            warnings.append("序列帧号不连续，已按磁盘实际帧顺序拼接。")
        if diagnostics is not None:
            diagnostics.update(
                source="磁盘序列帧号过滤" if frame_range else "磁盘序列全帧",
                requested=frame_range)
        return chosen, warnings, None

    if not allow_dump:
        raise MPlaySessionError(
            "MPlay 当前没有可转换的磁盘序列（seqls -f 输出：%r）"
            % (raw or "")[:500])
    try:
        spec, cleanup = dump_current_sequence(hou_module=hou_module,
                                              listdir=listdir,
                                              frame_range=frame_range,
                                              diagnostics=diagnostics)
    except Exception as exc:
        raise MPlaySessionError(
            "MPlay 当前没有可转换的磁盘序列，自动导出内存序列也失败：%s"
            "（seqls -f 输出：%r）" % (exc, (raw or "")[:300]))
    warnings = ["MPlay 当前是内存序列（拍屏/渲染），已用 imgsave 临时落盘再转码。"]
    if spec.has_gaps:
        warnings.append("序列帧号不连续，已按磁盘实际帧顺序拼接。")
    return spec, warnings, cleanup
