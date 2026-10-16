"""按原论文定义重建Local/HEFT与MaxRe/RR组合基线。"""
from collections import defaultdict
from dataclasses import asdict, replace
from graphlib import TopologicalSorter
import heapq
import math

from .model import from_dict
from .network import Network, NoPath
from .reliability import combined, replica_cap, required_target
from .scheduler import Copy, Scheduler


BASELINES = {
    "local_maxre": ("local", "static"),
    "local_rr": ("local", "dynamic"),
    "heft_maxre": ("heft", "static"),
    "heft_rr": ("heft", "dynamic"),
}


class BaselineScheduler(Scheduler):
    """共享当前模型约束，只替换任务顺序、车辆分配和可靠性目标。"""
    def __init__(self, scenario, method):
        if method not in BASELINES:
            raise ValueError(f"未知基线: {method}")
        super().__init__(scenario)
        self.method = method
        self.execution, self.allocation = BASELINES[method]
        self.ranks = {}

    def prepare_dag(self, dag):
        if self.execution == "heft":
            super().prepare_dag(dag)
            self.ranks[dag.id] = self.upward_ranks(dag)
            return
        rho = dag.requirement ** (1 / len(dag.tasks))
        source = self.scenario.vehicles[dag.source]
        for task in dag.tasks:
            placement = self.placement(dag, task, source)
            self.placements[dag.id, task.id] = [placement]
            self.caps[dag.id, task.id] = replica_cap(placement.reliability, rho)

    def upward_ranks(self, dag):
        """HEFT upward rank：候选车辆平均周转代价加最大后继rank。"""
        successors = {task.id: [] for task in dag.tasks}
        for task in dag.tasks:
            for predecessor in task.predecessors:
                successors[predecessor].append(task.id)
        graph = {task.id: task.predecessors for task in dag.tasks}
        order = list(TopologicalSorter(graph).static_order())
        average = {
            task.id: sum(place.send + place.execution + place.return_delay
                         for place in self.placements[dag.id, task.id])
                     / len(self.placements[dag.id, task.id])
            for task in dag.tasks
        }
        ranks = {}
        for task_id in reversed(order):
            ranks[task_id] = average[task_id] + max(
                (ranks[child] for child in successors[task_id]), default=0.0)
        return ranks

    def reliability_target(self, dag):
        if self.allocation == "static":
            return dag.requirement ** (1 / len(dag.tasks))
        return required_target(dag.requirement, len(dag.tasks),
                               self.counts[dag.id], self.log_products[dag.id])

    def task_order(self, dag):
        if self.execution == "heft":
            return sorted(dag.tasks, key=lambda task: (-self.ranks[dag.id][task.id], task.id))
        graph = {task.id: task.predecessors for task in dag.tasks}
        by_id = {task.id: task for task in dag.tasks}
        return [by_id[task_id] for task_id in TopologicalSorter(graph).static_order()]

    def assign_heft(self, dag, task, score):
        key = dag.id, task.id
        target = self.reliability_target(dag)
        unused = list(self.placements[key])
        placed = []
        achieved = 0.0
        while len(placed) < self.caps[key] and achieved < target:
            choices = [self.candidate(dag, task, place) for place in unused]
            copy = min(choices, key=lambda item: (item.finish, item.vehicle))
            placed.append(copy)
            self.copies.append(copy)
            self.free[copy.vehicle] = copy.cpu_finish
            unused = [place for place in unused if place.vehicle != copy.vehicle]
            achieved = combined(item.reliability for item in placed)
        self.store_task(dag, task, placed, achieved, target, score)

    def assign_local(self, dag, task, score):
        key = dag.id, task.id
        target = self.reliability_target(dag)
        placement = self.placements[key][0]
        placed = []
        achieved = 0.0
        while len(placed) < self.caps[key] and achieved < target:
            copy = self.candidate(dag, task, placement)
            placed.append(copy)
            self.copies.append(copy)
            # 同一源车辆上的重复执行按FIFO串行；瞬态故障按重复执行独立建模。
            self.free[copy.vehicle] = copy.cpu_finish
            achieved = combined(item.reliability for item in placed)
        self.store_task(dag, task, placed, achieved, target, score)

    def store_task(self, dag, task, placed, achieved, target, score):
        key = dag.id, task.id
        if achieved < target:
            self.infeasible[dag.id] = f"任务 {task.id} 未满足可靠性要求"
            return
        self.task_results[key] = {
            "dag": dag.id, "task": task.id, "finish": max(item.finish for item in placed),
            "reliability": achieved, "target": target, "cap": self.caps[key],
            "copies": len(placed), "priority_score": score,
        }
        self.log_products[dag.id] += math.log(achieved)
        self.counts[dag.id] += 1

    def plan_dag(self, dag):
        for task in self.task_order(dag):
            if not all((dag.id, predecessor) in self.task_results for predecessor in task.predecessors):
                raise ArithmeticError(f"{dag.id}/任务{task.id}: HEFT顺序违反拓扑约束")
            score = self.ranks[dag.id][task.id] if self.execution == "heft" else len(self.task_results)
            if self.execution == "heft":
                self.assign_heft(dag, task, score)
            else:
                self.assign_local(dag, task, score)
            if dag.id in self.infeasible:
                break

    def result(self):
        return {"copies": [asdict(copy) for copy in self.copies],
                "tasks": list(self.task_results.values()), "steps": [],
                "infeasible": dict(self.infeasible), "vehicle_free": dict(self.free),
                "baseline": self.method, "execution": self.execution,
                "allocation": self.allocation}


def run_baseline_closed_loop(data, method):
    """每源上一DAG完成后释放下一DAG；HEFT按DAG规划，不跨DAG重排。"""
    scenario = from_dict(data, allow_multiple_exits=True)
    scheduler = BaselineScheduler(scenario, method)
    streams = defaultdict(list)
    for dag in scenario.dags:
        streams[dag.source].append(dag)
    for stream in streams.values():
        stream.sort(key=lambda dag: dag.id)
    events = [(0.0, source, 0) for source in sorted(streams)]
    heapq.heapify(events)
    actual = {}
    releases = []
    while events:
        now = events[0][0]
        group = []
        while events and events[0][0] == now:
            _, source, index = heapq.heappop(events)
            group.append((source, index, replace(streams[source][index], arrival=now)))
        # 同时到达时按源车辆编号稳定处理；Local运行在不同源车上，不受此顺序影响。
        for source, index, dag in group:
            actual[dag.id] = dag
            releases.append({"dag": dag.id, "source": source, "sequence": index, "arrival": now})
            try:
                scheduler.prepare_dag(dag)
                scheduler.plan_dag(dag)
            except (NoPath, ValueError) as exc:
                scheduler.infeasible[dag.id] = str(exc)
            if dag.id in scheduler.infeasible:
                continue
            finish = max(scheduler.task_results[dag.id, task_id]["finish"] for task_id in dag.exit_ids)
            if index + 1 < len(streams[source]):
                heapq.heappush(events, (finish, source, index + 1))
    for dag in scenario.dags:
        if dag.id not in actual:
            scheduler.infeasible[dag.id] = "前序DAG不可行，闭环后续到达被阻断"
    realized = replace(scenario, dags=tuple(actual.get(dag.id, dag) for dag in scenario.dags))
    scheduler.scenario = realized
    return realized, scheduler.result(), releases
