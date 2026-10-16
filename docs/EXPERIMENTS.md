# 实验入口与论文内容对应关系

| 论文内容 | Python入口 | 主要输出 |
|---|---|---|
| 小规模说明性算例 | `python -m rpds run examples/paper_example.json` | `result.json`, `copies.csv`, `tasks.csv` |
| 历史Table IV核查 | `python -m rpds audit-table` | 核查报告和重算数据 |
| Random/GE/FFT主实验 | `python -m rpds.main_experiment` | 各方法响应时间及汇总 |
| 任务数量实验 | `python -m rpds.task_count_experiment` | 20--80任务结果 |
| 可靠性敏感性 | `python -m rpds.reliability_sensitivity` | 99.2%--99.8%结果 |
| 任务选择消融 | `python -m rpds.selection_path_ablation` | RPDS、FCFS、EFT-order |
| 路径选择消融 | `python -m rpds.selection_path_ablation` | STPF、Dijkstra、minimum-hop |
| 动态备份消融 | `python -m rpds.backup_ablation` | dynamic与static目标 |

## 当前论文采用的算法

当前RPDS使用 replica-aware remaining-path 任务选择策略。它先预测满足可靠性要求所需全部副本的最晚完成时间，再结合任务到DAG出口的剩余路径；选择任务后，通过EFT分配车辆。

## 已保存的正式结果

带日期的正式结果位于 `reports/`。主实验、任务数量和可靠性敏感性目录同时保存逐场景输入、压缩排程、汇总 CSV、图片和运行时源码快照；消融目录保存论文使用的汇总及成对比较数据。以后调整画图样式或核对均值、标准差时可直接读取这些文件，无需重跑算法。

旧任务排序规则的探索结果、已知失败运行和重复目录已清理。
