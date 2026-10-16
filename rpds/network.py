"""Algorithm 1：方向受限子图上的 STPF，不把最少跳数当作最短时延。"""
from dataclasses import dataclass
from math import inf, isclose


class NoPath(ValueError):
    pass


@dataclass(frozen=True)
class Path:
    nodes: tuple[int, ...]
    delay: float
    eat: dict[int, float]
    lat: dict[int, float]


def close(a, b):
    return isclose(a, b, rel_tol=1e-12, abs_tol=1e-12)


class Network:
    def __init__(self, scenario):
        self.rates = scenario.rates
        self.order = scenario.order
        self.epsilon = scenario.epsilon
        self.cache = {}

    def directed_nodes(self, source, target):
        """线性编队约定：只沿显式 formation_order 向目标推进。"""
        start, end = self.order.index(source), self.order.index(target)
        step = 1 if start < end else -1
        return self.order[start:end + 1] if step == 1 else tuple(reversed(self.order[end:start + 1]))

    def delay(self, nodes, size):
        # 空路径/本地路径没有跳数，严格返回零，包括 epsilon。
        return sum(size / self.rates[u, v] + self.epsilon for u, v in zip(nodes, nodes[1:]))

    def stpf(self, source, target, size):
        key = source, target, size
        if key not in self.cache:
            self.cache[key] = self._calculate(source, target, size)
        return self.cache[key]

    def _calculate(self, source, target, size):
        nodes = self.directed_nodes(source, target)
        pred = {v: [] for v in nodes}
        succ = {v: [] for v in nodes}
        rank = {v: i for i, v in enumerate(nodes)}
        for u, v in self.rates:
            if u in rank and v in rank and rank[u] < rank[v]:
                pred[v].append(u)
                succ[u].append(v)
        eat = self.forward(nodes, pred, source, size)
        if eat[target] == inf:
            raise NoPath(f'方向受限子图中 v{source} 到 v{target} 不可达')
        # lat=-inf 表示不能到达目标；与 eat=inf 合用，等价于先裁去非源-目标路径节点。
        lat = self.backward(nodes, succ, target, size, eat[target])
        valid = [v for v in nodes if eat[v] < inf and lat[v] > -inf]
        path = self.reconstruct(source, target, pred, eat, lat, size)
        return Path(path, self.delay(path, size), {v: eat[v] for v in valid}, {v: lat[v] for v in valid})

    def forward(self, nodes, pred, source, size):
        eat = dict.fromkeys(nodes, inf)
        eat[source] = 0.0
        for v in nodes:
            if v == source:
                continue
            eat[v] = min((eat[u] + size / self.rates[u, v] + self.epsilon for u in pred[v]), default=inf)
        return eat

    def backward(self, nodes, succ, target, size, end):
        lat = dict.fromkeys(nodes, -inf)
        lat[target] = end
        for u in reversed(nodes):
            if u == target:
                continue
            lat[u] = max((lat[v] - size / self.rates[u, v] - self.epsilon for v in succ[u]), default=-inf)
        return lat

    def reconstruct(self, source, target, pred, eat, lat, size):
        path = [target]
        while path[-1] != source:
            v = path[-1]
            candidates = [u for u in pred[v] if eat[u] < inf and lat[u] > -inf
                          and close(eat[u], lat[u])
                          and close(eat[u] + size / self.rates[u, v] + self.epsilon, eat[v])]
            if not candidates:
                raise ArithmeticError('STPF 回溯失败，请检查数值尺度')
            path.append(min(candidates))  # Algorithm 1：前驱编号最小者打破平局
        return tuple(reversed(path))
