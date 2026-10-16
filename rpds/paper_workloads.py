"""原生成器结构适配：记录修复，不声称恢复历史随机样本。"""
import math
import random
from .ablation_inputs import generate


def random_edges(rng, count):
    # 原任务生成.py逐对抽入出度阈值，补入口/出口；按当前论文采用1..5。
    indegree, outdegree = [0]*count, [0]*count
    edges = set()
    for i in range(count-1):
        for j in range(i+1,count):
            a,b = rng.choice(range(1,6)),rng.choice(range(1,6))
            probability=rng.choice((.3,.4,.5,.6))  # 修复原 random.choice(3)。
            if i==0 and j==count-1:
                continue
            if rng.random()<probability and outdegree[i]<a and indegree[j]<b:
                edges.add((i,j));outdegree[i]+=1;indegree[j]+=1
    for node in range(1,count):
        if not indegree[node]: edges.add((0,node))
    for node in range(count-1):
        if not outdegree[node]: edges.add((node,count-1))
    # 与原逻辑一致：补边后的最终度数不保证仍受抽样阈值限制。
    return edges


def ge_edges(rho=6):
    # 逐句恢复 GEDAG.py 中被注释的 GE 拓扑逻辑（不使用随机边权）。
    count=(rho*rho+rho-2)//2
    edges=set();end=rho-1;k=2;level=rho-1
    for i in range(count):
        for j in range(count):
            if i==0 and 0<j<rho: edges.add((i,j))
        for j in range(i,count):
            if i==end+1:
                for a in range(i+1,i+1+rho-k): edges.add((i,a))
                end+=rho-k+1;k+=1;level-=1  # 原式少计本阶段的主元任务，导致末尾自环。
                break
            if i>0 and j==i+level: edges.add((i,j))
    return count,edges


def fft_edges(rho=8):
    # 保留原FFT二叉树+蝶形结构，参数rho=8对应39个真实任务。
    count=int(2*rho+rho*math.log2(rho)-1)
    edges={(i,child) for i in range(rho) for child in (i*2+1,i*2+2)}
    pending=[(count-rho,count)]
    while pending:
        start,end=pending.pop()
        if end-start<=1: continue
        half=(end-start)//2
        for i in range(start,start+half):
            edges.update(((i-rho,i),(i-rho+half,i)))
        for i in range(start+half,end):
            edges.update(((i-rho,i),(i-rho-half,i)))
        pending.extend(((start-rho,start+half-rho),(start+half-rho,end-rho)))
    return count,edges


def tasks_for(rng, family, random_count=30):
    if family=='random': count=random_count;edges=random_edges(rng,count)
    elif family=='ge': count,edges=ge_edges()
    elif family=='fft': count,edges=fft_edges()
    else: raise ValueError(family)
    assert all(0<=u<v<count for u,v in edges)
    return [{'id':i+1,'predecessors':sorted(u+1 for u,v in edges if v==i),
             'input':rng.uniform(.1,.5),'output':rng.uniform(.1,.5),
             'workload':rng.uniform(.2,.5)} for i in range(count)]


def workload(seed, vehicles, family, rounds=3, requirement=.995, random_tasks=30):
    data=generate(seed,vehicles,tasks=1)
    # 拓扑采用明确的单车道快照，不冒称恢复了论文多车道原位置。
    rng=random.Random(100000+seed)
    data['dags']=[{'id':f'G{source:02d}_{sequence:02d}','source':source,'arrival':0.,
                   'requirement':requirement,'tasks':tasks_for(rng,family,random_tasks)}
                  for source in range(1,vehicles+1) for sequence in range(rounds)]
    data['metadata'].update(generator='original_structure_paper_parameters_v1',family=family,
                            rounds_per_source=rounds,arrival_mode='closed_loop_endogenous',
                            arrival_zero_is_placeholder=True,degree_policy='paper_1_to_5',
                            requirement=requirement,random_tasks=random_tasks)
    return data
