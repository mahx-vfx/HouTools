"""
Automation — 执行引擎
=========================
QThread 子类，在后台线程中逐个执行任务列表。
支持 5 种任务类型：BUTTON_CLICK / FLIPBOOK / HOME_ASSISTANT / OPEN_DW /
EXTERNAL_RUN（EXTERNAL_RUN 启动另一个 Houdini 进程，阻塞等其退出）。

执行器通过 ``hdefereval.executeDeferred`` 将 Houdini API 调用
派发到主线程执行，确保线程安全。

★ 设计意图：对主线程的同步等待（``_run_deferred`` 里的 ``ready.wait()``）
是无超时的，这是有意设计，不是缺陷，禁止加超时。本工具的本质是"逐个触发
Houdini 按钮"，顺序执行的唯一保证就是上一个任务的主线程调用返回后再派发
下一个——主线程被解算/缓存阻塞多久，就等多久（数小时是正常值）。任何超时
都会在任务实际完成前放行下一个任务，破坏顺序语义。因此：

- modal 对话框、长渲染等阻塞主线程的情形，在本语义下都属于"当前任务未
  完成，继续等"，与等解算跑完是同一条规则；
- 取消是协作式的，只能在任务边界生效（见 ``cancel``），在途任务不可打断；
- 已知会弹阻塞对话框的按钮（如 ``dl_Submit``）靠逐案消除弹框根源解决
  （见 ``_execute_button_click`` 的存盘特例），而不是改等待机制。
"""

import os
import subprocess
import threading
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from .task_types import (
    TaskItem,
    TaskType,
    ButtonClickParams,
    FlipbookParams,
    HomeAssistantParams,
    OpenDWParams,
    ExternalRunParams,
)
from .data_manager import AutomationDataManager
from . import external_run as ext

# 尝试导入 hdefereval — Houdini 环境外不可用，此时为 None
try:
    import hdefereval  # noqa: N812 — only available inside Houdini
except ImportError:
    hdefereval = None

from houtools.core.log import get_logger
logger = get_logger("automation.engine")


def _mask_webhook_url(url) -> str:
    """Webhook URL 脱敏：只保留 scheme://host，路径与 query 全部遮蔽。

    HA 的 webhook 路径（/api/webhook/<id>）本身就是调用凭证——日志头、
    异常消息里出现完整 URL 会把凭证从配置扩散到 $HIP 日志/控制台，随
    工程或日志分享即泄露。实际请求仍用原始 URL，脱敏只用于展示。
    """
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(str(url))
        if parts.netloc:
            return "{}://{}/***".format(parts.scheme or "http", parts.netloc)
    except Exception:
        pass
    return "***"


def _hip_never_saved() -> bool:
    """当前工程是否从未保存过（dl_Submit 前置保存的守卫判据）。

    ``hou.hipFile.hasNeverSaved`` 在 H22.0.429 不存在（2026-11 真机
    AttributeError）——判据按能力探测降级：hasNeverSaved（其他版本若有）
    > isNewFile（H22 实际 API，未保存过为 True，实测）> name() 基名
    兜底。边角：用户把工程手动存成 "untitled.hip" 会被兜底误判为未
    保存（多提示一次"请先手动保存"，不产生破坏，接受）。
    """
    import hou

    probe = getattr(hou.hipFile, "hasNeverSaved", None)
    if probe is not None:
        return bool(probe())
    probe = getattr(hou.hipFile, "isNewFile", None)
    if probe is not None:
        return bool(probe())
    return os.path.basename(hou.hipFile.name() or "").lower() == "untitled.hip"


class ExecutionEngine(QThread):
    """自动化任务执行引擎。

    接收 ``list[TaskItem]``，在 ``run()`` 中逐项派发给对应的桩执行器。
    通过信号向 UI 层报告进度和结果。
    """

    task_started = Signal(int, str, str)        # (task_index, task_type_value, timestamp_str)
    task_completed = Signal(int, bool, str, float)  # (task_index, success, message, elapsed_seconds)
    all_completed = Signal(int, int, str, float)    # (success_count, fail_count, timestamp_str, total_elapsed)

    def __init__(self, tasks: list[TaskItem], parent=None,
                 log_sink=None, host_config_name: str = ""):
        super().__init__(parent)
        self._tasks = tasks
        # 只写日志文件、不进控制台的通道（window._write_log 持锁、跨线程
        # 安全）。控制台要安静，但完整路径/run_id 不能从日志里消失
        # （2026-11 用户拍板）；子实例与无头测试没有 sink 时静默。
        self._log_sink = log_sink
        # 发起编排的主程序配置名，用于子日志命名「配置名_日期_哈希.log」
        self._host_config_name = str(host_config_name or "")
        self._cancelled = False
        # 本次运行因取消而未执行的任务数（协作式取消生效点统计；成功/
        # 失败不含它们——在途任务已计入各自结果）。完成回调读取并汇报
        self._cancelled_count = 0
        # 主线程预读 DW 软件路径:hou.getenv 严格说不该在 QThread 里调,
        # 每次运行读一次(而非每任务读)也足够
        self._dw_exe_path = AutomationDataManager.load_dw_exe_path()
        # 本次运行启动的全部 DW 子进程引用（防 GC ResourceWarning，见
        # _execute_open_dw 注释）
        self._dw_processes: list = []
        # 跨应用执行：取消事件让阻塞中的进程句柄等待立刻可中断；$HIP 在
        # 主线程预读（hou.getenv 严格说不该在 QThread 里调），供回执目录
        # 定位用。无头会话静默为空，不触发 data_manager 的 fallback 告警。
        self._stop_event = threading.Event()
        self._hip_dir = ""
        try:
            import hou
            self._hip_dir = hou.getenv("HIP") or ""
        except ImportError:
            pass

    # ── 主循环 ────────────────────────────────────────────

    def run(self):
        """遍历所有任务，派发给对应的执行器。

        ``all_completed`` 无论正常跑完还是被取消都会发出（取消只是提前
        break），UI 层依赖它复位状态，不可去掉。整个循环包在 try/finally
        里保证这一点：按钮回调脚本抛出的 SystemExit 等非 Exception 的
        BaseException 会穿透逐任务的 except Exception（_run_deferred 的
        wrapper 捕的是 BaseException 并在引擎线程原样重抛），漏了
        finally 会让 all_completed 永不发——UI 永久停在「停止中…」，
        stdout 重定向也挂到面板销毁才恢复。
        """
        success_count = 0
        fail_count = 0
        run_start_time = None
        cur_idx = None      # 在途任务号/起始时刻：BaseException 逃逸时补发
        cur_start = None    # task_completed 用（该任务在日志里有始有终）

        try:
            for idx, task in enumerate(self._tasks):
                if self._cancelled:
                    # 协作式取消生效点：从当前任务起剩余的启用任务全部
                    # 不再执行——统计数量供完成回调在日志/控制台汇报
                    # （在途任务已完成收尾、计入各自结果，不计入取消数）
                    self._cancelled_count = sum(
                        1 for t in self._tasks[idx:] if t.enabled)
                    break
                if not task.enabled:
                    continue

                cur_idx = idx
                start_time = datetime.now()
                cur_start = start_time
                if run_start_time is None:
                    run_start_time = start_time
                self.task_started.emit(idx, task.task_type.value, start_time.strftime("%H:%M:%S"))

                try:
                    if task.task_type == TaskType.BUTTON_CLICK:
                        self._execute_button_click(task.params)
                    elif task.task_type == TaskType.FLIPBOOK:
                        self._execute_flipbook(task.params)
                    elif task.task_type == TaskType.HOME_ASSISTANT:
                        self._execute_home_assistant(task.params)
                    elif task.task_type == TaskType.OPEN_DW:
                        self._execute_open_dw(task.params)
                    elif task.task_type == TaskType.EXTERNAL_RUN:
                        self._execute_external_run(task.params)
                    else:
                        raise ValueError(f"未知任务类型: {task.task_type}")

                    elapsed = (datetime.now() - start_time).total_seconds()
                    self.task_completed.emit(idx, True, "执行成功", elapsed)
                    success_count += 1
                except Exception as e:
                    elapsed = (datetime.now() - start_time).total_seconds()
                    self.task_completed.emit(idx, False, str(e), elapsed)
                    fail_count += 1

                cur_idx = None   # 任务已收账，此后逃逸不再归属任何任务
                self.msleep(100)
        except BaseException as exc:
            # 记账后吞掉：SystemExit 属按钮回调脚本的行为，不应连带杀掉
            # 引擎线程的收尾（finally 仍发 all_completed，UI 正常复位）。
            # 逐任务的 except Exception 接不住非 Exception 的
            # BaseException——在途任务缺一条 task_completed，按失败补发
            logger.error("engine aborted by %s: %s",
                         type(exc).__name__, exc, exc_info=True)
            fail_count += 1
            if cur_idx is not None:
                elapsed = ((datetime.now() - cur_start).total_seconds()
                           if cur_start else 0.0)
                self.task_completed.emit(
                    cur_idx, False, str(exc) or type(exc).__name__, elapsed)
        finally:
            # timestamp 仅用于日志展示（唯一消费方 _on_all_completed 只打印
            # 不解析；2026-10 由 "%Y/%m/%d/%H:%M:%S" 改为空格分隔）
            timestamp = datetime.now().strftime("%Y/%m/%d %H:%M:%S")
            total_elapsed = (datetime.now() - run_start_time).total_seconds() if run_start_time else 0
            self.all_completed.emit(success_count, fail_count, timestamp, total_elapsed)

    # ── 取消 ──────────────────────────────────────────────

    def cancel(self):
        """请求取消，``run()`` 将在当前任务完成后提前退出。

        协作式取消：标志位只在任务循环的边界检查，在途任务（及其对主线程
        的同步等待）不可被打断——这是有意设计，见模块文档。UI 层不得因
        调用了本方法就把界面复位成空闲，应等 ``all_completed`` 到达。

        同时置位 ``_stop_event``：跨应用执行的进程句柄等待靠它可中断，
        不需要把外部任务的执行等到底再响应取消（语义仍是协作式：由
        run() 循环顶部统一统计取消数）。
        """
        self._cancelled = True
        self._stop_event.set()

    # ── 仅日志通道（控制台不显示） ─────────────────────────

    def _log_detail(self, text: str) -> None:
        """把完整排障信息写进**日志文件**，不 print 到控制台。

        控制台输出要短（用户反馈「输出太多显得乱」），但绝对路径、
        run_id、ticket/result 路径、子日志完整路径等必须在日志里留全。
        ``window._write_log`` 自己持锁并追加换行，可从 QThread 直接调；
        失败只 debug，绝不影响任务执行。
        """
        sink = getattr(self, "_log_sink", None)
        if sink is None:
            return
        try:
            sink(text)
        except Exception as exc:
            logger.debug("详细日志写入失败（忽略）: %s", exc)

    # ── 主线程派发 ────────────────────────────────────────

    def _run_deferred(self, fn):
        """通过 ``hdefereval.executeDeferred`` 在主线程执行 ``fn``，并阻塞
        后台线程直到其返回。

        ★ ``ready.wait()`` 不带超时是有意设计，禁止"修复"成带超时等待：
        顺序执行依赖"上一个任务的主线程调用返回后再派发下一个"，主线程
        被解算/缓存/渲染阻塞多久就等多久，任何超时都会提前放行下一个任务
        破坏顺序语义。详见模块文档。

        若 ``hdefereval`` 不可用（测试环境等），则直接执行。
        """
        if hdefereval is not None:
            exc_info: list[BaseException | None] = [None]
            ready = threading.Event()

            def wrapper():
                try:
                    fn()
                except BaseException as e:
                    exc_info[0] = e
                finally:
                    ready.set()

            hdefereval.executeDeferred(wrapper)
            ready.wait()

            if exc_info[0] is not None:
                raise exc_info[0]  # type: ignore[misc]
        else:
            fn()

    # ── 执行器 ─────────────────────────────────────────────

    def _execute_button_click(self, params: ButtonClickParams):
        """执行按钮点击。

        接收 ``ButtonClickParams(node_path, parm_name)``，
        通过 ``hou.node()`` 查找节点，验证并按下按钮。

        Raises:
            ValueError: 节点不存在、参数不存在、非 Button 类型
        """
        def impl():
            import hou
            target_node = hou.node(params.node_path)
            if target_node is None:
                raise ValueError(f"节点不存在: {params.node_path}")

            target_parm = target_node.parm(params.parm_name)
            if target_parm is None:
                raise ValueError(f"参数不存在: {params.parm_name}")

            parm_template = target_parm.parmTemplate()
            if parm_template.type() != hou.parmTemplateType.Button:
                raise ValueError(f"不是按钮参数: {params.parm_name}")

            # dl_Submit（Deadline 提交）的回调要求点击前工程已保存到磁盘，
            # 否则会弹"保存工程"对话框卡住自动化流程；保存失败则不点击。
            # 从未保存过的工程连默认路径都没有,save() 必弹模态对话框问
            # 存放位置 —— 同样按"失败跳过点击"语义处理,请用户先手动保存
            if params.parm_name == "dl_Submit":
                if _hip_never_saved():
                    raise ValueError("工程从未保存过，已跳过 dl_Submit：请先手动保存")
                try:
                    hou.hipFile.save()
                except hou.OperationFailed as e:
                    raise ValueError(
                        f"保存工程失败，已跳过 dl_Submit 点击：{e}"
                    ) from e

            target_parm.pressButton()

        self._run_deferred(impl)

    def _execute_flipbook(self, params: FlipbookParams):
        """执行 Flipbook 拍屏。

        使用用户提供的帧范围和输出路径，写入 Houdini 的 flipbook settings，
        保留其他默认设置（分辨率、质量等）。

        Raises:
            RuntimeError: 找不到 Scene Viewer
        """
        def impl():
            import hou
            scene = hou.ui.paneTabOfType(hou.paneTabType.SceneViewer)
            if scene is None:
                raise RuntimeError("未找到 Scene Viewer")

            # stash() 复制用户当前对话框设置为底再改，与
            # capture_viewport_embed 同一手法——直接改 flipbookSettings()
            # 活对象会把用户的输出路径/帧范围永久留脏
            settings = scene.flipbookSettings().stash()

            # 解析帧范围表达式
            try:
                start_str = hou.text.expandString(params.start_frame)
                end_str = hou.text.expandString(params.end_frame)
                start = int(float(start_str))
                end = int(float(end_str))
                settings.frameRange((start, end))
            except (hou.OperationFailed, ValueError, TypeError, OverflowError) as e:
                logger.warning("帧范围无效，已沿用原范围: %s", e)

            # 设置输出路径（不展开 $F4 等表达式，让 Houdini 逐帧展开）
            if params.save_to_disk:
                settings.output(params.output_path)
            else:
                settings.output("")

            scene.flipbook(scene.curViewport(), settings)

        self._run_deferred(impl)

    def _execute_home_assistant(self, params: HomeAssistantParams):
        """执行 HomeAssistant Webhook 调用。

        接收 ``HomeAssistantParams(webhook_url)``，
        通过 ``requests.post()`` 发送 HTTP POST 请求。
        网络请求可直接在后台线程执行，无需 ``executeDeferred``。

        Raises:
            ValueError: webhook_url 为空
            ImportError: requests 模块不存在
            TimeoutError: 请求超时
            ConnectionError: 连接失败
        """
        if not params.webhook_url:
            raise ValueError("Webhook URL 为空")

        try:
            import requests
        except ImportError:
            raise ImportError("requests 模块未安装，请执行 pip install requests")

        try:
            with requests.post(params.webhook_url, timeout=5) as resp:
                # 底层连接随 with 块退出即归还连接池,不再依赖 GC 兜底
                resp.raise_for_status()
        except requests.HTTPError as exc:
            # HTTPError 的 str 带完整 URL（含 webhook 凭证路径）——重抛
            # 脱敏版，任务完成回调打印的异常不再泄露凭证
            raise RuntimeError(
                "请求失败（HTTP {}）: {}".format(
                    getattr(exc.response, "status_code", "?"),
                    _mask_webhook_url(params.webhook_url)))
        except requests.Timeout:
            raise TimeoutError(
                "请求超时: {}".format(_mask_webhook_url(params.webhook_url)))
        except requests.ConnectionError:
            raise ConnectionError(
                "连接失败: {}".format(_mask_webhook_url(params.webhook_url)))

    def _execute_open_dw(self, params: OpenDWParams):
        """执行打开DW：启动 Deadline Worker。

        软件路径从应用配置文件（``AutomationDataManager.get_app_config_path()``
        = 配置目录下 ``Automation_Config.json``）的 ``dw_exe_path`` 字段读取
        （用户可手动编辑），任务参数为空。
        ``subprocess.Popen`` 非阻塞启动，无需派发主线程。

        Raises:
            ValueError: 路径为空 / 文件不存在 / 启动失败
        """
        # 路径已在引擎构造时(主线程)预读,避免后台线程触碰 hou.getenv
        exe_path = self._dw_exe_path
        if not exe_path:
            # 防御分支：load_dw_exe_path 对空值回退默认路径，理论不可达
            raise ValueError(
                "DW 软件路径为空，请在配置文件 {} 中设置 dw_exe_path 字段"
                .format(AutomationDataManager.get_app_config_path())
            )
        if not os.path.isfile(exe_path):
            # 未配置过时这里报的是内置默认路径——文案须指明去哪改，否则
            # 看起来像"路径填错"却找不到配置处
            raise ValueError(
                "DW 软件路径不存在: {}（这是未配置时的内置默认路径，"
                "可在配置文件 {} 的 dw_exe_path 字段填入实际路径）"
                .format(exe_path,
                        AutomationDataManager.get_app_config_path())
            )

        try:
            # CREATE_NO_WINDOW 防配置成控制台程序时弹黑框（与 videoseq
            # 的 get_startup_kwargs 同一套约定）。stdio 全部 DEVNULL：不
            # 重定向时子进程继承 Houdini 的控制台，worker 日志会刷进主
            # 程序；引用挂 list（一次运行可含多个 DW 任务，覆盖赋值会让
            # 前序 Popen 失去引用、恰好在子进程仍运行时被 GC 发
            # ResourceWarning——引擎存活期 = 本次运行，收尾后随引擎释放）
            self._dw_processes.append(subprocess.Popen(
                [exe_path],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        except OSError as e:
            raise ValueError(f"启动 DW 失败: {exe_path} ({e})") from e

    # ── 跨应用执行（EXTERNAL_RUN）───────────────────────────

    def _execute_external_run(self, params: ExternalRunParams):
        """启动指定版本的 Houdini 打开外部工程、执行其 Automation 配置。

        在**后台线程**执行，不派发主线程（与 Webhook / 打开DW 同类）：
        等待由 ``external_run.wait_for_exit`` 阻塞在进程句柄上，内核在
        子进程退出时唤醒，期间零 CPU、不读状态文件、不写心跳。

        完成判定只认 ``result.json`` 回执：有 = 正常完成，无 = 崩溃/
        被杀/许可失败/手动关窗（记失败并继续队列，用户拍板「崩溃不卡
        住」）。进程退出码仅用于报错文案，不作为完成信号。

        ★ 硬约束：**本方法及其子实例的收尾流程绝不新增
        ``hou.hipFile.save()``**。任务项内部的保存（``_execute_button_click``
        的 ``dl_Submit`` 前置保存）必须原样保留、一行不动——Deadline 提交
        依赖它，「不保存」只针对全部任务完成后的收尾动作。

        Raises:
            ValueError / RuntimeError: 均由 run() 的逐任务 except 记失败
            并继续下一任务，不会中断整条队列。
        """
        app_id = str(params.app_id or "")
        exe = ext.resolve_exe(app_id)
        if not exe:
            raise ValueError(
                "未配置 {} 的 Houdini 可执行文件：请在 HouTools.json 的 env "
                "里把 {} 改成实际路径（重启 Houdini 后生效）".format(
                    app_id or "(未选版本)", ext.exe_env_key(app_id)))

        hip_path = str(params.hip_path or "").strip()
        if not hip_path or not os.path.isfile(hip_path):
            raise ValueError("外部工程不存在: {}".format(hip_path or "(空)"))
        hip_path = os.path.abspath(hip_path)

        config_name = str(params.config_name or "").strip()
        if not config_name:
            raise ValueError("未选择配置文件")
        if not os.path.isfile(ext.config_path_for_hip(hip_path, config_name)):
            raise ValueError(
                "外部工程的配置文件不存在: {} / {}".format(
                    hip_path, config_name))

        if not self._hip_dir:
            raise ValueError("当前工程 $HIP 不可用，无法确定回执目录")

        run_id = ext.new_run_id()
        run_dir = ext.runs_dir(self._hip_dir, run_id)
        ticket_path = os.path.join(run_dir, "ticket.json")
        result_path = os.path.join(run_dir, "result.json")
        # 子日志名 = 主程序配置名_日期_哈希.log（用户指定）；由 A 侧算好
        # 写进 ticket，子实例照用即可，不需要理解命名规则
        log_name = ext.log_basename(self._host_config_name, run_id)
        ext.write_ticket(ticket_path, {
            "run_id": run_id,
            "app_id": app_id,
            "hip_path": hip_path,
            "config_name": config_name,
            "host_config": self._host_config_name,
            "log_name": log_name,
            "result_path": result_path,
            "created_at": datetime.now().timestamp(),
            "startup_timeout_s": ext.STARTUP_TIMEOUT_S,
        })

        bootstrap = ext.bootstrap_path()
        if not os.path.isfile(bootstrap):
            raise ValueError("启动脚本不存在: {}".format(bootstrap))

        cmd = ext.build_command(exe, bootstrap)
        # 控制台只留一行短摘要；完整路径 / run_id / ticket / result 全部
        # 写进日志文件（_log_detail 不 print，不会让控制台变乱）
        print("启动外部 Houdini {}：{} / {}".format(
            app_id, os.path.basename(hip_path), config_name))
        self._log_detail(
            "外部执行启动: run_id={} app_id={} exe={}\n"
            "  hip_path={} config_name={} host_config={}\n"
            "  log_name={}\n  ticket={}\n  result={}".format(
                run_id, app_id, exe, hip_path, config_name,
                self._host_config_name, log_name,
                ticket_path, result_path))
        try:
            # stdio 全 DEVNULL：GUI 可执行文件不向管道写 stdout（实测），
            # 子进程若继承控制台会刷 Houdini 启动日志进主程序。
            proc = subprocess.Popen(
                cmd, env=ext.child_env(ticket_path),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=(subprocess.CREATE_NO_WINDOW
                              if os.name == "nt" else 0))
        except OSError as e:
            raise ValueError(
                "启动外部 Houdini 失败: {} ({})".format(exe, e)) from e

        def _started():
            """启动阶段结束时只读一次 ticket，确认启动脚本确实执行过。"""
            ticket = ext.read_ticket(ticket_path)
            return bool(ticket and ticket.get("started_at"))

        def _progress(elapsed):
            # 只写日志、不进控制台：40 小时任务在日志里有进度可查，
            # 控制台却不会被每 30 分钟一行的提示刷乱
            self._log_detail(
                "已等待 {} 分钟（无超时，等待进程退出），run_id={}"
                .format(int(elapsed // 60), run_id))

        phase, rc = ext.wait_for_exit(
            proc, stop_event=self._stop_event, started_check=_started,
            progress_cb=_progress)

        if phase in ("cancelled", "no_bootstrap"):
            ext.terminate_process(proc)
            self._log_detail(
                "外部执行终止: phase={} run_id={} ticket={} result={}"
                .format(phase, run_id, ticket_path, result_path))
            if phase == "cancelled":
                raise RuntimeError("跨应用执行已取消")
            raise RuntimeError(
                "外部 Houdini 已启动但自动化脚本未执行（{} 秒内无启动标记）："
                "可能卡在许可 / 崩溃恢复对话框，或 waitforui 未执行脚本".format(
                    ext.STARTUP_TIMEOUT_S))

        result = ext.read_result(result_path)
        if result is None:
            self._log_detail(
                "外部执行异常退出: 无回执 rc={} run_id={} result={}"
                .format(rc, run_id, result_path))
            raise RuntimeError(
                "外部应用异常退出（无回执，退出码 {}）：{}；已按失败继续队列"
                .format(rc if rc is not None else "?", result_path))
        if result.get("status") != "completed":
            self._log_detail(
                "外部执行返回失败: run_id={} status={} message={} result={}"
                .format(run_id, result.get("status"),
                        result.get("message"), result_path))
            raise RuntimeError("外部应用执行失败：{}".format(
                result.get("message") or "子实例返回失败状态"))

        # 控制台只打日志文件名；完整绝对路径、run_id、result 路径、
        # 版本号与全部统计写进日志正文（用户：控制台要短、日志不能缩减）
        log_path = result.get("log_path") or ""
        log_display = os.path.basename(log_path) or "(无)"
        print("外部执行完成：成功 {} / 失败 {} / 取消 {}，耗时 {} 秒；日志 {}"
              .format(result.get("success", 0), result.get("fail", 0),
                      result.get("cancelled", 0),
                      result.get("elapsed_s", 0), log_display))
        self._log_detail(
            "外部执行完成详情: run_id={} status={} success={} fail={} "
            "cancelled={} elapsed_s={} hou_version={}\n"
            "  hip_path={} config_name={} host_config={}\n"
            "  result={}\n  ticket={}\n  子日志完整路径={}".format(
                run_id, result.get("status"),
                result.get("success", 0), result.get("fail", 0),
                result.get("cancelled", 0), result.get("elapsed_s", 0),
                result.get("hou_version"), result.get("hip_path"),
                result.get("config_name"), self._host_config_name,
                result_path, ticket_path, log_path))
