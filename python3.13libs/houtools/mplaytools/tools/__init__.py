"""MPlay 专用工具入口：每个模块暴露 ``run(**kwargs)``。

由 ``houtools.mplaytools.dispatcher.run(tool_id)`` 按 id 加载，
不经过 ``houtools.tools.*``（那是 Houdini 主程序的场景工具）。
"""
