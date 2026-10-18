# 视窗截图嵌入（Capture Viewport to Node / Delete Viewport Capture）

适用：`houtools/tools/capture_viewport_embed.py`（主逻辑）+ `delete_viewport_capture.py`（薄入口，run 内懒 import 主模块防 reload 依赖序颠倒）+ OPmenu 条目（AGENTS.md 同名行的细节展开）。

## 概览

右键任意节点 → 截当前 3D 视窗 → JPG 落 `$HIP/HouTools_cfg/captures/` → 作为网络编辑器背景图钉在节点旁（`NetworkImage.setRelativeToPath(节点)` 锚定，节点移动图跟随）。

**图片只存路径引用不进 hip**：背景图 path 存 `$HIP/...` 变量形态、显示时展开，工程挪动/换机仍有效，hip 不膨胀，磁盘图删除显示即失效——对照 Labs "Capture and Embed" 的 `setDataBlock` 内嵌方案会撑大 hip 且删图不缩。

## 截图引擎 = 单帧 flipbook

H22 无 `hou.SceneViewer.viewerCapture`/`hou.imageFormat`，别被旧资料误导。

`flipbookSettings().stash()` 复制用户对话框设置为底再钉死：

- `frameRange((f,f))` / `output(展开绝对路径)` / `outputToMPlay(False)`（默认 True 会弹 MPlay）
- `initializeSimulations(False)`（防截图触发模拟重算）
- `beautyPassOnly(True)`（去网格/gizmo 覆盖层）
- `useResolution(False)` + `outputZoom(100)`（视口原始分辨率）

直接给 output 路径不弹保存对话框、扩展名定格式（jpg 直出，论坛实测 flipbook 的 png 输出质量差）。

## JPG 质量重编码

flipbook 的 JPG 质量不可控（FlipbookSettings 无该参数），落盘后 QImage 按 `_JPG_QUALITY=60`（用户指定；显示时重下采样后与原图肉眼无差且留"图被拖大看"余量，实测 flipbook 默认档约 868KB/张、q50 约 193KB）重编码原地替换：

- 写 `.~tmp.jpg` 临时文件再 `os.replace` 防半写损坏
- best-effort 失败仅告警保留原质量，不中断流程

## 文件命名

节点路径转横线、节点名内 `-` 先转义为 `~`（节点名 charset `[A-Za-z0-9._-]`、`~` 天然不冲突 → 编码可逆且单射，防 `/obj/net1/sub-foo` 与 `/obj/net1/sub/foo` 同名互覆；smoke test 断言注入性+可逆+用户示例）。

## 重截覆盖语义

同名文件覆写 + 移除旧背景图条目。`is_stale_capture(image, node_path, hip_paths)` 判据：锚点==节点路径 或 路径引用==候选 $HIP 路径之一——候选含当前文件名 + 节点上一次 marker 文件名，后者清跨会话改名（回调已死）留下的死锚点条目，O(1) 无扫描。

## rect 存锚点局部坐标

官方文档明确 rect 显示时按锚点位置平移：

- x 在节点左缘展开（`-(size.x()/2+NODE_GAP_UNITS)` 为图右缘，用户指定放左边）
- y 以节点中心竖直居中
- 显示宽 `DISPLAY_WIDTH_UNITS=2.0` 可调常数（用户指定再小一倍，原 4.0）
- 高按 `hou.imageResolution` 宽高比推算

## 落库与显示（两步缺一不可）

- 应用 = `editor.backgroundImages()` 过滤后 `setBackgroundImages(kept+(新,))` 整列表写回（立即显示）。
- **落库必须再调官方 `$HH/python3.13libs/nodegraphutils.py` 的 `saveBackgroundImages(pwd, 全列表, editor)` 写网络节点 userData**（键 `backgroundimages`；undo 组由它自建勿外包防嵌套；该模块顶层 import hou.ui 仅 GUI 可导入、须函数内 import）。setBackgroundImages 只改编辑器临时显示列表，切换层级后编辑器按该 userData 还原布局，只调前者图一切换层级就消失（首版实测踩坑；官方文档 "image layout is saved into the user data for the current network" 即指此）。

## 重截立即刷新 = 官方 "Reload Images" 清空重放手法

编辑器位图按路径缓存，同名覆写后 setBackgroundImages 重摆条目也不重读文件。替换旧条目时先 `setBackgroundImages([])` 再整份放回即强制重读——手法出自官方 `$HH/python3.13libs/nodegraphpopupmenus.py` BackgroundImageContextMenu 的 reloadimages 命令，注释原话 "To force a reload, just clear the images, then put them back"。

**严禁用层级往返刷新：setPwd 导航会触发解算重算直接卡死会话，用户实测否决。**

## 改名跟随

- 节点 userData 写 `MARKER_KEY="ht_capture_image"`（当前文件名，随 hip 持久化）+ 挂 `NameChanged` 回调（注册先摘旧防叠加，`_ht_capture_cb` 属性标识，两回调共享一份 state dict 由改名跟随自更新）。
- 改名后：磁盘文件 `os.replace` 到新编码名、userData 条目经官方 load→改→save 重锚、显示该网络的所有编辑器刷新（新路径缓存 miss 自动重读，无需清空重放）、marker 同步。
- **改名撤销（Ctrl+Z）会再触发 NameChanged 原路改回，撤销语义自洽。**

## 节点删除清理（只清条目、文件保留不删——用户拍板 2026-10）

`BeingDeleted` 回调只清背景图条目（全程用 state 记录的最后已知锚点/文件名/父路径，不触碰删除中的节点对象；加载期间跳过一层），**磁盘文件一律保留**：

- BeingDeleted 无法区分用户删除与场景拆除，且 GUI 的 File>New 实测可能不 fire hipFile clear 事件。
- 曾就地删文件 + 造一整套防御（hipFile 事件钩子/teardown 旗标/延迟队列/批量检测/工程名校验），两轮测试踩两坑（GUI New 事件缺失致截图文件被清光、`hou.UndosDisabler` 抽象类不可直接构造须走 `hou.undos.disabler()` 工厂）后放弃，防御机制整体拆除。
- 孤儿文件无害（文件名=节点路径自说明，手动清 `$HIP/HouTools_cfg/captures/` 即可），删文件是右键 Delete Viewport Capture 菜单的专职（用户主动、上下文明确）。

## Delete Viewport Capture 右键条目

context expression 按 `node.userData("ht_capture_image")` 过滤（与 MARKER_KEY 双处同步，userData 内存读取右键开销可忽略），删除 = 图条目 + 磁盘文件 + marker + 摘除回调（`_unwatch` 按 `_ht_capture_cb` 属性识别）。

## 视窗/编辑器选取

- 视窗选取 = `hou.ui.paneTabs()` 过滤 SceneViewer 按 `isCurrentTab()` 取可见（无可见报中文错）。
- 编辑器选取 = kwargs['editor'] → pwd 匹配 → 光标下/任意可见 NetworkEditor。

## 已知局限

父级网络改名不跟随——NameChanged 只在节点自身改名触发，父级改名后整批锚点/文件名失效，须重截或后续加父级回调。
