"""保留原表、约束核查、固定分配重计时、完整 RPDS 重跑：四类证据分开。"""
from collections import defaultdict
from dataclasses import asdict, replace
import csv
import json
import math
from pathlib import Path
from .model import from_dict
from .scheduler import Scheduler
from .metrics import calculate
from .validation import validate_schedule

ROOT = Path(__file__).resolve().parents[1]


def read_inputs():
    scenario = from_dict(json.loads((ROOT / 'examples/paper_example.json').read_text(encoding='utf-8')))
    rows = json.loads((ROOT / 'examples/historical_table_iv.json').read_text(encoding='utf-8'))['rows']
    return scenario, rows


def replay(scenario, rows, historical):
    """固定原表顺序/车辆数，不把它说成重新运行 RPDS。"""
    scheduler = Scheduler(scenario)
    lookup = {t.id: (d, t) for d in scenario.dags for t in d.tasks}
    grouped = defaultdict(list)
    for row in rows:
        grouped[row['task']].append(row)
    for tid, group in grouped.items():
        dag, task = lookup[tid]
        copies = []
        for row in group:
            vehicle = scenario.vehicles[row['vehicle']]
            p = scheduler.placement(dag, task, vehicle)
            copy = scheduler.candidate(dag, task, p)
            if historical:
                copy = replace(copy, start=row['start'], cpu_finish=row['start'] + p.execution, finish=row['finish'])
            copies.append(copy)
            scheduler.copies.append(copy)
            scheduler.free[vehicle.id] = copy.cpu_finish
        scheduler.task_results[dag.id, tid] = {'dag': dag.id, 'task': tid,
            'finish': max(c.finish for c in copies), 'reliability': 1 - math.prod(1 - c.reliability for c in copies)}
    return scheduler.result()


def compare(rows, fresh):
    by_key = {(c['task'], c['vehicle']): c for c in fresh['copies']}
    output = []
    for row in rows:
        c = by_key.get((row['task'], row['vehicle']))
        output.append({**row, 'new_start': c['start'] if c else None, 'new_finish': c['finish'] if c else None,
                       'matched_at_4_decimals': c is not None and round(c['start'], 4) == row['start']
                        and round(c['finish'], 4) == row['finish']})
    return output


def audit_table(folder):
    from .__main__ import write_outputs
    scenario, rows = read_inputs()
    original = replay(scenario, rows, historical=True)
    fixed = replay(scenario, rows, historical=False)
    fresh = Scheduler(scenario).run()
    fresh['validation_issues'] = validate_schedule(scenario, fresh)
    fresh['metrics'] = calculate(scenario, fresh)
    if fresh['validation_issues']:
        raise RuntimeError(fresh['validation_issues'])
    write_outputs(folder / 'rpds_recomputed', fresh)
    fixed['metrics'] = calculate(scenario, fixed)
    fixed['validation_issues'] = validate_schedule(scenario, fixed, require_complete=False)
    write_outputs(folder / 'fixed_assignments_retimed', fixed)
    # 每个表项四舍五入误差不超过 0.00005；两个时间相减用 0.00011 容差。
    issues = validate_schedule(scenario, original, tolerance=.00011, require_complete=False)
    comparison = compare(rows, fresh)
    report = {
        'historical_source_run_reproduced': False,
        'reason': '仓库未提交 car.txt、DAG*.txt、carpath.txt、carR.txt、历史随机种子或原始日志。',
        'paper_table_matched_by_corrected_rpds': len(rows) == len(fresh['copies']) and all(r['matched_at_4_decimals'] for r in comparison),
        'historical_table_constraint_issues': issues,
        'historical_table_metrics': calculate(scenario, original),
        'fixed_assignment_retiming_metrics': fixed['metrics'],
        'corrected_rpds_metrics': fresh['metrics'],
        'matched_historical_rows': sum(r['matched_at_4_decimals'] for r in comparison),
        'historical_rows': len(rows),
        'new_task_order': [s['selected_task'] for s in fresh['steps']],
        'comparison': comparison,
    }
    (folder / 'audit.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    with (folder / 'comparison.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=list(comparison[0])); writer.writeheader(); writer.writerows(comparison)
    lines = ['# Table IV 核查结果', '', '历史原始程序实验：**未复现**。' + report['reason'],
             '论文示例参数已重建；它不是缺失的原始实验数据。所有结果为模型预测。', '',
             f"修正版与原表吻合的列：{report['matched_historical_rows']}/{len(rows)}（按四位小数比较）。",
             f"修正版任务顺序：{report['new_task_order']}", '',
             '| 数据口径 | 副本数 | 平均 DAG 响应时间（秒） |', '|---|---:|---:|']
    for title, metrics in [('原表出口统计（时序不合法）', report['historical_table_metrics']),
                            ('固定原分配仅重计时（不是 RPDS 重跑）', fixed['metrics']),
                            ('修正版完整 RPDS', fresh['metrics'])]:
        lines.append(f"| {title} | {metrics['copies']} | {metrics['mean_response_all_dags']:.9f} |")
    lines += ['', '原文 0.4815 秒与原表三个出口 τ5/τ10/τ15 的平均值不一致。',
              '不能据此证明某段原代码取了全局最大的三个时间，也不能据此推断大规模图表的确切误差。',
              '', '## 原表约束违规（0.00011 秒容差）', ''] + ['- ' + x for x in issues]
    lines += ['', '## 解释边界', '',
              '- epsilon=0.001 秒取自 Appendix B；正文小案例未单独说明。',
              '- 车辆方向按 Fig4 推广为编号顺序；其他车辆对没有完整几何定义。',
              '- 修正版重算使用每任务输入大小确定 STPF，返回路径为其逆序。',
              '- 未重跑 Fig6–Fig10，未修复或验证旧 HEFT/Local/MaxRe/RR 基线。',
              '- 完整逐副本输出见 rpds_recomputed/copies.csv；ready 队列和 Eq17 目标见 result.json。']
    (folder / '核查报告.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k in ('historical_source_run_reproduced', 'matched_historical_rows', 'new_task_order')}, ensure_ascii=False))
