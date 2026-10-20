# Recipe Library（recipes 资产面板）

适用：`houtools/recipelib/`（store/metadata/docs/capture/browser/transfer）+ `tools/recipe_library.py`（AGENTS.md「Recipe Library」行的细节展开；代码块部分见 [codeblock.md](codeblock.md)，缩略图裁剪/截取见 [thumb-crop.md](thumb-crop.md) / [thumb-snip.md](thumb-snip.md)）。

## 库加载与扫描（store）

- 基于 H22 官方 recipes 的资产面板，**只加载用户库**：库 = 面板「设置 ▸ 库目录」配置的若干文件夹（`settings/recipelib.json` 的 `lib_dirs`，可多个，递归扫 `.hda`），出厂（$HFS）/Labs/偏好目录默认库一律不显示；`uiready.py` 启动即装库（Tab 菜单里的 session tool 从会话开始可用）。
- **坑：`saveToolRecipe` 每次保存都在 `<库文件夹>/backup/` 写 `HouToolsRecipes_bakN.hda`**，扫描必须剪掉 backup 目录——装进备份会让已删 recipe"复活"并与现行定义同名冲突。创建走 Houdini 官方保存流程（面板纯展示，不做保存）。
- **删除语义（无头实测）**：`HDADefinition.destroy()` 删掉文件里最后一个定义时 Houdini 自动卸载该库、`definitionsInFile` 对未加载文件抛 OperationFailed、磁盘留 83B 空壳——删后按"读不到定义（含 OperationFailed）= 已空"处理：卸载+删空壳；有幸存定义则文件保持加载、内容完好。
- 扫描安装的防护（2026-10-17 用户拍板收窄）：**扫描全程不删任何磁盘文件**——装不上的文件、读不到定义的空壳一律只卸载还原会话 + warning（原 <512B 空壳自愈已撤：lib_dirs 可能指向共享盘/生产目录，未经确认的自动删除不可接受），文件保留待用户手动清理；误放进来的非 recipe .hda（无 data 类定义）装入后立即卸载还原（不删文件）。

## 数据读取（公开 API）

- 枚举/元信息走公开 API：`recipeutils.recipeNames(RecipeCategory.xxx)` + `hou.nodeType(hou.dataNodeTypeCategory(), name).definition()` 直读 `data.recipe.json` section（不依赖内部包 hrecipes）。
- **坑：`data.recipe.json` 的 `data` 键不总是 dict**（ramp 类是 list、recipebuilder 是 str），必须 isinstance 防护——否则官方出厂 recipe 的 header 读取全线崩、告警刷屏。
- **label/submenu 的实际存储随 recipe_category 不同**（22.0.429 实测，与 recipe_format 文档页有出入）——tab_tool/preset 类在 `properties.label/submenu`，shelf 式 tool 在 `tool.tool_labels[]`/`tool.tab_submenus[]`。
- **节点数**：照官方 `hrecipes.utils.countContentsOfItem` 语义自实现（children/editable_nodes 递归累计、subnetindirectinput 不计，与 `hou.data.dataFromRecipe` 实测一致）。
- **版本号**：`info.houdini_version` 官方保存时经 `hou.applicationVersionString()` 写入（官方 354 个出厂 recipe 全带、跨 20.5~22.0 实测），**显示完整版本号**（如 22.0.429，不做 H22.0 缩写）。
- **显示链 = 用户自定义显示名（`metadata.display_names`，支持中文；双击预览名称或右键「自定义显示名」设置，清空即恢复）> 官方 label > 内部名末段**（官方保存时 label 常为空，如 mahx::my_copy_test → my_copy_test）；预览面板元信息第一行固定显示 `内部名称:` 全名。

## 侧栏（QTreeWidget + _SidebarDelegate）

结构：

- 左侧栏 = QTreeWidget 树（自绘 `_SidebarDelegate`，参考外部蓝图管理面板 UI）：全部/收藏/代码块/节点参数/子菜单▸/标签▸/层级▸——**「代码块」「节点参数」两行恒显（空段计 0，2026-10 用户指定统一语义）**。
- 段头不可选中、itemClicked 整行切换折叠（箭头由 delegate 画行内，`setRootIsDecorated(False)`），段头琥珀色计数=段内条目数、普通行灰计数=recipe 数、**段头收拢态整行调暗**（文字 #8f8f97/计数 #a8824e/箭头 #6a6a72，展开态 #d8d8de/#d29a55/#8a8a92——"已折叠"的视觉反馈，背景不动保持全栏统一）。
- **无 submenu 的 recipe 归「节点参数」（键 `cat::` 空串，`_match_category` 判 `not _submenus_of`）**；**「层级」段按库内实际出现的 `net_category` 动态生成（键 `net::Sop` 等，无层级的 recipe 不入段），过滤为 `net_category` 精确等值**。
- 折叠状态存 `recipelib_ui.json`（grp_expanded/tag_expanded/level_expanded）。

显示开关：

- **侧栏顶部三个并排显示开关（2026-10 用户指定，「全部」行上方，`_TYPE_TOGGLES`）**：工具/代码块/参数 三枚 checkable 按钮（「参数」=节点参数缩写；图标与对应侧栏行同款）。
  - 工具 = 工具箱 `icons/toolbox.svg`（用户提供矢量图，去 iconfont 淡色装饰圆、定稿 D1 原配色：白箱体 + #F88E20 橙 + #441B00 描边；子菜单子行与「工具」开关共用同一种类）。
  - 标签 = **`icons/tag.svg`**（用户提供矢量图，**已水平镜像**——path 数据不动、外包 `<g transform="translate(1024,0) scale(-1 1)">` 烘焙进文件，孔位朝右上、2026-10 用户指定；**填充永远整体替换成标签色**——无自定义色默认蓝 #5b9bd5，文件原配色 #FCBD3F 不参与显示；delegate 侧图标 tint 与行文字同过 `readable_on_dark` 提亮，过暗自定义色填充大面积后必须提亮）。
  - 代码块 = code、参数 = sliders。
- 两枚矢量图由 `ui/sidebar.py` 的 `_SVG_ICONS` 注册表统一管理（QSvgRenderer 直渲染同 favorite_badge 先例、缩放无损；SVG 画布留白 ~17%，渲染前 `setViewBox` 裁到内容外扩 6 单位；fill 整体替换用正则改 SVG 文本实现，QSvgRenderer 无逐元素换色 API，渲染器按 (kind, 替换色) 缓存——toolbox tint=None 用原配色、tag 永远传具体色；QtSvg/文件缺失降级手绘线稿 `_draw_folder_icon`/`_draw_tag_icon`）。
- 实心图形同 px 下比线稿显小，矢量种类行/开关渲染 +2px（`SVG_ICON_BOOST`，`SVG_ICON_KINDS` 判定，delegate 的占位 y 与 x 推进量同步用实际尺寸、防大图标压住文字；开关按钮尺寸记在 `ht_icon_px` 属性）。
- objectName `sidebarToggleBtn`，QSS 在窗口 STYLE_SHEET，关闭态文字/图标调暗同段头收拢约定，**悬停高亮=刷新/设置同款**（背景提亮 #3d3d3d+蓝描边，`:checked:hover` 再亮一档；QSS 特异性 `:hover`/`:checked` 相同后写者胜，规则顺序即语义：基础→!checked→hover→checked→checked:hover）。
- 开启显示对应类型卡片、关闭隐藏——`_type_visible` 全局过滤（所有分组视图的网格条目与侧栏各行计数都按它算），默认全开、存 `recipelib_ui.json` 的 `show_tools/show_code/show_nodeparm`；切换走轻量重建（落盘 + `_rebuild_sidebar` + `_apply_filter`，不重扫库文件，承接原「常规」页手法）。

容器与底色：

- 侧栏树外套 side_panel 容器挂进 splitter，`win.sidebar` 引用不变，**宽度约束（min150/max280）必须挂 side_panel**——QSplitter 只认直接子项的 min/max，树上的旧约束管不住容器（不挂则拖宽分割条时左栏无限变宽、开关条与树脱宽，2026-10 用户反馈）；NoFocus 不抢侧栏键盘焦点。
- **全栏底色必须统一且为亮一档 `#26262b`**（用户指定，比面板标准色 #1D1D20 亮；文字整栏加粗提亮，行文字 #e2e2e8/段头 #d8d8de/计数 #a8a8b0）：行区由 Qt 行绘制用 palette base 填、行下空白区是 viewport 自身底色、**子行左侧还有 branch 竖带**（行 rect 从缩进处开始），Houdini 全局样式下三者色值不一（离屏复现需给 app.setStyleSheet 模拟深色 branch）——三保险：`setIndentation(0)`（branch 几何彻底消失，子项缩进由 delegate 行内自画 `_SidebarDelegate.INDENT`）+ viewport 显式设 `#26262b` + delegate 整行铺 `#26262b`。
- **坑：`QTreeWidgetItem(str)` 在 PySide6 命中 QStringList 重载、把中文标签按字符拆进多列（"全部"→列0="全"），必须空构造后 setText**。
- 行首图标（grid/folder/tag）QPainter 手绘，**收藏行 = 卡片同款 `icons/favorite_badge.svg`**（QSvgRenderer 渲染，QtSvg 缺失回退手绘星），按 (kind,size,dpr) 缓存。

右键行为：

- **标签行右键「自定义颜色」**（`_on_sidebar_context_menu` 仅对 icon kind=="tag" 的行弹菜单）→ `_set_tag_color`。
- **侧栏右键不跳转**（`_SidebarTree` 吞掉右键 press——QAbstractItemView 默认任何键按下都设 current，右键即切分组，2026-10 用户指定右键仅弹菜单；菜单事件由窗口系统合成、不受 press 吞掉影响；无头测试合成按键必须走 `QApplication.sendEvent`——viewport 鼠标事件靠 QAbstractScrollArea 的 eventFilter 转发，`viewport.event()` 直调会绕过过滤器到不了树控件，假通过）。

## 网格与卡片

- **网格 UniformItemSizes 必须关**（离屏渲染实测：缩略图原图尺寸超过 iconSize 时——竖版 GIF/PNG 常见——条目 sizeHint 按原图计算，Uniform 按首条目尺寸强行裁剪，表现为该条目文字标签整个不可见、图标比例失真；占位图给 256px 档防大网格下不放大）。
- **卡片在格内右对齐、格宽固定（用户多次强调的最终形态，勿改）**：格宽 = 卡宽 + 2×GAP **恒定**（不随面板宽变化，无任何自适应重排）；卡片右对齐绘制（右缘贴格边）→ 卡片右缘恒等于 列数×格宽，与 Qt 换行判定（分界线 < 列数×格宽）天然重合——分界线触碰到卡片右缘的那一像素才换行；**卡间距恒定 2×GAP = 8px（GRID_CARD_GAP=4，用户指定收紧一半）、卡片大小只由滑条决定，均不随面板宽窄变化**——面板宽窄只改变每排列数，右侧余白留在分界线旁（不够一张卡+间距就不动，用户接受）。
- **用户否决过的方案不要再提（均为事故）**：改变卡片大小的收缩/拉伸（卡片是刚体）、间距随视口伸缩的均分方案（间距是刚体，用户称"间距变动=严重事故"）、居中/左对齐（换行点比触碰点早 6~14px，用户连续否决）、按视口平分格宽的自适应（引入视口-滚动条反馈回路=排列乱跳/闪动，活会话实测，且必然改变卡片大小或间距）。
- 网格条目 = `_CardDelegate` 全自绘卡片（不调父类 paint）：缩略图 **cover 裁剪铺满**（`window.thumb_pixmap` 按 (名,宽,高) 缓存 cover 后的 QPixmap——contain 会在图片比例与框不符时留灰边，cover 裁中心；框选裁剪与 cover 锁同一比例，所选即显示）+ 四行文字「颜色竖条+名字 / 类型•节点数 / 版本 / 标签」**全部加粗**（text_block_height 的度量与 paint 的 setFont 必须同步 bold，否则 gridSize 与实际绘制错位），选中时边框加粗；`text_block_height()` 与 paint 的行序公式必须一致，gridSize 由它推导。
- **颜色 = 整卡主题**（参考官方 Recipe Manager 卡片）：自定义色同时用于卡片边框（选中同色加粗 2px）、名字竖条；背景铺主题色混暗底（`_tint`）——文字区 alpha 0.35、缩略图区 0.12 分层次；**标签行文字不随主题色**（固定 #eeeeee 同显示名文字色，2026-10 用户指定，原 `_readable` 暗色提亮逻辑随之删除）；默认（未设色）灰主题 = 边框 #9a9aa2/竖条白；取色对话框抽 **`_pick_color` 卡片/标签共用**（非原生 QColorDialog + 中文按钮 + 「恢复默认」清除标志语义）。
- **标签颜色（2026-10 用户指定，同卡片主题色的交互模式）**：`metadata.tag_colors` 按标签名持久化（`set_tag_color` 只收 #rrggbb、其余视为清除；删 recipe 不清理——颜色跟随标签而非配方，残留无害）；有色标签**三处显示全部生效**——侧栏标签行（`ui/sidebar.py` 新增共享角色 `SIDEBAR_COLOR_ROLE`：行文字用可读化标签色、图标线稿整体着色 `sidebar_icon_pixmap(tint=...)`，HDR 面板不带角色零影响）、卡片标签行**逐 token 着色**（无色标签固定 #eeeeee；超宽整体 clip 裁切不做逐 token 省略）、预览标签栏富文本逐标签着色（QLabel 自动识别 rich text，标签名过 `html.escape` 防注入）；过暗色统一 `readable_on_dark`（ui/sidebar.py，lightness<90 提亮 180）保证深底可读；**选中态统一 = 主题色加粗 2px（默认灰主题与自定义色同规则，用户指定不另设蓝色选中边框）**。
- 官方 recipe 数据无 recipe 级颜色字段（rtypes 的 color 是单个网络条目的），故存 `metadata.colors`（右键「自定义颜色」QColorDialog——**清除颜色的入口在对话框内「恢复默认」按钮 + 确认**：置清除标志、期间再选其他色自动撤销；菜单里没有清除颜色项。`set_color` 只收 #rrggbb，删除 recipe 时清理）。
- 标签行显示 "#标签"（# 前缀、空格分隔，参考图同款），逐标签按各自颜色着色（无色固定 #eeeeee）；预览面板标签栏**面板框随卡片主题色**（`_apply_tags_panel_theme`：背景主题色混暗底 0.35 + 同色边框，未选中/未设色默认灰——2026-10 用户反馈恢复框的主题化），框内文字富文本逐标签着色（无色 #e8e8ec，加粗 14px——文字不随卡片色，只随标签色）。
- **「节点参数」卡片水印**：无 submenu 的卡片在卡片**最底层**（背景铺色之后、缩略图/文字之前）铺侧栏「节点参数」行同款滑块图标——`_draw_watermark` 复用 `ui/sidebar.py` 的 `sidebar_icon_pixmap("sliders")`（自带 (kind,size,dpr) 缓存，无需另建缓存），0.22 透明度、**右对齐且垂直居中于文字区**（居中放会和文字重叠打架，实测弃用；尺寸 = min(文字区高×0.55, 文字区宽×0.6) 夹在 18..48）；缩略图不透明故水印实际只在文字区/边距透出；判据 `not _win._submenus_of(info)` 与 `cat::` 过滤同源；smoke test 以"有/无水印两版渲染整图像素 diff"固化（节点参数卡必有差、其他类型必无差）。
- 卡片信息编排对照官方 Recipe Manager：去掉"使用次数"，类型只在第二行出现一次（标签行不再重复网络类别）。

## 元数据与缩略图管线

- 元数据（标签/收藏/缩略图路径）存 `settings/recipelib.json`、缩略图文件复制进 `settings/recipe_thumbs/`（有意不写 HDA section：元数据与库文件解耦）。
- **缩略图路径存相对插件根的相对路径**（`_to_stored`/`_to_abs` 转换，get 返回可用绝对路径；旧版绝对路径由 `migrate_legacy_thumb_paths` 在 reload 开头幂等迁移，跨盘退回绝对；项目整体挪动后仍有效——文档路径本就不持久化，每次从 `__file__` 推导，无此问题）。
- `thumb_pixmap` 的 QIcon 请求尺寸跟卡片实际显示（max(w,h)×dpr），**不要固定 1024**——QIcon 按请求尺寸缓存位图，大图每 recipe 常驻近原图分辨率；`_cover_cache` 上限 512 条、滑动条拖动中不清（sliderReleased 才清，拖动中全清会让可见卡逐帧重裁剪）。
- **收藏标识 = 卡片右上角角标**（与 HDR 面板共用 `houtools/ui/badge.py` 的 FavoriteBadge：QtSvg 本体 + QGraphicsDropShadowEffect 高斯投影，名字前缀不加星号；recipelib 的角标由 _CardDelegate 在 paint 时用 `badge_pixmap` 贴**缩略图区右上角=卡片内容区角**——**不能合成进缩略图 QIcon**（HDR 面板仍是合成模式）：缩略图 contain 留白时角标会随图片自身角落漂移、贴不到卡片角，故网格图标一律用 `_base_icon`，无缓存失效问题）。
- crop 预览从显示图 `_disp` 重采样（每 mouseMove 对原图整块 copy 会卡），`result_pixmap()` 确认时才走原图。

## 预览面板

- **预览面板随分割条铺满整格**（原 `setFixedWidth(272)` 在分割区更宽时右侧留白一截；`PREVIEW_W` 只作最小宽度可拖动），预览缩略图宽度跟随面板（`_PreviewImageLabel.resized` → 重渲染），尺寸变化从原图缓存 `_preview_pm` 重采样（防缩放帧再放大发糊；GIF 重算 `setScaledSize` 后 `jumpToFrame(当前帧)` 强制重投，不等下一帧播放）。
- **预览元信息卡片内标签（preview_meta + 四个 meta_cell）一律不折行且行高钉死**（wordWrap 标签高度随文本/宽度取整，空态与选中态之间差 1px——会话探针实测卡片 97↔98、"版本/焦点"行跳动；recipe 内部名是无空格 token 本就不可断行，超宽值横向裁切不撑高）。
- 预览面板网络+节点数合一行（`网络: Sop • 1 节点`，同卡片第二行格式）。
- 预览底部**只保留只读标签展示**（`tags_view` QLabel，# 前缀同卡片）——「标签设置」走**标签栏右键菜单**（`_on_tags_context_menu` → `_edit_tags`；外层 QFrame/内层 QLabel 两层都接 `customContextMenuRequested`，空态无选中不弹；原右下角按钮已取消，2026-10 用户指定；弹窗修改确认才写，无标签显示"（无标签）"）。
- **卡片右键菜单也有「标签设置」（2026-10 用户指定）**：网格右键的 Qt 原生行为会先改选中再弹菜单（**用户期望保留**，2026-10 拍板——右键即选中、预览跟随，省一次左键；与侧栏「右键不跳转」是两套约定，勿混淆）；`_edit_tags(info)` 传命中卡片的 info（`itemAt(pos)`），写入目标以命中卡为准、不依赖选中迁移时序（无参回退 `_selected_info()`，标签栏右键入口仍走无参）。
- 收藏/编辑文档/设置缩略图等入口都在右键菜单（应用=双击/拖拽卡片），面板不放重复按钮。`_NameDialog` 已通用化为 `_PromptDialog`（自建 label+QLineEdit+确认/取消，QInputDialog 会在显示时用平台文字重置按钮）。
- **预览缩略图点击弹大图查看器**（`_ImageViewerDialog`）：初始 contain 到屏幕可用区域 85%，**滚轮缩放**——`_ZoomScrollArea` 拦截 wheel 转发步进、适配尺寸的 0.2~8 倍钳制、从原图重采样平滑，**视口中心锚定**：内容点 = 视口中心 − label.pos（label.pos 已含滚动+居中偏移，滚动条 value 不进公式会双重计算），缩小到小于视口时手动居中摆放（QScrollArea.alignment 单向不足时不可靠），**中键按住拖动平移视框**（Houdini 网络编辑器同手感：ClosedHand 光标、内容跟随抓取，平移经滚动条实现）；滚动条常驻占位（视口尺寸恒定，锚定不因滚动条出现/消失跳变）；双击或 Esc 关闭，单击不关闭防平移误触；无缩略图时光标不提示不可点。
- **GIF 大图**：`_show_movie_frame` 在 frameChanged 时手动缩放当前帧到 label——**坑：GIF 解码器不支持 QImageReader 缩放**，`QMovie.setScaledSize` 对 GIF 无效（帧按原始尺寸交付，jumpToFrame 取缓存帧也无效），必须不 `setMovie` 改为手动 `frameChanged` 缩放 `currentPixmap`；`currentPixmap()` 返回的也是原始尺寸帧，验证显示效果只能 grab 像素测量或目检。

## 右键菜单体系（坐标系坑）

- **预览缩略图区 preview_label 右键 = 缩略图设置入口**（`browser._on_thumb_context_menu`：设置缩略图/截取缩略图/清除缩略图（仅有缩略图时），与卡片右键同一套 handler，`_clear_thumb` 的 item 可传 None——预览区无命中卡片）。
- **文档/备注渲染区右键 = 自建菜单「编辑文档」+「打开文档文件夹」**（`browser._on_doc_context_menu` → `_open_doc_for`/`_open_doc_dir`；后者 `os.startfile` 打开 `metadata.doc_dir` 目录，失败仅状态栏提示；「打开文档文件夹」仅当 doc 目录存在时显示——与卡片菜单「清除缩略图」仅在有缩略图时显示同款约定；`_DocPreview.__init__` 设 CustomContextMenu 策略禁掉 QTextBrowser 自带英文标准菜单——Copy/Copy Link Location/Select All 未选中文字时大半灰置误导排查，2026-10 用户拍板移除；文档编辑器预览栏无人接信号=右键不弹；**备注区 preview_comment 同接此菜单**——无文档 recipe 的文档区是隐藏的，备注区是唯一右键入口，用户反馈补的，也无「打开文档文件夹」项）。
- `customContextMenuRequested` 的 pos 在**事件接收控件的坐标系**——QAbstractScrollArea 把右键事件投递给 viewport（须 `viewport().mapToGlobal`）、QLabel 是控件自身（`mapToGlobal`），handler 按 sender 分流。
- smoke test 断言策略 + 发信号→菜单→动作全链路（文档区/备注区/缩略图区三条路，os.startfile 打桩验证调用参数）。

## Markdown 文档

- Markdown 文档存 `settings/recipe_docs/<safe名>/doc.md + assets/`（插入图片/视频复制进 assets 用相对引用，同名追加序号）；编辑器打开无文档的配方是**空白编辑器**（不自动建模板文件）。
- `doc_exists` = 文件存在**且正文非空白**——空白编辑器保存落的 0 字节 doc.md 视为无文档，主面板照常回退官方备注。
- 编辑器是**确认/取消模式**（无自动保存）：编辑不落盘、「确认」才 write_doc+发 docSaved+关窗（保存失败不关窗），「取消」/X 关闭丢弃改动（有修改先弹丢弃确认），取消时清理本次会话插入的 assets（正文已丢弃引用不存在）。
- **文档编辑器「确认」成功后必须清 `_inserted_assets` 并 `setModified(False)`**——否则再次关闭时"丢弃修改"确认会把已保存文档引用的 assets 删掉（保存成功 ≠ 会话结束）。
- 渲染 `QTextBrowser.setMarkdown`（**QtWebEngine 在 Houdini 内不可初始化**——Qt6 要求先于 QApplication，已实测；表格/图片/中文正常，代码块无高亮）。
- GIF：QImageWriter/hffmpeg 均无 GIF **编码器**（只能读），网格取首帧、预览区 QMovie、文档流动画走 **QMovie 逐帧同步文档资源**（loadResource 返回当前帧 + frameChanged 时 addResource + viewport 刷新——**坑：PySide6 的 QTextCursor 没有 insertWidget**，Qt 富文本不能内嵌控件，曾因此写崩过媒体替换方案）。
- 视频 `![x](a.mp4)` 在渲染前预处理成普通链接、点击弹 QMediaPlayer——**必须先 setVideoOutput 再 play 否则 InvalidMedia**（实测踩过）。
- **主面板有文档时直接渲染 markdown 文档**（与编辑器预览共用 MarkdownMediaView，媒体按视口宽缩放；无文档回退显示官方备注），编辑器保存发 docSaved 信号同步刷新面板。

## 应用流程

- 应用按类型分发：Tool 双击/「应用」按钮=官方工具架体验（参数照抄官方 _shelf_tool_script 但 **frame=False**——官方默认会把视角拉得极近，改由 `ensure_items_visible` 兜底：锚点落在视图外才把整组平移到视图中心，只平移不改缩放）；拖拽=落点对齐参数（drop_on_wire+不框选，apply 后按 items 锚点平移）。
- 两者应用前都做**网络层级匹配检查**（读 `tool.network_categories` 对比 `editor.pwd().childTypeCategory()`——**坑：`pwd()` 是容纳网络的节点（geo1 内部时 pwd=/obj/geo1），其 `type().category()` 是 Object 而非 Sop，显示网络类别必须用 childTypeCategory()（/obj→Object、geo1→Sop、geo1/dopnet1→Dop，实测）**，不匹配抛中文 RuntimeError 并由 `_push_houdini_error` 推到 **Houdini 左下角状态栏（severity=Error 红色）**；node/parm/decoration 预设需先选中目标节点）。
- **应用成功 = 底部状态栏绿色脉冲**（2026-10 用户指定：`_status_success` → `_flash_status_bar`，QVariantAnimation 关键帧 0→0.12 峰值→0 共 900ms 淡出，背景 rgba 绿 + 白字圆角，finished 回落窗口级样式；双击/拖拽/代码块追加的全部成功点都走它；重复触发 stop 后重启、不叠加动画对象）。**失败 = `_status_error` 红色脉冲**（#d1283e 同款动画，2026-10-21 用户指定）：代码块追加全失败/并入失败/创建失败（配方与代码块两处）/显示刷新失败闪红；前置未满足的引导文案（如"请先选中目标节点"）不闪保持纯文字。
- **应用入口同配方去重闸**（`_apply_gated`，0.75s 窗口 > 系统双击间隔）：`_on_double_click`/`_finish_drag` 两入口共用——双击序列偶尔漏进第二发（拖拽落网后紧跟的平台级 DblClick、事件循环重入），曾表现为状态栏连闪两次 + 节点/代码重复创建；窗口内同配方重复应用直接吞掉（不刷时间戳），应用抛异常回滚时间戳（失败不占闸，修正后可立即重试）；gate 在入口层，`_apply_recipe` 保持纯净供测试直调。
- **拖拽入网是自研方案**（不依赖网络编辑器接受任何 MIME）：拖动期间装 QApplication 级事件过滤器吃 MouseMove/Release/Esc，松手用 `hou.ui.paneTabUnderCursor()` 判落点、`editor.cursorPosition()` 取网络坐标，apply（click_to_place=False）后按返回 `items` 里 `tags.target_tag` 锚点把整组节点平移对齐（官方不保证该模式落点是鼠标点，实测对齐精确）。

## 搜索

`_match_search`：普通词全字段（显示名/内部名/备注/标签）不区分大小写子串匹配，**# 开头的词只搜标签**（忽略 # 前缀比较），多词之间 AND（"#生成 vex" = 标签含"生成"且全字段含"vex"）；搜索框 `NoContextMenu`——右键不弹 Qt 标准英文菜单（2026-10 用户指定）。

## 窗口级行为

- 窗口 `__init__` 零 hou 依赖（smoke test 直接实例化+打桩 list_recipes）；**每次从隐藏到显示自动 reload**（hideEvent 置 `_need_reload` 标志、showEvent 消费——单例窗口关闭只是隐藏，重开面板即拿最新库数据；置顶切换 `_toggle_pin` 会豁免一次）。
- **初始焦点显式给侧栏**（`__init__` 末尾 `sidebar.setFocus()`，对隐藏窗口 setFocus 即设定窗口激活时的焦点控件）：不指定时首次激活焦点落 tab 链首个可聚焦控件（Houdini 样式下按钮不可聚焦 → 落搜索框，用户不期望预激活）；**不能给网格——QAbstractItemView 拿到键盘焦点且 current 无效时会把首行设为 current 但不选中（实测），`currentItemChanged` 触发后表现为"预览显示第一卡但卡片无高亮"**；侧栏 rebuild 时已静默 setCurrentItem(KEY_ALL)（blockSignals），焦点落上零副作用，方向键可切分类。
- **该 Qt 行为按 FocusReason 分流**：Other/ActiveWindow/Tab/Shortcut 触发、Mouse 不触发（点击卡片走 mousePress 自己设 current+选中）；且**焦点恢复路径也踩坑**——上次关闭前点过空白=网格持焦且 current 已清，重开面板窗口激活把焦点恢复给网格，同症状复发，故 `_Grid.focusInEvent` 里兜底：焦点建立后若无选中项把 current 退回 -1（维持"currentItem 非空 ⟺ 有选中"不变量）。无头回归要点：offscreen 的 hide/show 后窗口不再 active、setFocus 焦点事件**不投递**（必须趁首次 show 后的激活态直接 setFocus 才能复现/测试）。
- reload 异常分支与空库同路径重建（清 `_info_by_name` + rebuild），防残留旧卡片可点选已不存在的 recipe。

## 设置面板

设置面板（顶栏「设置...」，ShareX 式左分类右内容，`_SettingsDialog._PAGES` 追加页即扩展，现为 库目录/数据 两页；「常规」页已整体删除——`_GeneralPage`/`_ClickToggleLabel`/`hide_nodeparm_in_all`/`_apply_general_settings` 已全部移除、无残留，显示过滤由侧栏顶部三个开关接管）：库目录管理迁入为第一页（原独立「库目录...」对话框删除，增删即时生效回调主面板 reload）+「数据」页导入/导出（见下节）。

## transfer（.zip 交换包，"打包带走/分享"）

- 包结构 = manifest.json（format_version=1，**hda_files 键必须是完整包内路径 `recipes/a.hda`**——裸文件名与 zip 成员对不上会被静默跳过）+ recipelib.json（按现存 recipe 过滤陈旧条目、thumbs 值改写为包内路径）+ thumbs/ + docs/<safe>/（含 assets）+ 可选 recipes/（.hda 按文件整体打包不按 recipe 拆，`RecipeInfo.library` 去重；backup 目录不在枚举内）。
- **导入合并语义**：按内部名逐条、包内覆盖同名、本地独有保留、收藏取并集、**含本地同名 recipe 的 .hda 整文件跳过**（同名定义=Houdini 按库优先级二选一，不可预期）；导入零 hou 依赖——.hda 落库后 `set_lib_dirs` + `reload()` → `ensure_libraries_installed` 自动安装。
- 导出先写 `.~part` 成功才原子替换（取消/失败不留半个 zip）、导入元数据 bulk_set 最后一次落盘（中途取消不改设置，已解文件成孤儿无害、重跑幂等）；进度走同步 QProgressDialog（`_run_with_progress`：setValue 模态下自动 processEvents，无需 QThread）；zip 内 json/md 走 deflate、媒体/hda 走 store（媒体已压缩，deflate 只烧 CPU）；元数据批量读写走 `metadata.all_metadata/bulk_set`（逐键 setter 一条一存，导入会写盘爆量）。
- smoke test 有完整导出→导入往返（临时目录双环境 + 同名冲突跳过 + 坏包拒绝 + 取消清理 + zip-slip 断言）。
- **导入防路径穿越**：manifest 的 `hda_files.*.original` 由包作者任意伪造，落盘目标只取 `os.path.basename`（`../` 与绝对路径均无效）；**文档目录名同样来自包内成员、同样可伪造且直接喂给 rmtree**——`_doc_dirs_of` 对目录名过 `_safe_rel` 同款校验（`docs/../doc.md` 曾折出 ".." 让 rmtree 删掉整个 settings/、`docs//doc.md` 折出空段删掉 DOCS_DIR 本身，读取阶段拒绝、导入前再断言 realpath 收敛在 DOCS_DIR 内），文档替换走"解到 `<safe>.~incoming` → 成功才换入"的 staging（旧版"先删后写"在解包失败时会让本地文档凭空消失）。
- **导入同名冲突对照含出厂 recipe 全集**（只对用户库名单会漏 $HFS 同名、恰好制造按优先级二选一）。

## safe_name 撞名与迁移

- **safe_name 折叠非单射（a::b 与 a/b 同折 a__b，"保留全名可防互撞"是错误结论）**：写入/读取路径一律走 `metadata.unique_safe_name`——与库内其他内部名真实撞名时追加 md5 前 8 位摘要（照 HDR thumb_path 的解法），**解析结果登记进内部键 `stems` 恒定化**（不恒定则后出现的撞名伙伴会把先写者的 plain 路径"翻转"成摘要路径、已有文件失联；恒定后先写者保 plain、后写者落摘要），未撞名者与 safe_name 完全一致、存量零迁移。
- 撞名检测数据源 = 元数据键 + 内部键 `docs`（write_doc/导入登记，纯文档零元数据的 recipe 也要参与互查）；撞名对的旧 plain 文档目录由 `migrate_colliding_doc_dirs` 在 reload 开头幂等改名（缩略图是存储式路径无需迁移）；`delete_doc_dir` 按 unique 名比对防连锁误伤；换扩展名重设缩略图/清除缩略图时按 unique 前缀清理旧文件（跳过仍被引用的，防孤儿）。

## 已知代价备忘（暂不修，2026-10 复审）

- **showEvent 触发的 reload 是主线程全量扫库**（hideEvent 置 `_need_reload`，重开面板即 `store.list_recipes` → `ensure_libraries_installed` 逐 .hda `definitionsInFile` 检视 + 逐 recipe 解析 `data.recipe.json`，uieady 启动时同样跑一遍）——冻结的是整个 Houdini 界面，lib_dirs 指向共享盘/大库时明显。库规模变大后做两件事：① 先 `os.stat` 各 .hda 取 mtime+size 指纹，与上次一致就跳过重扫；② 真正扫描挪 QThread（HOM 调用仍回主线程，按 Conventions「线程」约定），扫描期间状态栏提示"正在扫描库…"。
- `metadata.unique_safe_name` 非纯函数（会把解析结果写进 stems 段落盘）：导入流程的 `_doc_target_map` 在文档解包前调用它——取消/失败的导入会留下无害的 stems 登记条目（先写者保 plain 的既有语义，不出错）。介意的话给 `unique_safe_name` 加 `register=False` 形参、导入成功后再统一登记。
