"""每源上一DAG出口全部完成后释放下一DAG；已承诺计划不撤回。"""
from collections import defaultdict
from dataclasses import replace
import heapq
from .model import from_dict
from .network import NoPath
from .variants import VariantScheduler


def run_closed_loop(data, variant):
    scenario=from_dict(data,allow_multiple_exits=True)
    scheduler=VariantScheduler(scenario,variant)
    streams=defaultdict(list)
    for dag in scenario.dags:
        streams[dag.source].append(dag)
    for stream in streams.values(): stream.sort(key=lambda d:d.id)
    events=[(0.,source,0) for source in sorted(streams)]
    heapq.heapify(events)
    actual={}
    releases=[]
    while events:
        now=events[0][0]
        group=[];indices=[]
        while events and events[0][0]==now:
            _,source,index=heapq.heappop(events)
            dag=replace(streams[source][index],arrival=now)
            actual[dag.id]=dag;group.append(dag);indices.append((source,index))
            releases.append({'dag':dag.id,'source':source,'sequence':index,'arrival':now})
            try:
                scheduler.prepare_dag(dag)
            except (NoPath,ValueError) as exc:
                scheduler.infeasible[dag.id]=str(exc)
        # 每个到达事件完整规划，不等待实际执行结束，也不重排旧任务。
        scheduler.plan_batch(tuple(group))
        for dag,(source,index) in zip(group,indices):
            if dag.id in scheduler.infeasible: continue
            finish=max(scheduler.task_results[dag.id,t]['finish'] for t in dag.exit_ids)
            if index+1<len(streams[source]):
                heapq.heappush(events,(finish,source,index+1))
    # 上一DAG失败后，不假造它的完成时间或后续到达；全部标为阻断。
    for dag in scenario.dags:
        if dag.id not in actual:
            scheduler.infeasible[dag.id]='前序DAG不可行，闭环后续到达被阻断'
    realized=replace(scenario,dags=tuple(actual.get(d.id,d) for d in scenario.dags))
    scheduler.scenario=realized
    return realized,scheduler.result(),releases
