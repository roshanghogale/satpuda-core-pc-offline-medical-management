import os, json, shutil, sys

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

_BANNER_DEFAULTS = {
    'home_banner_size': 1500,
    'home_banner_use_default': False,
    'home_banner_path': '',
    # Legacy keys — migrated on load
    'home_banner_width': 1500,
    'home_banner_height': 0,
}

_TYPE_QTY_DEFAULTS = {
    'Tablet': 0, 'Syrup': 0, 'Injection': 0, 'Injection - Vial': 1,
    'Ointment': 0, 'Powder': 0, 'Bolus': 1, 'Liquid': 0,
    'Liniment': 0, 'Gel': 0, 'Vaccine': 0, 'Granules': 0,
    'Tablet Pack': 0, 'Bolus Pack': 0,
}

_SCHEDULE_UNIT_DEFAULTS = {
    'Tablet': 'd', 'Syrup': 'ml', 'Injection': 'ml',
    'Injection - Vial': 'Vial', 'Ointment': 'g', 'Powder': 'g',
    'Bolus': 'd', 'Liquid': 'ml', 'Liniment': 'ml',
    'Gel': 'g', 'Vaccine': 'ml', 'Granules': 'g',
    'Tablet Pack': 'pack', 'Bolus Pack': 'pack',
}

# Unit codes in Settings → Appearance:
#   d  = strip/tablet counting (strips × tablets per strip)
#   g  = grams (ointment, powder, gel, …)
#   ml = millilitres (syrup, injection, …)
_STRIP_UNIT_CODES = frozenset({'d', 'tab', 'tabs', 'tablet', 'tablets'})
_LEGACY_STRIP_TYPES = frozenset({'tablet', 'bolus'})


def normalize_measure_unit(unit: str) -> str:
    u = (unit or '').strip().lower()
    if u in ('g', 'gm', 'gram', 'grams'):
        return 'g'
    if u in ('ml', 'milliliter', 'milliliters'):
        return 'ml'
    if u in _STRIP_UNIT_CODES:
        return 'd'
    return (unit or '').strip()


def is_strip_count_unit(unit: str) -> bool:
    return normalize_measure_unit(unit) == 'd'


def is_strip_count_type(med_type: str, unit: str = None) -> bool:
    """True when qty is entered as strips × tablets-per-strip."""
    t = (med_type or '').strip().lower()
    if t in ('tablet pack', 'bolus pack'):
        return False
    if t in _LEGACY_STRIP_TYPES:
        return True
    if unit is None:
        cfg = load_layout()
        unit = cfg.get(f'unit_{med_type}', _SCHEDULE_UNIT_DEFAULTS.get(med_type, ''))
    return is_strip_count_unit(unit)


def get_type_measure_unit(med_type: str) -> str:
    """Pack-size suffix for non-strip types (ml, g, Vial, …). Empty for strip types."""
    cfg = load_layout()
    raw = cfg.get(f'unit_{med_type}', _SCHEDULE_UNIT_DEFAULTS.get(med_type, ''))
    if is_strip_count_unit(raw):
        return ''
    return (raw or '').strip()


def parse_tablets_per_stripe(unit_str) -> int:
    """Read tablets-per-strip from medicines.unit (numeric string like '10')."""
    s = str(unit_str or '').strip()
    if not s or is_strip_count_unit(s):
        return 1
    try:
        v = float(s)
        if v > 0:
            return int(v)
    except (ValueError, TypeError):
        pass
    import re
    nums = re.findall(r'\d+', s)
    return int(nums[0]) if nums else 1

_DEFAULT_SCHEDULES = ['', 'H', 'H1', 'X', 'G', 'K', 'C', 'C1', 'P', 'N', 'M']

_DEFAULT_MED_TYPES = [
    'Tablet', 'Capsule', 'Bolus', 'Syrup', 'Liquid', 'Powder', 'Drops',
    'Injection', 'Injection - Vial', 'Gel', 'Vaccine', 'Ointment', 'Liniment',
    'Granules', 'Tablet Pack', 'Bolus Pack', 'Instruments', 'Others',
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

def default_home_banner_path():
    """Built-in banner shipped with the app."""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'assets', 'home_banner.png')
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'assets', 'home_banner.png',
    )


def get_home_banner_path():
    """Return custom banner path when set, otherwise the built-in image."""
    cfg = load_layout()
    if cfg.get('home_banner_use_default'):
        return default_home_banner_path()
    custom = (cfg.get('home_banner_path') or '').strip()
    if custom and os.path.isfile(custom):
        return custom
    return default_home_banner_path()


def get_home_banner_size():
    """Return (width, height). Height is always derived from image aspect ratio."""
    cfg = load_layout()
    width = int(cfg.get('home_banner_size', _BANNER_DEFAULTS['home_banner_size']) or 1500)
    width = max(200, min(width, 4000))
    return width, 0


def use_default_home_banner():
    return bool(load_layout().get('home_banner_use_default'))


def copy_custom_home_banner(source_path: str) -> str:
    """Copy a user-selected image into the config folder."""
    ext = os.path.splitext(source_path)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'):
        ext = '.png'
    dest = os.path.join(_get_config_dir(), 'home_banner_custom' + ext)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(source_path, dest)
    return dest


def load_layout():
    """Always reads fresh from disk."""
    try:
        path = _get_config_path()
        if os.path.exists(path):
            data = json.loads(open(path).read())
            result = {k: int(data.get(k, v)) for k, v in _DEFAULTS.items()}
            if 'home_banner_size' in data:
                result['home_banner_size'] = int(data.get('home_banner_size') or 1500)
            else:
                result['home_banner_size'] = int(
                    data.get('home_banner_width', _BANNER_DEFAULTS['home_banner_size'])
                )
            result['home_banner_use_default'] = bool(
                data.get('home_banner_use_default', _BANNER_DEFAULTS['home_banner_use_default'])
            )
            result['home_banner_path'] = str(data.get('home_banner_path', '') or '')
            result['column_visibility'] = data.get('column_visibility') or {}
            result['export_column_visibility'] = data.get('export_column_visibility') or {}
            result['quick_access'] = data.get('quick_access') or {}
            # Load units for ALL saved med_types and auto-append any new defaults
            saved_types = list(data.get('med_types', list(_DEFAULT_MED_TYPES)) or [])
            merged_types = list(saved_types)
            for t in _DEFAULT_MED_TYPES:
                if t not in merged_types:
                    merged_types.append(t)
            for t in merged_types:
                result[f'unit_{t}'] = data.get(f'unit_{t}', _SCHEDULE_UNIT_DEFAULTS.get(t, ''))
                result[f'typeqty_{t}'] = data.get(f'typeqty_{t}', _TYPE_QTY_DEFAULTS.get(t, 0))
            result['schedules'] = data.get('schedules', list(_DEFAULT_SCHEDULES))
            result['med_types'] = merged_types
            return result
    except Exception:
        pass
    result = dict(_DEFAULTS)
    result.update(_BANNER_DEFAULTS)
    result['column_visibility'] = {}
    result['export_column_visibility'] = {}
    result['quick_access'] = {}
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


def get_configured_schedules():
    """Non-empty schedule codes from layout settings (H, H1, X, …)."""
    try:
        raw = load_layout().get("schedules", list(_DEFAULT_SCHEDULES))
    except Exception:
        raw = list(_DEFAULT_SCHEDULES)
    seen = set()
    out = []
    for s in raw:
        text = str(s).strip()
        if text and text not in seen:
            seen.add(text)
            out.append(text)
    return out
