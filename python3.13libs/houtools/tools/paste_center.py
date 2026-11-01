"""粘贴中心（Paste Center，节点右键菜单，双模式子项）
=========================================
网络编辑器 Ctrl+C 复制源节点 A 后，右键目标节点 B →「Paste Center [HT]」
子菜单（OPmenu.xml，所有节点可见）：在 B 正下方创建 transform 节点并接管
显示/渲染输出（启用），把 B 的输出内容平移到 A 的中心位置——

  px py pz ← centroid(B)             绝对值；枢轴落在 B 自身原中心，
                                     后续旋转/缩放绕自身中心
  tx ty tz ← centroid(A) − centroid(B)

  Paste Absolute Values [HT]   绝对值：求值后直接写入（transform 是新建
                               节点，参数天然干净，无需清表达式/关键帧）
  Paste Expressions [HT]       表达式：写入 centroid() 链接表达式；A 与 B
                               同网络用相对路径（../A），跨层级绝对路径；
                               之后移动 A 或 B，transform 实时跟随

transform 类型按目标节点所在网络类别映射：SOP/LOP/COP 的显示名都叫
Transform、内部类型名统一是 xform，t/p 均为三分量元组（H22.0.429 无头
实测三类俱全）；其余类别（Object/ROP/DOP 等）没有可创建的 transform
节点 → 黄字拒绝。

实现要点（与 paste_bbox.py 同源的坑）：
  - 绝对值与表达式走同一套 hou.hscriptExpression 求值 centroid()，cook
    出错的节点上不抛异常、静默返 0——求值前 src.errors() 与
    target.errors() 双预检，任一有错不写（求值双方都要拦）。
  - 新建节点的参数无锁、无关键帧：无需 deleteAllKeyframes /
    PermissionError 兜底（paste_bbox 改的是既有节点才需要）。
  - 源解析懒 import 复用 paste_as_object_merge 的 _source_items（点击时
    才加载，防 reloader 旧引用，同 delete_viewport_capture 手法）；
    剪贴板三种失效文案由它统一给出，这里拿不到路径直接返回。
  - "启用" = setDisplayFlag(True) + setRenderFlag(True)——不接管输出，
    新节点只是挂在网上的死支路，视窗里看不到效果。
"""

from houtools.core.log import get_logger

logger = get_logger("tools.paste_center")

# 目标节点所在网络类别 → transform 节点内部类型名
_TRANSFORM_TYPES = {
    "Sop": "xform",
    "Lop": "xform",
    "Cop": "xform",
}

_STACK_GAP = 0.5   # 与 dragdrop._STACK_GAP 同值（纵向摆位视觉一致）
_UNDO_GROUP = "pasteCenter"

# centroid() 的轴常量，与分量下标一一对应（px/py/pz、tx/ty/tz）
_CENTROID_AXES = ("D_X", "D_Y", "D_Z")


def run_absolute(kwargs=None):
    """Paste Absolute Values [HT] 菜单入口。"""
    _apply(kwargs, expression_mode=False)


def run_expressions(kwargs=None):
    """Paste Expressions [HT] 菜单入口。"""
    _apply(kwargs, expression_mode=True)


def _apply(kwargs, expression_mode):
    import hou

    target = (kwargs or {}).get("node")
    if target is None:
        return

    type_name = _TRANSFORM_TYPES.get(target.type().category().name())
    if type_name is None:
        _status("{} 上下文没有可创建的 transform 节点".format(
            target.type().category().name()), severity="warning")
        return

    # 点击时才加载，保证拿到 reloader 重载后的最新实现（含失效文案）
    from houtools.tools.paste_as_object_merge import _source_items
    items = _source_items()
    if not items:
        return   # 剪贴板三种失效文案由 _source_items 统一给出
    src = hou.node(items[0])
    if src is None:
        _status("剪贴板源节点已不存在（{}）".format(items[0]),
                severity="warning")
        return
    if src.path() == target.path():
        _status("源与目标是同一节点，未创建", severity="warning")
        return
    if not _node_ok(src) or not _node_ok(target):
        return

    try:
        with hou.undos.group(_UNDO_GROUP):
            xform = target.parent().createNode(type_name)
            xform.setNextInput(target)
            x, y = target.position()
            _w, h = target.size()
            xform.setPosition(hou.Vector2(x, y - h - _STACK_GAP))
            xform.setDisplayFlag(True)
            xform.setRenderFlag(True)
            _write_parms(xform, src, target, expression_mode)
            xform.setSelected(True, clear_all_selected=True)
    except Exception as exc:
        logger.warning("paste_center failed on %s: %s", target.path(), exc)
        _status("创建 transform 失败：{}".format(exc), severity="error")
        return

    _status("已把 {} 的中心粘贴到 {}（transform {}，{}）".format(
        src.name(), target.name(), xform.name(),
        "表达式" if expression_mode else "绝对值"), severity="important")


def _node_ok(node):
    """cook 出错预检：hscriptExpression 出错静默返 0，先挡下（paste_bbox 同款）。"""
    try:
        errors = node.errors()
    except Exception as exc:
        logger.debug("errors() probe failed on %s: %s", node.path(), exc)
        return True
    if errors:
        brief = str(errors).strip().splitlines()[0][:80]
        _status("节点 {} 存在错误，未粘贴：{}".format(node.name(), brief),
                severity="warning")
        return False
    return True


def _eval_center(path):
    """hscriptExpression 求节点中心三分量（cook 错误已在上游预检）。"""
    import hou
    return tuple(
        hou.hscriptExpression('centroid("{}", {})'.format(path, axis))
        for axis in _CENTROID_AXES)


def _write_parms(xform, src, target, expression_mode):
    """写 p（=目标中心）与 t（=源中心 − 目标中心）。

    transform 是新建节点：参数干净，set/setExpression 直接写、无需先清。
    """
    import hou

    target_path = xform.relativePathTo(target)
    if expression_mode:
        src_path = xform.relativePathTo(src)
        for i, axis in enumerate(_CENTROID_AXES):
            xform.parmTuple("p")[i].setExpression(
                'centroid("{}", {})'.format(target_path, axis),
                hou.exprLanguage.Hscript)
            xform.parmTuple("t")[i].setExpression(
                'centroid("{}", {}) - centroid("{}", {})'.format(
                    src_path, axis, target_path, axis),
                hou.exprLanguage.Hscript)
    else:
        cur_center = _eval_center(target.path())
        src_center = _eval_center(src.path())
        for i in range(3):
            xform.parmTuple("p")[i].set(cur_center[i])
            xform.parmTuple("t")[i].set(src_center[i] - cur_center[i])


def _status(message, severity="important"):
    """状态栏提示。

    severity: "important" 高亮成功确认 / "warning" 预期内拒绝（未创建
    任何东西）/ "error" 意外失败（内部映射 hou.severityType，调用点无须
    import hou）。
    """
    try:
        import hou
        level = {
            "important": hou.severityType.ImportantMessage,
            "warning": hou.severityType.Warning,
            "error": hou.severityType.Error,
        }[severity]
        hou.ui.setStatusMessage("HouTools: " + message, level)
    except Exception as exc:
        logger.debug("setStatusMessage failed: %s", exc)
