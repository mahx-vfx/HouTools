"""
Automation — 任务节点路径跟随改名（path_tracker）
==================================================

BUTTON_CLICK 任务的 ``params.node_path`` 存在工程外部 JSON（$HIP 配置
目录）里，节点改名后按旧路径解析会失败（引擎报「节点不存在」）。本
模块让路径像 Houdini 原生参数引用（``chs()`` 等）一样跟随改名。

机制（全部行为经 H22.0.429 / H21.0.440 hython 无头探针实测）：

- **监听源**：对每个被引用节点及其祖先网络链注册 ``NameChanged``
  节点事件（**只订改名**）。事件在改名调用的**同一同步调用栈内**
  触发（无事件循环依赖，无头可用）；父级改名只在父节点上发事件，
  子节点无感知——祖先链必须一并注册。
- **改名处理**：由事件 kwargs（``node``=新路径 + ``old_name``）反推
  旧绝对路径，对配置 JSON 里命中的 ``node_path`` 做**整段**前缀替换
  并原子落盘，随后同步通知观察者（面板把新文本程序化填回槽位输入框）。
- **注册时机**：``bootstrap()``（uiready 引导，GUI 会话一次）挂
  ``hou.hipFile`` 订阅并对当前工程立即注册；此后 ``reconcile()`` 在
  面板装载/落盘/Start 后对账（**sid 校验盲重绑**——无删除事件后登记
  可能因「节点删除 + 同名重建」过期，节点 sid 与登记不一致即摘旧挂
  新；reload 代际换装、死条目清理、任务增删后的增减注册全部收敛在
  对账一处）。
- **删除不订阅**（2026-11-22 用户拍板「删除掉链接就掉了，与原生
  死链行为一致」）：被引用节点被删后 JSON 路径保留为死链，丢失由
  面板红边框在交互间隙（切回面板/装载/落盘）现形。**删除场景零
  Python 介入——删 30 个节点与纯净 Houdini 逐比特一致**（稳定性
  第一优先级的最终形态；此前 BeingDeleted 回调内做重活曾致批量
  删除卡顿/崩溃，事件面收窄后该场景构造上不存在）。

**单写者语义**：节点改名引发的配置写盘**只由 tracker 完成**（读盘上
最新内容 → 只改命中的 node_path → ``expect_rev`` 原子写，冲突有限
重试）。面板观察者仅同步展示层（``blockSignals`` setText）并重读
rev 基线——面板自身的防抖「改动即存」通道不因改名事件触发，两条写
路径永不交叉；写盘成功后通知面板刷新 ``_config_rev``，否则面板下次
flush 会因 tracker 推进的盘上 rev 撞 ``ConfigConflictError`` 弹窗。

**农场零足迹**：bootstrap 只在 uiready（Houdini 机制本身仅交互会话
执行）调用，内部再挂 ``hou.isUIAvailable()`` 闸门——hython/hbatch
会话即使本模块被导入也不注册任何监听。hip 文件零写入（事件回调是
进程内会话状态，实测不进 hip；无插件机器打开工程完全干净）。

**reload 代际**：向 hou 注册的回调是模块级"桥接壳"（只转发到
``sys.modules`` 里当前模块的实现函数）——Reload Modules 重跑模块体
后，旧注册握着的旧壳依旧按属性转发到最新实现，功能恒新；登记表
（NameError 守卫活过 reload）持有注册时的壳引用，reconcile 对账时
统一换装当前壳。

日常改名的绝大多数与任务无关：改写入口先按配置逐文件做命中检查，
不命中零 IO 零反馈（用户拍板：控制台零 tracker 提示——改名反馈
走左下角 toast，仅 GUI 会话，无头静默）。
"""

from houtools.automation.data_manager import (
    AutomationDataManager,
    ConfigConflictError,
)
from houtools.core.log import get_logger

logger = get_logger("automation.path_tracker")

# 冲突重试次数：tracker 的读-改-写是主线程同步代码段，窗口极小，
# 他人抢先写入（同机多面板/跨机器）时重读盘上内容再试基本必中
_SAVE_RETRIES = 3

# ── 模块级登记表（必须活过热重载，NameError 守卫见项目 Anti-Patterns） ──
# _REGISTERED: {节点路径 str: (注册时使用的桥接壳函数, 注册时的 sessionId)}
#   键 = 注册时节点的绝对路径（改名事件会同步改写键——监听挂在节点
#   对象上跟随节点，键只是簿记）；值 = 壳引用（reconcile 摘除时要同一
#   对象）+ 注册时的节点 sessionId——**无删除事件后，节点可能已销毁
#   （回调随节点消亡）而登记仍在，同名重建的新节点 sid 必然不同，
#   reconcile 据此判定「登记已过期」并盲重绑，防漏挂**。
try:
    _REGISTERED
except NameError:
    _REGISTERED = {}
# _OBSERVERS: 通知回调列表（面板实例的 bound method；多面板共存，
#   destroyed 时各自摘除）
try:
    _OBSERVERS
except NameError:
    _OBSERVERS = []
# _HIP_HOOKED: hipFile 订阅幂等位。reload 保留 True——hipFile 回调
#   是转发壳（恒指最新实现），无需重挂也不得重复挂
try:
    _HIP_HOOKED
except NameError:
    _HIP_HOOKED = False


# ── 纯逻辑（无 hou 依赖，smoke 可直测） ─────────────────────

def compute_old_path(new_path: str, old_name: str):
    """由改名后的新路径 + 旧名字反推旧绝对路径。

    事件 kwargs 只给新路径（node）与旧名（old_name），父级段落没变，
    拼回去即旧路径。非法输入（空值 / 不以 ``/`` 开头）返回 None。

    注意 ``rpartition`` 头段形态：``"/obj/geo2"`` → 头段 ``"/obj"``；
    ``"/geo2"``（根直挂节点）→ 头段是**空串**而非 ``"/"``——旧路径
    拼回根下（smoke 固化）。
    """
    if not new_path or not old_name or not new_path.startswith("/"):
        return None
    parent = new_path.rpartition("/")[0]
    if parent == "":
        return "/" + old_name
    return f"{parent}/{old_name}"


def apply_rename_to_path(path: str, changes) -> str:
    """把 changes（(old, new) 对的序列）依次应用到 path。

    匹配必须**整段**：old 与 path 相等，或 old 是 path 的目录段前缀
    （``path.startswith(old + "/")``）——``/obj/geo1`` 不得误命中
    ``/obj/geo10``（smoke 固化）。空对直接跳过。
    """
    for old, new in changes or ():
        if not old or not new:
            continue
        if path == old:
            path = new
        elif path.startswith(old + "/"):
            path = new + path[len(old):]
    return path


def rewrite_task_paths(tasks, changes) -> int:
    """就地改写 tasks 里 BUTTON_CLICK 的 ``params.node_path``。

    只碰 BUTTON_CLICK + params 是对象 + node_path 是非空字符串的
    条目；其他类型（FLIPBOOK 输出路径是文件路径、HA 是 URL）与未知
    类型透传条目一律不动。返回命中改写的条目数。
    """
    hits = 0
    for t in tasks or ():
        if not isinstance(t, dict) or t.get("type") != "BUTTON_CLICK":
            continue
        params = t.get("params")
        if not isinstance(params, dict):
            continue
        node_path = params.get("node_path")
        if not isinstance(node_path, str) or not node_path:
            continue
        new_path = apply_rename_to_path(node_path, changes)
        if new_path != node_path:
            params["node_path"] = new_path
            hits += 1
    return hits


# ── 配置 JSON 改写（tracker 是唯一写者） ────────────────────

def _rewrite_configs(changes):
    """把 changes 落进当前 $HIP 配置目录的所有配置文件。

    逐文件：load 盘上最新内容 → 命中检查（不命中零 IO，日常改名
    常态）→ ``save(expect_rev)`` 原子写；``ConfigConflictError``
    （他人抢先写入）→ 重读重试（有限次，重试必须重新 load——盘上
    内容已被别人改过，旧快照作废）。

    返回 ``(受影响配置名元组, {配置名: 写盘后 rev 或 None})``。
    rev 为 None = 该文件命中但未落盘成功（IO 失败/重试耗尽）——
    观察者只更新展示层不刷新基线，面板下次真实编辑落盘会自愈。
    """
    touched = []
    revs = {}
    try:
        names = AutomationDataManager.list_configs()
    except Exception as exc:
        logger.warning("path_tracker: 列出配置目录失败: %s", exc)
        return tuple(touched), revs
    for name in names:
        for _attempt in range(_SAVE_RETRIES):
            try:
                tasks = AutomationDataManager.load(name)
            except Exception as exc:
                logger.warning("path_tracker: 读取配置 %s 失败: %s", name, exc)
                break
            if not tasks:
                break  # 无任务（文件缺失/空配置）——零写盘
            try:
                hits = rewrite_task_paths(tasks, changes)
            except Exception as exc:
                logger.warning("path_tracker: 改写配置 %s 失败: %s", name, exc)
                break
            if not hits:
                break  # 不引用被改名节点——零 IO（日常改名的常态）
            try:
                rev = AutomationDataManager.read_config_rev(name)
                ok = AutomationDataManager.save(
                    tasks, filename=name, expect_rev=rev)
            except ConfigConflictError:
                continue  # 他人抢先写入：重读盘上最新内容重试
            except Exception as exc:
                logger.warning(
                    "path_tracker: 配置 %s 落盘失败: %s", name, exc)
                ok = False
            if ok:
                touched.append(name)
                try:
                    revs[name] = AutomationDataManager.read_config_rev(name)
                except Exception:
                    revs[name] = None
            else:
                revs[name] = None
            break
    return tuple(touched), revs


# ── 观察者（纯 Python 回调列表，不碰 Qt——无头可测） ────────

def add_observer(fn) -> None:
    """注册观察者（面板实例；回调签名 ``fn(event: dict)``，主线程）。"""
    if fn not in _OBSERVERS:
        _OBSERVERS.append(fn)


def remove_observer(fn) -> None:
    """摘除观察者（面板 destroyed 兜底调用；不存在时静默）。"""
    try:
        _OBSERVERS.remove(fn)
    except ValueError:
        pass


def _notify(event: dict) -> None:
    """同步通知全部观察者；单个观察者异常只记录，不打断其他接收者。"""
    for fn in list(_OBSERVERS):
        try:
            fn(event)
        except Exception as exc:
            logger.warning("path_tracker: 观察者回调失败: %s", exc)


def _toast(level: str, message: str) -> None:
    """左下角 toast 通知（用户拍板：tracker 反馈走通知，控制台零提示）。

    - 仅 GUI 会话发（``hou.isUIAvailable`` 闸门）：无头环境直接静默——
      notify 的无头降级 logger 会进控制台（houtools logger DEBUG 级也
      打印），裸调会让 smoke/e2e 输出重新出现 tracker 行；
    - 通知是纯反馈通道，失败静默吞掉（toast 渲染失败无实际后果）。
    """
    try:
        import hou
        if not hou.isUIAvailable():
            return
    except ImportError:
        return
    except Exception:
        pass
    try:
        from houtools.ui import notify
        getattr(notify, level)(message)
    except Exception:
        pass  # 见 docstring：通知失败无后果，控制台零提示


# ── 节点事件处理（impl，由桥接壳转发） ──────────────────────

def _on_node_event_impl(*args, **kwargs):
    """节点事件分发入口（壳转发目标）。回调签名按 kwargs 收：
    ``node``（事件源节点，路径已是新值）、``event_type``、``old_name``。
    只处理改名——删除不订阅（见模块头「删除不订阅」）。"""
    try:
        import hou
    except ImportError:
        return
    event_type = kwargs.get("event_type")
    if event_type is hou.nodeEventType.NameChanged:
        _handle_rename(kwargs.get("node"), kwargs.get("old_name"))


def _handle_rename(node, old_name) -> None:
    """改名：反推旧路径 → 登记表改键 → 配置落盘 → 通知观察者。"""
    try:
        new_path = node.path() if node is not None else ""
    except Exception:
        return
    old_path = compute_old_path(new_path, old_name or "")
    if old_path is None or old_path == new_path:
        return
    # 登记表键随路径改写（含祖先改名时后代条目的键——监听挂在节点
    # 对象上不变，键只是簿记）
    _rekey_registered(old_path, new_path)
    changes = ((old_path, new_path),)
    touched, revs = _rewrite_configs(changes)
    if touched:
        # 反馈走左下角 toast（用户拍板：控制台零 tracker 提示）；未命中
        # 任务的改名零反馈（常态静默）
        _toast("info", "任务路径已跟随改名：%s → %s" % (old_path, new_path))
    _notify({
        "kind": "renamed",
        "changes": changes,
        "configs": touched,
        "revs": revs,
    })


def _rekey_registered(old_path: str, new_path: str) -> None:
    """登记表键随改名改写（整段匹配，与配置改写同一套纯逻辑）。"""
    for key in list(_REGISTERED):
        new_key = apply_rename_to_path(key, ((old_path, new_path),))
        if new_key != key:
            _REGISTERED[new_key] = _REGISTERED.pop(key)


# ── 注册簿记 ────────────────────────────────────────────────

def _event_types():
    """返回订阅的事件类型元组：**只含 NameChanged**。

    删除不订阅（2026-11-22 用户拍板「删除掉链接就掉了，与原生死链
    行为一致」）——删除场景零 Python 介入，删 30 个节点与纯净
    Houdini 逐比特一致；断链可视化由面板在交互间隙评估
    （window._refresh_missing_states，挂装载/落盘/showEvent）。
    元组必须全是 ``hou.nodeEventType`` 枚举成员——SWIG 生成对象的
    ``dir()`` 带有 ``thisown`` 等非枚举属性，动态拼元组会 TypeError
    （探针实测），这里写死。"""
    import hou
    return (hou.nodeEventType.NameChanged,)


def _register_path(path: str) -> bool:
    """对路径节点及其祖先链注册改名事件桥，返回是否发生了新注册。

    登记 = (壳, 注册时的 sessionId)。**sid 校验防漏挂**：无删除事件
    后，被引用节点可能已销毁而登记仍在——同名重建的新节点 sid 必然
    不同，判定「登记过期」摘旧挂新；sid 一致且壳是当前桥 = 同一个
    节点、回调必然仍在岗（跳过，稳定态零 HOM 写操作）。重复注册会让
    同一事件触发多次回调，此判定同时防重。reload 后条目里存的是旧代
    壳 → 同样走摘旧挂新换装。祖先链 parent() 逐级上溯（父级改名只在
    父节点上发事件，子节点无感知——实测）。"""
    try:
        import hou
    except ImportError:
        return False
    node = hou.node(path)
    if node is None:
        return False
    types = _event_types()
    registered = False
    chain = [node]
    parent = node.parent()
    while parent is not None:
        chain.append(parent)
        parent = parent.parent()
    for member in chain:
        try:
            member_path = member.path()
            member_sid = member.sessionId()
        except Exception:
            continue
        entry = _REGISTERED.get(member_path)
        if entry is not None and entry[0] is _node_event_bridge \
                and entry[1] == member_sid:
            continue  # 同节点同桥：回调必然仍在岗，零操作
        if entry is not None:
            # 登记过期（节点销毁重建 sid 变了 / reload 旧代壳）：摘旧
            # 挂新；旧回调已随旧节点消亡时摘除失败——静默后重挂
            try:
                member.removeEventCallback(types, entry[0])
            except Exception:
                pass
        try:
            member.addEventCallback(types, _node_event_bridge)
        except Exception as exc:
            logger.warning("path_tracker: 注册事件失败 %s: %s",
                           member_path, exc)
            continue
        _REGISTERED[member_path] = (_node_event_bridge, member_sid)
        registered = True
    return registered


def _unregister_path(path: str) -> None:
    """摘除单条登记（reconcile 清死条目/不再引用条目用）。

    节点仍存活才调 removeEventCallback（条目可能是过期登记——旧回调
    已随销毁节点消亡，摘除自然失败，一律 try 容错）；节点已销毁时
    回调随节点消亡，登记条目直接清即可。"""
    entry = _REGISTERED.pop(path, None)
    if entry is None:
        return
    try:
        import hou
    except ImportError:
        return
    node = hou.node(path)
    if node is None:
        return
    try:
        node.removeEventCallback(_event_types(), entry[0])
    except Exception as exc:
        logger.debug("path_tracker: 摘除事件失败 %s: %s", path, exc)


def reset_all() -> None:
    """hip 切换/清空：全量摘除监听清空登记表（旧 hip 的节点全部失效）。"""
    for key in list(_REGISTERED):
        _unregister_path(key)


def _expected_paths() -> set:
    """当前 $HIP 全部配置文件里被引用的节点路径全集（reconcile 的期望集）。

    遍历**全部**配置文件而非仅面板当前配置——改名跟随对每个配置生效，
    不依赖用户当前停在哪个配置上。"""
    paths = set()
    try:
        names = AutomationDataManager.list_configs()
    except Exception as exc:
        logger.warning("path_tracker: 列出配置目录失败: %s", exc)
        return paths
    for name in names:
        try:
            tasks = AutomationDataManager.load(name)
        except Exception as exc:
            logger.debug("path_tracker: 读取配置 %s 失败: %s", name, exc)
            continue
        for t in tasks or ():
            if not isinstance(t, dict) or t.get("type") != "BUTTON_CLICK":
                continue
            params = t.get("params")
            if not isinstance(params, dict):
                continue
            node_path = params.get("node_path")
            if isinstance(node_path, str) and node_path.startswith("/"):
                paths.add(node_path)
    return paths


def _is_ancestor_of_any(path: str, paths) -> bool:
    """path 是否是 paths 中某条路径的目录段祖先（祖先链条目保护）。"""
    prefix = path + "/"
    return any(p.startswith(prefix) for p in paths if p != path)


def reconcile() -> None:
    """对账：登记表 ↔ 配置引用路径集。

    - 期望集之外的条目（任务被删）→ 摘除；
    - 死条目（节点已销毁）→ 摘除——无删除事件后登记可能因「删除 +
      同名重建」过期，``_register_path`` 的 sid 校验会对过期条目盲
      重绑（正确性保障），对账清掉条目只是卫生；
    - 新引用 → 注册（含祖先链）；
    - reload 换代条目 → 摘旧壳挂当前壳。

    无头环境（smoke/无 Houdini 导入）直接返回。调用点：bootstrap、
    hipFile AfterLoad/AfterMerge、面板装载/落盘/Start 之后。
    """
    try:
        import hou  # noqa: F401 — 有 hou 才做簿记，无头直接返回
    except ImportError:
        return
    want = _expected_paths()
    for key in list(_REGISTERED):
        try:
            alive = hou.node(key) is not None
        except Exception:
            alive = False
        if not alive:
            # 死条目（节点已销毁）：回调随节点消亡，登记直接清——
            # 无删除事件后这是过期登记唯一的清理通道（配置仍引用死
            # 路径时 key in want 挡不住，必须按节点存活判定）
            _unregister_path(key)
            continue
        if key in want or _is_ancestor_of_any(key, want):
            continue  # 活引用/祖先链条目：仍服务于现存引用，保留
        _unregister_path(key)  # 活节点但已不被任何配置引用 → 摘除
    for path in sorted(want):
        _register_path(path)


# ── hipFile 订阅 ────────────────────────────────────────────

def _on_hip_event_impl(event_type, **kwargs):
    """hipFile 事件分发（壳转发目标）：hip 切换/合并后重置并重注册。

    AfterLoad：旧 hip 的节点全部销毁（回调随之失效）→ 清登记 → 对
    新工程配置重新注册——堵「打开工程后、开面板前改名」的空窗。
    AfterMerge：合入副本不带监听（实测），被引用节点不受影响，对账
    一次兜底簿记。AfterClear：场景清空，登记清零。
    """
    try:
        import hou
    except ImportError:
        return
    if event_type in (hou.hipFileEventType.AfterLoad,
                      hou.hipFileEventType.AfterMerge):
        reset_all()
        reconcile()
    elif event_type is hou.hipFileEventType.AfterClear:
        reset_all()


def bootstrap() -> None:
    """会话引导——只在 uiready 调用（UI 启动钩子本身 GUI-only）。

    双保险闸门：``hou.isUIAvailable()`` 显式拒绝无头会话（农场/
    hython/hbatch 里即使本模块被导入也不注册任何监听，零足迹）。
    重复引导（reload 复跑/手动调用）只做对账，不重复挂 hipFile 回调。
    """
    global _HIP_HOOKED
    try:
        import hou
    except ImportError:
        return
    try:
        if not hou.isUIAvailable():
            return
    except Exception:
        pass  # 探测失败按可用处理（正常 GUI 会话不会走到）
    if _HIP_HOOKED:
        reconcile()
        return
    try:
        hou.hipFile.addEventCallback(_hip_event_bridge)
        _HIP_HOOKED = True
    except Exception as exc:
        # 注意 hipFile 的 addEventCallback 只收回调一个参数（与 Node
        # 版的 (types, callback) 签名不同），事件类型在回调参数里过滤
        logger.warning("path_tracker: hipFile 订阅失败: %s", exc)
        return
    reconcile()
    # 不发任何"已就绪"提示（用户拍板：启动零提示）——注册是否在岗由
    # 首次改名/删除时的 toast 与面板红边框自然体现


# ── 桥接壳（reload 代际转发，见模块头注释） ─────────────────

def _node_event_bridge(*args, **kwargs):
    """节点事件壳：转发到当前模块实现。壳被重新定义也无碍——旧壳
    按 import 属性取到的是最新实现函数。"""
    try:
        from houtools.automation import path_tracker as _current
    except Exception:
        return
    _current._on_node_event_impl(*args, **kwargs)


def _hip_event_bridge(event_type, **kwargs):
    """hipFile 事件壳：签名只收回调一个参数（hipFile 专属），转发同上。"""
    try:
        from houtools.automation import path_tracker as _current
    except Exception:
        return
    _current._on_hip_event_impl(event_type, **kwargs)
