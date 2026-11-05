"""ffmpeg / ffprobe 查找与子进程辅助。

查找优先级（2026-11 改版）：
1. ``HOUTOOLS_FFMPEG`` env —— 包清单 HouTools.json 提供默认值（指向
   HouTools 配置目录，与 HOUTOOLS_SETTINGS_DIR 同根），把 ffmpeg.exe /
   ffprobe.exe 放进该目录即生效；值也可以直接写成 ffmpeg 完整路径或
   其他目录。指向不存在时视为未配置、静默落下一级（默认值常驻，"设了
   没放文件"是多数用户的常态，不告警）。
2. 系统 PATH 上的 ``hffmpeg`` / ``ffmpeg``（h 名优先）
3. ``$HFS/bin/hffmpeg`` —— Houdini 自带兜底（SideFX 只发 h 前缀这组
   名字，$HFS/bin 下不存在通用 ffmpeg，不查询）

ffprobe 不设独立变量：find_ffprobe 从选中的 ffmpeg 同目录推导（配套
版本最准，含 hffmpeg -> hffprobe 名字映射），缺失再回退 PATH 与
$HFS/bin/hffprobe——ffmpeg/ffprobe 本就成套分发，一处指定足够。

项目根 ``ffmpeg.exe`` 第一优先级已删（2026-11）：网络盘部署下共享对
可执行文件不授执行权（全部 exe 启动报 WinError 5），客户端无绕过手法，
自定义入口改走每用户本地目录。
"""

import os
import shutil
import subprocess
from typing import Optional

from houtools.core.log import get_logger

ENV_FFMPEG = "HOUTOOLS_FFMPEG"

logger = get_logger("videoseq.ffmpeg")


def _custom_ffmpeg() -> Optional[str]:
    """读 HOUTOOLS_FFMPEG：值是目录则按 h 名优先在目录内找 ffmpeg、是
    文件则直接用；不存在一律返回 None——清单默认值常驻，"设了没放
    文件"是常态，静默跳过（理由见模块 docstring）。"""
    value = (os.environ.get(ENV_FFMPEG) or "").strip()
    if not value:
        return None
    hexe = "hffmpeg.exe" if os.name == "nt" else "hffmpeg"
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    if os.path.isdir(value):
        for name in (hexe, exe):
            candidate = os.path.join(value, name)
            if os.path.isfile(candidate):
                return candidate
        return None
    if os.path.isfile(value):
        return value
    logger.debug("%s 指向的路径不存在，忽略: %r", ENV_FFMPEG, value)
    return None


def find_ffmpeg() -> Optional[str]:
    """定位可用的 ffmpeg 可执行文件，找不到返回 None。

    优先级：HOUTOOLS_FFMPEG（清单默认指 HouTools 配置目录）→
    PATH（hffmpeg 优先）→ $HFS/bin/hffmpeg。
    """
    hexe = "hffmpeg.exe" if os.name == "nt" else "hffmpeg"
    exe = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"

    custom = _custom_ffmpeg()
    if custom:
        return custom

    for name in (hexe, exe):
        found = shutil.which(name)
        if found:
            return found

    hfs = os.environ.get("HFS", "")
    if hfs:
        candidate = os.path.join(hfs, "bin", hexe)
        if os.path.isfile(candidate):
            return candidate
    return None


def find_ffprobe(ffmpeg_path: str) -> Optional[str]:
    """从 ffmpeg 路径推导同目录 ffprobe（兼容 hffmpeg -> hffprobe），
    同目录没有时回退 PATH 与 $HFS/bin 的 hffprobe/ffprobe（h 名优先）。

    同目录推导即"配套版本"语义——ffmpeg/ffprobe 成套分发，探测读容器
    元数据对构建版本不敏感，借用别处的 ffprobe 也准，但配套的最准。
    """
    if not ffmpeg_path:
        return None
    directory = os.path.dirname(ffmpeg_path)
    basename = os.path.basename(ffmpeg_path)

    for name in ("ffprobe.exe", "ffprobe"):
        candidate = os.path.join(directory, name)
        if os.path.isfile(candidate):
            return candidate

    lower = basename.lower()
    if lower.startswith("h") and "ffmpeg" in lower:
        probe_name = lower.replace("ffmpeg", "ffprobe")
        candidate = os.path.join(directory, probe_name)
        if os.path.isfile(candidate):
            return candidate

    probe_names = ("hffprobe.exe", "ffprobe.exe") if os.name == "nt" \
        else ("hffprobe", "ffprobe")
    for name in probe_names:
        found = shutil.which(name)
        if found:
            return found

    hfs = os.environ.get("HFS", "")
    if hfs:
        candidate = os.path.join(hfs, "bin", probe_names[0])
        if os.path.isfile(candidate):
            return candidate
    return None


def get_startup_kwargs() -> dict:
    """Windows 下隐藏 ffmpeg 子进程的控制台窗口。"""
    kwargs = {}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs
