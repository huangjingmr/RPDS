# RPDS

本仓库是论文 **Reliability-Aware Collaborative Computing for Multi-DAG Task Offloading in Vehicular Fleets** 的 Python 实现，包含 RPDS 调度器、对比算法、论文实验、消融实验和调度正确性检查。

> 建议 GitHub 仓库名使用 `RPDS`。`TNSE` 是期刊名称，不能直接说明项目内容，也不方便以后管理其他 TNSE 投稿。

## 先从哪里看

第一次阅读建议按下面顺序：

1. [`rpds/model.py`](rpds/model.py)：车辆、任务、DAG 和输入检查。
2. [`rpds/network.py`](rpds/network.py)：STPF 路径计算。
3. [`rpds/reliability.py`](rpds/reliability.py)：副本上限和动态可靠性目标。
4. [`rpds/scheduler.py`](rpds/scheduler.py)：RPDS 的完整调度流程。
5. [`rpds/validation.py`](rpds/validation.py)：独立检查前驱约束、FIFO 和可靠性。
6. [`tests/`](tests)：最小示例和回归测试。

更完整的中文说明见 [`docs/USAGE_CN.md`](docs/USAGE_CN.md)，实验与论文图表的对应关系见 [`docs/EXPERIMENTS.md`](docs/EXPERIMENTS.md)。

## 环境安装

核心算法只使用 Python 标准库，建议 Python 3.10 或更新版本。

```bash
cd /Users/hj/Projects/RPDS
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

如果需要生成论文图片，再安装绘图库：

```bash
python -m pip install -r requirements-plot.txt
```

## 三分钟运行

先运行测试：

```bash
python -m unittest discover -s tests -v
```

运行论文中的小规模算例：

```bash
python -m rpds run examples/paper_example.json \
  --output reports/paper_example
```

输出目录包含：

- `result.json`：完整调度结果和指标；
- `copies.csv`：每个副本的车辆、开始时间和完成时间；
- `tasks.csv`：每个任务的目标可靠性、副本数和实际可靠性。

核查历史 Table IV：

```bash
python -m rpds audit-table --output reports/table_iv
```

## 复现论文实验

当前论文采用的新任务选择策略是 **replica-aware remaining-path selection**，任务确定后使用 EFT 分配车辆。

```bash
# Random、GE、FFT 主实验
python -m rpds.main_experiment \
  --output reports/main_experiment --plot

# 不同任务数量
python -m rpds.task_count_experiment \
  --output reports/task_count --plot

# 不同可靠性要求
python -m rpds.reliability_sensitivity \
  --output reports/reliability_sensitivity --plot

# 任务选择和路径选择消融
python -m rpds.selection_path_ablation \
  --output reports/selection_path_ablation --plot

# 动态与静态可靠性分配消融
python -m rpds.backup_ablation \
  --source reports/rpds_current_runs_20261017 \
  --output reports/backup_ablation
```

各入口的完整参数可通过 `-h` 查看，例如：

```bash
python -m rpds.main_experiment -h
```

## 项目结构

```text
RPDS/
├── rpds/                    # 核心算法和实验入口
├── tests/                   # 自动化测试
├── examples/                # 可直接运行的 JSON 场景
├── docs/                    # 中文使用说明和实验索引
├── original/                # GitHub 原始代码备份，仅用于追溯
├── reports/                 # 本地生成的实验结果
├── backups/                 # 修改前备份
├── plot_*.py                # 论文图片绘制脚本
├── requirements-plot.txt    # 仅绘图需要的依赖
└── pyproject.toml           # 项目与 Python 版本说明
```

## 当前模型约定

- 后继任务等待所有前驱任务的计划完成时间；
- 同一车辆按照分配顺序 FIFO 执行；
- 本地传输和返回时延为零，远程链路每跳计入 `size/rate + epsilon`；
- 同一任务的副本放在不同车辆上；
- 后继任务按照所有副本中最晚返回的时间变为可执行；
- 可靠性是独立瞬时故障模型下的解析值，不是故障注入后的实测成功率；
- 一次运行内采用固定拓扑快照，没有使用 SUMO/CARLA 或实测 V2V 信道。

## 原始代码和历史结果

`original/` 保存下载仓库中的原始程序，`backups/` 保存本次整理前的文档备份。旧任务排序规则的探索性实验和失败运行已经清理；`reports/` 只保留当前算法的正式结果、Table IV 核查及可复用的小规模算例。

历史 Table IV 不能由缺失输入数据的旧程序完整复现。仓库提供的是表格核查和基于当前 RPDS 规则的重新计算，不会把推测结果冒充原始实验。

## 上传 GitHub 前

建议仓库名称改为 `RPDS`，Description 可写：

> Reference implementation of RPDS for reliability-aware collaborative multi-DAG task offloading in vehicular fleets.

上传前执行：

```bash
python -m unittest discover -s tests -v
git status --short
```

本次整理没有替你执行 `git push`。
