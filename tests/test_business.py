import json
import unittest
from pathlib import Path
from product.app import Application


class BusinessBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.app=Application()
        self.app.replay(json.loads((Path(__file__).parent.parent/'data'/'demo.json').read_text()))

    def tearDown(self):
        self.app.store.close()
    def test_absence_rejects_entire_adoption(self):
        self.app.execute('scenarios.save',dict(id='s2',shifts=[dict(worker='w1',window='b',skill='hot',
            start='2026-10-19T19:40:00',end='2026-10-19T20:00:00')],
            preparation=[dict(ingredient='rice',units=5,servings=10,at='2026-10-19T19:30:00')],reason='备选安排'))
        self.app.execute('staffing.absence',dict(id='w1'))
        before=self.app.store.snapshot()
        with self.assertRaises(ValueError):
            self.app.execute('scenarios.adopt',dict(id='s2'))
        self.assertEqual(self.app.store.snapshot(),before)

    def test_save_does_not_consume_resources(self):
        before=self.app.store.get('ingredient','rice')
        self.app.execute('scenarios.save',dict(id='s3',shifts=[],preparation=[],reason='仅作比较'))
        self.assertEqual(self.app.store.get('ingredient','rice'),before)
