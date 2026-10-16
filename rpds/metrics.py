"""实验指标：每 DAG 出口响应时间，与 Eq14 每源车辆等权平均分开报告。"""
from collections import defaultdict
import math


def mean(values):
    values = list(values)
    return sum(values) / len(values) if values else None


def calculate(scenario, result):
    tasks = {(t['dag'], t['task']): t for t in result['tasks']}
    per_dag = []
    per_source = defaultdict(list)
    for dag in scenario.dags:
        if dag.id in result['infeasible']:
            per_dag.append({'dag': dag.id, 'status': 'infeasible', 'response': None, 'reliability': None})
            continue
        reliability = math.prod(tasks[dag.id, t.id]['reliability'] for t in dag.tasks)
        response = max(tasks[dag.id, tid]['finish'] for tid in dag.exit_ids) - dag.arrival
        per_dag.append({'dag': dag.id, 'source': dag.source, 'status': 'scheduled', 'response': response,
                        'reliability': reliability, 'requirement': dag.requirement})
        per_source[dag.source].append(response)
    copies = result['copies']
    start = min(d.arrival for d in scenario.dags)
    end = max((c['finish'] for c in copies), default=start)
    horizon = end - start
    busy = {v: sum(c['execution'] for c in copies if c['vehicle'] == v) for v in scenario.vehicles}
    task_inputs = {(d.id, t.id): t for d in scenario.dags for t in d.tasks}
    hop_mbits = sum((len(c['path']) - 1) * (task_inputs[c['dag'], c['task']].input
                    + task_inputs[c['dag'], c['task']].output) for c in copies)
    successful_responses = [d['response'] for d in per_dag if d['status'] == 'scheduled']
    complete = not result['infeasible']
    active_mean = mean(mean(v) for v in per_source.values())
    return {
        'dags': per_dag, 'total_dags': len(scenario.dags), 'infeasible_dags': len(result['infeasible']),
        'feasible_fraction': len(successful_responses) / len(scenario.dags),
        'mean_response_all_dags': mean(successful_responses) if complete else None,
        'mean_response_feasible_dags_only': mean(successful_responses),
        # Eq14 的 M_i=0 项无定义；所有车辆均有 DAG 时才输出字面上的 Eq14。
        'eq14_all_vehicles': active_mean if complete and len(per_source) == len(scenario.vehicles) else None,
        'mean_response_active_sources': active_mean if complete else None,
        'active_source_count': len(per_source),
        'copies': len(copies), 'backups': len(copies) - len({(c['dag'], c['task']) for c in copies}),
        'cpu_busy_seconds': busy, 'observation_start': start, 'observation_end': end,
        'fleet_cpu_utilization': sum(busy.values()) / (len(busy) * horizon) if horizon else 0.0,
        'vehicle_utilization': {v: b / horizon if horizon else 0.0 for v, b in busy.items()},
        'communication_mbit_hops': hop_mbits,
        'reliability_kind': 'independent_fault_model_prediction_not_measured_success_rate',
    }
