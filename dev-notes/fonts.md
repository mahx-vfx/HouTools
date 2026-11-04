# 工具统一字体

适用：`houtools/ui/fonts.py` + `houtools/fonts/`（AGENTS.md「工具统一字体」行的细节展开）。

## 加载与范围

- 阿里妈妈数黑体 Bold（免费商用授权，用户指定）：`QFontDatabase.addApplicationFont` 私有加载（不装系统）。
- 四个工具窗口（recipelib/hdrlight/videoseq/automation）构造时各调一次 apply——不动 QApplication 全局字体，Houdini 自身 UI 不受影响。
- 字族名与过滤器安装标记挂 QApplication 动态属性（reload 不重复登记/装机）。

## 为什么必须逐控件显式 setFont

**会话实测（fxhoudini 探针）：Houdini 在应用层给控件类设了自家字体 SideFX Source Sans Pro，压过 Qt 的父子字体继承——只 setFont 窗口时子控件全是 SideFX**（`app.styleSheet()` 为空，非 QSS 所致；无头环境无此机制、会正常继承，故**无头验证通过 ≠ 会话生效**）。

所以 `apply()` 必须**逐控件显式 setFont 刷整棵子树**（实测显式设置能压过应用级类字体，A/B 抓图逐像素验证）。

## 动态创建的子控件

右键菜单/对话框/Automation 运行时加任务槽等动态子控件，由装在 QApplication 上的全局 ChildAdded 过滤器兜底：新控件挂在带 `_houtools_tool_window` 属性标记的窗口子树内就整枝刷 `root.font()`。

## 度量基准与新增字重

- **换字体后卡片度量基准必须跟窗口字体**：`_CardDelegate.text_block_height/_font` 走 `_win.font()`（曾用 QApplication.font()，换字体后 gridSize 与绘制错位）。
- 新字重往 `_FONT_FILES` 追加同族文件（字体自带 Bold 字面，setBold 才真正可见）。

## 文档阅读字体与豁免机制（2026-11）

- **Recipe Library 文档区专用阿里妈妈方圆体 VF**（`AlimamaFangYuanTiVF-Regular.ttf`，可变字体，wght 轴 200–700 + BEVL 轴；woff/woff2 Qt 加载不了，只取 ttf）：正文 Regular、加粗 Bold、标题 SemiBold 档（H22 经轴精确取 650/620/580）。`doc_family()`/`doc_font()` 与 `_tool_family` 同构，字族名缓存挂 app 动态属性（reload 不重装）；`doc_font` 显式钉 Weight.Normal（兜底语义，见下）。
- **★ 文件已重铸（2026-11 H21 文档区"更暗更灰"根因修复，详见 `dev-notes/recipelib.md`）**：原版 VF 默认实例 = wght200 Thin，而 Qt 6.5（H21）不支持轴权重选择——`setWeight(400)` 是 no-op（400 是 QFont 默认权重值，只有偏离默认才动轴），正文被渲染成最细档；**"钉 Normal 能避开 Thin"只在 H22（Qt 6.7+ 会把请求权重映射到轴坐标）上成立**。fontTools partial instancing 把 wght 默认移到 400（双轴全保留）后，H21/H22 双引擎 Normal 渲染亮像素数一致。教训：可变字体默认实例的行为必须 H21/H22 双引擎实测，单版验证的假设不可信。
- 为什么文档区不共用数黑体：数黑体只有粗字面，`**加粗**` 与正文零对比（markdown 导入器只给字重 700，同一字面看不出）；阅读正文本就该用有完整字重的字体。
- **豁免机制**：`mark_font_exempt(widget)` 挂 `_houtools_font_exempt` 动态属性，`_stamp` 与 ChildAdded enforcer 双处尊重（沿父链 `_is_exempt` 判定）。**属性必须在挂树前设置**（`_DocPreview.__init__` 期设置，enforcer 的 ChildAdded 才能赶在刷工具字体前看到）；工具窗口 `apply()` 跑在 preview 创建之前也不怕——挂树瞬间 enforcer 会刷，豁免属性就是拦这个的。
- PySide6 6.8 坑：`QFont.setWeight` 只收 `QFont.Weight` 枚举不收 int；`QTextCharFormat` 下划线枚举在 `QtGui.QTextCharFormat.UnderlineStyle`（不在 QFont 上）；`QTextBlockFormat.LineHeightTypes.*.value` 取 int（`int(enum)` 不支持）。
