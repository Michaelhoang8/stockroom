from http.client import HTTPConnection
import json
import csv
import io
import tempfile
import threading
import socket
import http.client
import unittest
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from portfolio.db import initialize, connect
from portfolio import services as s
from portfolio.server import handler_for
from portfolio.validation import ValidationError, ConflictError

class DomainTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'test.db')
        initialize(self.path)
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def product(self):
        return s.product_create(self.db, {'name': 'Keyboard', 'sku': 'KEY-01', 'reorder_point': 2})['id']

    def test_product_metadata_edit_is_versioned_without_changing_stock(self):
        product = self.product()
        s.movement_create(self.db, {'product_id': product, 'delta': 7, 'note': 'Opening stock'})
        updated = s.product_details(self.db, product, {'name': 'Renamed keyboard', 'reorder_point': 9, 'expected_version': 1})
        self.assertEqual(updated['version'], 2)
        replay = s.product_details(self.db, product, {'name': 'Renamed keyboard', 'reorder_point': 9, 'expected_version': 1})
        self.assertEqual(replay['version'], 2)
        row = s.snapshot(self.db)['products'][0]
        self.assertEqual((row['sku'], row['stock'], row['name'], row['reorder_point']), ('KEY-01', 7, 'Renamed keyboard', 9))
        self.assertEqual(self.db.execute('SELECT count(*) FROM movements').fetchone()[0], 1)

    def test_product_edit_validation_and_stale_versions_preserve_saved_values(self):
        product = self.product()
        s.product_details(self.db, product, {'reorder_point': 10, 'expected_version': 1})
        with self.assertRaises(ConflictError):
            s.product_details(self.db, product, {'name': 'Stale rename', 'expected_version': 1})
        for fields in ({'name': ''}, {'reorder_point': -1}, {'sku': 'REPLACED'}, {'expected_version': True}):
            with self.subTest(fields=fields), self.assertRaises(ValidationError):
                s.product_details(self.db, product, {'expected_version': 2, **fields})
        row = s.snapshot(self.db)['products'][0]
        self.assertEqual((row['name'], row['reorder_point'], row['version']), ('Keyboard', 10, 2))

    def test_concurrent_product_edit_has_one_winner(self):
        product = self.product()
        barrier = threading.Barrier(2)

        def edit(name):
            db = connect(self.path)
            try:
                barrier.wait(timeout=5)
                try:
                    return s.product_details(db, product, {'name': name, 'expected_version': 1})['version']
                except ConflictError:
                    return 'conflict'
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, ['First label', 'Second label']))
        self.assertCountEqual(results, [2, 'conflict'])

    def test_stock_rejects_overdraw_without_writing_ledger(self):
        pid = self.product()
        s.movement_create(self.db, {'product_id': pid, 'delta': 5, 'note': 'Receive'})
        with self.assertRaises(ValidationError):
            s.movement_create(self.db, {'product_id': pid, 'delta': -6, 'note': 'Ship'})
        self.assertEqual(s.snapshot(self.db)['products'][0]['stock'], 5)
        self.assertEqual(len(s.snapshot(self.db)['movements']), 1)

    def test_concurrent_withdrawals_cannot_oversell(self):
        pid = self.product()
        s.movement_create(self.db, {'product_id': pid, 'delta': 10, 'note': 'Receive'})
        barrier = threading.Barrier(2)

        def withdraw():
            db = connect(self.path)
            try:
                barrier.wait(timeout=5)
                s.movement_create(db, {'product_id': pid, 'delta': -7, 'note': 'Concurrent ship'})
                return True
            except ValidationError:
                return False
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: withdraw(), range(2)))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(s.snapshot(self.db)['products'][0]['stock'], 3)

    def test_stock_retry_replays_original_result_after_other_movements(self):
        pid = self.product()
        request = {'product_id': pid, 'delta': 5, 'note': 'Receipt', 'request_key': 'receipt-1'}
        first = s.movement_create(self.db, request)
        s.movement_create(self.db, {'product_id': pid, 'delta': -2, 'note': 'Later shipment'})
        other = connect(self.path)
        try:
            replay = s.movement_create(other, request)
            self.assertEqual(replay, {**first, 'replayed': True})
            with self.assertRaises(ConflictError):
                s.movement_create(other, {**request, 'delta': 6})
        finally:
            other.close()
        self.assertEqual(s.snapshot(self.db)['products'][0]['stock'], 3)
        self.assertEqual(len(s.snapshot(self.db)['movements']), 2)

    def test_concurrent_duplicate_receipts_write_once(self):
        pid = self.product()
        barrier = threading.Barrier(2)

        def receive(_):
            db = connect(self.path)
            try:
                barrier.wait(timeout=5)
                return s.movement_create(db, {'product_id': pid, 'delta': 7, 'note': 'Retry', 'request_key': 'same-receipt'})
            finally:
                db.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(receive, range(2)))
        self.assertEqual(sorted((r['replayed'] for r in results)), [False, True])
        self.assertEqual(results[0]['id'], results[1]['id'])
        self.assertEqual(s.snapshot(self.db)['products'][0]['stock'], 7)
        self.assertEqual(len(s.snapshot(self.db)['movements']), 1)

    def test_rejected_movement_does_not_reserve_request_key(self):
        pid = self.product()
        request = {'product_id': pid, 'delta': -1, 'note': 'Ship', 'request_key': 'pending-ship'}
        with self.assertRaises(ValidationError):
            s.movement_create(self.db, request)
        s.movement_create(self.db, {'product_id': pid, 'delta': 2, 'note': 'Receive'})
        self.assertFalse(s.movement_create(self.db, request)['replayed'])
        for invalid in ['', 'bad key', 17, True, 'a' * 129]:
            with self.subTest(key=invalid), self.assertRaises(ValidationError):
                s.movement_create(self.db, {**request, 'request_key': invalid})

    def test_full_ledger_export_exceeds_preview_and_escapes_spreadsheet_text(self):
        pid = s.product_create(self.db, {'name': '=SUM(1,2)', 'sku': '@FORMULA', 'reorder_point': 0})['id']
        with self.db:
            for index in range(503):
                self.db.execute('INSERT INTO movements(product_id,delta,note) VALUES (?,?,?)', (pid, 1, 'Receipt, with comma' if index else '=1+1'))
            self.db.execute('INSERT INTO movements(product_id,delta,note) VALUES (?,?,?)', (pid, -2, 'Ship'))
        chunks = list(s.movement_csv(self.db))
        self.assertEqual(len(chunks), 3)
        rows = list(csv.DictReader(io.StringIO(''.join(chunks))))
        self.assertEqual(len(rows), 504)
        self.assertEqual(len(s.snapshot(self.db)['movements']), 100)
        self.assertEqual(rows[0]['SKU'], "'@FORMULA")
        self.assertEqual(rows[0]['Product'], "'=SUM(1,2)")
        self.assertEqual(rows[0]['Reason'], "'=1+1")
        self.assertEqual(rows[1]['Reason'], 'Receipt, with comma')
        self.assertEqual(rows[-1]['Quantity change'], '-2')
        self.assertEqual(rows[-1]['Balance after movement'], '501')

    def test_empty_ledger_export_still_contains_headers(self):
        rows = list(csv.reader(io.StringIO(''.join(s.movement_csv(self.db)))))
        self.assertEqual(len(rows), 1)
        self.assertIn('Balance after movement', rows[0])

    def test_snapshot_is_consistent_during_concurrent_movement(self):
        pid = self.product()
        other = connect(self.path)
        wrote = []

        def concurrent_write(statement):
            if statement.startswith('SELECT m.*') and (not wrote):
                s.movement_create(other, {'product_id': pid, 'delta': 5, 'note': 'Concurrent receipt'})
                wrote.append(True)
        self.db.set_trace_callback(concurrent_write)
        try:
            snapshot = s.snapshot(self.db)
        finally:
            self.db.set_trace_callback(None)
            other.close()
        self.assertEqual(wrote, [True])
        self.assertEqual(snapshot['products'][0]['stock'], 0)
        self.assertEqual(snapshot['movements'], [])
        self.assertEqual(s.snapshot(self.db)['products'][0]['stock'], 5)

    def test_snapshot_preserves_callers_uncommitted_transaction(self):
        self.db.execute("INSERT INTO products(sku,name,reorder_point) VALUES ('UNCOMMITTED','Draft',0)")
        self.assertEqual(len(s.snapshot(self.db)['products']), 1)
        self.assertTrue(self.db.in_transaction)
        self.db.rollback()
        self.assertEqual(s.snapshot(self.db)['products'], [])
if __name__ == '__main__':
    unittest.main()
