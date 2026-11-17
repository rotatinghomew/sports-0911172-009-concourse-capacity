import json
import unittest
from pathlib import Path
from product.app import Application


class HistoricalDataTests(unittest.TestCase):
    def test_history_replays_without_external_services(self):
        app=Application()
        try:
            operations=json.loads((Path(__file__).parent.parent/'data'/'history.json').read_text())
            app.replay(operations)
            self.assertGreater(len(operations),100)
            self.assertTrue(app.store.snapshot())
        finally:
            app.store.close()
