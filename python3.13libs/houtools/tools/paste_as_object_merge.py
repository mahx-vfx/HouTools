"""粘贴为 Object Merge（Ctrl+Shift+V）
=================================
在网络编辑器中 Ctrl+C 复制节点后，按 Ctrl+Shift+V 在鼠标位置按
"目标上下文 × 源节点类别"创建引用节点（XXX 表示源节点名）：

  规则 1    SOP → SOP   object_merge ``Merge_XXX``（objpath1，同网络相对路径）
  规则 2    SOP → OBJ   geo ``XXX``，内含 object_merge ``Merge_XXX``
  规则 3    SOP → LOP   sopimport ``SOP_XXX``（soppath）
  规则 4    LOP → ROP   usdrender ``XXX``（loppath）
  规则 5    LOP → SOP   lopimport ``LOP_XXX``（loppath）
  规则 6    SOP → DOP   staticobject ``Object_XXX``（soppath）
  规则 7    LOP → LOP   fetch ``LOP_XXX``（loppath）
  规则 8    SOP → ROP   fetch ``SOP_XXX``（source；源为 File Cache 时
                        路径追加 ``/render``，指向其内部缓存 ROP）
  规则 9    SOP → COP   sopimport ``SOP_XXX``（soppath；仅新 COP，且需置 usesoppath=1）
  规则 10   OBJ → LOP   sopimport ``SOP_XXX``（soppath，可填 obj 路径）
  规则 11   OBJ → SOP   object_merge ``Merge_XXX``（objpath1 指向物体）

前置规则：粘贴出的节点颜色与源节点一致（规则 2 的 geo 容器同样着色）。
未列出的"目标 × 源"组合一律跳过，未覆盖的上下文静默不动。每个引用节点
横向偏移 n*2 排开；首个新节点带 clear_all_selected 选中。整次操作包在
``hou.undos.group`` 里，占用单个 undo 槽。

源节点解析只信 OS 剪贴板文本 —— Houdini Ctrl+C 时自写的节点路径（引用
语义必须有"原件路径"，内部节点剪贴板无法反查原件）。文本失效一律拒绝并
按失效原因给出可行动的提示（原件已不存在 / 剪贴板被其他复制覆盖），
不粘贴副本兜底——副本会污染目标网络且引用的是冻结快照（2026-11 用户
拍板移除该兜底）。

入口 ``run()`` 由 NetworkViewMenu.xml 的 scriptItem（含菜单点击与热键
触发）经 ``houtools.dev.dispatcher`` 调用；默认键位由 ``python3.13libs/uiready.py``
在会话启动时通过 ``hou.hotkeys.addAssignment`` 分配。
"""

import re

from houtools.core.log import get_logger

logger = get_logger("net.paste_merge")

# Houdini 网络视图拖放/剪贴板共享的节点路径 mime 格式
# （见 houpythonportion/qt/__init__.py 中的格式清单）
_MIME_NODE_PATH = "application/sidefx-houdini-node.path"

_HOU_NODE_RE = re.compile(
    r"""hou\s*\.\s*node\s*\(\s*['"](.+?)['"]\s*\)\s*$"""
)
# 多行形态（网络编辑器一次复制多个节点时，文本剪贴板是每行一个
# hou.node('...')）：上面的 $ 锚定匹配不到整段，提取/判型用非锚定版
_HOU_NODE_ANY_RE = re.compile(
    r"""hou\s*\.\s*node\s*\(\s*['"](.+?)['"]\s*\)"""
)


def run(**kwargs):
    """按"目标上下文 × 源节点类别"创建引用节点（规则 1-10）。"""
    import hou

    editor = _target_editor(kwargs)
    if editor is None:
        _status("未找到网络编辑器，无法粘贴", severity="warning")
        return

    context = editor.pwd()
    context_type = context.type().childTypeCategory()
    position = _paste_position(editor)
    try:
        with hou.undos.group("pasteAsObjectMerge"):
            _paste_as_merge(context, context_type, position)
    except Exception as e:
        _status(f"粘贴失败：{e}", severity="error")
        logger.warning("paste_as_object_merge failed in %s: %s", context.path(), e)


def _paste_as_merge(context, context_type, position):
    """上下文分派主流程：规则 1-10 覆盖的上下文，其余一律静默不动。"""
    import hou

    if context_type == hou.sopNodeTypeCategory():
        _paste_into_sop(context, position)
    elif context_type == hou.objNodeTypeCategory():
        _paste_into_obj(context, position)
    else:
        rules = _reference_rules().get(context_type)
        if rules is not None:
            _paste_reference_nodes(context, position, rules)


def _reference_rules() -> dict:
    """目标网络类别 → {源节点类别: (节点类型, 路径参数, 名称前缀, 附加参数)}。

    覆盖命名规则 3/4/6/7/8/9/10；规则 1/2/11 在 _paste_into_sop /
    _paste_into_obj 里单独处理（SOP 网络有内部剪贴板兜底、OBJ 网络要建
    geo 容器）。名称前缀为空串表示直接用源节点名；附加参数在路径参数
    之前设置。
    """
    import hou

    sop = hou.sopNodeTypeCategory()
    lop = hou.lopNodeTypeCategory()
    rules = {
        # 规则 3 SOP→LOP / 规则 10 OBJ→LOP / 规则 7 LOP→LOP
        lop: {
            sop: ("sopimport", "soppath", "SOP_", {}),
            hou.objNodeTypeCategory(): ("sopimport", "soppath", "SOP_", {}),
            lop: ("fetch", "loppath", "LOP_", {}),
        },
        # 规则 4 LOP→ROP / 规则 8 SOP→ROP
        hou.ropNodeTypeCategory(): {
            lop: ("usdrender", "loppath", "", {}),  # /out 的 USD Render ROP 内部名（usdrender_rop 是 LOP 内版本）
            sop: ("fetch", "source", "SOP_", {}),
        },
        # 规则 6 SOP→DOP
        hou.dopNodeTypeCategory(): {
            sop: ("staticobject", "soppath", "Object_", {}),
        },
        # 规则 9 SOP→COP：仅新 COP（copnet）；sopimport 的 usesoppath 默认 0，须先置 1
        hou.copNodeTypeCategory(): {
            sop: ("sopimport", "soppath", "SOP_", {"usesoppath": 1}),
        },
    }
    return rules


def _paste_into_sop(context, position):
    """SOP 网络：SOP/OBJ 源 → object_merge（规则 1/11），LOP 源 → lopimport（规则 5）。"""
    import hou

    src_items = _source_items()
    if not src_items:
        return

    n = 0
    for item in src_items:
        src = hou.node(item)
        if src is None:
            continue
        basename = item.rsplit("/", 1)[-1]
        if src.type().category() == hou.lopNodeTypeCategory():
            merge = context.createNode("lopimport", "LOP_" + basename)
            merge.parm("loppath").set(str(item))
        elif src.type().category() == hou.sopNodeTypeCategory():
            merge = context.createNode("object_merge", "Merge_" + basename)
            merge.parm("objpath1").set(_objpath_for(merge, item))
        elif src.type().category() == hou.objNodeTypeCategory():
            # 规则 11：OBJ 源 → object_merge 引用整个物体（objpath1 指向 /obj 节点，
            # 坐标变换走节点默认的 Into This Object，与规则 1 行为一致）
            merge = context.createNode("object_merge", "Merge_" + basename)
            merge.parm("objpath1").set(_objpath_for(merge, item))
        else:
            continue  # 未定义的"目标 × 源"组合，跳过
        _place(merge, position, n, src.color())
        merge.setSelected(True, clear_all_selected=(n == 0))
        n += 1
    _report_unmatched(n)


def _paste_into_obj(context, position):
    """OBJ 网络（规则 2）：SOP 源 → 每源一个 geo ``XXX``，内含 ``Merge_XXX``。"""
    import hou

    src_items = _source_items()
    if not src_items:
        return

    n = 0
    for item in src_items:
        src = hou.node(item)
        if src is None or src.type().category() != hou.sopNodeTypeCategory():
            continue
        basename = item.rsplit("/", 1)[-1]
        color = src.color()
        geo = context.createNode("geo", basename)
        _place(geo, position, n, color)
        merge = geo.createNode("object_merge", "Merge_" + basename)
        merge.parm("objpath1").set(_objpath_for(merge, item))
        merge.setColor(color)
        geo.setSelected(True, clear_all_selected=(n == 0))
        n += 1
    _report_unmatched(n)


def _paste_reference_nodes(context, position, rules):
    """通用引用粘贴（命名规则 3/4/6/7/8/9/10）：按源类别查表创建引用节点。"""
    import hou

    src_items = _source_items()
    if not src_items:
        return

    context_type = context.type().childTypeCategory()
    n = 0
    for item in src_items:
        src = hou.node(item)
        if src is None:
            continue
        rule = rules.get(src.type().category())
        if rule is None:
            continue  # 未定义的"目标 × 源"组合，跳过
        node_type, parm_name, prefix, extra_parms = rule
        node = context.createNode(node_type, prefix + item.rsplit("/", 1)[-1])
        for extra_name, extra_value in extra_parms.items():
            node.parm(extra_name).set(extra_value)
        node.parm(parm_name).set(str(item) + _path_suffix(context_type, src))
        _place(node, position, n, src.color())
        node.setSelected(True, clear_all_selected=(n == 0))
        n += 1
    _report_unmatched(n)


# ── 源解析 ────────────────────────────────────────────────────

def _source_items() -> list[str]:
    """返回剪贴板源节点路径列表（已验证存在）；为空时给出可行动的提示。

    仅信 OS 剪贴板文本——内部节点剪贴板无法反查原件路径，粘贴副本兜底
    会让目标网络凭空多出副本节点、引用的还是冻结快照（2026-11 用户拍板
    移除该兜底）。文本失效按原因区分文案：路径文本在但节点没了 → 原件
    被删/改名；文本是其他内容 → 剪贴板被覆盖；文本为空 → 从未复制。
    """
    import hou

    paths = _clipboard_node_paths()
    if paths:
        return paths

    try:
        text = hou.ui.getTextFromClipboard().strip()
    except Exception as e:
        logger.debug("clipboard text probe failed: %s", e)
        text = ""
    if not text:
        _status("剪贴板中没有可粘贴的节点（先在网络编辑器 Ctrl+C 复制节点）",
                severity="warning")
    elif _path_like_text(text):
        _status("剪贴板中的源节点已不存在（被删除或重命名），请重新复制",
                severity="warning")
    else:
        _status("剪贴板文本失效（已被其他复制覆盖），请重新复制节点",
                severity="warning")
    return []


def _path_like_text(text: str) -> bool:
    """剪贴板文本是否"像节点路径"（hou.node('...') 或 / 开头 token）。

    纯字符串判定、不碰 hou（smoke test 直测）：用于区分「复制过节点但
    原件已不存在」与「剪贴板被其他文本复制覆盖」两种失效文案。
    """
    if _HOU_NODE_RE.match(text) or _HOU_NODE_ANY_RE.search(text):
        return True
    return any(token.startswith("/") for token in text.split())


def _clipboard_node_paths() -> list[str]:
    """从 OS 剪贴板提取被复制节点路径，返回验证存在的路径列表。"""
    import hou

    paths: list[str] = []

    try:
        from PySide6.QtWidgets import QApplication
        mime = QApplication.clipboard().mimeData()
        if mime is not None and mime.hasFormat(_MIME_NODE_PATH):
            raw = bytes(mime.data(_MIME_NODE_PATH)).decode("utf-8", "replace")
            for token in raw.replace("\r", " ").replace("\n", " ").split():
                token = token.strip()
                if token and hou.node(token) is not None:
                    paths.append(token)
    except Exception as e:
        logger.debug("mime clipboard probe failed: %s", e)

    if paths:
        return paths

    try:
        text = hou.ui.getTextFromClipboard().strip()
        if text:
            # 先全量提取 hou.node('...')：多节点复制是每行一个，单行
            # 锚定正则匹配不到整段，token 过滤又只认 / 开头会全部漏掉
            found = _HOU_NODE_ANY_RE.findall(text)
            if found:
                paths.extend(p for p in found if hou.node(p) is not None)
            else:
                m = _HOU_NODE_RE.match(text)
                if m:
                    text = m.group(1)
                for token in text.split():
                    if token.startswith("/") and hou.node(token) is not None:
                        paths.append(token)
    except Exception as e:
        logger.debug("text clipboard probe failed: %s", e)

    return paths


# ── 通用 helper ───────────────────────────────────────────────

def _objpath_for(merge, src_path: str) -> str:
    """merge 与源同网络时用相对路径，否则保持绝对路径。"""
    import hou
    src = hou.node(src_path)
    if src is not None and merge.parent() == src.parent():
        return merge.relativePathTo(src)
    return src_path


def _path_suffix(context_type, src) -> str:
    """路径参数的追加后缀：SOP→ROP 的 fetch 且源为 File Cache 时返回 /render。

    File Cache（filecache / filecache::2.0 等）的缓存由其内部 ``render``
    ROP 触发，ROP 网络引用时须指向 ``<filecache>/render``。其余组合一律
    原样引用节点本身。
    """
    import hou

    if context_type != hou.ropNodeTypeCategory():
        return ""
    if src.type().name().split("::", 1)[0] != "filecache":
        return ""
    return "/render"


def _place(node, position, n, color=None):
    """放在鼠标位置并按序号横向偏移 n*2，继承源节点颜色。"""
    node.setPosition(position)
    node.move([n * 2.0, 0])
    if color is not None:
        node.setColor(color)


def _report_unmatched(n):
    """n=0 = 没有任何源匹配当前上下文的规则 → 提示。

    成功（n>0）静默：引用节点落网并选中、结果自明（成功不通知）。
    """
    if n:
        return
    _status("剪贴板节点类型在当前上下文没有对应的粘贴规则",
            severity="warning")


def _target_editor(kwargs: dict):
    """确定粘贴目标网络编辑器：kwargs > 鼠标下方 > 任意一个。"""
    import hou

    editor = (kwargs or {}).get("networkeditor")
    if editor is not None:
        return editor

    try:
        under = hou.ui.paneTabUnderCursor()
        if under is not None and under.type() == hou.paneTabType.NetworkEditor:
            return under
    except Exception as exc:
        logger.debug("paneTabUnderCursor failed: %s", exc)

    return hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)


def _paste_position(editor):
    """返回鼠标当前所在的网络坐标（限制在可视范围内）。"""
    try:
        return editor.cursorPosition()
    except Exception as exc:
        logger.debug("cursorPosition failed, using bounds center: %s", exc)
        # hou.BoundingRect 不可下标访问（实测 TypeError），必须走
        # min()/max()（返回 Vector2）
        bounds = editor.visibleBounds()
        lo, hi = bounds.min(), bounds.max()
        return ((lo.x() + hi.x()) / 2.0, (lo.y() + hi.y()) / 2.0)


def _status(message: str, severity="important"):
    """状态栏提示（不弹窗、不打控制台，保持操作流不被打断）。

    severity: "important" 高亮 / "warning" 预期内拒绝降级 / "error" 意外
    失败（内部映射 hou.severityType，调用点无须 import hou）。粘贴成功
    静默——引用节点落网并选中、结果自明，这里只报拒绝与失败。
    """
    try:
        import hou
        level = {
            "important": hou.severityType.ImportantMessage,
            "warning": hou.severityType.Warning,
            "error": hou.severityType.Error,
        }[severity]
        hou.ui.setStatusMessage(message, level)
    except Exception as exc:
        logger.debug("setStatusMessage failed: %s", exc)
