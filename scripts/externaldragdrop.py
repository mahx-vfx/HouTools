# HouTools: 官方 externaldragdrop 钩子分发器（逻辑在 houtools.dragdrop，
# 走 Reload 热加载）。Houdini 以 exec(<stdin>) 方式执行本文件：没有
# __file__，不要在此文件写任何逻辑。
#
# import 兜底：钩子内裸抛异常 = 拖入文件被当 hip 文件打开（dev-notes/
# dragdrop.md）。包处于半改状态（语法错误/导入期异常）时在这里吞掉，
# 返回 False 交还原生拖放，而不是让异常逃出钩子。
try:
    import houtools.dragdrop as _houtools_dragdrop
except Exception:
    _houtools_dragdrop = None


def dropAccept(file_list):
    if _houtools_dragdrop is None:
        return False
    return _houtools_dragdrop.drop_accept(file_list)
