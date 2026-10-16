"""固定种子的合成输入：同时到达的一批 DAG，不冒充旧闭环实验数据。"""
import random


def make_tasks(rng, count):
    """有序随机 DAG：随机前驱加末尾汇聚，确保唯一出口且无环。"""
    tasks = []
    for tid in range(1, count + 1):
        pred = [p for p in range(1, tid) if rng.random() < 0.15]
        if tid == count:
            used = {p for t in tasks for p in t['predecessors']}
            pred = sorted({t['id'] for t in tasks} - used)
        tasks.append({'id': tid, 'input': rng.uniform(.1, .5),
                      'output': rng.uniform(.1, .5),
                      'workload': rng.uniform(.2, .5), 'predecessors': pred})
    return tasks


def generate(seed, vehicles, tasks=30, requirement=.995):
    """单车道随机间距50--120m、通信半径200m，邻车保证连通。

    每个源车辆一个同时到达的 DAG，逐 DAG 平均与逐源平均相等。
    不复用论文旧生成器，不宣称复现其分布或21,000个DAG。
    """
    if vehicles < 2 or tasks < 1 or not 0 < requirement < 1:
        raise ValueError('vehicles >= 2、tasks >= 1、requirement 在 (0,1)')
    rng = random.Random(seed)
    positions = [0.0]
    for _ in range(vehicles - 1):
        positions.append(positions[-1] + rng.uniform(50, 120))
    links = [{'u': i + 1, 'v': j + 1, 'distance': positions[j] - positions[i]}
             for i in range(vehicles) for j in range(i + 1, vehicles)
             if positions[j] - positions[i] <= 200]
    return {
        'metadata': {'generator': 'ablation_batch_v1', 'seed': seed,
                     'arrival_mode': 'simultaneous_batch',
                     'historical_reproduction': False, 'positions_m': positions},
        'vehicles': [{'id': i, 'frequency': rng.uniform(1, 5),
                      'fault_rate': rng.uniform(.02, .03)} for i in range(1, vehicles + 1)],
        'formation_order': list(range(1, vehicles + 1)), 'links': links,
        'radio': {'bandwidth': 10, 'power': .1, 'theta': 17.8, 'noise': 1e-13},
        'epsilon': .001, 'communication_fault_rate': rng.uniform(.02, .03),
        'dags': [{'id': f'G{i}', 'source': i, 'arrival': 0.,
                  'requirement': requirement, 'tasks': make_tasks(rng, tasks)}
                 for i in range(1, vehicles + 1)],
    }
