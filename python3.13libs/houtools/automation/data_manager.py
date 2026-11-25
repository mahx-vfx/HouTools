"""
Automation — Data Manager 数据持久化模块
==============================================
提供 AutomationDataManager 类，负责 JSON 文件的读写和任务对象的
序列化/反序列化。

所有方法均为 @classmethod，因为全局只需一个逻辑实例，
但数据路径依赖 $HIP（动态计算），不使用 BaseJsonManager。

**多文件支持**:三方法 ``get_data_path`` / ``load`` / ``save`` 均接受
可选的 ``filename`` 参数(无 ``.json`` 后缀),用于支持 UI 的多配置
下拉——用户可选择配置目录下任一现存配置;经"新建配置"对话框输入
新名会立即落盘一份空配置(``{"tasks": []}``)。``filename=None``
走默认 ``Automation.json``,保持向后兼容。
"""

import os
import json
import re
import socket
import tempfile
import threading
from dataclasses import asdict, fields
from typing import Optional

from .task_types import (
    TaskType,
    TaskItem,
    ButtonClickParams,
    FlipbookParams,
    HomeAssistantParams,
    OpenDWParams,
    ExternalRunParams,
)

from houtools.core.log import get_logger
from . import app_settings as _aps
logger = get_logger("automation.data")

# 出厂默认值定义在统一设置层；此处保留别名兼容既有引用（含 smoke_test）
DW_EXE_PATH_DEFAULT = _aps.DW_EXE_PATH_DEFAULT

# $HIP 不可用、配置落系统临时目录的告警只发一次:路径计算是高频调用,
# 每次都告警会刷屏;无头测试等合法场景同样走 fallback
_HIP_FALLBACK_WARNED = False


class ConfigConflictError(Exception):
    """配置写盘冲突（乐观并发检测）：盘上 rev 与调用方基线不一致。

    冲突时**不写盘、不留底**——由调用方（window 的覆盖/另存/取消
    三选弹窗）决定处理方式。``disk_rev`` 是检测到的盘上 rev，
    ``path`` 是配置文件路径。
    """

    def __init__(self, path: str, disk_rev: int):
        self.path = path
        self.disk_rev = disk_rev
        super().__init__(
            f"配置已被其他机器修改 (disk rev={disk_rev}): {path}")


# 计算机名作配置文件名时需要替换的字符:Windows 文件名非法字符、
# 路径分隔符、控制符(NetBIOS 名允许的空格/点/连字符等保留)
_HOSTNAME_ILLEGAL = re.compile(r'[<>:"|?*/\\\x00-\x1f]')


class AutomationDataManager:
    """Automation 数据持久化管理器。

    所有方法均为类方法，无需实例化即可使用。
    数据以 JSON 格式存储在 ``{HIP}/HouTools_cfg/Automation_json/Automation.json``，
    $HIP 不可用时 fallback 到系统临时目录。
    """

    # ── 路径管理 ─────────────────────────────────────────

    @classmethod
    def _hip_base(cls) -> str:
        """返回配置根目录：Houdini 内为 $HIP，否则 fallback 系统临时目录。"""
        global _HIP_FALLBACK_WARNED
        try:
            import hou  # noqa: N812 — only available inside Houdini
            hip = hou.getenv("HIP")
            if hip:
                return hip
        except ImportError:
            pass
        base = tempfile.gettempdir()
        if not _HIP_FALLBACK_WARNED:
            _HIP_FALLBACK_WARNED = True
            logger.warning("未取到 $HIP，Automation 配置将落在系统临时目录: %s", base)
        return base

    @classmethod
    def config_hostname(cls) -> str:
        """返回本机计算机名（socket.gethostname），净化为合法文件名。

        用作默认配置名与「上次使用」标记的机器命名空间：多机器共用
        工程目录时各写各的配置文件，"改动即存"不再互相覆盖（2026-11
        用户拍板；计算机名在公司内网唯一且不随 DHCP 变，比 IP/用户名
        稳）。净化规则：Windows 文件名非法字符/路径分隔符/控制符替换
        为 ``_``，去掉结尾的点和空格；净化后为空（极端机器名）回退
        ``"Automation"`` 保持旧行为。
        """
        raw = ""
        try:
            raw = socket.gethostname() or ""
        except Exception as exc:  # gethostname 极少失败，留痕可查
            logger.warning("获取计算机名失败，回退默认配置名: %s", exc)
        name = _HOSTNAME_ILLEGAL.sub("_", raw).strip().rstrip(". ")
        return name if name else "Automation"

    @classmethod
    def get_data_path(cls, filename: Optional[str] = None) -> str:
        """返回数据文件的绝对路径(纯计算,不创建目录)。

        优先级:
          1. ``hou.getenv("HIP")`` — Houdini $HIP 环境变量
          2. ``tempfile.gettempdir()`` — 系统临时目录(fallback)

        Args:
            filename: 配置文件 basename(**无** ``.json`` 后缀)。
                      ``None`` 时返回默认 ``Automation.json``。

        ``hou`` 只在函数内部 try/except 导入,避免 Houdini 外 ImportError。

        **无副作用**:不创建任何文件/目录。配置目录只在 ``save()`` 真正
        写入时才创建(由调用方负责)。
        """
        base = cls._hip_base()

        name = filename if filename else "Automation"
        return os.path.join(base, "HouTools_cfg", "Automation_json", f"{name}.json")

    # ── 上次使用配置标记（随工程持久化，2026-11 用户拍板） ─────

    @classmethod
    def get_last_config_path(cls, hostname: Optional[str] = None) -> str:
        """返回"上次使用配置"标记文件路径:
        ``{HIP}/HouTools_cfg/Automation_last.<主机名>.json``。

        **按机器分文件**（2026-11 用户拍板）：多机器共用工程目录时，
        共享单文件标记会互相挤占（后写者覆盖前者的"上次选择"），按
        计算机名分文件后各机器只写自己的标记。``hostname=None`` 取
        本机（``config_hostname``）。与任务配置同随工程走；放在
        ``HouTools_cfg/`` 顶层而非 ``Automation_json/`` 内——后者
        的 ``*.json`` 会被 ``list_configs`` 枚举进配置下拉，标记文件
        混进去会污染下拉。"""
        host = hostname if hostname else cls.config_hostname()
        return os.path.join(cls._hip_base(), "HouTools_cfg",
                            f"Automation_last.{host}.json")

    @classmethod
    def _legacy_last_config_path(cls) -> str:
        """旧版共享标记路径（按工程单文件、不分公司机器）。

        2026-11 改按机器分文件前的存量：只读兼容（升级用户的"上次
        配置"不丢），永不回写。"""
        return os.path.join(cls._hip_base(), "HouTools_cfg",
                            "Automation_last.json")

    @staticmethod
    def _read_last_marker(path: str) -> Optional[str]:
        """读单个标记文件，返回配置名；缺失/损坏/内容非法返回 None。"""
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.warning("读取上次使用配置失败（按未持久化处理）: %s (%s)",
                           path, exc)
            return None
        if not isinstance(data, dict):
            logger.warning("上次使用配置标记顶层不是对象（按未持久化处理）: %s",
                           path)
            return None
        name = data.get("last_config")
        if isinstance(name, str) and name.strip():
            return name.strip()
        return None

    @classmethod
    def load_last_config(cls) -> Optional[str]:
        """读取本机上次使用的配置名；缺失/损坏/内容非法一律返回 None。

        先读本机标记（``Automation_last.<主机名>.json``），缺失时回读
        旧版共享标记（``Automation_last.json``，只读兼容——升级用户的
        上次选择不丢）。标记是可再生缓存：只读不迁移、不判配置名合法性
        （调用方拿 ``list_configs`` 自查存在性），损坏直接按未持久化
        处理，下次切换配置重写。"""
        name = cls._read_last_marker(cls.get_last_config_path())
        if name is not None:
            return name
        return cls._read_last_marker(cls._legacy_last_config_path())

    @classmethod
    def save_last_config(cls, name: str) -> bool:
        """记录上次使用的配置名（tmp + os.replace 原子写，同 save() 手法）。

        失败只告警返回 False——持久化是可再生缓存，失败不影响配置切换
        本身，下次切换会重写。"""
        path = cls.get_last_config_path()
        tmp_path = path + ".~tmp%d_%d" % (os.getpid(), threading.get_ident())
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump({"last_config": str(name)}, f,
                          ensure_ascii=False, indent=2)
            os.replace(tmp_path, path)
            return True
        except Exception as exc:
            logger.warning("保存上次使用配置标记失败: %s (%s)", path, exc)
            try:
                os.unlink(tmp_path)
            except OSError as exc2:
                logger.debug("cannot remove last-config tmp %s: %s",
                             tmp_path, exc2)
            return False

    @classmethod
    def read_config_rev(cls, filename: Optional[str] = None) -> int:
        """读取配置文件顶层的 ``rev`` 计数（乐观并发冲突检测的盘上基线）。

        文件缺失/损坏/顶层非对象/字段非法一律 0——损坏文件由 ``save()``
        的留底覆盖流程接管（那边有 warning），此处不重复告警（rev 读点
        在每次保存后都会经过，warning 会刷屏，只留 debug 可检索）。"""
        path = cls.get_data_path(filename)
        try:
            if not os.path.exists(path):
                return 0
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            logger.debug("读取配置 rev 失败（按 0 处理）: %s (%s)", path, exc)
            return 0
        if not isinstance(data, dict):
            return 0
        value = data.get("rev", 0)
        if isinstance(value, bool) or not isinstance(value, int):
            return 0
        return value

    # ── 应用级配置（统一走 app_settings 的 JsonStore） ───────

    @classmethod
    def get_app_config_path(cls) -> str:
        """返回应用级配置文件路径 ``{SETTINGS_DIR}/Automation_Config.json``。

        文件中 ``dw_exe_path``（打开DW 软件路径）与 ``apps``（跨应用执行
        版本列表）两个键共存；读写统一经 ``automation.app_settings`` 的
        JsonStore（脏键合并 + 跨进程 named mutex），面板补建与设置窗口
        保存这两个写入方不会互相覆盖。
        """
        return _aps.config_path()

    @classmethod
    def ensure_dw_config(cls) -> dict:
        """确保应用级配置文件可用并返回内容（面板打开时兜底补建）。

        原来这里手写的一套 tmp+os.replace / 坏文件留底已移除：该文件
        现在与 ``apps`` 共用一个 ``app_settings`` 的 JsonStore，补建、
        脏键合并、跨进程互斥都由统一存储层负责。保留方法名是为了不
        改动面板与引擎的调用点。
        """
        return _aps.ensure()

    @classmethod
    def load_dw_exe_path(cls) -> str:
        """打开DW 软件路径（只读，不修改文件）；缺键/空值回退出厂默认。

        每次重读磁盘，手改配置文件后重开或刷新面板即生效。
        """
        return _aps.get_dw_exe_path()

    # ── 核心 IO ──────────────────────────────────────────

    @classmethod
    def _backup_corrupt(cls, path: str) -> bool:
        """把解析失败的配置文件改名 ``.bak`` 留底（与 core/settings.py 的
        JsonStore 同语义）：配置读不出来时先留原件，防止后续 ``save()`` 按
        "读取失败 → 覆盖写"把尚可抢救的内容直接冲掉。

        返回是否留底成功。**写入方必须检查返回值**：留底失败（权限/占用
        等）时继续覆盖会把唯一原件冲掉——拒绝本次写入，原件留待用户处理。
        """
        bak = path + ".bak"
        try:
            os.replace(path, bak)
            logger.warning("损坏的配置文件已留底: %s", bak)
            return True
        except OSError as exc:
            logger.warning("无法留底损坏的配置文件 %s: %s", path, exc)
            return False

    @classmethod
    def load(cls, filename: Optional[str] = None) -> list[dict]:
        """从当前 ``$HIP`` 的配置目录读取任务列表（行为不变）。

        Args:
            filename: 配置文件 basename(**无** ``.json`` 后缀)。
                      ``None`` 时加载默认 ``Automation.json``。

        返回 ``data["tasks"]``,若文件不存在或 JSON 损坏则返回空列表。
        """
        return cls.load_file(cls.get_data_path(filename))

    @classmethod
    def load_file(cls, path: str) -> list[dict]:
        """按**绝对路径**读取任务列表（跨应用子实例用，与 load 同一套容错）。

        ``load()`` 走 ``get_data_path()`` → ``hou.getenv("HIP")``，只能读
        当前会话工程；子实例要读的是 ticket 指定的外部工程，直接按绝对
        路径读可绕开 ``$HIP`` 更新时序，且复用完全相同的损坏/脏数据容错。
        """
        try:
            if not os.path.exists(path):
                return []
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(
                    "配置顶层不是对象（%s），留底后按空配置处理: %s",
                    type(data).__name__, path,
                )
                cls._backup_corrupt(path)
                return []
            tasks = data.get("tasks", [])
            if not isinstance(tasks, list):
                # 不留底：顶层仍是合法对象，settings 可照常随下次 save 保留
                logger.warning(
                    "tasks 字段不是列表（%s），按空配置处理: %s",
                    type(tasks).__name__, path,
                )
                return []
            # 非 dict 元素（手改坏档）直接丢弃：面板槽渲染与保存透传都按
            # dict 处理，混入会让加载崩掉
            clean = [d for d in tasks if isinstance(d, dict)]
            if len(clean) != len(tasks):
                logger.warning(
                    "丢弃 %d 个非对象的任务条目: %s", len(tasks) - len(clean), path)
            # params 非 dict（手改坏档，含显式 null）就地降级为空对象：三条
            # 消费路径（UI 填槽 / TaskItem.from_dict / Start 执行）都只认
            # 对象，在读档入口统一归一——UI 填槽直接 .get()，不归一会把
            # AttributeError 沿 _load_data → __init__ 逃逸，面板创建不出来
            for d in clean:
                if "params" in d and not isinstance(d["params"], dict):
                    logger.warning(
                        "任务 %r 的 params 不是对象（%s），已按空参数处理: %s",
                        d.get("type"), type(d["params"]).__name__, path)
                    d["params"] = {}
            return clean
        except json.JSONDecodeError as exc:
            logger.warning("JSON 解析失败: %s (%s)", path, exc)
            cls._backup_corrupt(path)
            return []
        except Exception as exc:
            logger.warning("读取配置失败: %s (%s)", path, exc)
            return []

    @classmethod
    def save(cls, tasks_data: list[dict], filename: Optional[str] = None,
             expect_rev: Optional[int] = None) -> bool:
        """将任务 dict 列表写入 JSON 文件。

        Args:
            tasks_data: 任务数据列表
            filename: 配置文件 basename(**无** ``.json`` 后缀)。
                      ``None`` 时保存到默认 ``Automation.json``。
            expect_rev: 乐观并发基线（调用方上次读/写时的 ``rev``）。
                非 ``None`` 且与盘上实际 rev 不一致 → 抛
                ``ConfigConflictError``，**不写盘、不留底**，由调用方
                （window 的覆盖/另存/取消弹窗）分流。``None`` = 不检查
                （新建配置、冲突后强制覆盖等受控入口）。

        写入结构: ``{"tasks": tasks_data, "rev": n}``——每次写盘 rev
        递增，多机器共用目录时读方凭差异发现他人写入（2026-11 多用户
        拍板；不依赖 mtime，NAS 跨机器时钟偏差不可靠）。
        使用 ``ensure_ascii=False``(支持中文)和 ``indent=2``。

        **副作用**:首次调用会创建 ``{HIP}/HouTools_cfg/Automation_json/`` 目录
        (``os.makedirs(exist_ok=True)``)。这是 DataManager 中**唯一**允许
        创建配置目录的入口,与"仅在 Start 时落盘"的语义配合
        —— 打开面板不会产生任何文件。

        Returns:
            True 写入成功,False 写入异常。
        Raises:
            ConfigConflictError: expect_rev 与盘上 rev 不一致。
        """
        path = cls.get_data_path(filename)
        tmp_path = path + ".~tmp%d_%d" % (os.getpid(), threading.get_ident())
        # 名字带 PID/线程 id：固定临时名在同盘并发写时会互踩（两个
        # Houdini 开同一 $HIP、或 reload 后新旧实例并存），os.replace
        # 可能把对方半写的文件搬上正式名。
        # ⚠ 已知限制（跨进程，暂不修）：save/save_settings 是"读现有 →
        # 改一半 → 整写回"，两个进程开同一 $HIP 时后写者会以自己的旧
        # 快照覆盖对方刚写入的另一半（tmp 名只防半写、不防陈旧合并基）。
        # 需要多进程共写时再引入文件锁或 mtime 比对；单会话内两写者都
        # 在主线程、无此问题
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)  # 仅此处创建配置目录
            # 读取现有数据（保留 settings）；读不出来先留底再覆盖，
            # 尚可抢救的内容不随本次写入直接丢失
            existing_data = {}
            # 盘上 rev 基线：正常文件取顶层 rev（无字段按 0）；文件缺失
            # 按 0（expect_rev 非 0 时视为"文件被他人删除"→ 冲突）；损坏
            # 恢复（留底后空起）按调用方期望放行——原内容已保底 .bak，
            # 不属于"他人修改"，不误报冲突
            disk_rev = 0
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if not isinstance(loaded, dict):
                        raise ValueError(
                            "顶层必须是对象，实际为 " + type(loaded).__name__)
                    existing_data = loaded
                    rev = loaded.get("rev")
                    if isinstance(rev, bool) or not isinstance(rev, int):
                        rev = 0
                    disk_rev = rev
                except OSError as exc:
                    # 读不了 ≠ 文件坏了（杀软/同步盘瞬时占用等）：不动
                    # 原文件、本次拒绝写入——基于空合并基覆盖会把读到
                    # 的内容冲掉（core/settings.JsonStore 同约定）。改动
                    # 即存语义下下一次编辑会自动重试
                    logger.warning("读取现有配置失败，本次不保存: %s (%s)",
                                   path, exc)
                    return False
                except Exception as exc:
                    # 解析失败/顶层不是对象：留底再覆盖，尚可抢救的内容
                    # 不随本次写入直接丢失。留底失败（权限/占用）时原件
                    # 还在原位——拒绝本次写入，防唯一可抢救副本被冲掉
                    logger.warning("现有配置损坏，先留底: %s (%s)", path, exc)
                    if not cls._backup_corrupt(path):
                        logger.warning(
                            "留底失败，本次拒绝写入: %s", path)
                        return False
                    disk_rev = expect_rev if expect_rev is not None else 0
            # 乐观并发冲突检测：调用方基线与盘上不一致 = 他人已写入，
            # 拒绝写入（不覆盖、不留底），由调用方弹窗决定覆盖/另存/放弃
            if expect_rev is not None and expect_rev != disk_rev:
                raise ConfigConflictError(path, disk_rev)
            # 合并数据 + rev 递增（写入方推进计数，读方据此发现他人写入）
            existing_data["tasks"] = tasks_data
            existing_data["rev"] = disk_rev + 1
            # 临时文件 + 原子替换：写盘中途崩溃/断电不会截断原文件
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    existing_data,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp_path, path)
            return True
        except ConfigConflictError:
            # 冲突不是 IO 故障：原样上抛给调用方弹窗分流——落进下面的
            # 通用失败分支会把冲突伪装成"写盘失败 return False"
            raise
        except Exception as exc:
            logger.warning("保存配置失败: %s (%s)", path, exc)
            try:
                os.unlink(tmp_path)
            except OSError as exc2:
                logger.debug("cannot remove config tmp %s: %s",
                             tmp_path, exc2)
            return False

    @classmethod
    def load_settings(cls, filename: Optional[str] = None) -> dict:
        """从 JSON 文件读取设置数据。

        Args:
            filename: 配置文件 basename(**无** ``.json`` 后缀)。

        返回 ``data["settings"]``,若文件不存在或 JSON 损坏则返回空 dict。
        """
        path = cls.get_data_path(filename)
        try:
            if not os.path.exists(path):
                return {}
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning(
                    "配置顶层不是对象（%s），留底后按空设置处理: %s",
                    type(data).__name__, path,
                )
                cls._backup_corrupt(path)
                return {}
            settings = data.get("settings", {})
            if not isinstance(settings, dict):
                logger.warning(
                    "settings 字段不是对象（%s），按空设置处理: %s",
                    type(settings).__name__, path,
                )
                return {}
            return settings
        except json.JSONDecodeError as exc:
            logger.warning("JSON 解析失败: %s (%s)", path, exc)
            cls._backup_corrupt(path)
            return {}
        except Exception as exc:
            logger.warning("读取设置失败: %s (%s)", path, exc)
            return {}

    @classmethod
    def save_settings(cls, settings_data: dict, filename: Optional[str] = None) -> bool:
        """将设置 dict 写入 JSON 文件。

        Args:
            settings_data: 设置数据
            filename: 配置文件 basename(**无** ``.json`` 后缀)。

        写入结构: ``{"tasks": [...], "settings": settings_data}``
        """
        path = cls.get_data_path(filename)
        tmp_path = path + ".~tmp%d_%d" % (os.getpid(), threading.get_ident())
        # 名字带 PID/线程 id：固定临时名在同盘并发写时会互踩（两个
        # Houdini 开同一 $HIP、或 reload 后新旧实例并存），os.replace
        # 可能把对方半写的文件搬上正式名
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # 读取现有数据（保留 tasks）；读不出来先留底再覆盖，
            # 尚可抢救的内容不随本次写入直接丢失
            existing_data = {}
            if os.path.exists(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                    if not isinstance(loaded, dict):
                        raise ValueError(
                            "顶层必须是对象，实际为 " + type(loaded).__name__)
                    existing_data = loaded
                except OSError as exc:
                    # 读不了 ≠ 文件坏了：不动原文件、本次拒绝写入（同
                    # save() 的 OSError 分支语义）
                    logger.warning("读取现有配置失败，本次不保存: %s (%s)",
                                   path, exc)
                    return False
                except Exception as exc:
                    # 解析失败/顶层不是对象：留底再覆盖；留底失败拒绝写入
                    # （同 save() 的语义）
                    logger.warning("现有配置损坏，先留底: %s (%s)", path, exc)
                    if not cls._backup_corrupt(path):
                        logger.warning(
                            "留底失败，本次拒绝写入: %s", path)
                        return False
            # 合并数据 + rev 递增（settings 写也是一次写盘，推进计数——
            # 不做冲突检查：settings 低风险 last-writer-wins；其他机器的
            # task 写方会凭 rev 差异发现这次写入并弹窗）
            existing_data["settings"] = settings_data
            _rev = existing_data.get("rev")
            if isinstance(_rev, bool) or not isinstance(_rev, int):
                _rev = 0
            existing_data["rev"] = _rev + 1
            # 临时文件 + 原子替换：写盘中途崩溃/断电不会截断原文件
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(
                    existing_data,
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
            os.replace(tmp_path, path)
            return True
        except Exception as exc:
            logger.warning("保存设置失败: %s (%s)", path, exc)
            try:
                os.unlink(tmp_path)
            except OSError as exc2:
                logger.debug("cannot remove config tmp %s: %s",
                             tmp_path, exc2)
            return False

    @classmethod
    def _list_configs_in_dir(cls, json_dir: str) -> list[str]:
        """枚举指定目录下所有 ``.json`` 配置的 basename(**无后缀**)。

        行为约定:
          - **不创建** 配置目录(若不存在返回空列表,与 ``load()`` 一致)
          - 只看顶层文件,排除子目录
          - 自动去 ``.json`` 后缀,直接作为 ``load(filename)`` /
            ``save(data, filename)`` 的入参
          - **不验证 JSON 有效性**(空文件 / 损坏文件也会出现在列表中,
            由 ``load()`` 容错处理返回 ``[]``)
          - 文件名按字典序排序(sorted)——保证 UI 下拉显示稳定
          - 出错时返回空列表并记 warning(权限错误等不该伪装成"没有配置")
        """
        if not os.path.isdir(json_dir):
            return []
        try:
            return sorted(
                name[:-5]
                for name in os.listdir(json_dir)
                if name.endswith(".json")
                and os.path.isfile(os.path.join(json_dir, name))
            )
        except Exception as exc:
            logger.warning("列出配置目录失败（按无配置处理）: %s (%s)",
                           json_dir, exc)
            return []

    @classmethod
    def list_configs(cls) -> list[str]:
        """当前会话 ``$HIP`` 的配置列表（行为不变，见 ``_list_configs_in_dir``）。"""
        return cls._list_configs_in_dir(os.path.dirname(cls.get_data_path()))

    @classmethod
    def list_configs_for_hip(cls, hip_path: str) -> list[str]:
        """**外部工程**的配置列表（跨应用执行的配置下拉数据源）。

        ``list_configs()`` 走 ``get_data_path()`` → ``hou.getenv("HIP")``，
        永远只能列当前会话工程；列别的工程必须显式给 hip 路径。
        目录结构同当前工程：``<hip 所在目录>/HouTools_cfg/Automation_json/``。
        纯读盘、不创建目录、不校验内容，语义与 ``list_configs()`` 完全一致。
        """
        if not hip_path or not str(hip_path).strip():
            return []
        json_dir = os.path.join(
            os.path.dirname(os.path.abspath(str(hip_path))),
            "HouTools_cfg", "Automation_json")
        return cls._list_configs_in_dir(json_dir)

    @classmethod
    def load_last_config_for_hip(cls, hip_path: str) -> Optional[str]:
        """外部工程「上次使用配置」的只读查询（用于配置下拉默认选中）。

        按机器标记优先，旧共享标记只读兼容（与 ``load_last_config`` 同
        口径）；缺标记 / 标记损坏一律返回 None，调用方回落第一项。
        标记放在 ``HouTools_cfg/`` 顶层，**不会被配置下拉枚举到**。
        """
        if not hip_path or not str(hip_path).strip():
            return None
        base = os.path.join(
            os.path.dirname(os.path.abspath(str(hip_path))), "HouTools_cfg")
        for name in ("Automation_last.{}.json".format(cls.config_hostname()),
                     "Automation_last.json"):
            marker = cls._read_last_marker(os.path.join(base, name))
            if marker:
                return marker
        return None

    @classmethod
    def delete_config(cls, filename: Optional[str] = None) -> bool:
        """删除配置文件（物理删除、不可恢复——调用方负责二级确认）。

        文件不存在视为已删除返回 True（幽灵项清理）；删除失败（占用/
        权限）返回 False，原文件不动。"""
        path = cls.get_data_path(filename)
        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except OSError as exc:
            logger.warning("删除配置文件失败: %s (%s)", path, exc)
            return False

    @classmethod
    def remove_last_config(cls) -> bool:
        """删除本机的「上次使用配置」标记文件（best-effort）。

        供配置删除后清理指向被删配置的标记；失败只告警返回 False——
        标记是可再生缓存，残留的过期指向会在下次恢复时因配置不存在
        回落默认，不阻塞任何流程。"""
        path = cls.get_last_config_path()
        try:
            if os.path.exists(path):
                os.remove(path)
            return True
        except OSError as exc:
            logger.warning("删除上次使用配置标记失败: %s (%s)", path, exc)
            return False

    # ── 序列化 / 反序列化 ─────────────────────────────────

    @classmethod
    def serialize_task_type(cls, task_type: TaskType) -> str:
        """TaskType 枚举 → 字符串（枚举的 value）。"""
        return task_type.value

    @classmethod
    def deserialize_task_type(cls, type_str: str) -> TaskType:
        """字符串 → TaskType 枚举。"""
        return TaskType(type_str)

    @classmethod
    def serialize_params(cls, params) -> dict:
        """Params dataclass → dict。"""
        return asdict(params)

    @classmethod
    def deserialize_params(
        cls,
        type_str: str,
        params_dict: dict,
    ):
        """dict → Params dataclass。

        根据 ``type_str``（匹配 ``TaskType`` 的 value）决定返回的参数类型。
        所有参数类型都只恢复当前 dataclass 定义的字段：旧 JSON 文件里的
        遗留/未知字段被忽略（不再 TypeError），缺失字段走 dataclass 默认值。
        """
        task_type = TaskType(type_str)

        if task_type == TaskType.BUTTON_CLICK:
            return ButtonClickParams(**cls._filter_known_fields(params_dict, ButtonClickParams))
        elif task_type == TaskType.FLIPBOOK:
            return FlipbookParams(**cls._filter_known_fields(params_dict, FlipbookParams))
        elif task_type == TaskType.HOME_ASSISTANT:
            return HomeAssistantParams(**cls._filter_known_fields(params_dict, HomeAssistantParams))
        elif task_type == TaskType.OPEN_DW:
            return OpenDWParams()
        elif task_type == TaskType.EXTERNAL_RUN:
            return ExternalRunParams(**cls._filter_known_fields(params_dict, ExternalRunParams))
        else:
            raise ValueError(f"未知参数类型: {type_str}")

    @staticmethod
    def _filter_known_fields(params_dict: dict, params_cls) -> dict:
        """按 dataclass 字段过滤 ``params_dict``，丢弃当前版本不认识的字段。

        旧 JSON 可能携带已废弃/改名的字段（如 Flipbook 的 frame_range），
        直接 ``cls(**params_dict)`` 会 TypeError；统一过滤后缺失字段走
        dataclass 默认值。
        """
        known = {f.name for f in fields(params_cls)}
        return {k: v for k, v in params_dict.items() if k in known}

    # ── 高层便捷方法 ─────────────────────────────────────

    @classmethod
    def save_tasks(cls, tasks: list[TaskItem]) -> bool:
        """直接接受 ``TaskItem`` 列表，自动序列化后保存。"""
        tasks_data = [t.to_dict() for t in tasks]
        return cls.save(tasks_data)

    @classmethod
    def load_tasks(cls) -> list[TaskItem]:
        """加载并自动反序列化为 ``TaskItem`` 列表。"""
        data_list = cls.load()
        return [TaskItem.from_dict(d) for d in data_list]
