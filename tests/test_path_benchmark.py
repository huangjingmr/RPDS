"""独立计时基准的路径结果回归，不用耗时阈值作测试。"""
import itertools
import unittest
from rpds.model import from_dict
from rpds.path_benchmark import dijkstra, stpf, verify
from rpds.network import Network
from test_rpds import small


class PathBenchmarkTests(unittest.TestCase):
    def test_single_source_matches_exhaustive_and_single_target(self):
        data=small(4)
        data['links']=[{'u':u,'v':v,'rate':2+u*v} for u,v in itertools.combinations(range(1,5),2)]
        scenario=from_dict(data)
        net=Network(scenario)
        for source in scenario.order:
            targets=list(scenario.order)
            batch=dijkstra(scenario,source,targets,.5)
            for target,result in zip(targets,batch):
                self.assertEqual(result,dijkstra(scenario,source,[target],.5)[0])
                middle=[v for v in targets if v not in (source,target)]
                costs=[0.] if source==target else []
                if source!=target:
                    for length in range(len(middle)+1):
                        for inside in itertools.permutations(middle,length):
                            costs.append(net.delay((source,)+inside+(target,),.5))
                self.assertAlmostEqual(result[0],min(costs))
            verify(scenario,source,targets,.5,stpf(scenario,source,targets,.5),batch)

    def test_nonmonotonic_route(self):
        data=small(3)
        data['links']=[{'u':1,'v':2,'rate':1},{'u':1,'v':3,'rate':100},{'u':3,'v':2,'rate':100}]
        scenario=from_dict(data)
        path=dijkstra(scenario,1,[2],1)[0]
        self.assertEqual(path[1],(1,3,2))
        self.assertAlmostEqual(path[0],.04)
        self.assertGreater(verify(scenario,1,[2],1,stpf(scenario,1,[2],1),[path]),0)
