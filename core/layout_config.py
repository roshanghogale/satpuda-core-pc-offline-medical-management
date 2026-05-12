import os, json, sys

_DEFAULTS = {
    'billing_rows': 8,
    'inventory_rows': 15,
    'sales_history_rows': 15,
    'purchase_history_rows': 15,
    'purchase_rows': 4,
    'doctors_rows': 6,
    'suppliers_rows': 8,
    'customers_rows': 15,
}

_TYPE_QTY_DEFAULTS = {
    'Tablet': 0, 'Syrup': 0, 'Injection': 0, 'Injection - Vial': 1,
    'Ointment': 0, 'Powder': 0, 'Bolus': 1, 'Liquid': 0,
    'Liniment': 0, 'Gel': 0, 'Vaccine': 0, 'Granules': 0,
}

_SCHEDULE_UNIT_DEFAULTS = {
    'Tablet': 'Tablets', 'Syrup': 'ml', 'Injection': 'ml',
    'Injection - Vial': 'Vial', 'Ointment': 'g', 'Powder': 'g',
    'Bolus': 'Bolus', 'Liquid': 'ml', 'Liniment': 'ml',
    'Gel': 'g', 'Vaccine': 'ml', 'Granules': 'g',
}

_DEFAULT_SCHEDULES = ['', 'H', 'H1', 'X', 'G', 'K', 'C', 'C1', 'P', 'N', 'M']

_DEFAULT_MED_TYPES = [
    'Tablet', 'Syrup', 'Injection', 'Injection - Vial',
    'Ointment', 'Powder', 'Bolus', 'Liquid', 'Liniment',
    'Gel', 'Vaccine', 'Granules'
]

def _get_config_dir():
    if getattr(sys, 'frozen', False):
        return os.path.join(
            os.environ.get('LOCALAPPDATA', os.path.expanduser('~')),
            'VeterinaryApp')
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config')

def _get_config_path():
    """Always returns the correct path regardless of os.chdir."""
    return os.path.join(_get_config_dir(), 'layout_config.txt')

# Keep for backward compat but don't use at module level
_CONFIG_PATH = _get_config_path()

def load_layout():
    """Always reads fresh from disk."""
    try:
        path = _get_config_path()
        if os.path.exists(path):
            data = json.loads(open(path).read())
            result = {k: int(data.get(k, v)) for k, v in _DEFAULTS.items()}
            # Load units for ALL saved med_types, not just the defaults
            saved_types = data.get('med_types', list(_DEFAULT_MED_TYPES))
            for t in saved_types:
                result[f'unit_{t}'] = data.get(f'unit_{t}', _SCHEDULE_UNIT_DEFAULTS.get(t, ''))
                result[f'typeqty_{t}'] = data.get(f'typeqty_{t}', _TYPE_QTY_DEFAULTS.get(t, 0))
            result['schedules'] = data.get('schedules', list(_DEFAULT_SCHEDULES))
            result['med_types'] = saved_types
            return result
    except Exception:
        pass
    result = dict(_DEFAULTS)
    for k, v in _SCHEDULE_UNIT_DEFAULTS.items():
        result[f'unit_{k}'] = v
    for k, v in _TYPE_QTY_DEFAULTS.items():
        result[f'typeqty_{k}'] = v
    result['schedules'] = list(_DEFAULT_SCHEDULES)
    result['med_types'] = list(_DEFAULT_MED_TYPES)
    return result

_load = load_layout

def save_layout(data):
    path = _get_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(json.dumps(data))

# ── Module-level constants — read ONCE at startup, correct after restart ──
# All runtime code should call load_layout() directly instead of these.
_cfg = load_layout()

BILLING_ROWS          = _cfg['billing_rows']
INVENTORY_ROWS        = _cfg['inventory_rows']
SALES_HISTORY_ROWS    = _cfg['sales_history_rows']
PURCHASE_HISTORY_ROWS = _cfg['purchase_history_rows']
PURCHASE_ROWS         = _cfg['purchase_rows']
DOCTORS_ROWS          = _cfg['doctors_rows']
SUPPLIERS_ROWS        = _cfg['suppliers_rows']
CUSTOMERS_ROWS        = _cfg['customers_rows']

# These are correct at startup (after restart). Use load_layout() for live reads.
SCHEDULE_UNIT = {k: _cfg.get(f'unit_{k}', v) for k, v in _SCHEDULE_UNIT_DEFAULTS.items()}
TYPE_QTY      = {k: _cfg.get(f'typeqty_{k}', v) for k, v in _TYPE_QTY_DEFAULTS.items()}
SCHEDULES     = _cfg.get('schedules', list(_DEFAULT_SCHEDULES))
MED_TYPES     = _cfg.get('med_types', list(_DEFAULT_MED_TYPES))
