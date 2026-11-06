"""网络编辑器跳转/取景（Houdini 工具窗口通用小工具）
=========================================
jump_to_node 在网络编辑器中定位并居中一个节点（同官方路径栏点击行为）；
frame_node 是其取景半边（等价按 F），单独暴露给已持有编辑器引用的调用点。

原为 Automation 面板任务槽的跳转实现（automation/window.py 内私有两函数），
Video to Sequence 的「跳转到媒体来源节点」按钮需要同款行为，迁出为共享
模块——两个窗口共用一份实现防行为漂移。迁移不改行为与状态栏文案
（2026-11），Automation 的状态栏前缀经 prefix 参数保持原文。
"""

from houtools.core.log import get_logger
from houtools.dev import status

logger = get_logger("ui.networknav")


def frame_node(editor, node):
    """在 network editor 中取景单个节点（等价按 F 的效果）。

    H22 运行时没有 frameSelection/homeToSelection（文档列出但存根缺失），
    且 setCurrentNode 的 pick 机制会异步清空选择，因此不依赖选择状态：
    用 itemRect 取节点在该编辑器中的精确布局矩形（自适应节点大小，
    之前固定边距版对大节点会裁出视野导致居中时好时坏），四周外扩后交给
    setVisibleBounds。

    set_center_when_scale_rejected 必须为 True：max_scale 默认上限 100，
    当前视图缩放状态使目标缩放超限时，False（默认）会让整个调用被
    拒绝、什么都不发生——这正是"视图移远后无法居中"的原因；True 则
    在缩放被拒时仍把视图中心平移到节点上，保证每次点击都居中。
    """
    try:
        import hou
        rect = editor.itemRect(node)
        mn = rect.min()
        sz = rect.size()
        # 边距取节点尺寸的 1 倍（节点约占画面 1/3），视野留白更宽松；
        # 矩形越大所需放大倍率越小，也更不容易触发 max_scale 缩放上限
        pad_x = max(sz[0] * 1.0, 2.0)
        pad_y = max(sz[1] * 1.0, 1.5)
        editor.setVisibleBounds(
            hou.BoundingRect(
                mn[0] - pad_x, mn[1] - pad_y,
                mn[0] + sz[0] + pad_x, mn[1] + sz[1] + pad_y,
            ),
            set_center_when_scale_rejected=True,
        )
    except Exception as exc:  # noqa: BLE001 - 取景失败不影响跳转与选中
        logger.warning("frame node %s failed: %s", node, exc)


def jump_to_node(node_path, *, prefix="HouTools", empty_message=None):
    """在网络编辑器中跳转到节点（与 Houdini 自带跳转行为一致）。

    ``prefix`` 只用于状态栏消息前缀（Automation / Video to Sequence 各传
    自己的工具名）；``empty_message`` 覆写路径为空时的提示文案
    （Automation 的原文案是任务语境的"该任务未填写参数路径"，保持原文）。
    目标编辑器优先取最近聚焦的 Houdini 面板（hou.ui.currentPaneTabs），
    聚焦面板不是 NetworkEditor 时退回全局第一个；随后切 pwd、选中目标
    节点并取景居中。
    """
    try:
        import hou
    except ImportError:
        return
    node_path = (node_path or "").strip()
    if not node_path:
        status(empty_message or f"{prefix}: 目标节点路径为空",
               severity="warning")
        return
    node = hou.node(node_path)
    if node is None:
        status(f"{prefix}: 节点不存在 {node_path}", severity="warning")
        return
    editor = next(
        (p for p in hou.ui.currentPaneTabs() if isinstance(p, hou.NetworkEditor)),
        None,
    )
    if editor is None:
        editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
    if editor is None:
        status(f"{prefix}: 未找到网络编辑器", severity="warning")
        return
    if node.parent() != editor.pwd():
        editor.setPwd(node.parent())
    node.setSelected(True, clear_all_selected=True)
    frame_node(editor, node)
