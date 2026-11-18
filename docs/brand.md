# HouTools 品牌手册（Brand Guide）

**产品名**：HouTools（一个词，H/T 大写；不写 Hou Tools / Houtools）。
**方向**：深夜技术工作台——墨黑、品牌橙、深绿蓝图、1.5px 精确线条、克制动效。
**原则**：同一套品牌，不同密度；橙色是动作色不是背景色（单屏橙色 ≤ ~10%）；
品牌来自系统（颜色/字阶/间距/圆角/图标一致），不来自装饰。

> 唯一 token 源：`python3.13libs/houtools/ui/theme.py`。
> 改品牌只改 token，不在各窗口重写颜色。除该文件外，Python UI 代码不得写裸 hex。

---

## 1. 色彩

| Token | 值 | 用途 |
|---|---|---|
| `INK_1000` | `#05050a` | 窗口外缘 / About/docs 主背景 |
| `INK_950` | `#0a0a0f` | 工作区 / 面板底 |
| `INK_900` | `#101017` | 卡片 / 输入 / 列表底 |
| `INK_850` | `#15151c` | hover / 弹层 / Toast 底 |
| `INK_800` | `#1c1c25` | 强 hover / 次级按钮 hover |
| `LINE` | `#262631` | 1px 常规边框 |
| `LINE_STRONG` | `#34343f` | hover 边框 / 分隔 |
| `TEXT` | `#f0f0f3` | 标题 / 正文 |
| `TEXT_2` | `#b8b8c2` | 说明 / 标签 |
| `TEXT_3` | `#8a8a92` | 提示 / 占位 |
| `TEXT_4` | `#5c5c66` | 禁用 |
| `ACCENT` | `#ff5c38` | 焦点 / 选中 / 关键值 / 主按钮悬停 |
| `ACCENT_FILL` | `#bf380d` | 主按钮常态底（暗橙，白字过 AA） |
| `ACCENT_FILL_PRESSED` | `#a02e08` | 主按钮按下 |
| `ON_ACCENT` | `#ffffff` | ACCENT_FILL 底上的文字（白字） |
| `ACCENT_HOVER` | `#ff7454` | hover |
| `ACCENT_PRESSED` | `#e64a2a` | pressed |
| `ACCENT_SOFT` | `rgba(255,92,56,.12)` | 选中行底 / tab 底 |
| `ACCENT_LINE` | `rgba(255,92,56,.35)` | 选中描边 / 弱强调 |
| `SUCCESS` | `#45b06e` | 成功 / 完成 / 进度 |
| `SUCCESS_TEXT` | `#6ee7b7` | 深色底成功文字 |
| `WARNING` | `#f2a33c` | 警告 / 待处理 |
| `WARNING_TEXT` | `#ffd08a` | 深色底警告文字 |
| `ERROR` | `#d1283e` | 错误图标 / 边框 |
| `ERROR_TEXT` | `#ff7a7a` | 深色底错误文字 |
| `GRID_LINE` / `GRID_DOT` | `#2e8b57` / `#45b06e` | 仅品牌背景网格 |

规则：

- 主按钮 = `ACCENT_FILL` 暗橙底 + `ON_ACCENT` 白字（对比度 5.5 过 AA）；
  悬停点亮为 `ACCENT` 品牌亮橙、「点火」式交互，按下压深 `ACCENT_FILL_PRESSED`
  （2026-11-19 用户拍板：功能性观感优先，橙底白字/白字发虚/墨黑字方案均废止；
  亮橙底不承载文字——选中高亮文字用墨黑）。
- Info 语义不引入新色相：中性 `INK_850` 面 + `TEXT_2` 字 + 橙/中性图标。
- 蓝 `#0d6399`、紫 `#8a5cf5` 已从 UI chrome 退役；只允许作为 Recipe 用户内容色
  （8 色板：`theme.RECIPE_PALETTE`）。
- 状态不能只靠颜色：必须同时有图标或文字。
- 错误文字用 `ERROR_TEXT`，错误图标/边框用 `ERROR`。

---

## 2. 字体

| 角色 | 字体 | 字号 / 字重 | 用途 |
|---|---|---|---|
| `brand` | DingTalk JinBuTi（随包 TTF） | 15–24px / 600 | 仅品牌资产：docs 首页字标、README 头图、品牌手册 |
| `ui` | **Alimama ShuHeiTi Bold**（随包 TTF，单一 Bold 字面） | 11–13px / 700 | 全部工具面板：参数、列表、按钮、输入、正文 |
| `mono` | Consolas | 11–12px / 400 | 仅代码编辑器/代码块（面板路径保持 UI 字，不单独改 mono） |
| `doc` | **Alimama FangYuanTi VF**（随包 TTF） | 15px / 400 | Recipe 文档正文与标题 |

- **用户 2026-11 拍板：工具面板字体不改**——操作界面以功能性与清晰度为先，
  面板继续用品牌统一前的阿里妈妈数黑体 Bold；只有品牌资产（docs 字标、
  README 头图、品牌手册）使用钉钉进步体。
- 字号阶梯：`11 caption / 12 dense / 13 body / 15 section / 18 window title / 24 hero`；
  行高 `16/18/20/24/28/32`。
- 字重只用 400/500/600；正文不用 700。
- 中文不加字距；英文产品名/工具名可加 `0.04–0.08em`。
- 产品字标固定 **`Hou` 白（TEXT）+ `Tools` 品牌橙（ACCENT）**，两段同字号、同基线、无额外间隙。
- **About 页字标使用固定尺寸 `docs/brand/houtools-wordmark.png`，不依赖运行时字体加载**：否则 woff2 加载前后字宽变化会带动版本胶囊左右跳，`font-display:block` 还会先黑一下；图片有明确宽高，布局零位移。
- 品牌字缺失时回退阿里妈妈数黑体 Bold（文件保留），再回退系统 UI 字。
- **逐控件 `setFont` + QApplication ChildAdded 兜底不可退化为父子继承**：
  Houdini 在应用层给控件类设了 SideFX 字体，压过继承。

代码入口：

```python
from houtools.ui import fonts
fonts.apply(window)                  # 整窗 UI 角色
fonts.apply_role(widget, fonts.ROLE_BRAND)   # 仅品牌资产字标
fonts.apply_role(widget, fonts.ROLE_MONO)    # 仅代码编辑器/代码块
```

---

## 3. 间距 / 圆角 / 边框 / 图标 / 动效

| 项 | 值 |
|---|---|
| 基准 | 4px；间距 4 / 8 / 12 / 16 / 24 |
| 窗口内边距 | 16 |
| 控件高 | 28（紧凑 24） |
| 行高 | 30 |
| 圆角 | 控件 4 / 卡片面板 8 / 对话框 12 / 胶囊 999 |
| 边框 | 常规 1px；选中/焦点 2px |
| 图标 | 24 网格、1.5px 描边、圆角端点；14/16/20/24 四档 |
| 动效 | hover/press 120ms；面板 180ms；OutCubic；不弹跳、不无限漂移 |
| 焦点 | 2px `ACCENT` 边框（QSS 无 outline）；主按钮用 2px `FOCUS_RING` 浅橙 |
| 阴影 | 工作区不用重阴影；浮层 1px 边框 + 轻手绘阴影 |

图标规则：

- 侧栏图标默认 `TEXT_2`/`TEXT_3`；选中 `ACCENT`。
- 收藏星保留 `WARNING`，高版本警告角标保留 `ERROR`。
- 官方 Houdini 图标只作为 Recipe 卡片水印（0.18–0.22 透明度），不进 chrome。
- 产品图标 = `icons/brand_product.svg`（20px 单色线稿），不新造复杂标志。

---

## 4. 共享组件（`ui/components.py`）

| 组件 | 规格 |
|---|---|
| `BrandHeader` | **不在工具面板使用**（用户 2026-11 反馈：面板功能优先，不新增次级品牌顶栏）；组件仅保留给品牌资产/未来独立页。字标规则：`Hou` 白 + `Tools` 品牌橙，同字号同基线 |
| `SectionHeader` | 13/600；可选 2px `ACCENT` 左条；底部 1px `LINE` |
| `PrimaryButton` | `ACCENT_FILL` 暗橙底 + `ON_ACCENT` 白字，悬停点亮 `ACCENT`；高 28；圆角 4；13/600 |
| `SecondaryButton` | `INK_850` 底 + `TEXT` + 1px `LINE`；hover `INK_800` |
| `GhostButton` | 透明底 + `TEXT_2`；hover 5% 白 |
| `DangerButton` | 透明底 + `ERROR_TEXT` + 1px `ERROR` |
| `Pill` | 11px；999 圆角；中性 6% 白底；语义色 14% 透明底 + 同色文字 |
| `StatusDot` | 8px 实心圆；必须配文字/图标 |
| `EmptyState` | 48px 线稿图标 + 15/600 标题 + 12 `TEXT_3` 提示；可铺 6% 网格 + 四角点 |

按钮变体统一走动态属性：

```python
btn.setProperty("htRole", "primary")   # primary / ghost / danger
```

QSS 选择器优先 `objectName` / `[htRole="..."]`，不依赖类型选择器压 Houdini 全局样式。

---

## 5. Toast / 状态

- **结构/时序/槽位/渐透/补位全部保持不变**（用户拍板）；只换颜色与图标。
- 底色 `INK_850` + 1px `LINE` + 左侧 2px 语义色条；文字 `TEXT`。
- 语义色：success `SUCCESS`、warning `WARNING`、error `ERROR`、info `TEXT_2`。
- 状态栏：11px `TEXT_3`；成功 `SUCCESS_TEXT`、失败 `ERROR_TEXT`；脉冲机制保留。

---

## 6. Recipe Markdown 标题

H1–H6 全部有独立配色（8 色内容板），正文固定 `TEXT_2`：

| 级别 | 颜色 | 字号 |
|---|---|---|
| H1 | `ACCENT #ff5c38` | 15.0 |
| H2 | `WARNING #f2a33c` | 12.5 |
| H3 | `SUCCESS #45b06e` | 11.25 |
| H4 | `#5b9bd5` | 10.5 |
| H5 | `#9b8cd9` | 10.0 |
| H6 | `#d16a7a` | 9.5 |

标题与正文均用 `Alimama FangYuanTi VF`（原文档字），不再使用系统雅黑/钉钉进步体。

## 7. Recipe 卡片

- 卡片底 = `INK_900` + 1px `LINE` + 8 圆角 + 12 padding；hover `INK_850` / `LINE_STRONG`。
- 主题色只做 3px 左竖条 + 标签点 + **≤0.10** 的极淡底；选中态统一
  `ACCENT` 2px 边框 + `ACCENT_SOFT`，不再用卡片主题色做选中。
- 文字层级：名称 13/600，元信息 12/400，标签 12/400（不再全 Bold）。
- **网格几何（右对齐、`2×GAP`、滑条尺寸、卡间距）保持用户拍板形态，不得改动。**

---

## 8. 文案

- 菜单标签一律英文/ASCII（H22 中文菜单渲染不可靠）；窗口内部 UI 可用中文。
- 工具名固定：`Automation`、`Video to Sequence`、`HDR Library`、
  `Recipe Library`、`HouTools Settings`。
- 菜单省略号用 ASCII `...`；不在菜单里混用 `…` 与 `...`。
- 不出现个人品牌署名、个人组合标、作者 tagline、“by …”。
- 现有 GPL 版权声明保留。

---

## 9. Do / Don't

**Do**

- 工具面板保持功能优先：不新增品牌顶栏/版本条；品牌字标只出现在 docs/README/品牌资产。
- 新控件先问「有没有共享组件/token」；没有再加。
- 用 `theme.color("TOKEN")` / `qss.window_qss()`，不写裸 hex。
- 每个工具窗口只有一个橙色主按钮（Settings 的逐行「应用」按 5.5 规范例外）。
- 状态同时给颜色 + 图标/文字。
- 改窗口 UI 后重跑 `tests/gen_about_shots.py` 与 `tests/gen_brand_assets.py`。

**Don't**

- 不要在工具面板顶部新增次级品牌栏/版本条（用户 2026-11 明确否决）。
- 不把橙色铺成大面积背景；不做重阴影/发光堆叠。
- 不引入蓝/紫 UI chrome（内容色除外）。
- 不把海报墙/星空/涟漪搬进密集工作区。
- 不引 CDN/在线字体/在线图片；docs 必须完全离线。
- `docs/index.html` 的 `@font-face` 必须 `font-display: block`：`swap` 会在刷新时先用回退字再跳成钉钉进步体；首屏字标已改用固定 PNG，正文标题仍保留 block。
- 不改菜单 ID、热键符号、工具注册 ID、数据格式、线程/执行逻辑。

---

## 10. 验证

```text
python tests/test_theme_tokens.py
python tests/test_fonts.py
python tests/smoke_test.py
python tests/gen_about_shots.py
python tests/gen_brand_assets.py
```

验收要点：H21/H22 冒烟通过；无逐帧 `setStyleSheet`；docs 零外部 URL；
100/125/150/200% DPI 下 11/12/13px 可读。
