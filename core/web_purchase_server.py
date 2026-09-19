"""Local HTTP server: web purchase UI + API (same origin for save & medicine search)."""
import json
import mimetypes
import os
import shutil
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs, unquote, urlparse

try:
    from http.server import ThreadingHTTPServer as _ThreadingHTTPServer
except ImportError:

    class _ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True


class ThreadingHTTPServer(_ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

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


def prepare_web_purchase_root():
    """
    Return the folder that contains index.html.

    When frozen, copy bundled web_app files into LOCALAPPDATA so the server
    can write catalog.json and serve static files reliably (Win7/Win10 EXE).
    """
    import sys
    root = _web_app_dir()
    os.makedirs(root, exist_ok=True)
    if getattr(sys, 'frozen', False):
        bundle = os.path.join(sys._MEIPASS, 'web_app')
        # catalog.json is NOT in this list, and must never be.
        #
        # It is the SHOP'S OWN file: write_runtime_catalog builds it from this
        # store's database every time web purchase is opened -- its suppliers,
        # with their addresses, phone numbers, GSTINs and drug licence numbers.
        # A copy of it had been left in web_app/ in the repo, so it was bundled
        # into the EXE, and this loop then wrote ANOTHER shop's supplier list
        # over the shop's own on every single launch. Unconditionally, and
        # silently.
        #
        # launcher.html is gone for the same reason: 857 KB of one shop's real
        # supplier records, referenced by nothing but this line.
        for name in ('index.html', 'medicines.json'):
            src = os.path.join(bundle, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(root, name))
        src_assets = os.path.join(bundle, 'assets')
        dst_assets = os.path.join(root, 'assets')
        if os.path.isdir(src_assets):
            if os.path.isdir(dst_assets):
                shutil.rmtree(dst_assets, ignore_errors=True)
            shutil.copytree(src_assets, dst_assets)
    return root


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
    from core.layout_config import get_med_types
    return get_med_types()


def _is_online() -> bool:
    try:
        from core.sync_prefs import is_online_mode

        return bool(is_online_mode())
    except Exception:
        return False


def _get_suppliers(conn):
    # Online the engine's connection is sqlite3.connect(":memory:") -- an empty
    # shell. Reading suppliers from it returned NONE, and write_runtime_catalog
    # then saved that emptiness over the shop's own catalog.json, so the web
    # purchase-entry page lost every supplier it had.
    if _is_online():
        try:
            from core.online_catalog import suppliers as _online_suppliers

            rows = _online_suppliers() or []
            out = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                name = str(r.get('name') or '').strip()
                if not name:
                    continue
                out.append({
                    'name': name,
                    'address': str(r.get('address') or ''),
                    'phone': str(r.get('phone') or ''),
                    'gstin': str(r.get('gstin') or r.get('gst_number') or ''),
                    'dl_numbers': str(r.get('dl_numbers') or r.get('dl_number') or ''),
                })
            out.sort(key=lambda d: d['name'].lower())
            return {'ok': True, 'suppliers': out, 'count': len(out)}
        except Exception as e:
            # Do NOT fall through to the empty local table -- an unreachable
            # server must look like a failure, not like a shop with no suppliers.
            return {'ok': False, 'error': str(e), 'suppliers': []}
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
    if _is_online():
        try:
            from core.online_catalog import medicines as _online_medicines

            seen = {}
            for r in _online_medicines() or []:
                if not isinstance(r, dict):
                    continue
                nm = str(r.get('name') or '').strip()
                if nm:
                    seen.setdefault(nm.lower(), nm)
            return [seen[k] for k in sorted(seen)]
        except Exception:
            return []
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


def _get_inventory_stock(conn, query='', limit=100):
    """Aggregated stock by medicine name (no batch detail) for OPD prescription UI."""
    q = (query or '').strip()
    limit = max(1, min(int(limit or 100), 500))
    items = []
    try:
        cur = conn.cursor()
        if q:
            like = f'%{q}%'
            cur.execute(
                """
                SELECT name,
                       SUM(COALESCE(stock_qty, 0)) AS total_stock,
                       MAX(COALESCE(type, '')) AS med_type
                FROM medicines
                WHERE TRIM(COALESCE(name, '')) != ''
                  AND name LIKE ? COLLATE NOCASE
                GROUP BY name
                ORDER BY name COLLATE NOCASE
                LIMIT ?
                """,
                (like, limit),
            )
        else:
            cur.execute(
                """
                SELECT name,
                       SUM(COALESCE(stock_qty, 0)) AS total_stock,
                       MAX(COALESCE(type, '')) AS med_type
                FROM medicines
                WHERE TRIM(COALESCE(name, '')) != ''
                GROUP BY name
                ORDER BY name COLLATE NOCASE
                LIMIT ?
                """,
                (limit,),
            )
        for row in cur.fetchall():
            name = row[0]
            if not name:
                continue
            items.append({
                'name': name,
                'total_stock': int(row[1] or 0),
                'in_stock': int(row[1] or 0) > 0,
                'type': row[2] or '',
            })
    except Exception as e:
        return {'ok': False, 'error': str(e), 'items': []}
    return {'ok': True, 'items': items, 'count': len(items)}


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
    # A catalog with nothing in it is never an improvement on the one already
    # there. If the read failed -- server down, mode mid-switch -- keep the
    # shop's last good file rather than blanking the page it feeds.
    if not payload.get('suppliers') and not payload.get('inventory_medicine_names'):
        if os.path.isfile(path) and os.path.getsize(path) > 2:
            print(
                '[web purchase] keeping the existing catalog.json: '
                f"this read came back empty ({payload.get('supplier_error') or 'no rows'})"
            )
            return path
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
    return path


def _get_bootstrap(conn):
    """Lightweight — no medicine bulk load (387k+ names use /api/medicines/search)."""
    from core.app_prefs import load_app_mode
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
    from core.app_prefs import load_app_mode
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

        if path.rstrip('/') == '/api/inventory/stock':
            if conn is None:
                _json_response(self, 503, {'error': 'database not available'})
                return
            qs = parse_qs(urlparse(self.path).query)
            q = (qs.get('q') or [''])[0]
            limit = (qs.get('limit') or ['100'])[0]
            _json_response(self, 200, _get_inventory_stock(conn, q, limit))
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
    stop_web_purchase_server()
    _db['conn'] = _open_thread_safe_conn(conn)
    _web_root = web_root or prepare_web_purchase_root()

    last_err = None
    for attempt_port in range(port, port + 10):
        try:
            _server = ThreadingHTTPServer(('127.0.0.1', attempt_port), _WebPurchaseHandler)
            _port = attempt_port
            break
        except OSError as exc:
            last_err = exc
            _server = None
    else:
        raise RuntimeError(
            f'Could not start web purchase server on ports {port}-{port + 9}: {last_err}'
        )

    _server_thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _server_thread.start()
    return get_api_base_url()


def stop_web_purchase_server():
    global _server, _server_thread, _web_root
    srv = _server
    _server = None
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass
    _server_thread = None
    _close_server_conn()
    _web_root = None
    time.sleep(0.05)
