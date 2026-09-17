"""Kaggle 引导数据集的下载与入库（规格 §5.2/§5.3/§3.2/§10.2、计划 Task 12）。

两个模块刻意分开，因为它们的失败模式完全不同：
- `ingest.kaggle_subset`：需要网络与凭证（`KAGGLE_USERNAME`/`KAGGLE_KEY` 或
  `~/.kaggle/kaggle.json`），失败时必须给出**可操作**的提示，绝不整包下载 48.52 GB。
- `ingest.load_bootstrap`：需要数据库与常量层（先 `constants.load.load_constants`），
  幂等、可中断续跑，且必须按规格 §3.2 处理 2018 的版本归属边界。

`kaggle` / `python-dotenv` 只在**真正要下载时**才 import（见 `kaggle_subset`），
故未安装 `[ingest]` extra 的环境里测试套件照常收集与运行。
"""
