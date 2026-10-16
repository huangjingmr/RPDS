"""新的候选排序，非原论文Eq21/22；无截止时间或最优性保证。"""
from graphlib import TopologicalSorter
import math
from .reliability import combined


def remaining_path_tails(dag, placements):
    """拓扑逆序计算后继最长剩余路径；每任务使用车辆平均固有周转时长。

    不含车辆排队；尾长只是路径启发式估计，不是完成时间界或实际截止期。
    返回tail不包含任务自身。使用显式拓扑序，不依赖任务编号。
    """
    graph={t.id:t.predecessors for t in dag.tasks}
    order=list(TopologicalSorter(graph).static_order())
    successors={t.id:[] for t in dag.tasks}
    for task in dag.tasks:
        for pred in task.predecessors:successors[pred].append(task.id)
    cost={t.id:sum(p.send+p.execution+p.return_delay for p in placements[dag.id,t.id])/
          len(placements[dag.id,t.id]) for t in dag.tasks}
    tail={}
    for tid in reversed(order):
        tail[tid]=max((cost[s]+tail[s] for s in successors[tid]),default=0.)
    return tail


def predict_replica_finish(scheduler,dag,task):
    """在当前状态只读预测真实分配：不同车辆按EFT依次选到目标满足。

    候选车辆互不重复，因此给已选车辆追加副本不会改变其他候选的开始时间。
    这使预先排序与assign中的逐次最小选择等价，不需要修改/回滚队列。
    """
    target=scheduler.reliability_target(dag)
    cap=scheduler.caps[dag.id,task.id]
    choices=sorted((scheduler.candidate(dag,task,p) for p in scheduler.placements[dag.id,task.id]),
                   key=lambda c:(c.finish,c.vehicle))
    probabilities=[]
    for copy in choices[:cap]:
        probabilities.append(copy.reliability)
        if combined(probabilities)>=target:
            return copy.finish,len(probabilities)
    raise ValueError(f'{dag.id}/{task.id}: 预测中未能满足可靠性要求')
