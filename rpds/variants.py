"""新定义的单因素消融：不声称复现论文中丢失的 RR/greedy 实现。"""
from dataclasses import asdict, dataclass
from copy import deepcopy
import heapq
import math

from .network import Network, NoPath, Path
from .scheduler import Scheduler
from .priority_optimization import remaining_path_tails, predict_replica_finish


@dataclass(frozen=True)
class Policy:
    allocation: str = 'dynamic'
    priority: str = 'urgency'
    routing: str = 'stpf'


VARIANTS = {
    'rpds': Policy(),
    'static': Policy(allocation='static'),
    'fcfs': Policy(priority='fcfs'),
    'eft': Policy(priority='eft'),
    'dijkstra': Policy(routing='dijkstra'),
    'replica_eft': Policy(priority='replica_eft'),
    'path_slack': Policy(priority='path_slack'),
    # 路径消融：保持新的任务选择和动态可靠性分配，只把STPF换成全图Dijkstra。
    'path_slack_dijkstra': Policy(priority='path_slack', routing='dijkstra'),
    # 路径消融：保持方向受限子图，只把最短传输时延目标换成最少跳数。
    'path_slack_minhop': Policy(priority='path_slack', routing='minhop'),
}


class UnrestrictedNetwork(Network):
    """保留 stpf 调用接口与缓存键，内部改用全图 Dijkstra。

    同样以输入 size/rate+epsilon 为权，返回仍沿逆向路径。
    等长时按路径节点序列字典序选择；不会声称所有等长路径与 STPF 相同。
    """
    def _calculate(self, source, target, size):
        adjacency = {v: [] for v in self.order}
        for (u, v), rate in self.rates.items():
            adjacency[u].append((v, size / rate + self.epsilon))
        best = {source: (0.0, (source,))}
        queue = [(0.0, (source,), source)]
        settled = set()
        while queue:
            distance, nodes, u = heapq.heappop(queue)
            if u in settled or best.get(u) != (distance, nodes):
                continue
            settled.add(u)
            if u == target:
                return Path(nodes, distance, {}, {})
            for v, cost in sorted(adjacency[u]):
                if v in nodes or v in settled:
                    continue
                candidate = distance + cost, nodes + (v,)
                if candidate < best.get(v, (math.inf, ())):
                    best[v] = candidate
                    heapq.heappush(queue, (*candidate, v))
        raise NoPath(f'全图 v{source} 到 v{target} 不可达')


class MinimumHopNetwork(Network):
    """方向受限子图上的最少跳数路由；同跳数时选择传输时延最小路径。"""
    def _calculate(self, source, target, size):
        nodes = self.directed_nodes(source, target)
        rank = {vehicle: index for index, vehicle in enumerate(nodes)}
        best = {source: (0, 0.0, (source,))}
        for vehicle in nodes[1:]:
            candidates = []
            for predecessor in nodes[:rank[vehicle]]:
                if predecessor not in best or (predecessor, vehicle) not in self.rates:
                    continue
                hops, delay, path = best[predecessor]
                edge_delay = size / self.rates[predecessor, vehicle] + self.epsilon
                candidates.append((hops + 1, delay + edge_delay, path + (vehicle,)))
            if candidates:
                best[vehicle] = min(candidates)
        if target not in best:
            raise NoPath(f'方向受限子图中 v{source} 到 v{target} 不可达')
        _, delay, path = best[target]
        return Path(path, delay, {}, {})


class VariantScheduler(Scheduler):
    def __init__(self, scenario, variant='rpds'):
        if variant not in VARIANTS:
            raise ValueError(f'未知变体: {variant}')
        super().__init__(scenario)
        self.queue_tickets = {}
        self.path_tails = {}
        self.variant = variant
        self.policy = VARIANTS[variant]
        if self.policy.routing == 'dijkstra':
            self.network = UnrestrictedNetwork(scenario)
        elif self.policy.routing == 'minhop':
            self.network = MinimumHopNetwork(scenario)

    def prepare_dag(self, dag):
        super().prepare_dag(dag)
        if self.policy.priority == "path_slack":
            self.path_tails[dag.id] = remaining_path_tails(dag, self.placements)

    def ready_tasks(self, dags):
        ready = super().ready_tasks(dags)
        # 同一次更新中新进入的任务按稳定ID顺序入队；已有票号永不改变。
        for dag, task in sorted(ready, key=lambda pair: (pair[0].id, pair[1].id)):
            key = dag.id, task.id
            if key not in self.queue_tickets:
                self.queue_tickets[key] = len(self.queue_tickets)
        return ready

    def reliability_target(self, dag):
        if self.policy.allocation == 'static':
            return math.exp(math.log(dag.requirement) / len(dag.tasks))
        return super().reliability_target(dag)

    def urgency(self, dag, task):
        """复用列表调度接口；EFT 输出的是任务选择分数，不是 Eq22 紧迫度。"""
        if self.policy.priority in ('replica_eft', 'path_slack'):
            finish, _ = predict_replica_finish(self, dag, task)
            tail = self.path_tails[dag.id][task.id] if self.policy.priority == 'path_slack' else 0.0
            return finish - tail
        if self.policy.priority == 'fcfs':
            return self.queue_tickets[dag.id, task.id]
        if self.policy.priority == 'urgency':
            return super().urgency(dag, task)
        return min(self.candidate(dag, task, p).finish
                   for p in self.placements[dag.id, task.id])

    def result(self):
        result = deepcopy(super().result())
        # 显式改名，防止把 EFT 选择分数误读为论文紧迫度。
        for task in result['tasks']:
            task['priority_score'] = task.pop('urgency')
        for step in result['steps']:
            for row in step['ready']:
                row['priority_score'] = row.pop('urgency')
        result['variant'] = self.variant
        result['policy'] = asdict(self.policy)
        return result
