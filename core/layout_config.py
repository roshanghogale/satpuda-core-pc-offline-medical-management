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
    # Share of the space Home has beside Quick Actions, 10-100. 0 = never
    # chosen, and then home_banner_size (pixels) still decides, exactly as it
    # did -- see banner_width_pct.
    'home_banner_width_pct': 0,
    # Legacy keys — migrated on load
    'home_banner_width': 1500,
    'home_banner_height': 0,
}

_TYPE_QTY_DEFAULTS = {
    'Tablet': 0, 'Capsule': 0, 'Bolus': 1, 'Syrup': 0, 'Suspension': 0,
    'Liquid': 0, 'Powder': 0, 'Drops': 0, 'Eye Drops': 0, 'Ear Drops': 0,
    'Nasal Drops': 0, 'Injection': 0, 'Injection - Vial': 1, 'Gel': 0,
    'Vaccine': 0, 'Ointment': 0, 'Cream': 0, 'Liniment': 0, 'Granules': 0,
    'Lotion': 0, 'Spray': 0, 'Shampoo': 0, 'Sachet': 0, 'Soap': 0,
    'Inhaler': 0, 'Instrument / Medical Device': 0, 'Feed Supplement': 0,
    'Tablet Pack': 0, 'Bolus Pack': 0, 'Others': 0,
}

_SCHEDULE_UNIT_DEFAULTS = {
    'Tablet': 'd', 'Capsule': 'd', 'Bolus': 'd', 'Syrup': 'ml', 'Suspension': 'ml',
    'Liquid': 'ml', 'Powder': 'g', 'Drops': 'ml', 'Drop': 'ml', 'Eye Drops': 'ml',
    'Ear Drops': 'ml', 'Nasal Drops': 'ml', 'Injection': 'ml',
    'Injection - Vial': 'Vial', 'Gel': 'g', 'Vaccine': 'ml', 'Ointment': 'g',
    'Cream': 'g', 'Liniment': 'ml', 'Granules': 'g', 'Lotion': 'ml',
    'Spray': 'ml', 'Shampoo': 'ml', 'Sachet': 'pack', 'Soap': 'pack',
    'Inhaler': 'pack', 'Instrument / Medical Device': '',
    'Feed Supplement': 'g', 'Tablet Pack': 'pack', 'Bolus Pack': 'pack',
    'Others': '',
}

# Unit codes in Settings → Appearance:
#   d  = strip/tablet counting (strips × tablets per strip)
#   g  = grams (ointment, powder, gel, …)
#   ml = millilitres (syrup, injection, …)
_STRIP_UNIT_CODES = frozenset({'d', 'tab', 'tabs', 'tablet', 'tablets'})
_LEGACY_STRIP_TYPES = frozenset({'tablet', 'bolus', 'capsule'})


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
    if not s:
        return 1
    if is_strip_count_unit(s):
        return 1
    import re
    from core.bill_import_normalize import pack_is_volume_or_weight
    if pack_is_volume_or_weight(s):
        return 1
    m = re.match(r'^1\s*[Xx×*]\s*(\d+)$', s)
    if m:
        return max(1, int(m.group(1)))
    try:
        v = float(s)
        if v > 0:
            return int(v)
    except (ValueError, TypeError):
        pass
    nums = re.findall(r'\d+', s)
    return max(1, int(nums[0])) if nums else 1


def _infer_tps_from_name(name: str) -> int:
    """Infer pack size from names like 'FOO 10 TAB' or '1*10' (not strength like 500MG)."""
    import re
    s = str(name or "").upper()
    if not s:
        return 1
    m = re.search(r'1\s*[Xx×*]\s*(\d{1,3})\b', s)
    if m:
        n = int(m.group(1))
        if 2 <= n <= 100:
            return n
    # Digits immediately before TAB/TABLET/CAP — pack cue, not MG strength.
    m = re.search(r'\b(\d{1,3})\s*(?:TAB|TABLET|CAP|CAPS|CAPSULE)s?\b', s)
    if m:
        n = int(m.group(1))
        if 2 <= n <= 100:
            return n
    return 1


def _sibling_pack_from_catalog(name: str) -> int:
    """If a near-duplicate inventory row has unit>1, reuse that pack size."""
    try:
        from core.sync_prefs import is_online_mode
        if not is_online_mode():
            return 1
        from core.online_catalog import medicine_name_representatives
    except Exception:
        return 1
    import re
    base = re.sub(r'[^A-Z0-9]+', '', str(name or '').upper())
    # Drop trailing TAB/TABLET/MG noise for matching UNIENZYME TAB ↔ UNIENZYME TABLET
    base_core = re.sub(r'(TABLETS?|TABS?|CAPSULES?|CAPS?|MG|MCG)$', '', base)
    if len(base_core) < 4:
        return 1
    best = 1
    skip = str(name or "").strip().upper()
    for m in medicine_name_representatives():
        other = str(m.get("name") or "")
        if not other or other.strip().upper() == skip:
            continue
        other_key = re.sub(r'[^A-Z0-9]+', '', other.upper())
        other_core = re.sub(r'(TABLETS?|TABS?|CAPSULES?|CAPS?|MG|MCG)$', '', other_key)
        if not (other_core.startswith(base_core) or base_core.startswith(other_core)):
            continue
        if len(other_core) < 4:
            continue
        # Require shared prefix so ATARAX 10 does not pick ATARAX 25 pack.
        shorter, longer = sorted((base_core, other_core), key=len)
        if not longer.startswith(shorter):
            continue
        if not is_strip_count_type(m.get("type") or "", m.get("unit")):
            continue
        tps = parse_tablets_per_stripe(m.get("unit") or "1")
        if tps > best:
            best = tps
    return best


def resolve_tablets_per_stripe(unit_str, *, name: str = "", med_type: str = "") -> int:
    """Pack size for strip billing. Fixes unit='1' rows that still carry strip MRP."""
    tps = parse_tablets_per_stripe(unit_str)
    if tps > 1:
        return tps
    if not is_strip_count_type(med_type or "", unit_str):
        return max(1, tps)
    # Prefer sibling inventory pack (UNIENZYME TABLET unit=15) over name guesses.
    sibling = _sibling_pack_from_catalog(name)
    if sibling > 1:
        return sibling
    inferred = _infer_tps_from_name(name)
    if inferred > 1:
        return inferred
    return max(1, tps)

_DEFAULT_SCHEDULES = ['', 'H', 'H1', 'X', 'G', 'K', 'C', 'C1', 'P', 'N', 'M']

_DEFAULT_MED_TYPES = [
    'Bolus', 'Bolus Pack', 'Capsule', 'Cream', 'Drops', 'Ear Drops', 'Eye Drops',
    'Feed Supplement', 'Gel', 'Granules', 'Inhaler', 'Injection', 'Injection - Vial',
    'Instrument / Medical Device', 'Liniment', 'Liquid', 'Lotion', 'Nasal Drops',
    'Others', 'Ointment', 'Powder', 'Sachet', 'Shampoo', 'Soap', 'Spray',
    'Suspension', 'Syrup', 'Tablet', 'Tablet Pack', 'Vaccine',
]

_TYPE_ALIASES = {
    'drop': 'Drops',
    'instruments': 'Instrument / Medical Device',
    'instrument': 'Instrument / Medical Device',
    'medical device': 'Instrument / Medical Device',
}


def sort_med_types(types):
    """Return unique medicine types sorted A–Z (case-insensitive)."""
    seen = set()
    out = []
    for t in types or []:
        text = str(t).strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return sorted(out, key=lambda x: x.lower())


def normalize_med_type_name(name: str) -> str:
    text = (name or '').strip()
    if not text:
        return text
    return _TYPE_ALIASES.get(text.lower(), text)


def normalize_med_type_list(types):
    mapped = []
    for t in types or []:
        n = normalize_med_type_name(t)
        if n:
            mapped.append(n)
    return sort_med_types(mapped)


def get_med_types():
    """Fresh sorted medicine types from layout config (use for all dropdowns)."""
    cfg = load_layout()
    return sort_med_types(cfg.get('med_types', list(_DEFAULT_MED_TYPES)))

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

# ── which shop's banner this is ─────────────────────────────────────────────
# layout_config.txt is ONE file for the whole machine, and home_banner_path was
# ONE value in it. So was home_banner_use_default. Every store on the PC read
# the same two: whichever shop picked a banner last, every other shop showed
# that file -- it is on this disk, so it won, before anything per store was ever
# consulted -- and one shop ticking "use the built-in image" took another shop's
# banner away. The bytes were already per store (core.store_images); only this
# pointer was not, and the pointer is what decides.
#
# The choice is now recorded under the store that made it and read back for the
# store that is active now. The flat keys are still written, so an older build
# reading this file behaves exactly as it does today.
_BANNER_BY_STORE = 'home_banner_by_store'


def _active_store_key():
    try:
        from core.store_manager import get_active_store_key
        return str(get_active_store_key() or '').strip()
    except Exception:
        return ''


def _single_store_install():
    """True when this PC has at most one store folder."""
    try:
        from core.store_manager import get_stores_root
        root = get_stores_root()
        if not os.path.isdir(root):
            return True
        return len([d for d in os.listdir(root)
                    if os.path.isdir(os.path.join(root, d))]) <= 1
    except Exception:
        return False


def _saved_banner_map():
    """What the file on disk already records, per store.

    Read from the file rather than from the dict being saved, so a caller that
    builds its layout dict from scratch (the Tk Appearance tab does) cannot
    quietly drop every other store's banner on its way past.
    """
    try:
        path = _get_config_path()
        if not os.path.exists(path):
            return {}
        with open(path, encoding='utf-8-sig') as fh:
            data = json.loads(fh.read())
        by_store = data.get(_BANNER_BY_STORE)
        return dict(by_store) if isinstance(by_store, dict) else {}
    except Exception:
        return {}


def _banner_choice(data):
    """The banner THIS store chose, out of a file every store on the PC shares."""
    key = _active_store_key()
    by_store = data.get(_BANNER_BY_STORE)
    if not isinstance(by_store, dict):
        by_store = {}
    mine = by_store.get(key) if key else None
    if isinstance(mine, dict):
        return {
            'home_banner_path': str(mine.get('home_banner_path', '') or ''),
            'home_banner_use_default': bool(mine.get('home_banner_use_default', False)),
        }
    flat = {
        'home_banner_path': str(data.get('home_banner_path', '') or ''),
        'home_banner_use_default': bool(
            data.get('home_banner_use_default',
                     _BANNER_DEFAULTS['home_banner_use_default'])
        ),
    }
    if not key:
        # No store to attribute anything to: one shop, one banner, as before.
        return flat
    if by_store or not _single_store_install():
        # An unstamped value was written before the banner had a store, so it
        # can only have belonged to a single-store install -- the same rule
        # pharmacy_profile_io._sidecar_path and store_images._legacy_path
        # already apply to the other unkeyed leftovers. With a second shop on
        # this PC nobody knows whose it is, and guessing is precisely the
        # complaint: this store shows its own picture (store_images keeps the
        # bytes per store) or the built-in one, never the other shop's.
        return {'home_banner_path': '', 'home_banner_use_default': False}
    return flat


def _stamp_banner_owner(data):
    """Record the banner in this dict under the store that is choosing it.

    Every caller does load_layout -> change one thing -> save_layout, and
    load_layout has already answered with THIS store's banner, so writing it
    back under this store's key is a no-op for a save about anything else. What
    it stops is the other direction: a save made by shop B carrying shop A's
    banner path -- which is what the flat key held -- and B showing A's picture
    from then on.
    """
    out = dict(data or {})
    key = _active_store_key()
    if not key:
        return out
    by_store = _saved_banner_map()
    incoming = out.get(_BANNER_BY_STORE)
    if isinstance(incoming, dict):
        by_store.update({str(k): v for k, v in incoming.items()})
    if 'home_banner_path' not in out and 'home_banner_use_default' not in out:
        # A save about something else entirely, from a caller that built its
        # dict without the banner in it. Silence is not "clear my banner".
        out[_BANNER_BY_STORE] = by_store
        return out
    by_store[key] = {
        'home_banner_path': str(out.get('home_banner_path', '') or ''),
        'home_banner_use_default': bool(out.get('home_banner_use_default', False)),
    }
    out[_BANNER_BY_STORE] = by_store
    return out


def _banner_paths_of_other_stores():
    """Banner files another store on this PC is still pointing at."""
    key = _active_store_key()
    out = set()
    for store_key, choice in _saved_banner_map().items():
        if str(store_key) == key or not isinstance(choice, dict):
            continue
        text = str(choice.get('home_banner_path') or '').strip()
        if not text:
            continue
        try:
            out.add(os.path.normcase(os.path.abspath(text)))
        except Exception:
            continue
    return out


def default_home_banner_path():
    """Built-in banner shipped with the app (assets/home_banner.png)."""
    if getattr(sys, 'frozen', False):
        return os.path.join(sys._MEIPASS, 'assets', 'home_banner.png')
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'assets', 'home_banner.png',
    )


def get_home_banner_path():
    """Return the shop's banner when it has one, otherwise the built-in image.

    home_banner_path is an absolute path inside THIS machine's config folder and
    layout_config.txt never leaves this machine, so a banner chosen on the
    counter PC was invisible everywhere else -- and this function fell back to
    the built-in picture without saying a word, which is exactly what "I change
    the banner and nothing happens" looks like. The saved path is now only the
    first place we look; core.store_images keeps the picture per store and
    carries it with the store.

    load_layout answers with the ACTIVE store's choice, so `custom` below can
    only ever be this shop's own file. That is what stops the first branch --
    a path that exists on this disk, which beats everything else -- from
    handing the previous shop's banner to the next one.
    """
    cfg = load_layout()
    if cfg.get('home_banner_use_default'):
        return default_home_banner_path()
    custom = (cfg.get('home_banner_path') or '').strip()
    if custom and os.path.isfile(custom):
        return custom
    try:
        from core.store_images import HOME_BANNER, resolve_path

        found = resolve_path(HOME_BANNER, custom)
        if found and os.path.isfile(found):
            return found
    except Exception:
        pass
    return default_home_banner_path()


def get_home_banner_size():
    """Return (width, height). Height is always derived from image aspect ratio.

    Height stays derived on purpose, and 'home_banner_height' stays 0. Both
    drawing paths scale the picture to the width and keep its proportions -- Tk
    resizes it (ui/shared/home_page.py), the desktop page sets width and leaves
    height auto. A second number that forced a height could only letterbox or
    squash the shop's own picture, so there is one control, and it is the width.
    """
    cfg = load_layout()
    width = int(cfg.get('home_banner_size', _BANNER_DEFAULTS['home_banner_size']) or 1500)
    width = max(200, min(width, 4000))
    return width, 0


# ── banner width as a share of the space it lives in ────────────────────────
# home_banner_size is pixels, and a pixel width bigger than the panel it is drawn
# in means nothing: the banner cannot be wider than its panel. On the owner's
# 1440x900 screen that panel is 1202 px, so 1200 and the 1500 default drew the
# same picture, and so did everything above them -- he changed the number and
# saw nothing. A percentage of that panel has no dead range on any screen: 70
# is always narrower than 80.
#
# Stored under its own key rather than reinterpreting home_banner_size, so a
# value already saved in pixels keeps drawing exactly what it drew (0 here
# means "never chosen") and an older build still reads the pixels it knows.
HOME_BANNER_PCT_MIN = 10
HOME_BANNER_PCT_MAX = 100
_BANNER_PCT_KEY = 'home_banner_width_pct'


def banner_width_pct(value):
    """A stored or typed percentage as 10-100, 0 for "not chosen", None for junk.

    Never raises: load_layout wraps its whole read in one try, and one bad
    value here would have thrown away every other setting in the file.
    """
    if value is None or value == '' or isinstance(value, bool):
        return 0
    try:
        number = int(round(float(str(value).strip())))
    except (TypeError, ValueError):
        return None
    if number <= 0:
        return 0
    return max(HOME_BANNER_PCT_MIN, min(HOME_BANNER_PCT_MAX, number))


def get_home_banner_width_pct():
    """The shop's chosen share of the banner panel, or 0 when it never chose."""
    return int(load_layout().get(_BANNER_PCT_KEY) or 0)


def use_default_home_banner():
    return bool(load_layout().get('home_banner_use_default'))


_BANNER_COPY_PREFIX = 'home_banner_custom_'


def copy_custom_home_banner(source_path: str) -> str:
    """Copy a user-selected image into the config folder.

    Every copy gets its own timestamp, which is why the old one has to be pruned
    by hand afterwards -- see prune_custom_home_banners. Nothing here overwrites
    anything, so nothing here can lose a picture either.
    """
    from datetime import datetime

    ext = os.path.splitext(source_path)[1].lower()
    if ext not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'):
        ext = '.png'
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    dest = os.path.join(_get_config_dir(), f'{_BANNER_COPY_PREFIX}{stamp}{ext}')
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(source_path, dest)
    return dest


def prune_custom_home_banners(keep: str) -> list:
    """Delete the banner copies THIS app made, except the one now in use.

    copy_custom_home_banner stamps every copy with the time, so a shop that
    changed its banner ten times had ten full-size images sitting in the config
    folder and only ever looked at one -- this is the place a stale banner
    actually piles up. They are safe to sweep because they are our copies, not
    the shop's own file (that stays wherever the shop keeps it) and the prefix
    is written by nothing else.

    What is NOT ours to sweep is the copy another store on this PC is still
    pointing at. That used to be impossible -- home_banner_path was one
    machine-wide value -- and it is exactly what changed: the banner is recorded
    per store now, so one shop saving a new banner must not delete the file the
    shop next door still shows.

    Call it AFTER the new banner is saved and pointed at, never before: with no
    `keep` on disk this refuses to delete anything.
    """
    kept = str(keep or '').strip()
    if not kept or not os.path.isfile(kept):
        return []
    folder = _get_config_dir()
    try:
        entries = os.listdir(folder)
    except OSError:
        return []
    kept_norm = os.path.normcase(os.path.abspath(kept))
    in_use_elsewhere = _banner_paths_of_other_stores()
    removed = []
    for name in entries:
        if not name.startswith(_BANNER_COPY_PREFIX):
            continue
        path = os.path.join(folder, name)
        norm = os.path.normcase(os.path.abspath(path))
        if norm == kept_norm or norm in in_use_elsewhere:
            continue
        if not os.path.isfile(path):
            continue
        try:
            os.remove(path)
            removed.append(path)
        except OSError:
            pass
    return removed


def load_layout():
    """Always reads fresh from disk."""
    try:
        path = _get_config_path()
        if os.path.exists(path):
            with open(path, encoding='utf-8-sig') as fh:
                data = json.loads(fh.read())
            result = {k: int(data.get(k, v)) for k, v in _DEFAULTS.items()}
            if 'home_banner_size' in data:
                result['home_banner_size'] = int(data.get('home_banner_size') or 1500)
            else:
                result['home_banner_size'] = int(
                    data.get('home_banner_width', _BANNER_DEFAULTS['home_banner_size'])
                )
            # Carried through here or save_layout(load_layout()) -- which every
            # settings save does -- would silently drop it.
            result[_BANNER_PCT_KEY] = banner_width_pct(data.get(_BANNER_PCT_KEY)) or 0
            # Which picture, for the store that is active now -- not for
            # whichever shop on this PC saved the file last.
            result.update(_banner_choice(data))
            result['column_visibility'] = data.get('column_visibility') or {}
            result['export_column_visibility'] = data.get('export_column_visibility') or {}
            result['quick_access'] = data.get('quick_access') or {}
            result['dashboard_sections'] = data.get('dashboard_sections') or {}
            result['sales_history_cols_v2'] = bool(data.get('sales_history_cols_v2'))
            # Load units for ALL saved med_types and auto-append any new defaults
            saved_types = normalize_med_type_list(
                data.get('med_types', list(_DEFAULT_MED_TYPES)) or [])
            merged_types = list(saved_types)
            # Only when the shop has never saved a list of its own. Merging the
            # built-ins back in on every read is why deleting one did nothing --
            # it reappeared on the next load, and the repair pass below wrote it
            # back to the file for good measure.
            if 'med_types' not in data:
                for t in _DEFAULT_MED_TYPES:
                    if t not in merged_types:
                        merged_types.append(t)
            merged_types = sort_med_types(merged_types)
            for t in merged_types:
                unit_key = f'unit_{t}'
                qty_key = f'typeqty_{t}'
                result[unit_key] = data.get(
                    unit_key, _SCHEDULE_UNIT_DEFAULTS.get(t, ''))
                result[qty_key] = data.get(
                    qty_key, _TYPE_QTY_DEFAULTS.get(t, 0))
            saved_schedules = data.get('schedules', list(_DEFAULT_SCHEDULES)) or []
            merged_schedules = list(saved_schedules)
            if 'schedules' not in data:
                for s in _DEFAULT_SCHEDULES:
                    if s not in merged_schedules:
                        merged_schedules.append(s)
            result['schedules'] = merged_schedules
            result['med_types'] = merged_types
            # Sales margin toggles (Appearance → Sales Margin)
            for key in (
                'billing_show_margin_column',
                'billing_show_total_margin',
                'billing_margin_loss_warning',
            ):
                if key in data:
                    result[key] = bool(data[key])
            return result
    except Exception:
        pass
    result = dict(_DEFAULTS)
    result.update(_BANNER_DEFAULTS)
    result['column_visibility'] = {}
    result['export_column_visibility'] = {}
    result['quick_access'] = {}
    result['dashboard_sections'] = {}
    result['sales_history_cols_v2'] = False
    for k, v in _SCHEDULE_UNIT_DEFAULTS.items():
        result[f'unit_{k}'] = v
    for k, v in _TYPE_QTY_DEFAULTS.items():
        result[f'typeqty_{k}'] = v
    result['schedules'] = list(_DEFAULT_SCHEDULES)
    result['med_types'] = sort_med_types(list(_DEFAULT_MED_TYPES))
    return result

_load = load_layout

def save_layout(data):
    path = _get_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Built BEFORE the file is opened: open(path, 'w') truncates it, and the
    # stamp reads the other stores' banners back out of it. Doing it inside the
    # `with` handed _saved_banner_map an empty file, so every save wiped every
    # other store's entry.
    payload = json.dumps(_stamp_banner_owner(data))
    with open(path, 'w') as f:
        f.write(payload)

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


def get_layout_schedules():
    """Full schedule list for dropdowns — includes blank (non-scheduled), like classic Tk."""
    try:
        raw = load_layout().get("schedules", list(_DEFAULT_SCHEDULES))
    except Exception:
        raw = list(_DEFAULT_SCHEDULES)
    seen = set()
    out = []
    for s in raw:
        text = str(s).strip()
        key = text or "__blank__"
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    if "__blank__" not in seen:
        out.insert(0, "")
    return out


def repair_layout_config_file() -> bool:
    """Persist merged default med types/schedules into layout_config.txt if missing."""
    path = _get_config_path()
    try:
        if os.path.exists(path):
            with open(path, encoding="utf-8-sig") as fh:
                data = json.loads(fh.read())
        else:
            data = {}
    except Exception:
        data = {}
    changed = False

    saved_types = normalize_med_type_list(data.get("med_types", []) or [])
    merged_types = list(saved_types)
    # Same rule as load_layout, and this one matters more: it runs at every
    # start and writes the result back to the file, so a deleted built-in came
    # back permanently.
    if "med_types" not in data:
        for t in _DEFAULT_MED_TYPES:
            if t not in merged_types:
                merged_types.append(t)
                changed = True
    merged_types = sort_med_types(merged_types)
    if merged_types != saved_types:
        data["med_types"] = merged_types
        changed = True

    saved_schedules = list(data.get("schedules", []) or [])
    merged_schedules = list(saved_schedules)
    if "schedules" not in data:
        for s in _DEFAULT_SCHEDULES:
            if s not in merged_schedules:
                merged_schedules.append(s)
                changed = True
    if merged_schedules != saved_schedules:
        data["schedules"] = merged_schedules
        changed = True

    if not changed:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return True
