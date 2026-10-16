"""闭环到达、FCFS入队时刻、多出口以及原生成器适配的独立检查。"""
import ast
import copy
from pathlib import Path
import unittest
from rpds.ablation import execute
from rpds.ablation_suite import evaluate
from rpds.closed_loop import run_closed_loop
from rpds.metrics import calculate
from rpds.model import from_dict
from rpds.paper_workloads import workload,fft_edges,ge_edges
from rpds.variants import VariantScheduler
from test_rpds import small

class ClosedLoopTests(unittest.TestCase):
    def test_fcfs_new_successor_behind_waiting_task(self):
        data=small(1);data['vehicles'][0]['fault_rate']=0
        data['dags'][0]['tasks'].append(dict(id=2,input=1,output=1,workload=1,predecessors=[1]))
        later=copy.deepcopy(data['dags'][0]);later['id']='b';later['tasks']=later['tasks'][:1]
        data['dags'].append(later)
        result=VariantScheduler(from_dict(data),'fcfs').run()
        self.assertEqual([(s['selected_dag'],s['selected_task']) for s in result['steps']], [('a',1),('b',1),('a',2)])
        self.assertEqual([t['priority_score'] for t in result['tasks']],[0,1,2])

    def test_closed_loop_release_equals_previous_exit(self):
        for variant in ('rpds','fcfs','eft','static','dijkstra'):
            scenario,result,events=run_closed_loop(workload(0,4,'random',3),variant)
            tasks={(t['dag'],t['task']):t for t in result['tasks']}
            by_id={d.id:d for d in scenario.dags}
            previous={}
            for event in events:
                source=event['source']
                if source in previous:
                    dag=by_id[previous[source]]
                    finish=max(tasks[dag.id,i]['finish'] for i in dag.exit_ids)
                    self.assertEqual(event['arrival'],finish)
                else:self.assertEqual(event['arrival'],0)
                previous[source]=event['dag']
            self.assertEqual(len(events),12)

    def test_multiple_exits_no_virtual_task(self):
        data=small(1);data['vehicles'][0]['fault_rate']=0
        data['dags'][0]['tasks'].append(dict(id=2,input=1,output=1,workload=2,predecessors=[]))
        scenario,result,_=run_closed_loop(data,'rpds')
        self.assertEqual(len(result['tasks']),2)
        self.assertEqual(calculate(scenario,result)['mean_response_all_dags'],3.)
        with self.assertRaises(ValueError):from_dict(data) # 默认入口仍要求唯一出口。

    def test_initial_batch_matches_previous_runner(self):
        data=workload(0,4,'random',1)
        old=execute(data,'rpds');new=evaluate(data,'rpds')
        self.assertEqual(old['copies'],new['copies'])
        self.assertEqual(old['metrics'],new['metrics'])

    def test_infeasible_stream_is_not_silently_dropped(self):
        data=small(1);data['vehicles'][0]['fault_rate']=100
        first=data['dags'][0];first['id']='G01_00'
        second=copy.deepcopy(first);second['id']='G01_01'
        data['dags'].append(second)
        result=evaluate(data,'rpds')
        self.assertEqual(result['status'],'infeasible')
        self.assertEqual(result['metrics']['infeasible_dags'],2)
        self.assertIsNone(result['metrics']['mean_response_all_dags'])
        self.assertEqual(len(result['arrivals']),1)

    def test_future_workload_does_not_change_first_batch(self):
        data=workload(3,4,'random',2)
        altered=copy.deepcopy(data)
        for dag in altered['dags']:
            if dag['id'].endswith('_01'):
                for task in dag['tasks']:task['workload']*=1.1
        for variant in ('rpds','fcfs','eft'):
            _,a,_=run_closed_loop(data,variant)
            _,b,_=run_closed_loop(altered,variant)
            first=lambda r:[c for c in r['copies'] if c['dag'].endswith('_00')]
            self.assertEqual(first(a),first(b))

    def test_fft_edges_match_original_functions(self):
        # 只提取并执行原脚本已审阅的两个纯拓扑函数，不执行文件写入或主程序。
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'original/GEDAG.py').read_text())
        module=ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('cross','getDAG')],type_ignores=[])
        class Random:
            @staticmethod
            def randint(a,b):return 1
        class NP:random=Random
        context={'np':NP,'rho':8}
        exec(compile(module,'original/GEDAG.py','exec'),context)
        matrix=context['getDAG'](8,39)
        original={(i,j) for i,row in enumerate(matrix) for j,value in enumerate(row) if value}
        n,edges=fft_edges();self.assertEqual(n,39);self.assertEqual(edges,original)

    def test_ge_stage_boundaries_and_workload_validity(self):
        n,edges=ge_edges();self.assertEqual(n,20)
        self.assertEqual({j for i,j in edges if i==6},set(range(7,11)))
        self.assertEqual({j for i,j in edges if i==11},set(range(12,15)))
        self.assertEqual({j for i,j in edges if i==18},{19})
        for family in ('random','ge','fft'):
            a=workload(2,4,family,2);self.assertEqual(a,workload(2,4,family,2))
            s=from_dict(a,allow_multiple_exits=True)
            self.assertEqual(len(s.dags[0].exit_ids),8 if family=='fft' else 1)
            for variant in ('rpds','fcfs','eft','static','dijkstra'):
                result=evaluate(a,variant)
                self.assertEqual(result['status'],'complete',result['validation_issues'])

    def test_workload_accepts_reliability_and_random_task_count(self):
        data=workload(5,4,'random',2,requirement=.998,random_tasks=12)
        self.assertTrue(all(dag['requirement']==.998 for dag in data['dags']))
        self.assertTrue(all(len(dag['tasks'])==12 for dag in data['dags']))

if __name__=='__main__':unittest.main()
