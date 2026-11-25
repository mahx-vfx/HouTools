"""
Automation — 跨应用执行（纯逻辑层）
=====================================
「跨应用执行」任务（EXTERNAL_RUN）启动**另一个版本**的 Houdini 打开指定
工程、执行该工程自己的 Automation 配置，跑完直接退出；主工程 A 阻塞等
进程句柄，句柄消失后读一次回执判定结果，再继续队列中的下一个任务。

本模块**无 hou / 无 Qt 依赖**（纯路径、JSON、进程句柄），可无头测试。

设计边界（2026-11 用户逐轮拍板，勿轻易改动）：
- **版本列表完全由个人设置目录 ``Automation_Config.json`` 的 ``apps``
  键驱动**（2026-11-25 用户拍板 A+B 合并）：该文件同时存
  ``dw_exe_path``，读写统一经 ``automation.app_settings`` 的 JsonStore
  （脏键合并 + 跨进程 named mutex），面板补建与设置窗口保存两个写入方
  不会互相覆盖。代码里不存在任何版本枚举，用户在 ``apps`` 数组里任意
  增删改 ``{"id": ..., "exe": ...}``，下拉自动同步。H21/H22 共享同一份；
  改完重开面板即生效，不用重启 Houdini。任务 JSON 只存 ``app_id``、
  不存路径 —— 换小版本只改配置文件，已存配置不用动。旧包清单 env
  ``HOUTOOLS_H21_EXE`` / ``HOUTOOLS_H22_EXE`` 仅作兼容兜底。
- 回执放在**发起编排的主工程 A** 的
  ``{A $HIP}/HouTools_cfg/Automation_runs/<run_id>/``（与现有配置、
  日志同根），不能放进 ``Automation_json/`` —— 后者会被
  ``AutomationDataManager.list_configs()`` 枚举进配置下拉。
- 等待 = 阻塞等进程句柄，不轮询状态文件、不写心跳；启动阶段有界
  （90 秒，抓许可失败/启动弹窗/启动脚本未执行），执行阶段无界
  （40 小时渲染是正常值）。
- 完成判定只认 ``result.json``：有 = 正常完成，无 = 崩溃/被杀/许可失败/
  手动关窗。进程退出码只用于报错文案，不作为完成信号。
- **收尾阶段绝不新增 ``hou.hipFile.save()``**：任务项内部
  （``dl_Submit`` 点击前的强制保存）是必须且不可违背的已知边界，
  本模块与 bootstrap 都不得干预；这里说的「不保存」只针对全部任务
  执行完成后的收尾动作。
"""

import json
import os
import socket
import subprocess
import threading
import time
import uuid
from typing import Optional

from houtools.core.constants import PROJECT_ROOT
from houtools.core.log import get_logger
from . import app_settings as _aps

logger = get_logger("automation.external")

# 版本列表与 DW 软件路径合并存于个人设置目录的 Automation_Config.json
# （H21/H22 折叠到同一目录），不塞进 HouTools.json：Houdini 只会把包清单
# 的 env 注入进程，自定义顶层键它根本不读，放那里等于借壳。读写统一走
# app_settings 的 JsonStore；下面两个常量只是兼容既有引用的别名。
APPS_CONFIG_FILE = _aps.CONFIG_FILE
DEFAULT_APPS = _aps.DEFAULT_APPS

# 旧版包清单 env 变量：只为兼容未迁移的清单/旧 app_id，新配置一律走文件
LEGACY_ENV_KEYS = {
    "H21": "HOUTOOLS_H21_EXE",
    "H22": "HOUTOOLS_H22_EXE",
}

# 传给子实例的启动脚本（相对 python3.13libs；H21 通过 python3.11libs
# 入口壳扩展 __path__ 指向同一份真身，绝对路径对本脚本仍成立）。
BOOTSTRAP_REL = os.path.join("houtools", "automation", "external_bootstrap.py")

# 父进程 → 子进程只通过这一个 env 传 ticket 路径：不与包清单里任何变量
# 同名，不会被 Houdini 包 env 覆盖；也不在命令行里塞长路径。
ENV_TICKET = "HOUTOOLS_AUTORUN_TICKET"

STARTUP_TIMEOUT_S = 90.0      # 启动阶段有界；仅覆盖到启动脚本第一行
POLL_INTERVAL_S = 1.0         # 可中断等待的唤醒间隔，不是状态轮询


def apps_config_path() -> str:
    """应用级配置文件的绝对路径（dw_exe_path 与 apps 两键共存）。"""
    return _aps.config_path()


def config_hint() -> str:
    """错误提示里指向的配置位置（不再提包清单 env）。"""
    return apps_config_path()


def _default_apps() -> list[dict]:
    return [dict(item) for item in DEFAULT_APPS]


def load_apps() -> list[dict]:
    """读版本列表（薄封装；净化与 IO 统一在 app_settings 的 JsonStore）。

    每次重读磁盘，手改配置文件后重开/刷新面板即生效；坏文件由
    JsonStore 留底并回退出厂默认，不覆盖可抢救内容。
    """
    return _aps.get_apps()


def save_apps(apps: list[dict]) -> bool:
    """保存版本列表（薄封装；原子写 + 脏键合并 + 跨进程互斥由统一层负责）。

    跳过非对象 / 缺 id 或 exe 的条目、按 id 去重、**不校验 exe 是否存在**
    （用户可能先写好还没装的版本，执行时由 ``resolve_exe`` 报错）。
    写盘失败返回 False，磁盘内容不动。
    """
    return _aps.set_apps(apps)


def reset_apps() -> bool:
    """恢复 H21 / H22 两条出厂默认（设置窗口「恢复默认」入口）。"""
    return _aps.reset_apps()


def discover_apps() -> list[dict]:
    """下拉数据源：配置文件里的所有条目 + 旧 env 兜底，按 id 字典序。

    这里**不做 isfile 过滤**：用户写进配置的版本就该在下拉里看得见，
    路径写错时由 ``resolve_exe`` 给明确报错，而不是让这一项凭空消失。
    """
    by_id = {app["id"]: app for app in load_apps()}
    for legacy_id, env_key in LEGACY_ENV_KEYS.items():
        value = (os.environ.get(env_key) or "").strip()
        if value and legacy_id not in by_id:
            by_id[legacy_id] = {"id": legacy_id,
                                "exe": os.path.normpath(value)}
    return [by_id[key] for key in sorted(by_id)]


def app_ids() -> list[str]:
    """版本下拉的选项（由配置文件驱动，代码无枚举）。"""
    return [app["id"] for app in discover_apps()]


def resolve_exe(app_id: str) -> Optional[str]:
    """``app_id`` → exe 绝对路径；路径不存在返回 None。

    **不扫 PATH、不自动猜版本** —— 猜错版本打开工程会写坏工程，宁可报
    「未配置」。优先配置文件，旧包清单 env 变量只作兼容兜底。
    """
    key = str(app_id or "")
    for app in discover_apps():
        if app["id"] == key:
            return app["exe"] if os.path.isfile(app["exe"]) else None
    value = (os.environ.get(LEGACY_ENV_KEYS.get(key, "")) or "").strip()
    if value and os.path.isfile(value):
        return os.path.normpath(value)
    return None


def new_run_id() -> str:
    """一次编排运行的唯一标识：本地时间 + 主机名 + 随机后缀。"""
    host = ""
    try:
        host = socket.gethostname() or ""
    except Exception:
        pass
    host = "".join(c if c.isalnum() or c in "-_" else "_"
                   for c in host)[:24]
    return "{}_{}_{}".format(
        time.strftime("%Y%m%d_%H%M%S", time.localtime()),
        host or "host", uuid.uuid4().hex[:6])


def config_dir_for_hip(hip_path: str) -> str:
    """外部工程的 Automation 配置目录（纯计算，不创建目录）。"""
    return os.path.join(os.path.dirname(os.path.abspath(hip_path)),
                        "HouTools_cfg", "Automation_json")


def config_path_for_hip(hip_path: str, config_name: str) -> str:
    """外部工程某个配置文件的绝对路径（不含后缀的 basename）。"""
    return os.path.join(config_dir_for_hip(hip_path),
                        str(config_name) + ".json")


def runs_dir(hip_dir: str, run_id: str) -> str:
    """主工程 A 的一次运行目录（在 ``HouTools_cfg/`` 顶层）。"""
    return os.path.join(hip_dir, "HouTools_cfg", "Automation_runs", run_id)


def bootstrap_path() -> str:
    """子实例启动脚本的绝对路径（Windows 上 waitforui 只认绝对路径）。"""
    return str(PROJECT_ROOT / "python3.13libs" / BOOTSTRAP_REL)


def _safe_log_name(name: str) -> str:
    """配置名净化成合法文件名片段（不依赖 window，避免反向 import）。"""
    cleaned = "".join(
        c if c.isalnum() or c in "-_" else "_"
        for c in str(name or "").strip())
    return cleaned.strip("._")[:48] or "Automation"


def log_basename(host_config: str, run_id: str) -> str:
    """子实例日志文件名：``主程序配置名_日期_哈希.log``（2026-11 用户指定）。

    例：``WS-TEST_20261125_31a3f5.log``。只保留有意义的信息：
    哪个主工程配置发起、哪天、哪一次运行（6 位随机尾）。完整 run_id、
    绝对路径、ticket/result 路径都仍写在目录名、ticket、result 与**日志
    正文**里 —— 缩减的是控制台输出的噪音，不是日志内容。
    """
    parts = str(run_id or "").split("_")
    date = (parts[0] if parts and len(parts[0]) == 8 and parts[0].isdigit()
            else time.strftime("%Y%m%d"))
    short = parts[-1] if parts else uuid.uuid4().hex[:6]
    return "{}_{}_{}.log".format(_safe_log_name(host_config), date, short)


def _atomic_write_json(path: str, data: dict) -> None:
    """临时文件 + os.replace 原子写（与 data_manager.save 同手法）。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "{}.~tmp{}_{}".format(path, os.getpid(), threading.get_ident())
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def read_json(path: str) -> Optional[dict]:
    """读一个 JSON 对象；缺失/损坏/顶层不是对象一律返回 None。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.debug("读取运行文件失败: %s (%s)", path, exc)
        return None


def write_ticket(path: str, ticket: dict) -> None:
    _atomic_write_json(path, ticket)


def read_ticket(path: str) -> Optional[dict]:
    return read_json(path)


def write_result(path: str, result: dict) -> None:
    _atomic_write_json(path, result)


def read_result(path: str) -> Optional[dict]:
    return read_json(path)


def build_command(exe: str, bootstrap: str) -> list[str]:
    """启动命令：``houdini -foreground waitforui <启动脚本绝对路径>``。

    ``waitforui`` 之后的脚本等 GUI 就绪才执行（官方文档）；hip 由启动
    脚本自己 ``hou.hipFile.load()`` 打开，不依赖命令行文件参数顺序
    （社区实测 Windows 上 .py 必须绝对路径、hip 顺序在部分版本不稳）。
    ``-foreground`` 在 Windows 无实际 fork 语义，保留无害。
    """
    return [exe, "-foreground", "waitforui", bootstrap]


def child_env(ticket_path: str) -> dict:
    """子进程环境：继承父进程 + ticket 路径。"""
    env = os.environ.copy()
    env[ENV_TICKET] = ticket_path
    return env


def wait_for_exit(proc, stop_event=None, startup_timeout: float = STARTUP_TIMEOUT_S,
                   poll_interval: float = POLL_INTERVAL_S,
                   started_check=None, progress_cb=None,
                   progress_interval: float = 1800.0) -> tuple[str, Optional[int]]:
    """阻塞等进程句柄，返回 ``(phase, returncode)``。

    phase：
    - ``early_exit``  启动阶段（有界）内进程就退出了 —— 可能是极快跑完，
      也可能是闪退/许可失败，由调用方读回执判定；
    - ``no_bootstrap`` 启动阶段结束时进程仍在，但启动脚本没写出
      ``started_at`` 标记 —— 卡在许可弹窗/崩溃恢复对话框/脚本未被执行；
    - ``exited``      正常无界等待后进程退出；
    - ``cancelled``    收到取消事件（调用方负责终止子进程）。

    启动阶段用 ``proc.wait(timeout=...)`` 让内核唤醒，执行阶段用
    ``stop_event.wait(interval)`` 可中断等待 —— 期间**不读任何文件**，
    不是状态轮询；40 小时的等待只有每秒一次的事件检查，零 CPU 开销。

    ``started_check`` 只在启动阶段结束时调用一次（读一次 ticket），
    用来兜住「进程还在但卡住」这一 PID 方案覆盖不到的情形。

    ``progress_cb`` 每 ``progress_interval`` 秒（默认 30 分钟）被调用
    一次，只用于打印「已等待多久」——不读任何文件、不改变等待语义，
    让 40 小时级别的任务在 A 的日志里不是一片死寂。不传即零输出。
    """
    try:
        rc = proc.wait(timeout=startup_timeout)
        return ("early_exit", rc)
    except subprocess.TimeoutExpired:
        pass

    if started_check is not None:
        try:
            if not started_check():
                return ("no_bootstrap", None)
        except Exception as exc:
            logger.debug("启动标记检查失败（按已启动处理）: %s", exc)

    wait_started = time.monotonic()
    next_report = progress_interval
    while True:
        if stop_event is not None and stop_event.is_set():
            return ("cancelled", None)
        rc = proc.poll()
        if rc is not None:
            return ("exited", rc)
        elapsed = time.monotonic() - wait_started
        if progress_cb is not None and elapsed >= next_report:
            try:
                progress_cb(elapsed)
            except Exception as exc:
                logger.debug("等待进度回调失败（忽略）: %s", exc)
            next_report += progress_interval
        if stop_event is not None:
            stop_event.wait(poll_interval)
        else:
            time.sleep(poll_interval)


def terminate_process(proc) -> None:
    """请求终止子进程（取消 / 启动异常的兜底）。"""
    try:
        proc.terminate()
    except Exception as exc:
        logger.debug("终止外部 Houdini 失败（可能已退出）: %s", exc)
