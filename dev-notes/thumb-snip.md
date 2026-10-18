# 截取缩略图（屏幕框选，多显示器）

适用：`houtools/recipelib/capture.py` + browser `_snip_thumb` 系（AGENTS.md 同名行的细节展开）。

## 流程

右键卡片「截取缩略图」：面板自隐 → singleShot 等 DWM 重绘（`HIDE_DELAY_MS=200`，急了把面板截进去）→ **逐屏 `QScreen.grabWindow(0)` 抓全部屏幕**（物理像素、DPR 随图带；hython 无头探针实测 GDI 抓屏可用、不含光标；某屏抓取失败跳过该屏、全部失败才中止告警）。

## SnipOverlay 会话结构

- **`SnipOverlay` 是多屏会话控制器（QObject，非 QWidget）**，每屏一块 `_ScreenOverlay`（Tool+Frameless+StaysOnTop 铺各自冻结抓屏；抓屏是物理像素、窗口是逻辑区域，按抓屏 DPR 一比一映射）。
- **共享选框存"虚拟桌面坐标"（`QScreen.geometry` 的定位空间，与 `e.globalPosition()` 同空间）**，每块遮罩绘制时平移回本屏局部坐标（非主屏原点/DPR 不同均成立）。
- **坑：拖拽中光标跨屏时事件仍由按下那块遮罩接收（Qt 隐式鼠标抓取），不能拿本地坐标推算，必须用 `globalPosition()`（平台按真实光标位置给出，混合 DPR 也对）。**

## 选框交互（语义对齐 ThumbCropDialog）

- 选框可跨屏，严格锁 `crop.TARGET_RATIO`（`_fit_from_anchor` 与 `crop._fit_ratio_rect` 同算法，夹进**有遮罩屏幕的并集** `_bounds`、无 `_disp` 显示层无法直接复用故独立实现）。
- 框内拖动移动、四角手柄按比例缩放（复用 crop 的 HANDLE/MIN_SEL 常量）、框外重按重框；松手不确认，Enter 确认 / Esc 取消，无选区 Enter 忽略，最小选框 40px。

## ✓/✗ 按钮条（微信式，左叉右勾）

- 贴选框下缘、右缘对齐选框右缘；选框贴屏幕并集底部放不下时整条翻到选框上缘（窄选框再上移一行与尺寸标签上下堆叠），最后夹进 `_bounds` 保证点得到。
- 点击语义同 Enter/Esc（`_confirm`/`_cancel` 同路径，确认走 `_done` 闩）；按钮上按下不清当前选框；悬停淡高亮+手型光标（悬停格存会话、跨屏一致）。
- 尺寸标签随之移到**选框上缘左对齐**（微信同款，给下缘按钮条让位）。

## 会话收口

- `begin()` 全屏遮罩都 show+raise、键盘焦点给光标所在屏那块（Enter/Esc 在哪块屏都收口会话）。
- **confirmed 只发一次（`_done` 闩防焦点竞争双确认），Esc 不闩——遮罩不自我关闭、Esc 后会话仍归调用方处置（smoke test 依赖此语义）**；会话自身只发信号不自我关闭、生命周期归调用方（`close()` 逐屏关、面板 closeEvent 一并清理）。

## 确认 = 选框换算回物理像素

- 单屏内 = 该屏抓屏 ×该屏 DPR 逐像素裁（与旧单屏版一致）；跨屏 = 各屏截块按参与屏最大 DPR 重采样拼合（`_scaled_span` 区间两端同式舍入，共享边不裂缝）。
- **坑：Houdini 自带 PySide6 6.8.3 的 `drawPixmap(QRect, QPixmap, QRectF)` 签名标注收 QRectF、值分发实测拒绝 ValueError，源矩形必须传 QRect**。
- 尺寸标签只画属主屏（`_owns_chip`：含选框左上角的屏，落空取首个相交屏）。

## 落库与面板收尾

- `set_thumb_from_pixmap`（**fmt="JPEG" 质量 90 存 .jpg，用户指定**——屏幕照片类内容比 PNG 小得多；裁剪对话框路径按源图格式存，见 [thumb-crop.md](thumb-crop.md)，换格式时旧扩展名文件由 _clean_stale_thumbs 按前缀清理）。
- **收尾 `_snip_restore_panel`：先压掉 `_need_reload` 再 show**（同 `_toggle_pin` 豁免手法——截图引发的隐藏/重显不做全量 reload），只定向刷该卡片（pop `_thumb_cache` + 清 `_cover_cache` + `_apply_filter` + `_update_preview`）。
- **先落库再刷 UI**（截图期间面板被 Reload 销毁的极端时序下落库不丢，UI 收尾 RuntimeError 仅记日志）。

## 测试

smoke test 无头模拟拖拽/按键固化比例锁定与确认/取消语义（含按钮条：常规贴下缘/贴底翻转/窄选框堆叠三形态 + 点击 ✓/✗ 同键盘语义），跨屏拼合分支用"同一屏挂两份抓屏"打桩（离屏只有一个屏）。
