"""打包工程的纯路径逻辑（不 import hou / PySide6，可直接无头测试）。

职责：
- 识别文件名里的帧模板（``$F`` / ``$F4`` / ``${FF2}`` …），把模板与具体帧分开处理；
- 判断哪些路径需要"搬进 $HIP"（只有绝对路径需要，`$HIP/...` 与相对路径已经
  可移植；绝对判定兼容 POSIX 形态——``/proj/...`` 在 Windows 上
  ``os.path.isabs`` 返 False、Houdini 视为绝对，同 filecache_abs_path._is_abs）；
- 生成包内目标路径，并按**文件类型**归入 Houdini 官方 New Project 目录
  （``geo / hda / sim / abc / tex / render / flip / scripts``）；
- 包名净化（用户输入直接进 os.path.join，必须收敛）、默认包名、内置引用
  识别、场景切换检测、打包参数前缀、可读大小。

本模块不 import hou / PySide6，全部纯函数，可直接无头测试。
"""

from __future__ import annotations

import os
import re

# Houdini 帧模板：$F / $F1-$F9 / $FF / $FF1-$FF9 / ${F} / ${F4} / ${FF2}
_FRAME_RE = re.compile(r"\$\{(?:FF|F)\d?\}|\$(?:FF|F)\d?(?![A-Za-z0-9_])")

# 展开前把帧模板换成一个不会出现在文件名里的标记，展开后再按需还原。
# 不能用 \x00 之类控制字符：hou.text.expandString 遇到 NUL 会截断，$F4 模板
# 会变成"目录 + foo."这种残值（实测）。
MARKER = "__HTFRAME__"

# 包目录名（默认；UI 可改）
PACKAGE_DIR = "ht_package"

# 包内素材子目录（兼容旧调用：target_for/copy_plan 仍保留 assets 前缀语义；
# 新的窗口打包走 build_layout，按文件类型分到下面的官方目录）
PACKAGE_ASSETS = "assets"

# Houdini 官方 New Project 默认子目录（SideFX 文档 / File > New Project 实测）：
#   Geometry geo / Digital Assets hda / Simulation sim / Alembic abc /
#   Textures tex / Renders render / Flipbooks flip / Scripts scripts
# 打包按文件类型归入这些目录，让包结构与官方工程一致、到农场可直接当工程用。
CATEGORY_GEO = "geo"
CATEGORY_HDA = "hda"
CATEGORY_SIM = "sim"
CATEGORY_ABC = "abc"
CATEGORY_TEX = "tex"
CATEGORY_RENDER = "render"
CATEGORY_FLIP = "flip"
CATEGORY_SCRIPTS = "scripts"
CATEGORY_OTHER = "other"

# 扩展名 → 官方目录（小写、不带点）。分类函数是纯函数，不 import hou。
_EXT_GEO = {"bgeo", "vdb", "obj", "fbx", "ply", "stl", "usd", "usdc", "usdz",
            "gltf", "glb"}
_EXT_ABC = {"abc"}
_EXT_SIM = {"sim", "hi"}
_EXT_HDA = {"hda", "otl", "hdalc"}
_EXT_FLIP = {"mov", "mp4", "avi", "mkv", "webm"}
_EXT_SCRIPTS = {"py", "pyc", "cmd", "ps1", "bat", "sh", "js"}
# 图片类：输入引用（贴图）归 tex，输出引用（渲染结果）归 render
_EXT_IMAGE = {"exr", "hdr", "dpx", "tif", "tiff", "png", "jpg", "jpeg", "rat",
              "pic", "tex", "dds", "tga", "bmp", "psd", "webp", "tx"}
# 序列 .bgeo.sc 的末段扩展名是 .sc，需整链匹配（见 _ext_chain）
_EXT_GEO_CHAIN = {"bgeo.sc", "bgeo.gz", "bgeo.sc.gz"}

_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/]")

# UDIM 贴图模板（Houdini 标准写法）
UDIM_TOKEN = "<UDIM>"


def has_frame_token(raw):
    return bool(raw) and _FRAME_RE.search(raw) is not None


def has_udim_token(raw):
    """是否含 UDIM 模板 ``<UDIM>``（大小写不敏感）。"""
    return UDIM_TOKEN.lower() in (raw or "").lower()


def mask_frame_tokens(raw):
    """把帧模板换成 MARKER（展开 $HIP 等变量时不能让 $F 被算成当前帧）。"""
    return _FRAME_RE.sub(MARKER, raw)


def first_frame_token(raw):
    match = _FRAME_RE.search(raw or "")
    return match.group(0) if match else "$F4"


def unmask(text, token):
    return text.replace(MARKER, token)


def needs_packaging(raw):
    """是否需要把它搬进 $HIP：只有绝对路径（或展开后可能是绝对路径的变量）。

    POSIX 形态（``/mnt/proj/x``）也算绝对——Houdini 在 Windows 上把它解析
    到当前盘根，不是相对 $HIP 的可移植写法。
    """
    value = (raw or "").strip()
    if not value:
        return False
    lowered = value.lower()
    if lowered.startswith("$hip") or lowered.startswith("${hip}"):
        return False
    if _DRIVE_RE.match(value) or value.startswith(("/", "\\")):
        return True
    return value.startswith("$")


def is_absolute_like(path):
    """绝对路径判定，兼容 POSIX 形态（``/`` 开头单斜杠也算）。

    Python 3.13 起的 os.path.isabs 在 Windows 对 "/foo" 返 False，而
    Houdini 的文件参数把 "/proj/..." 视为绝对路径（同 filecache_abs_path
    的 _is_abs）；本模块调用方已先用 _looks_like_file_path 滤掉节点路径。
    """
    value = (path or "").strip()
    return bool(value) and (os.path.isabs(value)
                            or bool(_DRIVE_RE.match(value))
                            or value.startswith(("/", "\\")))


def flatten_dir(directory):
    """``D:\\proj\\tex`` -> ``D_proj_tex``；只保留字母数字/中文/._-，空段丢弃。"""
    path = os.path.normpath(directory or "")
    drive, rest = os.path.splitdrive(path)
    parts = [drive.rstrip(":").upper()] if drive else []
    parts += [seg for seg in re.split(r"[\\/]+", rest) if seg]
    key = "_".join(parts) or "files"
    key = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", key, flags=re.UNICODE)
    return key.strip("_.") or "files"


def _ext_chain(basename):
    """文件名的扩展名链（小写、去掉首点）：``cache.bgeo.sc`` → ``bgeo.sc``。

    Houdini 几何缓存常是 ``.bgeo.sc`` / ``.bgeo.gz`` 双扩展名，只看最后一段
    ``.sc`` 会误判，先尝试整链再回退到最后一段。帧号模板 ``$F4`` / ``<UDIM>``
    与点同级，取扩展名时从右侧取连续字母段。
    """
    name = (basename or "").replace("\\", "/").rpartition("/")[2]
    name = name.strip().strip('"')
    parts = name.rsplit(".", 2)
    candidates = []
    if len(parts) >= 3:
        candidates.append(".".join(p.lower() for p in parts[-2:]))
    if len(parts) >= 2:
        candidates.append(parts[-1].lower())
    return candidates


def category_for(path, kind="input"):
    """按文件类型 + 引用语义归入 Houdini 官方 New Project 目录。

    - 几何 ``.bgeo[.sc] / .vdb / .obj / .fbx / .usd`` → ``geo``；
    - ``.abc`` → ``abc``；``.sim`` → ``sim``；HDA → ``hda``；
    - 图片/序列：输入引用 → ``tex``，输出引用（渲染结果）→ ``render``；
    - 视频序列 → ``flip``；脚本 → ``scripts``；其余 → ``other``。

    ``kind`` 取自 collect.Ref.kind（``input`` / ``output``）；filecache 的缓存
    是 output 但扩展名是 bgeo，仍按扩展名优先归 geo（不进 render）。
    """
    chain = _ext_chain(path or "")
    for ext in chain:
        if ext in _EXT_GEO_CHAIN or ext in _EXT_GEO:
            return CATEGORY_GEO
        if ext in _EXT_ABC:
            return CATEGORY_ABC
        if ext in _EXT_SIM:
            return CATEGORY_SIM
        if ext in _EXT_HDA:
            return CATEGORY_HDA
        if ext in _EXT_FLIP:
            return CATEGORY_FLIP
        if ext in _EXT_SCRIPTS:
            return CATEGORY_SCRIPTS
        if ext in _EXT_IMAGE:
            return CATEGORY_RENDER if kind == "output" else CATEGORY_TEX
    return CATEGORY_OTHER


def default_package_name(hip_path, suffix="-pack"):
    """``D:/proj/shot_v001.hip`` → ``shot_v001-pack``；无路径回落默认包名。

    包名默认取工程名 + ``-pack``（用户要求）。工程名含空格等非法字符时交给
    sanitize_pkg_name 收敛；空值（未保存工程）回落默认包名。
    """
    if not hip_path:
        return PACKAGE_DIR
    stem = os.path.splitext(os.path.basename(hip_path.replace("\\", "/")))[0]
    return sanitize_pkg_name(stem + suffix) if stem else PACKAGE_DIR


def is_builtin_ref(raw):
    """参数原始写法是否指向 Houdini 安装目录（``$HH`` / ``$HFS``）。

    ``$HH`` = ``$HFS/houdini``，展开后是安装目录里的内置资源
    （如 ``$HH/geo/defgeo.bgeo`` 默认几何体），**不是用户工程素材**。
    这类引用按用户要求仍列在打包列表里（可见、由勾选决定是否打包），
    但状态列要标「内置」，避免把它当成需要搬运的工程文件。
    """
    value = (raw or "").strip().strip('"').lower()
    return value.startswith(("$hh/", "${hh}", "$hfs/", "${hfs}"))


def scene_signature(hip_path, is_new_file):
    """场景指纹（未保存 / 已保存的具体 hip 路径）——用于 new/open 后清空日志。

    window_manager 让工具窗口跨工程复用；两次打开若是不同工程（或其中一次是
    新工程），指纹变化，窗口据此丢弃上一工程的日志与扫描结果，避免残留。
    """
    if is_new_file:
        return ("new", "")
    return ("hip", os.path.normcase(os.path.normpath(hip_path or "")))


def is_within(path, root):
    """path 是否在 root 之内（大小写不敏感，Windows 语义）。"""
    if not path or not root:
        return False
    try:
        p = os.path.normcase(os.path.normpath(path))
        r = os.path.normcase(os.path.normpath(root))
        return os.path.commonpath([p, r]) == r
    except ValueError:
        return False


def rel_posix(path):
    return (path or "").replace("\\", "/")


def relative_to_hip(abs_path, hip_dir):
    """绝对路径在 $HIP 内 → 返回 ``$HIP/<相对>``；否则返回空串。"""
    if not is_within(abs_path, hip_dir):
        return ""
    rel = os.path.relpath(abs_path, hip_dir)
    return "$HIP/" + rel_posix(rel)


def subdir_for_dir(directory, hip_dir=""):
    """目录在包内 `assets/` 下的子目录（目录版 asset_subdir）。"""
    if hip_dir and is_within(directory, hip_dir):
        rel = os.path.relpath(directory, hip_dir)
        if rel in (".", ""):
            return ""
        return rel_posix(rel)
    return flatten_dir(directory)


def asset_subdir(abs_path, hip_dir=""):
    """素材在包内 `assets/` 下的子目录。

    - 文件在 `$HIP` 内 → 保留**相对 $HIP 的目录结构**（`$HIP/geo/x.bgeo.sc`
      → `geo`），不再把整条绝对路径拍平；
    - 包外 → 用展平后的源目录（`D:\\proj\\tex` → `D_proj_tex`）。
    """
    return subdir_for_dir(os.path.dirname(abs_path), hip_dir)


def package_target(abs_path, prefix, hip_dir=""):
    """包内目标（Houdini 写法）：``<prefix>/assets/<子目录>/<文件名>``。

    ``prefix`` 由调用方按**包根选项**算（见 ``target_prefix``）：
    ``$JOB`` → ``$JOB/<包名>``、``$HIP`` → ``$HIP``。生成方式固定
    「另存到包目录」，旧版「覆盖当前工程」分支已删除。
    """
    sub = asset_subdir(abs_path, hip_dir)
    tail = "/" + sub if sub else ""
    return "{}/assets{}/{}".format(prefix, tail, os.path.basename(abs_path))


def package_abs_target(abs_path, base_dir, package_name, hip_dir=""):
    """同 package_target，但返回磁盘绝对路径（用于复制）。"""
    sub = asset_subdir(abs_path, hip_dir)
    parts = [base_dir, package_name, PACKAGE_ASSETS]
    if sub:
        parts.append(sub)
    parts.append(os.path.basename(abs_path))
    return os.path.normpath(os.path.join(*parts))


def package_dir_abs(base_dir, package_name=PACKAGE_DIR):
    return os.path.join(base_dir, package_name)


def package_abs_path(base_dir, package_name, sub, name):
    """``<base>/<pkg>/assets/<sub>/<name>`` 的磁盘绝对路径（复制用）。"""
    parts = [base_dir, package_name, PACKAGE_ASSETS]
    if sub:
        parts.append(sub)
    parts.append(name)
    return os.path.normpath(os.path.join(*parts))


def category_subdir_for(path, kind="input", hip_dir="", dir_path=None):
    """按文件类型返回素材在包内的顶层目录（Houdini 官方 New Project 结构）。

    ``<category>[/<raw sub>]/``：category 取 category_for(path, kind)，raw 取
    原始展平/相对目录（dir_path 缺省为 path 的父目录）。若 raw 首段已等于
    category（如 ``$HIP/geo/x.bgeo`` 的 raw=``geo``），不再重复前缀。

    包内不再有统一 ``assets/`` 层，顶层直接是 ``geo/ tex/ render/ …``，
    与 File > New Project 生成的工程目录一致。
    """
    cat = category_for(path, kind)
    raw = subdir_for_dir(dir_path if dir_path is not None
                         else os.path.dirname(path or ""), hip_dir)
    first = raw.replace("\\", "/").split("/")[0].lower() if raw else ""
    if raw and first == cat:
        return raw
    return "{}/{}".format(cat, raw) if raw else cat


def package_cat_abs_path(base_dir, package_name, sub, name):
    """``<base>/<pkg>/<sub>/<name>`` 的磁盘路径（分类布局，无 assets 层）。"""
    parts = [base_dir, package_name]
    if sub:
        parts.append(sub)
    parts.append(name)
    return os.path.normpath(os.path.join(*parts))


def cat_target(prefix, sub, name=""):
    """分类布局的 Houdini 写法目标：``<prefix>/<sub>[/<name>]``。"""
    base = "{}/{}".format(prefix, sub) if sub else prefix
    return "{}/{}".format(base, name) if name else base


# 包名非法字符：Windows 文件名禁用符 + 控制字符 + 路径分隔符。分隔符被
# 换掉后 "../.." 这类穿越写法只剩字面下划线，写不出包根之外
_PKG_NAME_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


# Windows 保留设备名（不区分大小写；带扩展名也不允许，如 CON.txt）
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


def sanitize_pkg_name(text, default=PACKAGE_DIR):
    """包名净化成合法目录名：非法字符换 _、剥首尾点/空格、避开保留设备名。

    包名来自 UI 输入、直接进 ``os.path.join(base, pkg, ...)``：不净化的话
    ".."、含 ``/`` 的输入会写出包根之外，Windows 禁用符则让复制/zip 直接
    报错。首尾点剥掉防 ``..``（点全剥后空串走默认）与 Windows 尾点陷阱。
    保留设备名（CON/NUL/COM1…）即使带扩展名也不能做目录名，统一加 pkg_ 前缀。
    """
    value = _PKG_NAME_RE.sub("_", (text or "").strip())
    value = value.strip(". ")
    if not value:
        return default
    stem = value.split(".")[0].upper()
    if stem in _RESERVED_NAMES:
        value = "pkg_" + value
    return value


def target_prefix(root, package_name):
    """打包后参数的前缀（Houdini 写法），按**包根选项**生成（纯函数）。

    2026-11-21 用户拍板：生成方式固定「另存到包目录」（删除覆盖当前工程），
    包根只保留 ``$HIP`` / ``$JOB``；两者共用界面的「输出目录」输入框（留空 =
    当前工程目录，可填任意绝对目录改变产物落点），参数前缀不随输出目录变化：
    选 ``$HIP`` 仍写 ``$HIP``（另存 hip 后会话 $HIP 自动指向包目录），选 ``$JOB``
    由工具用 ``hou.putenv`` 把会话 ``$JOB`` 指向该目录。不读取任何环境变量配置。

    - ``JOB`` → ``$JOB/<包名>``：``$JOB`` 是会话里被固定住的项目根，另存
      后它不会随 ``$HIP`` 变化，必须带包名才指向包目录；
    - ``HIP`` → ``$HIP``：hip 另存进 ``<包>/`` 后会话的 ``$HIP`` 就是包目录，
      **省略包名**，否则多一级目录、路径失效。
    """
    if root == "JOB":
        return "$JOB/{}".format(package_name)
    return "$HIP"


def zip_path_for(hip_path):
    """``$HIP/shot.hip`` -> ``$HIP/shot_package.zip``。"""
    stem, _ = os.path.splitext(hip_path)
    return stem + "_package.zip"


def human_size(num):
    size = float(num or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return "{:.1f} {}".format(size, unit) if unit != "B" else "{} B".format(int(size))
        size /= 1024
    return "{:.1f} TB".format(size)
