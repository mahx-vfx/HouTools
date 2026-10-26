# 代码块（VEX / Python 片段卡片）

适用：`houtools/recipelib/codeblock.py` + `vex_keywords.py`（AGENTS.md「Recipe Library」行代码块部分的细节展开）。2026-10 新增。

## 存储 = 一卡一文件（2026-10-18 用户拍板，对照官方 `$HFS/houdini/vex/include/*.h` 的存放方式）

- 文本型卡片（VEX/Python 片段）：代码存 `settings/vex_python_lib/vex/<内部名>.h` 与 `settings/vex_python_lib/python/<内部名>.py`（文件即数据源，modified=mtime；平铺不递归；写入临时文件+os.replace 原子替换），**不进 HDA、不存 json**（`recipelib.json` 只剩按内部名键控的元数据）。
- **内部名 = 用户命名 = 文件名 stem**：仅首次创建可设、之后弹窗只读（2026-10-18 用户拍板）；`validate_stem` 规则 `^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$`（中文等非 ASCII 硬拒绝——文件名即身份不静默改写），**实时查重两语言目录合并、大小写不敏感（Windows FS），重名/非法直接禁用确定键+红字提示**。
- 中文展示名走「自定义显示名」（display_names，与内部名解耦）；换语言保存 = 文件搬目录（旧扩展名删除）。
- `migrate_legacy_code_blocks` 把旧版 json code_blocks 段幂等迁移成文件并 `rekey_metadata_name` 改挂元数据键（旧键 htcode::X → X，含文档目录改名），reload 开头调用。
- 收藏/标签/颜色/缩略图/显示名/文档全部按内部名（stem）复用既有元数据层；侧栏「代码块」段（`KEY_CODE`，恒显、空段计 0——2026-10 统一语义），**全部/收藏/标签视图代码块自动出现**，「节点参数」/子菜单/层级过滤明确排除（`is_code` 判别；侧栏顶部开关 `show_code` 关闭时代码卡片隐藏）；搜索 haystack 含**代码全文**。

## 卡片缩略图 = 代码文本渲染，可读性优先（2026-10-18 用户反馈重做）

- `render_code_pixmap` **无行号栏**（预览面板的代码视图才带行号，用户认可）、**字号只随缩略图高度取档 `min(22, max(10, h/4.5))` 绝不随内容缩小**、长行 `setTextWidth` 折行、展示不下按行截断（"内容可以不展示完，但字一定要看得清"）。
- **QTextDocument 默认前景色是黑，必须 mergeCharFormat 提亮**否则深底上隐形。
- 代码变更后必须 `_cover_cache.clear()`（缓存键只有 (名,宽,高) 不含内容哈希）。

## 高亮器 = 单遍主正则 + blockState 多行接力

- comment/string/number 在同一位置优先于 word 命中并整段消费，天然规避独立规则互相覆色（字符串里的 #、注释里的引号不会误着色）；/* */ 与三引号串跨行靠 previousBlockState。
- **VEX binding 正则不能用 `\b`**（"@P" 行首时 @ 是非词字符构不成词边界，必须 lookbehind `(?<![A-Za-z0-9_])`）。
- 函数着色 = 词表命中（`vex_keywords.py` 由 `$HFS/houdini/help/vex.zip` 的 functions/*.txt 离线提取 1120 个，Houdini 大版本升级后同法重生成）**或任意词后跟 "("**（自定义函数也着色）。
- 语言切换（弹窗下拉）必须先 `setDocument(None)` 摘旧高亮器（不摘会双重着色）且 setPlainText 重建文档清残留格式。

## 弹窗坑（fonts/中文化的时序）

- **编辑弹窗等宽字体必须在挂树后 `apply_mono` 压回**——fonts.py 的 ChildAdded 过滤器会把工具字体刷给刚加进布局的编辑器（QPlainTextEdit/行号栏都中招）。
- **弹窗按钮中文化同理必须挂布局后调**：`localize_buttons(self)` 走 `findChildren(QDialogButtonBox)`——把按钮盒自身当 dialog 传入、或按钮盒还没挂进布局就调用，都找不到盒子→按钮留英文（2026-10 用户反馈「新建代码块」弹窗 OK/Cancel 是英文即此坑）。

## 触发之一 = 双击追加到选中节点

`codeblock.append_to_nodes` 把代码**追加**到全部选中节点的代码参数尾部（现有内容非空时空行分隔），VEX→`snippet`、Python→`python`（22.0.429 无头实测 attribwrangle/volumewrangle/python SOP 均有该参数）；逐节点独立容错（无参数/锁定/关键帧/表达式/channel reference 跳过并汇报），整批一个 undo 组。

## 拖拽触发 = 双通路（2026-10-21 从 feature/codeblock-drag 定案并入）

- **拖入文本编辑框（VEX/Python 编辑器）**：放行原生落点（Houdini 自己的落点处理负责定位+写入，**数据始终正确**——拖入后代码正常执行，坏的只是 GL 编辑器显示层：只渲染首行、其余可框选/可删但不可见）+ `browser._refresh_dropped_editor_display` 触发参数面板整体重建（编辑器控件销毁重建后显示正常）。
- **实测定律**：parm.set 值变更（同值/往返）、重绘（拖面板/切窗口）、别处新开面板（浮动/复制——新实例天生干净但救不了旧实例）、搜索过滤全部无效；**唯一有效的是面板整体重建**，且只在「模板组真的变了」时触发——同一脚本内关了又开=净零变化被判无事发生（节点首次定制那次例外），processEvents 救不了（面板重建走 Houdini 空闲队列，非 Qt 事件）。
- **修复 = 两次真实变更各落一个事件周期**：载体参数（wrangle 家族→`autobind`，第二栏 Bindings 工作流不可见；python SOP→`maintainstate`）的隐藏旗标第一跳翻转（撤销组「HouTools 刷新编辑器」）→ `QTimer.singleShot(150)` 第二跳翻回——各触发一次重建，结束时旗标归位=面板外观零变化；参数值零触碰。识别走拖放瞬间光标所在的参数面板（`hou.ui.paneTabUnderCursor()`→`currentNode()`，ParameterEditor 继承 PathBasedPaneTab）；识别失败/无载体一律静默不做（普通文本框原生落点显示本来就是好的）；锁定 HDA 拒改→状态栏提示。
- **拖入网络编辑器**：`find_code_node_at` 矩形命中代码节点 → `NodeCodeDialog` 并入（预填现有代码+空行追加，确认 parm.set 写回单 undo 组）；空白 → `create_node_with_code` 创建对应节点（VEX→attribwrangle、Python→python SOP，仅 SOP 网络，层级不符中文报错）并填码，失败不残留半截节点。`find_code_node_at` 的 `language` 传 None 可命中任意语言的代码节点（调用方用 `code_parm_name` 判定语言）。
- **语言错配守卫（2026-10-21 用户指定）**：Python 卡不能拖进 VEX 编辑器/节点（反向同）。编辑框通路在**拖放层拦截**：代码拖拽期间装应用级事件过滤器，真实拖放的 Drop 事件流经过滤器（`QApplication::notify` 的短路只影响合成事件，真实拖放不受限）——光标下参数面板显示异语言代码节点时直接吞掉 Drop，Houdini 的写入处理不会执行，**无需任何事后撤销**（undoLabels 比对 + performUndo 的事后撤销实测不可靠：落点编辑不保证注册撤销条目，已弃）；过滤器识别不了的旁路落点事后只红闪提示、不做不可靠的事后撤销。网络通路命中异语言代码节点直接拒绝（不并入、也不 fallback 成在旁边凭空创建异语言节点）；空白创建不受限；双击本就只填匹配语言参数。
- 载体选型理由：变化必须真实发生但用户不可见——autobind 在第二栏（工作流盯第一栏编辑框）；vex_threadjobsize 被 Hide When 压着但 Numbers 模式会在第一栏显形，否决。
- 历史方案存档（全部判死，详见 feature/codeblock-drag 分支的逐轮实验）：值往返 parm.set、undo/redo 管线重建（原方案 G）、接管 Drop 走 Qt 文本控件/粘贴管线、Qt 合成事件、OS 级 SendInput。
- 存档知识（仍有效）：官方拖放 MIME 协议=hou.qt.mimeType（nodePath=`application/sidefx-houdini-node.path` 等）；多行编辑器是"缓冲+Ctrl+Enter 提交"模式（官方文档），提交键不救拖放渲染；`QApplication::notify` 对拖放族事件有"须有活动原生拖放"的短路——过滤器单测只能直调 `eventFilter(obj, drop_ev)`；已知边角：Esc 取消与"落在不接受拖放的空白"不可区分（都无 Drop），Esc 恰停在网络编辑器上会误创建。

## 水印 = Houdini 官方节点图标（2026-10-18 用户指定）

VEX=SOP_attribwrangle、Python=SOP_python 的节点 tile 图（IconMapping 实证 `SOP_python := MISC_python`、`LOP_attribwrangle := SOP_attribwrangle`，名字真实存在），`codeblock.code_watermark_pixmap` 经 `hou.qt.Icon` 取自图标索引随版本走（GUI 会话限定）、(语言,尺寸,dpr) 缓存 NameError 守卫活过 reload、逐候选回退；无头/取不到回退侧栏自绘 </>；`_draw_watermark` 已参数化 `pixmap_fn(size, dpr)`。

## 预览与入口

- **预览位切换**：代码块无自定义缩略图时 `preview_code`（只读 `_CodeEditor` 子类，复用行号栏）顶替 preview_label（两者 visibility 互斥、`_clear_preview` 必须复位），元信息行 = 语言/修改时间/行数；预览代码区右键「编辑代码」。
- **卡片第二行 = 语言+行数**（2026-10 用户指定，`_type_line` 出 "代码块 • VEX • N 行"，与预览元信息同口径）。
- **空白网格区右键 = 「新建代码块」**（`_on_context_menu` 的 item=None 分支；创建后 `_apply_code_blocks` 轻量重建——不重扫 HDA 库——并切到「代码块」段选中新卡）。

## transfer（随包走）

代码块按**文件**随包走，包内路径镜像本地布局 `vex_python_lib/<语言>/<名>.h|.py`（manifest 加 `code_blocks` 名单；`_code_file_plan` 校验包内成员路径 + 落盘只取 basename 防穿越），导入同名覆盖；**旧包兼容**：早期版本代码放 recipelib.json 的 code_blocks 段——读取端仍解析该键，导入时落成文件（`free_stem` 撞名让位）并 rekey 元数据；导出条目列表 = recipes+CodeInfos（`.name` 同协议，thumbs/docs/标签过滤自动生效）。

## 测试

smoke test 五段回归（文件存储+迁移/高亮渲染/弹窗查重/面板集成/打包往返）。

## 追加写入的三态预检（2026-10 复审）

- **append_to_nodes 写入前必须预检关键帧 / 表达式 / channel reference**：参数带表达式时 `set()` 不生效（表达式存活，代码根本没进去）；带关键帧时写入被盖回；**带引用时 `set()` 默认 `follow_parm_reference=True` 会顺着引用链写到被引用的那个参数上**（覆盖别人的 snippet）——三种都曾会静默失效却报"追加成功"。命中一律跳过并汇报（不清用户表达式/关键帧，那会破坏既有接线）；引用本质是 ch()/chs() 表达式、理论被 expression 分支覆盖，`getReferencedParm` 做权威兜底；干净参数写入显式 `follow_parm_reference=False` 双保险。无头回归：打桩 `hou.undos` + 鸭子类型参数直测（smoke test "codeblock append pre-check"）。
- **预检已提成 `codeblock.check_parm_writable(parm)`（2026-10 复审修复）**：拖拽并入通路（browser 的 NodeCodeDialog 确认后写回）此前漏了同款预检——弹窗前先过预检，不可写不开窗，命中红字跳过；两路写回共用一个函数，语义永远同步。
- **读现有代码必须 `unexpandedString()`，禁 `evalAsString()`**（2026-10 复审修复）：字符串参数的 evalAsString 会展开 `$` 变量——现有代码里的 `"$OS/$F"` 被求值成实际值后随追加整体写回，用户代码被静默污染（无头实测）。预检已排除关键帧，unexpandedString 此时安全（只对带关键帧参数抛错）。smoke test 固化含 `$OS` 的用例。
