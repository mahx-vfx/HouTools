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

## 触发方式 = 双击追加到选中节点（唯一，2026-10-18 拍板收敛）

`codeblock.append_to_nodes` 把代码**追加**到全部选中节点的代码参数尾部（现有内容非空时空行分隔），VEX→`snippet`、Python→`python`（22.0.429 无头实测 attribwrangle/volumewrangle/python SOP 均有该参数）；逐节点独立容错（无参数/锁定跳过并汇报），整批一个 undo 组。

## 拖拽触发为何移除（存档，勿在主分支重试同类方案）

- 内建 VEX 编辑器（GL 虚拟控件）的多行拖放渲染是 SideFX bug：内容写进编辑器但显示层不重算——只渲染首行、其余可框选/可删但不可见；纯文本/CRLF/官方 nodePath 格式（与原生节点拖拽逐位对齐）/外部拖源/txt 文件拖全部复现；Ctrl+Enter 提交不救；Qt 合成事件对虚拟控件无效；编辑器私有撤销栈 API 不可达——手动 Ctrl+Z/Ctrl+Shift+Z 虽能重建显示但无法编程触发，OS 注入被用户否决。
- **完整实验代码与逐轮结论存档在 `feature/codeblock-drag` 分支**（含 nodePath 伪装、撤销/重做重建显示、自建编辑器并入弹窗等全部尝试）；若未来 SideFX 修复该 bug 或提供编辑器 API，从该分支恢复。
- 存档知识：官方拖放 MIME 协议=hou.qt.mimeType（nodePath=`application/sidefx-houdini-node.path` 等，Qt 拖拽可 setData 伪装、文档明确"部分原生控件按 MIME 走不同落点行为"）；多行编辑器是"缓冲+Ctrl+Enter 提交"模式（官方文档），但提交键也不救拖放渲染；第三方 VEX 编辑器（pw_Houdini_VEX_Editor 等）从不和内建编辑器死磕，全部自建 Qt 编辑器绑定参数——本工具最终亦取此路。
- **关键发现：`QApplication::notify` 对拖放族事件有"须有活动原生拖放（drag manager 激活）"的短路——无活动拖放时合成 sendEvent 的 Drop 在进应用过滤器之前就被丢弃（无头探针实证）**，故过滤器单测只能直调 `eventFilter(obj, drop_ev)`（GL 输入模拟分支无法无头测试，仅 Qt 文本控件分支可测），真实拖放时事件正常流经过滤器；已知边角不变：Esc 取消与"落在不接受拖放的空白"不可区分（都无 Drop），Esc 恰停在网络编辑器上会误创建。

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
