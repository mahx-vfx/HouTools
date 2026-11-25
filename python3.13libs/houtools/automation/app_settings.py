"""Automation — 应用级设置的唯一存储入口
=========================================
把原来分散在两份文件里的应用级配置合并到
``{SETTINGS_DIR}/Automation_Config.json`` 的**两个键**：

- ``dw_exe_path``：「打开DW」任务的软件路径（原 Automation_Config.json）
- ``apps``：跨应用执行的版本列表（原 Automation_apps.json）

两者都是逐机、跨 H21/H22 共享、不随 ``$HIP`` 走的应用级设置，
但**写入方和时机不同**：DW 路径由设置窗口保存，版本列表由设置窗口
表格保存，而 Automation 面板每次打开还会兜底补建。合并成单一文件后，
若各自仍是「读整份 → 改自己那个键 → 整份写回」，两个写入方会互相
覆盖（双开 Houdini / 面板与设置窗口同时开着都会触发）。

因此这里统一走 ``core.settings.JsonStore``：**脏键合并 + 跨进程 named
mutex + 坏文件留底 + tmp/os.replace 原子写**，与 hotkeys / hdrlight /
recipelib 对齐。所有写入都是 ``set(save=True)`` 立即落盘；读取每次
重读磁盘，保证手改 JSON 后重开/刷新面板即生效，不必重启 Houdini。

本模块**无 hou / 无 Qt 依赖**，可无头测试。``data_manager`` 与
``external_run`` 只保留薄封装的公开 API，不再各自实现一套 IO。
"""

import copy
import json
import os
import threading

from houtools.core.log import get_logger
from houtools.core.settings import JsonStore

logger = get_logger("automation.app_settings")

CONFIG_FILE = "Automation_Config.json"
# 旧文件只在首次访问时迁移一次，改名留底、不删除
LEGACY_APPS_FILE = "Automation_apps.json"

# 打开DW 任务的默认可执行文件路径
DW_EXE_PATH_DEFAULT = "C:/Program Files/Thinkbox/Deadline10/bin/deadlineworker.exe"

# 版本列表出厂默认；代码里不存在任何版本枚举，用户可任意增删改
DEFAULT_APPS = [
    {"id": "H21",
     "exe": "C:/Program Files/Side Effects Software/Houdini 21.0.512/bin/houdinifx.exe"},
    {"id": "H22",
     "exe": "C:/Program Files/Side Effects Software/Houdini 22.0.429/bin/houdinifx.exe"},
]

_STORE = None
_INIT_LOCK = threading.Lock()


def _defaults() -> dict:
    """defaults 每次深拷贝，避免多个 Store 共享同一个可变 list。"""
    return {"dw_exe_path": DW_EXE_PATH_DEFAULT,
            "apps": copy.deepcopy(DEFAULT_APPS)}


def _default_apps() -> list[dict]:
    return copy.deepcopy(DEFAULT_APPS)


def _clean_apps(raw) -> list[dict]:
    """净化版本列表：跳过非对象 / 缺 id 或 exe / 重复 id 的条目。

    **不校验 exe 是否存在** —— 用户可能先写好还没安装的版本，路径写错
    时由 ``external_run.resolve_exe()`` 给明确报错，而不是让条目消失。
    """
    if not isinstance(raw, list):
        return []
    clean, seen = [], set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        app_id = str(item.get("id") or "").strip()
        exe = str(item.get("exe") or "").strip()
        if not app_id or not exe or app_id in seen:
            continue
        seen.add(app_id)
        clean.append({"id": app_id, "exe": os.path.normpath(exe)})
    return clean


def _migrate_legacy_apps(store: JsonStore) -> None:
    """把未合并的 ``Automation_apps.json`` 一次性并入主配置文件。

    该文件是 2026-11 开发期新增、未随发行版发布过的过渡文件；迁移后
    改名 ``.migrated`` **留底**，不删除。主配置文件里已有 ``apps`` 键
    时说明合并过，只做留底改名，不再读取旧内容。写入失败时保留旧文件，
    下次启动重试，避免用户自定义版本列表看起来凭空丢失。
    """
    legacy = store.path.parent / LEGACY_APPS_FILE
    if not legacy.is_file():
        return

    on_disk = {}
    if store.path.is_file():
        try:
            loaded = json.loads(store.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                on_disk = loaded
        except Exception as exc:
            logger.debug("迁移时读取主配置失败（按未合并处理）: %s", exc)

    ok = True
    if "apps" not in on_disk:
        apps = []
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
            apps = _clean_apps(data.get("apps") if isinstance(data, dict) else None)
        except Exception as exc:
            logger.warning("读取旧版本配置失败（保留原文件待人工处理）: %s (%s)",
                           legacy, exc)
            ok = False
        if ok and apps:
            # JsonStore.save 会把磁盘最新内容 + 本脏键合并写回，
            # 用户已有的 dw_exe_path 不会被覆盖
            ok = store.set("apps", apps)
            if not ok:
                logger.warning("旧版本配置合并失败（保留原文件，下次重试）: %s",
                               legacy)

    if ok:
        try:
            os.replace(legacy, legacy.with_name(legacy.name + ".migrated"))
            logger.info("旧版本配置已合并并留底: %s.migrated", legacy)
        except OSError as exc:
            logger.warning("旧版本配置留底改名失败（原文件保留）: %s (%s)",
                           legacy, exc)


def store() -> JsonStore:
    """返回唯一的 JsonStore 实例（懒加载 + 一次性迁移 + 补建）。"""
    global _STORE
    if _STORE is None:
        with _INIT_LOCK:
            if _STORE is None:
                s = JsonStore(CONFIG_FILE, defaults=_defaults())
                _migrate_legacy_apps(s)
                if not s.path.is_file():
                    s.save()          # 无脏键 + 文件缺失 → 按 defaults 补建
                _STORE = s
    return _STORE


def config_path() -> str:
    """应用级配置文件的绝对路径（错误提示与设置窗口展示用）。"""
    return str(store().path)


def reload() -> None:
    """重读磁盘最新内容（手改 JSON / 另一进程写入后刷新面板时调用）。

    所有写入都是 ``set(save=True)`` 立即落盘，正常路径上没有未保存的
    脏键；若上一次 ``save()`` 失败，脏键会被清掉、界面回到磁盘实际值，
    这与「保存失败」的提示一致，重试保存即可。
    """
    store().load()


def ensure() -> dict:
    """兜底补建并返回配置内容（Automation 面板每次打开时调用）。

    JsonStore 的 defaults 只在**内存**里合并，磁盘文件若存在但缺键，
    用户手改时就看不到该写什么。因此这里保持合并前
    ``ensure_dw_config()`` 的语义：文件缺 ``dw_exe_path`` / ``apps``
    时把缺失键补写回磁盘（走脏键合并，不覆盖用户其它键）。
    """
    s = store()
    s.load()
    on_disk = {}
    if s.path.is_file():
        try:
            loaded = json.loads(s.path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                on_disk = loaded
        except Exception as exc:
            logger.debug("兜底检查时读取应用配置失败: %s", exc)
    missing = [key for key in ("dw_exe_path", "apps") if key not in on_disk]
    if not s.path.is_file():
        s.save()                       # 无脏键 + 文件缺失 → 按 defaults 补建
    elif missing:
        defaults = _defaults()
        for key in missing:
            s.set(key, defaults[key], save=False)
        s.save()                       # 磁盘最新内容 + 这两个脏键
    return s.as_dict()


def get_dw_exe_path() -> str:
    """「打开DW」软件路径；缺键 / 空值回退出厂默认。"""
    s = store()
    s.load()
    value = s.get("dw_exe_path")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return DW_EXE_PATH_DEFAULT


def set_dw_exe_path(value: str) -> bool:
    """保存「打开DW」软件路径（空值视为恢复默认）。"""
    text = str(value or "").strip()
    return store().set("dw_exe_path", text or DW_EXE_PATH_DEFAULT)


def get_apps() -> list[dict]:
    """跨应用执行版本列表（每次重读磁盘，改完刷新面板即生效）。"""
    s = store()
    s.load()
    return _clean_apps(s.get("apps"))


def set_apps(apps) -> bool:
    """保存版本列表（设置窗口表格保存入口）。"""
    return store().set("apps", _clean_apps(apps))


def reset_apps() -> bool:
    """恢复 H21 / H22 两条出厂默认（设置窗口「恢复默认」入口）。"""
    return store().set("apps", _default_apps())
