import os
import sys
import argparse
import ctypes
import struct
import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
from PIL import Image, ImageTk
import re
from datetime import datetime
import shutil
import threading
import math
import json
from concurrent.futures import ProcessPoolExecutor, as_completed
from tqdm import tqdm
from threading import Lock
import multiprocessing
from localization.language_manager_metadatasearch import LanguageManagerMetadataSearch
from config.config_manager_metadatasearch import ConfigManagerMetadataSearch


def resource_path(relative_path):
    """Resolve resource path — works both in development and when bundled by PyInstaller."""
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = os.path.abspath(".")
    return os.path.join(base, relative_path)


# ---------------------------------------------------------------------------
# Image processing (module-level for multiprocessing compatibility)
# ---------------------------------------------------------------------------

def process_single_image(args):
    image_path, search_term, search_positive, search_negative, case_sensitive, custom_filter, ignore_term = args
    try:
        with Image.open(image_path) as image:
            exif_data = image.info
            if not exif_data:
                return None
            metadata = parse_exif_data(exif_data)
            match_result = matches_search_term(metadata, search_term, search_positive, search_negative, case_sensitive, ignore_term)
            if match_result and apply_custom_filter(image_path, metadata, custom_filter):
                return (image_path, match_result)
    except Exception:
        return None
    return None


def parse_exif_data(exif_data):
    if not exif_data or 'parameters' not in exif_data:
        return {}

    params = exif_data['parameters']
    parsed_data = {}

    positive_end = params.find('Negative prompt:')
    if positive_end != -1:
        parsed_data['Positive'] = params[:positive_end].strip()
        negative_start = positive_end + len('Negative prompt:')
        negative_end = params.find('Steps:')
        if negative_end != -1:
            parsed_data['Negative'] = params[negative_start:negative_end].strip()

    param_patterns = {
        'Steps':              r'Steps: (.*?)(?:,|$)',
        'Sampler':            r'Sampler: (.*?)(?:,|$)',
        'CFG scale':          r'CFG scale: (.*?)(?:,|$)',
        'Seed':               r'Seed: (.*?)(?:,|$)',
        'Size':               r'Size: (.*?)(?:,|$)',
        'Model':              r'Model: (.*?)(?:,|$)',
        'Denoising strength': r'Denoising strength: (.*?)(?:,|$)',
        'Clip skip':          r'Clip skip: (.*?)(?:,|$)',
        'Hires upscale':      r'Hires upscale: (.*?)(?:,|$)',
        'Hires steps':        r'Hires steps: (.*?)(?:,|$)',
        'Hires upscaler':     r'Hires upscaler: (.*?)(?:,|$)',
        'Lora hashes':        r'Lora hashes: "(.*?)"(?:,|$)',
    }

    for key, pattern in param_patterns.items():
        match = re.search(pattern, params)
        if match:
            parsed_data[key] = match.group(1).strip()

    return parsed_data


def matches_search_term(metadata, search_term, search_positive, search_negative, case_sensitive, ignore_term=None):
    if not metadata:
        return None
    if not search_term:
        return None

    if ignore_term:
        for ignore_group in [t.strip() for t in ignore_term.split('||')]:
            if not ignore_group:
                continue
            all_and_match = True
            for term in [t.strip() for t in ignore_group.split('&&')]:
                if not term:
                    continue
                pattern = f'.*{re.escape(term).replace(r"\\*", ".*").replace(r"\\?", ".")}.*'
                term_matched = False
                if search_positive or search_negative:
                    if search_positive and 'Positive' in metadata:
                        if re.search(pattern, metadata['Positive'], flags=0 if case_sensitive else re.IGNORECASE):
                            term_matched = True
                    if search_negative and 'Negative' in metadata:
                        if re.search(pattern, metadata['Negative'], flags=0 if case_sensitive else re.IGNORECASE):
                            term_matched = True
                else:
                    for value in metadata.values():
                        if isinstance(value, str) and re.search(pattern, value, flags=0 if case_sensitive else re.IGNORECASE):
                            term_matched = True
                            break
                if not term_matched:
                    all_and_match = False
                    break
            if all_and_match:
                return None

    for or_index, or_group in enumerate([t.strip() for t in search_term.split('||')]):
        if not or_group:
            continue
        all_and_match = True
        for term in [t.strip() for t in or_group.split('&&')]:
            if not term:
                continue
            pattern = f'.*{re.escape(term).replace(r"\\*", ".*").replace(r"\\?", ".")}.*'
            term_matched = False
            if search_positive or search_negative:
                if search_positive and 'Positive' in metadata:
                    if re.search(pattern, metadata['Positive'], flags=0 if case_sensitive else re.IGNORECASE):
                        term_matched = True
                if search_negative and 'Negative' in metadata:
                    if re.search(pattern, metadata['Negative'], flags=0 if case_sensitive else re.IGNORECASE):
                        term_matched = True
            else:
                for value in metadata.values():
                    if isinstance(value, str) and re.search(pattern, value, flags=0 if case_sensitive else re.IGNORECASE):
                        term_matched = True
                        break
            if not term_matched:
                all_and_match = False
                break
        if all_and_match:
            return (or_index, or_group)

    return None


def apply_custom_filter(image_path, metadata, custom_filter):
    if not custom_filter:
        return True
    try:
        for value in metadata.values():
            if isinstance(value, str) and re.search(custom_filter.strip(), value):
                return True
        return False
    except re.error:
        return False


def sanitize_folder_name(name):
    sanitized = re.sub(r'[<>:"/\\|?*]', '_', name).strip('. ')
    return sanitized if sanitized else 'unnamed'


def validate_search_term(search_term):
    warnings = []
    if not search_term:
        return "", []
    cleaned = re.sub(r'\|{3,}', '||', search_term)
    if cleaned != search_term:
        warnings.append("Multiple consecutive OR operators simplified to ||")
    cleaned = re.sub(r'&{3,}', '&&', cleaned)
    or_terms = [t.strip() for t in cleaned.split('||')]
    valid_or_terms = []
    for or_term in or_terms:
        and_terms = [t.strip() for t in or_term.split('&&')]
        valid_and = [t for t in and_terms if t]
        if len(and_terms) != len(valid_and):
            warnings.append("Empty AND terms were removed from search")
        if valid_and:
            valid_or_terms.append(' && '.join(valid_and))
    if len(or_terms) != len(valid_or_terms):
        warnings.append("Empty OR terms were removed from search")
    if not valid_or_terms:
        warnings.append("No valid search terms found after cleaning")
        return "", warnings
    return ' || '.join(valid_or_terms), warnings


# ---------------------------------------------------------------------------
# Dark theme
# ---------------------------------------------------------------------------

_DARK = {
    'bg':      '#18192a',
    'bg2':     '#22243a',
    'bg3':     '#0f1020',
    'fg':      '#d0d2e8',
    'fg2':     '#8890b8',
    'border':  '#3a3b55',
    'accent':  '#4a6eff',
    'sel_bg':  '#2a3580',
    'btn_bg':  '#2a2c48',
    'btn_act': '#3a3c58',
    'disabled':'#55577a',
}


def apply_dark_theme(root):
    style = ttk.Style(root)
    style.theme_use('clam')
    d = _DARK

    root.configure(bg=d['bg'])

    style.configure('.',
        background=d['bg'], foreground=d['fg'],
        bordercolor=d['border'], darkcolor=d['bg2'], lightcolor=d['bg2'],
        troughcolor=d['bg2'], selectbackground=d['sel_bg'],
        selectforeground=d['fg'], fieldbackground=d['bg3'],
        insertcolor=d['fg'], relief='flat',
    )
    style.configure('TFrame', background=d['bg'])
    style.configure('TLabel', background=d['bg'], foreground=d['fg'])
    style.configure('TLabelframe', background=d['bg'], foreground=d['fg'],
                    bordercolor=d['border'])
    style.configure('TLabelframe.Label', background=d['bg'], foreground=d['fg'])

    style.configure('TButton',
        background=d['btn_bg'], foreground=d['fg'],
        bordercolor=d['border'], focusthickness=0, padding=(6, 4))
    style.map('TButton',
        background=[('active', d['btn_act']), ('pressed', d['accent']),
                    ('disabled', d['bg2'])],
        foreground=[('disabled', d['disabled'])],
        relief=[('pressed', 'flat'), ('!pressed', 'flat')])

    style.configure('TCheckbutton', background=d['bg'], foreground=d['fg'],
                    focusthickness=0, indicatorcolor=d['bg3'],
                    indicatorrelief='flat')
    style.map('TCheckbutton',
        background=[('active', d['bg'])],
        foreground=[('disabled', d['disabled'])],
        indicatorcolor=[('selected', d['accent']), ('!selected', d['bg3'])])

    style.configure('TEntry',
        fieldbackground=d['bg3'], foreground=d['fg'],
        insertcolor=d['fg'], bordercolor=d['border'],
        selectbackground=d['sel_bg'], selectforeground=d['fg'])

    style.configure('TCombobox',
        fieldbackground=d['bg3'], foreground=d['fg'],
        selectbackground=d['sel_bg'], selectforeground=d['fg'],
        background=d['btn_bg'], bordercolor=d['border'],
        arrowcolor=d['fg'])
    style.map('TCombobox',
        fieldbackground=[('readonly', d['bg3'])],
        foreground=[('disabled', d['disabled'])],
        selectbackground=[('readonly', d['sel_bg'])])

    style.configure('TScrollbar',
        background=d['bg2'], troughcolor=d['bg'],
        arrowcolor=d['fg'], bordercolor=d['bg'], relief='flat')
    style.map('TScrollbar', background=[('active', d['btn_act'])])

    style.configure('Horizontal.TProgressbar',
        background=d['accent'], troughcolor=d['bg2'],
        bordercolor=d['border'], darkcolor=d['accent'], lightcolor=d['accent'])
    style.configure('Vertical.TProgressbar',
        background=d['accent'], troughcolor=d['bg2'])

    style.configure('TSeparator', background=d['border'])


def style_menu(menu, dark=True):
    if not dark:
        return
    d = _DARK
    try:
        menu.configure(
            bg=d['bg2'], fg=d['fg'],
            activebackground=d['accent'], activeforeground='#ffffff',
            borderwidth=1, relief='flat',
            disabledforeground=d['disabled'],
        )
    except tk.TclError:
        pass


def apply_light_theme(root):
    style = ttk.Style(root)
    try:
        style.theme_use('vista')
    except tk.TclError:
        try:
            style.theme_use('default')
        except tk.TclError:
            pass
    root.configure(bg='SystemButtonFace')


# ---------------------------------------------------------------------------
# MetadataSearcher
# ---------------------------------------------------------------------------

class MetadataSearcher:
    def __init__(self, search_term, recursive=False, log_path=None, copy_path=None,
                 move_path=None, custom_filter=None, search_positive=True,
                 search_negative=False, case_sensitive=False, ignore_term=None, lang=None):
        cleaned_term, search_warnings = validate_search_term(search_term)
        cleaned_ignore, ignore_warnings = validate_search_term(ignore_term) if ignore_term else ("", [])
        self.search_term = cleaned_term
        self.ignore_term = cleaned_ignore

        warnings = search_warnings + ignore_warnings
        self.recursive = recursive
        self.log_path = log_path
        self.copy_path = copy_path
        self.move_path = move_path
        self.custom_filter = custom_filter
        self.match_folder_structure = True
        self.create_or_subfolders = False
        self.search_positive = search_positive
        self.search_negative = search_negative
        self.case_sensitive = case_sensitive
        self.lang = lang
        self.output_text = []
        self.search_root = None

        self.found_files = []
        self.found_paths = []
        self.output_paths = []
        self.copied_files = []
        self.moved_files = []
        self.log_lock = Lock()
        self.progress_callback = None

        if self.log_path:
            os.makedirs(self.log_path, exist_ok=True)
        if self.copy_path:
            os.makedirs(self.copy_path, exist_ok=True)
        if self.move_path:
            os.makedirs(self.move_path, exist_ok=True)

        for warning in warnings:
            self.log(f"Warning: {warning}")
        if cleaned_term != search_term:
            self.log(f"Search term was cleaned to: {cleaned_term}")

    def count_files(self, folder_path):
        if self.recursive:
            return sum(
                sum(1 for f in files if f.lower().endswith('.png'))
                for _, _, files in os.walk(folder_path)
            )
        return sum(1 for f in os.listdir(folder_path) if f.lower().endswith('.png'))

    def get_all_png_files(self, folder_path):
        if self.recursive:
            return [
                os.path.join(root, f)
                for root, _, files in os.walk(folder_path)
                for f in files if f.lower().endswith('.png')
            ]
        return [
            os.path.join(folder_path, f)
            for f in os.listdir(folder_path) if f.lower().endswith('.png')
        ]

    def process_match(self, match_data):
        if not match_data:
            return
        image_path, (or_index, or_term) = match_data
        filename = os.path.basename(image_path)
        self.found_files.append(filename)
        self.found_paths.append(image_path)

        dest_path = None
        if self.copy_path or self.move_path:
            if self.create_or_subfolders:
                subfolder = sanitize_folder_name(or_term)
                if self.match_folder_structure:
                    rel_path = os.path.relpath(os.path.dirname(image_path), self.search_root)
                    dest_dir = (os.path.join(self.copy_path or self.move_path, subfolder)
                                if rel_path == '.'
                                else os.path.join(self.copy_path or self.move_path, subfolder, rel_path))
                else:
                    dest_dir = os.path.join(self.copy_path or self.move_path, subfolder)
            else:
                if self.match_folder_structure:
                    rel_path = os.path.relpath(os.path.dirname(image_path), self.search_root)
                    dest_dir = (self.copy_path or self.move_path
                                if rel_path == '.'
                                else os.path.join(self.copy_path or self.move_path, rel_path))
                else:
                    dest_dir = self.copy_path or self.move_path

            os.makedirs(dest_dir, exist_ok=True)
            dest_path = os.path.join(dest_dir, filename)

            if self.copy_path:
                shutil.copy2(image_path, dest_path)
                self.copied_files.append(dest_path)
            if self.move_path:
                shutil.move(image_path, dest_path)
                self.moved_files.append(dest_path)

            self.output_paths.append(dest_path)
        else:
            self.output_paths.append(image_path)

    def log(self, message):
        with self.log_lock:
            self.output_text.append(message)
            print(message)
            if self.log_path:
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                log_file = os.path.join(self.log_path, f"log_{timestamp}.txt")
                with open(log_file, 'a', encoding='utf-8') as f:
                    f.write(f"{datetime.now().isoformat()}: {message}\n")

    def set_progress_callback(self, callback):
        self.progress_callback = callback

    def update_progress(self, phase, current, total):
        if self.progress_callback:
            self.progress_callback(phase, current, total)

    def search_images(self, folder_path):
        self.search_root = folder_path
        self.log(self.lang.get_string("messages.searching_in").format(folder_path))

        self.found_files = []
        self.found_paths = []
        self.output_paths = []
        self.copied_files = []
        self.moved_files = []

        if not self.search_term and not self.custom_filter:
            self.log(self.lang.get_string("errors.no_valid_terms"))
            return

        self.log(self.lang.get_string("progress.counting"))
        total_files = self.count_files(folder_path)
        self.log(self.lang.get_string("progress.found_files").format(total_files))

        png_files = self.get_all_png_files(folder_path)
        process_args = [
            (f, self.search_term, self.search_positive, self.search_negative,
             self.case_sensitive, self.custom_filter, self.ignore_term)
            for f in png_files
        ]

        matching_files = 0
        processed_files = 0
        max_workers = max(1, multiprocessing.cpu_count() - 1)
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_single_image, arg) for arg in process_args]
            progress_stream = sys.stderr if sys.stderr is not None else sys.stdout
            future_iterator = (
                tqdm(
                    as_completed(futures),
                    total=len(futures),
                    desc=self.lang.get_string("progress.processing"),
                    unit="file",
                    file=progress_stream,
                )
                if progress_stream is not None
                else as_completed(futures)
            )
            for future in future_iterator:
                processed_files += 1
                result = future.result()
                if result:
                    matching_files += 1
                    self.process_match(result)
                self.update_progress("search", processed_files, total_files)

        if matching_files > 0:
            self.log("\n" + self.lang.get_string("messages.matching_files"))
            for filename in self.found_files:
                self.log(filename)
            self.log("\n" + self.lang.get_string("messages.complete_paths"))
            if self.copy_path:
                for path in self.copied_files:
                    self.log(path)
            elif self.move_path:
                for path in self.moved_files:
                    self.log(path)
            else:
                for path in self.found_paths:
                    self.log(path)

        self.log("\n" + self.lang.get_string("messages.summary"))
        self.log(self.lang.get_string("messages.total_files").format(total_files))
        self.log(self.lang.get_string("messages.matches_found").format(matching_files))

        actions = []
        if self.log_path:
            actions.append(self.lang.get_string("messages.logged_files"))
        if self.copy_path:
            actions.append(self.lang.get_string("messages.copied_files").format(len(self.copied_files)))
        if self.move_path:
            actions.append(self.lang.get_string("messages.moved_files").format(len(self.moved_files)))
        if actions:
            self.log(self.lang.get_string("messages.actions_taken").format(", ".join(actions)))


# ---------------------------------------------------------------------------
# Image Preview window
# ---------------------------------------------------------------------------

class ImagePreview(tk.Toplevel):
    def __init__(self, parent, image_path):
        super().__init__(parent)
        self.title(os.path.basename(image_path))
        self.geometry("900x700")
        self.minsize(640, 480)
        self.image_path = image_path
        self.pil_image = None
        self.zoom = 1.0
        self.photo = None

        self._setup_ui()
        self._load_image()

        # Set icon
        try:
            self.iconbitmap(resource_path('app_icon.ico'))
        except Exception:
            pass

    def _setup_ui(self):
        toolbar = ttk.Frame(self)
        toolbar.pack(fill=tk.X, padx=4, pady=4)

        ttk.Button(toolbar, text="Zoom In (+)", command=self._zoom_in).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Zoom Out (-)", command=self._zoom_out).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Fit to Window", command=self._fit_to_window).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="1:1 (100%)", command=self._actual_size).pack(side=tk.LEFT, padx=2)

        self.zoom_label = ttk.Label(toolbar, text="100%", width=8)
        self.zoom_label.pack(side=tk.LEFT, padx=6)

        self.info_label = ttk.Label(toolbar, text="")
        self.info_label.pack(side=tk.RIGHT, padx=6)

        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(frame, bg='#1a1a2e', cursor='hand2')
        v_scroll = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=self.canvas.yview)
        h_scroll = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=v_scroll.set, xscrollcommand=h_scroll.set)

        h_scroll.pack(side=tk.BOTTOM, fill=tk.X)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas.bind('<MouseWheel>', self._on_mousewheel)
        self.canvas.bind('<ButtonPress-1>', lambda e: self.canvas.scan_mark(e.x, e.y))
        self.canvas.bind('<B1-Motion>', lambda e: self.canvas.scan_dragto(e.x, e.y, gain=1))
        self.bind('<plus>', lambda e: self._zoom_in())
        self.bind('<equal>', lambda e: self._zoom_in())
        self.bind('<minus>', lambda e: self._zoom_out())
        self.bind('<Escape>', lambda e: self.destroy())

    def _load_image(self):
        try:
            self.pil_image = Image.open(self.image_path)
            w, h = self.pil_image.size
            self.info_label.config(text=f"{w} × {h}  |  {os.path.basename(self.image_path)}")
            self.after(50, self._open_sized_to_image)
        except Exception as e:
            self.canvas.create_text(200, 200, text=f"Error loading image:\n{e}", fill='white')

    def _open_sized_to_image(self):
        if not self.pil_image:
            return
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        max_w = max(900, int(screen_w * 0.96))
        max_h = max(700, int(screen_h * 0.94))
        win_w = min(self.pil_image.width + 48, max_w)
        win_h = min(self.pil_image.height + 120, max_h)
        x = max((screen_w - win_w) // 2, 0)
        y = max((screen_h - win_h) // 2, 0)
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        self._fit_to_window()

    def _display(self):
        if not self.pil_image:
            return
        w = max(1, int(self.pil_image.width * self.zoom))
        h = max(1, int(self.pil_image.height * self.zoom))
        resized = self.pil_image.resize((w, h), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.photo)
        self.canvas.configure(scrollregion=(0, 0, w, h))
        self.zoom_label.config(text=f"{int(self.zoom * 100)}%")

    def _zoom_in(self):
        self.zoom = min(self.zoom * 1.25, 8.0)
        self._display()

    def _zoom_out(self):
        self.zoom = max(self.zoom / 1.25, 0.05)
        self._display()

    def _fit_to_window(self):
        if not self.pil_image:
            return
        self.update_idletasks()
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        self.zoom = min(cw / self.pil_image.width, ch / self.pil_image.height, 1.0)
        self._display()

    def _actual_size(self):
        self.zoom = 1.0
        self._display()

    def _on_mousewheel(self, event):
        if event.delta > 0:
            self._zoom_in()
        else:
            self._zoom_out()


# ---------------------------------------------------------------------------
# Image Browser window
# ---------------------------------------------------------------------------

class ImageBrowser(tk.Toplevel):
    def __init__(self, parent, image_paths, lang):
        super().__init__(parent)
        self.title("Image Browser")
        self.geometry("1200x820")
        self.image_paths = list(image_paths)
        self.lang = lang
        self.selected = set()
        self.thumbnail_cache = {}   # path -> PIL Image (capped at 512px)
        self.photo_cache = {}       # (path, size) -> PhotoImage
        self.thumbnail_size = 150
        self.cell_data = []         # list of (frame_widget, path, index)
        self._scale_job = None
        self._rebuild_job = None
        self._mousewheel_bound = False

        try:
            self.iconbitmap(resource_path('app_icon.ico'))
        except Exception:
            pass

        self._setup_ui()
        self.after(0, self._maximize_window)
        self._start_loading()

    def _maximize_window(self):
        try:
            self.state('zoomed')
        except Exception:
            pass

    def _setup_ui(self):
        # ── Top toolbar ──────────────────────────────────────────────────
        top = ttk.Frame(self, padding=4)
        top.pack(fill=tk.X)
        top.columnconfigure(1, weight=1)

        ttk.Label(top, text="Scale:").grid(row=0, column=0, padx=(0, 8), sticky='w')
        self.scale_var = tk.IntVar(value=150)
        self.scale_label = ttk.Label(top, text="150 px", width=7)
        self.scale_widget = tk.Scale(
            top,
            from_=80,
            to=900,
            resolution=10,
            variable=self.scale_var,
            orient=tk.HORIZONTAL,
            showvalue=False,
            sliderlength=28,
            width=24,
            highlightthickness=0,
            bd=0,
            command=self._on_scale_change,
        )
        self.scale_widget.grid(row=0, column=1, sticky='ew')
        self.scale_widget.bind('<ButtonRelease-1>', self._apply_scale_change)
        self.scale_label.grid(row=0, column=2, padx=(8, 12), sticky='w')

        self.status_var = tk.StringVar(value=f"Loading {len(self.image_paths)} images…")
        ttk.Label(top, textvariable=self.status_var).grid(row=0, column=3, sticky='e')

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        # ── Scrollable canvas ────────────────────────────────────────────
        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(container, bg='#18192a', highlightthickness=0)
        v_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=v_scroll.set)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.inner = tk.Frame(self.canvas, bg='#18192a')
        self.canvas_win = self.canvas.create_window((0, 0), window=self.inner, anchor=tk.NW)

        self.inner.bind('<Configure>', self._sync_scroll)
        self.inner.bind('<Enter>', self._bind_mousewheel)
        self.inner.bind('<Leave>', self._unbind_mousewheel)
        self.canvas.bind('<Configure>', self._on_canvas_resize)
        self.canvas.bind('<Enter>', self._bind_mousewheel)
        self.canvas.bind('<Leave>', self._unbind_mousewheel)
        self.bind('<Escape>', lambda e: self.destroy())
        self.bind('<Prior>', lambda e: self.canvas.yview_scroll(-1, 'pages'))
        self.bind('<Next>', lambda e: self.canvas.yview_scroll(1, 'pages'))

    def _sync_scroll(self, event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _bind_mousewheel(self, event=None):
        if self._mousewheel_bound:
            return
        self.bind_all('<MouseWheel>', self._on_mousewheel)
        self._mousewheel_bound = True

    def _unbind_mousewheel(self, event=None):
        current = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        if current and str(current).startswith(str(self)):
            return
        if not self._mousewheel_bound:
            return
        self.unbind_all('<MouseWheel>')
        self._mousewheel_bound = False

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    def _on_canvas_resize(self, event=None):
        self.canvas.itemconfig(self.canvas_win, width=self.canvas.winfo_width())
        self._schedule_rebuild(50)

    def _schedule_rebuild(self, delay=0):
        if self._rebuild_job is not None:
            self.after_cancel(self._rebuild_job)
        self._rebuild_job = self.after(delay, self._run_rebuild)

    def _run_rebuild(self):
        self._rebuild_job = None
        self._rebuild_grid()

    def _on_scale_change(self, value):
        size = int(float(value))
        self.scale_label.config(text=f"{size} px")
        if self._scale_job is not None:
            self.after_cancel(self._scale_job)
        self._scale_job = self.after(80, self._apply_scale_change)

    def _apply_scale_change(self, event=None):
        if self._scale_job is not None:
            self.after_cancel(self._scale_job)
            self._scale_job = None
        size = int(self.scale_var.get())
        if size != self.thumbnail_size:
            self.thumbnail_size = size
            self.photo_cache.clear()
        self._schedule_rebuild()

    # ── Thumbnail loading ────────────────────────────────────────────────

    def _start_loading(self):
        threading.Thread(target=self._load_all, daemon=True).start()

    def _load_all(self):
        for path in self.image_paths:
            try:
                img = Image.open(path)
                img.thumbnail((1024, 1024), Image.LANCZOS)
                # Convert to RGBA so resizing later is consistent
                self.thumbnail_cache[path] = img.convert('RGBA')
            except Exception:
                self.thumbnail_cache[path] = None
        self.after(0, self._run_rebuild)

    def _get_photo(self, path):
        size = self.thumbnail_size
        key = (path, size)
        if key in self.photo_cache:
            return self.photo_cache[key]
        src = self.thumbnail_cache.get(path)
        if src is None:
            return None
        img = src.copy()
        img.thumbnail((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        self.photo_cache[key] = photo
        return photo

    # ── Grid construction ────────────────────────────────────────────────

    def _rebuild_grid(self):
        for w in self.inner.winfo_children():
            w.destroy()
        self.cell_data = []

        cw = self.canvas.winfo_width() or 1200
        cell_size = self.thumbnail_size + 34
        cols = max(1, cw // cell_size)
        self.inner.grid_anchor(tk.NW)
        for col in range(cols):
            self.inner.grid_columnconfigure(col, weight=1, uniform='thumb')

        name_font_size = max(9, min(16, self.thumbnail_size // 16))
        wrap_length = self.thumbnail_size + 20

        for idx, path in enumerate(self.image_paths):
            row, col = divmod(idx, cols)
            cell = tk.Frame(self.inner, bg='#18192a',
                            highlightthickness=2, highlightbackground='#18192a')
            cell.grid(row=row, column=col, padx=5, pady=5, sticky='n')

            photo = self._get_photo(path)
            if photo:
                img_lbl = tk.Label(cell, image=photo, bg='#18192a', cursor='hand2')
                img_lbl.image = photo
                img_lbl.pack(padx=2, pady=(2, 4))
            else:
                # Placeholder tile
                img_lbl = tk.Label(cell, text="?", width=max(4, self.thumbnail_size // 12),
                                   height=max(2, self.thumbnail_size // 22),
                                   bg='#2a2b40', fg='#666688', cursor='hand2')
                img_lbl.pack(padx=2, pady=(2, 4))

            short = os.path.basename(path)
            if len(short) > 22:
                short = short[:19] + "…"
            short = os.path.basename(path)
            name_lbl = tk.Label(
                cell,
                text=short,
                bg='#18192a',
                fg='#ccccdd',
                font=('Segoe UI', name_font_size),
                wraplength=wrap_length,
                justify=tk.CENTER,
                cursor='hand2',
            )
            name_lbl.pack(fill=tk.X, padx=2, pady=(0, 2))

            for w in (cell, img_lbl, name_lbl):
                w.bind('<Button-1>', lambda e, i=idx: self._on_click(e, i))
                w.bind('<Double-Button-1>', lambda e, i=idx: self._on_double_click(i))
                w.bind('<Button-3>', lambda e, i=idx: self._on_right_click(e, i))

            self.cell_data.append((cell, path, idx))

        self._refresh_highlights()
        self._update_status()
        self._sync_scroll()

    # ── Selection ────────────────────────────────────────────────────────

    def _on_click(self, event, idx):
        ctrl = bool(event.state & 0x4)
        if ctrl:
            if idx in self.selected:
                self.selected.discard(idx)
            else:
                self.selected.add(idx)
        else:
            self.selected = {idx}
        self._refresh_highlights()
        self._update_status()

    def _refresh_highlights(self):
        for cell, path, idx in self.cell_data:
            color = '#3a7eff' if idx in self.selected else '#18192a'
            cell.config(highlightbackground=color)

    def _update_status(self):
        total = len(self.image_paths)
        sel = len(self.selected)
        self.status_var.set(
            f"{total} image{'s' if total != 1 else ''}  |  {sel} selected" if sel
            else f"{total} image{'s' if total != 1 else ''}"
        )

    # ── Double-click ─────────────────────────────────────────────────────

    def _on_double_click(self, idx):
        ImagePreview(self, self.image_paths[idx])

    # ── Right-click context menu ─────────────────────────────────────────

    def _on_right_click(self, event, idx):
        if idx not in self.selected:
            self.selected = {idx}
            self._refresh_highlights()
            self._update_status()

        paths = [self.image_paths[i] for i in sorted(self.selected)]
        multi = len(paths) > 1

        menu = tk.Menu(self, tearoff=0)

        if not multi:
            menu.add_command(label="Open Image (Preview)",
                             command=lambda: ImagePreview(self, paths[0]))
            menu.add_command(label="Open with Default App",
                             command=lambda: os.startfile(paths[0]))
            menu.add_command(label="Open Containing Folder",
                             command=lambda: os.startfile(os.path.dirname(paths[0])))
            menu.add_separator()
            menu.add_command(label="Copy Path",
                             command=lambda: self._copy_text(paths[0]))
        else:
            menu.add_command(label=f"Preview {min(len(paths), 5)} images",
                             command=lambda: [ImagePreview(self, p) for p in paths[:5]])
            menu.add_command(label="Open Containing Folder",
                             command=lambda: os.startfile(os.path.dirname(paths[0])))
            menu.add_separator()
            menu.add_command(label="Copy Paths (all)",
                             command=lambda: self._copy_text('\n'.join(paths)))

        menu.add_separator()
        label = f"Delete {len(paths)} image{'s' if multi else ''}"
        menu.add_command(label=label, foreground='#cc3333',
                         command=lambda: self._delete_images(paths))

        menu.tk_popup(event.x_root, event.y_root)

    def _copy_text(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _delete_images(self, paths):
        n = len(paths)
        if not messagebox.askyesno(
                "Confirm Delete",
                f"Permanently delete {n} image{'s' if n > 1 else ''}?\n\nThis cannot be undone.",
                parent=self):
            return
        for path in paths:
            try:
                os.remove(path)
            except Exception as e:
                messagebox.showerror("Delete Error", f"Could not delete:\n{path}\n\n{e}", parent=self)
            if path in self.image_paths:
                self.image_paths.remove(path)
            self.thumbnail_cache.pop(path, None)
            for key in [k for k in self.photo_cache if k[0] == path]:
                del self.photo_cache[key]
        self.selected.clear()
        self._rebuild_grid()


# ---------------------------------------------------------------------------
# Main GUI
# ---------------------------------------------------------------------------

class ImagePreview(tk.Toplevel):
    def __init__(self, parent, image_path, lang, dark_mode=True):
        super().__init__(parent)
        self.lang = lang
        self._dark_mode = dark_mode
        self.image_path = image_path
        self.pil_image = None
        self.zoom = 1.0
        self.photo = None
        self._zoom_drag_origin = None
        self._zoom_drag_start = 1.0
        self._zoom_dragged = False
        self._pan_offset = [0, 0]
        self._pan_start = None
        self._pan_start_offset = [0, 0]

        self.configure(bg=_DARK['bg'])
        self._setup_ui()
        self._load_image()

        try:
            self.iconbitmap(resource_path('app_icon.ico'))
        except Exception:
            pass

    def _setup_ui(self):
        frame = ttk.Frame(self)
        frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(frame, bg='#1a1a2e', cursor='fleur', highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind('<MouseWheel>', self._on_mousewheel)
        self.canvas.bind('<ButtonPress-3>', self._start_zoom_drag)
        self.canvas.bind('<B3-Motion>', self._on_zoom_drag)
        self.canvas.bind('<ButtonRelease-3>', self._end_zoom_drag)
        self.canvas.bind('<ButtonPress-1>', self._start_pan)
        self.canvas.bind('<B1-Motion>', self._on_pan)
        self.canvas.bind('<ButtonRelease-1>', self._end_pan)
        self.canvas.bind('<ButtonPress-2>', self._start_pan)
        self.canvas.bind('<B2-Motion>', self._on_pan)
        self.canvas.bind('<ButtonRelease-2>', self._end_pan)
        self.canvas.bind('<Configure>', lambda e: self._display())
        self.bind('<Escape>', lambda e: self.destroy())

    def _load_image(self):
        try:
            self.pil_image = Image.open(self.image_path)
            w, h = self.pil_image.size
            self.title(f"{w}×{h} — {os.path.basename(self.image_path)}")
            self.after(50, self._open_sized_to_image)
        except Exception as e:
            err = self.lang.get_string("image_preview.error_loading").format(e)
            self.canvas.create_text(200, 200, text=err, fill='white')

    def _open_sized_to_image(self):
        if not self.pil_image:
            return
        self.update_idletasks()
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        max_canvas_w = max(480, int(screen_w * 0.92))
        max_canvas_h = max(360, int(screen_h * 0.88))
        fit_zoom = min(max_canvas_w / self.pil_image.width, max_canvas_h / self.pil_image.height, 1.0)
        display_w = max(1, int(self.pil_image.width * fit_zoom))
        display_h = max(1, int(self.pil_image.height * fit_zoom))
        win_w = display_w + 24
        win_h = display_h + 48
        x = max((screen_w - win_w) // 2, 0)
        y = max((screen_h - win_h) // 2, 0)
        self.geometry(f"{win_w}x{win_h}+{x}+{y}")
        self.after(10, self._fit_to_window)

    def _display(self):
        if not self.pil_image:
            return
        w = max(1, int(self.pil_image.width * self.zoom))
        h = max(1, int(self.pil_image.height * self.zoom))
        resized = self.pil_image.resize((w, h), Image.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        x = (cw - w) // 2 + self._pan_offset[0]
        y = (ch - h) // 2 + self._pan_offset[1]
        self.canvas.create_image(x, y, anchor=tk.NW, image=self.photo)
        try:
            iw, ih = self.pil_image.size
            pct = int(self.zoom * 100)
            self.title(f"{iw}×{ih} — {os.path.basename(self.image_path)} — {pct}%")
        except Exception:
            pass

    def _fit_to_window(self):
        if not self.pil_image:
            return
        self.update_idletasks()
        cw = self.canvas.winfo_width() or 800
        ch = self.canvas.winfo_height() or 600
        self.zoom = min(cw / self.pil_image.width, ch / self.pil_image.height, 1.0)
        self._pan_offset = [0, 0]
        self._display()

    def _actual_size(self):
        self.zoom = 1.0
        self._pan_offset = [0, 0]
        self._display()

    def _on_mousewheel(self, event):
        if event.delta > 0:
            self.zoom = min(self.zoom * 1.12, 8.0)
        else:
            self.zoom = max(self.zoom / 1.12, 0.05)
        self._display()

    def _start_zoom_drag(self, event):
        self._zoom_drag_origin = (event.x_root, event.y_root)
        self._zoom_drag_start = self.zoom
        self._zoom_dragged = False

    def _on_zoom_drag(self, event):
        if self._zoom_drag_origin is None:
            return
        dx = event.x_root - self._zoom_drag_origin[0]
        dy = self._zoom_drag_origin[1] - event.y_root
        if abs(dx) > 5 or abs(dy) > 5:
            self._zoom_dragged = True
        if self._zoom_dragged:
            factor = max(0.1, 1.0 + ((dx + dy) / 320.0))
            self.zoom = min(max(self._zoom_drag_start * factor, 0.05), 8.0)
            self._display()

    def _end_zoom_drag(self, event):
        dragged = self._zoom_dragged
        self._zoom_drag_origin = None
        self._zoom_dragged = False
        if not dragged:
            self._show_context_menu(event)

    def _show_context_menu(self, event):
        menu = tk.Menu(self, tearoff=0)
        style_menu(menu, dark=self._dark_mode)
        menu.add_command(
            label=self.lang.get_string("image_preview.menu_fit_to_window"),
            command=self._fit_to_window,
        )
        menu.add_command(
            label=self.lang.get_string("image_preview.menu_actual_size"),
            command=self._actual_size,
        )
        menu.add_separator()
        hint = self.lang.get_string("image_preview.hint")
        for part in hint.split('  •  '):
            menu.add_command(label=part.strip(), state='disabled')
        menu.tk_popup(event.x_root, event.y_root)

    def _start_pan(self, event):
        self._pan_start = (event.x, event.y)
        self._pan_start_offset = list(self._pan_offset)

    def _on_pan(self, event):
        if self._pan_start is None:
            return
        self._pan_offset[0] = self._pan_start_offset[0] + (event.x - self._pan_start[0])
        self._pan_offset[1] = self._pan_start_offset[1] + (event.y - self._pan_start[1])
        self._display()

    def _end_pan(self, event):
        self._pan_start = None


# ---------------------------------------------------------------------------
# Modern Slider widget
# ---------------------------------------------------------------------------

class ModernSlider(tk.Canvas):
    """Horizontal slider with a circular knob, filled track, and hover tooltip."""

    TRACK_H = 5
    KNOB_R = 11
    TRACK_BG = '#2a2b40'
    TRACK_FILL = '#4a6eff'
    KNOB_COLOR = '#dde0f8'
    KNOB_BORDER = '#6677cc'

    def __init__(self, parent, from_=0, to=100, resolution=1, variable=None, command=None, dark_mode=True, **kwargs):
        if dark_mode:
            self.TRACK_BG = '#2a2b40'
            self.TRACK_FILL = '#4a6eff'
            self.KNOB_COLOR = '#dde0f8'
            self.KNOB_BORDER = '#6677cc'
            kwargs.setdefault('bg', '#18192a')
        else:
            self.TRACK_BG = '#c8c8d8'
            self.TRACK_FILL = '#4a6eff'
            self.KNOB_COLOR = '#f5f5f8'
            self.KNOB_BORDER = '#9999bb'
            kwargs.setdefault('bg', 'SystemButtonFace')
        h = (self.KNOB_R + 4) * 2
        super().__init__(parent, height=h, highlightthickness=0, **kwargs)
        self._from = float(from_)
        self._to = float(to)
        self._resolution = float(resolution)
        self._command = command
        self._dragging = False
        self._tooltip_win = None
        self._tooltip_label = None

        self._var = variable if variable is not None else tk.IntVar(value=int(from_))

        self.bind('<Configure>', lambda e: self.after(0, self._draw))
        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<Enter>', self._show_tooltip)
        self.bind('<Leave>', self._hide_tooltip)
        self.bind('<Motion>', self._on_motion)
        self._var.trace_add('write', lambda *a: self.after(0, self._draw))

    def get(self):
        return self._var.get()

    def set(self, value):
        stepped = round(float(value) / self._resolution) * self._resolution
        self._var.set(int(max(self._from, min(self._to, stepped))))

    def _value_to_x(self, value):
        w = self.winfo_width()
        pad = self.KNOB_R + 2
        track_w = max(1.0, w - pad * 2)
        ratio = (float(value) - self._from) / max(1.0, self._to - self._from)
        return pad + ratio * track_w

    def _x_to_value(self, x):
        w = self.winfo_width()
        pad = self.KNOB_R + 2
        track_w = max(1.0, w - pad * 2)
        ratio = max(0.0, min(1.0, (x - pad) / track_w))
        raw = self._from + ratio * (self._to - self._from)
        stepped = round(raw / self._resolution) * self._resolution
        return int(max(self._from, min(self._to, stepped)))

    def _rounded_rect(self, x1, y1, x2, y2, r, **kw):
        r = min(r, max(1, int((x2 - x1) / 2)), max(1, int((y2 - y1) / 2)))
        pts = [
            x1 + r, y1,  x2 - r, y1,
            x2, y1,      x2, y1 + r,
            x2, y2 - r,  x2, y2,
            x2 - r, y2,  x1 + r, y2,
            x1, y2,      x1, y2 - r,
            x1, y1 + r,  x1, y1,
        ]
        return self.create_polygon(pts, smooth=True, **kw)

    def _draw(self):
        self.delete('all')
        w = self.winfo_width()
        h = self.winfo_height()
        if w < 8 or h < 8:
            return
        cy = h // 2
        pad = self.KNOB_R + 2
        ty1 = cy - self.TRACK_H // 2
        ty2 = cy + self.TRACK_H // 2

        self._rounded_rect(pad, ty1, w - pad, ty2, self.TRACK_H // 2,
                           fill=self.TRACK_BG, outline='')

        value = self._var.get()
        kx = self._value_to_x(value)
        if kx > pad + 1:
            self._rounded_rect(pad, ty1, kx, ty2, self.TRACK_H // 2,
                               fill=self.TRACK_FILL, outline='')

        r = self.KNOB_R
        self.create_oval(kx - r + 1, cy - r + 2, kx + r + 1, cy + r + 2,
                         fill='#0d0e1a', outline='')
        self.create_oval(kx - r, cy - r, kx + r, cy + r,
                         fill=self.KNOB_COLOR, outline=self.KNOB_BORDER, width=1.5)
        hi_r = max(2, r // 3)
        self.create_oval(kx - hi_r, cy - hi_r - 2, kx + hi_r, cy - 2,
                         fill='#ffffff', outline='')

    def _fire(self, value):
        if self._command:
            self._command(str(value))

    def _on_press(self, event):
        self._dragging = True
        v = self._x_to_value(event.x)
        self._var.set(v)
        self._update_tooltip_text(v)

    def _on_drag(self, event):
        if not self._dragging:
            return
        v = self._x_to_value(event.x)
        self._var.set(v)
        self._update_tooltip_text(v)

    def _on_release(self, event):
        if self._dragging:
            v = self._x_to_value(event.x)
            self._var.set(v)
            self._fire(v)
        self._dragging = False

    def _show_tooltip(self, event=None):
        if self._tooltip_win:
            return
        value = self._var.get()
        self._tooltip_win = tk.Toplevel(self)
        self._tooltip_win.wm_overrideredirect(True)
        x = (event.x_root + 12) if event else (self.winfo_rootx() + 12)
        y = (event.y_root - 34) if event else (self.winfo_rooty() - 34)
        self._tooltip_win.wm_geometry(f"+{x}+{y}")
        self._tooltip_label = ttk.Label(
            self._tooltip_win, text=f"{value} px",
            relief='solid', borderwidth=1, padding=(5, 3))
        self._tooltip_label.pack()

    def _hide_tooltip(self, event=None):
        if self._tooltip_win:
            try:
                self._tooltip_win.destroy()
            except Exception:
                pass
        self._tooltip_win = None
        self._tooltip_label = None

    def _on_motion(self, event):
        if self._tooltip_win:
            self._tooltip_win.wm_geometry(f"+{event.x_root + 12}+{event.y_root - 34}")
            self._update_tooltip_text(self._var.get())

    def _update_tooltip_text(self, value):
        if self._tooltip_label:
            try:
                self._tooltip_label.config(text=f"{value} px")
            except Exception:
                pass


class ImageBrowser(tk.Toplevel):
    def __init__(self, parent, image_paths, lang, search_term="", config=None, dark_mode=True):
        super().__init__(parent)
        self.image_paths = list(image_paths)
        self.lang = lang
        self.search_term = (search_term or "").strip()
        self._config = config
        self._dark_mode = dark_mode
        self.selected = set()
        self.thumbnail_cache = {}
        self.photo_cache = {}
        saved_size = 700
        if config:
            try:
                saved_size = int(config.get("Interface", "browser_thumbnail_size", "700"))
                saved_size = max(80, min(1200, saved_size))
            except (ValueError, TypeError):
                saved_size = 700
        self.thumbnail_size = saved_size
        self.cell_data = []
        self._scale_job = None
        self._mousewheel_bound = False
        self._mmb_origin_y = None
        self._mmb_current_y = None
        self._mmb_active = False
        self._rounded_mask_cache = {}
        self._padding = 1
        self._label_height = 18
        self._spinner_angle = 0
        self._spinner_job = None
        self._spinner_arc = None
        self._spinner_text = None

        self.configure(bg=_DARK['bg'])

        try:
            self.iconbitmap(resource_path('app_icon.ico'))
        except Exception:
            pass

        self.geometry("1200x820")
        self._setup_ui()
        self.scale_var.set(saved_size)
        self._update_status()
        self.after(0, self._maximize_window)
        self._start_loading()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _maximize_window(self):
        try:
            if sys.platform == 'win32':
                from ctypes import wintypes
                rc = wintypes.RECT()
                # SPI_GETWORKAREA: screen area minus taskbar
                ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rc), 0)
                self.geometry(f"{rc.right - rc.left}x{rc.bottom - rc.top}+{rc.left}+{rc.top}")
            else:
                self.state('zoomed')
        except Exception:
            try:
                self.state('zoomed')
            except Exception:
                pass

    def _on_close(self):
        if self._config:
            self._config.set("Interface", "browser_thumbnail_size", str(self.thumbnail_size))
            self._config.save_config()
        self.destroy()

    def _setup_ui(self):
        top = ttk.Frame(self, padding=(4, 4, 4, 4))
        top.pack(fill=tk.X)
        top.columnconfigure(0, weight=1)

        self.scale_var = tk.IntVar(value=700)
        self.scale_widget = ModernSlider(
            top,
            from_=80,
            to=1200,
            resolution=10,
            variable=self.scale_var,
            command=self._on_scale_change,
            dark_mode=self._dark_mode,
        )
        self.scale_widget.grid(row=0, column=0, sticky='ew', padx=(4, 4), pady=2)

        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X)

        container = ttk.Frame(self)
        container.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(container, bg='#18192a', highlightthickness=0)
        v_scroll = ttk.Scrollbar(container, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=v_scroll.set)
        v_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.canvas.bind('<Configure>', self._on_canvas_resize)
        self.canvas.bind('<Enter>', self._bind_mousewheel)
        self.canvas.bind('<Leave>', self._unbind_mousewheel)
        self.canvas.bind('<ButtonPress-2>', self._start_mmb_scroll)
        self.canvas.bind('<B2-Motion>', self._on_mmb_scroll)
        self.canvas.bind('<ButtonRelease-2>', self._end_mmb_scroll)
        self.bind('<Escape>', lambda e: self.destroy())
        self.bind('<Prior>', lambda e: self.canvas.yview_scroll(-1, 'pages'))
        self.bind('<Next>', lambda e: self.canvas.yview_scroll(1, 'pages'))

    def _bind_mousewheel(self, event=None):
        if self._mousewheel_bound:
            return
        self.bind_all('<MouseWheel>', self._on_mousewheel)
        self._mousewheel_bound = True

    def _unbind_mousewheel(self, event=None):
        current = self.winfo_containing(self.winfo_pointerx(), self.winfo_pointery())
        if current and str(current).startswith(str(self)):
            return
        if not self._mousewheel_bound:
            return
        self.unbind_all('<MouseWheel>')
        self._mousewheel_bound = False

    def _on_mousewheel(self, event):
        try:
            if event.widget.winfo_toplevel() is not self:
                return
        except Exception:
            return
        if event.state & 0x4:
            direction = 1 if event.delta > 0 else -1
            current = int(self.scale_var.get())
            new_value = max(80, min(1200, current + direction * 10))
            self.scale_var.set(new_value)
            self._apply_scale_change()
            return
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), 'units')

    def _on_canvas_resize(self, event=None):
        self._layout_cells()

    def _on_scale_change(self, value):
        if self._scale_job is not None:
            self.after_cancel(self._scale_job)
        self._scale_job = self.after(60, self._apply_scale_change)

    def _apply_scale_change(self, event=None):
        if self._scale_job is not None:
            self.after_cancel(self._scale_job)
            self._scale_job = None
        size = int(self.scale_var.get())
        if size != self.thumbnail_size:
            self.thumbnail_size = size
            self.photo_cache.clear()
        self._build_cells()

    def _start_mmb_scroll(self, event):
        self._mmb_origin_y = event.y_root
        self._mmb_current_y = event.y_root
        self._mmb_active = True
        self.canvas.config(cursor='sb_v_double_arrow')
        self._mmb_tick()

    def _on_mmb_scroll(self, event):
        if self._mmb_active:
            self._mmb_current_y = event.y_root

    def _end_mmb_scroll(self, event):
        self._mmb_active = False
        self._mmb_origin_y = None
        self.canvas.config(cursor='')

    def _mmb_tick(self):
        if not self._mmb_active:
            return
        dy = self._mmb_current_y - self._mmb_origin_y
        dead = 8
        if abs(dy) > dead:
            direction = 1 if dy > 0 else -1
            distance = abs(dy) - dead
            # Power curve: smooth ramp-up, max 84 px/tick (~5040 px/sec at 60fps)
            px = min(math.pow(distance / 80.0, 1.7) * 84.0, 84.0) * direction
            bbox = self.canvas.bbox('all')
            total_h = max(1, (bbox[3] - bbox[1]) if bbox else 1)
            self.canvas.yview_moveto(
                max(0.0, min(1.0, self.canvas.yview()[0] + px / total_h))
            )
        self.after(16, self._mmb_tick)

    def _start_loading(self):
        self.after(20, self._show_spinner)
        threading.Thread(target=self._load_all, daemon=True).start()

    def _show_spinner(self):
        self._spinner_r = 42  # radius (32 * 1.3 ≈ 42)
        self._spinner_arc = self.canvas.create_arc(
            0, 0, 1, 1,  # positioned each frame in _animate_spinner
            start=0, extent=280, outline='#4a6eff', style='arc', width=4,
        )
        self._spinner_text = self.canvas.create_text(
            0, 0, text="Loading…", fill='#8890b8', font=('Segoe UI', 11),
        )
        self._animate_spinner()

    def _animate_spinner(self):
        if self._spinner_arc is None:
            return
        try:
            cw = self.canvas.winfo_width() or 800
            ch = self.canvas.winfo_height() or 600
            cx, cy = cw // 2, ch // 2
            r = self._spinner_r
            self.canvas.coords(self._spinner_arc, cx - r, cy - r, cx + r, cy + r)
            self.canvas.coords(self._spinner_text, cx, cy + r + 24)
            self._spinner_angle = (self._spinner_angle + 9) % 360
            self.canvas.itemconfig(self._spinner_arc, start=self._spinner_angle)
            self._spinner_job = self.after(28, self._animate_spinner)
        except Exception:
            pass

    def _hide_spinner(self):
        if self._spinner_job is not None:
            try:
                self.after_cancel(self._spinner_job)
            except Exception:
                pass
            self._spinner_job = None
        for attr in ('_spinner_arc', '_spinner_text'):
            item = getattr(self, attr, None)
            if item is not None:
                try:
                    self.canvas.delete(item)
                except Exception:
                    pass
                setattr(self, attr, None)

    def _load_all(self):
        for path in self.image_paths:
            try:
                img = Image.open(path)
                img.load()
                self.thumbnail_cache[path] = img.convert('RGBA')
            except Exception:
                self.thumbnail_cache[path] = None
        self.after(0, self._build_cells)

    def _get_photo(self, path):
        size = max(32, self.thumbnail_size)
        key = (path, size)
        if key in self.photo_cache:
            return self.photo_cache[key]
        src = self.thumbnail_cache.get(path)
        if src is None:
            return None
        scale = size / src.height
        target_w = max(1, int(src.width * scale))
        target_h = size
        img = src.resize((target_w, target_h), Image.LANCZOS)
        radius = max(8, min(18, target_h // 10))
        mask_key = (target_w, target_h, radius)
        mask = self._rounded_mask_cache.get(mask_key)
        if mask is None:
            from PIL import ImageDraw
            mask = Image.new('L', (target_w, target_h), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, target_w, target_h), radius=radius, fill=255)
            self._rounded_mask_cache[mask_key] = mask
        rounded = Image.new('RGBA', (target_w, target_h), (0, 0, 0, 0))
        rounded.paste(img, (0, 0), mask)
        photo = ImageTk.PhotoImage(rounded)
        result = (photo, target_w, target_h, radius)
        self.photo_cache[key] = result
        return result

    def _truncate_filename(self, path, max_chars):
        name = os.path.basename(path)
        if len(name) <= max_chars:
            return name
        return name[:max(1, max_chars - 1)] + "…"

    def _build_cells(self):
        self._hide_spinner()
        self.canvas.delete("all")
        self.cell_data = []
        font_size = max(9, min(13, self.thumbnail_size // 18))
        for idx, path in enumerate(self.image_paths):
            bg_id = self.canvas.create_rectangle(0, 0, 0, 0, fill='#18192a', outline='', width=0)
            photo_info = self._get_photo(path)
            if photo_info:
                photo, width, height, img_radius = photo_info
                image_id = self.canvas.create_image(0, 0, anchor=tk.NW, image=photo)
            else:
                photo = None
                width = max(40, int(self.thumbnail_size * 0.7))
                height = max(32, self.thumbnail_size)
                img_radius = max(8, min(18, height // 10))
                image_id = self.canvas.create_rectangle(0, 0, width, height, fill='#2a2b40', outline='')
            sel_id = self.canvas.create_polygon(
                [0, 0, 1, 0, 1, 1, 0, 1], smooth=True, fill='', outline='', width=4
            )
            text_id = self.canvas.create_text(
                0, 0, anchor=tk.NW, text="", fill='#ccccdd', font=('Segoe UI', font_size)
            )
            for item_id in (bg_id, image_id, sel_id, text_id):
                self.canvas.tag_bind(item_id, '<Button-1>', lambda e, i=idx: self._on_click(e, i))
                self.canvas.tag_bind(item_id, '<Double-Button-1>', lambda e, i=idx: self._on_double_click(i))
                self.canvas.tag_bind(item_id, '<Button-3>', lambda e, i=idx: self._on_right_click(e, i))
            self.cell_data.append({
                'bg': bg_id,
                'image': image_id,
                'sel': sel_id,
                'text': text_id,
                'path': path,
                'idx': idx,
                'photo': photo,
                'img_w': width,
                'img_h': height,
                'img_radius': img_radius,
            })
        self._layout_cells()
        self._refresh_highlights()
        self._update_status()

    def _layout_cells(self):
        if not self.cell_data:
            self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), self.canvas.winfo_height()))
            return

        y = self._padding
        row = []
        row_width = 0
        max_width = max(120, self.canvas.winfo_width() - 8)
        row_height = self.thumbnail_size + self._label_height + (self._padding * 3)

        for cell in self.cell_data:
            photo_info = self._get_photo(cell['path'])
            if photo_info:
                photo, img_w, img_h, img_radius = photo_info
                cell['photo'] = photo
                cell['img_w'] = img_w
                cell['img_h'] = img_h
                cell['img_radius'] = img_radius
                self.canvas.itemconfig(cell['image'], image=photo)
            item_width = cell['img_w'] + (self._padding * 2)
            if row and row_width + item_width > max_width:
                self._place_row(row, y, max_width, row_width, is_last=False)
                y += row_height
                row = []
                row_width = 0
            row.append(cell)
            row_width += item_width

        if row:
            self._place_row(row, y, max_width, row_width, is_last=True)
            y += row_height

        self.canvas.configure(scrollregion=(0, 0, max_width, y + self._padding))

    def _sel_polygon_pts(self, x1, y1, x2, y2, r):
        return [
            x1+r, y1, x2-r, y1,
            x2, y1, x2, y1+r,
            x2, y2-r, x2, y2,
            x2-r, y2, x1+r, y2,
            x1, y2, x1, y2-r,
            x1, y1+r, x1, y1,
        ]

    def _place_row(self, row, y, available_width, row_width, is_last=False):
        if is_last or len(row) <= 1:
            gap = self._padding
        else:
            extra = max(0, available_width - row_width)
            gap = self._padding + (extra / len(row))
        x = self._padding
        font_size = max(9, min(13, self.thumbnail_size // 18))

        for cell in row:
            img_w = cell['img_w']
            img_h = cell['img_h']
            self.canvas.coords(cell['bg'], x - 1, y - 1, x + img_w + 1, y + img_h + self._label_height + 1)
            self.canvas.coords(cell['image'], x, y)
            # B-spline smooth=True renders corners at ~70% of the r vertex distance.
            # Multiply the PIL radius by 1/0.7 ≈ 1.43, then add 20% extra rounding.
            img_r = cell.get('img_radius', max(8, min(18, img_h // 10)))
            r = int(img_r * 2.471) + 2
            pts = self._sel_polygon_pts(x - 2, y - 2, x + img_w + 2, y + img_h + 2, r)
            self.canvas.coords(cell['sel'], *pts)
            text = self._truncate_filename(cell['path'], max(10, img_w // 9))
            self.canvas.itemconfig(cell['text'], text=text, font=('Segoe UI', font_size), width=0)
            self.canvas.coords(cell['text'], x, y + img_h + 1)
            x += img_w + gap

    def _on_click(self, event, idx):
        ctrl = bool(event.state & 0x4)
        if ctrl:
            if idx in self.selected:
                self.selected.discard(idx)
            else:
                self.selected.add(idx)
        else:
            self.selected = {idx}
        self._refresh_highlights()
        self._update_status()

    def _refresh_highlights(self):
        for cell in self.cell_data:
            if cell['idx'] in self.selected:
                self.canvas.itemconfig(cell['sel'], outline='#ffffff', width=4)
            else:
                self.canvas.itemconfig(cell['sel'], outline='', width=0)

    def _update_status(self):
        total = len(self.image_paths)
        sel = len(self.selected)
        count_str = f"{total} image{'s' if total != 1 else ''}"
        sel_str = f" - {sel} selected" if sel else ""
        term_str = f" - {self.search_term}" if self.search_term else ""
        self.title(f"Metadata Image Search{term_str} - {count_str}{sel_str}")

    def _on_double_click(self, idx):
        ImagePreview(self, self.image_paths[idx], self.lang, dark_mode=self._dark_mode)

    def _on_right_click(self, event, idx):
        if len(self.selected) <= 1:
            self.selected = {idx}
            self._refresh_highlights()
            self._update_status()

        paths = [self.image_paths[i] for i in sorted(self.selected)]
        multi = len(paths) > 1
        lang = self.lang

        menu = tk.Menu(self, tearoff=0)
        style_menu(menu, dark=self._dark_mode)

        if not multi:
            menu.add_command(
                label=lang.get_string("image_browser.menu_open_preview"),
                command=lambda: ImagePreview(self, paths[0], self.lang, dark_mode=self._dark_mode))
            menu.add_command(
                label=lang.get_string("image_browser.menu_open_default"),
                command=lambda: os.startfile(paths[0]))
            menu.add_command(
                label=lang.get_string("image_browser.menu_open_folder"),
                command=lambda: os.startfile(os.path.dirname(paths[0])))
            menu.add_separator()
            menu.add_command(
                label=lang.get_string("image_browser.menu_copy_path"),
                command=lambda: self._copy_text(paths[0]))
        else:
            n_prev = min(len(paths), 5)
            menu.add_command(
                label=lang.get_string("image_browser.menu_preview_n").format(n_prev),
                command=lambda: [ImagePreview(self, p, self.lang, dark_mode=self._dark_mode) for p in paths[:5]])
            menu.add_command(
                label=lang.get_string("image_browser.menu_open_folder"),
                command=lambda: os.startfile(os.path.dirname(paths[0])))
            menu.add_separator()
            menu.add_command(
                label=lang.get_string("image_browser.menu_copy_paths"),
                command=lambda: self._copy_text('\n'.join(paths)))

        menu.add_command(
            label=lang.get_string("image_browser.menu_copy_files"),
            command=lambda: self._copy_files_to_clipboard(paths))
        menu.add_separator()
        del_label = (
            lang.get_string("image_browser.menu_delete_many").format(len(paths))
            if multi else
            lang.get_string("image_browser.menu_delete_one")
        )
        menu.add_command(label=del_label, foreground='#ff6060',
                         command=lambda: self._delete_images(paths))

        menu.tk_popup(event.x_root, event.y_root)

    def _copy_text(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _copy_files_to_clipboard(self, paths):
        try:
            CF_HDROP = 15
            GHND = 0x0042
            file_list = '\0'.join(paths) + '\0\0'
            file_bytes = file_list.encode('utf-16-le')
            dropfiles = struct.pack('<IIIII', 20, 0, 0, 0, 1)
            data = dropfiles + file_bytes
            h = ctypes.windll.kernel32.GlobalAlloc(GHND, len(data))
            p = ctypes.windll.kernel32.GlobalLock(h)
            ctypes.memmove(p, data, len(data))
            ctypes.windll.kernel32.GlobalUnlock(h)
            ctypes.windll.user32.OpenClipboard(0)
            ctypes.windll.user32.EmptyClipboard()
            ctypes.windll.user32.SetClipboardData(CF_HDROP, h)
            ctypes.windll.user32.CloseClipboard()
        except Exception:
            self._copy_text('\n'.join(paths))

    def _delete_images(self, paths):
        n = len(paths)
        msg_key = "image_browser.confirm_delete_one" if n == 1 else "image_browser.confirm_delete_many"
        msg = self.lang.get_string(msg_key).format(n)
        if not messagebox.askyesno(
                self.lang.get_string("image_browser.confirm_delete_title"),
                msg,
                parent=self):
            return
        for path in paths:
            try:
                os.remove(path)
            except Exception as e:
                messagebox.showerror(
                    self.lang.get_string("image_browser.delete_error_title"),
                    self.lang.get_string("image_browser.delete_error_message").format(path, e),
                    parent=self)
            if path in self.image_paths:
                self.image_paths.remove(path)
            self.thumbnail_cache.pop(path, None)
            for key in [k for k in self.photo_cache if k[0] == path]:
                del self.photo_cache[key]
        self.selected.clear()
        self._build_cells()

class SearchGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.search_thread = None
        self.search_active = False
        self._closing = False

        self.config = ConfigManagerMetadataSearch()
        initial_language = self.config.get("Interface", "language", "English")
        self.lang = LanguageManagerMetadataSearch("metadatasearch", initial_language)
        self._initial_language = initial_language

        self.dark_mode = tk.BooleanVar(
            value=self.config.get_bool("Interface", "dark_mode", False))
        if self.dark_mode.get():
            apply_dark_theme(self.root)

        self.root.title(self.lang.get_string("window.title"))
        self.root.geometry("990x620")

        # Application icon
        self._app_icon_photo = None
        try:
            self.root.iconbitmap(resource_path('app_icon.ico'))
        except Exception:
            try:
                self._app_icon_photo = tk.PhotoImage(file=resource_path('app_icon.png'))
                self.root.iconphoto(True, self._app_icon_photo)
            except Exception:
                pass

        self.browse_buttons = []

        # ── Main frame ───────────────────────────────────────────────────
        self.main_frame = ttk.Frame(self.root, padding="10")
        self.main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # Folder selection
        folder_label = ttk.Label(self.main_frame,
                                  text=self.lang.get_string("labels.folder_path"),
                                  width=20, anchor='e')
        folder_label.grid(row=0, column=0, sticky=tk.W)
        self._add_tooltip(folder_label, "folder_path")

        self.folder_path = tk.StringVar(value=self.config.get("Paths", "default_search_folder", ""))
        self.folder_entry = ttk.Entry(self.main_frame, textvariable=self.folder_path)
        self.folder_entry.grid(row=0, column=1, columnspan=2, sticky=(tk.W, tk.E))

        self.browse_button = ttk.Button(
            self.main_frame, text=self.lang.get_string("buttons.browse"),
            command=self.browse_folder, width=20)
        self.browse_button.grid(row=0, column=3, padx=5)
        self.browse_buttons.append(self.browse_button)

        # Search term
        search_label = ttk.Label(self.main_frame,
                                  text=self.lang.get_string("labels.search_term"),
                                  width=20, anchor='e')
        search_label.grid(row=1, column=0, sticky=tk.W)
        self._add_tooltip(search_label, "search_term")

        self.search_term = tk.StringVar(value=self.config.get("Search", "search_term", ""))
        ttk.Entry(self.main_frame, textvariable=self.search_term).grid(
            row=1, column=1, columnspan=2, sticky=(tk.W, tk.E))

        # Ignore term
        ignore_label = ttk.Label(self.main_frame,
                                  text=self.lang.get_string("labels.ignore_term"),
                                  width=20, anchor='e')
        ignore_label.grid(row=2, column=0, sticky=tk.W)
        self._add_tooltip(ignore_label, "ignore_term")

        self.ignore_term = tk.StringVar(value=self.config.get("Search", "ignore_term", ""))
        ttk.Entry(self.main_frame, textvariable=self.ignore_term).grid(
            row=2, column=1, columnspan=2, sticky=(tk.W, tk.E))

        # ── Options frame ────────────────────────────────────────────────
        options_frame = ttk.LabelFrame(
            self.main_frame, text=self.lang.get_string("frames.options"), padding="5")
        options_frame.grid(row=3, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=10)
        options_frame.columnconfigure(1, weight=1)
        options_frame.frame_id = "options"

        # Row 0 checkboxes
        checkbox_frame = ttk.Frame(options_frame)
        checkbox_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E))
        self._checkbox_frame = checkbox_frame

        self.recursive = tk.BooleanVar(value=self.config.get_bool("Search", "recursive", True))
        rc = ttk.Checkbutton(checkbox_frame, text=self.lang.get_string("checkboxes.recursive.text"),
                             variable=self.recursive)
        rc.grid(row=0, column=0, sticky=tk.W, padx=5)
        self._add_tooltip(rc, "recursive")

        self.log_enabled = tk.BooleanVar(value=self.config.get_bool("Output", "enable_logging", False))
        lc = ttk.Checkbutton(checkbox_frame, text=self.lang.get_string("checkboxes.logging.text"),
                             variable=self.log_enabled)
        lc.grid(row=0, column=1, sticky=tk.W, padx=5)
        self._add_tooltip(lc, "logging")

        self.match_folder_structure = tk.BooleanVar(
            value=self.config.get_bool("Output", "match_folder_structure", True))
        mc = ttk.Checkbutton(checkbox_frame,
                             text=self.lang.get_string("checkboxes.match_structure.text"),
                             variable=self.match_folder_structure)
        mc.grid(row=0, column=2, sticky=tk.W, padx=5)
        self._add_tooltip(mc, "match_structure")

        self.create_or_subfolders = tk.BooleanVar(
            value=self.config.get_bool("Output", "create_or_subfolders", False))
        oc = ttk.Checkbutton(checkbox_frame,
                             text=self.lang.get_string("checkboxes.or_subfolders.text"),
                             variable=self.create_or_subfolders)
        oc.grid(row=0, column=3, sticky=tk.W, padx=5)
        self._add_tooltip(oc, "or_subfolders")

        # Row 1 checkboxes
        self.search_positive = tk.BooleanVar(value=self.config.get_bool("Search", "search_positive", True))
        pc = ttk.Checkbutton(checkbox_frame,
                             text=self.lang.get_string("checkboxes.search_positive.text"),
                             variable=self.search_positive)
        pc.grid(row=1, column=0, sticky=tk.W, padx=5)
        self._add_tooltip(pc, "search_positive")

        self.search_negative = tk.BooleanVar(value=self.config.get_bool("Search", "search_negative", False))
        nc = ttk.Checkbutton(checkbox_frame,
                             text=self.lang.get_string("checkboxes.search_negative.text"),
                             variable=self.search_negative)
        nc.grid(row=1, column=1, sticky=tk.W, padx=5)
        self._add_tooltip(nc, "search_negative")

        self.case_sensitive = tk.BooleanVar(value=self.config.get_bool("Search", "case_sensitive", False))
        cc = ttk.Checkbutton(checkbox_frame,
                             text=self.lang.get_string("checkboxes.case_sensitive.text"),
                             variable=self.case_sensitive)
        cc.grid(row=1, column=2, sticky=tk.W, padx=5)
        self._add_tooltip(cc, "case_sensitive")

        self.dark_mode_cb = ttk.Checkbutton(
            checkbox_frame,
            text=self.lang.get_string("checkboxes.dark_mode.text"),
            variable=self.dark_mode,
            command=self._toggle_dark_mode)
        self.dark_mode_cb.grid(row=1, column=3, sticky=tk.W, padx=5)
        self._add_tooltip(self.dark_mode_cb, "dark_mode")

        # ── Language selector (col 3 = same column as Browse buttons) ────────
        self._lang_display_to_code = {}
        _all_langs = self.lang.get_languages()
        _ordered = ['English'] + sorted([l for l in _all_langs if l != 'English'])
        _lang_display = []
        for _lc in _ordered:
            if _lc not in _all_langs:
                continue
            _name = self._get_language_name(_lc)
            self._lang_display_to_code[_name] = _lc
            _lang_display.append(_name)

        lang_frame = ttk.Frame(options_frame)
        lang_frame.grid(row=0, column=3, sticky=(tk.N, tk.S, tk.E, tk.W), padx=5)

        self.language_label_widget = ttk.Label(
            lang_frame, text=self.lang.get_string("labels.language"), anchor='w')
        self.language_label_widget.pack(anchor='w', side=tk.TOP, pady=(0, 2))

        self.language_combo = ttk.Combobox(
            lang_frame, values=_lang_display, state='readonly')
        _current_display = self._get_language_name(initial_language)
        self.language_combo.set(_current_display)
        self.language_combo.pack(fill=tk.X, side=tk.TOP, padx=(0, 30))
        self.language_combo.bind('<<ComboboxSelected>>', self._on_language_combo_change)

        # ── Copy To (checkbox + entry + browse) ───────────────────────────
        self.copy_enabled = tk.BooleanVar(
            value=self.config.get_bool("Output", "copy_enabled", False))
        self.copy_cb = ttk.Checkbutton(
            self.main_frame,
            text=self.lang.get_string("labels.copy_to"),
            variable=self.copy_enabled)
        self.copy_cb.grid(row=4, column=0, sticky=tk.W, pady=2)
        self._add_tooltip(self.copy_cb, "copy_to")

        self.copy_path = tk.StringVar(value=self.config.get("Paths", "default_copy_folder", ""))
        ttk.Entry(self.main_frame, textvariable=self.copy_path).grid(
            row=4, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=2)

        copy_browse = ttk.Button(self.main_frame, text=self.lang.get_string("buttons.browse"),
                                  command=lambda: self.browse_output("copy"), width=20)
        copy_browse.grid(row=4, column=3, padx=5, pady=2)
        self.browse_buttons.append(copy_browse)

        # ── Move To (checkbox + entry + browse) ───────────────────────────
        self.move_enabled = tk.BooleanVar(
            value=self.config.get_bool("Output", "move_enabled", False))
        self.move_cb = ttk.Checkbutton(
            self.main_frame,
            text=self.lang.get_string("labels.move_to"),
            variable=self.move_enabled)
        self.move_cb.grid(row=5, column=0, sticky=tk.W, pady=2)
        self._add_tooltip(self.move_cb, "move_to")

        self.move_path = tk.StringVar(value=self.config.get("Paths", "default_move_folder", ""))
        ttk.Entry(self.main_frame, textvariable=self.move_path).grid(
            row=5, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=2)

        move_browse = ttk.Button(self.main_frame, text=self.lang.get_string("buttons.browse"),
                                  command=lambda: self.browse_output("move"), width=20)
        move_browse.grid(row=5, column=3, padx=5, pady=2)
        self.browse_buttons.append(move_browse)

        # Regex filter
        regex_label = ttk.Label(self.main_frame, text=self.lang.get_string("labels.regex"),
                                 width=20, anchor='e')
        regex_label.grid(row=6, column=0, sticky=tk.W, pady=2)
        self._add_tooltip(regex_label, "regex_filter")

        self.custom_filter = ttk.Entry(self.main_frame)
        self.custom_filter.grid(row=6, column=1, columnspan=2, sticky=(tk.W, tk.E), pady=2)

        # ── Button row (Search + View) ────────────────────────────────────
        btn_frame = ttk.Frame(self.main_frame)
        btn_frame.grid(row=7, column=0, columnspan=4, pady=10)

        self.search_button = ttk.Button(
            btn_frame, text=self.lang.get_string("buttons.search"),
            command=self.start_search, width=30, padding=(10, 5))
        self.search_button.pack(side=tk.LEFT, padx=6)

        self.view_button = ttk.Button(
            btn_frame, text=self.lang.get_string("buttons.view_images"),
            command=self.open_image_browser, width=16, padding=(10, 5))
        self.view_button.pack(side=tk.LEFT, padx=6)
        self.view_button.state(['disabled'])

        # ── Progress ──────────────────────────────────────────────────────
        progress_frame = ttk.LabelFrame(
            self.main_frame, text=self.lang.get_string("frames.progress"), padding="5")
        progress_frame.grid(row=8, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=5)
        progress_frame.columnconfigure(0, weight=1)
        progress_frame.frame_id = "progress"

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(progress_frame, variable=self.progress_var, maximum=100)
        self.progress_bar.grid(row=0, column=0, sticky=(tk.W, tk.E), padx=5, pady=5)

        self.progress_label = ttk.Label(progress_frame, text=self.lang.get_string("progress.ready"))
        self.progress_label.grid(row=1, column=0, sticky=tk.W, padx=5)

        # ── Output text area ──────────────────────────────────────────────
        d = _DARK
        _oa_bg = d['bg3'] if self.dark_mode.get() else 'white'
        _oa_fg = d['fg'] if self.dark_mode.get() else 'black'
        _oa_sel = d['sel_bg'] if self.dark_mode.get() else '#0078d7'
        _oa_selfg = d['fg'] if self.dark_mode.get() else 'white'
        self.output_area = scrolledtext.ScrolledText(
            self.main_frame, height=15,
            bg=_oa_bg, fg=_oa_fg, insertbackground=_oa_fg,
            selectbackground=_oa_sel, selectforeground=_oa_selfg,
            relief='flat', borderwidth=1,
            font=('Consolas', 9),
        )
        self.output_area.grid(row=9, column=0, columnspan=4, sticky=(tk.W, tk.E, tk.N, tk.S))

        self.main_frame.columnconfigure(1, weight=1)
        self.main_frame.rowconfigure(9, weight=1)

        self._last_result_paths = []
        self.root.protocol("WM_DELETE_WINDOW", self._on_closing)

    # ── Tooltip ───────────────────────────────────────────────────────────

    def _add_tooltip(self, widget, key):
        widget.tooltip_key = key
        tooltip_text = self.lang.get_tooltip(key)
        if not tooltip_text:
            return
        widget.tooltip = tooltip_text

        def show(event):
            if hasattr(widget, '_tip_win'):
                widget._tip_win.destroy()
            tip = tk.Toplevel()
            tip.wm_overrideredirect(True)
            tip.wm_geometry(f"+{event.x_root+10}+{event.y_root+10}")
            ttk.Label(tip, text=widget.tooltip, justify='left',
                      relief='solid', borderwidth=1).pack()
            widget._tip_win = tip

        def hide(event):
            if hasattr(widget, '_tip_win'):
                widget._tip_win.destroy()
                del widget._tip_win

        widget.bind('<Enter>', show)
        widget.bind('<Leave>', hide)

    # ── Dark mode ─────────────────────────────────────────────────────────

    def _toggle_dark_mode(self):
        if self.dark_mode.get():
            apply_dark_theme(self.root)
            d = _DARK
            self.output_area.config(
                bg=d['bg3'], fg=d['fg'], insertbackground=d['fg'],
                selectbackground=d['sel_bg'], selectforeground=d['fg'])
        else:
            apply_light_theme(self.root)
            self.output_area.config(
                bg='white', fg='black', insertbackground='black',
                selectbackground='#0078d7', selectforeground='white')

    # ── Language ──────────────────────────────────────────────────────────

    def _get_language_name(self, lang_code):
        self.lang.set_language(lang_code)
        name = self.lang.get_string("language.name")
        self.lang.set_language(self._initial_language)
        return name

    def _on_language_combo_change(self, event=None):
        selected = self.language_combo.get()
        lang_code = self._lang_display_to_code.get(selected)
        if lang_code:
            self._on_language_change(lang_code)

    def _on_language_change(self, lang_code):
        self.lang.set_language(lang_code)
        self.config.set("Interface", "language", lang_code)
        self.config.save_config()
        self._update_gui_strings()

    def _update_gui_strings(self):
        self.root.title(self.lang.get_string("window.title"))

        def update_container(container):
            for widget in container.winfo_children():
                gi = widget.grid_info()

                if isinstance(widget, ttk.Label):
                    if container == self.main_frame:
                        if gi.get('row') == 0 and gi.get('column') == 0:
                            widget.config(text=self.lang.get_string("labels.folder_path"))
                        elif gi.get('row') == 1 and gi.get('column') == 0:
                            widget.config(text=self.lang.get_string("labels.search_term"))
                        elif gi.get('row') == 2 and gi.get('column') == 0:
                            widget.config(text=self.lang.get_string("labels.ignore_term"))
                        elif gi.get('row') == 6 and gi.get('column') == 0:
                            widget.config(text=self.lang.get_string("labels.regex"))

                elif isinstance(widget, ttk.Button):
                    if widget == self.search_button:
                        widget.config(text=self.lang.get_string("buttons.search"))
                    elif widget == self.view_button:
                        widget.config(text=self.lang.get_string("buttons.view_images"))
                    elif widget in self.browse_buttons:
                        widget.config(text=self.lang.get_string("buttons.browse"))

                elif isinstance(widget, ttk.Checkbutton):
                    row = gi.get('row', -1)
                    col = gi.get('column', 0)
                    if container is self._checkbox_frame:
                        if row == 0:
                            if col == 0:
                                widget.config(text=self.lang.get_string("checkboxes.recursive.text"))
                            elif col == 1:
                                widget.config(text=self.lang.get_string("checkboxes.logging.text"))
                            elif col == 2:
                                widget.config(text=self.lang.get_string("checkboxes.match_structure.text"))
                            elif col == 3:
                                widget.config(text=self.lang.get_string("checkboxes.or_subfolders.text"))
                        elif row == 1:
                            if col == 0:
                                widget.config(text=self.lang.get_string("checkboxes.search_positive.text"))
                            elif col == 1:
                                widget.config(text=self.lang.get_string("checkboxes.search_negative.text"))
                            elif col == 2:
                                widget.config(text=self.lang.get_string("checkboxes.case_sensitive.text"))
                            elif col == 3:
                                widget.config(text=self.lang.get_string("checkboxes.dark_mode.text"))
                    elif container is self.main_frame:
                        if row == 4 and col == 0:
                            widget.config(text=self.lang.get_string("labels.copy_to"))
                        elif row == 5 and col == 0:
                            widget.config(text=self.lang.get_string("labels.move_to"))

                elif isinstance(widget, ttk.LabelFrame):
                    if hasattr(widget, 'frame_id'):
                        widget.config(text=self.lang.get_string(f"frames.{widget.frame_id}"))
                    update_container(widget)
                elif isinstance(widget, (ttk.Frame, tk.Frame)):
                    update_container(widget)

        update_container(self.main_frame)
        self.language_label_widget.config(text=self.lang.get_string("labels.language"))
        self.progress_label.config(text=self.lang.get_string("progress.ready"))
        self._update_tooltips()

    def _update_tooltips(self):
        def update_tips(container):
            for widget in container.winfo_children():
                if hasattr(widget, 'tooltip_key'):
                    t = self.lang.get_tooltip(widget.tooltip_key)
                    if t:
                        widget.tooltip = t
                if isinstance(widget, (ttk.Frame, ttk.LabelFrame, tk.Frame)):
                    update_tips(widget)
        update_tips(self.main_frame)

    # ── File browser ──────────────────────────────────────────────────────

    def browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.folder_path.set(folder)

    def browse_output(self, output_type):
        folder = filedialog.askdirectory()
        if folder:
            if output_type == "copy":
                self.copy_path.set(folder)
            else:
                self.move_path.set(folder)

    # ── Output / progress ─────────────────────────────────────────────────

    def _run_on_ui_thread(self, callback, *args):
        if self._closing:
            return
        try:
            if threading.current_thread() is threading.main_thread():
                if self.root.winfo_exists():
                    callback(*args)
            else:
                self.root.after(0, lambda: self._run_on_ui_thread(callback, *args))
        except (RuntimeError, tk.TclError):
            pass

    def _set_search_controls(self, running):
        if running:
            self.search_button.state(['disabled'])
            self.view_button.state(['disabled'])
        else:
            self.search_button.state(['!disabled'])
            if self._last_result_paths:
                self.view_button.state(['!disabled'])

    def log_output(self, message):
        self._run_on_ui_thread(self._append_log_output, message)

    def _append_log_output(self, message):
        self.output_area.insert(tk.END, message + "\n")
        self.output_area.see(tk.END)
        self.root.update_idletasks()

    def update_progress(self, phase, current, total):
        self._run_on_ui_thread(self._update_progress_ui, phase, current, total)

    def _update_progress_ui(self, phase, current, total):
        if total > 0:
            pct = (current / total) * 100
            self.progress_var.set(pct)
            phase_text = self.lang.get_string(f"progress.{phase}")
            self.progress_label.config(text=f"{phase_text}: {current}/{total} ({pct:.1f}%)")
        self.root.update_idletasks()

    def _confirm_action(self, action_type):
        if action_type == "move":
            return messagebox.askyesno(
                self.lang.get_string("confirmations.move_title"),
                self.lang.get_string("confirmations.move_message"))
        return True

    # ── Search ────────────────────────────────────────────────────────────

    def start_search(self):
        self.output_area.delete(1.0, tk.END)
        self.progress_var.set(0)
        self.progress_label.config(text=self.lang.get_string("progress.starting"))
        self._set_search_controls(True)
        self._last_result_paths = []
        options = {
            "folder_path": self.folder_path.get(),
            "search_term": self.search_term.get(),
            "recursive": self.recursive.get(),
            "log_path": "logs" if self.log_enabled.get() else None,
            "copy_path": self.copy_path.get() if self.copy_enabled.get() else None,
            "move_path": self.move_path.get() if self.move_enabled.get() else None,
            "custom_filter": self.custom_filter.get() or None,
            "search_positive": self.search_positive.get(),
            "search_negative": self.search_negative.get(),
            "case_sensitive": self.case_sensitive.get(),
            "ignore_term": self.ignore_term.get() or None,
            "match_folder_structure": self.match_folder_structure.get(),
            "create_or_subfolders": self.create_or_subfolders.get(),
        }

        if options["move_path"] and not self._confirm_action("move"):
            self._set_search_controls(False)
            self.progress_label.config(text=self.lang.get_string("progress.ready"))
            return

        self.search_active = True
        self.search_thread = threading.Thread(target=self._run_search, args=(options,), daemon=True)
        self.search_thread.start()

    def _run_search(self, options):
        result_paths = []

        try:
            searcher = MetadataSearcher(
                search_term=options["search_term"],
                recursive=options["recursive"],
                log_path=options["log_path"],
                copy_path=options["copy_path"],
                move_path=options["move_path"],
                custom_filter=options["custom_filter"],
                search_positive=options["search_positive"],
                search_negative=options["search_negative"],
                case_sensitive=options["case_sensitive"],
                ignore_term=options["ignore_term"],
                lang=self.lang,
            )
            searcher.match_folder_structure = options["match_folder_structure"]
            searcher.create_or_subfolders = options["create_or_subfolders"]
            searcher.set_progress_callback(self.update_progress)

            original_log = searcher.log
            searcher.log = lambda msg: [original_log(msg), self.log_output(msg)]

            searcher.search_images(options["folder_path"])
            self.log_output("\n" + self.lang.get_string("progress.completed"))

            result_paths = list(searcher.output_paths)

        except Exception as e:
            self.log_output("\n" + self.lang.get_string("errors.search_error").format(str(e)))
        finally:
            self.search_active = False
            self._run_on_ui_thread(self._finish_search, result_paths)

    def _finish_search(self, result_paths):
        self._last_result_paths = result_paths
        self._set_search_controls(False)
        self.progress_label.config(text=self.lang.get_string("progress.ready"))

    # ── Image browser ─────────────────────────────────────────────────────

    def open_image_browser(self):
        if not self._last_result_paths:
            return
        browser = ImageBrowser(self.root, self._last_result_paths, self.lang,
                               self.search_term.get(), config=self.config,
                               dark_mode=self.dark_mode.get())
        browser.focus_set()

    # ── Close / tray ──────────────────────────────────────────────────────

    def _on_closing(self):
        self._closing = True
        self.config.set("Interface", "language", self.lang.current_language)
        self.config.set("Interface", "dark_mode", str(self.dark_mode.get()))
        self.config.set("Search", "recursive", str(self.recursive.get()))
        self.config.set("Search", "case_sensitive", str(self.case_sensitive.get()))
        self.config.set("Search", "search_positive", str(self.search_positive.get()))
        self.config.set("Search", "search_negative", str(self.search_negative.get()))
        self.config.set("Search", "search_term", self.search_term.get())
        self.config.set("Search", "ignore_term", self.ignore_term.get())
        self.config.set("Output", "match_folder_structure", str(self.match_folder_structure.get()))
        self.config.set("Output", "create_or_subfolders", str(self.create_or_subfolders.get()))
        self.config.set("Output", "enable_logging", str(self.log_enabled.get()))
        self.config.set("Output", "copy_enabled", str(self.copy_enabled.get()))
        self.config.set("Output", "move_enabled", str(self.move_enabled.get()))
        self.config.set("Paths", "default_search_folder", self.folder_path.get())
        self.config.set("Paths", "default_copy_folder", self.copy_path.get())
        self.config.set("Paths", "default_move_folder", self.move_path.get())
        self.config.save_config()
        if self.search_active and self.search_thread and self.search_thread.is_alive():
            os._exit(0)
        self.root.destroy()


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(description="Search PNG images for metadata matching a search term")
    parser.add_argument("--folder", help="Folder to search in")
    parser.add_argument("--term", help="Search term (supports wildcards)")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--log-path")
    parser.add_argument("--copy-to")
    parser.add_argument("--move-to")
    parser.add_argument("--filter")
    parser.add_argument("--case-sensitive", action="store_true")
    return parser.parse_args()


def main():
    multiprocessing.freeze_support()  # Required for PyInstaller + multiprocessing on Windows

    args = parse_args()
    if args.folder and args.term:
        lang = LanguageManagerMetadataSearch("metadatasearch", "English")
        searcher = MetadataSearcher(
            search_term=args.term,
            recursive=args.recursive,
            log_path=args.log_path,
            copy_path=args.copy_to,
            move_path=args.move_to,
            custom_filter=args.filter,
            case_sensitive=args.case_sensitive,
            lang=lang,
        )
        searcher.search_images(args.folder)
    else:
        gui = SearchGUI()
        gui.root.mainloop()


if __name__ == "__main__":
    main()
