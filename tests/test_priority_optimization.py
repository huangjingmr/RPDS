"""副本预测独立性、拓扑尾长与实际分配一致性。"""
import copy
import unittest
from rpds.model import from_dict
from rpds.variants import VariantScheduler
from rpds.priority_optimization import predict_replica_finish,remaining_path_tails
from rpds.paper_workloads import workload
from rpds.ablation_suite import evaluate
from test_rpds import small

class PriorityOptimizationTests(unittest.TestCase):
    def test_replica_prediction_matches_assignment_without_mutation(self):
        scenario=from_dict(workload(0,4,'random',1))
        for variant in ('replica_eft','path_slack'):
            scheduler=VariantScheduler(scenario,variant)
            for d in scenario.dags:scheduler.prepare_dag(d)
            ready=scheduler.ready_tasks(scenario.dags)
            for _ in range(12):
                dag,task=min(ready,key=lambda pair:(scheduler.urgency(*pair),pair[0].id,pair[1].id))
                before=copy.deepcopy((scheduler.free,scheduler.copies,scheduler.task_results,scheduler.log_products,scheduler.counts))
                finish,count=predict_replica_finish(scheduler,dag,task)
                self.assertEqual(before,(scheduler.free,scheduler.copies,scheduler.task_results,scheduler.log_products,scheduler.counts))
                scheduler.assign(dag,task,scheduler.urgency(dag,task))
                actual=scheduler.task_results[dag.id,task.id]
                self.assertEqual(finish,actual['finish']);self.assertEqual(count,actual['copies'])
                ready=scheduler.ready_tasks(scenario.dags)

    def test_tail_uses_graph_not_task_id(self):
        data=small(1);data['vehicles'][0]['fault_rate']=0
        data['dags'][0]['tasks']=[dict(id=i,input=0,output=0,workload=w,predecessors=pred)
                                 for i,w,pred in [(9,1,[]),(2,2,[9]),(7,3,[2])]]
        s=VariantScheduler(from_dict(data),'path_slack');dag=s.scenario.dags[0];s.prepare_dag(dag)
        self.assertEqual(s.path_tails[dag.id],{7:0.,2:3.,9:5.})

    def test_all_families_validate(self):
        for f in ('random','ge','fft'):
            for variant in ('replica_eft','path_slack'):
                result=evaluate(workload(1,4,f,2),variant)
                self.assertEqual(result['status'],'complete',result['validation_issues'])

if __name__=='__main__':unittest.main()

class SelectionTests(unittest.TestCase):
    def test_selection_uses_all_development_cases(self):
        from rpds.optimization_experiment import select_development
        rows=[dict(case=c,variant=v,status='complete',eq14_response_s=t)
              for c,values in [('a',(10,8,9)),('b',(10,9,7))]
              for v,t in zip(('fcfs','replica_eft','path_slack'),values)]
        self.assertEqual(select_development(rows)['selected'],'path_slack')
        rows[-1]['status']='invalid'
        self.assertEqual(select_development(rows)['selected'],'replica_eft')
