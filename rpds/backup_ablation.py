"""复用冻结的新排序结果，只补跑静态可靠性分配；不改历史数据。"""
import argparse
import csv
import gzip
import json
from pathlib import Path
import statistics
from .ablation import digest, write_csv, write_json
from .ablation_suite import evaluate, summarize
from .variants import Policy, VARIANTS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('输出目录已存在，请换一个目录')
    # 先核对影响排程和验证的源码，防止跨版本直接拼接结果。
    manifest = json.loads((args.source / 'manifest.json').read_text())
    modules = ('scheduler.py', 'variants.py', 'priority_optimization.py', 'closed_loop.py',
               'model.py', 'network.py', 'reliability.py', 'metrics.py', 'validation.py')
    for name in modules:
        if digest(Path(__file__).parent / name) != manifest['source_sha256'][name]:
            raise ValueError(f'源码已改变，不能直接复用: {name}')
    with (args.source / 'runs.csv').open(encoding='utf-8-sig') as stream:
        originals = [r for r in csv.DictReader(stream) if r['variant'] == 'path_slack']
    if len(originals) != 420:
        raise ValueError('预期完整的420个冻结场景')
    args.output.mkdir(parents=True)
    VARIANTS['path_slack_static'] = Policy(allocation='static', priority='path_slack')
    # 冻结新增脚本和原始源码，独立保存所有新增运行。
    for source in Path(__file__).parent.glob('*.py'):
        target = args.output / 'source' / 'rpds' / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    write_json(args.output / 'manifest.json', dict(status='running', source=str(args.source.resolve()),
               source_manifest_sha256=digest(args.source / 'manifest.json'),
               changed_factor='dynamic versus static reliability allocation; both use path_slack',
               historical_reproduction=False))
    rows, pairs = [], []
    for index, row in enumerate(originals, 1):
        folder = args.source / 'cases' / row['case']
        if digest(folder / 'input.json') != row['input_sha256']:
            raise ValueError('输入哈希不匹配')
        data = json.loads((folder / 'input.json').read_text())
        with gzip.open(folder / 'path_slack.json.gz', 'rt') as stream:
            original = json.load(stream)
        if original['status'] != 'complete' or original['validation_issues'] or original['reliability_violations']:
            raise ValueError('原始运行未通过验证')
        if original['input_sha256'] != row['input_sha256']:
            raise ValueError('原始结果输入哈希不匹配')
        result = evaluate(data, 'path_slack_static')
        result['input_sha256'] = row['input_sha256']
        output_case = args.output / 'cases' / row['case']
        output_case.mkdir(parents=True)
        with gzip.open(output_case / 'path_slack_static.json.gz', 'wt', encoding='utf-8', compresslevel=1) as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False)
        task_count = sum(len(d['tasks']) for d in data['dags'])
        for variant, run in (('path_slack', original), ('path_slack_static', result)):
            m = run['metrics']
            rows.append(dict(case=row['case'], family=row['family'], vehicles=int(row['vehicles']),
                seed=int(row['seed']), variant=variant, status=run['status'],
                mean_response_s=m['eq14_all_vehicles'], backups_per_task=m['backups']/task_count,
                validation_issues=len(run['validation_issues']), reliability_violations=run['reliability_violations']))
        a, b = original['metrics'], result['metrics']
        ok = result['status'] == 'complete'
        pairs.append(dict(case=row['case'], family=row['family'], vehicles=int(row['vehicles']),
            both_complete=ok, response_reduction_percent=100*(b['eq14_all_vehicles']-a['eq14_all_vehicles'])/b['eq14_all_vehicles'] if ok else None,
            backups_reduction_percent=100*(b['backups']-a['backups'])/b['backups'] if ok and b['backups'] else None))
        if index % 20 == 0:
            write_csv(args.output / 'runs.csv', rows)
            print(f'{index}/420 static runs complete', flush=True)
    write_csv(args.output / 'paired.csv', pairs)
    summary = summarize(rows)
    write_csv(args.output / 'summary.csv', summary)
    lines = ['# 新RPDS：动态与静态备份分配消融', '',
        '固定新的副本感知DAG路径排序和STPF；只替换可靠性目标为静态rho。复用420个动态运行，补跑420个静态运行。',
        '三种工作负载、车辆4/6/8/10/12/14/16、种子20–39、每源3个闭环DAG。输入序列相同；后续实际到达时刻由各策略前一DAG完成时刻决定。',
        '响应按Eq14计算；备份不含主副本。未向响应注入策略计算耗时，不绘制耗时图。不复现历史数据。', '',
        '|工作负载|动态响应平均降幅%|动态备份平均降幅%|响应胜/平/负|', '|---|---:|---:|---|']
    for family in ('random', 'ge', 'fft'):
        group = [p for p in pairs if p['family'] == family and p['both_complete']]
        values = [p['response_reduction_percent'] for p in group]
        backups = [p['backups_reduction_percent'] for p in group if p['backups_reduction_percent'] is not None]
        wins = sum(v > 1e-9 for v in values)
        losses = sum(v < -1e-9 for v in values)
        lines.append(f'|{family}|{statistics.mean(values):.4f}|{statistics.mean(backups):.4f}|{wins}/{len(values)-wins-losses}/{losses}|')
    issues = sum(r['validation_issues'] + r['reliability_violations'] for r in rows)
    incomplete = sum(r['status'] != 'complete' for r in rows)
    lines += ['', f'约束及可靠性问题数：{issues}；非完整运行数：{incomplete}。',
        '百分比为逐场景配对降幅的平均值，正数表示动态方案较低；误差条为跨种子标准差。',
        '这是整个闭环调度方案的单因素替换结果，不能解释为相同调度状态下的纯副本数量效应，也不能据此断言该模块比其他模块贡献最大。']
    (args.output / '实验报告.md').write_text('\n'.join(lines)+'\n')
    plot(args.output, summary)
    write_json(args.output / 'completion.json', dict(status='complete' if not issues and not incomplete else 'has_errors', reused=420, new_runs=420))
    print('\n'.join(lines), flush=True)


def plot(output, summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    for family in ('random', 'ge', 'fft'):
        fig, axes = plt.subplots(1, 2, figsize=(8, 3.4))
        for variant, label in (('path_slack', 'RPDS (dynamic)'), ('path_slack_static', 'RPDS-static')):
            group = sorted((r for r in summary if r['family'] == family and r['variant'] == variant), key=lambda r:r['vehicles'])
            for ax, field, ylabel in zip(axes, ('mean_response_s', 'backups_per_task'), ('Eq.14 response time (s)', 'Backups per task')):
                ax.errorbar([r['vehicles'] for r in group], [r[field] for r in group], yerr=[r[field+'_sd'] for r in group], marker='o', markersize=3, capsize=2, label=label)
                ax.set_xlabel('Vehicles'); ax.set_ylabel(ylabel); ax.grid(alpha=.2)
        for ax in axes: ax.legend(fontsize=8)
        fig.suptitle(f'{family.upper()}: reliability allocation ablation (mean ± SD)')
        fig.tight_layout()
        fig.savefig(output / f'{family}.pdf')
        fig.savefig(output / f'{family}.png', dpi=180)
        plt.close(fig)


if __name__ == '__main__':
    main()
