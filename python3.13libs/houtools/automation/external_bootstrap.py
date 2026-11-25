"""
Automation — 跨应用子实例启动脚本
==================================
由主工程 A 通过 ``houdini -foreground waitforui <本文件绝对路径>`` 启动，
等 GUI 就绪后执行本脚本。职责单一：

1. 读 ``HOUTOOLS_AUTORUN_TICKET`` 指向的 ticket，回写 ``started_at``
   启动标记（A 在 90 秒启动阶段结束时只读一次，兜住「进程还在但卡在
   许可/恢复弹窗」）；
2. 自己 ``hou.hipFile.load()`` 打开目标工程——不依赖命令行文件参数
   顺序（社区实测 Windows 上 .py 必须绝对路径、hip 顺序在部分版本不稳）；
3. 按 ticket 的绝对路径读取该工程的 Automation 配置，用**同一个**
   ``ExecutionEngine`` 串行执行；
4. 全部任务完成后写 ``result.json`` 回执，然后
   ``hou.exit(0, suppress_save_prompt=True)`` 直接退出。

★ 硬约束（2026-11 用户拍板）：
- **收尾阶段绝不新增 ``hou.hipFile.save()``**：不保存 hip、不写回工程。
  任务项内部的保存（``dl_Submit`` 点击前的强制保存）是必须且不可违背
  的已知边界，由引擎原样执行，本脚本一行都不干预；「不保存」只针对
  全部任务执行完成后的收尾动作。
- ``suppress_save_prompt=True`` 只抑制**退出时**的「是否保存」模态框，
  不拦截任务项内已发生的保存。少了它，任一按钮回调改过场景就会弹框，
  B 的进程句柄不消失，A 会永远等下去。
- ``hou.exit()`` 会抛 ``SystemExit``，而 ``ExecutionEngine.run()`` 有
  ``except BaseException`` 兜底会吞掉它 —— 退出动作必须挂在
  ``all_completed`` 之后、经 ``hdefereval.executeDeferred`` 排到主线程，
  不能在引擎的信号回调里同步直接调。

本文件同时被 H21（Python 3.11）与 H22（Python 3.13）执行，语法只用到
两者共有的子集。
"""

import os
import sys
import time
import traceback
from pathlib import Path


def _ensure_import_path():
    """正常情况下 HOUDINI_PATH 已把 pythonX.Ylibs 加进 sys.path（H21 的
    python3.11libs 入口壳会把 __path__ 指向 python3.13libs 真身）。
    兜底把真身目录插进去，防止某些启动方式下包尚未可见。
    """
    try:
        from houtools.automation import external_run  # noqa: F401
        return
    except ImportError:
        libs = str(Path(__file__).resolve().parents[2])
        if libs not in sys.path:
            sys.path.insert(0, libs)


_ensure_import_path()

from houtools.automation import external_run as ext  # noqa: E402

# 引擎不能被 GC：启动脚本执行完就返回，QThread 靠模块级引用活到收尾
_ENGINE = None


class _Tee:
    """把子实例的 stdout/stderr 同时写进 B 工程的日志（A 侧日志另算）。"""

    def __init__(self, original, stream):
        self._original = original
        self._stream = stream

    def write(self, text):
        for target in (self._original, self._stream):
            try:
                target.write(text)
                target.flush()
            except Exception:
                pass

    def flush(self):
        for target in (self._original, self._stream):
            try:
                target.flush()
            except Exception:
                pass

    def __getattr__(self, name):
        return getattr(self._original, name)


def _install_log(log_path):
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    stream = open(log_path, "a", encoding="utf-8", errors="replace")
    sys.stdout = _Tee(sys.stdout, stream)
    sys.stderr = _Tee(sys.stderr, stream)


def _exit_app(hou):
    """在主线程排队执行退出；不在引擎回调里同步调用。"""
    def _do():
        try:
            # 抑制退出时的保存提示；不保存 hip 是收尾的既定语义
            hou.exit(0, suppress_save_prompt=True)
        except SystemExit:
            # 官方语义：hou.exit 抛 SystemExit，由 Houdini 主线程接管退出
            raise
        except Exception as exc:
            print("hou.exit 失败: {}".format(exc))
        # 走到这里说明 hou.exit 既没抛也没真正退出 —— 兜底强制退出，
        # 否则 B 的进程句柄不消失，A 会永远等（无人值守最忌讳的卡死）
        try:
            hou.hscript("quit -f")
        except Exception as exc:
            print("quit -f 兜底失败: {}".format(exc))

    try:
        import hdefereval
    except ImportError:
        hdefereval = None
    if hdefereval is not None:
        hdefereval.executeDeferred(_do)
    else:
        _do()


def main():
    import hou
    from houtools.automation.data_manager import AutomationDataManager
    from houtools.automation.task_types import TaskItem
    from houtools.automation.execution_engine import ExecutionEngine

    ticket_path = (os.environ.get(ext.ENV_TICKET) or "").strip()
    if not ticket_path:
        print("缺少 ticket 环境变量，直接退出")
        _exit_app(hou)
        return

    ticket = ext.read_ticket(ticket_path)
    if not ticket:
        print("ticket 不存在或已损坏，直接退出: {}".format(ticket_path))
        _exit_app(hou)
        return

    result_path = str(ticket.get("result_path") or "")
    run_id = str(ticket.get("run_id") or "unknown")
    hip_path = str(ticket.get("hip_path") or "")
    config_name = str(ticket.get("config_name") or "")
    started = time.time()

    # 启动标记：A 只在启动阶段结束时读一次，不是轮询
    try:
        marked = dict(ticket)
        marked["started_at"] = started
        ext.write_ticket(ticket_path, marked)
    except Exception as exc:
        print("回写启动标记失败: {}".format(exc))

    log_path = ""
    try:
        log_dir = os.path.join(os.path.dirname(os.path.abspath(hip_path)),
                               "HouTools_cfg", "Automation_logs")
        # 命名 = 主程序配置名_日期_哈希.log，由 A 侧算好放进 ticket；
        # 缺字段时按同一规则本地兜底
        log_name = (str(ticket.get("log_name") or "")
                    or ext.log_basename(
                        str(ticket.get("host_config") or ""), run_id))
        log_path = os.path.join(log_dir, log_name)
        _install_log(log_path)
        # 完整运行信息只写进日志文件（子实例 stdout 已 DEVNULL，不会进
        # A 的控制台）——控制台精简，日志内容一律不缩减
        print("运行信息: run_id={} app_id={} hip_path={} config_name={} "
              "host_config={} log_name={}\n"
              "  ticket={}\n  result={}\n  日志完整路径={}".format(
                  run_id, ticket.get("app_id"), hip_path, config_name,
                  ticket.get("host_config"), log_name,
                  ticket_path, result_path, log_path))
    except Exception as exc:
        print("日志安装失败（继续执行）: {}".format(exc))
        log_path = ""

    # hou API 在主线程预读，避免引擎线程的信号回调里碰 HOM
    try:
        hou_version = ".".join(str(v) for v in hou.applicationVersion())
    except Exception:
        hou_version = "?"

    def _finish(status, message, success=0, fail=0, cancelled=0):
        """写回执 + 退出。A 只认这张回执判定完成，进程句柄只用于唤醒。"""
        try:
            ext.write_result(result_path, {
                "run_id": run_id,
                "status": status,
                "message": message,
                "success": success,
                "fail": fail,
                "cancelled": cancelled,
                "started_at": started,
                "finished_at": time.time(),
                "elapsed_s": round(time.time() - started, 2),
                "hou_version": hou_version,
                "config_name": config_name,
                "hip_path": hip_path,
                "log_path": log_path,
            })
        except Exception as exc:
            # 回执写不出来 = A 会按「崩溃」记失败并继续队列，不会卡住
            print("回执写入失败: {}".format(exc))
        _exit_app(hou)

    try:
        if not hip_path or not os.path.isfile(hip_path):
            raise RuntimeError("目标工程不存在: {}".format(hip_path or "(空)"))
        if (not config_name or os.path.basename(config_name) != config_name
                or config_name in (".", "..")):
            raise RuntimeError("非法配置名: {!r}".format(config_name))

        try:
            hou.hipFile.load(hip_path, suppress_save_prompt=True)
        except TypeError:
            # 老版本 hipFile.load 没有 suppress_save_prompt 形参
            hou.hipFile.load(hip_path)

        config_path = ext.config_path_for_hip(hip_path, config_name)
        tasks_data = AutomationDataManager.load_file(config_path)
        task_items = []
        for data in tasks_data:
            try:
                task_items.append(TaskItem.from_dict(data))
            except (ValueError, KeyError, TypeError) as exc:
                # 与主面板 _start_execution 同款：坏任务跳过，不拖垮整轮
                print("跳过不可执行任务 type={}: {}".format(
                    data.get("type"), exc))

        print("开始执行 {} 个任务：{} / {}".format(
            len(task_items), hip_path, config_name))

        global _ENGINE
        _ENGINE = ExecutionEngine(task_items)

        def _on_all(success, fail, _timestamp, _total):
            # 普通函数直连：回调运行在引擎线程。只写文件 + 排主线程退出，
            # 不在这里碰任何 hou API、不在这里调 hipFile.save()。
            _finish("completed", "", success, fail, 0)

        _ENGINE.all_completed.connect(_on_all)
        _ENGINE.start()
    except Exception as exc:
        print("启动阶段异常: {}".format(exc))
        traceback.print_exc()
        _finish("failed", str(exc))


# 不以 __name__ == "__main__" 为闸门：Houdini 命令行执行 .py 的全局环境
# 不保证按标准脚本设 __name__；只认父进程传入的 ticket env —— 被别的
# 模块 import 时该变量不存在，不会误触发任何执行。
if os.environ.get(ext.ENV_TICKET):
    try:
        main()
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        try:
            import hou
            _exit_app(hou)
        except Exception:
            pass
