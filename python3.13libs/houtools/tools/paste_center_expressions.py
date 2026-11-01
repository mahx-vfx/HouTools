"""Paste Expressions [HT] 菜单入口（薄分发）。

OPmenu.xml「Paste Center [HT]」子菜单的双模式子项之一：在右键目标节点
下方创建 transform 并写入 centroid() 链接表达式（同网络相对路径、跨层级
绝对路径）。逻辑全在 paste_center.py，此处 run() 内懒 import（同
delete_viewport_capture 手法），reloader 的插入序依赖（先被 import 的是
依赖）不被打破。
"""


def run(kwargs=None):
    from houtools.tools import paste_center
    paste_center.run_expressions(kwargs)
