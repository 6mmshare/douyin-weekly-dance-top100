# 抖音指定博主最近7天视频工具

这是 `douyin-weekly-dance-top100` 仓库中的独立子功能，用于：

1. 按精确抖音号解析指定博主主页，并抓取最近7天发布的全部公开视频；
2. 基于采集结果离线生成最近7天视频 Top100。

该工具复用仓库根目录的公共运行环境和抖音登录资料：

```text
..\.venv\Scripts\python.exe
..\data\profiles\douyin
```

但自己的配置、断点、日志和输出保存在本子目录中，不会与原Top100功能混用。

## 目录要求

```text
douyin-weekly-dance-top100-main\
├─ .venv\
├─ data\profiles\douyin\
├─ weekly_dance_ranker.py
└─ douyin_creator_recent7d_tool\
   ├─ 01_collect_recent_videos.py
   ├─ 02_rank_recent_videos.py
   ├─ creator_recent7d_core.py
   ├─ creator_recent7d_config.yaml
   └─ creators_watchlist.xlsx   # 本地自行创建，不提交GitHub
```

## 首次使用

1. 运行 `install.bat`，只检查上一级环境，不安装或下载内容；
2. 运行 `login_once.bat`，确认共享抖音Profile仍然处于登录状态；
3. 准备 `creators_watchlist.xlsx`；
4. 运行 `00_检查博主名单.bat`；
5. 运行 `01_抓取最近7天视频_前台.bat`；
6. 采集完成后运行 `02_生成最近7天Top100.bat`。

## 名单格式

Excel第一张工作表至少需要以下列：

| 启用 | 抖音名称 | 抖音账号（精确） | 粉丝数（数值） | 获赞数（数值） |
|---|---|---|---:|---:|
| 是 | 示例账号 | example_id | 100000 | 1000000 |

也可以运行 `create_watchlist_template.py` 自动生成模板。

## 严格采集规则

- 使用精确抖音号匹配账号，不用昵称模糊猜测；
- 进入已解析的博主主页；
- 只读取主页作品接口；
- 校验视频作者身份；
- 只保留发布时间明确且位于统计窗口内的视频；
- 不扫描详情页推荐流，不用旧视频或其他账号视频凑数。

## 验证码机制

新工具内部复制采用了原Top100中已验证的严格判断思路，但不会修改或导入原Top100代码：

- 骨架屏、隐藏DOM、预加载iframe不算验证码；
- 正常用户卡片或视频卡片已显示时优先判断页面正常；
- 只有可见弹窗、滑块、拼图、遮罩等强信号组合才暂停；
- 强信号需连续检测；
- 完成验证后等待页面稳定再继续当前博主。

## 两个独立功能

### 功能1：抓取最近7天全部视频

```text
01_抓取最近7天视频_前台.bat
```

输出：

```text
01_最近7天全部视频.xlsx
01_最近7天全部视频.csv
01_最近7天全部视频.json
```

### 功能2：生成Top100

```text
02_生成最近7天Top100.bat
```

这一步只读取本地JSON，不访问抖音，可以修改权重后反复运行。

默认评分：点赞35% + 评论10% + 分享20% + 收藏15% + 每小时点赞20%。

## 注意

原Top100与本工具共用同一个Chrome Profile，不能同时运行。

不要提交以下内容：

```text
creators_watchlist.xlsx
data/
logs/
output/
debug_screenshots/
.venv/
```
