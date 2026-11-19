"""打包工程的纯路径逻辑（不 import hou / PySide6，可直接无头测试）。

职责：
- 识别文件名里的帧模板（``$F`` / ``$F4`` / ``${FF2}`` …），把模板与具体帧分开处理；
- 判断哪些路径需要"搬进 $HIP"（只有绝对路径需要，`$HIP/...` 与相对路径已经
  可移植；绝对判定兼容 POSIX 形态——``/proj/...`` 在 Windows 上
  ``os.path.isabs`` 返 False、Houdini 视为绝对，同 filecache_abs_path._is_abs）；
- 生成包内目标路径（``$HIP/ht_package/<展平目录>/<文件名>``）；
- 包名净化（用户输入直接进 os.path.join，必须收敛）、zip 路径、可读大小。
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

# 包内素材子目录
PACKAGE_ASSETS = "assets"

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

    ``prefix`` 由调用方按**生成方式**算（这是最容易写错的地方）：

    - 覆盖当前工程（hip 留在原地）：``$HIP/<包名>``（或 ``$JOB/<包名>``）；
    - 另存到包目录（hip 存进包目录）：**``$HIP``**——保存后 hip 的 ``$HIP``
      就是包目录，再带一层包名会多出一级目录、路径直接失效。
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
