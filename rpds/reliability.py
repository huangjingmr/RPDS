"""Eq. (11)-(17)，故障只暴露于通信和计算，不包含排队等待。"""
import math


def combined(values):
    values = tuple(values)
    if not values:
        return 0.0
    if 1.0 in values:
        return 1.0
    return -math.expm1(sum(math.log1p(-r) for r in values))


def replica_cap(worst, rho):
    """Theorem 2。零故障率/数值下溢的边界单独处理。"""
    if worst >= rho:
        return 1
    if worst <= 0 or rho >= 1:
        return math.inf
    cap = math.ceil(math.log1p(-rho) / math.log1p(-worst))
    # 不用宽松容差把不达标的可靠性误判为通过。
    while -math.expm1(cap * math.log1p(-worst)) < rho:
        cap += 1
    return cap


def required_target(requirement, count, scheduled, log_product):
    """k=scheduled+1；使用同一 DAG 的已实现可靠性和 rho^(N-k)。"""
    remaining = count - scheduled - 1
    log_rho = math.log(requirement) / count
    target = math.exp(math.log(requirement) - log_product - remaining * log_rho)
    if not 0 < target < 1:
        raise ArithmeticError(f'Eq17 目标不在 (0,1): {target}，不能截断掩盖不可行性')
    return target
