"""Project paths and shared constants."""

import os
from pathlib import Path

# constants.py -> core -> houtools -> python3.13libs -> project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

PYTHON_LIBS_DIR = PROJECT_ROOT / "python3.13libs"
MENU_FILE = PROJECT_ROOT / "MainMenuCommon.xml"


def settings_env_override():
    """tier1 命中时返回归一化的覆盖路径，未配置/不合法返回空串。

    与 SETTINGS_DIR 的 tier1 同一套判定；设置窗口的「由
    HOUTOOLS_SETTINGS_DIR 指定」标注复用本函数，不重复实现。
    """
    env = (os.environ.get("HOUTOOLS_SETTINGS_DIR") or "").strip()
    if env and "$" not in env and os.path.isabs(env):
        return os.path.normpath(env)
    return ""


def _resolve_settings_dir():
    """settings 目录解析链（与 HouTools.json 包清单的 env 条目配套）。

    1. HOUTOOLS_SETTINGS_DIR 显式指定（HouTools.json 包清单的 env 条目或
       shell 环境）：须为不含 $ 的绝对路径——$ 字面量说明包清单的变量
       展开没发生，照单全收会 mkdir 出名字带 $ 的目录；相对路径锚不到
       稳定位置，同样拒绝；
    2. Houdini 进程内回落 HOUDINI_USER_PREF_DIR 的上一级（GUI 默认
       Documents/houdiniXX.Y → Documents/HouTools/settings；H21/H22 的
       版本目录被 ".." 折叠，双版本共享同一 settings；HOUDINI_USER_PREF_DIR
       怎么定，settings 就跟到哪，与 Houdini 自身的配置位置同根）；
    3. 纯 python 无头（冒烟测试自带 env，实际到不了这）退回插件根内，
       与 1.9.x 的旧落点保持一致。
    """
    override = settings_env_override()
    if override:
        return Path(override)
    pref = os.environ.get("HOUDINI_USER_PREF_DIR")
    if pref:
        return Path(os.path.normpath(
            os.path.join(pref, os.pardir, "HouTools", "settings")))
    return PROJECT_ROOT / "settings"


SETTINGS_DIR = _resolve_settings_dir()
