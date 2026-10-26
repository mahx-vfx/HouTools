"""Recipe Library - 基于 Houdini 官方 recipes 的资产库工具入口。

浏览/搜索/标签/收藏用户库里的 recipes（面板只加载 settings/recipelib.json
的 lib_dirs 配置的库文件夹，出厂 $HFS/Labs 默认库不显示），双击或拖入
网络创建，可挂缩略图（含 GIF）与 Markdown 文档（图片/视频）。底层经
houtools.recipelib.store 包 hou.data / recipeutils 公开 API。
"""


def run():
    from houtools.recipelib.browser import show_recipe_library
    show_recipe_library()
