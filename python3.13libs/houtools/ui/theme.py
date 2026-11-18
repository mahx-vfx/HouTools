"""HouTools 品牌 token 唯一来源（深夜技术工作台）。

纯 Python，不 import Qt：UI 模块从这里取颜色/字号/间距/圆角，QSS 由
``houtools.ui.qss`` 按 token 拼接。除本文件外，UI 代码不得写裸 hex
（用户内容色除外：Recipe 主题/标签色见 ``RECIPE_PALETTE``，语法高亮见
``SYNTAX``）。

品牌方向（docs/brand.md）：
- 墨黑底 + 品牌橙动作色 + 深绿蓝图点缀
- 橙色是动作色不是背景色：单屏橙色面积不超过约 10%
- 状态不能只靠颜色：必须同时有图标或文字
"""

# ---------------------------------------------------------------------------
# 色彩
# ---------------------------------------------------------------------------
TOKENS = {
    # 墨黑层级
    "INK_1000": "#05050a",   # 窗口外缘 / About/docs 主背景
    "INK_950": "#0a0a0f",    # 工作区 / 面板底
    "INK_900": "#101017",    # 卡片 / 输入 / 列表底
    "INK_850": "#15151c",    # hover / 弹层 / Toast 底
    "INK_800": "#1c1c25",    # 强 hover / 次级按钮 hover
    # 线条
    "LINE": "#262631",       # 1px 常规边框
    "LINE_STRONG": "#34343f",  # hover 边框 / 分隔
    # 文字
    "TEXT": "#f0f0f3",
    "TEXT_2": "#b8b8c2",
    "TEXT_3": "#8a8a92",
    "TEXT_4": "#5c5c66",
    # 品牌橙（动作色）
    "ACCENT": "#ff5c38",
    "ACCENT_HOVER": "#ff7454",
    "ACCENT_PRESSED": "#e64a2a",
    "ACCENT_SOFT": "rgba(255,92,56,.12)",
    "ACCENT_LINE": "rgba(255,92,56,.35)",
    # ACCENT 底上的前景（白字；2026-11 用户拍板，不再走 on_color 的墨黑字）
    "ON_ACCENT": "#ffffff",
    # 主按钮常态底：暗橙（白字对比度 5.5 ≥ 4.5 AA），悬停点亮为 ACCENT
    # 品牌亮橙、「点火」式交互（2026-11-19 用户拍板，经 #d14314 压暗一档），按下压深
    "ACCENT_FILL": "#bf380d",
    "ACCENT_FILL_PRESSED": "#a02e08",
    # 语义
    "SUCCESS": "#45b06e",
    "SUCCESS_TEXT": "#6ee7b7",
    "WARNING": "#f2a33c",
    "WARNING_TEXT": "#ffd08a",
    "ERROR": "#d1283e",
    "ERROR_TEXT": "#ff7a7a",
    # 品牌背景网格（只用于 About / 空状态）
    "GRID_LINE": "#2e8b57",
    "GRID_DOT": "#45b06e",
}

# 统一用浅橙做焦点环（QSS 无 outline；主按钮焦点用 2px 浅橙）
FOCUS_RING = "#ffd0c4"
TOKENS["FOCUS_RING"] = FOCUS_RING


def color(name):
    """按 token 名取色值；未知 token 直接 KeyError（防拼错静默）。"""
    return TOKENS[name]


def rgba(name, alpha):
    """token → ``rgba(r,g,b,a)``；alpha 为 0-1 浮点（QSS 可直接用）。"""
    value = color(name)
    if value.startswith("rgba("):
        return value
    r = int(value[1:3], 16)
    g = int(value[3:5], 16)
    b = int(value[5:7], 16)
    return "rgba({},{},{},{:.2f})".format(r, g, b, float(alpha))


def with_alpha(hex_color, alpha):
    """任意 ``#rrggbb`` → ``rgba(...)``（Qt 绘制用，alpha 0-1）。"""
    value = hex_color.lstrip("#")
    r = int(value[0:2], 16)
    g = int(value[2:4], 16)
    b = int(value[4:6], 16)
    return "rgba({},{},{},{:.2f})".format(r, g, b, float(alpha))


def on_color(hex_color):
    """深/浅底上的可读前景色：亮底用墨黑字，暗底用白字。"""
    value = hex_color.lstrip("#")[:6]
    r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    # sRGB 相对亮度（粗略；阈值取经验值，保证橙/绿底用墨黑字）
    lum = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0
    return TOKENS["INK_1000"] if lum > 0.42 else "#ffffff"


# ---------------------------------------------------------------------------
# 字体角色
# ---------------------------------------------------------------------------
FONT_ROLES = {
    # 品牌字：窗口标题、产品字标、About/docs 大标题
    "brand": {
        "families": ("DingTalk JinBuTi", "Alimama ShuHeiTi",
                     "Microsoft YaHei UI", "Microsoft YaHei"),
        "size": 13,
        "weight": 600,
        "letter_spacing": 0.06,
    },
    # UI 字：参数、列表、输入、正文——沿用面板原字体（阿里妈妈数黑体
    # Bold，功能性优先：操作界面可读性比品牌字形更重要；用户 2026-11
    # 明确要求面板字体不改）
    "ui": {
        "families": ("Alimama ShuHeiTi", "Microsoft YaHei UI",
                     "Microsoft YaHei", "Segoe UI"),
        "size": 12,
        "weight": 700,
        "letter_spacing": 0.0,
    },
    # 等宽：路径、版本、帧数、代码、计数
    "mono": {
        "families": ("Consolas", "Courier New"),
        "size": 11,
        "weight": 400,
        "letter_spacing": 0.0,
    },
    # 文档正文：Recipe 文档阅读——沿用原方圆体 VF（有完整字重轴）；
    # docs/index.html 的正文仍用系统雅黑（网页独立）
    "doc": {
        "families": ("Alimama FangYuanTi VF", "Microsoft YaHei",
                     "Segoe UI"),
        "size": 15,
        "weight": 400,
        "letter_spacing": 0.0,
    },
}

# 字号阶梯（px）：11 caption / 12 dense / 13 body / 15 section /
# 18 window title / 24 hero；行高 16/18/20/24/28/32
FONT_SIZES = {
    "caption": 11,
    "dense": 12,
    "body": 13,
    "section": 15,
    "window_title": 18,
    "hero": 24,
}


def font_role(name):
    """取字体角色定义（未知角色 KeyError）。"""
    return FONT_ROLES[name]


# ---------------------------------------------------------------------------
# 间距 / 圆角 / 尺寸 / 动效
# ---------------------------------------------------------------------------
SPACE = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24}
RADIUS = {"control": 4, "card": 8, "dialog": 12, "pill": 999}
METRICS = {
    "window_padding": 16,
    "control_height": 28,
    "control_height_compact": 24,
    "row_height": 30,
    "focus_border": 2,
}
MOTION = {"hover_ms": 120, "panel_ms": 180, "easing": "OutCubic"}

# ---------------------------------------------------------------------------
# 用户内容色（Recipe 主题/标签、语法高亮）——8 色品牌邻近色板 + 中性
# ---------------------------------------------------------------------------
RECIPE_PALETTE = (
    "#ff5c38",  # 品牌橙
    "#e2571b",  # 焦橙
    "#f2a33c",  # 琥珀
    "#45b06e",  # 品牌绿
    "#2e8b57",  # 深绿
    "#5b9bd5",  # 冷蓝（仅内容色）
    "#9b8cd9",  # 雾紫（仅内容色）
    "#d16a7a",  # 玫瑰
)
NEUTRAL_CONTENT = TOKENS["TEXT_3"]

# 语法高亮（Recipe 代码块，深底可读；随主题换为品牌邻近色）
SYNTAX = {
    "keyword": "#c586c0",
    "type": "#4ec9b0",
    "func": "#dcdcaa",
    "string": "#ce9178",
    "comment": "#6a9955",
    "number": "#b5cea8",
    "directive": "#c586c0",
    "binding": "#9cdcfe",
    "builtin": "#9cdcfe",
    "text": "#d4d4d4",
    "thumb_bg": "#17171b",
    "gutter_text": "#55555c",
}

# 历史资源内容色（外部 SVG 原始 fill 等；仅用于着色替换，不作 UI chrome）
ASSET_COLORS = {
    "pin_svg_fill": "#e6e6e6",
}

# 文档阅读（Recipe Markdown）标题层级：颜色/字重与品牌橙体系对齐
DOC_HEADINGS = {
    1: (15.0, TOKENS["ACCENT"], 700, 10, 4),
    2: (12.5, TOKENS["WARNING"], 600, 8, 3),
    3: (11.25, TOKENS["SUCCESS"], 500, 7, 2),
    4: (10.5, "#5b9bd5", 500, 6, 2),
    5: (10.0, "#9b8cd9", 500, 6, 2),
    6: (9.5, "#d16a7a", 500, 6, 2),
}
DOC_BOLD_COLOR = TOKENS["WARNING"]
DOC_ITALIC_COLOR = TOKENS["SUCCESS_TEXT"]
DOC_LINK_COLOR = TOKENS["WARNING_TEXT"]
DOC_CODE_BG = TOKENS["INK_850"]
DOC_QUOTE_BG = TOKENS["INK_900"]
DOC_QUOTE_BAR = TOKENS["LINE_STRONG"]
