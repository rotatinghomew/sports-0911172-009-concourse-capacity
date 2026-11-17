import json
import tempfile
import unittest
from pathlib import Path
from product.app import Application

DATA=Path(__file__).parent.parent/'data'/'demo.json'


class ProductTests(unittest.TestCase):
    def setUp(self):
        self.app=Application()
        self.operations=json.loads(DATA.read_text())

    def tearDown(self):
        self.app.store.close()

    def test_workflow(self):
        self.app.replay(self.operations)
        state=self.app.store.snapshot()
        for kind,count in {'queue_ticket': 1, 'shift': 1, 'prepared': 1, 'scenario': 1}.items():
            self.assertEqual(len(state[kind]),count,kind)
        self.assertEqual(state['ingredient'][0]['units'],20)

    def test_business_rejection_is_atomic(self):
        self.app.replay(self.operations)
        before=self.app.store.snapshot()
        command,arguments=['staffing.assign', {'id': 'bad', 'worker': 'w1', 'window': 'b', 'skill': 'hot', 'start': '2026-10-18T19:45:00', 'end': '2026-10-18T20:15:00'}]
        with self.assertRaises(ValueError):
            self.app.execute(command,arguments)
        self.assertEqual(before,self.app.store.snapshot())

    def test_duplicate_identifier_does_not_mutate(self):
        first=self.operations[0]
        self.app.execute(*first)
        before=self.app.store.snapshot()
        with self.assertRaises(ValueError):
            self.app.execute(*first)
        self.assertEqual(before,self.app.store.snapshot())

    def test_outer_transaction_rolls_back_history(self):
        with self.assertRaises(RuntimeError):
            with self.app.store.atomic():
                self.app.execute(*self.operations[0])
                raise RuntimeError('取消操作')
        self.assertEqual(self.app.store.snapshot(),{})
        self.assertEqual(self.app.store.connection.execute('SELECT COUNT(*) FROM changes').fetchone()[0],0)

    def test_persistent_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            path=str(Path(directory)/'business.sqlite3')
            writer=Application(path)
            writer.replay(self.operations)
            expected=writer.store.snapshot()
            writer.store.close()
            reader=Application(path)
            try:
                self.assertEqual(reader.store.snapshot(),expected)
                first_kind=next(iter(expected))
                first_key=expected[first_kind][0]['id']
                self.assertTrue(reader.store.history(first_kind,first_key))
            finally:
                reader.store.close()

    def test_private_operation_rejected(self):
        with self.assertRaises(ValueError):
            self.app.execute('store._get',{})


if __name__=='__main__':
    unittest.main()
