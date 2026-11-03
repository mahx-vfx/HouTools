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
# 变量原值照搬）；字重用 VF 变量轴精确取主题的 650/620/580 小数权重
_HEAD_STYLES = {   # level: (pt, 颜色, wght 轴值, 上间距, 下间距)
    1: (15.0, "#4F74AF", 650, 10, 4),
    2: (12.5, "#8567BD", 620, 8, 3),
    3: (11.25, "#4E9A8A", 580, 7, 2),
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
_LIST_INDENT_PX = 14           # 列表缩进（Qt 默认 40px/级，空白过大）
_DOC_MARGIN_PX = 10            # 文档页边距（默认 4px 太贴边）
_BAR_CHARS = "\u2009\u2009"    # 引用左线体：thin space×2 打底色（逐块续成整线）
# markdown 导入器打的块属性（Qt 头文件未公开常量名，PySide6 无属性名，
# 数值经 detect 探针实测）：引用块 int 1 / 围栏代码块 bool True
_MD_PROP_BLOCKQUOTE = 4224
_MD_PROP_CODE_FENCE = 4176
_INLINE_CODE_FAMILY = "monospace"   # 导入器给行内代码/围栏打的字族标记
# VF 变量轴精确字重（Qt 6.7+）；旧 Qt 就近取枚举档（H21 能力探测消化）
_HAS_VAR_AXIS = hasattr(QtGui.QFont, "setVariableAxis")


def _apply_heading_weight(f, wght):
    """标题字重：变量轴精确取 650/620/580 等小数权重，旧 Qt 降级枚举档。"""
    if _HAS_VAR_AXIS:
        f.setVariableAxis(QtGui.QFont.Tag("wght"), float(wght))
    else:
        f.setWeight(QtGui.QFont.Weight(
            500 if wght < 540 else 600 if wght < 660 else 700))


class _DocPreview(QtWidgets.QTextBrowser):
    """文档预览：相对路径图片按文档目录解析（markdown 里写 assets/x.png）。

    垂直滚动条为悬浮细滑杆：reparent 到 QTextBrowser 本体（与原生滚动
    条同级的容器子控件，不占布局，文字列宽恒定），4px 宽、可滚动时才
    显示——主面板预览与文档编辑器右栏共用本类，两处自动一致。
    字体为文档阅读字体（方圆体 VF，有完整字重轴），豁免工具统一字体
    刷树（fonts.py 豁免机制）；setMarkdown 后由 _restyle_document 重排
    层级（标题分级配色/粗体变色/列表缩进等）。
    """

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
        if watched is self.viewport() \
                and event.type() == QtCore.QEvent.Resize:
            self._sync_overlay_sb_geometry()
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
        blocks = []   # (block_number, blockFormat, [(pos, len, charFormat)])
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
            blocks.append((block.blockNumber(), block.blockFormat(), frags))
            block = block.next()
        # ---- 第二趟：纯写（新鲜寻址）----
        for number, bf, frags in blocks:
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
            nbf.setLineHeight(_LINE_HEIGHT_PCT, lh_type)
            if level:
                _pt, _color, _wght, top_m, bottom_m = _HEAD_STYLES[level]
                nbf.setTopMargin(top_m)
                nbf.setBottomMargin(bottom_m)
            elif is_quote:
                # 导入器给引用块 40px 左右 margin（"空白太多"的来源之一）
                nbf.setLeftMargin(8)
                nbf.setRightMargin(0)
            elif is_fence:
                nbf.setBackground(QtGui.QColor(_CODE_BG))
            if is_quote:
                # OnePage：引用块 = 微亮面板底（引用内的标题也垫底色）
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
        # ---- 第三趟：引用左线垫底（块内插入不改块号）----
        # 条体 = thin space×2 打底色 + 普通空格做间隔，无字形依赖。
        # 标题竖条三轮方案（▍字形/thin space 底色条/图片条）全部被否，
        # 2026-11 用户拍板：标题不放任何装饰条，只留配色/字阶/字重。
        for number, bf, _frags in blocks:
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
            path, allowed = self._resolve(url)
            if allowed and os.path.exists(path):
                img = QtGui.QImage(path)
                if not img.isNull():
                    return img
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
            QtGui.QDesktopServices.openUrl(url)


class _MovieLabel(QtWidgets.QLabel):
    """GIF 播放标签（MediaDialog 用，QMovie 循环播放）。"""

    def __init__(self, path, max_w, parent=None):
        super().__init__(parent)
        movie = QtGui.QMovie(self)
        movie.setFileName(path)
        movie.setCacheMode(QtGui.QMovie.CacheAll)
        movie.jumpToFrame(0)
        pm = movie.currentPixmap()
        if not pm.isNull() and pm.width() > max_w:
            h = max(1, int(round(pm.height() * max_w / pm.width())))
            movie.setScaledSize(QtCore.QSize(max_w, h))
        self.setMovie(movie)
        movie.start()


class MarkdownMediaView(_DocPreview):
    """可渲染 markdown + 内联媒体的预览视图（文档编辑器右栏与主面板共用）。

    - GIF：QMovie 逐帧同步进文档资源（loadResource 返回当前帧 +
      frameChanged 时 addResource + viewport 刷新），真正内联动画；
      超宽的帧按视口宽度等比缩小
    - 视频：set_markdown_with_media 前把 ![](x.mp4) 预处理成普通链接
      （QTextCursor 无 insertWidget，Qt 富文本不支持内嵌控件），点击
      链接由 _DocPreview._on_anchor 弹播放器
    """

    _VIDEO_IMAGE_RE = re.compile(
        r"!\[([^\]]*)\]\(([^)]+\.(?:mp4|mov|avi|webm|mkv))\)", re.IGNORECASE)

    def __init__(self, doc_dir, parent=None):
        super().__init__(doc_dir, parent)
        self._movies = {}   # 文档里的 url 字符串 -> QMovie

    def set_markdown_with_media(self, text):
        self.stop_media()
        text = self._VIDEO_IMAGE_RE.sub(r"[▶ \1](\2)", text)
        self.setMarkdown(text)
        self._restyle_document()
        # QMovie 在布局首次查询图片资源时经 loadResource 惰性创建并启动

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
                self._movie_frame_pixmap(movie))
            self.viewport().update()

    def _movie_frame_pixmap(self, movie):
        pm = movie.currentPixmap()
        max_w = max(120, self.viewport().width() - 12)
        if not pm.isNull() and pm.width() > max_w:
            pm = pm.scaledToWidth(max_w, QtCore.Qt.SmoothTransformation)
        return pm

    def loadResource(self, rtype, url):
        if rtype == QtGui.QTextDocument.ImageResource:
            url_str = url.toString()
            if url_str.lower().split("?", 1)[0].endswith(".gif"):
                movie = self._movies.get(url_str)
                if movie is None:
                    path, allowed = self._resolve(url)
                    if allowed and os.path.exists(path):
                        movie = QtGui.QMovie(self)
                        movie.setFileName(path)
                        movie.setCacheMode(QtGui.QMovie.CacheAll)
                        movie.frameChanged.connect(
                            lambda _i, u=url_str: self._on_movie_frame(u))
                        self._movies[url_str] = movie
                        movie.start()
                if movie is not None:
                    return self._movie_frame_pixmap(movie)
        return super().loadResource(rtype, url)


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

        self.editor = QtWidgets.QPlainTextEdit()
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

    def closeEvent(self, event):
        # X 关闭等同取消：有未保存修改先确认丢弃（拒绝则不关窗）
        if self._confirm_discard():
            self._discard_inserted_assets()
            super().closeEvent(event)
        else:
            event.ignore()
