"""Floating Tk control panel for demo-only observation collection."""
from __future__ import annotations

import argparse
import json
import os
import queue
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import ImageTk

from collection_audit import safe_error_code
from collector_cli import CollectorConfig, load_config, run_collection
from observer_session import DesktopObserverSession
from observer_store import parse_entry_time
from screen_observer import (
    capture_full_desktop,
    enable_dpi_awareness,
    estimate_chart_region,
    numeric_candidate_boxes,
    read_ocr_words,
)
from visual_perception import create_visual_backend


class CollectorSession:
    """Own one background run of the existing collector at a time."""

    def __init__(self, config: CollectorConfig):
        self.config = config
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.stop_event = threading.Event()
        self._worker: threading.Thread | None = None

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def start(self) -> bool:
        if self.is_running:
            return False
        self.stop_event.clear()
        self._worker = threading.Thread(
            target=self._run, name="trading-copilot-collector", daemon=True)
        self._worker.start()
        return True

    def stop(self) -> None:
        self.stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._worker is not None:
            self._worker.join(timeout)

    def _run(self) -> None:
        try:
            run_collection(
                self.config, stop_event=self.stop_event,
                output=lambda line: self.events.put(("output", line)))
        except Exception as exc:
            self.events.put(("failure", safe_error_code(str(exc)) or "ERROR"))
        finally:
            self.events.put(("finished", ""))


class CollectorPanel:
    def __init__(self, root: tk.Tk, session: DesktopObserverSession, *,
                 config: CollectorConfig | None = None):
        self.root = root
        self.session = session
        self.config = config
        self.started_at: float | None = None
        self.elapsed_final = 0.0
        self.closing = False
        self.status = tk.StringVar(value="READY")
        self.elapsed = tk.StringVar(value="00:00:00")
        self.capture_count = tk.StringVar(value="0")
        self.market_count = tk.StringVar(value="0")
        self.non_market_count = tk.StringVar(value="0")
        self.valid_price_count = tk.StringVar(value="0")
        self.error_count = tk.StringVar(value="0")
        self.last_analysis = tk.StringVar(value="No frames analyzed")
        self.observation_quality = tk.StringVar(value="UNKNOWN")
        self.last_market_event = tk.StringVar(value="No market events")
        self.visual_model_status = tk.StringVar(value="DISABLED_LOCAL_OCR_FALLBACK")
        self.ocr_status = tk.StringVar(value="IDLE")
        self.price_candidate = tk.StringVar(value="None")
        self.price_rejection = tk.StringVar(value="None")
        self.inference_latency = tk.StringVar(value="0 ms")
        self.latest_error = tk.StringVar(value="None")
        self.summary = tk.StringVar(value="No session report yet")
        self.paper_status = tk.StringVar(
            value="INSUFFICIENT VERIFIED MARKET DATA")
        self.model_readiness = tk.StringVar(value="NOT_READY")
        self.always_on_top = tk.BooleanVar(value=True)
        self.full_desktop_consent = tk.BooleanVar(value=False)
        self.interval = tk.StringVar(value="5")
        self.exclusions = tk.StringVar(value="")
        self.report_path: str | None = None
        self.trade_window: tk.Toplevel | None = None

        root.title("Trading Copilot Observer")
        root.resizable(False, True)
        root.attributes("-topmost", True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.configure(padx=14, pady=12)

        tk.Label(root, text="DESKTOP MARKET OBSERVER", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.status_label = tk.Label(
            root, textvariable=self.status, font=("Segoe UI", 12, "bold"))
        self.status_label.grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(8, 2))
        tk.Label(root, text="Elapsed").grid(row=2, column=0, sticky="w")
        tk.Label(root, textvariable=self.elapsed).grid(row=2, column=1, sticky="e")
        for row, label, value in (
                (3, "Total captures", self.capture_count),
                (4, "Market observations", self.market_count),
                (5, "Non-market frames", self.non_market_count),
                (6, "Capture/OCR errors", self.error_count),
                (7, "Validated prices", self.valid_price_count),
                (8, "Observation quality", self.observation_quality),
                (9, "Last market event", self.last_market_event),
                (10, "Visual model", self.visual_model_status),
                (11, "OCR status", self.ocr_status),
                (12, "Price candidate", self.price_candidate),
                (13, "Price rejection", self.price_rejection),
                (14, "Inference latency", self.inference_latency)):
            tk.Label(root, text=label).grid(row=row, column=0, sticky="w")
            tk.Label(root, textvariable=value).grid(row=row, column=1, sticky="e")
        tk.Label(root, text="Last analysis").grid(row=15, column=0, sticky="nw")
        tk.Label(root, textvariable=self.last_analysis, wraplength=240,
                 justify="right").grid(row=15, column=1, sticky="e")
        tk.Label(root, text="Latest error").grid(row=16, column=0, sticky="nw")
        tk.Label(root, textvariable=self.latest_error, wraplength=230,
                 justify="right").grid(row=16, column=1, sticky="e")
        tk.Label(root, textvariable=self.summary, wraplength=270,
                 justify="left").grid(row=17, column=0, columnspan=2,
                                       sticky="w", pady=(6, 2))
        tk.Label(root, text="Model readiness").grid(row=18, column=0, sticky="w")
        tk.Label(root, textvariable=self.model_readiness).grid(
            row=18, column=1, sticky="e")
        tk.Label(root, text="Paper-trade suggestions:").grid(row=19, column=0, sticky="w")
        tk.Label(root, textvariable=self.paper_status, wraplength=200,
                 justify="right").grid(row=19, column=1, sticky="e")

        controls = tk.Frame(root)
        controls.grid(row=20, column=0, columnspan=2, sticky="ew", pady=(8, 4))
        tk.Label(controls, text="Interval").pack(side="left")
        self.interval_box = ttk.Combobox(
            controls, textvariable=self.interval, values=("2", "3", "5"),
            width=3, state="readonly")
        self.interval_box.pack(side="left", padx=(4, 10))
        self.start_button = tk.Button(
            controls, text="START", width=12, command=self.start)
        self.start_button.pack(side="left", padx=(0, 5))
        self.stop_button = tk.Button(
            controls, text="STOP", width=12, command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=(0, 5))
        self.pause_button = tk.Button(
            controls, text="PAUSE", width=9, command=self.pause, state="disabled")
        self.pause_button.pack(side="left")
        actions = tk.Frame(root)
        actions.grid(row=21, column=0, columnspan=2, sticky="ew")
        self.resume_button = tk.Button(
            actions, text="RESUME", command=self.resume, state="disabled")
        self.resume_button.pack(side="left", padx=(0, 5))
        self.trade_button = tk.Button(
            actions, text="RECORD DEMO TRADE", command=self.open_trade_editor)
        self.trade_button.pack(side="left", padx=(0, 5))
        self.report_button = tk.Button(
            actions, text="OPEN SESSION REPORT", command=self.open_report,
            state="disabled")
        self.report_button.pack(side="left")
        tk.Button(root, text="Save debug snapshot...",
                  command=self.save_debug_snapshot).grid(
                      row=22, column=0, columnspan=2, sticky="w", pady=(5, 0))
        tk.Label(root, text="Exclude visible apps (process names, comma-separated)").grid(
            row=23, column=0, columnspan=2, sticky="w", pady=(7, 0))
        tk.Entry(root, textvariable=self.exclusions, width=42).grid(
            row=24, column=0, columnspan=2, sticky="ew")
        tk.Checkbutton(
            root, text="Always on top", variable=self.always_on_top,
            command=self._toggle_topmost).grid(row=25, column=0, columnspan=2,
                                                sticky="w")
        tk.Checkbutton(
            root, text="I explicitly consent to full-desktop capture",
            variable=self.full_desktop_consent).grid(
                row=26, column=0, columnspan=2, sticky="w")
        tk.Button(root, text="Visual Debug / Confirm Price Region",
                  command=self.visual_debug_mode).grid(
                      row=27, column=0, columnspan=2, sticky="w", pady=(4, 0))
        self._poll()

    def start(self) -> None:
        if not self.full_desktop_consent.get():
            messagebox.showwarning(
                "Full-desktop capture", "Opt in before starting desktop observation.",
                parent=self.root)
            return
        if self.session.is_running:
            return
        try:
            excluded = tuple(item.strip() for item in self.exclusions.get().split(",")
                             if item.strip())
            started = self.session.start(
                interval_seconds=int(self.interval.get()),
                excluded_applications=excluded)
        except (OSError, RuntimeError, ValueError) as exc:
            self.latest_error.set(str(exc))
            self.status.set("ERROR")
            return
        if not started:
            return
        self.started_at = time.monotonic()
        self.elapsed_final = 0.0
        self.report_path = None
        self.report_button.configure(state="disabled")
        self.capture_count.set("0")
        self.market_count.set("0")
        self.non_market_count.set("0")
        self.valid_price_count.set("0")
        self.error_count.set("0")
        self.last_analysis.set("Waiting for first frame")
        self.latest_error.set("None")
        self.summary.set("Session active; report is generated on Stop")
        self.status.set("CAPTURING")
        self._set_controls()

    def stop(self) -> None:
        if self.session.is_running:
            self.session.stop()
            self.status.set("STOPPING")
        self._set_controls()

    def pause(self) -> None:
        self.session.pause()
        self.status.set("PAUSED")
        self._set_controls()

    def resume(self) -> None:
        self.session.resume()
        self.status.set("CAPTURING")
        self._set_controls()

    def close(self) -> None:
        self.closing = True
        self.stop()
        if not self.session.is_running:
            self.root.destroy()

    def _toggle_topmost(self) -> None:
        self.root.attributes("-topmost", self.always_on_top.get())

    def _set_controls(self) -> None:
        running = self.session.is_running
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")
        paused = self.session.is_paused
        self.pause_button.configure(
            state="normal" if running and not paused else "disabled")
        self.resume_button.configure(
            state="normal" if running and paused else "disabled")

    def _poll(self) -> None:
        while True:
            try:
                kind, value = self.session.events.get_nowait()
            except queue.Empty:
                break
            if kind == "output":
                self._handle_output(value)
            elif kind == "frame":
                self._handle_frame(value)
            elif kind == "status":
                self.status.set(str(value))
            elif kind == "snapshot":
                self.latest_error.set(str(value))
            elif kind == "failure":
                self.latest_error.set(str(value))
                self.status.set("ERROR")
            elif kind == "finished":
                self.report_path = str(value["report_path"])
                self.report_button.configure(state="normal")
                self.summary.set(
                    f"Stopped: {value['counts']['total']} frames; report ready")
                self.model_readiness.set(value.get("model_readiness", "NOT_READY"))
            elif kind == "worker_exited":
                if self.started_at is not None:
                    self.elapsed_final = time.monotonic() - self.started_at
                    self.started_at = None
                if self.status.get() not in {"ERROR"}:
                    self.status.set("STOPPED")
                self._set_controls()
                if self.closing and not self.session.is_running:
                    self.root.destroy()
                    return
        elapsed = (time.monotonic() - self.started_at
                   if self.started_at is not None else self.elapsed_final)
        seconds = max(0, int(elapsed))
        self.elapsed.set(f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}")
        self._set_controls()
        if self.closing and not self.session.is_running:
            self.root.destroy()
            return
        self.root.after(100, self._poll)

    def _handle_frame(self, payload: dict) -> None:
        counts = payload["counts"]
        self.capture_count.set(str(counts["total"]))
        self.market_count.set(str(counts["market"]))
        self.non_market_count.set(str(counts["non_market"]))
        self.valid_price_count.set(str(counts["valid_prices"]))
        self.error_count.set(str(counts["errors"]))
        self.last_analysis.set(str(payload["result"]))
        record = payload["record"]
        fields = record.extracted_fields
        vision = fields.get("visual_model", {})
        self.visual_model_status.set(str(vision.get("status", "DISABLED_LOCAL_OCR_FALLBACK")))
        self.ocr_status.set(str(fields.get("ocr_engine_status", record.extraction_status)))
        candidate = fields.get("candidate_price")
        self.price_candidate.set(str(candidate) if candidate is not None else "None")
        self.price_rejection.set(str(fields.get("price_rejection_reason") or "None"))
        self.inference_latency.set(f"{vision.get('latency_ms', 0)} ms")
        flags = record.extracted_fields.get("quality_flags", [])
        self.observation_quality.set(
            " / ".join(flags) if flags else record.extraction_status)
        events = record.extracted_fields.get("temporal_events", [])
        if events:
            self.last_market_event.set(str(events[-1].get("type", "MARKET_EVENT")))
        self.latest_error.set(record.error_reason or "None")
        if self.status.get() != "PAUSED":
            self.status.set("CAPTURING")

    def _handle_output(self, _line: str) -> None:
        return

    def open_report(self) -> None:
        if self.report_path and os.path.isfile(self.report_path):
            webbrowser.open(Path(self.report_path).resolve().as_uri())

    def open_trade_editor(self) -> None:
        if not self.session.session_id:
            messagebox.showinfo("Demo trade", "Start an observer session first.",
                                parent=self.root)
            return
        if self.trade_window is not None and self.trade_window.winfo_exists():
            self.trade_window.lift()
            return
        self.trade_window = tk.Toplevel(self.root)
        window = self.trade_window
        window.title("Record / Correct Demo Trade")
        window.transient(self.root)
        window.grab_set()
        fields = {}
        defaults = {
            "Asset": "", "Direction (UP/DOWN)": "UP",
            "Entry time (ISO local)": datetime.now().astimezone().isoformat(timespec="seconds"),
            "Duration seconds": "60", "Stake (demo only)": "1",
            "Entry price (optional)": "", "Outcome": "OPEN",
        }
        for row, (label, default) in enumerate(defaults.items()):
            tk.Label(window, text=label).grid(row=row, column=0, sticky="w", padx=8, pady=3)
            variable = tk.StringVar(value=default)
            fields[label] = variable
            tk.Entry(window, textvariable=variable, width=34).grid(
                row=row, column=1, padx=8, pady=3)
        trade_list = tk.Listbox(window, width=55, height=5)
        trade_list.grid(row=len(defaults), column=0, columnspan=2,
                        padx=8, pady=5, sticky="ew")
        trades = self.session.trades()
        for trade in trades:
            trade_list.insert("end", f"#{trade['trade_id']} {trade['asset']} "
                              f"{trade['direction']} {trade['outcome']}")
        selected_trade: list[int | None] = [None]

        def select_trade(_event=None):
            selection = trade_list.curselection()
            if not selection:
                selected_trade[0] = None
                return
            trade = trades[selection[0]]
            selected_trade[0] = int(trade["trade_id"])
            values = {
                "Asset": trade["asset"],
                "Direction (UP/DOWN)": trade["direction"],
                "Entry time (ISO local)": datetime.fromtimestamp(
                    trade["entry_time_ms"] / 1000).astimezone().isoformat(timespec="seconds"),
                "Duration seconds": str(trade["duration_seconds"]),
                "Stake (demo only)": str(trade["stake"]),
                "Entry price (optional)": "" if trade["entry_price"] is None
                else str(trade["entry_price"]),
                "Outcome": trade["outcome"],
            }
            for key, value in values.items():
                fields[key].set(value)

        trade_list.bind("<<ListboxSelect>>", select_trade)

        def save_trade():
            try:
                values = {
                    "asset": fields["Asset"].get().strip(),
                    "direction": fields["Direction (UP/DOWN)"].get().strip().upper(),
                    "entry_time_ms": parse_entry_time(
                        fields["Entry time (ISO local)"].get()),
                    "duration_seconds": int(fields["Duration seconds"].get()),
                    "stake": float(fields["Stake (demo only)"].get()),
                    "entry_price": (float(fields["Entry price (optional)"].get())
                                    if fields["Entry price (optional)"].get().strip()
                                    else None),
                    "outcome": fields["Outcome"].get().strip().upper(),
                }
                if selected_trade[0] is None:
                    self.session.add_trade(**values)
                else:
                    self.session.update_trade(selected_trade[0], **values)
                self.report_path = str(self.session.refresh_report())
                self.report_button.configure(state="normal")
                messagebox.showinfo("Demo trade", "Trade saved locally.", parent=window)
                window.destroy()
            except (OSError, ValueError, TypeError) as exc:
                messagebox.showerror("Trade not saved", str(exc), parent=window)

        tk.Button(window, text="Save / Correct", command=save_trade).grid(
            row=len(defaults) + 1, column=0, padx=8, pady=8, sticky="w")
        tk.Label(window, text="Manual demo record only; no orders are placed.").grid(
            row=len(defaults) + 1, column=1, padx=8, sticky="e")

    def save_debug_snapshot(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self.root, title="Save explicit full-desktop debug snapshot",
            defaultextension=".png", filetypes=(("PNG image", "*.png"),))
        if not destination:
            return
        if not messagebox.askyesno(
                "Sensitive screenshot", "This explicitly saves the full visible desktop. Continue?",
                parent=self.root):
            return

        def save():
            try:
                capture_full_desktop().save(destination, format="PNG")
                self.session.events.put(("snapshot", f"Saved: {destination}"))
            except Exception:
                self.session.events.put(("snapshot", "Debug snapshot failed"))

        threading.Thread(target=save, name="observer-debug-snapshot", daemon=True).start()

    def visual_debug_mode(self) -> None:
        self.latest_error.set("Capturing one private in-memory debug frame")

        def inspect_frame():
            try:
                image = capture_full_desktop()
                words = read_ocr_words(
                    image, executable=self.session.tesseract_executable, psm=11)
                chart_region = estimate_chart_region(image)
                candidates = numeric_candidate_boxes(words)
                self.root.after(0, lambda: self._show_visual_debug(
                    image, chart_region, candidates))
            except Exception as exc:
                self.root.after(0, lambda: self.latest_error.set(
                    f"Visual debug OCR/capture failed: {type(exc).__name__}"))

        threading.Thread(target=inspect_frame, name="visual-debug-capture",
                         daemon=True).start()

    def _show_visual_debug(self, image, chart_region, candidate_boxes) -> None:
        preview = tk.Toplevel(self.root)
        preview.title("Visual Debug: confirm current-price region")
        preview.transient(self.root)
        scale = min(1000 / image.width, 650 / image.height, 1.0)
        display_size = (round(image.width * scale), round(image.height * scale))
        thumbnail = image.resize(display_size)
        photo = ImageTk.PhotoImage(thumbnail, master=preview)
        canvas = tk.Canvas(preview, width=display_size[0], height=display_size[1],
                           highlightthickness=0)
        canvas.pack()
        canvas.create_image(0, 0, anchor="nw", image=photo)
        canvas.image = photo
        if chart_region:
            canvas.create_rectangle(
                chart_region[0] * display_size[0],
                chart_region[1] * display_size[1],
                chart_region[2] * display_size[0],
                chart_region[3] * display_size[1], outline="#00d4ff", width=2)
        for left, top, right, bottom in candidate_boxes:
            canvas.create_rectangle(left * scale, top * scale, right * scale,
                                    bottom * scale, outline="#ffb020", width=2)
        panel = tk.Frame(preview, padx=8, pady=8)
        panel.pack(fill="x")
        tk.Label(panel, text=(
            "Drag tightly around the live current-price label only. "
            "Candidate boxes are unverified OCR numbers."),
            wraplength=750, justify="left").grid(
                row=0, column=0, columnspan=3, sticky="w")
        asset = tk.StringVar(value="CRYPTO IDX")
        tk.Label(panel, text="Visible asset").grid(row=1, column=0, sticky="w")
        tk.Entry(panel, textvariable=asset, width=24).grid(row=1, column=1, sticky="w")
        start_point: list[tuple[float, float] | None] = [None]
        selection: list[int | None] = [None]

        def begin(event):
            start_point[0] = (event.x, event.y)
            if selection[0] is not None:
                canvas.delete(selection[0])
            selection[0] = canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline="#ff3b30", width=3)

        def drag(event):
            if start_point[0] is not None and selection[0] is not None:
                canvas.coords(selection[0], *start_point[0], event.x, event.y)

        def confirm():
            if start_point[0] is None or selection[0] is None:
                messagebox.showwarning("Select a region", "Drag around the current price label.",
                                       parent=preview)
                return
            x1, y1, x2, y2 = canvas.coords(selection[0])
            left, right = sorted((x1, x2))
            top, bottom = sorted((y1, y2))
            if right - left < 8 or bottom - top < 6:
                messagebox.showwarning("Region too small", "Select the complete price label.",
                                       parent=preview)
                return
            roi = (left / display_size[0], top / display_size[1],
                   right / display_size[0], bottom / display_size[1])
            try:
                self.session.set_confirmed_price_region(roi, asset.get())
            except ValueError as exc:
                messagebox.showerror("Region not saved", str(exc), parent=preview)
                return
            self.latest_error.set("Waiting for two consistent price readings")
            self.observation_quality.set("USER_CONFIRMED_REGION; TEMPORAL CHECK REQUIRED")
            preview.destroy()

        canvas.bind("<ButtonPress-1>", begin)
        canvas.bind("<B1-Motion>", drag)
        tk.Button(panel, text="Confirm current-price region",
                  command=confirm).grid(row=1, column=2, padx=(8, 0))
        tk.Label(panel, text=(f"Frame {image.width}x{image.height}; "
                              f"OCR number boxes: {len(candidate_boxes)}; "
                              "preview is not saved"),
                 anchor="w").grid(row=2, column=0, columnspan=3,
                                  sticky="w", pady=(5, 0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Floating demo-only Trading Copilot collection controls")
    parser.add_argument("--config", default="collector_config.json")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    enable_dpi_awareness()
    root = tk.Tk()
    observer = DesktopObserverSession(
        config.database_path,
        tesseract_executable=config.tesseract_executable,
        visual_backend=create_visual_backend(
            config.vision_backend, model=config.vision_model,
            timeout_seconds=config.vision_timeout_seconds))
    CollectorPanel(root, observer, config=config)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
