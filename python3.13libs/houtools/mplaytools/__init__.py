"""MPlay 专用工具集（独立于 Houdini 主程序的工具链）。

MPlay 是独立进程：有 hou / hscript / Qt，但**没有 .hip 场景与节点树**，
``hou.ui.paneTab*``、``hou.node``、``hou.qt.Icon`` 一类主程序 API 不可用，
``uiready.py`` 与默认热键的启动钩子也不保证执行。因此这套工具：

* 只依赖纯标准库 + ``houtools.videoseq.ffmpeg``（同一套 ffmpeg 查找规则）；
* 不在 import 期触碰 hou / PySide6，全部按需导入并允许测试注入；
* 菜单走 ``MainMenuMPlay.xml`` → ``mplaytools.dispatcher``，
  不使用 ``houtools.tools.*``（那些是 Houdini 场景工具）；
* 分发器每次点击重载本工具集，改代码无需重启 MPlay。

实现细节与逐条坑表见 ``dev-notes/mplaytools.md``。
"""
