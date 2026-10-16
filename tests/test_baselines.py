"""重建基线的算法定义、约束和闭环行为测试。"""
import unittest

from rpds.baselines import BASELINES, BaselineScheduler, run_baseline_closed_loop
from rpds.metrics import calculate
from rpds.model import from_dict
from rpds.paper_workloads import workload
from rpds.validation import validate_schedule


class BaselineTests(unittest.TestCase):
    def test_all_baselines_complete_and_validate(self):
        data = workload(2, 4, "ge", 2)
        for method, (_, allocation) in BASELINES.items():
            scenario, result, _ = run_baseline_closed_loop(data, method)
            issues = validate_schedule(
                scenario, result, allocation=allocation,
                allow_same_vehicle_replicas=method.startswith("local"))
            self.assertFalse(issues, (method, issues[:3]))
            metrics = calculate(scenario, result)
            self.assertEqual(metrics["infeasible_dags"], 0)
            self.assertTrue(all(dag["reliability"] >= dag["requirement"]
                                for dag in metrics["dags"]))

    def test_maxre_is_static_and_rr_updates_target(self):
        scenario = from_dict(workload(3, 4, "random", 1), allow_multiple_exits=True)
        dag = scenario.dags[0]
        static = BaselineScheduler(scenario, "heft_maxre")
        dynamic = BaselineScheduler(scenario, "heft_rr")
        for scheduler in (static, dynamic):
            scheduler.prepare_dag(dag)
        rho = dag.requirement ** (1 / len(dag.tasks))
        self.assertAlmostEqual(static.reliability_target(dag), rho)
        first = dynamic.task_order(dag)[0]
        dynamic.assign_heft(dag, first, dynamic.ranks[dag.id][first.id])
        self.assertLessEqual(dynamic.reliability_target(dag), rho + 1e-12)

    def test_local_replicas_stay_on_source_and_are_serial(self):
        data = workload(4, 4, "random", 1)
        scenario, result, _ = run_baseline_closed_loop(data, "local_maxre")
        for dag in scenario.dags:
            copies = [copy for copy in result["copies"] if copy["dag"] == dag.id]
            self.assertTrue(all(copy["vehicle"] == dag.source for copy in copies))
            self.assertTrue(all(copy["path"] == (dag.source,) for copy in copies))


if __name__ == "__main__":
    unittest.main()
