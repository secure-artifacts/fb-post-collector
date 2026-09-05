# Facebook 贴文采集工具

本项目是一个本地采集工具。启动后会打开本地网页面板，用“项目 + 任务”的方式配置 Facebook 贴文链接采集、Google Sheets 写回、字段排序、环境检测和运行历史。

当前版本：`v1.3.0`

## 下载安装

从 [GitHub Releases](https://github.com/secure-artifacts/fb-post-collector/releases) 下载 Windows 安装包或 zip。

安装后启动软件：

- 会自动打开管理页面
- 任务栏右下角会显示托盘图标，关闭网页不会退出软件
- 右键托盘图标可重新打开页面或退出
- 更新安装时，安装程序会先关闭正在运行的进程

验证构建来源：

```powershell
gh attestation verify FBPostCollector-v1.3.0-windows.zip --owner secure-artifacts
```

## 启动

```powershell
python launcher.py
```

默认会打开：

```text
http://127.0.0.1:5088
```

## 主要功能

- 项目配置：浏览器用户、Google 表格、分表、贴文链接列、数据开始行、写入开始列、抓取日志列。
- 浏览器账号：每个 Facebook 账号对应一个独立抓取浏览器配置，不影响日常 Chrome；项目可勾选多个账号轮询抓取。
- 字段配置：勾选字段、拖拽排序、为每个字段指定写入列、导入导出配置。
- 增量抓取：优先看抓取日志列，其次看写入列是否已有数据，只跑新增空行。
- 任务模块：每 N 分钟、每 N 小时、每天、每周、每月、固定一次。
- 运行历史：任务汇总和每行详细错误都保存到 SQLite。
- 环境检测：Tesseract、ffmpeg、Whisper、yt-dlp，支持一键安装缺失组件。
- 错误文案：表格只写用户能看懂的错误，比如 `链接无效`；技术细节保存在本地数据库。

## 授权与外部工具

- Google Sheets：把应用 OAuth 客户端文件放到项目根目录或数据目录，命名为 `google_credentials.json`。用户在“授权设置”里点击“登录 Google 账号”，授权 token 会保存到本地 SQLite；也可以一键删除授权后重新登录。
- Gyazo：当前源码版支持粘贴 `access_token`；完整 OAuth 向导留在下一步增强。
- Tesseract：计划打包时内置 `tools/tesseract`；源码版也会检测系统安装。
- ffmpeg：计划打包时内置 `tools/ffmpeg`；源码版也会检测系统安装。
- Whisper：不内置，用户自行安装，面板会检测 `whisper` 命令。

## 推荐使用流程

1. 打开左侧 `浏览器账号`。
2. 为每个 Facebook 账号新增一个抓取账号。
3. 点击 `打开登录`，在弹出的专用 Chrome 里登录 Facebook。
4. 登录完成后关闭这个专用 Chrome。
5. 回到项目页，把项目绑定到对应的抓取浏览器账号。
6. 运行项目或创建定时任务。

抓取账号使用独立 Chrome User Data 目录，所以你可以继续正常使用日常 Chrome 的其他用户。

## Windows 打包

源码跑通后执行：

```powershell
pyinstaller FBPostCollector.spec
```

打包目标会包含 Flask 后端、网页静态文件和可选的 `tools` 目录。Whisper 不会被打包，需要用户自行安装。

## 当前实现边界

Facebook 页面结构经常变化，当前抓取器已实现 Selenium 打开链接、页面状态判断、基础 OpenGraph 信息、图片上传和 OCR 接口。点赞量、评论量、分享量、作者 ID、Reels 音频等复杂字段已经预留字段和流水线位置，后续可以在 `fb_collector/services/scraper.py` 中逐项增强。
