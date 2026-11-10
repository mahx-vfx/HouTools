"""Recipe Library 的 Markdown 文档：编辑器、实时预览、媒体播放。

渲染走 QTextBrowser.setMarkdown + _restyle_document 渲染后格式化。
QtWebEngine 方案 2026-11 复核后否决：H21/H22 的 PySide6 都自带模块，
Qt6.8 下创建 QWebEngineView 也不再要求先于 QApplication（旧结论
"必须 initialize 先于 QApplication" 已被探针证伪），但无头环境端到端
加载页面挂死，且 Chromium 进程模型/内存/安全面重做/双版本一致的代价
不成比例——细节见 dev-notes/recipelib.md。能力边界（目检验证）：
标题分级配色/粗体对比/行内代码底色/链接色由 restyle 补齐，围栏代码块
无语法高亮。

媒体的处理（Houdini 22.0.429 GUI 会话实测）：
- 文档流里的 GIF：markdown ![]() 先渲染出静态首帧，随后后处理把该
  字符替换成 QLabel+QMovie 原地动起来（QTextCursor.insertWidget）。
- 文档流里的视频（![x](assets/a.mp4)）：替换成"▶ 文件名"按钮，点击
  弹出 MediaDialog（QMediaPlayer+QVideoWidget）播放。
  ⚠ 必须先 player.setVideoOutput(widget) 再 play，否则 InvalidMedia。
"""

import os
import re
from urllib.parse import unquote

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget

from houtools.core.log import get_logger
from houtools.dev import status
from houtools.recipelib import metadata
from houtools.ui import fonts as tool_fonts
from houtools.ui.dialogs import localize_buttons, warn

log = get_logger("recipelib.docs")

PREVIEW_DEBOUNCE_MS = 300

# 悬浮细滑杆配色（设在滚动条对象自身——拼进视图样式表会整段解析失败，
# 2026-11 实测；轨道透明 + 半透明滑块悬浮在文字右缘）
_OVERLAY_SB_QSS = (
    "QScrollBar:vertical { width: 4px; background: transparent; }"
    "QScrollBar::handle:vertical { background: rgba(140,140,152,0.5); "
    "min-height: 24px; border-radius: 2px; }"
    "QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical "
    "{ height: 0px; width: 0px; }"
    "QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical "
    "{ background: transparent; }")

# ---- 文档排版（setMarkdown 后格式化，2026-11 用户拍板）----
# 配色参考用户 vault 的 OnePage 主题暗色方案（--typo-h1..h6/bold/italic
# 变量原值照搬）；字重一~三级按级别加粗（2026-11 用户要求）：700/600/500
# 恰是 H21 枚举降级档的落点，两版渲染一致；H4-6 与 H3 同重，靠字号/色阶区分
_HEAD_STYLES = {   # level: (pt, 颜色, wght 轴值, 上间距, 下间距)
    1: (15.0, "#4F74AF", 700, 10, 4),
    2: (12.5, "#8567BD", 600, 8, 3),
    3: (11.25, "#4E9A8A", 500, 7, 2),
    4: (10.5, "#C0765E", 500, 6, 2),
    5: (10.0, "#C99B5E", 500, 6, 2),
    6: (9.5, "#8A8478", 500, 6, 2),
}
_BOLD_COLOR = "#E89A60"        # OnePage --typo-bold（加粗橙）
_ITALIC_COLOR = "#A6BA9D"      # OnePage --typo-italic（斜体灰绿）
_LINK_COLOR = "#5eb1ef"        # 链接：导入器默认纯蓝 #0000ff 太扎眼
_CODE_BG = "#2d2d2d"           # 行内代码/围栏块底色
_QUOTE_BG = "#232327"          # OnePage blockquote 面板底（primary-alt 近似）
_QUOTE_BAR = "#5a5a66"         # OnePage blockquote 左竖条（color-base-50 近似）
_LINE_HEIGHT_PCT = 130         # 正文行高（比例式）
_LIST_INDENT_PX = 8            # 列表缩进（Qt 默认 40px/级，空白过大）
_DOC_MARGIN_PX = 10            # 文档页边距（默认 4px 太贴边）
_QUOTE_NEST_PX = 12            # 嵌套引用每级再缩进（一级 0=面板贴内容边）
_BAR_CHARS = "\u2009\u2009"    # 引用左线体：thin space×2 打底色（逐块续成整线）
# markdown 导入器打的块属性（Qt 头文件未公开常量名，PySide6 无属性名，
# 数值经 detect 探针实测）：引用块 int 1 / 围栏代码块 bool True
_MD_PROP_BLOCKQUOTE = 4224
_MD_PROP_CODE_FENCE = 4176
_INLINE_CODE_FAMILY = "monospace"   # 导入器给行内代码/围栏打的字族标记
# 硬换行续行标记：word joiner（Cf，非空白零宽不可见——U+200B 是断行
# 机会点会引起意外折行，弃用）；_soft_to_hard 贴在续行内容开头、导入后
# 随块文本存活（探针实测，**加粗** 解析不受影响），_restyle_document 据
# 此判定续行块并在收尾移除、不进选中复制的文本
_CONT_MARK = "\u2060"
# VF 变量轴精确字重（Qt 6.7+）；旧 Qt 就近取枚举档（H21 能力探测消化）
_HAS_VAR_AXIS = hasattr(QtGui.QFont, "setVariableAxis")


def _apply_heading_weight(f, wght):
    """标题字重：枚举档打底（fontWeight() 报告值两版一致——轴值不回写
    weight）+ Qt 6.7+ 变量轴精确取权重。轴对 wght 渲染恒优先于枚举档
    （实测 dual w600+a650 == dual w700+a650 == axis650）；旧 Qt 无轴 API，
    就用枚举档渲染。"""
    f.setWeight(QtGui.QFont.Weight(
        500 if wght < 540 else 600 if wght < 660 else 700))
    if _HAS_VAR_AXIS:
        f.setVariableAxis(QtGui.QFont.Tag("wght"), float(wght))


class _DocPreview(QtWidgets.QTextBrowser):
    """文档预览：相对路径图片按文档目录解析（markdown 里写 assets/x.png）。

    垂直滚动条为悬浮细滑杆：reparent 到 QTextBrowser 本体（与原生滚动
    条同级的容器子控件，不占布局，文字列宽恒定），4px 宽、可滚动时才
    显示——主面板预览与文档编辑器右栏共用本类，两处自动一致。
    字体为文档阅读字体（方圆体 VF，有完整字重轴），豁免工具统一字体
    刷树（fonts.py 豁免机制）；setMarkdown 后由 _restyle_document 重排
    层级（标题分级配色/粗体变色/列表缩进等）。
    """

    middleClick = QtCore.Signal(QtCore.QPoint)   # 全局坐标（鼠标按下点）

    def __init__(self, doc_dir, parent=None):
        super().__init__(parent)
        self._doc_dir = doc_dir
        self.setOpenLinks(False)
        self.anchorClicked.connect(self._on_anchor)
        # 吞掉 QTextBrowser 自带的英文标准右键菜单（Copy/Copy Link
        # Location/Select All，未选中文字时大半灰置徒增困惑）：
        # CustomContextMenu 策略只发信号、不弹默认菜单——主面板接收信号
        # 自建「编辑文档」菜单，无人接收（文档编辑器预览栏）= 右键不弹
        self.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        # ---- 文档阅读字体 ----
        # 豁免标记必须在挂树前设置（enforcer 的 ChildAdded 才能赶在刷
        # 工具字体前看到）；方圆体缺失则保持原字体（graceful 降级）
        tool_fonts.mark_font_exempt(self)
        doc_font = tool_fonts.doc_font(self)
        if doc_font is not None:
            self.setFont(doc_font)
        # ---- 悬浮细滑杆 ----
        # 策略常关 + 滚动条对象挂到本控件（与原生滚动条同级的容器子控件）：
        # QAbstractScrollArea 只给自挂的滚动条留布局宽度，policy 常关后
        # viewport 占满全宽（滑杆压在文字上）。滚轮/键盘滚动不受策略影响，
        # 拖拽走这条 4px 窄条。
        # ★ 必须挂容器而不能挂 viewport：QTextBrowser 滚动内容走
        # QWidget::scroll() 位块搬移（文档明说 including its children），
        # viewport 的子控件每滚一像素就被物理搬走一像素——滑杆会跟着
        # 内容反向漂移、反复滚动糊成整条残影（2026-11 实测踩坑）。
        sb = self.verticalScrollBar()
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        sb.setParent(self)
        sb.setFixedWidth(4)
        sb.setStyleSheet(_OVERLAY_SB_QSS)
        sb.raise_()   # 与 viewport 同为容器子控件，压到 viewport 之上
        sb.show()
        sb.rangeChanged.connect(self._sync_overlay_sb_range)
        # show() 已置可见，而 (0,0)→(0,0) 不发 rangeChanged——按当前范围
        # 初始化一次显隐，否则空态/短文档也挂着一条滑杆
        self._sync_overlay_sb_range(sb.minimum(), sb.maximum())
        # viewport 尺寸变化（面板拖宽/横滚条出现）时重贴右缘、随高度
        self.viewport().installEventFilter(self)
        self._sync_overlay_sb_geometry()

    def _sync_overlay_sb_geometry(self):
        """悬浮滑杆贴 viewport 右缘（容器坐标 = viewport 位置 + frame
        偏移）、高度随 viewport（4px 宽已定死）。"""
        vp = self.viewport()
        sb = self.verticalScrollBar()
        anchor = vp.mapTo(self, QtCore.QPoint(0, 0))
        sb.setFixedHeight(max(1, vp.height()))
        sb.move(anchor.x() + max(0, vp.width() - sb.width()), anchor.y())

    def _sync_overlay_sb_range(self, minimum, maximum):
        """内容可滚才显示滑杆（对齐原生 AsNeeded 语义：短文档不见条）。"""
        self.verticalScrollBar().setVisible(maximum > minimum)

    def eventFilter(self, watched, event):
        if watched is self.viewport():
            if event.type() == QtCore.QEvent.Resize:
                self._sync_overlay_sb_geometry()
            elif (event.type() == QtCore.QEvent.MouseButtonPress
                    and event.button() == QtCore.Qt.MiddleButton):
                # 中键必须挂在 viewport 的过滤器上：QTextEdit 系控件的
                # viewport 鼠标事件被 QWidgetTextControl 直接 processEvent
                # 吞掉，不会进子类 mousePressEvent（offscreen 直调探针
                # 绕过真实路由时发现不了）。不吞事件，交回原处理无害
                self.middleClick.emit(event.globalPosition().toPoint())
        return super().eventFilter(watched, event)

    def set_doc_dir(self, doc_dir):
        self._doc_dir = doc_dir

    def _restyle_document(self):
        """setMarkdown 后重排层级（2026-11 用户拍板）：标题分级配色字重、
        加粗/斜体变色、行内代码底色、链接强调色、引用块面板化、列表缩进、
        行高。标题不放任何装饰条（字形/底色条/图片条三轮方案全部被否）。

        导入器只打格式标记（headingLevel/字重 700/monospace 字族/anchor），
        这里读标记回填观感。两趟走，趟间不允许复用块句柄——
        ★ QTextBlock 句柄在文档编辑（setBlockFormat 也算）后失效，
        拿旧句柄 next()/读格式会在旧树上错位寻址，把 A 块的格式拷给 B 块
        （实测 hl 串块）；所以第一趟纯读、第二趟按块号 findBlockByNumber
        新鲜寻址纯写（格式编辑不挪字符位置，第一趟记的片段位置全程有效）。
        引用左线（thin space 底色）是文本插入，放最后垫底。"""
        doc = self.document()
        doc.setIndentWidth(_LIST_INDENT_PX)
        doc.setDocumentMargin(_DOC_MARGIN_PX)
        family = tool_fonts.doc_family()   # 标题沿用阅读字族的重字重
        lh_type = QtGui.QTextBlockFormat.LineHeightTypes.\
            ProportionalHeight.value
        # ---- 第一趟：纯读 ----
        # cont = 块文本带 _CONT_MARK（_soft_to_hard 打的硬换行续行标记）
        blocks = []   # (block_number, blockFormat, frags, is_cont)
        block = doc.firstBlock()
        while block.isValid():
            frags = []
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    frags.append((frag.position(), frag.length(),
                                  frag.charFormat()))
                it += 1
            # 标记贴在前缀链后，容 4 字符缩进/引用前缀的偏差
            cont = 0 <= block.text().find(_CONT_MARK) <= 4
            blocks.append((block.blockNumber(), block.blockFormat(),
                           frags, cont))
            block = block.next()
        # ---- 第二趟：纯写（新鲜寻址）----
        cont_by_num = {number: cont for number, _bf, _frags, cont in blocks}
        for number, bf, frags, cont in blocks:
            level = bf.headingLevel()
            # 引用标记存 int 1、围栏标记存 bool True——boolProperty/
            # intProperty 各只能读对一种（类型不匹配静默返 0），统一
            # property() 真值判断
            is_quote = bool(bf.property(_MD_PROP_BLOCKQUOTE))
            is_fence = bool(bf.property(_MD_PROP_CODE_FENCE))
            # 全量拷属性（heading 级别/列表 objectIndex/既有 margin 都在
            # 里面，丢哪样塌哪样），再叠加行高与分类修饰
            nbf = QtGui.QTextBlockFormat()
            for key, value in bf.properties().items():
                nbf.setProperty(key, value)
            if not is_quote:
                nbf.setLineHeight(_LINE_HEIGHT_PCT, lh_type)
            if level:
                _pt, _color, _wght, top_m, bottom_m = _HEAD_STYLES[level]
                nbf.setTopMargin(top_m)
                nbf.setBottomMargin(bottom_m)
            elif is_quote:
                # 导入器给引用块 40px 左 margin。左缩进 0=面板贴内容边缘
                # （Obsidian 同款，2026-11 用户反馈 8px 前方空白多）；嵌套
                # 引用按深度递进缩进（导入器 40/80 全被覆平的问题一并修）
                depth = int(bf.property(_MD_PROP_BLOCKQUOTE) or 0)
                nbf.setLeftMargin(_QUOTE_NEST_PX * max(0, depth - 1))
                nbf.setRightMargin(0)
            elif is_fence:
                nbf.setBackground(QtGui.QColor(_CODE_BG))
            if not level:
                # 硬换行续行块与上一块无缝拼合（Obsidian 段内换行不分段）；
                # 下一块是续行时本块底边距一并归零。块背景不覆盖块边距与
                # 比例行高 leading（实测各漏 5-12px 页面底色），引用面板
                # 不归零就断成一条条色带；空行分隔的段落不在此列，保留
                # 导入器 6/6 边距 = 分段间隙（=Obsidian 相邻引用独立元素）
                if cont:
                    nbf.setTopMargin(0)
                if cont_by_num.get(number + 1):
                    nbf.setBottomMargin(0)
            if is_quote:
                # OnePage：引用块 = 微亮面板底（引用内的标题也垫底色）。
                # 行高保持自然（不套全局 130%）：比例行高的 leading 不被
                # 块背景覆盖，块尾会漏 5px 页面底色、多行引用断成一条条
                # 色带（LH100/FixedHeight=自然高实测无缝，探针固化在
                # dev-notes）；引用面板靠底色+竖条成立，行距紧一点可接受
                nbf.setBackground(QtGui.QColor(_QUOTE_BG))
            blk = doc.findBlockByNumber(number)
            sel = QtGui.QTextCursor(blk)
            sel.setPosition(blk.position())
            # 末块 length 含文档尾分隔符，position+length == characterCount
            # 而 setPosition 有效上界是 characterCount-1——钳制防告警
            sel.setPosition(
                min(blk.position() + blk.length(),
                    doc.characterCount() - 1),
                QtGui.QTextCursor.KeepAnchor)
            sel.setBlockFormat(nbf)
            if level:
                pt, color, wght, _tm, _bm = _HEAD_STYLES[level]
                cf = QtGui.QTextCharFormat()
                f = QtGui.QFont(self.font())
                if family:
                    f.setFamily(family)
                f.setPointSizeF(pt)
                _apply_heading_weight(f, wght)
                cf.setFont(f)
                cf.setForeground(QtGui.QColor(color))
                sel.setCharFormat(cf)
            else:
                for pos, length, old_cf in frags:
                    fmt = self._restyle_fragment_format(old_cf, is_fence)
                    if fmt is None:
                        continue
                    cur = QtGui.QTextCursor(doc)
                    cur.setPosition(pos)
                    cur.setPosition(pos + length,
                                    QtGui.QTextCursor.KeepAnchor)
                    cur.setCharFormat(fmt)
        # ---- 第三趟：移除续行标记 + 引用左线垫底（块内插入不改块号）----
        # 标记已完成使命（第二趟边距判定），先逐块移除再插竖条，块号都
        # 不受字符删除/插入影响。条体 = thin space×2 打底色 + 普通空格做
        # 间隔，无字形依赖。标题竖条三轮方案（▍字形/thin space 底色条/
        # 图片条）全部被否，2026-11 用户拍板：标题不放任何装饰条，只留
        # 配色/字阶/字重。
        for number, _bf, _frags, _cont in blocks:
            blk = doc.findBlockByNumber(number)
            idx = blk.text().find(_CONT_MARK)
            if 0 <= idx <= 4:
                cur = QtGui.QTextCursor(blk)
                cur.setPosition(blk.position() + idx)
                cur.setPosition(blk.position() + idx + len(_CONT_MARK),
                                QtGui.QTextCursor.KeepAnchor)
                cur.removeSelectedText()
        for number, bf, _frags, _cont in blocks:
            if not bool(bf.property(_MD_PROP_BLOCKQUOTE)):
                continue
            bar_cf = QtGui.QTextCharFormat()
            bar_cf.setForeground(QtGui.QColor(_QUOTE_BAR))
            bar_cf.setBackground(QtGui.QColor(_QUOTE_BAR))
            cur = QtGui.QTextCursor(doc.findBlockByNumber(number))
            cur.insertText(_BAR_CHARS, bar_cf)
            cur.insertText(" ", QtGui.QTextCharFormat())

    def _restyle_fragment_format(self, cf, is_fence):
        """片段级配色（优先级：链接 > 行内代码 > 粗体 > 斜体；引用文字
        用正常色——OnePage 引用块靠面板底色区分，不做灰斜）；无需改动
        返回 None。只读不写，供第二趟按位置套用。"""
        fmt = None
        fams = cf.fontFamilies()
        is_code = (fams is not None
                   and list(fams) == [_INLINE_CODE_FAMILY]
                   and not is_fence)
        if cf.isAnchor():
            fmt = QtGui.QTextCharFormat(cf)
            fmt.setForeground(QtGui.QColor(_LINK_COLOR))
        elif is_code:
            fmt = QtGui.QTextCharFormat(cf)
            fmt.setBackground(QtGui.QColor(_CODE_BG))
        elif cf.fontWeight() >= int(QtGui.QFont.Weight.Bold):
            fmt = QtGui.QTextCharFormat(cf)
            fmt.setForeground(QtGui.QColor(_BOLD_COLOR))
        elif cf.fontItalic():
            fmt = QtGui.QTextCharFormat(cf)
            fmt.setForeground(QtGui.QColor(_ITALIC_COLOR))
        return fmt

    def _resolve(self, url):
        """链接/资源 URL → (绝对路径, 是否允许)。

        文档内容可能来自导入的分享包（不可信第三方）：解析出的路径必须
        收敛在文档目录之内——``../`` 穿越、盘符绝对路径、file:// 指向
        别处一律不允许，防穿越读取本地文件与诱导打开目录外的内容。
        """
        path = unquote(url.toString())
        if path.startswith("file:///"):
            path = QtCore.QUrl(path).toLocalFile()
        if not os.path.isabs(path):
            path = os.path.join(self._doc_dir, path)
        path = os.path.normpath(path)
        try:
            root = os.path.normcase(os.path.realpath(self._doc_dir)) + os.sep
            allowed = os.path.normcase(
                os.path.realpath(path)).startswith(root)
        except OSError:
            allowed = False
        return path, allowed

    def loadResource(self, rtype, url):
        if rtype == QtGui.QTextDocument.ImageResource:
            # 失败关闭：目录外/文件缺失/解码失败一律返回空图，**绝不
            # 回退基类**——基类对绝对 file:/// URL 会按原样读文件，目录
            # 校验就形同虚设（离屏探针实证非空返回）。空 QImage 在文档
            # 里渲染为无图，第三方文档不再能诱导读取目录外本地图片
            path, allowed = self._resolve(url)
            if allowed and os.path.exists(path):
                img = QtGui.QImage(path)
                if not img.isNull():
                    return img
            return QtGui.QImage()
        return super().loadResource(rtype, url)

    def _on_anchor(self, url):
        scheme = url.scheme().lower()
        if scheme in ("http", "https", "mailto"):
            QtGui.QDesktopServices.openUrl(url)
            return
        if scheme not in ("", "file"):
            return   # 可拉起任意关联程序/系统组件的 scheme 一律忽略
        path, allowed = self._resolve(url)
        ext = os.path.splitext(path)[1].lower()
        # 本地链接只放行"文档目录内 + 媒体白名单内"的文件：.bat/.exe/
        # .lnk 交给默认关联程序即等于执行（导入分享包 → 点链接 → 执行
        # 是完整攻击链）；目录外或非媒体一律拒绝并提示
        if (not allowed
                or ext not in metadata.IMAGE_EXTS + metadata.VIDEO_EXTS):
            status("Recipe Library: 文档链接已拒绝"
                   "（仅允许文档目录内的图片/视频）", severity="warning")
            return
        if os.path.exists(path) and (ext in metadata.VIDEO_EXTS
                                     or ext == ".gif"):
            MediaDialog.open_media(self, path)
            return
        if os.path.exists(path):
            # 打开解析后的绝对路径而非原始 url：相对链接经 QDesktopServices
            # 会按进程 CWD 解析，等于丢弃上面 _resolve 的校验结果（校验了
            # A 打开 B）；图片分支与上方 GIF/视频分支的口径对齐
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))


# GIF 播放期全帧解码预算：QMovie CacheAll 按"全帧 × 原始分辨率"缓存
# 解码内存（800x600x1000 帧 ≈ 1.9GB）——导入侧 50MP 只约束单帧、管不住
# 帧数，恶意/异常包可借此打爆 Houdini 进程。与 transfer.py 的导入期
# 全帧预算（MAX_GIF_TOTAL_PIXELS）同一数值：超限动画降级为静态首帧，
# 不创建 QMovie。
GIF_MOVIE_MAX_TOTAL_PIXELS = 50 * 1000 * 1000


def _gif_over_budget(path):
    """GIF 全帧累计解码像素是否超预算（CacheAll 会缓存全部原始帧）。

    读失败/帧数未知（imageCount 返回 -1）一律视为未超——不因探测失败
    拒播正常动画；真正的超大帧仍被单帧 50MP 导入上限拦住。"""
    try:
        reader = QtGui.QImageReader(str(path))
        size = reader.size()
        count = reader.imageCount()
        return bool(size.isValid() and count > 0
                    and size.width() * size.height() * count
                    > GIF_MOVIE_MAX_TOTAL_PIXELS)
    except Exception:
        return False


class _MovieLabel(QtWidgets.QLabel):
    """GIF 播放标签（MediaDialog 用，QMovie 循环播放）。

    ⚠ 不用 QMovie.setScaledSize：GIF 解码器不支持 QImageReader 缩放，
    帧按原始尺寸交付、缩放后显示不变（大 GIF 会按原分辨率溢出
    MediaDialog，且 CacheAll 让全尺寸帧常驻内存）——本仓 browser.py 的
    大图查看器已实测并改走 frameChanged 手动缩放，此处同一手法。
    """

    def __init__(self, path, max_w, parent=None):
        super().__init__(parent)
        self._max_w = max_w
        if _gif_over_budget(path):
            # 超预算动画降级为静态首帧：CacheAll 全帧缓存的解码内存
            # 随帧数无上限增长（导入单帧上限管不住帧数），不创建 QMovie
            self.setPixmap(QtGui.QPixmap.fromImage(
                QtGui.QImageReader(str(path)).read()))
            return
        movie = QtGui.QMovie(self)
        movie.setFileName(path)
        movie.setCacheMode(QtGui.QMovie.CacheAll)
        movie.jumpToFrame(0)
        self._movie = movie
        pm = movie.currentPixmap()
        if not pm.isNull() and pm.width() > max_w:
            # 只记宽度上限，逐帧手动缩（frameChanged 交付，不 setMovie——
            # setMovie 会绕过缩放按原始尺寸交付）
            movie.frameChanged.connect(self._show_scaled_frame)
            self._show_scaled_frame()
        else:
            self.setMovie(movie)
        movie.start()

    def _show_scaled_frame(self):
        """把 movie 当前帧手动缩放后显示（替代 setScaledSize，见类注释）。"""
        pm = self._movie.currentPixmap()
        if pm.isNull():
            return
        if pm.width() > self._max_w:
            h = max(1, int(round(pm.height() * self._max_w / pm.width())))
            pm = pm.scaled(self._max_w, h, QtCore.Qt.KeepAspectRatio,
                           QtCore.Qt.SmoothTransformation)
        self.setPixmap(pm)


class MarkdownMediaView(_DocPreview):
    """可渲染 markdown + 内联媒体的预览视图（文档编辑器右栏与主面板共用）。

    - GIF：QMovie 逐帧同步进文档资源（loadResource 返回当前帧 +
      frameChanged 时 addResource + viewport 刷新），真正内联动画；
      超宽的帧按视口宽度等比缩小
    - 图片尺寸参数（2026-11 用户要求）：![alt|300](path)、![alt|300x200]
      （Obsidian 式）与 ![alt](path =300x)、![alt](path =x150)（Typora
      式），setMarkdown 前剥除尺寸、导入后写进 QTextImageFormat（实测
      其宽高确定性控制布局绘制尺寸）；GIF 帧按注册宽度缩放
    - <br>：无斜杠形式会被 Qt 的 markdown 导入吞掉段落剩余内容（实测），
      统一重写为 <br/>（段内软换行 U+2028）
    - 视频：set_markdown_with_media 前把 ![](x.mp4) 预处理成普通链接
      （QTextCursor 无 insertWidget，Qt 富文本不支持内嵌控件），点击
      链接由 _DocPreview._on_anchor 弹播放器
    """

    _VIDEO_IMAGE_RE = re.compile(
        r"!\[([^\]]*)\]\(([^)]+\.(?:mp4|mov|avi|webm|mkv))\)", re.IGNORECASE)
    # 图片尺寸参数（2026-11 用户要求）：Obsidian 式 alt 尺寸 + Typora 式
    # URL 后缀。md4c 对 URL 空格零容忍（![t](x.png =250x) 整段变字面文本）、
    # 对无空格的 y.png=250x 会把后缀并进 URL（砸 _resolve 与 .gif 判定）、
    # 内联 <img> 整体丢弃——所以两种写法都必须在 setMarkdown 前剥除。
    # 两种写法合并为单正则单趟 sub：同 URL 多次注册按文档顺序后写覆盖
    # 先写（分趟会打乱跨语法的文档顺序）
    _IMAGE_SIZE_RE = re.compile(
        r"!\[([^\]]*)\]\(([^)\s]+?)\s+=((?:\d+(?:x\d*)?|x\d+))\)"   # Typora
        r"|"
        r"!\[([^|\]]*)\|(\d+)(?:x(\d+))?\]\(([^)\s]+)\)"            # Obsidian
    )
    # <br>（无斜杠）在 Qt 的 markdown 导入里会吞掉段落剩余内容（实测
    # 'A<br>B' → 'A'），<br/> 才是段内软换行（U+2028）——统一重写
    _BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
    # 软换行升级硬换行（2026-11 用户拍板以 Obsidian 体验为准）：Qt 导入
    # 器把段内软换行折算成空格，「> test」的下一行/普通段落的下一行都
    # 会并进同一行渲染；Obsidian 默认（非严格换行）逐行显示。扫描给上
    # 一行补两个尾空格（硬换行标记），导入器即按行拆块；续行内容开头
    # 贴 word joiner 标记（_CONT_MARK），restyle 据此把续行块与上一块
    # 边距归零无缝拼合（=Obsidian 段内换行），空行分隔的段落保留导入器
    # 6/6 边距=分段间隙（=Obsidian 相邻引用是带间距的独立元素），标记
    # 在 restyle 收尾移除。围栏内是代码文本，跳过（尾空格会进代码内
    # 容，奇偶判定允许 > 前缀、与 _DocEditor 回车延续同口径）
    _FENCE_LINE_RE = re.compile(r"^\s*(?:>\s*)*(```|~~~)")
    # 引用前缀链（含嵌套 > > 与无空格 >b）；* 量词兼容无前缀的普通段落
    _PREFIX_CHAIN_RE = re.compile(r"^\s*(?:>\s*)*")
    # 块级标记行：md4c 对它们自然开新块、不参与软换行续行，且标记前贴
    # 不可见字符会破坏其解析（探针实测 '\u2060- item' 不再是列表）——
    # 一律跳过不转换。含集合：无序/有序列表、ATX 标题、围栏、置中线
    # （---/=== setext 下划线与 ***/hr，2+ 即拦）；| 打头的表格行同理
    _BLOCK_MARKER_RE = re.compile(
        r"(?:[-+*] |\d{1,9}[.)] |#{1,6}(?: |$)|`{3}|~{3}"
        r"|\*{3}(?: |$)|_{3}(?: |$)|-{2,}|={2,}|\|)")
    # ATX 标题（上一行侧单独用：标题行后是新段落、不算软换行续行）
    _HEADING_RE = re.compile(r"#{1,6}(?: |$)")
    _HARDBREAK_END_RE = re.compile(r"(?:  |\\)$")

    def __init__(self, doc_dir, parent=None):
        super().__init__(doc_dir, parent)
        self._movies = {}   # 文档里的 url 字符串 -> QMovie
        self._image_sizes = {}   # url 字符串 -> (w, h)，参数化尺寸注册表

    def set_markdown_with_media(self, text):
        self.stop_media()
        text = self._VIDEO_IMAGE_RE.sub(r"[▶ \1](\2)", text)
        text, self._image_sizes = self._extract_image_sizes(text)
        text = self._BR_RE.sub("<br/>", text)
        text = self._soft_to_hard(text)
        self.setMarkdown(text)
        self._restyle_document()
        self._apply_image_sizes()
        # QMovie 在布局首次查询图片资源时经 loadResource 惰性创建并启动

    @classmethod
    def _soft_to_hard(cls, text):
        """段内软换行 → 硬换行 + 续行标记，让源码逐行渲染成独立行。

        见 _FENCE_LINE_RE 处注释。上一行已以两个空格或 \\ 结尾视为硬换
        行已在，只补标记不重复加空格；下一行为空行/空引用行/块级标记行
        （列表/标题/围栏/表格行）时不动——段落本就断开或 md4c 自然开新
        块。标记只贴在"标记行检查通过"的续行上，块级标记行永不受污染。"""
        lines = text.split("\n")
        in_fence = False
        # ★ 按下标取行而非 enumerate 快照：本轮给 lines[i+1] 插的标记
        # 在下一轮它成为"上一行"时必须可见（快照会拿旧值覆写丢标记）
        for i in range(len(lines) - 1):
            line = lines[i]
            if cls._FENCE_LINE_RE.match(line):
                in_fence = not in_fence
                continue
            if in_fence or not line.strip():
                continue
            # 上一行侧：空引用行（> / > 空白）= 段落断开，不参与续行
            # （「> a\n>\n> b」的 b 须保留分段间隙）；ATX 标题行后是
            # 新段落而非软换行续行（尾空格被 ATX 剥掉、硬换行不成立），
            # 也不标——标了会让正文 T0 贴死标题（Obsidian 有段距）
            prev_m = cls._PREFIX_CHAIN_RE.match(line)
            prev_content = line[prev_m.end():]
            if (not prev_content.strip()
                    or cls._HEADING_RE.match(prev_content)):
                continue
            m = cls._PREFIX_CHAIN_RE.match(lines[i + 1])
            if m is None:
                continue
            content = lines[i + 1][m.end():]
            if not content.strip() or cls._BLOCK_MARKER_RE.match(content):
                continue
            if not cls._HARDBREAK_END_RE.search(line):
                lines[i] = line.rstrip() + "  "
            lines[i + 1] = lines[i + 1][:m.end()] + _CONT_MARK + content
        return "\n".join(lines)

    def _extract_image_sizes(self, text):
        """抽取并剥除图片尺寸参数，返回 (清洗后的 markdown, {url: (w, h)})。

        - ![alt|300](path) / ![alt|300x200](path)：尺寸在 alt 文本里，
          URL 天然干净
        - ![alt](path =300x) / ![alt](path =x150)：Typora 式，必须在导入
          前剥除（URL 空格让 md4c 拒绝整段、无空格变体污染 URL）
        只给一边的尺寸按源图比例补全（QImageReader 只探尺寸不解码）；
        同 URL 多次注册按文档顺序后写覆盖先写（文档级注册表按 URL 键）。
        """
        sizes = {}

        def _record(url, w, h):
            size = self._resolve_size(url, w, h)
            if size is not None:
                sizes[url] = size

        def _sub(m):
            if m.group(3) is not None:   # Typora：![alt](url =spec)，组1=alt
                url, spec = m.group(2), m.group(3).lower()
                if spec.startswith("x"):
                    _record(url, None, int(spec[1:]))
                elif "x" in spec:
                    a, b = spec.split("x", 1)
                    _record(url, int(a) if a else None,
                            int(b) if b else None)
                else:
                    _record(url, int(spec), None)
                return "![%s](%s)" % (m.group(1), url)
            # Obsidian：![alt|WxH](url)
            url = m.group(7)
            _record(url, int(m.group(5)),
                    int(m.group(6)) if m.group(6) else None)
            return "![%s](%s)" % (m.group(4), url)

        text = self._IMAGE_SIZE_RE.sub(_sub, text)
        return text, sizes

    def _resolve_size(self, url_str, w, h):
        """补全单边尺寸：缺的一边按源图比例（QImageReader 只探尺寸不解
        码整图）。两边都无或源图不可读返回 None（该图维持原生尺寸）。

        路径解析复用 _resolve 的收敛校验：文档内容可能来自导入的分享包
        （不可信第三方），尺寸探测也不能读文档目录外的文件——不过收敛
        是防线上唯一曾被绕开的口（loadResource/_on_anchor 都走 _resolve）。
        """
        if w and h:
            return int(w), int(h)
        if not w and not h:
            return None
        path, allowed = self._resolve(QtCore.QUrl(url_str))
        if not allowed:
            return None
        try:
            sz = QtGui.QImageReader(path).size()
        except Exception as exc:
            log.debug("probe image size failed %s: %s", path, exc)
            return None
        if not sz.isValid() or sz.width() <= 0 or sz.height() <= 0:
            return None
        if w:
            return int(w), max(1, int(round(sz.height() * w / sz.width())))
        return max(1, int(round(sz.width() * h / sz.height()))), int(h)

    def _lookup_size(self, url_str):
        """按 fragment 的图片 URL 查注册尺寸；md4c 对非 ASCII 路径可能
        百分号编码，raw 未命中再试 unquote。"""
        sizes = self._image_sizes
        if not sizes:
            return None
        size = sizes.get(url_str)
        if size is None:
            size = sizes.get(unquote(url_str))
        return size

    def _apply_image_sizes(self):
        """setMarkdown 后把注册尺寸写进图片 fragment 的
        QTextImageFormat（实测该宽高确定性地控制布局绘制尺寸）。"""
        if not self._image_sizes:
            return
        doc = self.document()
        blk = doc.firstBlock()
        while blk.isValid():
            it = blk.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                if fmt.isImageFormat():
                    # PySide6 的 charFormat() 返回基类包装，name() 在
                    # QTextImageFormat 上——isImageFormat 后显式转型
                    size = self._lookup_size(
                        QtGui.QTextImageFormat(fmt).name())
                    if size is not None:
                        cur = QtGui.QTextCursor(blk)
                        cur.setPosition(frag.position())
                        cur.setPosition(
                            frag.position() + frag.length(),
                            QtGui.QTextCursor.KeepAnchor)
                        imf = QtGui.QTextImageFormat(fmt)
                        imf.setWidth(size[0])
                        imf.setHeight(size[1])
                        cur.setCharFormat(imf)
                it += 1
            blk = blk.next()

    def stop_media(self):
        for movie in self._movies.values():
            try:
                movie.stop()
                # movie 挂在本视图下：不显式释放则 C++ 对象滞留，
                # CacheAll 的全帧缓存随之滞留（loadResource 里创建的
                # movie 都登记在 _movies，在这里统一释放）
                movie.deleteLater()
            except RuntimeError as exc:
                log.debug("stop doc movie failed (deleted C++ obj): %s", exc)
        self._movies.clear()

    def _on_movie_frame(self, url_str):
        movie = self._movies.get(url_str)
        if movie is not None:
            self.document().addResource(
                QtGui.QTextDocument.ImageResource, QtCore.QUrl(url_str),
                self._movie_frame_pixmap(movie, url_str))
            self.viewport().update()

    def _movie_frame_pixmap(self, movie, url_str):
        """当前帧 pixmap：注册了参数化尺寸的按注册宽度等比缩放（参数
        优先于视口压宽），否则沿用"超视口宽等比缩小"。"""
        pm = movie.currentPixmap()
        if pm.isNull():
            return pm
        size = self._lookup_size(url_str)
        if size is not None:
            return pm.scaledToWidth(
                max(1, size[0]), QtCore.Qt.SmoothTransformation)
        max_w = max(120, self.viewport().width() - 12)
        if pm.width() > max_w:
            pm = pm.scaledToWidth(max_w, QtCore.Qt.SmoothTransformation)
        return pm

    def loadResource(self, rtype, url):
        if rtype == QtGui.QTextDocument.ImageResource:
            url_str = url.toString()
            if url_str.lower().split("?", 1)[0].endswith(".gif"):
                movie = self._movies.get(url_str)
                if movie is None:
                    path, allowed = self._resolve(url)
                    if allowed and os.path.exists(path) \
                            and not _gif_over_budget(path):
                        # 超预算 GIF 不创建 QMovie（CacheAll 全帧缓存可
                        # 达 GB 级）：落回下方 super() = 父类 loadResource
                        # 的放行分支，读静态首帧渲染
                        movie = QtGui.QMovie(self)
                        movie.setFileName(path)
                        movie.setCacheMode(QtGui.QMovie.CacheAll)
                        movie.frameChanged.connect(
                            lambda _i, u=url_str: self._on_movie_frame(u))
                        self._movies[url_str] = movie
                        movie.start()
                if movie is not None:
                    return self._movie_frame_pixmap(movie, url_str)
        return super().loadResource(rtype, url)


# 文档详情面板单例登记：同时刻只存在一个面板，open_at 先关旧的——
# 模块级字典 + NameError 守卫活过 Reload（同 MediaDialog 手法）
try:
    _DOC_INFO_STATE
except NameError:
    _DOC_INFO_STATE = {"current": None}


_PIN_SVG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "icons", "pin.svg")
_PIN_SVG_FILL = "#e6e6e6"      # pin.svg 的原始 fill，着色时整体替换
_PIN_COLOR_IDLE = "#555555"    # 未钉住：同 ✕/□ 的灰
_PIN_COLOR_PINNED = "#e0533a"  # 钉住：橙红（取 📌 emoji 的观感色）
_PIN_ICON_CACHE: dict = {}     # 纯渲染缓存，Reload 后重建（ui/icons 同注释）


def _pin_icon(filled):
    """钉住按钮图标：icons/pin.svg（斜置图钉，📌 emoji 同款造型）按状态
    整体替换 fill 渲染——未钉住灰、钉住橙红（2026-11 用户指定）。

    不能用 📌 emoji 字符：彩色 emoji 由字体（Segoe UI Emoji）渲染，
    QSS 的 color 属性管不到它——checked 切换样式等于没切。
    """
    cached = _PIN_ICON_CACHE.get(filled)
    if cached is not None:
        return cached
    icon = None
    try:
        from PySide6 import QtSvg
        with open(_PIN_SVG_PATH, "r", encoding="utf-8") as f:
            svg = f.read()
        color = _PIN_COLOR_PINNED if filled else _PIN_COLOR_IDLE
        renderer = QtSvg.QSvgRenderer(QtCore.QByteArray(
            svg.replace(_PIN_SVG_FILL, color).encode("utf-8")))
        if renderer.isValid():
            icon = QtGui.QIcon()
            for dpr in (1.0, 2.0):   # 双档位图，Qt 按屏幕 DPR 取用（svg_icon 同套路）
                pm = QtGui.QPixmap(int(14 * dpr), int(14 * dpr))
                pm.setDevicePixelRatio(dpr)
                pm.fill(QtCore.Qt.transparent)
                p = QtGui.QPainter(pm)
                p.setRenderHint(QtGui.QPainter.Antialiasing, True)
                renderer.render(p, QtCore.QRectF(0, 0, 14, 14))
                p.end()
                icon.addPixmap(pm)
    except Exception as exc:
        log.debug("pin svg render failed (filled=%s): %s", filled, exc)
        icon = None
    if icon is None:
        icon = _pin_icon_fallback(filled)   # QtSvg 缺失/素材损坏：手绘兜底
    _PIN_ICON_CACHE[filled] = icon
    return icon


def _pin_icon_fallback(filled):
    """_pin_icon 的手绘兜底（ui/sidebar 线稿同款手法，QtSvg 缺失时用）。"""
    color = _PIN_COLOR_PINNED if filled else _PIN_COLOR_IDLE
    icon = QtGui.QIcon()
    for dpr in (1.0, 2.0):   # 双档位图，Qt 按屏幕 DPR 取用（svg_icon 同套路）
        pm = QtGui.QPixmap(int(14 * dpr), int(14 * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(QtCore.Qt.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.Antialiasing, True)
        pen = QtGui.QPen(QtGui.QColor(color), 1.4)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(QtGui.QColor(color) if filled else QtCore.Qt.NoBrush)
        p.drawRoundedRect(QtCore.QRectF(5.2, 1.6, 3.6, 2.8), 1.4, 1.4)  # 圆帽
        body = QtGui.QPainterPath()   # 锥形针体：宽肩收窄
        body.moveTo(4.0, 4.4)
        body.lineTo(10.0, 4.4)
        body.lineTo(7.7, 8.4)
        body.lineTo(6.3, 8.4)
        body.closeSubpath()
        p.drawPath(body)
        p.drawLine(QtCore.QPointF(7.0, 8.4), QtCore.QPointF(7.0, 12.6))  # 针尖
        p.end()
        icon.addPixmap(pm)
    return icon


class DocInfoPopup(QtWidgets.QFrame):
    """文档详情面板（Node Info 风格）：中键点击预览区弹出完整渲染文档。

    无边框工具窗口 + 自绘标题条：拖拽移动、最大化/还原、钉住、✕ 关闭。
    未钉住时点击面板外或应用失焦即自动关闭（应用级事件过滤 MouseButtonPress
    落在面板外 + ApplicationDeactivate；不用 Qt.Popup——它无法钉住，
    失焦关闭是写死在窗口标志里的语义）；钉住后常驻，仍可拖动/最大化/关闭。
    内嵌 MarkdownMediaView 用与主面板完全相同的渲染管线（标题分级配色/
    GIF 动图/视频按钮）。WA_DeleteOnClose 用完即毁（临时浮层不进
    window_manager）；面板在豁免字体子树内，不经 enforcer 刷工具字体。
    """

    _QSS = (
        "QFrame#docInfoPopup { background: #1D1D20; "
        "border: 1px solid #3d3d3d; }"
        "QFrame#docInfoHeader { background: #ffffff; }")
    _BTN_QSS = (
        "QToolButton { background: transparent; border: none; "
        "color: #555555; font-size: 13px; padding: 2px 4px; }"
        "QToolButton:hover { background: #dcdcdc; color: #1a1a1e; "
        "border-radius: 3px; }")
    _BTN_QSS_PINNED = (
        "QToolButton { background: transparent; border: none; "
        "color: #0d6399; font-size: 13px; padding: 2px 4px; }"
        "QToolButton:hover { background: #dcdcdc; "
        "border-radius: 3px; }")
    _MAX_BTN_QSS = (
        "QToolButton { background: transparent; border: none; "
        "color: #555555; font-size: 17px; padding: 1px 5px; }"
        "QToolButton:hover { background: #dcdcdc; color: #1a1a1e; "
        "border-radius: 3px; }")
    _RESIZE_MARGIN = 6          # 边缘缩放热区（px）
    _MIN_SIZE = (360, 240)      # 缩放下限
    _RESIZE_CURSORS = {
        "n": QtCore.Qt.SizeVerCursor, "s": QtCore.Qt.SizeVerCursor,
        "e": QtCore.Qt.SizeHorCursor, "w": QtCore.Qt.SizeHorCursor,
        "nw": QtCore.Qt.SizeFDiagCursor, "se": QtCore.Qt.SizeFDiagCursor,
        "ne": QtCore.Qt.SizeBDiagCursor, "sw": QtCore.Qt.SizeBDiagCursor}

    @classmethod
    def open_at(cls, parent, info, global_pos):
        """在 global_pos（全局坐标）附近弹出；先关旧面板（同时刻一个）。"""
        prev = _DOC_INFO_STATE["current"]
        if prev is not None:
            try:
                prev.close()
            except RuntimeError as exc:
                log.debug("close prev doc info popup failed: %s", exc)
        popup = cls(parent, info)
        screen = parent.screen() if parent is not None \
            else QtGui.QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(global_pos.x() + 8, geo.left()),
                geo.right() - popup.width() + 1)
        y = global_pos.y() + 8
        if y + popup.height() > geo.bottom():   # 下方放不下 → 弹在上方
            y = global_pos.y() - popup.height() - 8
        y = min(max(y, geo.top()), geo.bottom() - popup.height() + 1)
        popup.move(x, y)
        popup.show()
        popup.raise_()
        # show 后窗口框 margin 才确定（实测 offscreen 弹层 +2px 偏移），
        # 用 frameGeometry 复钳一次，把整框收回屏幕
        fg = popup.frameGeometry()
        if not geo.contains(fg):
            dx = dy = 0
            if fg.right() > geo.right():
                dx = geo.right() - fg.right()
            if fg.bottom() > geo.bottom():
                dy = geo.bottom() - fg.bottom()
            if fg.left() < geo.left():
                dx = geo.left() - fg.left()
            if fg.top() < geo.top():
                dy = geo.top() - fg.top()
            popup.move(popup.pos() + QtCore.QPoint(dx, dy))
        _DOC_INFO_STATE["current"] = popup
        return popup

    def __init__(self, parent, info):
        super().__init__(parent)
        self.setObjectName("docInfoPopup")
        self.setWindowFlags(QtCore.Qt.Tool | QtCore.Qt.FramelessWindowHint)
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setStyleSheet(self._QSS)
        self.resize(460, 540)
        self._pinned = False
        self._maximized = False
        self._normal_geo = None
        self._drag_pos = None
        self._resizing = None   # 活动缩放方向（"n"/"se"/...），None=非缩放
        self._resize_start = None
        self._resize_start_geo = None
        # 边缘缩放的光标/按下事件走 frame 自己的 mouse 事件——头部与
        # 身体容器需开 mouseTracking 才能把悬停 move 传上来（QLabel 等
        # 子件不开 tracking，悬停不经过它们）
        self.setMouseTracking(True)

        header = QtWidgets.QFrame()
        header.setObjectName("docInfoHeader")
        header.setFixedHeight(26)
        header.setMouseTracking(True)   # 顶边缩放热区在标题条范围内
        name_lbl = QtWidgets.QLabel(info.display_label)
        name_lbl.setStyleSheet(
            "color: #1a1a1e; font-weight: bold; font-size: 13px; "
            "background: transparent; border: none;")
        file_lbl = QtWidgets.QLabel("doc.md")
        file_lbl.setStyleSheet(
            "color: #8a8a8a; font-size: 11px; "
            "background: transparent; border: none;")
        self._pin_btn = QtWidgets.QToolButton()
        self._pin_btn.setCheckable(True)
        self._pin_btn.setToolTip("钉住：不再随失焦关闭")
        self._pin_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self._pin_btn.setIconSize(QtCore.QSize(14, 14))
        self._pin_btn.setIcon(_pin_icon(False))
        self._max_btn = QtWidgets.QToolButton()
        self._max_btn.setText("□")
        self._max_btn.setToolTip("最大化 / 还原")
        self._max_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self._close_btn = QtWidgets.QToolButton()
        self._close_btn.setText("✕")
        self._close_btn.setToolTip("关闭")
        self._close_btn.setFocusPolicy(QtCore.Qt.NoFocus)
        self._pin_btn.setStyleSheet(self._BTN_QSS)
        self._max_btn.setStyleSheet(self._MAX_BTN_QSS)
        self._close_btn.setStyleSheet(self._BTN_QSS)
        self._pin_btn.toggled.connect(self._on_pin)
        self._max_btn.clicked.connect(self._toggle_max)
        self._close_btn.clicked.connect(self.close)

        head_lay = QtWidgets.QHBoxLayout(header)
        head_lay.setContentsMargins(8, 2, 4, 2)
        head_lay.setSpacing(6)
        head_lay.addWidget(name_lbl)
        head_lay.addWidget(file_lbl)
        head_lay.addStretch(1)
        head_lay.addWidget(self._pin_btn)
        head_lay.addWidget(self._max_btn)
        head_lay.addWidget(self._close_btn)

        # ⚠ 不能在构造期传 parent=self（孤儿防线改走下方 deleteLater）：
        # 构造期挂进工具窗口子树，字体 enforcer 的 ChildAdded 过滤器会在
        # QTextBrowser 构造期间触碰内部滚动条，其 Python 包装被提前粘成
        # QWidget（dialogs.py 记录的包装类型粘连，H21 实测）——随后
        # _DocPreview.__init__ 读 sb.minimum() 即 AttributeError。无 parent
        # 构造、稍后由布局收养与既有路径一致（enforcer 触碰发生在子类
        # 绑定完成后，不粘连）
        self._view = MarkdownMediaView(metadata.doc_dir(info.name))
        self._view.setStyleSheet(
            "color: #cccccc; background: transparent; border: none;")
        hint = None
        if metadata.doc_exists(info.name):
            self._view.set_markdown_with_media(metadata.read_doc(info.name))
        else:
            # 无文档：视图不进布局，就地销毁——不挂 parent 也不 deleteLater
            # 会成为孤儿顶层窗体（popup 是 WA_DeleteOnClose，析构后只能
            # 等 GC 回收）
            self._view.hide()
            self._view.deleteLater()
            hint = QtWidgets.QLabel("未创建文档（右键 → 编辑文档）")
            hint.setStyleSheet(
                "color: #9a9aa2; background: transparent; border: none;")

        body = QtWidgets.QWidget()
        body.setStyleSheet("background: transparent; border: none;")
        body.setMouseTracking(True)   # 底/左/右缩放热区悬停经它传上来
        body_lay = QtWidgets.QVBoxLayout(body)
        body_lay.setContentsMargins(10, 8, 12, 10)
        if hint is not None:
            body_lay.addWidget(hint)
        else:
            body_lay.addWidget(self._view, 1)

        lay = QtWidgets.QVBoxLayout(self)
        # 零边距：白色标题条直接贴住边框——留 1px 内距会露一圈 #1D1D20
        # 底色，看起来像标题栏外包了道黑边（2026-11 用户反馈）
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        lay.addWidget(body, 1)

        # 未钉住时的失焦关闭：应用级过滤抓"面板外鼠标按下"，事件级抓
        # 应用失焦（Alt-Tab）。**必须延迟安装（singleShot 0）**：面板是在
        # 中键按下事件的投递中被 spawn 的，若立即安装，同一次在途 press
        # 会被应用过滤器当成"面板外按下"把面板当场关掉——开了又瞬间自
        # 关，用户看起来就是"中键无法触发"（真实会话实测踩坑）。filter
        # 对象随 WA_DeleteOnClose 析构自动摘除
        self._filter_installed = False
        QtCore.QTimer.singleShot(0, self._install_close_filter)

    # ---------------- 失焦关闭 / 钉住 ----------------

    def _install_close_filter(self):
        if self._filter_installed:
            return
        QtWidgets.QApplication.instance().installEventFilter(self)
        self._filter_installed = True

    def eventFilter(self, obj, event):   # noqa: A003
        if (event.type() == QtCore.QEvent.MouseButtonPress
                and not self._pinned
                and isinstance(obj, QtWidgets.QWidget)
                and obj is not self
                and not self.isAncestorOf(obj)):
            self.close()
        return False

    def event(self, e):
        if e.type() == QtCore.QEvent.ApplicationDeactivate \
                and not self._pinned:
            self.close()
        return super().event(e)

    def _on_pin(self, checked):
        self._pinned = checked
        self._pin_btn.setIcon(_pin_icon(checked))
        self._pin_btn.setStyleSheet(
            self._BTN_QSS_PINNED if checked else self._BTN_QSS)

    # ---------------- 移动 / 边缘缩放 / 最大化 ----------------

    def _resize_dir(self, pos):
        """光标所在的边缘缩放方向（8 向）；不在热区返回 None。最大化时
        禁缩放。热区沿 rect 边缘 6px；顶部热区覆盖标题条上缘，覆盖不到
        的标题条区域（y>6）是拖拽移动区。"""
        if self._maximized:
            return None
        r = self.rect()
        m = self._RESIZE_MARGIN
        left = pos.x() < m
        right = pos.x() >= r.width() - m
        top = pos.y() < m
        bottom = pos.y() >= r.height() - m
        if not (left or right or top or bottom):
            return None
        d = ""
        if top:
            d += "n"
        if bottom:
            d += "s"
        if left:
            d += "w"
        if right:
            d += "e"
        return d

    def _sync_resize_cursor(self, pos):
        d = self._resize_dir(pos)
        cursor = self._RESIZE_CURSORS.get(d, QtCore.Qt.ArrowCursor)
        if self.cursor().shape() != cursor:
            self.setCursor(cursor)

    def _apply_resize(self, global_pos):
        dx = global_pos.x() - self._resize_start.x()
        dy = global_pos.y() - self._resize_start.y()
        geo = QtCore.QRect(self._resize_start_geo)
        min_w, min_h = self._MIN_SIZE
        d = self._resizing
        if "e" in d:
            geo.setWidth(max(min_w,
                             self._resize_start_geo.width() + dx))
        if "w" in d:
            geo.setLeft(min(self._resize_start_geo.left() + dx,
                            self._resize_start_geo.right() - min_w + 1))
        if "s" in d:
            geo.setHeight(max(min_h,
                              self._resize_start_geo.height() + dy))
        if "n" in d:
            geo.setTop(min(self._resize_start_geo.top() + dy,
                           self._resize_start_geo.bottom() - min_h + 1))
        self.setGeometry(geo)

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            pos = event.position().toPoint()
            d = self._resize_dir(pos)
            if d is not None:
                self._resizing = d
                self._resize_start = event.globalPosition().toPoint()
                self._resize_start_geo = self.geometry()
                return
            if (not self._maximized
                    and pos.y() <= self._header_height()):
                self._drag_pos = (event.globalPosition().toPoint()
                                  - self.frameGeometry().topLeft())
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        buttons = event.buttons()
        if self._resizing is not None \
                and buttons & QtCore.Qt.LeftButton:
            self._apply_resize(event.globalPosition().toPoint())
            return
        if self._drag_pos is not None \
                and buttons & QtCore.Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            return
        if buttons == QtCore.Qt.NoButton:
            # 悬停：边缘热区切缩放光标，标题条/内部保持默认箭头
            self._sync_resize_cursor(event.position().toPoint())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._resizing is not None:
            self._resizing = None
            self._resize_start = None
            self._resize_start_geo = None
            self.unsetCursor()
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def _header_height(self):
        # 标题条高（含边框内 1px 偏移）；QFrame 直系子件在标题条外不起拖
        return 27

    def _toggle_max(self):
        geo = self.screen().availableGeometry().adjusted(8, 8, -8, -8)
        if not self._maximized:
            self._normal_geo = self.geometry()
            self.setGeometry(geo)
            self._maximized = True
            self._max_btn.setText("❐")
        else:
            self.setGeometry(self._normal_geo)
            self._maximized = False
            self._max_btn.setText("□")

    def closeEvent(self, event):
        if _DOC_INFO_STATE["current"] is self:
            _DOC_INFO_STATE["current"] = None
        try:
            self._view.stop_media()
        except RuntimeError as exc:
            log.debug("stop doc info movies failed: %s", exc)
        if self._filter_installed:
            QtWidgets.QApplication.instance().removeEventFilter(self)
        super().closeEvent(event)


# MediaDialog 的单例状态：模块级字典 + NameError 守卫活过 Reload（见
# MediaDialog docstring）
try:
    _MEDIA_DIALOG_STATE
except NameError:
    _MEDIA_DIALOG_STATE = {"current": None}


class MediaDialog(QtWidgets.QDialog):
    """媒体播放对话框：GIF 用 QMovie 循环，视频用 QMediaPlayer。

    非模态（看文档时可反复对照网络），同一时刻一个实例。单例状态挂
    模块级字典（NameError 守卫活过 Reload）：类属性在 Reload 重建类
    后归 None、旧实例的 closeEvent 却写回旧类——单例保证会静默失效
    （可同时开两个播放器）。
    """

    @classmethod
    def _current(cls):
        return _MEDIA_DIALOG_STATE["current"]

    @classmethod
    def open_media(cls, parent, path):
        prev = _MEDIA_DIALOG_STATE["current"]
        if prev is not None:
            try:
                prev.close()
                prev.deleteLater()
            except RuntimeError as exc:
                log.debug("close previous media dialog failed: %s", exc)
        dlg = cls(parent, path)
        _MEDIA_DIALOG_STATE["current"] = dlg
        dlg.show()
        dlg.raise_()
        return dlg

    def __init__(self, parent, path):
        super().__init__(parent)
        # ✕ 关闭即析构：dialog 有 C++ parent，Python 引用丢弃后对象会随
        # parent 永久滞留（QMediaPlayer/QVideoWidget，GIF 还有 CacheAll
        # 全帧缓存的 _MovieLabel）；closeEvent 已清掉单例引用，open_media
        # 的 prev.deleteLater() 够不到它——关闭本身必须完成回收
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setWindowTitle("预览 - {}".format(os.path.basename(path)))
        self.setModal(False)
        self.resize(680, 480)
        self._path = path
        self._player = None
        self._audio = None
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        ext = os.path.splitext(path)[1].lower()
        if ext == ".gif":
            label = _MovieLabel(path, 640, self)
            lay.addWidget(label, 1)
            return

        # 视频：⚠ setVideoOutput 必须在 play 之前，否则 InvalidMedia
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(0.8)
        self._player.setAudioOutput(self._audio)
        video = QVideoWidget(self)
        self._player.setVideoOutput(video)
        self._player.setSource(QtCore.QUrl.fromLocalFile(path))
        lay.addWidget(video, 1)

        bar = QtWidgets.QHBoxLayout()
        self._play_btn = QtWidgets.QPushButton("暂停")
        self._slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self._slider.setRange(0, 1000)
        self._time = QtWidgets.QLabel("0:00")
        bar.addWidget(self._play_btn)
        bar.addWidget(self._slider, 1)
        bar.addWidget(self._time)
        lay.addLayout(bar)

        self._play_btn.clicked.connect(self._toggle_play)
        self._slider.sliderMoved.connect(self._player.setPosition)
        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(
            lambda d: self._slider.setValue(0))
        self._player.play()

    def _toggle_play(self):
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self._play_btn.setText("播放")
        else:
            self._player.play()
            self._play_btn.setText("暂停")

    def _on_position(self, pos):
        dur = max(1, self._player.duration())
        if not self._slider.isSliderDown():
            self._slider.setValue(int(pos * 1000 / dur))
        secs = pos // 1000
        self._time.setText("{}:{:02d}".format(secs // 60, secs % 60))

    def closeEvent(self, event):
        if self._player is not None:
            self._player.stop()
            self._player.setVideoOutput(None)
        if _MEDIA_DIALOG_STATE["current"] is self:
            _MEDIA_DIALOG_STATE["current"] = None
        super().closeEvent(event)


# ---- 文档编辑器：列表延续（Obsidian 式，2026-11 用户要求）----
# 无序/有序/任务勾选框/引用行尾回车延续同型标记（有序自增、勾选框延续为
# 未勾选）；空列表行/空引用行（只有标记无内容）行尾回车 = 退出该层格式；
# 引用与列表可组合（"> - item" → "> - "），逐层回车逐层退。行中回车在
# 光标处切分、后半段随标记落到下一行。围栏内与 Shift+Enter 不延续。
_LIST_ITEM_RE = re.compile(
    r"^(\s*)([-*+]|\d+[\.\)])\s+(\[[ xX]\]\s*)?(.*)$")
_QUOTE_RE = re.compile(r"^(\s*)((?:>\s*)+)")
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


class _DocEditor(QtWidgets.QPlainTextEdit):
    """markdown 文档编辑器：回车自动延续列表/勾选框/引用格式（Obsidian 式）。

    拦截 Return 键自行处理（其余键全部走原生）；继续/退出都是单步
    insertText/removeSelectedText，撤销粒度天然一次一步。解析按
    「缩进 × 引用前缀 × 列表标记」三段组合：引用可嵌套（"> > "整体一个
    前缀）、引用内可挂列表；空项退出逐层进行（"> - " → "> " → 空）。
    """

    def keyPressEvent(self, event):
        mods = event.modifiers() & ~QtCore.Qt.KeypadModifier
        if (event.key() != QtCore.Qt.Key_Return
                or mods != QtCore.Qt.NoModifier):
            super().keyPressEvent(event)   # Shift+Enter 软换行等走原生
            return
        cursor = self.textCursor()
        if cursor.hasSelection():
            super().keyPressEvent(event)   # 有选区：原生替换选区
            return
        block = cursor.block()
        blk = block.previous()
        fences = 0
        while blk.isValid():
            if _FENCE_RE.match(blk.text()):
                fences += 1
            blk = blk.previous()
        if fences % 2:                     # 代码围栏内不延续
            super().keyPressEvent(event)
            return
        col = cursor.positionInBlock()
        line = block.text()[:col]
        qm = _QUOTE_RE.match(line)
        indent = qm.group(1) if qm else ""
        quotes = qm.group(2) if qm else ""
        content = line[qm.end():] if qm else line
        lm = _LIST_ITEM_RE.match(content)
        if lm is None:
            if not quotes:
                super().keyPressEvent(event)   # 普通行：原生换行
                return
            if cursor.atBlockEnd() and not content:
                self._strip_prefix(len(indent))   # 空引用行：退出引用层
                return
            cursor.insertText("\n" + indent + quotes)  # 引用延续，尾部随行
            return
        l_indent, marker, checkbox, rest = lm.groups()
        if cursor.atBlockEnd() and not rest:
            # 空列表项（只有标记无内容）：只退列表层、保留引用前缀，
            # 下一层回车再退引用。rest 取自截至光标的行文本——行尾时
            # "标记后无内容"即整行仅标记
            self._strip_prefix(
                len(indent) + len(quotes) + len(l_indent))
            return
        # 延续列表标记：有序自增；勾选框延续为未勾选 [ ]
        if marker[0].isdigit():
            marker = str(int(marker[:-1]) + 1) + marker[-1]
        cont = indent + quotes + l_indent + marker + " "
        if checkbox is not None:
            cont += "[ ] "
        cursor.insertText("\n" + cont)   # 光标后既有文字自然落到标记之后

    def _strip_prefix(self, keep):
        """退出引用/列表层：删除行内 keep 之后到行尾的标记文本。
        保留 keep（原始缩进或缩进+引用前缀），光标落在保留段末尾。"""
        cur = self.textCursor()
        cur.setPosition(cur.block().position() + keep)
        cur.movePosition(QtGui.QTextCursor.EndOfBlock,
                         QtGui.QTextCursor.KeepAnchor)
        cur.removeSelectedText()


class DocEditorDialog(QtWidgets.QDialog):
    """配方文档编辑器（非模态子窗口）：左编辑、右实时预览。

    - 插入图片/视频把文件复制进配方 assets/，正文插入相对引用
    - 预览自动刷新（防抖）；GIF 原地动、视频渲染成播放按钮
    - 确认/取消模式：编辑期间不落盘，点「确认」才保存并关窗（发
      docSaved 信号，主面板同步刷新文档预览）；「取消」/关窗丢弃改动
      （有未保存修改先确认），本次会话插入的 assets 一并清理
    """

    docSaved = QtCore.Signal(str)   # recipe 内部名

    def __init__(self, parent, info, display_label=None):
        super().__init__(parent)
        self.setWindowTitle("文档 - {}".format(display_label
                                              or info.display_label))
        self.setModal(False)
        self.resize(960, 620)
        self._info = info
        self._name = info.name
        self._doc_dir = metadata.doc_dir(self._name)
        self._inserted_assets = []   # 本次会话新插入的 assets 相对路径

        self.editor = _DocEditor()
        # 没有文档就是空白（不自动建模板文件）——确认保存后 doc.md 才存在，
        # 主面板的"有文档渲染 markdown / 无文档显示备注"随之切换
        self.editor.setPlainText(metadata.read_doc(self._name))
        self.editor.document().setModified(False)
        self.preview = MarkdownMediaView(self._doc_dir)

        insert_img = QtWidgets.QPushButton("插入图片...")
        insert_media = QtWidgets.QPushButton("插入视频/GIF...")
        open_dir_btn = QtWidgets.QPushButton("打开文档文件夹")
        tip = QtWidgets.QLabel("Markdown 语法；图片/视频会复制进 assets/ 并用相对路径引用")
        tip.setStyleSheet("color: #888888;")
        btns = QtWidgets.QHBoxLayout()
        btns.addWidget(tip, 1)
        btns.addWidget(insert_img)
        btns.addWidget(insert_media)
        btns.addWidget(open_dir_btn)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.setHandleWidth(4)
        splitter.addWidget(self.editor)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 470])

        bbox = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        bbox.accepted.connect(self._confirm_save)
        bbox.rejected.connect(self._cancel)

        lay = QtWidgets.QVBoxLayout(self)
        lay.addLayout(btns)
        lay.addWidget(splitter, 1)
        lay.addWidget(bbox)
        localize_buttons(self)   # 布局收养后才有父子关系，Ok → 确认、Cancel → 取消

        insert_img.clicked.connect(lambda: self._insert_asset(False))
        insert_media.clicked.connect(lambda: self._insert_asset(True))
        open_dir_btn.clicked.connect(self._open_dir)

        self._render_timer = QtCore.QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(PREVIEW_DEBOUNCE_MS)
        self._render_timer.timeout.connect(self._render_preview)
        self.editor.textChanged.connect(self._on_text_changed)
        self.editor.setTabChangesFocus(False)

        self._render_preview()

    # ---------------- 编辑 / 保存 ----------------

    def _on_text_changed(self):
        self._render_timer.start()   # 只刷新右侧预览；落盘等「确认」

    def _confirm_save(self):
        try:
            metadata.write_doc(self._name, self.editor.toPlainText())
        except OSError as exc:
            log.warning("save doc failed for %s: %s", self._name, exc)
            warn(self, "保存失败", str(exc))
            return   # 保存失败不关窗，内容留在编辑器里
        # 保存成功：本次会话插入的 assets 已被保存的文档引用，清掉登记
        # 防止再次关闭时走"丢弃修改"清理误删它们；同时复位 modified
        # 标志，再次关窗不再弹"有未保存修改"确认
        self._inserted_assets = []
        self.editor.document().setModified(False)
        self.docSaved.emit(self._name)
        self.accept()

    def _cancel(self):
        if self._confirm_discard():
            self._discard_inserted_assets()
            self.reject()

    def _confirm_discard(self):
        """有未保存修改时确认丢弃；返回 True=继续关闭。"""
        if not self.editor.document().isModified():
            return True
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("放弃修改")
        box.setText("有未保存的修改，确定丢弃？")
        box.setStandardButtons(QtWidgets.QMessageBox.Yes
                               | QtWidgets.QMessageBox.No)
        localize_buttons(box)   # Yes → 确认、No → 取消
        return box.exec_() == QtWidgets.QMessageBox.Yes

    def _discard_inserted_assets(self):
        """取消时清理本次会话插入的 assets（正文已丢弃，引用不存在）。"""
        for rel in self._inserted_assets:
            path = os.path.join(self._doc_dir, rel.replace("/", os.sep))
            try:
                os.remove(path)
            except OSError as exc:
                log.debug("cleanup asset %s failed: %s", path, exc)
        self._inserted_assets = []

    def _insert_asset(self, media):
        start = self._doc_dir
        if media:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "插入视频或 GIF", start, metadata.MEDIA_FILTER)
        else:
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "插入图片", start,
                "图片 (*.png *.jpg *.jpeg *.gif *.webp *.bmp);;全部文件 (*.*)")
        if not path:
            return
        try:
            rel = metadata.insert_asset(self._name, path)
        except (OSError, RuntimeError) as exc:
            warn(self, "插入失败", str(exc))
            return
        # alt 取文件名，剥掉会破坏 markdown 图片语法的字符（[]、换行）
        alt = re.sub(r"[\[\]\r\n]+", "", os.path.basename(path))
        self._inserted_assets.append(rel)
        self.editor.insertPlainText("![{}]({})".format(alt, rel))

    def _open_dir(self):
        QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(self._doc_dir))

    # ---------------- 预览渲染 ----------------

    def _render_preview(self):
        self.preview.set_markdown_with_media(self.editor.toPlainText())

    def done(self, result):
        # 关闭出口统一停播：QMovie 由计时器驱动、与可见性无关，窗体隐藏
        # 后仍在逐帧解码并对隐藏 viewport 触发刷新，持续烧 CPU（主面板
        # 预览已修过同款问题）。确认/取消/✕ 三条路都经 done()，一处收口
        self.preview.stop_media()
        super().done(result)

    def closeEvent(self, event):
        # X 关闭等同取消：有未保存修改先确认丢弃（拒绝则不关窗）
        # Reload Modules 触发的关闭不弹模态（模态会卡住 reload 流程）：
        # 直接拒绝关闭——window_manager 据此中止重载，草稿与窗口都保留
        from houtools.ui import window_manager as _wm
        if getattr(_wm, "_closing_all", False):
            event.ignore()
            return
        if self._confirm_discard():
            self._discard_inserted_assets()
            super().closeEvent(event)
        else:
            event.ignore()
