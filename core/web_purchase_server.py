"""Local HTTP server: web purchase UI + API (same origin for save & medicine search)."""
import json
import mimetypes
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

_DEFAULT_PORT = 8765
_server = None
_server_thread = None
_port = _DEFAULT_PORT
_web_root = None

# Dedicated SQLite connection for HTTP worker threads (main app conn is same-thread only).
_db = {'conn': None}


def _db_path_from_conn(conn):
    try:
        for row in conn.execute('PRAGMA database_list').fetchall():
            if row[1] == 'main' and row[2]:
                return row[2]
    except Exception:
        pass
    return None


def _open_thread_safe_conn(app_conn):
    """Open a connection the background server may use from any thread."""
    if app_conn is None:
        return None
    path = _db_path_from_conn(app_conn)
    if not path:
        return app_conn
    return sqlite3.connect(path, check_same_thread=False)


def _close_server_conn():
    conn = _db.get('conn')
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
    _db['conn'] = None


def _active_conn():
    return _db.get('conn')


def _web_app_dir():
    import sys
    if getattr(sys, 'frozen', False):
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp', 'web_app',
        )
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, 'web_app')


def _json_response(handler, status, payload):
    body = json.dumps(payload).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Access-Control-Allow-Origin', '*')
    handler.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    handler.send_header('Access-Control-Allow-Headers', 'Content-Type')
    handler.send_header('Content-Length', str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _layout_schedules():
    from core.layout_config import load_layout, _DEFAULT_SCHEDULES
    layout = load_layout()
    schedules = layout.get('schedules') or list(_DEFAULT_SCHEDULES)
    return schedules if schedules else list(_DEFAULT_SCHEDULES)


def _layout_med_types():
    from core.layout_config import load_layout, _DEFAULT_MED_TYPES
    layout = load_layout()
    med_types = layout.get('med_types') or list(_DEFAULT_MED_TYPES)
    return med_types if med_types else list(_DEFAULT_MED_TYPES)


def _get_suppliers(conn):
    if conn is None:
        return {'ok': False, 'error': 'database not connected', 'suppliers': []}
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT name, address, phone, gstin, dl_numbers
            FROM suppliers
            WHERE TRIM(COALESCE(name, '')) != ''
            ORDER BY name COLLATE NOCASE
        """)
        suppliers = [
            {
                'name': r[0] or '',
                'address': r[1] or '',
                'phone': r[2] or '',
                'gstin': r[3] or '',
                'dl_numbers': r[4] or '',
            }
            for r in cur.fetchall()
        ]
        return {'ok': True, 'suppliers': suppliers, 'count': len(suppliers)}
    except Exception as e:
        return {'ok': False, 'error': str(e), 'suppliers': []}


def _get_schedules():
    return {'ok': True, 'schedules': _layout_schedules()}


def _get_med_types():
    return {'ok': True, 'med_types': _layout_med_types()}


def _inventory_medicine_names(conn):
    """Distinct medicine names already in your stock (including newly saved purchases)."""
    names = []
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT DISTINCT name FROM medicines
            WHERE TRIM(COALESCE(name, '')) != ''
            ORDER BY name COLLATE NOCASE
        """)
        names = [r[0] for r in cur.fetchall() if r[0]]
    except Exception:
        pass
    return names


def build_runtime_catalog(conn):
    """Suppliers from DB + schedules/types from layout (same sources as desktop UI)."""
    sup = _get_suppliers(conn)
    inventory_names = _inventory_medicine_names(conn) if conn is not None else []
    return {
        'ok': True,
        'suppliers': sup.get('suppliers', []) if sup.get('ok') else [],
        'schedules': _layout_schedules(),
        'med_types': _layout_med_types(),
        'inventory_medicine_names': inventory_names,
        'supplier_count': len(sup.get('suppliers', []) or []),
        'supplier_error': sup.get('error'),
    }


def write_runtime_catalog(web_root, conn):
    """
    Write catalog.json next to index.html when opening web purchase.
    Web UI loads this file (like medicines.json) — reliable vs stale API connection.
    """
    payload = build_runtime_catalog(conn)
    path = os.path.join(web_root, 'catalog.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def _get_bootstrap(conn):
    """Lightweight — no medicine bulk load (387k+ names use /api/medicines/search)."""
    from core.app_setup import load_app_mode
    return {
        'ok': True,
        'schedules': _layout_schedules(),
        'med_types': _layout_med_types(),
        'suppliers': _get_suppliers(conn).get('suppliers', []),
        'app_mode': load_app_mode(),
        'medicine_search': 'api',
    }


def _search_inventory_medicines(conn, query, limit):
    q = (query or '').strip()
    limit = max(1, min(int(limit or 50), 100))
    cur = conn.cursor()
    if q:
        like = f'%{q}%'
        cur.execute(
            """
            SELECT DISTINCT name FROM medicines
            WHERE TRIM(COALESCE(name, '')) != ''
              AND name LIKE ? COLLATE NOCASE
            ORDER BY name COLLATE NOCASE
            LIMIT ?
            """,
            (like, limit),
        )
    else:
        cur.execute(
            """
            SELECT DISTINCT name FROM medicines
            WHERE TRIM(COALESCE(name, '')) != ''
            ORDER BY name COLLATE NOCASE
            LIMIT ?
            """,
            (limit,),
        )
    return [r[0] for r in cur.fetchall() if r[0]]


def _search_medicines(conn, query, limit=50, min_master_chars=2):
    """Inventory first; master DB (387k+) only when query has min_master_chars+ letters."""
    from core.app_setup import load_app_mode
    from core.master_medicine_service import search_master_names

    q = (query or '').strip()
    limit = max(1, min(int(limit or 50), 100))
    names = []
    seen = set()

    try:
        for n in _search_inventory_medicines(conn, q, limit):
            key = n.lower()
            if key not in seen:
                seen.add(key)
                names.append(n)
            if len(names) >= limit:
                break

        remaining = limit - len(names)
        if remaining > 0 and load_app_mode() == 'medical' and len(q) >= min_master_chars:
            for n in search_master_names(q, limit=remaining):
                key = n.lower()
                if key not in seen:
                    seen.add(key)
                    names.append(n)
                if len(names) >= limit:
                    break
    except Exception:
        pass

    hint = None
    if load_app_mode() == 'medical' and q and len(q) < min_master_chars:
        hint = (
            f'Type at least {min_master_chars} characters to search '
            'the master medicine list (387k+ names).'
        )
    elif load_app_mode() == 'medical' and not q:
        hint = (
            'Showing your inventory medicines. '
            'Type 2+ letters to search the master list.'
        )

    return {'ok': True, 'names': names, 'query': q, 'hint': hint}


class _WebPurchaseHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def _send_cors_preflight(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_OPTIONS(self):
        self._send_cors_preflight()

    def do_GET(self):
        global _web_root
        path = urlparse(self.path).path
        path = unquote(path)
        conn = _active_conn()

        if path == '/favicon.ico':
            self.send_response(204)
            self.end_headers()
            return

        if path.rstrip('/') == '/api/health':
            _json_response(self, 200, {
                'ok': True,
                'service': 'web-purchase',
                'db_connected': conn is not None,
            })
            return

        if path.rstrip('/') == '/api/bootstrap':
            if conn is None:
                _json_response(self, 503, {'error': 'database not available'})
                return
            _json_response(self, 200, _get_bootstrap(conn))
            return

        if path.rstrip('/') == '/api/suppliers':
            if conn is None:
                _json_response(self, 503, {'error': 'database not available'})
                return
            payload = _get_suppliers(conn)
            status = 200 if payload.get('ok') else 500
            _json_response(self, status, payload)
            return

        if path.rstrip('/') == '/api/schedules':
            _json_response(self, 200, _get_schedules())
            return

        if path.rstrip('/') == '/api/med-types':
            _json_response(self, 200, _get_med_types())
            return

        if path.rstrip('/') == '/api/medicines/search':
            if conn is None:
                _json_response(self, 503, {'error': 'database not available'})
                return
            qs = parse_qs(urlparse(self.path).query)
            q = (qs.get('q') or [''])[0]
            limit = (qs.get('limit') or ['50'])[0]
            _json_response(self, 200, _search_medicines(conn, q, limit))
            return

        if path.rstrip('/') == '/api/inventory-medicines':
            if conn is None:
                _json_response(self, 503, {'error': 'database not available'})
                return
            _json_response(self, 200, {
                'ok': True,
                'names': _inventory_medicine_names(conn),
            })
            return

        self._serve_static(path)

    def do_POST(self):
        path = urlparse(self.path).path.rstrip('/')
        conn = _active_conn()

        if path == '/api/suppliers':
            if conn is None:
                _json_response(self, 503, {'error': 'database not available'})
                return
            try:
                length = int(self.headers.get('Content-Length', 0))
                raw = self.rfile.read(length).decode('utf-8') if length else '{}'
                data = json.loads(raw)
            except json.JSONDecodeError as e:
                _json_response(self, 400, {'error': f'invalid JSON: {e}'})
                return
            except Exception as e:
                _json_response(self, 400, {'error': str(e)})
                return
            try:
                from core.purchase_service import get_or_create_supplier
                name = (data.get('name') or '').strip()
                if not name:
                    _json_response(self, 400, {'error': 'supplier name is required'})
                    return
                sid = get_or_create_supplier(
                    conn,
                    name,
                    (data.get('address') or '').strip(),
                    (data.get('phone') or '').strip(),
                    (data.get('gstin') or '').strip(),
                    (data.get('dl_numbers') or '').strip(),
                )
                conn.commit()
                root = _web_root or _web_app_dir()
                try:
                    write_runtime_catalog(root, conn)
                except Exception:
                    pass
                cur = conn.cursor()
                cur.execute(
                    "SELECT id, name, address, phone, gstin, dl_numbers "
                    "FROM suppliers WHERE id=?",
                    (sid,),
                )
                row = cur.fetchone()
                supplier = {
                    'id': row[0],
                    'name': row[1] or '',
                    'address': row[2] or '',
                    'phone': row[3] or '',
                    'gstin': row[4] or '',
                    'dl_numbers': row[5] or '',
                }
                _json_response(self, 200, {'ok': True, 'supplier': supplier})
            except Exception as e:
                _json_response(self, 500, {'error': str(e)})
            return

        if path != '/api/purchases/save':
            _json_response(self, 404, {'error': 'not found'})
            return
        if conn is None:
            _json_response(self, 503, {'error': 'database not available'})
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            raw = self.rfile.read(length).decode('utf-8') if length else '{}'
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            _json_response(self, 400, {'error': f'invalid JSON: {e}'})
            return
        except Exception as e:
            _json_response(self, 400, {'error': str(e)})
            return

        try:
            from core.web_purchase_save import save_purchases_from_web_json
            result = save_purchases_from_web_json(conn, data)
            if result.get('saved', 0) > 0:
                root = _web_root or _web_app_dir()
                try:
                    write_runtime_catalog(root, conn)
                    result['inventory_medicine_names'] = _inventory_medicine_names(conn)
                except Exception:
                    result['inventory_medicine_names'] = result.get('saved_medicine_names', [])
            status = 200 if not result['errors'] else 207
            _json_response(self, status, result)
        except ValueError as e:
            _json_response(self, 400, {'error': str(e)})
        except Exception as e:
            _json_response(self, 500, {'error': str(e)})

    def _serve_static(self, path):
        root = _web_root or _web_app_dir()
        if path in ('', '/'):
            path = '/index.html'
        safe = path.lstrip('/').replace('..', '')
        file_path = os.path.join(root, safe)
        if not os.path.isfile(file_path):
            self.send_error(404)
            return
        mime, _ = mimetypes.guess_type(file_path)
        if not mime:
            mime = 'application/octet-stream'
        try:
            with open(file_path, 'rb') as f:
                data = f.read()
        except OSError:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache')
        self.end_headers()
        self.wfile.write(data)


def get_api_base_url():
    return f'http://127.0.0.1:{_port}'


def start_web_purchase_server(conn, port=_DEFAULT_PORT, web_root=None):
    """Start background server with a thread-safe DB connection to the app database."""
    global _server, _server_thread, _port, _web_root
    _close_server_conn()
    _db['conn'] = _open_thread_safe_conn(conn)
    _port = port
    _web_root = web_root or _web_app_dir()

    if _server is not None:
        return get_api_base_url()

    _server = ThreadingHTTPServer(('127.0.0.1', port), _WebPurchaseHandler)
    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    return get_api_base_url()


def stop_web_purchase_server():
    global _server, _server_thread, _web_root
    if _server is not None:
        try:
            _server.shutdown()
        except Exception:
            pass
        _server = None
        _server_thread = None
    _close_server_conn()
    _web_root = None
