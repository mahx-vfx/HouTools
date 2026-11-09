# notify — Toast 通知（ui/notify.py + dev.status 委托）

2026-11 全新模块，替代"纯状态栏文本"的通知样式。本文记录实机调试定稿的
结构与坑表。**改本模块前必读**——多数条目都有 MCP 实测数据背书，不是推测。

## API

```python
from houtools.ui import notify

notify.success("已创建 transform")        # 总 2s（保持 1s + 渐透 1s）
notify.info("...")                        # 总 2s
notify.warning("...")                     # 总 2s
notify.error("...")                       # 总 4s（保持 3s + 渐透 1s）
notify.show(msg, level="info", duration=5000)   # duration=总时长 ms（含渐透），下限 500（2026-11 复审修复：下限作用在总时长上——旧写法落保持段，duration∈[500,1500) 全被拉成 1500ms；总时长不足完整渐透（<1000ms）时渐透按需压缩，_hold_ms/_fade_ms 一对函数统一切账）
notify.clear()                            # 立即收掉全部（各自渐透）
```

返回 toast 实例（`_Toast`），失败返回 None（细节已进日志）。**通知永不
打断宿主操作**是硬约定：整条链路 try/except 兜底。

- `dev.status(message, error=, severity=)`：全仓 ~40 处既有调用点的稳定
  入口，签名不变；按调用 `importlib.import_module` 委托到
  `notify.show_legacy`。旧 severity 映射：message/important→info、
  warning→warning、error→error（表只活在 notify 的 `_LEGACY_SEVERITY`，
  smoke test 固化对照）。调用点后续遇到语义明确的再逐步改成
  `notify.success()` 等。
- dev/__init__.py 改动**需重启 Houdini**（dev 永不重载）；notify 自身
  走 Reload 热加载，活跃 toast 跨重载继续跑（见下）。

## 结构定稿（用户逐项拍板）

- 位置：左下角第一条贴状态栏，向上堆叠（间距 6px）；同屏上限 4 条，
  超出最旧的先进入渐透
- 视觉：整条等级色底（success `#285C43` / info `#0d6399` / warning
  `#80601E` / error `#d1283e`）+ 白字 9pt + SVG 徽标（亮色圆牌/三角）
- 行为：hover 暂停倒计时（离开按剩余时间继续，至少再给 800ms）、点击
  立即渐透、`WA_ShowWithoutActivating` 不抢焦点
- 动画：入场自下 8px 上滑（**无淡入**，用户拍板——全透明度出现）+
  补位下滑 170ms OutCubic；两者共用一条可重定向 pos 动画通道，
  `_target_y` 记账保证连发打断不丢余量（坑表 #14）
- **原生状态栏不是通知通道**（2026-11 拍板：toast 与状态栏文本不得同时
  出现——初版的镜像导致双通道，已删）；statusbar 只剩 dev 的 notify
  不可用兜底

## 坑表（全部有实测背书）

| # | 坑 | 结论 |
|---|---|---|
| 1 | **半透明顶层窗（`WA_TranslucentBackground`）上 QSS `background` 不渲染**——文字（QLabel 子控件）正常、QFrame 自身底色消失 | 底色必须 `paintEvent` 手绘圆角矩形（`rect().adjusted(0,0,-1,-1)` 防抗锯齿边被窗口边界裁掉）；H21/H22 同病 |
| 2 | **`QGraphicsOpacityEffect` 撞 QSS**——动画在跑、画面不动（"属性在动但画面不动"，两轮盲修被它骗过） | 淡入淡出走 `windowOpacity`（系统合成通道，必然生效）；**子部件没有 windowOpacity**，这就是 toast 必须做成顶层窗的原因 |
| 3 | **Houdini Qt 不渲染 COLR 彩色 emoji**——`Segoe UI Emoji` 显式指定也只出单色轮廓（✅⚠️❌💡 全变线框） | 图标走 SVG（`icons/toast_*.svg` 经 `ui.icons.svg_icon` 缓存）；`svg_icon` 返回 QIcon，取 32 档 `pixmap` 后 `setDevicePixelRatio(2.0)` 缩到 16 逻辑像素；渲染失败回退文字 glyph |
| 4 | **补位时机错误 = "瞬间消失"**——渐透开始就让位，上方 toast 立刻下移，恰好压住还在原地渐透的 toast（实测：不透明 warning y=1003 盖住 65% 透明的 info y=1004，差 1px；被盖的 fade 数据全程正常，纯视觉谋杀） | **槽位保留到渐透结束**：`_fadeout` 只原地淡出，`_gone`（动画 finished、C++ 对象真没了）才从 `_ACTIVE` 摘除并让上方下滑补位 |
| 5 | **串行锁死锁教训**——第一版给渐透加互斥锁（怀疑并发渐透打架），排队重试被自己的 `_queued` 再入守卫拦死：toast 卡屏 45s+、点击无响应（click 也走 `_fadeout` 被拦） | 并发渐透实测**无问题**（MCP 探针：两条同步 0.95/0.92→0.05/0.02 衰减），锁已删。教训：给重试路径加守卫时，守卫必须区分"首次进入"与"重试进入" |
| 6 | **顶层窗坐标**——`pos()`/`move()` 对窗口型 widget 是**全局屏幕坐标**（即使有 parent） | 堆叠计算统一用 `_main_window().mapToGlobal(QPoint(0, y))` 一个基准；补位位移直接在全局坐标上加减 |
| 7 | **move 压 z 序**——补位 move 之后 toast 可能掉到兄弟窗口之下 | 每次 move 后 `raise_()`；StaysOnTop+Tool 组合保证不进任务栏、不抢焦点、随主窗最小化 |
| 8 | **reload 期间活跃 toast**——`_ACTIVE` 登记表被 reload 重绑成新空容器 | `try/except NameError` 守卫沿用旧集合（`window_manager._closing` 同款）；旧 toast 实例跨重载继续跑自己的动画（方法引用同一 module dict 的全局常量）；toast **不进 window_manager**（3s 自灭，close_all 不碰） |
| 9 | **线程**——Qt widget 只能主线程建 | `show()` 检测 `QThread.currentThread() is not app.thread()` → `hou.hdefereval.executeDeferred` 派发主线程；无 hou 的后台线程退日志（与无头同款） |
| 10 | **无头**——`hou.qt.mainWindow()` 无 GUI 直接异常 | 无 QApplication → `_log_only`（等级映射到日志级别）返回 None；有 app 无 hou → `_main_window` 抛 RuntimeError 被 `show()` 兜底接住 → logger.warning + 退原生状态栏 |
| 11 | **dev.status 委托的引用陷阱**——dev 永不重载而 notify 会被重载，模块级 import 会持旧类引用（Anti-Pattern 条款） | `status()` 内**按调用** `import_module` 取最新模块对象；notify 模块本身不可用（导入失败）时退回原生状态栏（旧实现行为，内联在 dev/__init__.py 兜底分支）。**工具侧绕过陷阱**：首版只委托了走 dev.status 的调用点，工具各自的 `_status` 包装/散点直调全在旁路（12 处，重启也不生效）——已收口，见上方「收口记录」 |
| 12 | **字体**——toast 不在工具窗口子树内，字体 enforcer 的 ChildAdded 兜底够不到 | 构造时显式 `fonts.apply(self)`（换族保字号）；正文 9pt 在 apply **之后**用 QFont 覆写（不走 QSS font-size，避免 QSS/QFont 相互覆盖的顺序不确定性） |
| 13 | **同屏上限死循环卡死主线程**——`while len(_ACTIVE) >= MAX_VISIBLE: _ACTIVE[0]._fadeout()` 撞上栈底已在渐透（`_fadeout` 对 `_closing` 是 no-op，槽位保留期它还在列表里）永不推进；连发"剪贴板文本失效"提示实测 Houdini 卡死 | 上限只数**存活**（未 `_closing`）的 toast：`_toast_cap_victims()` 返回强收名单（确定性列表收缩，无死循环可能）；smoke test 固化 victim 语义 |
| 14 | **补位动画连发丢余量**——pos 动画被打断时若从"当前位置 + delta"重飞，上次没飞完的余量被吞掉，连发几次整摞悄悄上漂 | 每 toast 记**逻辑目标位 `_target_y`**：新补位先把账加上，动画停旧起新、从当前插值位置飞向记账终点（无缝续飞）；入场与补位共用同一条 pos 动画通道（`_pos_anim`）；reload 过渡期旧实例无 `_shift_down` 方法 → `getattr` 探测退瞬移，1-3s 自愈 |
| 15 | **`_target_y` 坐标系混用**——窗口型 widget 的 `pos()`/`move()` 是**全局屏幕坐标**（有 parent 也一样，实测 readback 证实），而初版 `_place` 把 WIN 相对偏移存进 `_target_y`：入场/补位动画终点比实际位置**高一个主窗屏幕原点**（QRect(0,29,…) 机器上悬高 29px；主窗 y 偏移为 0 的机器不可见，极易溜过验证） | `_place` 在 `move(mapToGlobal(...))` 之后取 `self._target_y = self.y()`（全局实际值），后续补位 delta 全在全局坐标上累计；MCP 探针数字比对（入场落点/补位终点 == mapToGlobal 理论值）固化 |

## 收口记录（2026-11）

首版委托只覆盖了走 `dev.status` 的调用点（dispatcher/reloader/docs 等），
**工具侧另有 12 处旁路直调 `hou.ui.setStatusMessage`**——各自 `_status` 薄
包装（paste_as_object_merge / paste_bbox / paste_center / dragdrop /
about_houtools）+ 散点直调（delete_viewport_capture / open_cache_folder /
capture_viewport_embed / houtools_settings / recipelib.browser
`_push_houdini_error` / ui.networknav / automation.window 两处配置守卫）。
用户重启后通知仍旧样的根因即此。已全部收口：统一 `from houtools.dev
import status`（dev 永不重载，from-import 安全，dispatcher 同款）委托，
消息文本逐字节不变、纯换通道；顺带清掉各处只为状态栏存在的死 `import hou`。

**规范：新增通知一律走 `dev.status` 或 `notify.*`，禁止再直调
`hou.ui.setStatusMessage`**（合法出口只剩两处：notify 的状态栏镜像、
dev 的 notify 不可用兜底）。`ui/status_flash.py` 是面板内状态栏脉冲
（面板自身 QLabel），不属全局通知通道，不在收口范围。

## 验证记录（MCP 实测，H21 21.0.512）

- 单条渐透：opacity 0.81→0.5→消失，截图逐帧对上（Houdini 在后台、全屏
  视频盖屏也一样）
- 并发渐透：四条 350ms 连发，info/success 同步衰减 + warning/error 正常
- 无头冒烟：`tests/smoke_test.py` 的 "notify module OK" 段固化时长/映射/
  降级/委托断言
- 观感定稿截图经用户确认（彩色横条 + SVG 徽标 + 左对齐）
