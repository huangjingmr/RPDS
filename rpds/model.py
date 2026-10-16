"""输入模型与校验。单位统一为秒、Mbit、Gcycle、GHz、Mbps。"""
from dataclasses import dataclass
from graphlib import TopologicalSorter, CycleError
import math


def number(value, name, *, positive=False):
    """尽早拒绝 NaN、无穷大和不合法负数，避免产生假可行调度。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError(f'{name} 必须为数值')
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f'{name} 必须为有限的非负数（positive={positive}）')
    return float(value)


@dataclass(frozen=True)
class Vehicle:
    id: int
    frequency: float
    fault_rate: float


@dataclass(frozen=True)
class Task:
    id: int
    input: float
    workload: float
    output: float
    predecessors: tuple[int, ...] = ()


@dataclass(frozen=True)
class DAG:
    id: str
    source: int
    arrival: float
    requirement: float
    tasks: tuple[Task, ...]

    @property
    def exit_ids(self):
        """真实出口集合；FFT保留多个出口，不添加参与可靠性分配的虚拟任务。"""
        used = {p for t in self.tasks for p in t.predecessors}
        return tuple(t.id for t in self.tasks if t.id not in used)

    @property
    def exit_id(self):
        if len(self.exit_ids) != 1:
            raise ValueError("多出口DAG请使用 exit_ids")
        return self.exit_ids[0]


@dataclass
class Scenario:
    vehicles: dict[int, Vehicle]
    rates: dict[tuple[int, int], float]
    order: tuple[int, ...]
    epsilon: float
    communication_fault_rate: float
    dags: tuple[DAG, ...]


def validate_dag(dag, vehicles, allow_multiple_exits=False):
    if dag.source not in vehicles:
        raise ValueError(f'{dag.id}: 源车辆不存在')
    number(dag.arrival, 'arrival')
    if not 0 < dag.requirement < 1:
        raise ValueError('DAG requirement 必须在 (0,1)，不支持恰为 1 的目标')
    ids = {t.id for t in dag.tasks}
    if not ids or len(ids) != len(dag.tasks):
        raise ValueError('DAG 不能为空，任务 ID 不能重复')
    graph = {}
    for task in dag.tasks:
        number(task.input, 'input'); number(task.output, 'output')
        number(task.workload, 'workload')
        if not set(task.predecessors) <= ids or len(set(task.predecessors)) != len(task.predecessors):
            raise ValueError('前驱不存在或重复')
        graph[task.id] = task.predecessors
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError as exc:
        raise ValueError('输入含有环，不是 DAG') from exc
    used = {p for t in dag.tasks for p in t.predecessors}
    if not allow_multiple_exits and len(ids - used) != 1:
        raise ValueError('要求唯一出口；多出口请显式添加零工作量汇聚任务')


def from_dict(data, allow_multiple_exits=False):
    """读取显式网络，不从车辆编号猜测距离。formation_order 由用户提供。"""
    vehicles = {}
    for item in data['vehicles']:
        vid = item['id']
        if not isinstance(vid, int) or isinstance(vid, bool) or vid in vehicles:
            raise ValueError('车辆 ID 必须为不重复整数')
        vehicles[vid] = Vehicle(vid, number(item['frequency'], 'frequency', positive=True),
                                number(item['fault_rate'], 'fault_rate'))
    order = tuple(data['formation_order'])
    if not vehicles or len(order) != len(vehicles) or set(order) != set(vehicles):
        raise ValueError('formation_order 必须恰好列出每一辆车')
    radio = data.get('radio', {})
    rates = {}
    for link in data['links']:
        u, v = link['u'], link['v']
        if u == v or u not in vehicles or v not in vehicles or (u, v) in rates:
            raise ValueError('非法或重复的无向边')
        rate = link.get('rate')
        if rate is None:
            distance = number(link['distance'], 'distance', positive=True)
            b = number(radio['bandwidth'], 'bandwidth', positive=True)
            p = number(radio['power'], 'power', positive=True)
            theta = number(radio['theta'], 'theta', positive=True)
            noise = number(radio['noise'], 'noise', positive=True)
            rate = b * math.log2(1 + p * theta / distance**2 / noise)  # Eq. (1)
        rates[u, v] = rates[v, u] = number(rate, 'rate', positive=True)
    dags = []
    for item in data['dags']:
        tasks = tuple(Task(t['id'], t['input'], t['workload'], t['output'],
                           tuple(t.get('predecessors', []))) for t in item['tasks'])
        if any(not isinstance(t.id, int) or isinstance(t.id, bool) for t in tasks):
            raise ValueError('任务 ID 必须为整数')
        dag = DAG(str(item['id']), item['source'], item['arrival'], item['requirement'], tasks)
        validate_dag(dag, vehicles, allow_multiple_exits)
        dags.append(dag)
    if not dags or len({d.id for d in dags}) != len(dags):
        raise ValueError('至少需要一个 DAG，且 DAG ID 不能重复')
    return Scenario(vehicles, rates, order, number(data['epsilon'], 'epsilon'),
                    number(data['communication_fault_rate'], 'communication_fault_rate'), tuple(dags))
