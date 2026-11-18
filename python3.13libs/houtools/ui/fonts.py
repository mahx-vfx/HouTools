"""HouTools 工具窗口角色化字体系统。

角色：``brand``（钉钉进步体，仅 docs/README 品牌资产）、``ui``（Alimama
ShuHeiTi Bold，全部工具面板）、``mono``（Consolas，仅代码编辑器）、``doc``
（Alimama FangYuanTi VF，Recipe 文档）。

⚠ 会话实测（fxhoudini 探针）：Houdini 在应用层给控件类设了自家字体
（SideFX Source Sans Pro），优先级压过 Qt 的父子字体继承——只 setFont
窗口时子控件仍是 SideFX 字体。所以必须逐控件显式 setFont（实测显式
设置能压过应用级类字体），动态创建的子控件（右键菜单、对话框、
Automation 运行时加的任务槽）经应用级 ChildAdded 过滤器补刷：新控件
只要挂在工具窗口子树内，按其最近祖先角色刷字体。

reload 安全：字体族名与过滤器安装标记挂 QApplication 动态属性（模块
重载不丢）；过滤器实例 parent 到 QApplication，随会话存活。
"""

from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from houtools.core.log import get_logger
from houtools.ui import theme

log = get_logger("ui.fonts")

_FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"

# 品牌字：钉钉进步体（随包 TTF）；缺失时回退阿里妈妈数黑体 Bold（旧品牌字，
# 文件保留不删），再回退系统 UI 字。
_BRAND_FONT_FILES = ("DingTalk-JinBuTi.ttf", "AlimamaShuHeiTi-Bold.ttf")
_BRAND_FAMILY_PROP = "_houtools_brand_font_family"
# 面板 UI 字体：沿用品牌统一前的阿里妈妈数黑体 Bold（用户 2026-11 反馈：
# 操作界面以功能性/清晰度为主，面板字体不改）
_UI_FONT_FILES = ("AlimamaShuHeiTi-Bold.ttf",)
_UI_FAMILY_PROP = "_houtools_ui_font_family"
# 文档阅读字体旧文件（方圆体 VF）；新品牌体系优先系统雅黑，文件保留回退
_DOC_FONT_FILES = ("AlimamaFangYuanTiVF-Regular.ttf",)
_SYSTEM_FAMILIES_PROP = "_houtools_system_font_families"
_ROLE_FAMILY_PROPS = {
    "brand": "_houtools_role_family_brand",
    "ui": "_houtools_role_family_ui",
    "mono": "_houtools_role_family_mono",
    "doc": "_houtools_role_family_doc",
}
_ENFORCER_PROP = "_houtools_font_enforcer_installed"
_WINDOW_PROP = "_houtools_tool_window"   # 工具窗口标记（enforcer 判定用）
_ROLE_PROP = "_houtools_font_role"       # 单控件/子树字体角色
_EXEMPT_PROP = "_houtools_font_exempt"   # 子树豁免标记（doc 区不刷工具字体）

ROLE_UI = "ui"
ROLE_BRAND = "brand"
ROLE_MONO = "mono"
ROLE_DOC = "doc"
ROLES = (ROLE_UI, ROLE_BRAND, ROLE_MONO, ROLE_DOC)


# ---------------------------------------------------------------------------
# 字族解析
# ---------------------------------------------------------------------------

def _app():
    return QtWidgets.QApplication.instance()


def _system_families():
    """系统可用字族集合（QFontDatabase.families，缓存挂 app）。"""
    app = _app()
    if app is None:
        return set()
    cached = app.property(_SYSTEM_FAMILIES_PROP)
    if cached is not None:
        return cached
    families = set(QtGui.QFontDatabase.families())
    app.setProperty(_SYSTEM_FAMILIES_PROP, families)
    return families


def _pick_system(candidates):
    """按候选序取第一个系统可用字族；都不可用返回 None。"""
    families = _system_families()
    for name in candidates:
        if name in families:
            return name
    return None


def _load_private_font(files, prop, label):
    """加载私有字体文件，返回公共字族名；缺失/失败返回 ""（缓存哨兵）。"""
    app = _app()
    if app is None:
        return ""
    cached = app.property(prop)
    if cached is not None:
        return cached
    family = None
    for name in files:
        path = _FONTS_DIR / name
        if not path.is_file():
            log.warning("%s font missing: %s", label, path)
            continue
        font_id = QtGui.QFontDatabase.addApplicationFont(str(path))
        if font_id < 0:
            log.warning("%s font load failed: %s", label, path)
            continue
        families = QtGui.QFontDatabase.applicationFontFamilies(font_id)
        if not families:
            log.warning("%s font has no family: %s", label, path)
            continue
        if family is None:
            family = families[0]
            # 单一字族即停：回退文件只在首选缺失时才加载（避免把整套
            # 阿里妈妈数黑体也装进会话）
            break
    app.setProperty(prop, family or "")
    return family or ""


def _family_for_role(role):
    """角色 → 字族名（带缓存；系统字缺失回退 None）。"""
    if role not in theme.FONT_ROLES:
        role = ROLE_UI
    app = _app()
    prop = _ROLE_FAMILY_PROPS.get(role)
    if app is not None and prop is not None:
        cached = app.property(prop)
        if cached is not None:
            return cached or None
    spec = theme.FONT_ROLES[role]
    family = None
    if role == ROLE_BRAND:
        family = _load_private_font(_BRAND_FONT_FILES, _BRAND_FAMILY_PROP,
                                    "brand")
        if not family:
            family = _pick_system(spec["families"][2:]) or ""
    elif role == ROLE_DOC:
        # Recipe 文档区沿用原方圆体 VF；文件缺失才回退系统雅黑
        family = _load_private_font(_DOC_FONT_FILES,
                                    "_houtools_doc_font_family", "doc")
        if not family:
            family = _pick_system(("Microsoft YaHei",
                                   "Microsoft YaHei UI", "Segoe UI")) or ""
    elif role == ROLE_MONO:
        family = _pick_system(spec["families"]) or             (_system_families() and "Courier New") or ""
    else:
        # UI 角色：面板原字体优先，缺失才回退系统字
        family = _load_private_font(_UI_FONT_FILES, _UI_FAMILY_PROP, "ui")
        if not family:
            family = _pick_system(spec["families"][1:]) or ""
    if app is not None and prop is not None:
        app.setProperty(prop, family or "")
    return family or None


def family(role=ROLE_UI):
    """角色字族名（供 QTextCharFormat 等直接用）；不可用时 None。"""
    return _family_for_role(role)


def brand_family():
    return family(ROLE_BRAND)


def ui_family():
    return family(ROLE_UI)


def mono_family():
    return family(ROLE_MONO)


def doc_family():
    return family(ROLE_DOC)


# ---------------------------------------------------------------------------
# 字体对象
# ---------------------------------------------------------------------------

def _make_font(role, base=None):
    """按角色构造 QFont；base 存在时沿用其字号/下划线等属性。"""
    spec = theme.FONT_ROLES.get(role) or theme.FONT_ROLES[ROLE_UI]
    font = QtGui.QFont(base) if base is not None else QtGui.QFont()
    fam = _family_for_role(role)
    if fam:
        font.setFamily(fam)
    if role == ROLE_BRAND:
        font.setWeight(QtGui.QFont.Weight.DemiBold)
    elif role == ROLE_MONO:
        font.setStyleHint(QtGui.QFont.StyleHint.Monospace,
                          QtGui.QFont.StyleStrategy.PreferDefault)
        font.setFixedPitch(True)
        font.setWeight(QtGui.QFont.Weight.Normal)
    # ui/doc 保留控件原有字重：BrandHeader 工具名 500、SectionHeader/
    # EmptyState 标题 600 是显式设置的，不能被 stamping 抹成 400
    return font


def role_font(role=ROLE_UI, base=None):
    """公开：按角色取 QFont（可传基准字体保留字号）。"""
    return _make_font(role if role in ROLES else ROLE_UI, base)


def set_letter_spacing(font, em, pixel_size=None):
    """给字标加字距（英文产品名/工具名 0.04–0.08em）；中文不加。"""
    size = pixel_size or font.pixelSize()
    if size and size > 0:
        font.setLetterSpacing(QtGui.QFont.SpacingType.AbsoluteSpacing,
                              float(em) * size)
    return font


def doc_font(widget):
    """文档区阅读字体：沿用控件字号只换字族，字重钉 Normal。"""
    if doc_family() is None:
        return None
    font = QtGui.QFont(widget.font())
    font.setFamily(doc_family())
    font.setWeight(QtGui.QFont.Weight.Normal)
    return font


# ---------------------------------------------------------------------------
# 角色刷树
# ---------------------------------------------------------------------------

def mark_font_exempt(widget):
    """标记 widget 子树豁免工具字体（doc 区自己管理字体）。

    属性在 __init__ 期设置、先于挂树，enforcer 的 ChildAdded 才能赶在
    刷字体前看到。
    """
    widget.setProperty(_EXEMPT_PROP, True)


def _is_exempt(widget):
    """widget 自身或任一祖先带豁免标记（沿父链查，深度有限成本可忽略）。"""
    w = widget
    while w is not None:
        if w.property(_EXEMPT_PROP):
            return True
        w = w.parent()
    return False


def _effective_role(widget):
    """沿父链找最近的字体角色；无标记回退 UI。"""
    w = widget
    while w is not None:
        role = w.property(_ROLE_PROP)
        if role in ROLES:
            return role
        w = w.parent()
    return ROLE_UI


def _stamp_tree(widget):
    """widget 及其后代显式设字体；每个控件按最近角色取字体。

    显式设置才能压过 Houdini 的应用级类字体；靠继承会被压回 SideFX
    字体。豁免子树整枝跳过。
    """
    if _is_exempt(widget):
        return
    role = _effective_role(widget)
    widget.setFont(_make_font(role, widget.font()))
    for child in widget.children():
        if isinstance(child, QtWidgets.QWidget):
            _stamp_tree(child)


def _tool_root_of(widget):
    """沿父链找标记过的工具窗口根；不在工具窗口子树内返回 None。"""
    w = widget
    while w is not None:
        if w.property(_WINDOW_PROP):
            return w
        w = w.parent()
    return None


class _ToolFontEnforcer(QtCore.QObject):
    """应用级 ChildAdded 监听：新控件挂在工具窗口子树内就整枝刷字体。

    按新控件最近祖先的字体角色选字族（而不是统一窗口字体），品牌区
    动态创建的子控件也能保持品牌字。
    """

    def eventFilter(self, obj, event):
        if event.type() != QtCore.QEvent.ChildAdded:
            return False
        child = event.child()
        if not isinstance(child, QtWidgets.QWidget):
            return False
        if _tool_root_of(obj) is not None:
            _stamp_tree(child)
        return False


def _ensure_enforcer():
    """全局 ChildAdded 过滤器只装一次（标记挂 app，reload 不重复装）。"""
    app = _app()
    if app is None or app.property(_ENFORCER_PROP):
        return
    app.installEventFilter(_ToolFontEnforcer(app))
    app.setProperty(_ENFORCER_PROP, True)


def apply(widget, role=ROLE_UI):
    """把工具字体角色应用到 widget 子树；字体不可用时保持原字体。

    拷贝各控件当前字体只改字族/字重——字号等沿用原设置（应用默认
    字号不变，只换脸）。子树逐控件显式设置 + 全局过滤器兜底后续动态
    控件。
    """
    widget.setProperty(_WINDOW_PROP, True)
    widget.setProperty(_ROLE_PROP, role if role in ROLES else ROLE_UI)
    _stamp_tree(widget)
    _ensure_enforcer()


def apply_role(widget, role):
    """给单个控件/子树指定字体角色（品牌字标、等宽路径等）。"""
    widget.setProperty(_ROLE_PROP, role if role in ROLES else ROLE_UI)
    _stamp_tree(widget)
