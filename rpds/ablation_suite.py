"""论文参数、原结构适配的闭环配对消融，20种子固定计划。"""
import argparse
from collections import defaultdict
from datetime import datetime,timezone
import gzip
import hashlib
import json
from pathlib import Path
import platform
import statistics
import time
from .ablation import write_json,write_csv,digest
from .closed_loop import run_closed_loop
from .metrics import calculate
from .paper_workloads import workload
from .validation import validate_schedule
from .variants import VARIANTS

STRATEGIES=('rpds','fcfs','eft','static','dijkstra')
LABELS={'rpds':'RPDS','fcfs':'RPDS-FCFS','eft':'RPDS-EFT-order',
        'static':'RPDS-Static','dijkstra':'RPDS-Dijkstra'}


def evaluate(data,variant):
    start=time.perf_counter()
    scenario,result,arrivals=run_closed_loop(data,variant)
    elapsed=time.perf_counter()-start
    issues=validate_schedule(scenario,result,allocation=VARIANTS[variant].allocation)
    metrics=calculate(scenario,result)
    bad=sum(d['reliability']+1e-10<d['requirement'] for d in metrics['dags'] if d['status']=='scheduled')
    status='invalid' if issues or bad else ('infeasible' if result['infeasible'] else 'complete')
    result.update(metrics=metrics,validation_issues=issues,status=status,arrivals=arrivals,
                  scheduler_seconds=elapsed,reliability_violations=bad)
    return result


def run_case(data,folder,case,family,n,seed):
    write_json(folder/'input.json',data)
    input_hash=digest(folder/'input.json')
    rows=[]
    for variant in STRATEGIES:
        result=evaluate(data,variant)
        result['input_sha256']=input_hash
        with gzip.open(folder/f'{variant}.json.gz','wt',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,allow_nan=False)
        m=result['metrics'];complete=result['status']=='complete'
        tasks=sum(len(d['tasks']) for d in data['dags'])
        rows.append(dict(case=case,family=family,vehicles=n,seed=seed,variant=variant,
                         input_sha256=input_hash,status=result['status'],
                         mean_response_s=m['mean_response_all_dags'] if complete else None,
                         eq14_response_s=m['eq14_all_vehicles'] if complete else None,
                         backups_per_task=m['backups']/tasks if complete else None,
                         backups=m['backups'],tasks=tasks,dags=m['total_dags'],
                         infeasible_dags=m['infeasible_dags'],feasible_fraction=m['feasible_fraction'],
                         validation_issues=len(result['validation_issues']),reliability_violations=result['reliability_violations'],
                         min_reliability=min((d['reliability'] for d in m['dags'] if d['status']=='scheduled'),default=None),
                         scheduler_seconds=result['scheduler_seconds']))
    return rows


def summarize(rows):
    groups=defaultdict(list)
    for row in rows: groups[row['family'],row['vehicles'],row['variant']].append(row)
    summary=[]
    for (family,n,variant),group in sorted(groups.items()):
        complete=[r for r in group if r['status']=='complete'];all_complete=len(complete)==len(group)
        entry=dict(family=family,vehicles=n,variant=variant,runs=len(group),complete_runs=len(complete),
                   invalid_runs=sum(r['status']=='invalid' for r in group),
                   infeasible_runs=sum(r['status']=='infeasible' for r in group))
        for field in ('mean_response_s','backups_per_task'):
            values=[r[field] for r in complete]
            entry[field]=statistics.mean(values) if all_complete else None
            entry[field+'_sd']=statistics.stdev(values) if all_complete and len(values)>1 else None
        summary.append(entry)
    return summary


def paired(rows):
    base={r['case']:r for r in rows if r['variant']=='rpds'}
    result=[]
    for row in rows:
        if row['variant']=='rpds':continue
        rpds=base[row['case']];ok=row['status']==rpds['status']=='complete'
        difference=row['mean_response_s']-rpds['mean_response_s'] if ok else None
        result.append(dict(case=row['case'],family=row['family'],vehicles=row['vehicles'],seed=row['seed'],
                           variant=row['variant'],both_complete=ok,
                           alternative_minus_rpds_s=difference,
                           rpds_reduction_percent=100*difference/row['mean_response_s'] if ok else None))
    return result


def plot(output,summary):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors={'rpds':'#1764a3','fcfs':'#9864a6','eft':'#d17d17','static':'#538b3d'}
    for family in sorted({r['family'] for r in summary}):
        fig,axes=plt.subplots(1,3,figsize=(12,3.6))
        panels=[(('rpds','fcfs','eft'),'mean_response_s','(a) Task ordering','Mean DAG response (s)'),
                (('rpds','static'),'mean_response_s','(b) Reliability allocation','Mean DAG response (s)'),
                (('rpds','static'),'backups_per_task','(c) Replication overhead','Backups per task')]
        for ax,(variants,field,title,ylabel) in zip(axes,panels):
            for variant in variants:
                group=sorted((r for r in summary if r['family']==family and r['variant']==variant),key=lambda r:r['vehicles'])
                y=[r[field] if r[field] is not None else float('nan') for r in group]
                errors=[r[field+'_sd'] or 0 for r in group]
                ax.errorbar([r['vehicles'] for r in group],y,yerr=errors,label=LABELS[variant],
                            color=colors[variant],marker={'rpds':'o','fcfs':'s','eft':'^','static':'D'}[variant],
                            capsize=2,linewidth=1.3,markersize=4,elinewidth=.65)
            ax.set_title(title,fontsize=10);ax.set_xlabel('Vehicles');ax.set_ylabel(ylabel)
            ax.grid(alpha=.2);ax.legend(fontsize=7)
        fig.suptitle(f'{family.upper()}: closed-loop arrivals; error bars = across-seed SD',fontsize=11)
        fig.tight_layout();fig.savefig(output/f'{family}.png',dpi=200)
        fig.savefig(output/f'{family}.pdf');plt.close(fig)


def report(output,summary,pairs):
    lines=['# 新闭环消融实验结果','',
           '这是原结构适配、按当前论文参数重新生成的实验，不是历史21,000个DAG或原RR/greedy图的复现。',
           '每源3个DAG；首个同时在0时刻到达，后续在该策略的前一个DAG完成时到达。所有策略共享相同拓扑、任务序列、参数，实际到达时刻因完成时间不同而不同。结果包括起始和收尾阶段，不代表稳态。',
           '主图误差条为跨种子样本标准差，不是置信区间。配对结果每种子一行，不把同一运行内DAG当独立样本。',
           'FFT保留39个真实任务及多出口，完成时间取所有出口最晚副本返回，不添加虚拟计算任务。', '',
           '|类型|车辆|策略|完整/总运行|平均响应(s)|标准差(s)|备份/任务|', '|---|---:|---|---|---:|---:|---:|']
    def fmt(x): return 'NA' if x is None else f'{x:.5f}'
    for r in summary:
        lines.append(f"|{r['family']}|{r['vehicles']}|{LABELS[r['variant']]}|{r['complete_runs']}/{r['runs']}|{fmt(r['mean_response_s'])}|{fmt(r['mean_response_s_sd'])}|{fmt(r['backups_per_task'])}|")
    lines+=['','## 配对方向概览','','正差值表示RPDS更快。只列完整配对的胜/平/负，不能替代差异大小和不确定性。','',
            '|类型|替代策略|RPDS更快|相同|RPDS更慢|不完整配对|','|---|---|---:|---:|---:|---:|']
    for family in sorted({r['family'] for r in summary}):
        for variant in STRATEGIES[1:]:
            group=[r for r in pairs if r['family']==family and r['variant']==variant]
            values=[r['alternative_minus_rpds_s'] for r in group if r['both_complete']]
            lines.append(f"|{family}|{LABELS[variant]}|{sum(x>1e-10 for x in values)}|{sum(abs(x)<=1e-10 for x in values)}|{sum(x< -1e-10 for x in values)}|{len(group)-len(values)}|")
    lines+=['','## 路径模块说明','',
            'RPDS-Dijkstra的响应/备份数据在上表和CSV完整报告。该组件实现的总调度耗时不可用来声称STPF搜索更快；独立路径计时见既有path_timing_20261015报告（当前STPF实现较慢）。',
            '', '生成器修复、拓扑差异、FCFS定义、运行方式详见工程根目录 CLOSED_LOOP_ABLATION.md。未经验证的截止时间、公平性、总体等效性、统计显著性或普遍优势不由本实验推出。']
    (output/'实验报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vehicles',nargs='+',type=int,default=[4,6,8,10,12,14,16])
    parser.add_argument('--seeds',nargs='+',type=int,default=list(range(20)))
    parser.add_argument('--families',nargs='+',choices=['random','ge','fft'],default=['random','ge','fft'])
    parser.add_argument('--rounds',type=int,default=3)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--plot',action='store_true')
    args=parser.parse_args()
    if args.output.exists():parser.error('选择尚不存在的新输出目录')
    if min(args.vehicles)<2 or args.rounds<1:parser.error('车辆至少2，每源DAG至少1')
    for values in (args.vehicles,args.seeds,args.families):
        if len(set(values))!=len(values):parser.error('不允许重复配置')
    if args.plot:
        import matplotlib
    args.output.mkdir(parents=True)
    manifest=dict(created_utc=datetime.now(timezone.utc).isoformat(),status='running',
                  python=platform.python_version(),platform=platform.platform(),
                  arguments={k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
                  historical_reproduction=False,source_sha256={})
    for source in Path(__file__).parent.glob('*.py'):
        target=args.output/'source'/'rpds'/source.name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(source.read_bytes());manifest['source_sha256'][source.name]=digest(source)
    write_json(args.output/'manifest.json',manifest)
    rows=[]
    for family in args.families:
        for n in args.vehicles:
            for seed in args.seeds:
                data=workload(seed,n,family,args.rounds);case=f'{family}_n{n}_seed{seed}'
                rows.extend(run_case(data,args.output/'cases'/case,case,family,n,seed))
            write_csv(args.output/'runs.csv',rows)
            print(f'{family}, n={n}: {len(args.seeds)}个种子 × 5策略完成',flush=True)
    summary=summarize(rows);pairs=paired(rows)
    write_csv(args.output/'summary.csv',summary);write_csv(args.output/'paired.csv',pairs)
    report(args.output,summary,pairs)
    if args.plot:plot(args.output,summary)
    manifest['status']='complete' if all(r['status']=='complete' for r in rows) else 'has_incomplete_runs'
    manifest['runs']=len(rows);manifest['dags_per_variant']=sum(r['dags'] for r in rows if r['variant']=='rpds')
    write_json(args.output/'manifest.json',manifest)
    print(args.output.resolve(),manifest['status'])
    return 0 if manifest['status']=='complete' else 2


if __name__=='__main__':raise SystemExit(main())
