import unittest
from datetime import datetime, timedelta
from product.app import Application


def match_ops(day, served=6, abandoned=2, waiting=1, unknown=1, prep=12):
    ops = [['kitchen.prepare', {'id': f'p{day}', 'ingredient': 'rice', 'units': prep,
                                'servings': prep, 'at': f'{day}T19:30:00'}]]
    for i in range(served):
        tid = f'q{day}-s{i}'
        called = f'{day}T19:4{i}:00' if i == 0 else f'{day}T19:4{i}:30'
        ops.append(['queueing.issue', {'id': tid, 'party': f'g{i}', 'at': f'{day}T19:4{i}:00',
                                       'recorded_at': f'{day}T19:4{i}:05',
                                       'window': 'v0' if i % 2 == 0 else 'v1'}])
        ops.append(['queueing.call', {'id': tid, 'at': called}])
        ops.append(['queueing.complete', {'id': tid, 'at': f'{day}T19:4{i}:50', 'cents': 2000 + 100 * i}])
        ops.append(['kitchen.consume', {'id': f'p{day}', 'count': 1}])
    for i in range(abandoned):
        tid = f'q{day}-a{i}'
        ops.append(['queueing.issue', {'id': tid, 'party': 'ga', 'at': f'{day}T19:5{i}:00',
                                       'recorded_at': f'{day}T19:5{i}:05', 'window': 'v0'}])
        ops.append(['queueing.abandon', {'id': tid, 'at': f'{day}T19:5{i+4}:00'}])
    for i in range(waiting):
        ops.append(['queueing.issue', {'id': f'q{day}-w{i}', 'party': 'gw', 'at': f'{day}T19:53:00',
                                       'recorded_at': f'{day}T19:53:05', 'window': 'v1'}])
    for i in range(unknown):
        ops.append(['queueing.issue', {'id': f'q{day}-u{i}', 'party': 'gu', 'at': None,
                                       'recorded_at': f'{day}T19:58:00', 'window': 'v1'}])
    return ops


def base_ops():
    ops = [['staffing.worker', {'id': 'a', 'skills': ['hot']}],
           ['staffing.worker', {'id': 'b', 'skills': ['cold']}],
           ['staffing.worker', {'id': 'c', 'skills': ['hot']}],
           ['kitchen.stock', {'id': 'rice', 'units': 1000}]]
    for day in ('2026-09-01', '2026-09-04', '2026-09-07'):
        ops += match_ops(day)
    return ops


EXTRA_SHIFT = {'worker': 'c', 'window': 'v9', 'skill': 'hot',
               'start': '2026-09-11T19:40:00', 'end': '2026-09-11T20:10:00'}


class AnalysisTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Application()
        self.app.replay(base_ops())

    def tearDown(self):
        self.app.store.close()

    def evaluate(self, plans, **kwargs):
        arguments = {'id': kwargs.pop('id', 'ev1'), 'plans': plans,
                     'constraints': {'immovable': ['a'], 'window_skill': {'v9': 'hot'}},
                     'assumptions': {'prep_service_gain_seconds': 10,
                                     'serving_cost_cents': 300, 'replications': 10}}
        arguments.update(kwargs)
        return self.app.execute('analysis.evaluate', arguments)


class CoverageTests(AnalysisTestCase):
    def test_categories_keep_observation_boundaries(self):
        report = self.app.execute('analysis.coverage', {})
        first = report['matches'][0]
        self.assertEqual(first['served'], 6)
        self.assertEqual(first['abandoned'], 2)
        self.assertEqual(first['still_waiting'], 2)
        self.assertEqual(first['unknown_time'], 1)
        self.assertEqual(first['no_wait'], 1)
        self.assertEqual(first['service_samples'], 6)
        self.assertEqual(report['totals']['unknown_time'], 3)

    def test_unknown_time_not_counted_as_observed_wait(self):
        report = self.app.execute('analysis.coverage', {})
        first = report['matches'][0]
        self.assertEqual(first['tickets'], 10)
        states = first['served'] + first['abandoned'] + first['still_waiting'] + first['called_open']
        self.assertEqual(states, 10)
        self.assertLessEqual(first['unknown_time'], first['still_waiting'])
        known_waits = first['served'] + first['abandoned'] + first['still_waiting'] - first['unknown_time']
        self.assertEqual(known_waits, 9)


class EvaluateTests(AnalysisTestCase):
    def test_constraint_violations_listed_with_performance(self):
        plans = [
            {'name': '加窗合规', 'shifts': [EXTRA_SHIFT], 'preparation': []},
            {'name': '加窗违规', 'shifts': [dict(EXTRA_SHIFT, worker='a')], 'preparation': []},
            {'name': '一人两窗', 'shifts': [EXTRA_SHIFT, dict(EXTRA_SHIFT, window='v8')], 'preparation': []},
            {'name': '备餐超量', 'shifts': [], 'preparation': [
                {'ingredient': 'rice', 'units': 5000, 'servings': 100, 'at': '2026-09-11T19:20:00'}]},
        ]
        report = self.evaluate(plans)['report']
        self.assertTrue(report['plans']['加窗合规']['feasible'])
        self.assertIn('不可移动', ''.join(report['feasibility']['加窗违规']))
        self.assertIn('一人不能同时上两个窗口', ''.join(report['feasibility']['一人两窗']))
        self.assertIn('不足', ''.join(report['feasibility']['备餐超量']))
        self.assertIsNone(report['plans']['备餐超量']['per_match'])
        self.assertIsNotNone(report['plans']['加窗合规']['summary'])
        self.assertIn('现状', report['plans'])

    def test_evaluate_does_not_consume_resources(self):
        before_shifts = len(self.app.store.all('shift'))
        before_units = self.app.store.get('ingredient', 'rice')['units']
        self.evaluate([{'name': '加窗合规', 'shifts': [EXTRA_SHIFT], 'preparation': []}])
        self.assertEqual(len(self.app.store.all('shift')), before_shifts)
        self.assertEqual(self.app.store.get('ingredient', 'rice')['units'], before_units)
        self.assertEqual(len(self.app.store.all('evaluation')), 1)

    def test_evaluation_is_deterministic(self):
        plans = [{'name': '加窗合规', 'shifts': [EXTRA_SHIFT], 'preparation': []}]
        first = self.evaluate(plans)['report']['plans']
        second = self.evaluate(plans, id='ev2')['report']['plans']
        self.assertEqual(first, second)

    def test_insufficient_data_blocks_recommendation(self):
        app = Application()
        try:
            app.replay([['kitchen.stock', {'id': 'rice', 'units': 100}],
                        ['queueing.issue', {'id': 'q1', 'party': 'g', 'at': None,
                                            'recorded_at': '2026-09-01T19:58:00', 'window': 'v0'}],
                        ['queueing.issue', {'id': 'q2', 'party': 'g', 'at': '2026-09-01T19:50:00',
                                            'recorded_at': '2026-09-01T19:50:05', 'window': 'v0'}],
                        ['queueing.abandon', {'id': 'q2', 'at': '2026-09-01T19:55:00'}]])
            report = app.execute('analysis.evaluate', {
                'id': 'evx', 'plans': [{'name': '加窗', 'shifts': [], 'preparation': []}]})['report']
            self.assertIsNone(report['recommendation'])
            self.assertTrue(report['recommendation_blocked'])
            self.assertIsNone(report['validation'])
        finally:
            app.store.close()

    def test_validation_and_assumptions_reported(self):
        report = self.evaluate([{'name': '加窗合规', 'shifts': [EXTRA_SHIFT], 'preparation': []}])['report']
        self.assertEqual(len(report['validation']['per_match']), 3)
        self.assertIn('unserved_share', report['validation']['mean_abs_error'])
        self.assertTrue(any('未验证假设' in note for note in report['assumptions']))
        self.assertIsNone(report['recommendation'])
        self.assertTrue(any('历史检验' in reason for reason in report['recommendation_blocked']))


def consistent_ops():
    """容量受限且与回放模型一致的场次：9 人成交、3 人终未叫号，服务时长恒定。"""
    ops = [['staffing.worker', {'id': 'c', 'skills': ['hot']}],
           ['kitchen.stock', {'id': 'rice', 'units': 1000}]]
    for day in ('2026-09-01', '2026-09-04', '2026-09-07'):
        base = datetime.fromisoformat(f'{day}T19:40:00')
        ops.append(['kitchen.prepare', {'id': f'p{day}', 'ingredient': 'rice', 'units': 12,
                                        'servings': 12, 'at': f'{day}T19:30:00'}])
        for i in range(12):
            at = (base + timedelta(seconds=60 * i)).isoformat()
            tid = f'q{day}-{i}'
            ops.append(['queueing.issue', {'id': tid, 'party': f'g{i}', 'at': at,
                                           'recorded_at': at, 'window': f'v{i % 2}'}])
            if i < 9:
                called = (base + timedelta(seconds=60 * i + 120)).isoformat()
                done = (base + timedelta(seconds=60 * i + 720)).isoformat()
                ops.append(['queueing.call', {'id': tid, 'at': called}])
                ops.append(['queueing.complete', {'id': tid, 'at': done, 'cents': 2000}])
                ops.append(['kitchen.consume', {'id': f'p{day}', 'count': 1}])
    return ops


class RecommendationTests(unittest.TestCase):
    def setUp(self):
        self.app = Application()
        self.app.replay(consistent_ops())

    def tearDown(self):
        self.app.store.close()

    def evaluate(self, plans):
        return self.app.execute('analysis.evaluate', {
            'id': 'evr', 'plans': plans,
            'constraints': {'window_skill': {'v9': 'hot'}},
            'assumptions': {'prep_service_gain_seconds': 10, 'serving_cost_cents': 300,
                            'replications': 5}})['report']

    def test_recommends_plan_that_relieves_capacity(self):
        report = self.evaluate([{'name': '加窗提能', 'shifts': [
            {'worker': 'c', 'window': 'v9', 'skill': 'hot',
             'start': '2026-09-11T19:40:00', 'end': '2026-09-11T20:10:00'}], 'preparation': []}])
        recommendation = report['recommendation']
        self.assertIsNotNone(recommendation)
        self.assertEqual(recommendation['plan'], '加窗提能')
        self.assertGreater(recommendation['median_net_increment_cents'], 0)

    def test_recommends_baseline_when_prep_only_loses_money(self):
        report = self.evaluate([{'name': '只备餐', 'shifts': [], 'preparation': [
            {'ingredient': 'rice', 'units': 6, 'servings': 6, 'at': '2026-09-11T19:20:00'}]}])
        self.assertEqual(report['recommendation']['plan'], '现状')


class AdoptionTests(AnalysisTestCase):
    def prepare_plan(self):
        return [{'name': '提前备餐', 'shifts': [], 'preparation': [
            {'ingredient': 'rice', 'units': 30, 'servings': 20, 'at': '2026-09-11T19:20:00'}]}]

    def test_absence_after_analysis_blocks_adoption(self):
        self.evaluate([{'name': '加窗合规', 'shifts': [EXTRA_SHIFT], 'preparation': []}])
        self.app.execute('scenarios.save', {'id': 's9', 'shifts': [EXTRA_SHIFT],
                                            'preparation': [], 'reason': '按 ev1 比较',
                                            'evaluation': 'ev1'})
        self.app.execute('staffing.absence', {'id': 'c'})
        with self.assertRaises(ValueError):
            self.app.execute('scenarios.adopt', {'id': 's9'})
        self.assertEqual(self.app.store.get('scenario', 's9')['state'], 'draft')

    def test_waste_after_analysis_blocks_adoption(self):
        self.evaluate(self.prepare_plan())
        self.app.execute('scenarios.save', {'id': 's10', 'shifts': [],
                                            'preparation': self.prepare_plan()[0]['preparation'],
                                            'reason': '按 ev1 比较', 'evaluation': 'ev1'})
        self.app.execute('kitchen.waste', {'id': 'rice', 'units': 940})
        with self.assertRaises(ValueError):
            self.app.execute('scenarios.adopt', {'id': 's10'})
        self.assertEqual(self.app.store.get('scenario', 's10')['state'], 'draft')

    def test_adoption_marks_inputs_changed_since_analysis(self):
        self.evaluate(self.prepare_plan())
        self.app.execute('scenarios.save', {'id': 's11', 'shifts': [],
                                            'preparation': self.prepare_plan()[0]['preparation'],
                                            'reason': '按 ev1 比较', 'evaluation': 'ev1'})
        self.app.execute('kitchen.prepare', {'id': 'other', 'ingredient': 'rice', 'units': 10,
                                             'servings': 5, 'at': '2026-09-10T10:00:00'})
        adopted = self.app.execute('scenarios.adopt', {'id': 's11'})
        self.assertEqual(adopted['state'], 'adopted')
        self.assertTrue(any('rice' in note for note in adopted['input_changes']))

    def test_adopted_plan_keeps_rationale(self):
        self.evaluate(self.prepare_plan())
        self.app.execute('scenarios.save', {'id': 's12', 'shifts': [],
                                            'preparation': self.prepare_plan()[0]['preparation'],
                                            'reason': '按 ev1 比较', 'evaluation': 'ev1'})
        self.app.execute('scenarios.adopt', {'id': 's12'})
        history = self.app.store.history('scenario', 's12')
        self.assertEqual(history[0]['payload']['reason'], '按 ev1 比较')
        self.assertEqual(history[0]['payload']['evaluation'], 'ev1')
        archived = self.app.store.get('evaluation', 'ev1')
        self.assertIn('提前备餐', archived['report']['plans'])
        self.assertTrue(archived['snapshot'])


if __name__ == '__main__':
    unittest.main()
