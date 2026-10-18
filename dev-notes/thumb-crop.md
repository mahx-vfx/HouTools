# 缩略图裁剪（ThumbCropDialog）

适用：`houtools/recipelib/crop.py`（AGENTS.md「缩略图裁剪」行的细节展开）。

## 交互

`ThumbCropDialog`：头像上传式框选（选框锁卡片缩略图区比例 1:0.66，四角按比例缩放/框内移动/框外重框/三分线+卡片效果预览），确认返回原图分辨率 QPixmap。

## 设置缩略图流程

- 静态图先弹裁剪再按**源图格式**存（`metadata.thumb_format_for_source`：jpg→JPEG 质量 90、png→PNG、webp/bmp 等回退 PNG——Houdini Qt 无 webp 编码器、bmp 臃肿，`QImageWriter` 实测）。
- `metadata.set_thumb_from_pixmap`，相对路径存储；换格式时旧扩展名文件由 `_clean_stale_thumbs` 按前缀清理。
- **GIF 跳过裁剪**原样复制（Qt 无 GIF 编码器，裁剪存不回动图），卡片显示端 cover 中心裁剪兜底。

## 坑

- **画选框边框/三分线必须显式构造 QPen**——paintEvent 前段 setPen(NoPen) 画遮罩块后，`pen()` 取回的仍是 NoPen 样式、改色改宽不换样式，整段笔画静默不可见（2026-10 像素探针发现并修复，smoke test 像素断言固化）。
- 真实平台下 `QWidget.grab()` 输出设备像素，测试采样须按 `devicePixelRatio()` 映射。
- smoke test 裸 python 环境缺图片编解码插件（qjpeg 依赖的 Qt DLL 解析不到；**勿用 PATH/QT_PLUGIN_PATH 补**——前者劫持 PySide6 的 Qt DLL 解析直接 ImportError，后者加载仍失败），JPEG 断言按 `QImageWriter` 能力分流，GUI 会话无此问题（hython 实测 jpeg 可写）。
