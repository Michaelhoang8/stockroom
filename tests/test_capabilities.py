import json
import csv
import io
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path
from portfolio import capabilities as c, data_apps as d, services
from portfolio.db import initialize, connect, SCHEMA
from portfolio.validation import ValidationError, NotFoundError

class PersistentCapabilities(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'test.db')
        initialize(self.path)
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def test_replenishment_uses_withdrawals_and_preserves_ledger(self):
        pid = services.product_create(self.db, {'name': 'Item', 'sku': 'ONE', 'reorder_point': 3})['id']
        services.movement_create(self.db, {'product_id': pid, 'delta': 100, 'note': 'Receipt'})
        services.movement_create(self.db, {'product_id': pid, 'delta': -90, 'note': 'Fulfillment'})
        item = c.stock_plan(self.db, {'lead_days': 7, 'safety_days': 7})['items'][0]
        self.assertEqual((item['daily_demand'], item['target'], item['order_quantity']), (3, 42, 32))
        self.assertEqual(services.snapshot(self.db)['products'][0]['stock'], 10)

    def test_replenishment_csv_preserves_assumptions_and_escapes_product_names(self):
        from datetime import datetime, timedelta
        services.product_create(self.db, {'name': '=SYNTHETIC()', 'sku': '+PLAN', 'reorder_point': 3})
        result = c.stock_plan(self.db, {'lead_days': 5, 'safety_days': 2})
        window = result['demand_window']
        self.assertEqual(datetime.fromisoformat(window['end']) - datetime.fromisoformat(window['start']), timedelta(days=30))
        exported = list(csv.DictReader(io.StringIO(result['csv'])))
        self.assertEqual(len(exported), 1)
        row = exported[0]
        self.assertEqual((row['sku'], row['product']), ("'+PLAN", "'=SYNTHETIC()"))
        self.assertEqual((row['lead_days'], row['safety_days'], row['order_quantity']), ('5', '2', '4'))
        self.assertEqual(row['days_cover'], '')
        self.assertEqual((row['window_start_utc'], row['window_end_utc']), (window['start'], window['end']))

    def test_replenishment_empty_workspace_exports_a_header(self):
        report = c.stock_plan(self.db, {})
        self.assertEqual(report['items'], [])
        self.assertEqual(report['demand_window']['days'], 30)
        self.assertEqual(len(list(csv.reader(io.StringIO(report['csv'])))), 1)

    def test_replenishment_zero_demand_has_undefined_coverage(self):
        services.product_create(self.db, {'name': 'New', 'sku': 'NEW', 'reorder_point': 3})
        item = c.stock_plan(self.db, {})['items'][0]
        self.assertIsNone(item['days_cover'])
        self.assertEqual(item['order_quantity'], 4)

    def test_replenishment_excludes_old_and_future_demand(self):
        pid = services.product_create(self.db, {'name': 'Dated', 'sku': 'DATES', 'reorder_point': 1})['id']
        with self.db:
            self.db.execute("INSERT INTO movements(product_id,delta,note) VALUES (?,100,'Receive')", (pid,))
            for modifier in ['-31 days', '+1 day', '-1 day']:
                self.db.execute("INSERT INTO movements(product_id,delta,note,created_at) VALUES (?,-10,'Dated',datetime('now',?))", (pid, modifier))
        item = c.stock_plan(self.db, {})['items'][0]
        self.assertEqual(item['withdrawals_30d'], 10)
        self.assertEqual(item['stock'], 70)
if __name__ == '__main__':
    unittest.main()
