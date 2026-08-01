# Douyin Weekly Dance Top100

一个面向 Windows 的抖音公开视频候选采集与排序工具。程序按上一段完整时间窗口搜索公开视频，补全互动数据，生成 HTML、Excel 和 CSV 报告，方便人工筛选最终 Top50。

> 本项目使用正常浏览器会话访问公开页面，不绕过验证码、不破解平台签名，也不会自动推断画面中人物的性别或年龄。请遵守所在地法律、平台条款和合理访问频率。

## 功能

- Playwright 持久化登录状态
- 前台可见模式与屏幕外后台兼容模式
- 搜索阶段和详情阶段断点续跑
- 每 20 条原子保存进度
- 随机访问间隔、批次休息和连续失败退避
- 验证码、登录失效、空数据时安全停止
- 任务锁，避免两个进程同时占用同一浏览器资料目录
- 按点赞、分享、收藏、评论、发布后点赞速度和文本匹配度排序
- 输出 HTML 人工筛选页、Excel、CSV 和诊断数据

## 隐私与安全

以下内容不会被 Git 跟踪，**不要手动上传**：

- `data/profiles/`：Cookie、LocalStorage、IndexedDB 等登录状态
- `data/checkpoints/`：断点和候选数据
- `output/`：采集结果和公开视频元数据
- `logs/`：运行日志
- `debug_screenshots/`：错误或验证码截图

登录目录相当于账号凭证。不要分享，不要提交到公开仓库。

## 环境

- Windows 10/11
- Python 3.10–3.12
- Chrome/Chromium 由 Playwright 安装

## 安装

1. 下载或克隆仓库。
2. 双击 `install.bat`。
3. 安装完成后双击 `login_once.bat`。
4. 在弹出的专用浏览器中正常登录抖音，确认页面可用后回到命令窗口按回车。

也可以在命令行运行：

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe -m playwright install chromium
```

## 使用

后台兼容模式：

```bat
run_weekly.bat
```

该模式使用正常 Chromium 内核，但把窗口移动到屏幕外，以避免部分站点在真正无头模式下返回空数据。

前台可见模式：

```bat
run_visible.bat
```

用于首次检查、验证码、登录失效或页面改版排查。前后台模式共用登录状态和断点。

查看进度：

```bat
SHOW_PROGRESS.bat
```

打开最近结果：

```bat
OPEN_LATEST_RESULT.bat
```

重置当前统计周期断点：

```bat
RESET_CURRENT_PROGRESS.bat
```

该操作不会删除 `data/profiles/douyin` 登录状态。

## 配置

主要配置位于 `config.yaml`：

- `platforms.douyin.keywords`：搜索词
- `filters.*`：文字筛选和排除规则
- `scoring.*`：排序权重
- `browser.max_detail_visits_per_platform`：最多补全数量
- `browser.checkpoint_batch_size`：断点保存批次
- `browser.detail_delay_*`：详情访问随机间隔
- `browser.batch_rest_*`：批次休息时间

当前配置会排除明确出现的男性、多人/组合、女团、教学/教程、合集、搬运、虚拟人物和未成年人文字信号。最终内容是否符合目标，仍需人工检查封面与原视频。

## 输出

默认输出到：

```text
output/<统计日期范围>/
```

包括：

- `weekly_dance_top100.html`
- `weekly_dance_top100.xlsx`
- CSV/JSON 结果与诊断信息

## 工作原理

1. 计算上一段完整统计窗口。
2. 逐个关键词读取公开搜索响应和 DOM 视频链接。
3. 去重并进行宽松文字初筛。
4. 按候选热度选择需要补全的详情页。
5. 分批补全互动数和发布时间，并持续保存断点。
6. 对合格候选做百分位评分并生成 Top100 报告。

## 已知限制

- 抖音页面和接口可能改版，解析逻辑可能需要维护。
- 平台可能要求验证码；项目不会尝试绕过。
- 网页端公开字段可能缺失，报告会标记数据质量。
- 文本筛选不能可靠判断画面内容、性别或年龄，必须人工复核。
- 搜索结果受账号、地区、登录状态和平台个性化影响，不是官方全站榜单。

## 许可证

MIT License。详见 [LICENSE](LICENSE)。
