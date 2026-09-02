#!/usr/bin/env python3
"""Elaris Importer — a desktop front end for the Fantasia Archive → World Anvil pipeline.

Tkinter is used deliberately: it is in the standard library, so the packaged
build needs no bundled GUI toolkit and stays a single file.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import tkinter as tk
import traceback
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from elaris_import.pipeline import PipelineError, convert, load_worlds, upload

APP_NAME = "Elaris Importer"
API_DOCS = "https://www.worldanvil.com/api/external/boromir/documentation"
TOKEN_PAGE = "https://www.worldanvil.com/api/auth/key"

# Settings live next to the user's other app data, never beside the exe, so a
# read-only install directory still works.
if sys.platform == "win32":
    CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "ElarisImporter"
else:
    CONFIG_DIR = Path.home() / ".config" / "elaris-importer"
CONFIG_PATH = CONFIG_DIR / "settings.json"

# Saved only when the user ticks "remember"; see _save_settings.
SECRET_KEYS = ("app_key", "auth_token")


class App(ttk.Frame):
    def __init__(self, master: tk.Tk):
        super().__init__(master, padding=12)
        self.grid(sticky="nsew")
        master.columnconfigure(0, weight=1)
        master.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)

        self.messages: queue.Queue[str] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_flag = threading.Event()
        self.worlds: list[dict] = []

        self.export_path = tk.StringVar()
        self.map_path = tk.StringVar()
        self.out_path = tk.StringVar(value=str(Path.cwd() / "out"))
        self.scale = tk.IntVar(value=4)
        self.render_png = tk.BooleanVar(value=True)
        self.app_key = tk.StringVar()
        self.auth_token = tk.StringVar()
        self.world_label = tk.StringVar()
        self.remember = tk.BooleanVar(value=False)
        self.template_fields = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Ready.")

        self._build_inputs()
        self._build_worldanvil()
        self._build_actions()
        self._build_log()

        self._load_settings()
        self.after(100, self._drain)
        master.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- layout -----------------------------------------------------------
    def _build_inputs(self) -> None:
        box = ttk.LabelFrame(self, text="1. Your files", padding=10)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)

        self._file_row(
            box, 0, "Fantasia Archive export", self.export_path,
            lambda: self._pick_export(),
            "The .zip you exported, or the unzipped folder",
        )
        self._file_row(
            box, 1, "Azgaar map (optional)", self.map_path,
            lambda: self._pick_file(self.map_path, [("Azgaar map", "*.map"), ("All", "*.*")]),
            "The .map file from Fantasy Map Generator",
        )
        self._file_row(
            box, 2, "Output folder", self.out_path,
            lambda: self._pick_dir(self.out_path),
            "Where the converted files are written",
        )

        options = ttk.Frame(box)
        options.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(options, text="Render map image", variable=self.render_png,
                        command=self._toggle_scale).pack(side="left")
        ttk.Label(options, text="   Size").pack(side="left")
        self.scale_box = ttk.Spinbox(options, from_=1, to=8, width=4,
                                     textvariable=self.scale)
        self.scale_box.pack(side="left", padx=4)
        self.size_label = ttk.Label(options, text="", foreground="#666")
        self.size_label.pack(side="left")
        # Trace rather than the Spinbox's command, so typing and programmatic
        # changes update the preview too, not just the arrow buttons.
        self.scale.trace_add("write", lambda *_: self._show_size())
        self._show_size()

    def _build_worldanvil(self) -> None:
        box = ttk.LabelFrame(
            self, text="2. World Anvil (optional — leave blank to convert only)",
            padding=10,
        )
        box.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text="Application key").grid(row=0, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.app_key, show="•").grid(
            row=0, column=1, sticky="ew", padx=6, pady=2)

        ttk.Label(box, text="Auth token").grid(row=1, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.auth_token, show="•").grid(
            row=1, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(box, text="Get tokens…",
                   command=lambda: webbrowser.open(TOKEN_PAGE)).grid(
            row=1, column=2, padx=2)

        ttk.Label(box, text="World").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.world_box = ttk.Combobox(box, textvariable=self.world_label,
                                      state="readonly", values=[])
        self.world_box.grid(row=2, column=1, sticky="ew", padx=6, pady=(6, 0))
        ttk.Button(box, text="Load worlds", command=self._load_worlds).grid(
            row=2, column=2, padx=2, pady=(6, 0))

        extras = ttk.Frame(box)
        extras.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(extras, text="Remember credentials on this computer",
                        variable=self.remember).pack(side="left")
        ttk.Checkbutton(extras, text="Send template fields",
                        variable=self.template_fields).pack(side="left", padx=(16, 0))

        ttk.Label(
            box,
            text="Creating an application key needs a Guild rank above Grandmaster.",
            foreground="#666", cursor="hand2",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))

    def _build_actions(self) -> None:
        bar = ttk.Frame(self)
        bar.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        bar.columnconfigure(3, weight=1)

        self.convert_btn = ttk.Button(bar, text="Convert", command=self._run_convert)
        self.convert_btn.grid(row=0, column=0)
        self.upload_btn = ttk.Button(bar, text="Convert and upload",
                                     command=self._run_convert_upload)
        self.upload_btn.grid(row=0, column=1, padx=6)
        self.stop_btn = ttk.Button(bar, text="Stop", command=self._stop,
                                   state="disabled")
        self.stop_btn.grid(row=0, column=2)
        self.open_btn = ttk.Button(bar, text="Open output folder",
                                   command=self._open_output)
        self.open_btn.grid(row=0, column=4, sticky="e")

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(self, textvariable=self.status).grid(row=4, column=0, sticky="w")

    def _build_log(self) -> None:
        box = ttk.LabelFrame(self, text="Log", padding=6)
        box.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
        box.columnconfigure(0, weight=1)
        box.rowconfigure(0, weight=1)
        self.rowconfigure(5, weight=1)

        self.log_view = tk.Text(box, height=14, wrap="none", state="disabled",
                                font=("Consolas" if sys.platform == "win32"
                                      else "monospace", 9))
        self.log_view.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.log_view.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log_view.configure(yscrollcommand=scroll.set)

    def _file_row(self, parent, row, label, var, command, hint) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        entry = ttk.Entry(parent, textvariable=var)
        entry.grid(row=row, column=1, sticky="ew", padx=6, pady=2)
        ttk.Button(parent, text="Browse…", command=command).grid(row=row, column=2)
        self._add_tooltip(entry, hint)

    def _add_tooltip(self, widget, text: str) -> None:
        """A hover label. Tkinter has no tooltip widget, so this is a bare toplevel."""
        tip: tk.Toplevel | None = None

        def show(_event):
            nonlocal tip
            if tip:
                return
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 2
            tip = tk.Toplevel(widget)
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{x}+{y}")
            tk.Label(tip, text=text, background="#ffffe0", relief="solid",
                     borderwidth=1, padx=4, pady=2).pack()

        def hide(_event):
            nonlocal tip
            if tip:
                tip.destroy()
                tip = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    # -- pickers ----------------------------------------------------------
    def _pick_export(self) -> None:
        path = filedialog.askopenfilename(
            title="Select the Fantasia Archive export (.zip)",
            filetypes=[("Zip archive", "*.zip"), ("All files", "*.*")],
        )
        if not path:
            path = filedialog.askdirectory(title="…or select the unzipped folder")
        if path:
            self.export_path.set(path)
            if not self.out_path.get():
                self.out_path.set(str(Path(path).parent / "out"))

    def _pick_file(self, var, filetypes) -> None:
        path = filedialog.askopenfilename(filetypes=filetypes)
        if path:
            var.set(path)

    def _pick_dir(self, var) -> None:
        path = filedialog.askdirectory()
        if path:
            var.set(path)

    def _toggle_scale(self) -> None:
        self.scale_box.configure(state="normal" if self.render_png.get() else "disabled")
        self._show_size()

    def _show_size(self) -> None:
        scale = self._scale_value()
        # 1536x695 is this map's native export size; close enough as a preview.
        self.size_label.configure(text=f"≈ {1536 * scale} × {695 * scale} px")

    def _open_output(self) -> None:
        out = Path(self.out_path.get())
        if not out.exists():
            messagebox.showinfo(APP_NAME, "Nothing there yet — run a conversion first.")
            return
        if sys.platform == "win32":
            os.startfile(out)  # noqa: S606 - the documented Windows way to open a folder
        else:
            webbrowser.open(out.as_uri())

    # -- worker plumbing --------------------------------------------------
    def log(self, message: str) -> None:
        self.messages.put(message)

    def _drain(self) -> None:
        """Move worker messages onto the widget. Tkinter is not thread-safe."""
        wrote = False
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            self.log_view.configure(state="normal")
            self.log_view.insert("end", message + "\n")
            self.log_view.configure(state="disabled")
            wrote = True
        if wrote:
            self.log_view.see("end")
        self.after(100, self._drain)

    def _busy(self, busy: bool, status: str = "") -> None:
        state = "disabled" if busy else "normal"
        self.convert_btn.configure(state=state)
        self.upload_btn.configure(state=state)
        self.stop_btn.configure(state="normal" if busy else "disabled")
        self.progress.configure(mode="indeterminate" if busy else "determinate")
        if busy:
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.configure(value=0)
        if status:
            self.status.set(status)

    def _start(self, target, status: str) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo(APP_NAME, "Something is already running.")
            return
        self.stop_flag.clear()
        self._busy(True, status)

        def run():
            try:
                target()
            except PipelineError as exc:
                # Bind the text now: `exc` is unbound when the except block
                # ends, and this dialog is shown later on the main thread.
                message = str(exc)
                self.log(f"\nERROR: {message}")
                self.after(0, lambda: messagebox.showerror(APP_NAME, message))
            except Exception:
                self.log("\nUnexpected error:\n" + traceback.format_exc())
                self.after(0, lambda: messagebox.showerror(
                    APP_NAME, "Unexpected error — see the log."))
            finally:
                self.after(0, lambda: self._busy(False, "Ready."))

        self.worker = threading.Thread(target=run, daemon=True)
        self.worker.start()

    def _stop(self) -> None:
        self.stop_flag.set()
        self.status.set("Stopping after the current article…")

    # -- actions ----------------------------------------------------------
    def _inputs(self) -> tuple[Path | None, Path | None, Path]:
        export = Path(self.export_path.get()) if self.export_path.get() else None
        map_file = Path(self.map_path.get()) if self.map_path.get() else None
        if not export and not map_file:
            raise PipelineError("Choose an export folder/zip, a map file, or both.")
        if not self.out_path.get():
            raise PipelineError("Choose an output folder.")
        return export, map_file, Path(self.out_path.get())

    def _do_convert(self):
        export, map_file, out = self._inputs()
        return convert(
            export, map_file, out,
            scale=self._scale_value(),
            render_png=self.render_png.get(),
            log=self.log,
        )

    def _run_convert(self) -> None:
        self._start(self._do_convert, "Converting…")

    def _run_convert_upload(self) -> None:
        app_key = self.app_key.get().strip()
        auth_token = self.auth_token.get().strip()
        world = self._selected_world()

        if not app_key or not auth_token:
            messagebox.showerror(APP_NAME, "Enter your application key and auth token.")
            return
        if not world:
            messagebox.showerror(APP_NAME, "Load your worlds and pick one first.")
            return
        if not messagebox.askyesno(
            APP_NAME,
            f"Create or update articles in “{world['title']}”?\n\n"
            "Articles are created as private. Re-running updates the same "
            "articles instead of duplicating them.",
        ):
            return

        def job():
            self._do_convert()
            self.log("")
            upload(
                Path(self.out_path.get()), world["id"], app_key, auth_token,
                template_fields=self.template_fields.get(),
                log=self.log,
                should_stop=self.stop_flag.is_set,
            )

        self._start(job, "Converting and uploading…")

    def _selected_world(self) -> dict | None:
        label = self.world_label.get()
        return next((w for w in self.worlds if self._world_label(w) == label), None)

    @staticmethod
    def _world_label(world: dict) -> str:
        return f"{world['title']}  ({world['id'][:8]}…)"

    def _load_worlds(self) -> None:
        app_key = self.app_key.get().strip()
        auth_token = self.auth_token.get().strip()
        if not app_key or not auth_token:
            messagebox.showerror(APP_NAME, "Enter your application key and auth token.")
            return

        def job():
            self.log("Fetching your worlds…")
            worlds = load_worlds(app_key, auth_token)
            self.worlds = worlds
            labels = [self._world_label(w) for w in worlds]

            def apply():
                self.world_box.configure(values=labels)
                if labels:
                    self.world_box.current(0)
            self.after(0, apply)
            self.log(f"Found {len(worlds)} world(s).")
            for w in worlds:
                self.log(f"    {w['id']}  {w['title']}")

        self._start(job, "Loading worlds…")

    # -- settings ---------------------------------------------------------
    def _load_settings(self) -> None:
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.export_path.set(data.get("export_path", ""))
        self.map_path.set(data.get("map_path", ""))
        self.out_path.set(data.get("out_path", self.out_path.get()))
        self.scale.set(data.get("scale", 4))
        self.render_png.set(data.get("render_png", True))
        self.template_fields.set(data.get("template_fields", True))
        if data.get("remember"):
            self.remember.set(True)
            self.app_key.set(data.get("app_key", ""))
            self.auth_token.set(data.get("auth_token", ""))
        self._toggle_scale()

    def _scale_value(self) -> int:
        """The map scale as an int, clamped; the spinbox accepts free text."""
        try:
            return max(1, min(8, int(self.scale.get())))
        except (tk.TclError, ValueError):
            return 4

    def _save_settings(self) -> None:
        data = {
            "export_path": self.export_path.get(),
            "map_path": self.map_path.get(),
            "out_path": self.out_path.get(),
            "scale": self._scale_value(),
            "render_png": self.render_png.get(),
            "template_fields": self.template_fields.get(),
            "remember": self.remember.get(),
        }
        if self.remember.get():
            data["app_key"] = self.app_key.get()
            data["auth_token"] = self.auth_token.get()

        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")
            # Credentials are stored in the clear, so at least keep the file
            # readable only by this user where the OS supports it.
            if self.remember.get() and sys.platform != "win32":
                CONFIG_PATH.chmod(0o600)
        except OSError:
            pass  # settings are a convenience; never block closing on them

    def _on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno(APP_NAME, "A job is running. Quit anyway?"):
                return
            self.stop_flag.set()
        self._save_settings()
        self.master.destroy()


def selftest() -> int:
    """Prove the frozen build can import everything and open a window.

    Run by CI on the built executable. A windowed exe has no stdout, so the
    exit code is the whole report.
    """
    from elaris_import import azgaar, bbcode, fa_parse, mapping, wapi  # noqa: F401

    root = tk.Tk()
    root.withdraw()
    App(root)
    root.update()
    root.destroy()
    return 0


def main() -> int:
    if "--selftest" in sys.argv:
        return selftest()

    root = tk.Tk()
    root.title(APP_NAME)
    root.minsize(720, 640)
    try:
        ttk.Style().theme_use("vista" if sys.platform == "win32" else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
