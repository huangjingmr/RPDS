"""消融策略与配对输出的回归检查。"""
import json
import math
from pathlib import Path
import tempfile
import unittest
from dataclasses import asdict
from rpds import Scheduler, from_dict
from rpds.ablation import execute, run_case, aggregate, paired_comparisons, main
from rpds.ablation_inputs import generate
from rpds.network import Network
from rpds.variants import VARIANTS, VariantScheduler, UnrestrictedNetwork, MinimumHopNetwork
from test_rpds import small

ROOT = Path(__file__).resolve().parents[1]

class AblationTests(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT/'examples/paper_example.json').read_text())

    def test_default_preserves_schedule(self):
        old = Scheduler(from_dict(self.data)).run()
        scheduler = VariantScheduler(from_dict(self.data))
        new = scheduler.run()
        self.assertEqual(old['copies'], new['copies'])
        self.assertEqual(new, scheduler.result())
        for a, b in zip(old['tasks'], new['tasks']):
            b = dict(b); b['urgency'] = b.pop('priority_score')
            self.assertEqual(a, b)

    def test_one_policy_change(self):
        base = asdict(VARIANTS['rpds'])
        for name, policy in VARIANTS.items():
            # 组合变体用于在“最新RPDS”基础上只替换路径模块；
            # 其直接基准是path_slack，而不是保留旧紧迫度的rpds。
            combined = {'path_slack_dijkstra', 'path_slack_minhop'}
            reference = asdict(VARIANTS['path_slack']) if name in combined else base
            changes = sum(value != reference[key] for key,value in asdict(policy).items())
            self.assertEqual(changes, 0 if name == 'rpds' else 1)

    def test_static_target_and_all_variants_validate(self):
        for variant in VARIANTS:
            result = execute(self.data, variant)
            self.assertEqual(result['status'], 'complete', result['validation_issues'])
        scheduler = VariantScheduler(from_dict(self.data), 'static')
        for dag in scheduler.scenario.dags:
            self.assertAlmostEqual(scheduler.reliability_target(dag), math.exp(math.log(dag.requirement)/len(dag.tasks)))

    def test_unrestricted_path_can_reverse_direction(self):
        data=small()
        data['links']=[{'u':1,'v':2,'rate':1}, {'u':1,'v':3,'rate':100}, {'u':3,'v':2,'rate':100}]
        scenario=from_dict(data)
        self.assertEqual(Network(scenario).stpf(1,2,1).nodes,(1,2))
        path=UnrestrictedNetwork(scenario).stpf(1,2,1)
        self.assertEqual(path.nodes,(1,3,2))
        self.assertAlmostEqual(path.delay,.04)
        self.assertEqual(UnrestrictedNetwork(scenario).stpf(1,1,1).delay,0)

    def test_minimum_hop_uses_same_subgraph_but_different_objective(self):
        data=small()
        data['links']=[{'u':1,'v':3,'rate':1}, {'u':1,'v':2,'rate':100}, {'u':2,'v':3,'rate':100}]
        scenario=from_dict(data)
        self.assertEqual(Network(scenario).stpf(1,3,1).nodes,(1,2,3))
        self.assertEqual(MinimumHopNetwork(scenario).stpf(1,3,1).nodes,(1,3))

    def test_eft_score_and_selection(self):
        scheduler=VariantScheduler(from_dict(self.data), 'eft')
        for dag in scheduler.scenario.dags:
            scheduler.prepare_dag(dag)
        for dag,task in scheduler.ready_tasks(scheduler.scenario.dags):
            expected=min(max(scheduler.free[p.vehicle],dag.arrival+p.send)
                         +p.execution+p.return_delay
                         for p in scheduler.placements[dag.id,task.id])
            self.assertAlmostEqual(scheduler.urgency(dag,task),expected)
        result=VariantScheduler(from_dict(self.data),'eft').run()
        for step in result['steps']:
            chosen=min(step['ready'],key=lambda r:(r['priority_score'],r['dag'],r['task']))
            self.assertEqual((chosen['dag'],chosen['task']),
                             (step['selected_dag'],step['selected_task']))
        self.assertNotEqual(result['copies'],Scheduler(from_dict(self.data)).run()['copies'])

    def test_seed_and_shared_input(self):
        self.assertEqual(generate(7,4,5),generate(7,4,5))
        self.assertNotEqual(generate(7,4,5),generate(8,4,5))
        with tempfile.TemporaryDirectory() as path:
            rows=run_case(generate(7,4,5),'case',7,Path(path),VARIANTS)
            self.assertEqual(len({r['input_sha256'] for r in rows}),1)
            self.assertTrue(all(r['status']=='complete' for r in rows))
            self.assertEqual(len(paired_comparisons(rows)),len(VARIANTS)-1)
            failed=next(r for r in rows if r['variant']=='dijkstra')
            failed['status']='infeasible'; failed['mean_response_s']=None
            summary=aggregate(rows)
            self.assertIsNone(next(r for r in summary if r['variant']=='dijkstra')['mean_response_s'])
            self.assertIsNone(next(r for r in paired_comparisons(rows) if r['variant']=='dijkstra')['rpds_reduction_percent'])

    def test_output_cannot_overwrite(self):
        with tempfile.TemporaryDirectory() as path:
            marker=Path(path)/'keep'; marker.write_text('original')
            with self.assertRaises(SystemExit):
                main(['--output',path])
            self.assertEqual(marker.read_text(),'original')

if __name__=='__main__': unittest.main()
