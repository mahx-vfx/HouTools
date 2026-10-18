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
