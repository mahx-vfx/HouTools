"""打包工程：把场景依赖收进 $HIP/ht_package，并把绝对路径改成相对路径。

- 纯路径逻辑：:mod:`houtools.package.paths`（不依赖 hou，可无头测试）
- 扫描：:mod:`houtools.package.collect`（``import hou`` 全在函数内）
- 窗口：:mod:`houtools.package.window`

对应渲染农场提交前的"整理工程"：只带启用的节点缓存与外部引用，绝对路径
一律搬进 $HIP 并改成相对路径，最后可打成 ``<hip>_package.zip``。
"""
