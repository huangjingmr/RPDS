"""独立路径耗时实验：现有 STPF 对照前驱指针版二叉堆 Dijkstra。

不改动调度器。计时包含查询准备、边权、搜索及显式路径重建，排除输入生成与校验。
"""
import argparse
import csv
import heapq
import json
import math
import platform
import random
import statistics
import time
from pathlib import Path
from .ablation_inputs import generate
from .model import from_dict
from .network import Network
from .ablation import write_json, write_csv, digest


def reconstruct(parent, source, target):
    path = [target]
    while path[-1] != source:
        path.append(parent[path[-1]])
    return tuple(reversed(path))


def dijkstra(scenario, source, targets, size):
    """一次单源搜索；单目标提前结束，多目标共用距离和前驱。

    邻接表和消息相关边权在计时内构造，与原 STPF 查询内建图对应。
    不复制路径进堆；严格改善才更新前驱，等距由堆的车辆编号决定。
    """
    adjacency = {v: [] for v in scenario.order}
    for (u, v), rate in scenario.rates.items():
        adjacency[u].append((v, size / rate + scenario.epsilon))
    remaining = set(targets)
    distances = {source: 0.0}
    parent = {}
    queue = [(0.0, source)]
    while queue and remaining:
        distance, u = heapq.heappop(queue)
        if distance != distances[u]:
            continue
        remaining.discard(u)
        if not remaining:
            break
        for v, weight in adjacency[u]:
            candidate = distance + weight
            if candidate < distances.get(v, math.inf):
                distances[v] = candidate
                parent[v] = u
                heapq.heappush(queue, (candidate, v))
    if remaining:
        raise ValueError('存在不可达目标')
    return [(distances[t], reconstruct(parent, source, t)) for t in targets]


def stpf(scenario, source, targets, size):
    network = Network(scenario)
    # 每目标直接调用计算函数，禁止答案缓存影响冷查询耗时。
    paths = [network._calculate(source, target, size) for target in targets]
    return [(p.delay, p.nodes) for p in paths]


def verify(scenario, source, targets, size, restricted, unrestricted):
    net = Network(scenario)
    gaps = []
    for target, a, b in zip(targets, restricted, unrestricted):
        for distance, path in (a, b):
            assert path[0] == source and path[-1] == target
            assert len(set(path)) == len(path)
            assert math.isclose(distance, net.delay(path, size), rel_tol=1e-10, abs_tol=1e-10)
        assert a[0] + 1e-10 >= b[0]
        gaps.append(a[0] - b[0])
    return max(gaps, default=0.0)


def timed(call, loops):
    start = time.perf_counter_ns()
    for _ in range(loops):
        call()
    return (time.perf_counter_ns() - start) / loops / 1e6


def run_case(scenario, n, seed, source, mode, size, repeats):
    targets = [n if source <= n // 2 else 1] if mode == 'single_target' else list(scenario.order)
    calls = {'stpf': lambda: stpf(scenario, source, targets, size),
             'dijkstra': lambda: dijkstra(scenario, source, targets, size)}
    # 预热及正确性检查在计时之外；预热不会保留查询答案。
    gap = verify(scenario, source, targets, size, calls['stpf'](), calls['dijkstra']())
    loops = 10 if mode == 'single_target' else 1
    rng = random.Random(seed * 10000 + n * 10 + source)
    rows = []
    for repeat in range(repeats):
        order = list(calls)
        rng.shuffle(order)
        for algorithm in order:
            elapsed = timed(calls[algorithm], loops)
            rows.append(dict(vehicles=n, seed=seed, source=source, mode=mode,
                             algorithm=algorithm, repeat=repeat, loops=loops,
                             targets=len(targets), edges=len(scenario.rates)//2,
                             input_mbit=size, milliseconds=elapsed, max_delay_gap_s=gap))
    return rows


def summarize(rows):
    result = []
    for n, mode in sorted({(r['vehicles'], r['mode']) for r in rows}):
        entry = {'vehicles': n, 'mode': mode}
        for algorithm in ('stpf', 'dijkstra'):
            group = [r for r in rows if (r['vehicles'], r['mode'], r['algorithm']) == (n, mode, algorithm)]
            # 先取每个拓扑/源点重复计时的中位数，再对场景中位数汇总。
            cases = {(r['seed'],r['source']) for r in group}
            values = [statistics.median(r['milliseconds'] for r in group if (r['seed'],r['source']) == case)
                      for case in cases]
            entry[algorithm+'_median_ms'] = statistics.median(values)
            entry[algorithm+'_min_case_ms'] = min(values)
            entry[algorithm+'_max_case_ms'] = max(values)
            entry['cases'] = len(values)
        entry['stpf_over_dijkstra'] = entry['stpf_median_ms']/entry['dijkstra_median_ms']
        result.append(entry)
    return result


def report(output, summary):
    lines = ['# 独立路径计算耗时实验', '',
             '当前 STPF 实现 vs 二叉堆/前驱指针 Dijkstra；同一源到所有目标时 Dijkstra 仅搜索一次。',
             '单位毫秒。先取每个拓扑/源点的重复计时中位数，再取各场景中位数；不是置信区间。', '',
             '|车辆|查询类型|STPF(ms)|Dijkstra(ms)|STPF/Dijkstra|', '|---|---|---:|---:|---:|']
    for r in summary:
        mode = '单目标（中点/端点到远端）' if r['mode']=='single_target' else '同源所有目标（全部重建路径）'
        lines.append(f"|{r['vehicles']}|{mode}|{r['stpf_median_ms']:.4f}|{r['dijkstra_median_ms']:.4f}|{r['stpf_over_dijkstra']:.2f}|")
    lines += ['', '比值大于1表示当前STPF更慢。这里测量路径算法的实际计算耗时，不是网络传输时延或任务响应时间。',
              '', '计时范围包括每次查询的邻接/子图构建、边权计算、搜索和路径重建；排除场景生成、文件保存和正确性校验。缓存关闭，先预热，交替随机安排两算法测量顺序，保留原始重复数据。',
              '', 'STPF保留原代码的前向/后向扫描及最短路径节点信息；Dijkstra只返回距离和一条路径。因此这是当前组件实现的替换成本比较，不是相同附加输出的微操作比较。',
              '', '单源多目标对应当前RPDS为一个任务检查全部候选车辆的需求。STPF仍逐目标运行，Dijkstra共用单源搜索；两者均显式重建每条路径。不得把本结果解释为所有STPF优化实现的理论下界。',
              '', '场景：固定通信半径200m、相邻间距50–120m的稀疏单车道网络，5个种子，源点为两端与中点，默认消息0.5Mbit。车数增大意味着车队变长，不意味着密度增大。详见manifest与保存的inputs。',
              '', '运行受Python实现、解释器和本机负载影响，未做CPU绑定；计时重复不是独立拓扑样本。用于轻量诊断，不单独证明普遍性能优势。']
    (output/'结果说明.md').write_text('\n'.join(lines)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--vehicles',type=int,nargs='+',default=[16,32,64,128,256])
    parser.add_argument('--seeds',type=int,nargs='+',default=[0,1,2,3,4])
    parser.add_argument('--repeats',type=int,default=5)
    parser.add_argument('--size',type=float,default=.5)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.output.exists(): parser.error('输出目录已存在，请使用新目录')
    if min(args.vehicles)<2 or args.repeats<1 or not math.isfinite(args.size) or args.size<=0:
        parser.error('参数必须为合法正数，车辆数至少2')
    args.output.mkdir(parents=True)
    manifest={'python':platform.python_version(),'platform':platform.platform(),
              'arguments':{k:str(v) if isinstance(v,Path) else v for k,v in vars(args).items()},
              'status':'running','source_sha256':{}}
    for source in Path(__file__).parent.glob('*.py'):
        target=args.output/'source'/'rpds'/source.name
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(source.read_bytes())
        manifest['source_sha256'][source.name]=digest(source)
    write_json(args.output/'manifest.json',manifest)
    rows=[]
    for n in args.vehicles:
        for seed in args.seeds:
            data=generate(seed,n,tasks=1)
            write_json(args.output/'inputs'/f'n{n}_seed{seed}.json',data)
            scenario=from_dict(data)
            for source in sorted({1,(n+1)//2,n}):
                for mode in ('single_target','all_targets'):
                    rows.extend(run_case(scenario,n,seed,source,mode,args.size,args.repeats))
            print(f'n={n}, seed={seed} 完成',flush=True)
    summary=summarize(rows)
    write_csv(args.output/'timings.csv',rows)
    write_csv(args.output/'summary.csv',summary)
    report(args.output,summary)
    manifest['status']='complete'
    manifest['max_observed_delay_gap_s']=max(r['max_delay_gap_s'] for r in rows)
    write_json(args.output/'manifest.json',manifest)
    print(args.output.resolve())


if __name__=='__main__': main()
