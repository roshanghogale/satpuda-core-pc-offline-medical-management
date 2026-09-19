"""Voice control for open dialogs and startup alerts."""
from __future__ import annotations

import re

from core.voice.voice_log import voice_log


def _normalize(text: str) -> str:
    return " ".join((text or "").lower().split())


def _is_close_phrase(text: str) -> bool:
    t = _normalize(text)
    return t in (
        "close", "close dialog", "close window", "dismiss", "go back",
        "close popup", "close alert", "close message", "escape",
    ) or t.startswith("close ")


def _is_cancel_phrase(text: str) -> bool:
    t = _normalize(text)
    return t in ("cancel", "cancel dialog", "never mind", "no thanks", "abort", "no")


def _is_cancel_all_phrase(text: str) -> bool:
    t = _normalize(text)
    return t in (
        "cancel all", "cancel all alerts", "close all alerts",
        "dismiss all", "skip all alerts", "close all dialogs",
    )


def _phrase_variants(text: str) -> list[str]:
    from core.voice.command_parser import _strip_wake_word
    from core.voice.text_normalize import normalize_transcript

    raw = (text or "").strip()
    recognized = normalize_transcript(raw) or raw
    variants = []
    for item in (raw, recognized):
        n = _normalize(item)
        if n and n not in variants:
            variants.append(n)
    remainder = _strip_wake_word(recognized)
    if remainder:
        n = _normalize(remainder)
        if n and n not in variants:
            variants.append(n)
    return variants


def _parse_schedule_names(text: str, schedules: list) -> list[str]:
    t = _normalize(text)
    picked = []
    for sch in schedules:
        if re.search(rf"\b{re.escape(str(sch).lower())}\b", t):
            picked.append(sch)
    return picked


def _apply_schedule_selections(ctx: dict, phrase: str) -> list[str]:
    mode = ctx["mode"]
    chk_vars = ctx["chk_vars"]
    schedules = ctx["schedules"]

    if re.search(r"\b(all schedules|everything)\b", phrase):
        mode.set("all")
        return ["All Schedules"]

    if re.search(r"\bnon[- ]?scheduled\b", phrase):
        mode.set("non_scheduled")
        return ["Non-Scheduled"]

    if re.search(r"\b(select all|show all schedules|all listed|check all)\b", phrase):
        ctx["on_select_all"]()
        return ["All listed schedules checked"]

    if re.search(r"\bclear checks?\b", phrase) or phrase.strip() == "clear":
        ctx["on_clear"]()
        return ["Checks cleared"]

    picked = _parse_schedule_names(phrase, schedules)
    if picked:
        mode.set("selected")
        for name in picked:
            var = chk_vars.get(name)
            if var is not None:
                var.set(True)
        return picked
    return []


def _try_schedule_dialog_voice(dlg, phrase: str) -> dict | None:
    ctx = getattr(dlg, "_satpuda_voice_ctx", None)
    if not ctx:
        return None

    if re.search(r"\b(show schedules|list schedules|what schedules)\b", phrase):
        names = ", ".join(ctx["schedules"]) or "none configured"
        return {
            "action_id": "schedule_list",
            "label": "Schedules",
            "message": f"Schedules: {names}.",
        }

    if re.search(r"\b(export report|save report|run report)\b", phrase):
        changed = _apply_schedule_selections(ctx, phrase)
        voice_log(f"Voice: schedule export ({changed})")
        on_export = ctx["on_export"]
        dlg.after(0, on_export)
        return {
            "action_id": "schedule_export",
            "label": "Export Report",
            "message": "Exporting schedule report.",
        }

    if re.search(r"\b(print report|print schedule)\b", phrase):
        changed = _apply_schedule_selections(ctx, phrase)
        voice_log(f"Voice: schedule print ({changed})")
        on_print = ctx.get("on_print") or ctx["on_export"]
        dlg.after(0, on_print)
        return {
            "action_id": "schedule_print",
            "label": "Print",
            "message": "Printing schedule report.",
        }

    changed = _apply_schedule_selections(ctx, phrase)
    if changed:
        voice_log(f"Voice: schedule selection {changed}")
        if len(changed) == 1 and changed[0] in ("All Schedules", "Non-Scheduled"):
            return {
                "action_id": "schedule_select",
                "label": "Schedule",
                "message": f"{changed[0]} selected.",
            }
        return {
            "action_id": "schedule_select",
            "label": "Schedule",
            "message": f"Selected {', '.join(changed)}.",
        }
    return None


def _score_export_option(phrase: str, label: str) -> int:
    pl = phrase.lower()
    ll = label.lower()
    if ll in pl:
        return len(ll) + 10
    if pl in ll:
        return len(pl) + 5
    score = 0
    for token in re.findall(r"[a-z0-9]+", ll):
        if len(token) >= 3 and token in pl:
            score += len(token)
    return score


def _try_export_option_dialog_voice(dlg, phrase: str) -> dict | None:
    ctx = getattr(dlg, "_satpuda_voice_ctx", None)
    if not ctx:
        return None

    options = ctx.get("options") or []
    if not options:
        return None

    best_idx = -1
    best_score = 0
    for idx, (label, _) in enumerate(options):
        score = _score_export_option(phrase, label)
        if score > best_score:
            best_score = score
            best_idx = idx

    if best_idx < 0 or best_score < 4:
        if re.search(r"\b(first|one|1st)\b", phrase):
            best_idx = 0
        elif re.search(r"\b(second|two|2nd)\b", phrase) and len(options) > 1:
            best_idx = 1
        elif re.search(r"\b(third|three|3rd)\b", phrase) and len(options) > 2:
            best_idx = 2
        else:
            return None

    label, fn = options[best_idx]
    lb = ctx.get("lb")
    if lb is not None:
        try:
            lb.selection_clear(0, "end")
            lb.selection_set(best_idx)
            lb.activate(best_idx)
        except Exception:
            pass

    try:
        dlg.grab_release()
    except Exception:
        pass
    try:
        dlg.destroy()
    except Exception:
        pass

    voice_log(f'Voice: export option "{label}"')
    try:
        from core.voice.voice_dialog import arm_voice_dialog_hints
        arm_voice_dialog_hints()
        # Never run export/filedialog on the voice listen thread — it blocks mic.
        root = getattr(dlg, "master", None) or dlg
        try:
            root = root.winfo_toplevel()
        except Exception:
            pass
        root.after(0, fn)
    except Exception as exc:
        voice_log(f"Export option failed: {exc}", level="error")
        return {
            "action_id": "export_option",
            "label": label,
            "message": f"Could not open {label}.",
        }

    return {
        "action_id": "export_option",
        "label": label,
        "message": f"Opening {label}.",
    }


def _try_export_format_dialog_voice(dlg, phrase: str) -> dict | None:
    ctx = getattr(dlg, "_satpuda_voice_ctx", None)
    if not ctx:
        return None
    t = phrase
    fmt_var = ctx.get("fmt_var")
    if fmt_var is not None:
        if re.search(r"\b(excel|xlsx|spreadsheet)\b", t):
            fmt_var.set("xlsx")
            return {
                "action_id": "export_format",
                "label": "Excel",
                "message": "Excel selected.",
            }
        if re.search(r"\b(csv|c s v)\b", t):
            fmt_var.set("csv")
            return {
                "action_id": "export_format",
                "label": "CSV",
                "message": "CSV selected.",
            }
        if re.search(r"\b(pdf|p d f|html)\b", t):
            fmt_var.set("pdf")
            return {
                "action_id": "export_format",
                "label": "PDF",
                "message": "PDF selected.",
            }
    if re.search(r"\b(export|save|confirm|ok|done)\b", t):
        fn = ctx.get("on_export")
        if callable(fn):
            dlg.after(0, fn)
            return {
                "action_id": "export_format_go",
                "label": "Export",
                "message": "Exporting.",
            }
    if re.search(r"\b(cancel|close|abort)\b", t):
        fn = ctx.get("on_cancel")
        if callable(fn):
            dlg.after(0, fn)
            return {
                "action_id": "export_format_cancel",
                "label": "Cancel",
                "message": "Export cancelled.",
            }
    return None


def _try_print_all_period_voice(dlg, phrase: str) -> dict | None:
    ctx = getattr(dlg, "_satpuda_voice_ctx", None)
    if not ctx:
        return None
    t = phrase
    paper_var = ctx.get("paper_var")
    if paper_var is not None:
        for size in ("a4", "a5", "a6"):
            if re.search(rf"\b{size}\b", t):
                try:
                    paper_var.set(size.upper())
                except Exception:
                    pass
                return {
                    "action_id": "print_all_paper",
                    "label": size.upper(),
                    "message": f"Paper set to {size.upper()}.",
                }
    if re.search(r"\b(select bills?|choose bills?|next|continue|print)\b", t):
        fn = ctx.get("on_select_bills")
        if callable(fn):
            dlg.after(0, fn)
            return {
                "action_id": "print_all_select_bills",
                "label": "Select Bills",
                "message": "Opening bill selection.",
            }
    if re.search(r"\b(cancel|close|abort)\b", t):
        fn = ctx.get("on_cancel")
        if callable(fn):
            dlg.after(0, fn)
            return {
                "action_id": "print_all_cancel",
                "label": "Cancel",
                "message": "Print All cancelled.",
            }
    return None


def _try_print_all_select_voice(dlg, phrase: str) -> dict | None:
    ctx = getattr(dlg, "_satpuda_voice_ctx", None)
    if not ctx:
        return None
    t = phrase
    if re.search(r"\b(select all|check all|all bills)\b", t):
        fn = ctx.get("on_select_all")
        if callable(fn):
            dlg.after(0, fn)
            return {
                "action_id": "print_all_select_all",
                "label": "Select All",
                "message": "All bills selected.",
            }
    if re.search(r"\b(clear all|uncheck all|deselect all)\b", t):
        fn = ctx.get("on_clear")
        if callable(fn):
            dlg.after(0, fn)
            return {
                "action_id": "print_all_clear",
                "label": "Clear",
                "message": "Selection cleared.",
            }
    if re.search(r"\b(print selected|print|confirm|ok|done)\b", t):
        fn = ctx.get("on_print")
        if callable(fn):
            dlg.after(0, fn)
            return {
                "action_id": "print_all_print",
                "label": "Print",
                "message": "Printing selected bills.",
            }
    if re.search(r"\b(cancel|close|abort)\b", t):
        fn = ctx.get("on_cancel")
        if callable(fn):
            dlg.after(0, fn)
            return {
                "action_id": "print_all_select_cancel",
                "label": "Cancel",
                "message": "Selection cancelled.",
            }
    return None


def _try_messagebox_voice(dlg, phrase: str) -> dict | None:
    ctx = getattr(dlg, "_satpuda_voice_ctx", None)
    if not ctx:
        return None
    buttons = ctx.get("buttons") or []
    t = phrase
    for label, fn in buttons:
        lab = str(label or "").strip().lower()
        if not lab or not callable(fn):
            continue
        if lab in t or re.search(rf"\b{re.escape(lab)}\b", t):
            dlg.after(0, fn)
            return {
                "action_id": "messagebox",
                "label": label,
                "message": f"{label}.",
            }
    if re.search(r"\b(yes|yeah|yep|ok|okay|confirm|sure)\b", t):
        for label, fn in buttons:
            if str(label).strip().lower() in ("yes", "ok", "okay"):
                dlg.after(0, fn)
                return {"action_id": "messagebox", "label": label, "message": f"{label}."}
    if re.search(r"\b(no|cancel|nope)\b", t):
        for label, fn in buttons:
            if str(label).strip().lower() in ("no", "cancel"):
                dlg.after(0, fn)
                return {"action_id": "messagebox", "label": label, "message": f"{label}."}
    return None


def _try_specialized_dialog_voice(dlg, variants: list[str]) -> dict | None:
    kind = getattr(dlg, "_satpuda_voice_kind", None)
    if not kind:
        return None
    for phrase in variants:
        if kind == "schedule_report":
            hit = _try_schedule_dialog_voice(dlg, phrase)
        elif kind == "export_option":
            hit = _try_export_option_dialog_voice(dlg, phrase)
        elif kind == "export_format":
            hit = _try_export_format_dialog_voice(dlg, phrase)
        elif kind == "print_all_period":
            hit = _try_print_all_period_voice(dlg, phrase)
        elif kind == "print_all_select":
            hit = _try_print_all_select_voice(dlg, phrase)
        elif kind == "messagebox":
            hit = _try_messagebox_voice(dlg, phrase)
        else:
            hit = None
        if hit is not None:
            return hit
    return None


def try_dialog_voice(app, text: str, *, allow_without_wake: bool = False) -> dict | None:
    """
    Handle voice while a modal is open (no wake word needed).
    Without an open dialog, returns None — Satpuda is required for all other commands.
    """
    if not allow_without_wake:
        return None

    from core.dialog_escape import active_dialog, close_active_dialog

    variants = _phrase_variants(text)
    if not variants:
        return None

    dlg = active_dialog(app.root)
    if dlg is None:
        return None

    if any(_is_cancel_all_phrase(v) for v in variants):
        from core.startup_alerts import voice_cancel_all_alerts
        if voice_cancel_all_alerts():
            voice_log("Voice: cancel all startup alerts")
            return {"action_id": "cancel_all_alerts", "label": "Cancel All", "message": "All alerts cancelled."}
        closed = 0
        root = app.root
        for _ in range(8):
            if not close_active_dialog(root):
                break
            closed += 1
        if closed:
            voice_log(f"Voice: closed {closed} dialog(s)")
            return {
                "action_id": "cancel_all_alerts",
                "label": "Close All",
                "message": f"Closed {closed} dialog{'s' if closed != 1 else ''}.",
            }
        return None

    hit = _try_specialized_dialog_voice(dlg, variants)
    if hit is not None:
        return hit

    for phrase in variants:
        if _is_close_phrase(phrase) or _is_cancel_phrase(phrase):
            if close_active_dialog(app.root):
                voice_log(f'Voice: closed dialog ("{text}")')
                return {"action_id": "close_dialog", "label": "Close", "message": "Dialog closed."}
            break
    return None
