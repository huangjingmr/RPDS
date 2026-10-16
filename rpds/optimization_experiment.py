"""排序优化的固定开发集选择与独立验证；严禁用验证结果换候选。"""
import argparse
import csv
from datetime import datetime,timezone
import gzip
import json
from pathlib import Path
import platform
import statistics
from .ablation import write_json,write_csv,digest
from .ablation_suite import evaluate,summarize
from .paper_workloads import workload

BASELINES=('rpds','fcfs','eft')
CANDIDATES=('replica_eft','path_slack')
LABELS={'rpds':'Original RPDS','fcfs':'RPDS-FCFS','eft':'RPDS-EFT-order',
        'replica_eft':'Replica-aware order','path_slack':'Replica + path order'}


def run_case(data,folder,family,n,seed,variants):
    write_json(folder/'input.json',data);sha=digest(folder/'input.json')
    rows=[]
    for variant in variants:
        result=evaluate(data,variant);result['input_sha256']=sha
        # 仅更换压缩级别以减少落盘开销，不改变任何排程记录。
        with gzip.open(folder/f'{variant}.json.gz','wt',encoding='utf-8',compresslevel=1) as f:
            json.dump(result,f,ensure_ascii=False,allow_nan=False)
        m=result['metrics'];ok=result['status']=='complete'
        tasks=sum(len(d['tasks']) for d in data['dags'])
        rows.append(dict(case=folder.name,family=family,vehicles=n,seed=seed,variant=variant,
                         input_sha256=sha,status=result['status'],
                         mean_response_s=m['eq14_all_vehicles'] if ok else None,
                         eq14_response_s=m['eq14_all_vehicles'] if ok else None,
                         backups_per_task=m['backups']/tasks if ok else None,
                         backups=m['backups'],tasks=tasks,dags=m['total_dags'],
                         scheduler_seconds=result['scheduler_seconds'],
                         validation_issues=len(result['validation_issues']),
                         reliability_violations=result['reliability_violations'],
                         infeasible_dags=m['infeasible_dags']))
    return rows


def select_development(rows):
    base={r['case']:r for r in rows if r['variant']=='fcfs'}
    scores={}
    for candidate in CANDIDATES:
        group=[r for r in rows if r['variant']==candidate]
        valid=all(r['status']==base[r['case']]['status']=='complete' for r in group)
        scores[candidate]=statistics.mean(r['eq14_response_s']/base[r['case']]['eq14_response_s'] for r in group) if valid else None
    eligible=[c for c in CANDIDATES if scores[c] is not None]
    if not eligible:raise RuntimeError('两个候选均存在不完整开发运行，不进入验证')
    winner=min(eligible,key=lambda c:scores[c])
    if all(scores[c] is not None for c in CANDIDATES) and abs(scores[CANDIDATES[0]]-scores[CANDIDATES[1]])<1e-12:
        winner=CANDIDATES[0]
    return dict(selected=winner,scores_mean_ratio_to_fcfs=scores,
                criterion='mean over all development cases of Eq14(candidate)/Eq14(FCFS); smaller wins',
                development_seeds=list(range(5)),validation_seeds=list(range(20,40)),
                frozen_before_validation=True)


def compare(rows,candidate):
    candidates={r['case']:r for r in rows if r['variant']==candidate}
    pairs=[]
    for r in rows:
        if r['variant'] not in BASELINES:continue
        c=candidates[r['case']];ok=r['status']==c['status']=='complete'
        delta=r['eq14_response_s']-c['eq14_response_s'] if ok else None
        pairs.append(dict(case=r['case'],family=r['family'],vehicles=r['vehicles'],seed=r['seed'],baseline=r['variant'],
                          candidate=candidate,both_complete=ok,baseline_minus_candidate_s=delta,
                          candidate_reduction_percent=100*delta/r['eq14_response_s'] if ok else None))
    return pairs


def timing_summary(rows):
    result=[]
    groups=sorted({(r['family'],r['vehicles'],r['variant']) for r in rows})
    for family,n,v in groups:
        values=[r['scheduler_seconds'] for r in rows if (r['family'],r['vehicles'],r['variant'])==(family,n,v)]
        result.append(dict(family=family,vehicles=n,variant=v,mean_seconds=statistics.mean(values),
                           median_seconds=statistics.median(values)))
    return result


def plot(output,summary,timings,candidate):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    colors={'rpds':'#777777','fcfs':'#8859a8','eft':'#c47919',candidate:'#116ba5'}
    for family in ('random','ge','fft'):
        fig,axes=plt.subplots(1,3,figsize=(12,3.6))
        for v in (*BASELINES,candidate):
            group=sorted((r for r in summary if r['family']==family and r['variant']==v),key=lambda r:r['vehicles'])
            for ax,field in zip(axes[:2],('mean_response_s','backups_per_task')):
                ax.errorbar([r['vehicles'] for r in group],[r[field] if r[field] is not None else float('nan') for r in group],
                            yerr=[r[field+'_sd'] or 0 for r in group],label=LABELS[v],color=colors[v],marker='o',capsize=2,linewidth=1,markersize=3,elinewidth=.6)
            ts=sorted((r for r in timings if r['family']==family and r['variant']==v),key=lambda r:r['vehicles'])
            axes[2].plot([r['vehicles'] for r in ts],[r['median_seconds'] for r in ts],marker='o',color=colors[v],label=LABELS[v],markersize=3)
        for ax,label in zip(axes,('Eq.14 DAG response (s)','Backups per task','Median scheduler runtime (s)')):
            ax.set_xlabel('Vehicles');ax.set_ylabel(label);ax.grid(alpha=.2);ax.legend(fontsize=7)
        fig.suptitle(f'{family.upper()}: held-out seeds 20-39; bars = across-seed SD',fontsize=11)
        fig.tight_layout();fig.savefig(output/f'{family}.png',dpi=180);fig.savefig(output/f'{family}.pdf');plt.close(fig)


def report(output,summary,pairs,candidate,phase):
    lines=['# 排序改进实验', '',f'阶段：{phase}；选定候选：{candidate}。',
           '所有响应值按公式14先对每源DAG求平均，再对车辆等权平均。策略计算时间单独记录，未加到应用响应时间。',
           '候选含全部副本预测；path_slack还考虑剩余路径名义时长。它们不是原Eq21/22的等价实现，不宣称最优性、截止时间保证或独创性。','',
           '|类型|车辆|策略|完整/总数|Eq14响应(s)|备份/任务|','|---|---:|---|---|---:|---:|']
    def fmt(x):return 'NA' if x is None else f'{x:.6f}'
    for r in summary:
        lines.append(f"|{r['family']}|{r['vehicles']}|{r['variant']}|{r['complete_runs']}/{r['runs']}|{fmt(r['mean_response_s'])}|{fmt(r['backups_per_task'])}|")
    lines+=['','## 成对比较','','正的降幅表示候选更快；不能把开发集结果当独立验证证据。','',
            '|类型|基线|候选胜/平/负|平均逐实例响应降幅(%)|不完整配对|','|---|---|---|---:|---:|']
    for family in ('random','ge','fft'):
        for baseline in BASELINES:
            group=[r for r in pairs if r['family']==family and r['baseline']==baseline]
            valid=[r for r in group if r['both_complete']]
            diffs=[r['baseline_minus_candidate_s'] for r in valid]
            wins=sum(x>1e-10 for x in diffs);ties=sum(abs(x)<=1e-10 for x in diffs);losses=sum(x< -1e-10 for x in diffs)
            gain=statistics.mean(r['candidate_reduction_percent'] for r in valid) if valid else None
            lines.append(f'|{family}|{baseline}|{wins}/{ties}/{losses}|{fmt(gain)}|{len(group)-len(valid)}|')
    lines+=['','## 边界','',
            '新种子仍来自同一生成分布：单车道快照、有限3轮闭环、修复适配的随机/GE/FFT结构；不是原历史数据或真实车联网验证。',
            '备份开销、运行耗时和响应一起评估。未进行显著性检验；误差条是种子间标准差。没有因验证结果更换公式或候选。',
            '如果后续根据本验证集继续改算法，种子20..39将成为开发数据，需另设新的独立验证集。',
            '详见工程根目录PRIORITY_OPTIMIZATION.md；原算法、旧实验和论文文件均保留。']
    (output/'实验报告.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase',choices=['development','validation'],required=True)
    parser.add_argument('--selection',type=Path,help='验证时必须给出开发集冻结选择文件')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists():parser.error('输出目录已存在')
    selection=None
    if args.phase=='development':
        seeds=list(range(5));sizes=[4,8,16];variants=(*BASELINES,*CANDIDATES)
    else:
        if not args.selection:parser.error('验证必须读取开发集selection.json')
        selection=json.loads(args.selection.read_text())
        if selection.get('selected') not in CANDIDATES or not selection.get('frozen_before_validation'):parser.error('非法选择文件')
        seeds=list(range(20,40));sizes=[4,6,8,10,12,14,16];variants=(*BASELINES,selection['selected'])
    args.output.mkdir(parents=True)
    manifest=dict(phase=args.phase,created_utc=datetime.now(timezone.utc).isoformat(),status='running',
                  python=platform.python_version(),platform=platform.platform(),seeds=seeds,vehicles=sizes,
                  variants=variants,rounds=3,source_sha256={},historical_reproduction=False)
    if selection:
        manifest['selection_sha256']=digest(args.selection);write_json(args.output/'selection.json',selection)
    for source in Path(__file__).parent.glob('*.py'):
        target=args.output/'source'/'rpds'/source.name;target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(source.read_bytes());manifest['source_sha256'][source.name]=digest(source)
    protocol=Path(__file__).resolve().parents[1]/'PRIORITY_OPTIMIZATION.md'
    (args.output/'实验设置.md').write_bytes(protocol.read_bytes())
    write_json(args.output/'manifest.json',manifest)
    rows=[]
    for family in ('random','ge','fft'):
        for n in sizes:
            for seed in seeds:
                data=workload(seed,n,family,3);case=f'{family}_n{n}_seed{seed}'
                rows.extend(run_case(data,args.output/'cases'/case,family,n,seed,variants))
            write_csv(args.output/'runs.csv',rows)
            print(f'{args.phase}: {family}, n={n}, {len(seeds)} seeds done',flush=True)
    if selection is None:
        selection=select_development(rows);write_json(args.output/'selection.json',selection)
    candidate=selection['selected'];summary=summarize(rows);pairs=compare(rows,candidate);timings=timing_summary(rows)
    write_csv(args.output/'summary.csv',summary);write_csv(args.output/'paired.csv',pairs);write_csv(args.output/'timings.csv',timings)
    report(args.output,summary,pairs,candidate,args.phase)
    if args.phase=='validation':plot(args.output,summary,timings,candidate)
    manifest['status']='complete' if all(r['status']=='complete' for r in rows) else 'has_incomplete_runs'
    manifest['selected']=candidate;manifest['runs']=len(rows);write_json(args.output/'manifest.json',manifest)
    print(args.output.resolve(),manifest['status'],selection,flush=True)
    return 0 if manifest['status']=='complete' else 2


if __name__=='__main__':raise SystemExit(main())
