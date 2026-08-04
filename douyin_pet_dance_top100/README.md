# 抖音每周宠物跳舞/宠物武打 Top100

放置位置：

```text
D:\Codex\weekly_dance_top100_fixed\douyin_pet_dance_top100
```

本项目复用上一级：

- `.venv` Python环境；
- `data\profiles\douyin` 抖音登录状态。

自己的 `output`、`logs`、`data/checkpoints` 保持独立。不要与上一级其他抖音采集任务同时运行。

## 首次使用

1. `00_检查环境.bat`
2. `login_once.bat`（如果已登录，只确认后回黑框按回车）
3. `01_运行宠物Top100_前台.bat`

登录稳定后可用后台版。

## 时间范围

默认统计运行日前7个完整自然日，不包含当天；发布时间不明确或窗口外的视频不会进入详情补全和榜单。

## 结果

结果页沿用原Top100的大卡片人工筛选机制：

- 保留/删除候选；
- 只看保留/删除；
- 自动保存选择；
- 最终人工选出Top50并导出CSV。

## 关键词维护

编辑 `config.yaml` 中：

```yaml
platforms:
  douyin:
    keywords:
```

新增搜索词一行一个。排除词、宠物主体词、跳舞词和武打词也都在同一配置文件中。

## 配置文件说明

本工具只使用当前目录中的：

```text
config.yaml
```

请不要改名。`login_once.bat`、前台版、后台版、查看进度和清除断点都会自动读取它。
