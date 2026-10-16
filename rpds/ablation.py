"""可复现消融入口：python -m rpds.ablation --help。"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import statistics
import time

from .ablation_inputs import generate
from .metrics import calculate
from .model import from_dict
from .validation import validate_schedule
from .variants import VARIANTS, VariantScheduler


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path, rows):
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        if rows:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def statistics_of(values):
    """只做描述统计；不把少量种子当成显著性检验。"""
    values = [x for x in values if x is not None]
    return {'count': len(values), 'mean': statistics.mean(values) if values else None,
            'sd': statistics.stdev(values) if len(values) > 1 else None}


def execute(data, variant):
    scenario = from_dict(data)
    scheduler = VariantScheduler(scenario, variant)
    start = time.perf_counter()
    result = scheduler.run()
    elapsed = time.perf_counter() - start
    issues = validate_schedule(scenario, result, allocation=scheduler.policy.allocation)
    result['validation_issues'] = issues
    result['metrics'] = calculate(scenario, result)
    result['scheduler_seconds'] = elapsed
    dags = result['metrics']['dags']
    reliabilities = [d['reliability'] for d in dags if d['status'] == 'scheduled']
    violations = sum(d['reliability'] + 1e-10 < d['requirement']
                     for d in dags if d['status'] == 'scheduled')
    result['reliability_violations'] = violations
    result['min_modeled_reliability'] = min(reliabilities, default=None)
    if issues or violations:
        result['status'] = 'invalid'
    else:
        result['status'] = 'infeasible' if result['infeasible'] else 'complete'
    return result


def run_case(data, case, seed, output, variants):
    case_dir = output / 'cases' / case
    write_json(case_dir / 'input.json', data)
    input_hash = digest(case_dir / 'input.json')
    rows = []
    for variant in variants:
        result = execute(data, variant)
        result['input_sha256'] = input_hash
        write_json(case_dir / f'{variant}.json', result)
        metrics = result['metrics']
        complete = result['status'] == 'complete'
        rows.append({
            'case': case, 'seed': seed, 'vehicles': len(data['vehicles']),
            'variant': variant, 'input_sha256': input_hash, 'status': result['status'],
            'mean_response_s': metrics['mean_response_all_dags'] if complete else None,
            'eq14_response_s': metrics['eq14_all_vehicles'] if complete else None,
            'backups': metrics['backups'], 'copies': metrics['copies'],
            'feasible_fraction': metrics['feasible_fraction'],
            'infeasible_dags': metrics['infeasible_dags'],
            'reliability_violations': result['reliability_violations'],
            'min_modeled_reliability': result['min_modeled_reliability'],
            'communication_mbit_hops': metrics['communication_mbit_hops'],
            'fleet_cpu_utilization': metrics['fleet_cpu_utilization'],
            'scheduler_seconds': result['scheduler_seconds'],
            'validation_issues': len(result['validation_issues']),
        })
    return rows


def aggregate(rows):
    summaries = []
    for vehicles, variant in sorted({(r['vehicles'], r['variant']) for r in rows}):
        group = [r for r in rows if (r['vehicles'], r['variant']) == (vehicles, variant)]
        complete = [r for r in group if r['status'] == 'complete']
        stats = statistics_of(r['mean_response_s'] for r in complete)
        all_complete = len(complete) == len(group)
        summaries.append({
            'vehicles': vehicles, 'variant': variant, 'runs': len(group),
            'complete_runs': len(complete), 'all_runs_complete': all_complete,
            # 有失败时不输出代表全体场景的平均响应，防止幸存者偏差。
            'mean_response_s': stats['mean'] if all_complete else None,
            'response_sd_s': stats['sd'] if all_complete else None,
            'mean_response_complete_only_s': stats['mean'],
            'mean_backups': statistics.mean(r['backups'] for r in complete) if all_complete else None,
            'mean_scheduler_seconds': statistics.mean(r['scheduler_seconds'] for r in group),
            'invalid_runs': sum(r['status'] == 'invalid' for r in group),
            'infeasible_runs': sum(r['status'] == 'infeasible' for r in group),
        })
    return summaries


def paired_comparisons(rows):
    baseline = {r['case']: r for r in rows if r['variant'] == 'rpds'}
    pairs = []
    for row in rows:
        if row['variant'] == 'rpds':
            continue
        base = baseline[row['case']]
        complete = row['status'] == base['status'] == 'complete'
        response = row['mean_response_s']
        gain = None
        if complete and response > 0:
            gain = 100 * (response - base['mean_response_s']) / response
        pairs.append({'case': row['case'], 'vehicles': row['vehicles'],
                      'seed': row['seed'], 'variant': row['variant'],
                      'both_complete': complete, 'rpds_reduction_percent': gain})
    return pairs


def save_report(output, rows, summary):
    text = ['# 新生成的消融实验结果', '',
            '**不是论文旧图的复现，不可直接沿用旧提升比例。**', '',
            '三种变体分别只改变目标分配、任务选择、路由策略。各变体使用同一份输入文件。',
            '均保留独立故障可靠性检查、FIFO、前驱约束、所有副本最晚返回完成规则。', '',
            '|车辆|策略|完整运行/总运行|平均响应(s)|平均备份数|',
            '|---|---|---|---|---|']
    for r in summary:
        value = '不可作全体平均' if r['mean_response_s'] is None else f"{r['mean_response_s']:.6f}"
        text.append(f"|{r['vehicles']}|{r['variant']}|{r['complete_runs']}/{r['runs']}|{value}|{r['mean_backups']}|")
    text.extend(['', '## 解读边界', '',
                 '- 默认生成器使用同时到达的一批随机 DAG，不是论文21,000个DAG的闭环到达实验。',
                 '- 不保证 RPDS 获胜。方向受限路径与全图最短路相同时，路由变体可能完全相同。',
                 '- 描述统计以随机种子为运行单位；同一输入下的成对差异见 paired.csv。未做显著性检验。',
                 '- paired.csv 的提升比例为 (变体响应−RPDS响应)/变体响应×100%；负值表示RPDS更慢。',
                 '- 模型可靠性不是实际成功率；调度器耗时受本机负载影响，与应用响应时间分开报告。',
                 '- 不可行和约束失败不会当作成功结果；存在不完整运行时，汇总的全体平均置空。',
                 '- 原始输入、逐副本排程、逐DAG指标、校验结果、代码快照和SHA256均随结果保存。'])
    (output / '实验报告.md').write_text('\n'.join(text) + '\n', encoding='utf-8')


def plot_summary(output, summary):
    """可选绘图依赖；计算/验证本身只用标准库。"""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    for variant in VARIANTS:
        rows = sorted((r for r in summary if r['variant'] == variant), key=lambda r: r['vehicles'])
        if not rows:
            continue
        for axis, field in zip(axes, ('mean_response_s', 'mean_backups')):
            y = [float('nan') if r[field] is None else r[field] for r in rows]
            axis.plot([r['vehicles'] for r in rows], y, marker='o', label=variant)
    for axis, label in zip(axes, ('Mean DAG response time (s)', 'Mean backup count')):
        axis.set_xlabel('Number of vehicles')
        axis.set_ylabel(label)
        axis.grid(alpha=.25)
        axis.legend()
    fig.suptitle('New paired ablations (not historical reproduction)')
    fig.tight_layout()
    fig.savefig(output / 'ablation.png', dpi=180)
    fig.savefig(output / 'ablation.pdf')
    plt.close(fig)


def main(argv=None):
    parser = argparse.ArgumentParser(description='新定义的配对消融；不复现丢失的历史RR/greedy实现')
    parser.add_argument('--input', type=Path, help='显式JSON场景；不提供则生成新合成场景')
    parser.add_argument('--vehicles', type=int, nargs='+', default=[4, 6, 8, 10, 12, 14, 16])
    parser.add_argument('--seeds', type=int, nargs='+', default=list(range(5)))
    parser.add_argument('--tasks', type=int, default=30)
    parser.add_argument('--requirement', type=float, default=.995)
    parser.add_argument('--variants', nargs='+', choices=list(VARIANTS), default=['rpds', 'static', 'fcfs', 'eft', 'dijkstra'])
    parser.add_argument('--output', required=True, type=Path, help='新的空目录，禁止覆盖旧结果')
    parser.add_argument('--plot', action='store_true', help='可选：需要matplotlib')
    args = parser.parse_args(argv)
    if 'rpds' not in args.variants or len(set(args.variants)) != len(args.variants):
        parser.error('变体必须包含 rpds，且不得重复')
    if len(set(args.seeds)) != len(args.seeds) or len(set(args.vehicles)) != len(args.vehicles):
        parser.error('种子及车辆规模不能重复，否则会重复计算同一样本')
    if args.tasks < 1 or min(args.vehicles) < 2 or not 0 < args.requirement < 1:
        parser.error('不合法的场景参数')
    if args.output.exists():
        parser.error('输出目录已存在，请选择新目录；旧结果不覆盖')
    if args.input:
        data = json.loads(args.input.read_text(encoding='utf-8'))
        from_dict(data)  # 写入目录前检查参数。
    if args.plot:
        try:
            import matplotlib
        except ImportError:
            parser.error("--plot 需要安装 matplotlib；或去掉 --plot 只生成数据")
    args.output.mkdir(parents=True)
    manifest = {'created_utc': datetime.now(timezone.utc).isoformat(),
                'python': platform.python_version(), 'platform': platform.platform(),
                'arguments': {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
                'historical_reproduction': False, 'source_sha256': {}, 'status': 'running'}
    for source in sorted(Path(__file__).parent.glob('*.py')):
        target = args.output / 'source' / 'rpds' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        manifest['source_sha256'][source.name] = digest(source)
    write_json(args.output / 'manifest.json', manifest)
    rows = []
    if args.input:
        rows.extend(run_case(data, 'provided_input', None, args.output, args.variants))
    else:
        for vehicles in args.vehicles:
            for seed in args.seeds:
                data = generate(seed, vehicles, args.tasks, args.requirement)
                rows.extend(run_case(data, f'n{vehicles}_seed{seed}', seed, args.output, args.variants))
                print(f'n={vehicles}, seed={seed}: 完成 {len(args.variants)} 个变体', flush=True)
    summary = aggregate(rows)
    write_csv(args.output / 'runs.csv', rows)
    write_csv(args.output / 'summary.csv', summary)
    write_csv(args.output / 'paired.csv', paired_comparisons(rows))
    save_report(args.output, rows, summary)
    manifest['status'] = 'complete' if all(r['status'] == 'complete' for r in rows) else 'has_incomplete_runs'
    if args.plot:
        plot_summary(args.output, summary)
    write_json(args.output / 'manifest.json', manifest)
    print(f'结果保存在 {args.output}')
    return 0 if manifest['status'] == 'complete' else 2


if __name__ == '__main__':
    raise SystemExit(main())
