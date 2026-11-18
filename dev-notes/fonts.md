# 工具字体角色系统

适用：`python3.13libs/houtools/ui/fonts.py` + `houtools/fonts/`（AGENTS.md「工具统一字体」行的细节展开）。

## 角色与字族

| 角色 | 字族 | 用途 | 回退 |
|---|---|---|---|
| `ROLE_BRAND` | **DingTalk JinBuTi**（`DingTalk-JinBuTi.ttf`，随包） | **仅品牌资产**：docs 首页字标、README 头图、品牌手册 | 阿里妈妈数黑体 Bold → 系统 UI 字 |
| `ROLE_UI` | **Alimama ShuHeiTi Bold**（随包） | **全部工具面板**：参数、列表、按钮、输入、正文（用户 2026-11 拍板：面板字体不改，功能优先） | Microsoft YaHei UI → Segoe UI |
| `ROLE_MONO` | **Consolas** | 仅代码编辑器 / 代码块；面板路径/计数不再单独改 mono | Courier New |
| `ROLE_DOC` | **Alimama FangYuanTi VF**（随包） | Recipe 文档正文与 H1–H6 标题 | Microsoft YaHei → Segoe UI |

- 钉钉进步体来自网站品牌字体（woff2 → fontTools 转 TTF；Qt 不加载 woff2），
  2.1MB、7526 字形，随包分发；只用于品牌资产，不进工具面板。
- 面板 UI 字体沿用品牌统一前的阿里妈妈数黑体 Bold；方圆体 VF 继续作为
  Recipe 文档字。两个文件都随包，不删除。
- 首次加载失败只 warning 一次（字族名缓存挂 `QApplication` 动态属性，reload 不重装）。

## API

```python
from houtools.ui import fonts

fonts.apply(window)                       # 整窗默认 UI 角色
fonts.apply_role(widget, fonts.ROLE_BRAND)  # 字标/标题子树
fonts.apply_role(widget, fonts.ROLE_MONO)   # 仅代码编辑器/代码块
fonts.role_font("ui", base_font)            # 取 QFont（可保留字号）
fonts.brand_family()/ui_family()/mono_family()/doc_family()
fonts.mark_font_exempt(widget)              # 文档区豁免（自管字体）
```

`_stamp_tree` 递归遍历每个控件，**按最近祖先的 `_houtools_font_role` 属性**
选字族；复制控件当前字体只改字族/字重，字号由调用方/QSS 决定。

## 为什么必须逐控件显式 setFont

**会话实测（fxhoudini 探针）：Houdini 在应用层给控件类设了自家字体
SideFX Source Sans Pro，压过 Qt 的父子字体继承——只 setFont 窗口时子控件
仍是 SideFX**（`app.styleSheet()` 为空，非 QSS 所致；无头环境无此机制、
会正常继承，故**无头验证通过 ≠ 会话生效**）。

所以 `apply()` 必须**逐控件显式 setFont 刷整棵子树**（实测显式设置能压过
应用级类字体，A/B 抓图逐像素验证）。

## 动态创建的子控件

右键菜单/对话框/Automation 运行时加任务槽等动态子控件，由装在
`QApplication` 上的全局 ChildAdded 过滤器兜底：新控件挂在带
`_houtools_tool_window` 属性标记的窗口子树内，就按**最近祖先字体角色**
整枝刷字体（品牌区动态子控件也保持品牌字）。

## 度量基准

- **换字体后卡片度量基准必须跟窗口字体**：`_CardDelegate.text_block_height/_font`
  走 `_win.font()`（曾用 `QApplication.font()`，换字体后 gridSize 与绘制错位）。
- 品牌字只有单字面（Regular），标题加粗靠 `QFont.Weight.DemiBold`；正文
  不要依赖品牌字做 700。

## 文档阅读字体与豁免机制

- Recipe 文档区用 `ROLE_DOC`（方圆体 VF）；`_restyle_document` 给正文先刷
  `TEXT_2`（Qt setMarkdown 默认黑字在深底上隐形），标题按 H1–H6 各自颜色；
  `mark_font_exempt` 挂 `_houtools_font_exempt`
  动态属性，`_stamp_tree` 与 ChildAdded enforcer 双处沿父链尊重。
- **属性必须在挂树前设置**（`_DocPreview.__init__` 期设置，enforcer 的
  ChildAdded 才能赶在刷工具字体前看到）。
- 方圆体 VF（`AlimamaFangYuanTiVF-Regular.ttf`，wght 200–700 + BEVL 轴；
  woff/woff2 Qt 加载不了，只取 ttf）**仍是 Recipe 文档当前字体**。
  **文件已重铸（2026-11）：默认实例 wght=400**——原版默认实例是 wght200
  Thin，Qt 6.5（H21）不支持轴权重选择（`setWeight(400)` 是 no-op，只有
  权重偏离默认值才动轴），会把正文渲染成最细档；重铸后两版默认渲染亮
  像素数一致（755=755）。
- PySide6 6.8 坑：`QFont.setWeight` 只收 `QFont.Weight` 枚举不收 int；
  `QTextCharFormat` 下划线枚举在 `QtGui.QTextCharFormat.UnderlineStyle`
  （不在 QFont 上）；`QTextBlockFormat.LineHeightTypes.*.value` 取 int。
