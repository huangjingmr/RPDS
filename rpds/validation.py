"""独立复算产物约束，避免仅用调度器自身的输出断言自身正确。"""
from collections import defaultdict
import math


def validate_schedule(scenario, result, tolerance=1e-10, require_complete=True, allocation="dynamic",
                      allow_same_vehicle_replicas=False):
    if allocation not in ("dynamic", "static"):
        raise ValueError("未知可靠性分配规则")
    issues = []
    dag_map = {d.id: d for d in scenario.dags}
    task_map = {(d.id, t.id): t for d in scenario.dags for t in d.tasks}
    grouped = defaultdict(list)
    free = dict.fromkeys(scenario.vehicles, 0.0)
    for c in result['copies']:
        grouped[c['dag'], c['task']].append(c)
    for c in result['copies']:
        key = c['dag'], c['task']
        dag, task = dag_map[c['dag']], task_map[key]
        vehicle = scenario.vehicles[c['vehicle']]
        label = f'{dag.id}/τ{task.id}/v{vehicle.id}'
        pred_ft = [max(x['finish'] for x in grouped[dag.id, p]) for p in task.predecessors if grouped[dag.id, p]]
        if len(pred_ft) != len(task.predecessors):
            issues.append(f'{label}: 缺少前驱副本')
        release = max([dag.arrival] + pred_ft)
        path = c['path']
        if not path or path[0] != dag.source or path[-1] != vehicle.id:
            issues.append(f'{label}: 路径端点错误')
            continue
        if any((u, v) not in scenario.rates for u, v in zip(path, path[1:])):
            issues.append(f'{label}: 使用不存在的网络边')
            continue
        send = sum(task.input / scenario.rates[u, v] + scenario.epsilon for u, v in zip(path, path[1:]))
        back = sum(task.output / scenario.rates[u, v] + scenario.epsilon for u, v in zip(path, path[1:]))
        execution = task.workload / vehicle.frequency
        if c['start'] + tolerance < release + send:
            issues.append(f'{label}: DAG/到达约束违规，st={c["start"]:.7f} < {release + send:.7f}')
        if c['start'] + tolerance < free[vehicle.id]:
            issues.append(f'{label}: 车辆分配顺序 FIFO 违规')
        expected_finish = c['start'] + execution + back
        if abs(c['finish'] - expected_finish) > tolerance:
            issues.append(f'{label}: Eq7 违规，ft={c["finish"]:.7f}，应为 {expected_finish:.7f}')
        if abs(c['cpu_finish'] - c['start'] - execution) > tolerance:
            issues.append(f'{label}: CPU 完成时间错误')
        expected_r = math.exp(-vehicle.fault_rate * execution - scenario.communication_fault_rate * (send + back))
        if abs(c['reliability'] - expected_r) > tolerance:
            issues.append(f'{label}: Eq11 可靠性错误')
        if list(c['return_path']) != list(reversed(path)):
            issues.append(f'{label}: 返回未沿原路径逆行')
        free[vehicle.id] = c['start'] + execution
    stored = {(t['dag'], t['task']): t for t in result.get('tasks', [])}
    for key, rows in grouped.items():
        if not allow_same_vehicle_replicas and len({c['vehicle'] for c in rows}) != len(rows):
            issues.append(f'{key}: 同任务副本车辆重复')
        if key in stored and abs(stored[key]['finish'] - max(c['finish'] for c in rows)) > tolerance:
            issues.append(f'{key}: Eq8 未使用最晚返回')
    if require_complete:
        check_dags(scenario, result, grouped, stored, issues, tolerance, allocation)
    return issues


def check_dags(scenario, result, grouped, stored, issues, tolerance, allocation):
    for dag in scenario.dags:
        if dag.id in result['infeasible']:
            continue
        if any(not grouped[dag.id, t.id] for t in dag.tasks):
            issues.append(f'{dag.id}: 未完整调度却未报告不可行')
            continue
        product = 1.0
        scheduled = [t for t in result['tasks'] if t['dag'] == dag.id]
        rho = dag.requirement ** (1 / len(dag.tasks))
        for k, task in enumerate(scheduled, 1):
            target = rho if allocation == "static" else dag.requirement / (product * rho ** (len(dag.tasks) - k))
            rows = grouped[dag.id, task['task']]
            achieved = 1 - math.prod(1 - c['reliability'] for c in rows)
            if abs(task['target'] - target) > tolerance or achieved + tolerance < target:
                issues.append(f'{dag.id}/τ{task["task"]}: {allocation} 可靠性目标不满足')
            if abs(task['reliability'] - achieved) > tolerance:
                issues.append(f'{dag.id}: Eq12 记录错误')
            product *= achieved
        if product + tolerance < dag.requirement:
            issues.append(f'{dag.id}: DAG 可靠性不足')
