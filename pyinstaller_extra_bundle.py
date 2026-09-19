"""
Extra PyInstaller datas / hiddenimports for Satpuda voice + bill import.
Bill photos use Gemini AI only — no local EasyOCR/torch bundled.
"""
from __future__ import annotations

import os
import json

def _try_collect_all(package: str):
  """Return (datas, binaries, hiddenimports) or empty lists."""
  try:
    from PyInstaller.utils.hooks import collect_all
    d, b, h = collect_all(package)
    return list(d), list(b), list(h)
  except Exception:
    return [], [], []


def _try_collect_submodules(package: str):
  try:
    from PyInstaller.utils.hooks import collect_submodules
    return list(collect_submodules(package))
  except Exception:
    return []


_SYSTEM_DLL_NAMES = frozenset({
    "kernel32.dll",
    "ntdll.dll",
    "user32.dll",
    "gdi32.dll",
    "advapi32.dll",
    "shell32.dll",
    "ole32.dll",
    "oleaut32.dll",
    "ws2_32.dll",
    "winmm.dll",
})


def filter_system_dlls(binaries, *, keep_ucrt=False):
  """Drop accidental system DLL copies that break voice/audio on some PCs."""
  cleaned = []
  for entry in binaries:
    try:
      name = os.path.basename(str(entry[0])).lower()
    except Exception:
      cleaned.append(entry)
      continue
    if name in _SYSTEM_DLL_NAMES:
      continue
    if name.startswith("api-ms-win-") and not (keep_ucrt and name.startswith("api-ms-win-crt-")):
      continue
    cleaned.append(entry)
  return cleaned


def _optional_build_secrets() -> list:
  """Bundle API keys / Server creds / master DB present at build time (gitignored)."""
  out = []
  for fname in ("gemini_api_key.txt", "server_service_account.json"):
    src = os.path.join("config", fname)
    if not os.path.isfile(src):
      continue
    try:
      if fname.endswith(".json"):
        json.load(open(src, encoding="utf-8"))
      elif not open(src, encoding="utf-8").read().strip():
        continue
    except OSError:
      continue
    except json.JSONDecodeError:
      continue
    out.append((src, "config"))
  master_db = os.path.join("config", "master_medicine.db")
  if os.path.isfile(master_db) and os.path.getsize(master_db) > 1024:
    out.append((master_db, "config"))
  return out


# ── Config seeds (first-run defaults in AppData) ─────────────────────────────
EXTRA_DATAS = [
    ("config/voice_assistant_enabled.txt", "config"),
    ("config/voice_assistant_name.txt", "config"),
    ("config/voice_auto_start_mic.txt", "config"),
    ("config/voice_language.txt", "config"),
    ("config/voice_mic_device.txt", "config"),
    ("config/gemini_bill_enabled.txt", "config"),
    ("config/import_default_schedule.txt", "config"),
    ("config/sync_mode.txt", "config"),
]

# ── App modules (lazy-imported at runtime) ───────────────────────────────────
_EXTRA_HIDDEN = [
    # Voice assistant
    "core.voice",
    "core.voice.assistant",
    "core.voice.assistant_config",
    "core.voice.recognizer",
    "core.voice.tts",
    "core.voice.command_parser",
    "core.voice.command_help",
    "core.voice.command_reference",
    "core.voice.action_executor",
    "core.voice.action_registry",
    "core.voice.page_action_executor",
    "core.voice.page_action_registry",
    "core.voice.navigator",
    "core.voice.screen_registry",
    "core.voice.voice_control",
    "core.voice.voice_dialog",
    "core.voice.dialog_voice",
    "core.voice.voice_export_flow",
    "core.voice.export_ui",
    "core.voice.voice_log",
    "core.voice.voice_sounds",
    "core.voice.voice_config",
    "core.voice.mic_devices",
    "core.voice.win_audio",
    "core.voice.audio_input",
    "core.voice.wake_matcher",
    "core.voice.marathi_normalize",
    "core.voice.marathi_prefixes",
    "core.voice.pronunciation",
    "core.voice.text_normalize",
    "core.voice.tts_phrases",
    "widgets.satpuda_voice_dialog",
    "widgets.satpuda_float_overlay",
    # Bill import — Gemini + PDF parsers (no EasyOCR)
    "core.purchase_import_flow",
    "core.purchase_image_ocr",
    "core.purchase_bill_image_parser",
    "core.gemini_bill_parser",
    "core.gemini_bill_config",
    "core.gemini_rest_client",
    "core.gemini_medicine_metadata",
    "core.bill_import_normalize",
    "core.bill_scan_sections",
    "core.bill_page_utils",
    "core.bill_field_mapper",
    "core.medicine_metadata_resolver",
    "core.export_prefs",
    "core.page_cache",
    "core.background_workers",
    "core.windows_print_dialog",
    "core.bill_output",
    "core.printer_manager",
    "core.ui_tree_loader",
    # Server realtime sync
    "core.server_entity_sync",
    "core.sync_coordinator",
    "core.sync_bootstrap",
    "core.sync_prefs",
    "core.store_link",
    "core.store_manager",
    
    
    "google.api_core",
    "google.api_core.client_options",
    "google.api_core.retry",
    "google.auth.transport.grpc",
    "google.auth.transport.requests",
    "grpc",
    "grpc_status",
    "proto",
    "proto_plus",
    "google.protobuf",
    "ui.settings.settings_tabs.import_tab",
    "ui.shared.import_purchases",
    # PDF parsers
    "pdfplumber",
    "pdfplumber.page",
    "pdfplumber.table",
    "pypdfium2",
    "pypdfium2_raw",
    # Voice + Gemini
    "faster_whisper",
    "ctranslate2",
    "sounddevice",
    "numpy",
    "pyttsx3",
    "win32com",
    "win32com.client",
    "pywintypes",
    "pythoncom",
    "google.generativeai",
    "google.ai.generativelanguage",
    "google.genai",
    "qrcode",
]


def bundle_extras(
    *,
    include_heavy_ocr: bool = False,
    include_whisper: bool = True,
    include_voice: bool = True,
    include_gemini: bool = True,
    include_server_sync: bool = True,
):
  """
  Return (datas, binaries, hiddenimports) to merge into Analysis().
  include_heavy_ocr is ignored — local OCR is not bundled (Gemini only for photos).
  Release / Win7 builds pass include_whisper=False, include_voice=False,
  include_gemini=False, include_server_sync=False.
  """
  datas = list(EXTRA_DATAS)
  if not include_voice:
    datas = [
        d for d in datas
        if not any(
            name in d[0]
            for name in (
                "voice_assistant_enabled.txt",
                "voice_assistant_name.txt",
                "voice_auto_start_mic.txt",
                "voice_language.txt",
                "voice_mic_device.txt",
            )
        )
    ]
  if not include_gemini:
    datas = [
        d for d in datas
        if "gemini_bill_enabled.txt" not in d[0] and "gemini_api_key.txt" not in d[0]
    ]
  if include_gemini:
    datas.extend(_optional_build_secrets())
  else:
    datas.extend(
        item for item in _optional_build_secrets()
        if "gemini_api_key" not in item[0]
    )

  binaries = []
  hidden = list(_EXTRA_HIDDEN)

  _voice_prefixes = (
      "core.voice.",
      "widgets.satpuda_voice_dialog",
      "widgets.satpuda_float_overlay",
  )
  _gemini_names = {
      "core.gemini_bill_parser",
      "core.gemini_bill_config",
      "core.gemini_rest_client",
      "core.gemini_medicine_metadata",
      "google.generativeai",
      "google.ai.generativelanguage",
      "google.genai",
  }

  if not include_voice:
    hidden = [
        h for h in hidden
        if not any(h.startswith(p) for p in _voice_prefixes if p.endswith("."))
        and h not in ("widgets.satpuda_voice_dialog", "widgets.satpuda_float_overlay")
        and h not in ("faster_whisper", "ctranslate2", "sounddevice", "pyttsx3", "win32com", "win32com.client", "pywintypes", "pythoncom")
    ]
  if not include_gemini:
    hidden = [h for h in hidden if h not in _gemini_names and not h.startswith("core.gemini_")]

  _server_sync_names = {
      "core.server_entity_sync",
      "core.sync_coordinator",
      "core.sync_bootstrap",
      "core.sync_prefs",
      "core.store_link",
      "core.server_api",
      "core.server_live",
      "core.server_sync",
  }
  if not include_server_sync:
    hidden = [h for h in hidden if h not in _server_sync_names and not h.startswith("core.server_")]

  _optional_pkgs = ["numpy", "pdfplumber", "qrcode"]
  # Server sync uses stdlib urllib / http.client — no grpc/firestore packages.

  for pkg in _optional_pkgs:
    d, b, h = _try_collect_all(pkg)
    datas.extend(d)
    binaries.extend(b)
    hidden.extend(h)

  if include_gemini:
    for pkg in ("google.generativeai", "google.genai"):
      d, b, h = _try_collect_all(pkg)
      datas.extend(d)
      binaries.extend(b)
      hidden.extend(h)

  if include_whisper and include_voice:
    for pkg in ("faster_whisper", "ctranslate2", "sounddevice"):
      d, b, h = _try_collect_all(pkg)
      datas.extend(d)
      binaries.extend(b)
      hidden.extend(h)
  else:
    for mod in ("faster_whisper", "ctranslate2"):
      if mod in hidden:
        hidden.remove(mod)

  for pkg in ("core", "ui", "widgets", "bill_templates"):
    hidden.extend(_try_collect_submodules(pkg))

  if not include_voice:
    hidden = [
        h for h in hidden
        if not h.startswith("core.voice.")
        and h not in ("widgets.satpuda_voice_dialog", "widgets.satpuda_float_overlay")
        and h not in ("faster_whisper", "ctranslate2", "sounddevice", "pyttsx3", "win32com", "win32com.client", "pywintypes", "pythoncom")
    ]
  if not include_gemini:
    hidden = [
        h for h in hidden
        if h not in _gemini_names
        and not h.startswith("core.gemini_")
        and h not in ("google.generativeai", "google.ai.generativelanguage", "google.genai")
    ]

  seen = set()
  hidden_unique = []
  for name in hidden:
    if name not in seen:
      seen.add(name)
      hidden_unique.append(name)

  return datas, binaries, hidden_unique


# ── What a build must NOT carry ──────────────────────────────────────────────
#
# include_whisper=False / include_voice=False only ever filtered hiddenimports.
# That is not enough, and the shipped engine proved it: PyInstaller's module
# graph follows imports written INSIDE functions, so
#
#   run_desktop_api.py -> core.desktop_api -> core.desktop_settings_service
#     -> core.voice (the package __init__ imports the whole assistant)
#     -> core.voice.assistant -> core.voice.recognizer
#     -> faster_whisper -> av / ctranslate2 / onnxruntime / tokenizers
#                          -> huggingface_hub -> hf_xet
#
# dragged 165 MB of speech runtime into a build that had asked for none of it.
# core.voice itself is pure Python and stays (core/desktop_settings_service.py
# get_voice/save_voice imports it, and core/themed_messagebox.py reaches
# core.voice.tts) -- every import of the wheels below is inside a function and
# already guarded, so a build without them behaves exactly as it does on a PC
# where the voice packages were never pip-installed.
_WHISPER_RUNTIME = (
    "faster_whisper",   # core/voice/recognizer.py:63,133 -- inside a function,
                        # already raises VoiceRecognitionError on ImportError
    "ctranslate2",      # faster_whisper.transcribe
    "av",               # faster_whisper.audio
    "onnxruntime",      # faster_whisper.vad
    "tokenizers",       # faster_whisper.tokenizer
    "huggingface_hub",  # faster_whisper.utils (model download)
    "hf_xet",           # huggingface_hub.file_download
)

_VOICE_RUNTIME = (
    "sounddevice",  # core/voice/audio_input.py:193 -- inside a function
    "pyttsx3",      # core/voice/tts.py:155,329 -- inside a function
)

# camelot and tabula-py are the 2nd/3rd PDF-invoice table parsers behind
# pdfplumber (core/purchase_importer.py:254 / 266). BOTH import pandas at
# module scope, and pandas has been in this spec's excludes since it was
# written -- so in every frozen build `import camelot` has already been raising
# ModuleNotFoundError, caught by `except ImportError` in parse_purchase_pdf.
# Naming camelot here changes nothing a shop can see; it only stops
# camelot.core from dragging in opencv (111 MB) for a parser the build cannot
# run. cv2 is named too so that a future dependency cannot quietly put opencv
# back, and tabula for symmetry -- it is the same pandas-dead path.
#
# This has its OWN flag rather than riding on include_heavy_ocr. The two are
# unrelated: heavy OCR is EasyOCR/torch for bill PHOTOS, while these are PDF
# table parsers. Hanging them off the OCR flag meant that turning local OCR
# back on some day would silently return 111 MB of opencv for a parser that
# still could not run, because pandas would still be excluded.
_DEAD_PDF_TABLE_PARSERS = (
    "camelot",
    "cv2",
    "tabula",
)


def bundle_excludes(
    *,
    include_heavy_ocr: bool = False,
    include_whisper: bool = True,
    include_voice: bool = True,
    include_pdf_table_parsers: bool = False,
):
  """Third-party packages a build has already said it does not want.

  Returns names for Analysis(excludes=...). Excludes are the only lever that
  works here: these packages are reached through real (guarded) function-level
  imports, so dropping them from hiddenimports leaves them in the graph.

  include_heavy_ocr is accepted so a spec can pass its whole feature dict, but
  it does not decide anything here -- local OCR is not bundled at all.
  """
  out = []
  if not include_whisper:
    out.extend(_WHISPER_RUNTIME)
  if not include_voice:
    out.extend(_VOICE_RUNTIME)
  if not include_pdf_table_parsers:
    out.extend(_DEAD_PDF_TABLE_PARSERS)
  seen = set()
  unique = []
  for name in out:
    if name not in seen:
      seen.add(name)
      unique.append(name)
  return unique


# ── Google API discovery documents ───────────────────────────────────────────
#
# hook-googleapiclient.model.py does collect_data_files("googleapiclient.
# discovery_cache"), which bundles the discovery document of EVERY Google API:
# 600 files, 100.3 MB in the Win10 engine. This app builds exactly one service,
# core/backup_manager.py:563
#     build('drive', 'v3', http=http, cache_discovery=False)
# and because no discoveryServiceUrl is passed, googleapiclient.discovery.build
# resolves static_discovery=True and reads
# googleapiclient/discovery_cache/documents/drive.v3.json off disk. That file
# must stay -- without it Drive backup dies with UnknownApiNameOrVersion. The
# other 598 are dead weight.
DRIVE_DISCOVERY_DOCS = ("drive.v3.json", "drive.v2.json")


def prune_discovery_documents(datas, keep=DRIVE_DISCOVERY_DOCS):
  """Drop every Google API discovery document except Drive's."""
  marker = "googleapiclient/discovery_cache/documents/"
  kept = []
  dropped = 0
  for entry in datas:
    dest = str(entry[0]).replace("\\", "/")
    if marker in dest and os.path.basename(dest) not in keep:
      dropped += 1
      continue
    kept.append(entry)
  if dropped:
    print(f"[spec] dropped {dropped} unused Google API discovery documents "
          f"(kept {', '.join(keep)})")
  return kept
