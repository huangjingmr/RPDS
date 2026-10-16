"""最小回归集 + 小规模穷举路径参照。全部用标准库 unittest。"""
import copy
import itertools
import json
import math
from pathlib import Path
import random
import unittest
from rpds import Scheduler, from_dict
from rpds.network import Network, NoPath
from rpds.reliability import replica_cap, required_target, combined
from rpds.metrics import calculate
from rpds.validation import validate_schedule
from rpds.audit import read_inputs, replay

ROOT = Path(__file__).resolve().parents[1]


def small(n=3):
    return {'vehicles': [{'id':i, 'frequency':1., 'fault_rate':.1} for i in range(1, n+1)],
            'formation_order': list(range(1,n+1)),
            'links': [{'u':i,'v':i+1,'rate':10.} for i in range(1,n)],
            'epsilon':.01,'communication_fault_rate':.1,
            'dags':[{'id':'a','source':1,'arrival':0.,'requirement':.9,
                     'tasks':[{'id':1,'input':1.,'workload':1.,'output':1.,'predecessors':[]}]}]}


class NetworkTests(unittest.TestCase):
    def test_local_zero_and_each_hop_epsilon(self):
        net = Network(from_dict(small()))
        self.assertEqual(net.stpf(1,1,7).delay, 0)
        self.assertAlmostEqual(net.stpf(1,3,1).delay, .22)
        self.assertAlmostEqual(net.stpf(3,1,2).delay, .42)

    def test_shortest_delay_can_use_more_hops(self):
        data=small(); data['links'] += [{'u':1,'v':3,'rate':1}]
        net=Network(from_dict(data))
        self.assertEqual(net.stpf(1,3,1).nodes,(1,2,3))
        # 输入消息大小改变最优路径：小消息选择更少的转发次数。
        self.assertEqual(net.stpf(1,3,.001).nodes,(1,3))

    def test_tie_reconstruction_and_pruning(self):
        data=small(5)
        data['links']=[{'u':u,'v':v,'rate':10.} for u,v in [(1,2),(2,4),(1,3),(3,4),(1,5)]]
        net=Network(from_dict(data)); p=net.stpf(1,4,1)
        self.assertEqual(p.nodes,(1,2,4))
        self.assertTrue(all(abs(p.eat[v]-p.lat[v])<1e-12 for v in p.nodes))
        data['links'].remove({'u':3,'v':4,'rate':10.})
        p=Network(from_dict(data)).stpf(1,4,1)
        self.assertNotIn(3,p.eat)  # 可从源到达，但不能到目标的节点被裁去

    def test_no_path_in_direction_subgraph(self):
        data=small(); data['links']=[{'u':1,'v':3,'rate':10.},{'u':3,'v':2,'rate':10.}]
        with self.assertRaises(NoPath): Network(from_dict(data)).stpf(1,2,1)

    def test_stpf_against_exhaustive_paths(self):
        rng=random.Random(81)
        data=small(5)
        data['links']=[{'u':i,'v':j,'rate':rng.uniform(1,50)} for i,j in itertools.combinations(range(1,6),2)]
        net=Network(from_dict(data))
        for source,target in itertools.permutations(range(1,6),2):
            nodes=net.directed_nodes(source,target)
            for size in [.01,1.,200.]:
                candidates=[]
                for length in range(len(nodes)-1):
                    for inside in itertools.combinations(nodes[1:-1],length):
                        candidates.append(net.delay((source,)+inside+(target,),size))
                self.assertAlmostEqual(net.stpf(source,target,size).delay,min(candidates),places=12)

    def test_appendix_200_mbit(self):
        scenario,_=read_inputs(); p=Network(scenario).stpf(1,6,200)
        self.assertEqual(p.nodes,(1,3,4,6))
        self.assertAlmostEqual(p.delay,2.035,delta=.001)


class SchedulingTests(unittest.TestCase):
    def test_wait_for_slowest_replica(self):
        data=small(); data['dags'][0]['requirement']=.99
        data['dags'][0]['tasks'].append({'id':2,'input':1.,'output':1.,'workload':1.,'predecessors':[1]})
        result=Scheduler(from_dict(data)).run()
        first=[c for c in result['copies'] if c['task']==1]
        self.assertGreater(len(first),1)
        last=max(c['finish'] for c in first)
        for c in result['copies']:
            if c['task']==2: self.assertGreaterEqual(c['start']+1e-12,last+c['send'])
        self.assertEqual(validate_schedule(from_dict(data),result),[])

    def test_cpu_free_before_result_returns(self):
        data=small(2); data['dags'][0]['requirement']=.8
        data['vehicles'][1]['frequency']=100
        data['dags'][0]['tasks'][0]['output']=2
        data['dags'].append(copy.deepcopy(data['dags'][0])); data['dags'][1]['id']='b'
        result=Scheduler(from_dict(data)).run(); a,b=result['copies']
        self.assertEqual(a['vehicle'],2); self.assertEqual(b['vehicle'],2)
        self.assertAlmostEqual(b['start'],a['cpu_finish'])
        self.assertLess(b['start'],a['finish'])

    def test_arrivals_and_committed_fifo_no_insertion(self):
        data=small(1); data['vehicles'][0]['fault_rate']=0
        data['dags'][0]['arrival']=2
        data['dags'][0]['tasks'][0]['workload']=5
        later=copy.deepcopy(data['dags'][0]); later.update(id='b',arrival=3)
        later['tasks'][0]['workload']=.1; data['dags'].append(later)
        result=Scheduler(from_dict(data)).run(); a,b=result['copies']
        self.assertEqual(a['start'],2); self.assertEqual(b['start'],7)
        self.assertEqual(validate_schedule(from_dict(data),result),[])

    def test_entry_transmission_cannot_start_before_arrival(self):
        data=small(2); data['dags'][0]['arrival']=5; data['dags'][0]['requirement']=.8
        data['vehicles'][1]['frequency']=100
        row=Scheduler(from_dict(data)).run()['copies'][0]
        self.assertEqual(row['vehicle'],2); self.assertAlmostEqual(row['start'],5.11)

    def test_eq17_surplus_is_per_dag(self):
        req=.99; rho=req**.2
        self.assertAlmostEqual(required_target(req,5,0,0),rho)
        self.assertAlmostEqual(required_target(req,5,1,math.log(.9999)),req/(.9999*rho**3))
        self.assertLess(required_target(req,5,1,math.log(.9999)),rho)
        self.assertAlmostEqual(required_target(req,5,4,math.log(.999)),req/.999)

    def test_replica_cap_boundary_and_infeasible(self):
        self.assertEqual(replica_cap(1,.99),1)
        self.assertEqual(replica_cap(.9,.99),2)
        self.assertEqual(combined([.9,.9]),.99)
        data=small(1); data['dags'][0]['requirement']=.9999
        result=Scheduler(from_dict(data)).run()
        self.assertIn('a',result['infeasible']); self.assertFalse(result['copies'])
        metrics=calculate(from_dict(data),result)
        self.assertIsNone(metrics['mean_response_all_dags'])
        self.assertEqual(metrics['feasible_fraction'],0)

    def test_urgency_uses_current_local_start(self):
        scenario=from_dict(small()); s=Scheduler(scenario); dag=scenario.dags[0]; task=dag.tasks[0]
        s.prepare_dag(dag); s.free.update({1:10,2:20,3:30})
        self.assertAlmostEqual(s.urgency(dag,task),11-20)

    def test_reliability_excludes_queue_wait(self):
        scenario=from_dict(small()); s=Scheduler(scenario); dag=scenario.dags[0]; t=dag.tasks[0]
        p=s.placement(dag,t,scenario.vehicles[2]); first=s.candidate(dag,t,p)
        s.free[2]=100; second=s.candidate(dag,t,p)
        self.assertEqual(first.reliability,second.reliability)
        self.assertEqual(first.return_path,tuple(reversed(first.path)))
        self.assertAlmostEqual(first.reliability,math.exp(-.1*(1+.11+.11)))

    def test_paper_example_and_historical_table(self):
        scenario,rows=read_inputs(); result=Scheduler(scenario).run()
        self.assertEqual(validate_schedule(scenario,result),[])
        self.assertEqual(result['steps'][0]['selected_task'],11)
        # 论文称 τ11 需两副本；Eq11/17 在明确参数下第一副本已经达标。
        task=result['tasks'][0]
        self.assertEqual(task['copies'],1)
        self.assertGreaterEqual(task['reliability'],task['target'])
        for step in result['steps']:
            chosen=next(r for r in step['ready'] if (r['dag'],r['task'])==(step['selected_dag'],step['selected_task']))
            self.assertEqual(chosen['urgency'],min(r['urgency'] for r in step['ready']))
        old=replay(scenario,rows,True)
        self.assertTrue(validate_schedule(scenario,old,tolerance=.00011,require_complete=False))
        self.assertAlmostEqual(calculate(scenario,old)['mean_response_all_dags'],(.4882+.4796+.3961)/3)
        self.assertEqual(validate_schedule(scenario,replay(scenario,rows,False),require_complete=False),[])

    def test_random_dags_invariants(self):
        rng=random.Random(620)
        for iteration in range(20):
            data=small(4); data['dags']=[]
            for di in range(3):
                tasks=[]
                for tid in range(1,6):
                    pred=[p for p in range(1,tid) if rng.random()<.4]
                    if tid==5: pred=list(range(1,5))
                    tasks.append({'id':tid,'input':rng.random(), 'output':rng.random(),
                                  'workload':rng.uniform(.05,.5),'predecessors':pred})
                data['dags'].append({'id':str(di),'source':di+1,'arrival':di%2,
                                    'requirement':.99,'tasks':tasks})
            scenario=from_dict(data); result=Scheduler(scenario).run()
            self.assertFalse(result['infeasible'])
            self.assertEqual(validate_schedule(scenario,result),[])
            self.assertTrue(all(t['copies']<=t['cap'] for t in result['tasks']))

    def test_invalid_graph_and_units_rejected(self):
        data=small(); data['dags'][0]['tasks'][0]['predecessors']=[1]
        with self.assertRaises(ValueError): from_dict(data)
        data=small(); data['vehicles'][0]['frequency']=float('nan')
        with self.assertRaises(ValueError): from_dict(data)
        data=small(); data['dags'][0]['tasks'].append({'id':2,'input':0,'output':0,'workload':0})
        with self.assertRaises(ValueError): from_dict(data)

    def test_metrics_unequal_dags_per_source(self):
        data=small(2)
        data['dags']=[]
        for name,source,work in [('a',1,1),('b',1,3),('c',2,8)]:
            data['dags'].append({'id':name,'source':source,'arrival':0,'requirement':.9,
                                'tasks':[{'id':1,'input':0,'output':0,'workload':work}]})
        scenario=from_dict(data)
        result={'tasks':[{'dag':name,'task':1,'finish':finish,'reliability':1.} for name,finish in [('a',1),('b',3),('c',8)]],
                'copies':[],'infeasible':{}}
        metrics=calculate(scenario,result)
        self.assertEqual(metrics['mean_response_all_dags'],4)
        self.assertEqual(metrics['eq14_all_vehicles'],5)


if __name__=='__main__': unittest.main()
