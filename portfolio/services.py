import re
from .db import rows
from .validation import ValidationError, NotFoundError, ConflictError, text, integer

def product_create(db, data):
    values = (text(data, 'sku', 40).upper(), text(data, 'name'), integer(data, 'reorder_point'))
    with db:
        product_id = db.execute('INSERT INTO products(sku,name,reorder_point) VALUES (?,?,?)', values).lastrowid
    return {'id': product_id}

def product_details(db, product_id, data):
    if set(data) - {'name', 'reorder_point', 'expected_version'}:
        raise ValidationError('Only product name and reorder point can be edited')
    version = integer(data, 'expected_version', 1)
    with db:
        db.execute('BEGIN IMMEDIATE')
        old = db.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
        if old is None:
            raise NotFoundError('Product not found')
        merged = {**dict(old), **data}
        name, reorder = (text(merged, 'name'), integer(merged, 'reorder_point'))
        changed = name != old['name'] or reorder != old['reorder_point']
        if changed:
            if version != old['version']:
                raise ConflictError('This product changed since it was loaded. Your edits are preserved; load saved values before editing again.')
            db.execute('UPDATE products SET name=?,reorder_point=?,version=version+1 WHERE id=?', (name, reorder, product_id))
        return {'id': product_id, 'name': name, 'reorder_point': reorder, 'version': old['version'] + bool(changed)}

def movement_create(db, data):
    product_id = integer(data, 'product_id', 1)
    delta = integer(data, 'delta', -1000000, 1000000)
    note = text(data, 'note', 500)
    request_key = data.get('request_key')
    if request_key is not None and (not isinstance(request_key, str) or not re.fullmatch('[A-Za-z0-9_.:-]{1,128}', request_key)):
        raise ValidationError('request_key must contain 1–128 ASCII letters, digits, or _ . : -')
    if delta == 0:
        raise ValidationError('Quantity must not be zero')
    with db:
        db.execute('BEGIN IMMEDIATE')
        if request_key is not None:
            previous = db.execute('SELECT * FROM movement_requests WHERE request_key=?', (request_key,)).fetchone()
            if previous:
                if (previous['product_id'], previous['delta'], previous['note']) != (product_id, delta, note):
                    raise ConflictError('request_key was already used for a different movement')
                return {'id': previous['movement_id'], 'stock': previous['stock_after'], 'replayed': True}
        if db.execute('SELECT id FROM products WHERE id=?', (product_id,)).fetchone() is None:
            raise NotFoundError('Product not found')
        stock = db.execute('SELECT COALESCE(SUM(delta),0) FROM movements WHERE product_id=?', (product_id,)).fetchone()[0]
        if stock + delta < 0:
            raise ValidationError(f'Insufficient stock: only {stock} available')
        movement_id = db.execute('INSERT INTO movements(product_id,delta,note) VALUES (?,?,?)', (product_id, delta, note)).lastrowid
        if request_key is not None:
            db.execute('INSERT INTO movement_requests VALUES (?,?,?,?,?,?)', (request_key, movement_id, product_id, delta, note, stock + delta))
    return {'id': movement_id, 'stock': stock + delta, 'replayed': False}

def snapshot(db):
    if db.in_transaction:
        return _snapshot_rows(db)
    with db:
        db.execute('BEGIN')
        return _snapshot_rows(db)

def _snapshot_rows(db):
    return {'products': rows(db.execute('SELECT p.*, COALESCE(SUM(m.delta),0) AS stock FROM products p LEFT JOIN movements m ON p.id=m.product_id GROUP BY p.id ORDER BY p.name')), 'movements': rows(db.execute('SELECT m.*,p.sku FROM movements m JOIN products p ON m.product_id=p.id ORDER BY m.id DESC LIMIT 100'))}

def movement_csv(db, batch_size=500):
    """Yield the full ledger as CSV chunks from one SQLite query snapshot."""
    from .data_apps import safe_csv
    cursor = db.execute('SELECT m.id,m.created_at,p.sku,p.name,m.delta,\n        SUM(m.delta) OVER (PARTITION BY m.product_id ORDER BY m.id) AS balance,m.note\n        FROM movements m JOIN products p ON p.id=m.product_id ORDER BY m.id')
    try:
        yield safe_csv(['Movement ID', 'Timestamp UTC', 'SKU', 'Product', 'Quantity change', 'Balance after movement', 'Reason'], [])
        while True:
            batch = cursor.fetchmany(batch_size)
            if not batch:
                break
            yield safe_csv([], [[str(value) for value in row] for row in batch], include_header=False)
    finally:
        cursor.close()
