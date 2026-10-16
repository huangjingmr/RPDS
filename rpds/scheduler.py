"""RPDS 列表调度：全局就绪队列、车辆分配顺序 FIFO、不同车辆独立副本。"""
from dataclasses import dataclass, asdict
from itertools import groupby
import math
from .network import Network, NoPath
from .reliability import combined, replica_cap, required_target


@dataclass(frozen=True)
class Placement:
    dag: str
    task: int
    vehicle: int
    path: tuple[int, ...]
    send: float
    execution: float
    return_delay: float
    reliability: float


@dataclass(frozen=True)
class Copy:
    dag: str
    task: int
    vehicle: int
    path: tuple[int, ...]
    return_path: tuple[int, ...]
    start: float
    cpu_finish: float
    finish: float
    send: float
    execution: float
    return_delay: float
    reliability: float


class Scheduler:
    def __init__(self, scenario):
        self.scenario = scenario
        self.network = Network(scenario)
        self.free = dict.fromkeys(scenario.vehicles, 0.0)
        self.placements = {}
        self.caps = {}
        self.infeasible = {}
        self.task_results = {}
        self.copies = []
        self.steps = []
        self.log_products = dict.fromkeys((d.id for d in scenario.dags), 0.0)
        self.counts = dict.fromkeys((d.id for d in scenario.dags), 0)
        self.ran = False

    def placement(self, dag, task, vehicle):
        """按每个任务输入大小跑 STPF；返回沿同一条路径反向走（论文第8页）。"""
        path = self.network.stpf(dag.source, vehicle.id, task.input).nodes
        send = self.network.delay(path, task.input)
        back = self.network.delay(tuple(reversed(path)), task.output)
        execution = task.workload / vehicle.frequency
        exposure = self.scenario.communication_fault_rate * (send + back) + vehicle.fault_rate * execution
        return Placement(dag.id, task.id, vehicle.id, path, send, execution, back, math.exp(-exposure))

    def prepare_dag(self, dag):
        """提前检查整个 DAG 的保守副本上限，不把不可行 DAG 的部分结果当成功。"""
        rho = math.exp(math.log(dag.requirement) / len(dag.tasks))
        for task in dag.tasks:
            choices = [self.placement(dag, task, v) for v in self.scenario.vehicles.values()]
            self.placements[dag.id, task.id] = choices
            cap = replica_cap(min(p.reliability for p in choices), rho)
            if cap > len(self.free):
                raise ValueError(f'任务 {task.id}: 保守副本上限 {cap} 超过车辆数 {len(self.free)}')
            self.caps[dag.id, task.id] = cap

    def predecessor_finish(self, dag, task):
        # 入口在 DAG 到达时可发送；非入口必须等待所有前驱的所有副本返回。
        return max([dag.arrival] + [self.task_results[dag.id, p]['finish'] for p in task.predecessors])

    def candidate(self, dag, task, place):
        start = max(self.free[place.vehicle], self.predecessor_finish(dag, task) + place.send)
        end = start + place.execution
        return Copy(dag.id, task.id, place.vehicle, place.path, tuple(reversed(place.path)),
                    start, end, end + place.return_delay, place.send, place.execution,
                    place.return_delay, place.reliability)

    def urgency(self, dag, task):
        choices = [self.candidate(dag, task, p) for p in self.placements[dag.id, task.id]]
        local = next(c for c in choices if c.vehicle == dag.source)
        return local.cpu_finish - sum(c.start for c in choices) / len(choices)  # Eq21-22

    def ready_tasks(self, dags):
        tasks = [(dag, task) for dag in dags for task in dag.tasks]
        return [(d, t) for d, t in tasks if d.id not in self.infeasible
                and (d.id, t.id) not in self.task_results
                and all((d.id, p) in self.task_results for p in t.predecessors)]

    def reliability_target(self, dag):
        """默认 Eq17；消融通过覆写这一函数替换分配规则。"""
        return required_target(dag.requirement, len(dag.tasks),
                               self.counts[dag.id], self.log_products[dag.id])

    def assign(self, dag, task, urgency):
        key = dag.id, task.id
        target = self.reliability_target(dag)
        placed = []
        unused = list(self.placements[key])
        achieved = 0.0
        while len(placed) < self.caps[key] and achieved < target:
            choices = [self.candidate(dag, task, p) for p in unused]
            copy = min(choices, key=lambda c: (c.finish, c.vehicle))  # Eq23，平局选最小编号
            placed.append(copy)
            self.copies.append(copy)
            self.free[copy.vehicle] = copy.cpu_finish  # Eq6：CPU 不等待返回链路
            unused = [p for p in unused if p.vehicle != copy.vehicle]
            achieved = combined(c.reliability for c in placed)
        if achieved < target:
            self.infeasible[dag.id] = f'任务 {task.id} 达到副本上限仍未满足 Eq17'
            return
        self.task_results[key] = {'dag': dag.id, 'task': task.id, 'finish': max(c.finish for c in placed),
                                  'reliability': achieved, 'target': target, 'cap': self.caps[key],
                                  'copies': len(placed), 'urgency': urgency}
        self.log_products[dag.id] += math.log(achieved)
        self.counts[dag.id] += 1

    def plan_batch(self, dags):
        ready = self.ready_tasks(dags)
        while ready:
            scored = [(self.urgency(d, t), d, t) for d, t in ready]
            urgency, dag, task = min(scored, key=lambda x: (x[0], x[1].id, x[2].id))
            self.steps.append({'step': len(self.steps) + 1, 'selected_dag': dag.id, 'selected_task': task.id,
                               'ready': [{'dag': d.id, 'task': t.id, 'urgency': u} for u, d, t in scored]})
            self.assign(dag, task, urgency)
            ready = self.ready_tasks(dags)

    def run(self):
        if self.ran:
            raise RuntimeError('每次实验请创建新的 Scheduler，防止复用上一轮状态')
        self.ran = True
        for dag in self.scenario.dags:
            try:
                self.prepare_dag(dag)
            except (NoPath, ValueError) as exc:
                self.infeasible[dag.id] = str(exc)
        # 同时到达的所有 DAG 竞争。新到达只追加，不改早先已经承诺的完整计划。
        ordered = sorted(self.scenario.dags, key=lambda d: (d.arrival, d.id))
        for _, group in groupby(ordered, key=lambda d: d.arrival):
            self.plan_batch(tuple(group))
        return self.result()

    def result(self):
        return {'copies': [asdict(c) for c in self.copies], 'tasks': list(self.task_results.values()),
                'steps': self.steps, 'infeasible': dict(self.infeasible), 'vehicle_free': dict(self.free)}
