import math
from . import data_apps
from .validation import integer

def stock_plan(db, data):
    lead = integer(data, 'lead_days', 1, 365) if 'lead_days' in data else 7
    safety = integer(data, 'safety_days', 0, 365) if 'safety_days' in data else 7
    start, end = db.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now','-30 days'),strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()
    rows = db.execute('SELECT p.*, COALESCE(SUM(m.delta),0) stock, COALESCE(SUM(CASE WHEN m.delta<0 AND julianday(m.created_at)>=julianday(?) AND julianday(m.created_at)<=julianday(?) THEN -m.delta ELSE 0 END),0) demand FROM products p LEFT JOIN movements m ON m.product_id=p.id GROUP BY p.id ORDER BY p.name', (start, end)).fetchall()
    items = []
    for row in rows:
        daily = row['demand'] / 30
        target = max(row['reorder_point'] + 1, math.ceil(daily * (lead + safety)))
        items.append({'sku': row['sku'], 'name': row['name'], 'stock': row['stock'], 'withdrawals_30d': row['demand'], 'daily_demand': round(daily, 3), 'days_cover': round(row['stock'] / daily, 1) if daily else None, 'target': target, 'order_quantity': max(0, target - row['stock'])})
    columns = ['sku', 'product', 'stock', 'withdrawals_30d', 'daily_demand', 'days_cover', 'target', 'order_quantity', 'lead_days', 'safety_days', 'window_start_utc', 'window_end_utc']
    exported = data_apps.safe_csv(columns, [[str(value) if value is not None else '' for value in [item['sku'], item['name'], item['stock'], item['withdrawals_30d'], item['daily_demand'], item['days_cover'], item['target'], item['order_quantity'], lead, safety, start, end]] for item in items])
    return {'items': items, 'lead_days': lead, 'safety_days': safety, 'demand_window': {'start': start, 'end': end, 'days': 30}, 'csv': exported, 'note': 'Uses gross withdrawals over the displayed trailing 30-day window, including days with no movements. Returns/receipts do not reduce measured demand. Stock reflects the current ledger. Target is max(reorder point + 1, expected lead + safety demand); this is a scenario, not a forecast or purchase order.'}
