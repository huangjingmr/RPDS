# 使用说明

## 1. 运行环境

核心调度器只依赖 Python 标准库。推荐使用 Python 3.10--3.14。Matplotlib 只用于绘图，不影响调度和测试。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-plot.txt  # 需要画图时再装
```

在 Windows PowerShell 中激活环境：

```powershell
.venv\Scripts\Activate.ps1
```

## 2. 最小运行流程

```bash
python -m unittest discover -s tests -v
python -m rpds run examples/paper_example.json --output reports/paper_example
```

如果测试通过且 `validation_issues` 为空，说明该结果满足程序检查的 DAG 前驱、车辆 FIFO、返回时延和模型可靠性约束。

## 3. 输入文件

场景使用 JSON，主要字段如下：

- `vehicles`：车辆编号、CPU频率和计算故障率；
- `links`：车辆间无向链路，给出速率或距离；
- `formation_order`：STPF 使用的编队顺序；
- `epsilon`：每跳转发时延，单位秒；
- `communication_fault_rate`：通信故障率；
- `dags`：DAG来源车辆、到达时间、可靠性要求和任务列表；
- `tasks`：输入量、输出量、计算量及前驱任务。

单位统一为：时间 `s`、数据量 `Mbit`、计算量 `Gcycle`、频率 `GHz`、速率 `Mbps`。

建议复制 `examples/paper_example.json` 后修改，不要直接改示例文件。

## 4. 如何读核心代码

### 调度主流程

`rpds/scheduler.py` 的 `Scheduler.run()` 是主流程：

1. 检查每个 DAG 的副本上限和路径可行性；
2. 建立就绪任务队列；
3. 计算每个就绪任务的优先级；
4. 选择优先级最高的任务；
5. 按 EFT 逐个放置副本，达到动态可靠性目标后停止；
6. 更新车辆 FIFO 和任务完成时间；
7. 当前驱均已规划时释放后继任务。

### 路径

`rpds/network.py` 实现方向受限子图上的 STPF。输入数据量参与边权计算，因此同一路径查询可能随任务输入大小变化。

### 可靠性

`rpds/reliability.py` 实现单副本可靠性组合、副本上限以及动态可靠性目标。等待时间不计入故障暴露时间。

### 结果检查

`rpds/validation.py` 不依赖调度器内部状态，使用输出结果重新检查主要约束。增加新算法或新实验后，都应继续调用该检查。

## 5. 常用命令

```bash
# 查看主入口帮助
python -m rpds -h

# 单场景调度
python -m rpds run INPUT.json --output OUTPUT_DIR

# 历史 Table IV 核查
python -m rpds audit-table --output reports/table_iv

# 主实验
python -m rpds.main_experiment --output reports/main_experiment --plot

# 消融实验
python -m rpds.selection_path_ablation --output reports/selection_path_ablation --plot
python -m rpds.backup_ablation --output reports/backup_ablation --plot
```

若提示缺少 `matplotlib`，去掉 `--plot` 仍可生成 CSV 数据，或执行：

```bash
python -m pip install -r requirements-plot.txt
```

## 6. 输出文件

实验入口通常生成：

- `manifest.json`：参数、时间、代码摘要等复现信息；
- `runs.csv`：每次运行的原始结果；
- `summary.csv`：按配置汇总的均值；
- `paired.csv`：同一输入下的成对比较；
- PDF/PNG：安装 Matplotlib 且带 `--plot` 时生成。

不要手工修改生成的 CSV 后再作为论文数据。如果更改算法，应使用新输出目录重跑并保留对应的 `manifest.json`。

## 7. 常见问题

**为什么项目核心叫 `rpds`？** 这是论文所提算法的缩写，适合作为代码包和仓库名称。

**为什么目录中还有旧实验？** `original/`、`backups/` 和带日期的 `reports/` 用于追溯开发过程。当前论文入口以 README 的“复现论文实验”为准。

**是否复现了最早论文中的 Table IV？** 没有。旧仓库缺少原始输入和随机种子。当前代码能够核查旧表约束，并用明确规则重新计算，但不会声称恢复了丢失的原始实验。
