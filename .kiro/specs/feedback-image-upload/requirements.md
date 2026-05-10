# 需求文档

## 简介

为现有的"需求和Bug反馈"功能增加图片上传能力。用户在提交反馈时可以通过粘贴剪贴板截图（Ctrl+V）或点击上传按钮的方式添加图片，以便更直观地描述问题或需求。图片支持预览和删除，并随反馈内容一起提交到后端存储。

## 术语表

- **Feedback_System**: 反馈系统，负责处理用户提交的Bug报告和功能需求
- **Image_Uploader**: 图片上传组件，负责处理图片的粘贴、选择、预览和上传
- **Feedback_Image**: 反馈图片，与反馈记录关联的图片文件
- **Clipboard_Handler**: 剪贴板处理器，负责监听和处理粘贴事件中的图片数据
- **Image_Preview**: 图片预览组件，显示已添加图片的缩略图并支持删除操作

## 需求

### 需求 1：图片粘贴上传

**用户故事：** 作为用户，我希望能够通过 Ctrl+V 粘贴剪贴板中的截图到反馈表单，以便快速添加问题截图。

#### 验收标准

1. WHEN 用户在反馈表单区域按下 Ctrl+V 且剪贴板包含图片数据, THE Clipboard_Handler SHALL 提取图片数据并添加到待上传列表
2. WHEN 用户粘贴图片成功, THE Image_Preview SHALL 显示该图片的缩略图预览
3. IF 剪贴板不包含图片数据, THEN THE Clipboard_Handler SHALL 忽略该粘贴操作且不显示错误提示
4. IF 粘贴的图片超过 5MB, THEN THE Feedback_System SHALL 显示错误提示"图片大小不能超过 5MB"

### 需求 2：点击上传图片

**用户故事：** 作为用户，我希望能够通过点击上传按钮选择本地图片文件，以便添加已保存的截图或图片。

#### 验收标准

1. THE Feedback_System SHALL 在反馈表单中显示图片上传按钮
2. WHEN 用户点击上传按钮, THE Image_Uploader SHALL 打开文件选择对话框
3. THE Image_Uploader SHALL 仅允许选择 PNG、JPG、JPEG、GIF、WebP 格式的图片文件
4. WHEN 用户选择有效图片文件, THE Image_Preview SHALL 显示该图片的缩略图预览
5. IF 选择的图片超过 5MB, THEN THE Feedback_System SHALL 显示错误提示"图片大小不能超过 5MB"

### 需求 3：图片预览与删除

**用户故事：** 作为用户，我希望能够预览已添加的图片并删除不需要的图片，以便在提交前确认反馈内容。

#### 验收标准

1. THE Image_Preview SHALL 以缩略图网格形式显示所有已添加的图片
2. WHEN 用户点击图片缩略图, THE Image_Preview SHALL 显示该图片的大图预览
3. THE Image_Preview SHALL 在每个缩略图上显示删除按钮
4. WHEN 用户点击删除按钮, THE Image_Preview SHALL 从待上传列表中移除该图片
5. THE Feedback_System SHALL 限制每条反馈最多上传 5 张图片
6. IF 用户尝试添加第 6 张图片, THEN THE Feedback_System SHALL 显示错误提示"最多只能上传 5 张图片"

### 需求 4：图片随反馈提交

**用户故事：** 作为用户，我希望图片能够随反馈内容一起提交到后端，以便开发人员能够查看我的截图。

#### 验收标准

1. WHEN 用户点击提交反馈按钮, THE Feedback_System SHALL 先上传所有待上传图片到服务器
2. WHEN 所有图片上传成功, THE Feedback_System SHALL 将图片 URL 列表与反馈内容一起提交
3. IF 任一图片上传失败, THEN THE Feedback_System SHALL 显示错误提示并中止反馈提交
4. WHILE 图片正在上传, THE Feedback_System SHALL 显示上传进度指示器
5. WHEN 反馈提交成功, THE Feedback_System SHALL 清空表单和已添加的图片

### 需求 5：后端图片存储

**用户故事：** 作为系统，我需要将反馈图片存储到服务器并与反馈记录关联，以便后续查看和管理。

#### 验收标准

1. THE Feedback_System SHALL 提供 POST /api/v1/feedbacks/images 接口用于上传反馈图片
2. WHEN 接收到图片上传请求, THE Feedback_System SHALL 将图片存储到 uploads/feedbacks 目录
3. THE Feedback_System SHALL 使用内容哈希值作为文件名以避免重复存储
4. THE Feedback_System SHALL 在 Feedback 数据模型中新增 images 字段存储图片 URL 列表
5. WHEN 创建反馈时包含图片 URL, THE Feedback_System SHALL 将图片 URL 列表保存到数据库

### 需求 6：反馈详情显示图片

**用户故事：** 作为用户，我希望在查看反馈详情时能够看到关联的图片，以便回顾问题描述。

#### 验收标准

1. WHEN 用户查看反馈详情, THE Feedback_System SHALL 显示该反馈关联的所有图片
2. THE Feedback_System SHALL 以缩略图形式显示图片列表
3. WHEN 用户点击缩略图, THE Feedback_System SHALL 显示图片大图预览
4. IF 反馈没有关联图片, THEN THE Feedback_System SHALL 不显示图片区域
