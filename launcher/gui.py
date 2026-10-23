"""Tkinter GUI for PyVortex — a modern, professional launcher interface.

Dark, polished theme with a Minecraft-launcher feel.  Features:
  * Multi-account support (Microsoft premium + offline/cracked)
  * In-game avatar previews (player face) with async loading + cache
  * Configurable game directory, version downloader, settings,
    Java install flow and GameLauncher wiring.

Only standard-library tkinter/ttk is used.  No PNG/photo handlers are relied
upon for the avatar: faces are built pixel-by-pixel from RGB hex via
PhotoImage.put(), which works on every Tk build.
"""

import os
import sys
import json
import time
import math
import queue
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

from . import core
from . import auth
from . import avatar
from .game import GameLauncher
from .java import JavaManager, PREFERRED_DEFAULT

def _detect_app_dir():
    """Config lives in the vanilla game data directory (%APPDATA%/.minecraft).

    Sharing the standard .minecraft folder means the launcher reuses the
    same versions/mods/saves as the official Minecraft launcher, and avoids
    all detection issues with PyInstaller/Nuitka temp dirs.
    """
    return Path(os.environ.get("APPDATA", str(Path.home()))) / ".minecraft"

APP_DIR = _detect_app_dir()
APP_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_FILE = APP_DIR / "config.json"

# Default game data location (%APPDATA%\.minecraft), editable in Settings.
DEFAULT_GAME_DIR = Path(
    os.environ.get("APPDATA", str(Path.home()))) / ".minecraft"

# Migrate config/accounts from older builds that used %APPDATA%/PyVortex.
_OLD_APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "PyVortex"
if not CONFIG_FILE.exists() and (_OLD_APP_DIR / "config.json").exists():
    try:
        import shutil
        shutil.copyfile(_OLD_APP_DIR / "config.json", CONFIG_FILE)
        # Point a stale game_dir back at the standard .minecraft folder.
        _migrated = json.loads(CONFIG_FILE.read_text("utf-8-sig"))
        if Path(_migrated.get("game_dir", "")).name == "PyVortex":
            _migrated["game_dir"] = str(DEFAULT_GAME_DIR)
            CONFIG_FILE.write_text(
                json.dumps(_migrated, indent=2, ensure_ascii=False), "utf-8")
    except Exception:
        pass

DEFAULTS = {
    "name": "Player1",
    "ram_mb": core.DEFAULT_RAM_MB,
    "version": "",
    "game_dir": str(DEFAULT_GAME_DIR),
    "close_on_launch": True,
    "accounts": [],
    "selected_account": None,
}

# --- theme palette --------------------------------------------------------- #
FONT = "Segoe UI"
BG = "#15181c"            # deepest window background
PANEL = "#1e2228"         # section panels
CARD = "#262b33"          # cards / inputs
CARD_HOVER = "#303741"
OUTLINE = "#383f49"
TEXT = "#e8ecf1"
MUTED = "#8f99a5"
ACCENT = "#3ecf8e"        # primary green (Play)
ACCENT_DARK = "#2fae78"
ACCENT_DIM = "#1f6f4f"
DANGER = "#e5534b"
DANGER_DARK = "#c13d35"


def make_dirt_icon():
    """Generate a Minecraft grass block PhotoImage (32×32) for the window icon.

    Top ~9 rows are green grass, bottom rows are brown dirt, with a ragged
    grass/dirt transition in between — just like the real block.
    """
    import random
    w = h = 32
    img = tk.PhotoImage(width=w, height=h)
    # Grass palette
    grass_base = (89, 176, 60)
    grass_dark = (62, 140, 38)
    grass_light = (110, 200, 78)
    # Dirt palette
    dirt_base = (139, 94, 60)
    dirt_dark = (107, 63, 31)
    dirt_light = (160, 107, 69)
    dirt_spot = (74, 46, 26)
    # Ragged grass/dirt boundary per column (row where dirt starts)
    rng = random.Random(42)
    boundary = [8 + rng.randint(-2, 3) for _ in range(w)]
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            is_grass = y < boundary[x]
            if is_grass:
                n = rng.random()
                if n < 0.18:
                    c = grass_dark
                elif n < 0.35:
                    c = grass_light
                else:
                    c = grass_base
            else:
                n = rng.random()
                if n < 0.08:
                    c = dirt_spot
                elif n < 0.22:
                    c = dirt_dark
                elif n < 0.35:
                    c = dirt_light
                else:
                    c = dirt_base
            r = max(0, min(255, c[0] + rng.randint(-6, 6)))
            g = max(0, min(255, c[1] + rng.randint(-6, 6)))
            b = max(0, min(255, c[2] + rng.randint(-6, 6)))
            row.append(f"#{r:02x}{g:02x}{b:02x}")
        rows.append(" ".join(row))
    img.put(rows)
    return img


# --- config helpers -------------------------------------------------------- #
def load_config():
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text("utf-8")))
        except Exception:
            pass
    return cfg


def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), "utf-8")


def threading_work(fn):
    import threading
    t = threading.Thread(target=fn, daemon=True)
    t.start()


def centre_win(win, width, height):
    """Position a (possibly undrawn) window at the centre of the screen."""
    win.update_idletasks()
    sx = win.winfo_screenwidth()
    sy = win.winfo_screenheight()
    x = max((sx - width) // 2, 0)
    y = max((sy - height) // 2, 0)
    win.geometry(f"{width}x{height}+{x}+{y}")


def setup_style(widget):
    """Apply the dark theme to the given widget's Tk (idempotent)."""
    root = widget.winfo_toplevel()
    # Tk-level styling that ttk cannot express (combobox popdown, focus).
    root.option_add("*TCombobox*Listbox.background", CARD)
    root.option_add("*TCombobox*Listbox.foreground", TEXT)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_DARK)
    root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
    root.option_add("*TCombobox*Listbox.font", (FONT, 9))

    style = ttk.Style(widget)
    try:
        style.theme_use("clam")  # best base for full custom theming
    except Exception:
        pass

    style.configure(".", font=(FONT, 9))
    style.configure("BG.TFrame", background=BG)
    style.configure("BG.TLabel", background=BG, foreground=TEXT)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Card.TLabel", background=CARD, foreground=TEXT)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("CardMuted.TLabel", background=CARD, foreground=MUTED)

    # Buttons
    style.configure("TButton", background=CARD, foreground=TEXT,
                    borderwidth=0, focusthickness=0, padding=(14, 9),
                    font=(FONT, 10))
    style.map("TButton",
              background=[("active", CARD_HOVER), ("pressed", PANEL),
                          ("disabled", PANEL)],
              foreground=[("active", "#ffffff"), ("disabled", MUTED)])

    style.configure("Secondary.TButton", background=PANEL,
                    foreground=TEXT, padding=(14, 9))
    style.map("Secondary.TButton",
              background=[("active", CARD_HOVER), ("pressed", CARD),
                          ("disabled", PANEL)],
              foreground=[("active", "#ffffff"), ("disabled", MUTED)])

    style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                    font=(FONT, 11, "bold"), padding=(12, 12))
    style.map("Accent.TButton",
              background=[("active", "#4ce59c"), ("pressed", ACCENT_DARK),
                          ("disabled", ACCENT_DIM)],
              foreground=[("active", "#ffffff"), ("disabled", "#6f8f80")])

    style.configure("Danger.TButton", background="#7a2f2b", foreground="#ffffff",
                    font=(FONT, 10, "bold"), padding=(14, 9))
    style.map("Danger.TButton",
              background=[("active", "#933a35"), ("pressed", DANGER_DARK),
                          ("disabled", PANEL)],
              foreground=[("active", "#ffffff"), ("disabled", MUTED)])

    # Bigger variant for the account manager toolbar
    style.configure("Big.TButton", background=CARD, foreground=TEXT,
                    borderwidth=0, focusthickness=0, padding=(20, 12),
                    font=(FONT, 10))
    style.map("Big.TButton",
              background=[("active", CARD_HOVER), ("pressed", PANEL),
                          ("disabled", PANEL)],
              foreground=[("active", "#ffffff"), ("disabled", MUTED)])

    # Inputs
    style.configure("TEntry", fieldbackground=CARD, foreground=TEXT,
                    insertcolor=TEXT, bordercolor=OUTLINE, padding=4,
                    relief="flat")
    style.map("TEntry", fieldbackground=[("readonly", CARD)],
              foreground=[("readonly", TEXT)])

    style.configure("TCombobox", fieldbackground=CARD, foreground=TEXT,
                    arrowcolor=TEXT, padding=4, bordercolor=OUTLINE,
                    relief="flat")
    style.map("TCombobox",
              fieldbackground=[("readonly", CARD)],
              foreground=[("readonly", TEXT)],
              arrowcolor=[("disabled", MUTED)])

    style.configure("TCheckbutton", background=BG, foreground=TEXT)
    style.map("TCheckbutton",
              background=[("active", BG)],
              foreground=[("disabled", MUTED)])

    style.configure("Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=CARD, bordercolor=CARD, lightcolor=ACCENT,
                    darkcolor=ACCENT)

    style.configure("Header.TLabel", background=BG, foreground="#ffffff",
                    font=(FONT, 24, "bold"))
    style.configure("Subhead.TLabel", background=BG, foreground=MUTED,
                    font=(FONT, 9))
    style.configure("Badge.TLabel", background=CARD, foreground=ACCENT,
                    font=(FONT, 9, "bold"))
    style.configure("Title.TLabel", background=CARD, foreground="#ffffff",
                    font=(FONT, 11, "bold"))
    style.configure("TSeparator", background=OUTLINE)


# --- in-game avatar rendering ---------------------------------------------- #
# Module-level cache keyed by (account 'created', size).  PhotoImages are kept
# referenced here so they are never garbage-collected while a canvas uses them.
_AVATAR_CACHE = {}
_AVATAR_PHOTOS = []

# Thread-safe hand-off used by AvatarWidget.loading: worker threads push
# results here, and a main-thread poller drains them. Calling widget.after(...)
# from a non-main thread is not safe in Tkinter, so we route through a queue.
_AVATAR_QUEUE = queue.Queue()


def _placeholder_data(account, size):
    """Local (no-network) initial placeholder from an account."""
    name = (account or {}).get("name", "") or ""
    return {
        "kind": "initial",
        "initial": (name[:1].upper() if name else "?"),
        "bg": avatar.account_color(name),
        "fg": "#ffffff",
    }


class AvatarWidget(tk.Canvas):
    """A circular (or ring-bordered) player avatar.

    Renders a PhotoImage face pixel-by-pixel when available, or an initial
    letter badge.  Premium faces require a network fetch, which is done in a
    worker thread and applied back on the main thread; a placeholder is shown
    immediately while the fetch runs.  Results are cached by account so
    re-selecting never re-hits the network.
    """

    def __init__(self, master, size=32, bg=PANEL, ring=OUTLINE, **kw):
        self._size = int(size)
        self._ring = ring
        self._token = None
        self._photo_refs = []
        kw.setdefault("highlightthickness", 0)
        kw.setdefault("bd", 0)
        kw.setdefault("bg", bg)
        super().__init__(master, width=self._size, height=self._size, **kw)
        self._draw_empty()

    # -- drawing primitives ------------------------------------------------- #
    def _draw_empty(self):
        self.delete("all")
        self.create_oval(0, 0, self._size, self._size, fill="#3a414c",
                         outline=self._ring, width=1)

    def _draw_initial(self, data):
        self.delete("all")
        r = self._size / 2.0
        self.create_oval(1, 1, self._size - 1, self._size - 1,
                         fill=data["bg"], outline=self._ring, width=1)
        fs = max(9, int(r * 1.15))
        self.create_text(r, r + 1, text=data["initial"], fill=data["fg"],
                         font=(FONT, fs, "bold"))

    def _circle_mask(self, fill):
        """Clip the square face photo to a circle by painting the 4 corners."""
        r = self._size / 2.0
        cx = cy = r
        s = self._size

        def arc_pts(a0, a1, steps=10):
            pts = []
            for i in range(steps + 1):
                a = math.radians(a0 + (a1 - a0) * i / steps)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
            return pts

        corners = {
            # corner vertex -> (arc start, arc end)
            (0, 0): (180.0, 270.0),
            (s, 0): (270.0, 360.0),
            (0, s): (90.0, 180.0),
            (s, s): (0.0, 90.0),
        }
        for (vx, vy), (a0, a1) in corners.items():
            poly = [(vx, vy)] + arc_pts(a0, a1) + [(vx, vy)]
            flat = [coord for pt in poly for coord in pt]
            self.create_polygon(flat, fill=fill, outline=fill)

    def _render_face(self, data):
        self.delete("all")
        size = self._size
        photo = tk.PhotoImage(width=size, height=size)
        rows = data["rows"]
        # NOTE: photo.put MUST be given a list of row strings (one per row).
        # Passing a single string of colours only paints the first pixel
        # (Tk quirk), which made avatars render blank before.
        row_strings = []
        for y, row in enumerate(rows):
            if y >= size:
                break
            row_strings.append(" ".join(row[:size]))
        photo.put(row_strings)
        self._photo_refs.append(photo)
        _AVATAR_PHOTOS.append(photo)
        self.create_image(size / 2.0, size / 2.0, image=photo)
        self._circle_mask(self.cget("bg"))
        self.create_oval(1, 1, size - 1, size - 1, outline=self._ring,
                         width=1)

    # -- public API --------------------------------------------------------- #
    def show(self, account):
        """Render the avatar for `account` (None clears it)."""
        token = (account or {}).get("created")
        self._token = token
        self._photo_refs = []

        if not account:
            self._draw_empty()
            return

        # Cache by account identity (token/name/uuid) so switching names or
        # adding a UUID never returns another account's face.
        key = (token, account.get("name"), account.get("uuid"), self._size)
        cached = _AVATAR_CACHE.get(key)
        if cached is not None:
            self._apply(cached)
            return

        # Show the placeholder instantly, then swap in the face when ready.
        self._apply(_placeholder_data(account, self._size))
        capture = dict(account)

        def work(key=key, token=token):
            try:
                # mineatar.io is used first when the account has a UUID (premium);
                # mc-heads.net (username) covers offline accounts.
                data = avatar.avatar_data(capture.get("name", ""), self._size,
                                          uuid=capture.get("uuid"))
            except Exception:
                data = _placeholder_data(capture, self._size)
            _AVATAR_CACHE[key] = data
            # Push to a thread-safe queue; the main-thread poller applies it.
            _AVATAR_QUEUE.put((self, token, data))

        threading_work(work)
        self._ensure_poller()

    def _ensure_poller(self):
        """Start (once) a main-thread poller that drains the avatar queue.

        Must only be scheduled from the main thread (show() always is).
        The queue carries a strong widget reference, so a pending result never
        has its widget garbage-collected before _on_loaded runs.
        """
        if getattr(self, "_poller_started", False):
            return
        self._poller_started = True

        def poll():
            try:
                while True:
                    w, tok, data = _AVATAR_QUEUE.get_nowait()
                    w._on_loaded(tok, data)  # _on_loaded guards stale tokens
            except queue.Empty:
                pass
            try:
                self.after(100, poll)
            except Exception:
                pass

        self.after(100, poll)

    def _on_loaded(self, token, data):
        if token != self._token:
            return  # stale result for an account that was deselected
        # The widget (e.g. the account-manager preview) may have been destroyed
        # while the fetch was in flight; applying to a dead canvas raises
        # TclError inside the Tk callback, so bail out first.
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        self._apply(data)

    def _apply(self, data):
        self._photo_refs = []
        if data.get("kind") == "face" and data.get("rows"):
            self._render_face(data)
        else:
            self._draw_initial(data)


# --- account helpers ------------------------------------------------------- #
def new_offline_account(name):
    return {"state": "offline", "type": "offline", "name": name,
            "created": time.time()}


def account_dot_color(acc):
    """Dot colour for an account: green (premium/MS) vs red (offline)."""
    return ACCENT if acc.get("type") == "microsoft" else DANGER


def account_label(acc):
    return acc.get("name", "?")


def _ensure_launcher_profiles(game_dir: Path):
    """Create a minimal launcher_profiles.json if absent.

    Fabric Installer and some other mod installers require this file to exist
    in the game directory.  Without it Fabric throws:
      java.lang.RuntimeException: No launcher profile.json found!
    """
    lp = game_dir / "launcher_profiles.json"
    if not lp.exists():
        data = {"profiles": {}, "selectedProfile": "", "clientToken": ""}
        lp.write_text(json.dumps(data, indent=2), "utf-8")


# --- main window ----------------------------------------------------------- #
class LauncherApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.cfg = load_config()
        core.set_download_workers(self.cfg.get("download_threads", core.DOWNLOAD_WORKERS))
        self.game_dir = Path(self.cfg.get("game_dir") or DEFAULT_GAME_DIR)
        self.game_dir.mkdir(parents=True, exist_ok=True)
        _ensure_launcher_profiles(self.game_dir)
        self.launcher = GameLauncher(self.game_dir)
        self.versions = []
        self._installing = False
        self._accounts = list(self.cfg.get("accounts") or [])
        self._selected_account = None

        self._build_ui()
        self._refresh_versions()
        self._refresh_java()
        self._apply_selected_account()

    # ------------------------------------------------------------------ #
    def _build_ui(self):
        root = self.root
        setup_style(root)
        root.title(f"{core.APP_NAME} {core.LAUNCHER_VERSION}")
        root.resizable(False, False)
        root.configure(bg=BG)
        try:
            root.iconphoto(True, make_dirt_icon())
        except Exception:
            pass

        W, H = 470, 660
        centre_win(root, W, H)

        outer = ttk.Frame(root, style="BG.TFrame", padding=(20, 18))
        outer.pack(fill="both", expand=True)

        # ---- Header --------------------------------------------------- #
        header = ttk.Frame(outer, style="BG.TFrame")
        header.pack(fill="x", pady=(0, 6))
        title = ttk.Label(header, text="PyVortex", style="Header.TLabel")
        title.pack(side="left")
        badge = tk.Frame(header, bg=CARD)
        tk.Label(badge, text=f"  v{core.LAUNCHER_VERSION}  ", bg=CARD,
                 fg=ACCENT, font=(FONT, 9, "bold")).pack()
        badge.pack(side="left", padx=(10, 0), pady=(10, 0))
        ttk.Label(header, text="Minecraft Launcher", style="Subhead.TLabel"
                  ).pack(side="right", anchor="s", pady=(0, 2))

        # accent underline bar
        bar = tk.Frame(outer, bg=ACCENT, height=2)
        bar.pack(fill="x", pady=(0, 16))

        # ---- Account card --------------------------------------------- #
        acc_card = ttk.Frame(outer, style="Card.TFrame", padding=(14, 14))
        acc_card.pack(fill="x")

        acc_top = ttk.Frame(acc_card, style="Card.TFrame")
        acc_top.pack(fill="x")
        ttk.Label(acc_top, text="ACCOUNT", style="CardMuted.TLabel"
                  ).pack(side="left")


        acc_row = ttk.Frame(acc_card, style="Card.TFrame")
        acc_row.pack(fill="x", pady=(10, 0))
        # Small status dot reflecting the selected account's type (green=MS,
        # red=offline). Stays hidden when no account is selected.
        self.acc_dot = tk.Canvas(acc_row, width=10, height=10,
                                 highlightthickness=0, bd=0, bg=CARD)
        self.acc_dot.pack(side="left", padx=(0, 8))
        # Avatar canvas next to the dropdown
        self.acc_avatar = AvatarWidget(acc_row, size=30, bg=CARD)
        self.acc_avatar.pack(side="left", padx=(0, 10))

        self.acc_var = tk.StringVar(value="No account")
        acc_cb = ttk.Combobox(acc_row, textvariable=self.acc_var,
                              state="readonly")
        acc_cb.pack(side="left", fill="x", expand=True)
        acc_cb.bind("<<ComboboxSelected>>", self._on_account_selected)
        self.acc_cb = acc_cb

        self.btn_accounts = ttk.Button(acc_row, text="Accounts",
                                       command=self._open_accounts)
        self.btn_accounts.pack(side="left", padx=(10, 0))

        # ---- Game setup card ------------------------------------------ #
        ttk.Separator(outer).pack(fill="x", pady=16)

        setup_card = ttk.Frame(outer, style="Card.TFrame", padding=(14, 14))
        setup_card.pack(fill="x")
        setup_card.columnconfigure(1, weight=1)

        ttk.Label(setup_card, text="GAME SETUP", style="CardMuted.TLabel"
                  ).grid(row=0, column=0, columnspan=2, sticky="w",
                         pady=(0, 12))

        # Username
        ttk.Label(setup_card, text="Username", style="Card.TLabel"
                  ).grid(row=1, column=0, sticky="w", padx=(0, 16),
                         pady=(0, 10))
        self.name_var = tk.StringVar(value=self.cfg.get("name", "Player1"))
        self.name_entry = ttk.Entry(setup_card, textvariable=self.name_var)
        self.name_entry.grid(row=1, column=1, sticky="we", pady=(0, 10))

        # RAM
        ttk.Label(setup_card, text="Memory", style="Card.TLabel"
                  ).grid(row=2, column=0, sticky="w", padx=(0, 16),
                         pady=(0, 10))
        ram_wrap = ttk.Frame(setup_card, style="Card.TFrame")
        ram_wrap.grid(row=2, column=1, sticky="we", pady=(0, 10))
        self.ram_var = tk.StringVar(value=str(self.cfg.get("ram_mb", core.DEFAULT_RAM_MB)))
        ent_ram = ttk.Entry(ram_wrap, textvariable=self.ram_var, width=6)
        ent_ram.pack(side="left")
        ent_ram.config(validate="key",
                       validatecommand=(root.register(self._only_digits), "%P"))
        ttk.Label(ram_wrap, text="MB", style="CardMuted.TLabel"
                  ).pack(side="left", padx=(8, 0))

        # Version
        ttk.Label(setup_card, text="Version", style="Card.TLabel"
                  ).grid(row=3, column=0, sticky="w", padx=(0, 16),
                         pady=(0, 10))
        self.version_cb = ttk.Combobox(setup_card, state="readonly")
        self.version_cb.grid(row=3, column=1, sticky="we", pady=(0, 10))
        self.version_cb.bind("<<ComboboxSelected>>", self._save_version)

        # Java
        ttk.Label(setup_card, text="Java", style="Card.TLabel"
                  ).grid(row=4, column=0, sticky="w", padx=(0, 16))
        java_wrap = ttk.Frame(setup_card, style="Card.TFrame")
        java_wrap.grid(row=4, column=1, sticky="we")
        self.java_status_var = tk.StringVar(value="Checking...")
        java_lbl = ttk.Label(java_wrap, textvariable=self.java_status_var,
                             style="Card.TLabel")
        java_lbl.pack(side="left", fill="x", expand=True)
        self.btn_java = ttk.Button(java_wrap, text="Install Java",
                                   command=self._install_java)
        self.btn_java.pack(side="right")

        # ---- Play ------------------------------------------------------ #
        ttk.Separator(outer).pack(fill="x", pady=16)
        self.btn_play = ttk.Button(outer, text="Play", style="Accent.TButton",
                                   command=self._play)
        self.btn_play.pack(fill="x")

        # ---- Secondary actions ---------------------------------------- #
        row = ttk.Frame(outer, style="BG.TFrame")
        row.pack(fill="x", pady=(10, 0))
        self.btn_downloader = ttk.Button(row, text="Downloader",
                                         style="Secondary.TButton",
                                         command=self._open_downloader)
        self.btn_downloader.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_settings = ttk.Button(row, text="Settings",
                                       style="Secondary.TButton",
                                       command=self._open_settings)
        self.btn_settings.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ---- Folder buttons ------------------------------------------- #
        row2 = ttk.Frame(outer, style="BG.TFrame")
        row2.pack(fill="x", pady=(6, 0))
        ttk.Button(row2, text="\U0001F4C2 Game Folder",
                   style="Secondary.TButton",
                   command=self._open_game_folder
                   ).pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.btn_mods = ttk.Button(row2, text="\U0001F4C2 Mods Folder",
                                   style="Secondary.TButton",
                                   command=self._open_mods_folder)
        self.btn_mods.pack(side="left", fill="x", expand=True, padx=(4, 0))

        # ---- Footer ---------------------------------------------------- #
        foot = ttk.Frame(outer, style="BG.TFrame")
        foot.pack(fill="x", pady=(12, 0))
        ttk.Label(foot, text="PyVortex", style="Muted.TLabel").pack(side="left")
        ttk.Label(foot, text=str(self.game_dir), style="Muted.TLabel"
                  ).pack(side="right")

        self._tooltip(acc_cb, "Select or manage your Minecraft accounts")
        self._tooltip(self.btn_play, "Launch Minecraft with the selected profile")

    def _tooltip(self, widget, text):
        try:
            from idlelib.tooltip import Hovertip
            Hovertip(widget, text, hover_delay=600)
        except Exception:
            pass

    def _only_digits(self, value):
        return value.isdigit() or value == ""

    def _log(self, msg):
        pass

    def _set_status(self, msg):
        pass

    # ------------------------------------------------------------------ #
    #  Account selection
    # ------------------------------------------------------------------ #
    def _account_list(self):
        return self._accounts

    def _apply_selected_account(self, account=None):
        if account is None:
            sel = self.cfg.get("selected_account")
            for a in self._accounts:
                if a.get("created") == sel:
                    self._selected_account = a
                    break
            else:
                self._selected_account = self._accounts[0] if self._accounts else None
        else:
            self._selected_account = account
            self.cfg["selected_account"] = account.get("created")
        self._refresh_account_ui()

    def _refresh_account_ui(self):
        self.acc_var.set(account_label(self._selected_account)
                         if self._selected_account else "No account")
        labels = [account_label(a) for a in self._accounts]
        self.acc_cb["values"] = labels
        entry = self._selected_account
        if entry:
            if entry.get("type") == "microsoft":
                self.name_var.set(entry.get("name", ""))
                self.name_entry.config(state="disabled")
            else:
                self.name_entry.config(state="normal")
                self.name_var.set(entry.get("name", "Player1"))
        else:
            self.name_entry.config(state="normal")
            self.name_var.set("Player1")
        # Keep the in-window avatar in sync with the selected account.
        self.acc_avatar.show(self._selected_account)
        # Reflect the selected account's type via a small status dot.
        self.acc_dot.delete("all")
        if self._selected_account:
            color = account_dot_color(self._selected_account)
            self.acc_dot.create_oval(1, 1, 9, 9, fill=color, outline=color)

    def _on_account_selected(self, _e=None):
        label = self.acc_var.get()
        for a in self._accounts:
            if account_label(a) == label:
                self._apply_selected_account(a)
                self.cfg["selected_account"] = a.get("created")
                save_config(self.cfg)
                break

    def _open_accounts(self):
        AccountManagerWindow(self.root, self)

    def _current_username(self):
        acc = self._selected_account
        if acc and acc.get("type") == "microsoft":
            return acc.get("name", "Player")
        name = self.name_var.get().strip()
        return name if name else "Player1"

    # ------------------------------------------------------------------ #
    def _refresh_versions(self):
        self.version_cb["values"] = ["Loading..."]
        self.version_cb.set("Loading...")
        def work():
            try:
                vers = self.launcher.downloaded_versions()
            except Exception:
                vers = []
            self.root.after(0, lambda: self._set_versions(vers))
        threading_work(work)

    def _set_versions(self, vers):
        self.versions = vers
        if not vers:
            self.version_cb["values"] = ["No versions installed"]
            self.version_cb.set("No versions installed")
        else:
            self.version_cb["values"] = vers
            prev = self.cfg.get("version")
            if prev in vers:
                self.version_cb.set(prev)
            else:
                self.version_cb.current(0)
        self._update_play_state()

    def _save_version(self, _e=None):
        self.cfg["version"] = self.version_cb.get()
        save_config(self.cfg)

    # ------------------------------------------------------------------ #
    def _refresh_java(self, auto_install=True):
        self.java_status_var.set("Checking...")
        self.btn_java.config(state="disabled")
        def work():
            installed = self.launcher.java.installed_features()
            self.root.after(0, lambda: self._set_java(installed, auto_install))
        threading_work(work)

    def _set_java(self, installed, auto_install):
        if self._installing:
            return
        if installed:
            label = ", ".join(f"Java {f}" for f in sorted(installed, reverse=True))
            self.java_status_var.set(label)
            self.btn_java.config(state="disabled", text="Java installed")
            self._update_play_state()
            return
        if auto_install:
            self._begin_install([PREFERRED_DEFAULT])
        else:
            self.java_status_var.set("Java not installed")
            self.btn_java.config(state="normal", text="Install Java")
            self.btn_play.config(state="disabled")

    def _update_play_state(self):
        v = self.version_cb.get()
        has_ver = bool(v) and not v.startswith(("No versions", "Loading"))
        has_java = bool(self.launcher.java.installed_features())
        self.btn_play.config(
            state="normal" if (has_ver and has_java and not self._installing)
            else "disabled")

    def _begin_install(self, features):
        self._installing = True
        self.btn_java.config(state="disabled")
        self.btn_play.config(state="disabled")
        if len(features) == 1:
            self.java_status_var.set(f"Downloading Java {features[0]}...")
        else:
            self.java_status_var.set("Downloading Java...")

        def work():
            def report(msg, frac):
                self.root.after(0, lambda: self.java_status_var.set(msg))
            ok = self.launcher.java.ensure_java(features, progress=report)
            self.root.after(0, lambda: self._done_install(ok))
        threading_work(work)

    def _done_install(self, ok):
        self._installing = False
        if ok:
            self._refresh_java(auto_install=False)
        else:
            self.java_status_var.set("Java install failed")
            self.btn_java.config(state="normal", text="Retry")
            self.btn_play.config(state="disabled")

    def _install_java(self):
        self._begin_install(self.launcher.java.JAVA_FEATURES)

    def _selected_java(self):
        return self.launcher.java.bundled_javaw()

    # ------------------------------------------------------------------ #
    def _play(self):
        acc = self._selected_account
        name = self._current_username()
        if acc and acc.get("type") == "microsoft":
            name = acc.get("name", name)
        if len(name.strip()) < 3:
            messagebox.showerror("Error", "Name is too short! Minimum length is 3.")
            return
        try:
            ram = int(self.ram_var.get())
        except ValueError:
            ram = core.DEFAULT_RAM_MB
        if ram and ram < core.MIN_RAM_MB:
            ram = core.MIN_RAM_MB
            messagebox.showwarning(
                "Warning",
                "You allocated too low amount of memory!\n\n"
                f"Allocated memory set to {core.MIN_RAM_MB} MB to prevent crashes.")

        version_id = self.version_cb.get()
        if not version_id or version_id.startswith("No versions"):
            messagebox.showerror("Error", "Select a version first.")
            return

        # Persist offline name back to the account
        if acc and acc.get("type") == "offline":
            acc["name"] = name
            save_config(self.cfg)

        self.cfg.update({"name": name, "ram_mb": ram, "version": version_id})
        save_config(self.cfg)

        self.btn_play.config(state="disabled")
        self.btn_downloader.config(state="disabled")
        self.btn_settings.config(state="disabled")
        self.java_status_var.set("Preparing Java...")

        def work():
            launched_ok = False
            try:
                java_path = self.launcher.ensure_version_java(
                    version_id,
                    progress=lambda msg, f: self.root.after(
                        0, lambda: self.java_status_var.set(msg)),
                )
                self.launcher.launch(version_id, name, ram, java_path=java_path,
                                     account=acc)
                launched_ok = True
                self.root.after(0, lambda: self.java_status_var.set("Launched"))
            except Exception as e:
                err = str(e)
                self.root.after(
                    0, lambda err=err: messagebox.showerror("Launch error", err))
            finally:
                self.root.after(0, self._reenable)
                self.root.after(0, lambda: self._refresh_java(auto_install=False))
                if launched_ok and self.cfg.get("close_on_launch", True):
                    self.root.after(2500, self.root.destroy)

        threading_work(work)

    def _reenable(self):
        self.btn_play.config(state="normal")
        self.btn_downloader.config(state="normal")
        self.btn_settings.config(state="normal")

    # ------------------------------------------------------------------ #
    def _open_downloader(self):
        DownloaderWindow(self.root, self.launcher, on_done=self._refresh_versions)

    def _open_settings(self):
        SettingsWindow(self.root, self)

    def _open_game_folder(self):
        path = self.game_dir
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    def _open_mods_folder(self):
        mods = self.game_dir / "mods"
        mods.mkdir(parents=True, exist_ok=True)
        os.startfile(str(mods))


# --- account manager window ------------------------------------------------ #
class AccountManagerWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        setup_style(self)
        self.title("Accounts")
        self.transient(master)
        centre_win(self, 470, 480)
        self.resizable(False, False)
        self.configure(bg=BG)

        outer = ttk.Frame(self, style="BG.TFrame", padding=(16, 14))
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="ACCOUNTS", style="Subhead.TLabel"
                  ).pack(anchor="w")
        bar = tk.Frame(outer, bg=ACCENT, height=2)
        bar.pack(fill="x", pady=(6, 12))

        # ---- Selected account preview (card with avatar) -------------- #
        preview = ttk.Frame(outer, style="Card.TFrame", padding=(12, 12))
        preview.pack(fill="x")
        self.preview_avatar = AvatarWidget(preview, size=64, bg=CARD)
        self.preview_avatar.pack(side="left", padx=(0, 14))

        self.preview_meta = ttk.Frame(preview, style="Card.TFrame")
        self.preview_meta.pack(side="left", fill="y")
        self.preview_name = ttk.Label(self.preview_meta, text="No account",
                                      style="Title.TLabel", anchor="w")
        self.preview_name.pack(fill="x")
        self.preview_type = ttk.Label(self.preview_meta, text="",
                                      style="CardMuted.TLabel", anchor="w")
        self.preview_type.pack(fill="x", pady=(2, 0))

        # ---- List ----------------------------------------------- #
        ttk.Label(outer, text="Saved profiles", style="CardMuted.TLabel"
                  ).pack(anchor="w", pady=(16, 6))
        listwrap = ttk.Frame(outer, style="Card.TFrame")
        listwrap.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(
            listwrap, height=8, bg=CARD, fg=TEXT, selectbackground=ACCENT_DARK,
            selectforeground="#ffffff", relief="flat", bd=0,
            highlightthickness=1, highlightbackground=OUTLINE,
            highlightcolor=ACCENT, font=(FONT, 10), activestyle="none")
        self.listbox.pack(fill="both", expand=True, padx=1, pady=1)
        self.listbox.bind("<Double-Button-1>", lambda _e: self._select())
        # The virtual select event only refreshes the preview, so programmatic
        # re-fills never trigger a close.  A real click on a profile applies it
        # and closes this window, so the choice takes effect immediately.
        self.listbox.bind("<<ListboxSelect>>",
                          lambda _e: self._update_preview())
        self.listbox.bind("<ButtonRelease-1>",
                          lambda _e: (self._update_preview(), self._select()))

        # ---- Buttons ------------------------------------------- #
        btns = ttk.Frame(outer, style="BG.TFrame")
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Add Microsoft", style="Big.TButton",
                   command=self._add_microsoft).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="Add Crack", style="Big.TButton",
                   command=self._add_crack).pack(side="left", padx=(0, 4))
        ttk.Button(btns, text="Delete", style="Danger.TButton",
                   command=self._delete).pack(side="left")

        self.status = ttk.Label(outer, text="", style="Muted.TLabel")
        self.status.pack(fill="x", pady=(10, 0))

        self._fill()
        self._update_preview()

    def _fill(self):
        self.listbox.delete(0, "end")
        selected_idx = None
        for i, a in enumerate(self.app._accounts):
            # Whole item foreground is the dot colour, so the "● " glyph reads
            # as a green/red status dot in front of the plain name. A trailing
            # " •" marks the currently selected account (kept muted).
            mark = "  •" if a is self.app._selected_account else ""
            self.listbox.insert("end", "● " + account_label(a) + mark)
            last = self.listbox.size() - 1
            self.listbox.itemconfig(last, foreground=account_dot_color(a))
            if a is self.app._selected_account:
                selected_idx = last
        # Always highlight the currently-applied account so the preview and
        # Delete button have something to work with.
        if selected_idx is not None:
            self.listbox.selection_clear(0, "end")
            self.listbox.selection_set(selected_idx)
            self.listbox.see(selected_idx)
        self._update_preview()

    def _update_preview(self):
        """Show the avatar + details for the currently selected list entry."""
        if not self.winfo_exists():
            return
        sel = self.app._selected_account
        idx = self.listbox.curselection()
        if idx:
            sel = self.app._accounts[idx[0]]
        if sel:
            self.preview_avatar.show(sel)
            self.preview_name.config(text=sel.get("name", "?"))
            kind = "Microsoft premium" if sel.get("type") == "microsoft" else "Offline"
            self.preview_type.config(text=kind)
        else:
            self.preview_avatar.show(None)
            self.preview_name.config(text="No account")
            self.preview_type.config(text="")

    def _persist(self):
        self.app.cfg["accounts"] = self.app._accounts
        self.app.cfg["selected_account"] = (
            self.app._selected_account.get("created")
            if self.app._selected_account else None)
        save_config(self.app.cfg)

    def _add_crack(self):
        dlg = tk.Toplevel(self)
        dlg.title("Add Offline Account")
        dlg.transient(self)
        dlg.configure(bg=BG)
        setup_style(dlg)
        centre_win(dlg, 360, 200)
        f = ttk.Frame(dlg, style="BG.TFrame")
        f.pack(fill="both", expand=True, padx=16, pady=14)
        ttk.Label(f, text="Display name (3+ characters):", style="BG.TLabel"
                  ).pack(anchor="w")
        var = tk.StringVar(value="Player1")
        ent = ttk.Entry(f, textvariable=var)
        ent.pack(fill="x", pady=(6, 4))
        ent.focus_set()
        ttk.Label(f, text="Offline accounts play without a paid Minecraft "
                          "profile.", style="Muted.TLabel", wraplength=320,
                  justify="left").pack(anchor="w", pady=(0, 10))
        def ok():
            name = var.get().strip()
            if len(name) < 3:
                messagebox.showerror("Error", "Name must be 3+ characters.", parent=dlg)
                return
            acc = new_offline_account(name)
            self.app._accounts.append(acc)
            self.app._apply_selected_account(acc)
            self._persist()
            self._fill()
            dlg.destroy()
        row = ttk.Frame(f, style="BG.TFrame")
        row.pack(fill="x", pady=(6, 0))
        row.columnconfigure(0, weight=1)
        row.columnconfigure(1, weight=1)
        ttk.Button(row, text="Add", style="Accent.TButton",
                   command=ok).grid(row=0, column=0, sticky="we", padx=(0, 4))
        ttk.Button(row, text="Cancel", style="Big.TButton",
                   command=dlg.destroy).grid(row=0, column=1, sticky="we", padx=(4, 0))

    def _add_microsoft(self):
        self.status.config(text="")
        MicrosoftLoginDialog(self, self._on_ms_result)

    def _on_ms_result(self, result):
        result["created"] = time.time()
        self.app._accounts.append(result)
        self.app._apply_selected_account(result)
        self._persist()
        self._fill()
        self.status.config(text=f"Added {result.get('name')}")

    def _delete(self):
        # Prefer the highlighted row; fall back to the currently-applied account
        # so Delete never silently does nothing.
        sel = self.listbox.curselection()
        if sel:
            idx = sel[0]
        elif self.app._selected_account:
            for i, a in enumerate(self.app._accounts):
                if a is self.app._selected_account:
                    idx = i
                    break
            else:
                return
        else:
            return
        acc = self.app._accounts[idx]
        name = acc.get("name", "?")
        kind = "Microsoft premium" if acc.get("type") == "microsoft" else "offline"
        if not messagebox.askyesno("Delete account",
                                   f"Delete {name} ({kind})?",
                                   parent=self):
            return
        if acc is self.app._selected_account:
            self.app._selected_account = None
        self.app._accounts.pop(idx)
        if self.app._selected_account is None and self.app._accounts:
            self.app._selected_account = self.app._accounts[0]
        self._persist()
        self.app._refresh_account_ui()
        self._fill()

    def _select(self):
        idx = self.listbox.curselection()
        if not idx:
            return
        acc = self.app._accounts[idx[0]]
        self.app._apply_selected_account(acc)
        self._persist()
        # Close the manager once an account has been chosen, so the user does
        # not have to press X manually after picking a profile.
        self.destroy()


# --- microsoft login dialog ------------------------------------------------ #
class MicrosoftLoginDialog(tk.Toplevel):
    """Modal dialog for the Microsoft device-code flow.

    Shows the verification link + code with Copy buttons, then polls for
    approval in a worker thread. Uses the existing auth.DeviceCodeSession and
    auth.complete_login — no new dependencies.
    """

    def __init__(self, parent, on_result):
        super().__init__(parent)
        self.parent = parent
        self.on_result = on_result
        self._session = None

        setup_style(self)
        self.title("Sign in to Microsoft")
        self.transient(parent)
        centre_win(self, 440, 380)
        self.resizable(False, False)
        self.configure(bg=BG)

        # Present the verification link + code before starting the worker, so a
        # network failure surfaces as a friendly message instead of a dead dialog.
        self._session = auth.DeviceCodeSession()
        try:
            self.uri, self.user_code = self._session.start()
        except Exception as e:
            messagebox.showerror("Microsoft login", f"Could not start sign-in.\n\n{e}",
                                 parent=parent)
            self.destroy()
            return

        outer = ttk.Frame(self, style="BG.TFrame", padding=(16, 14))
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="Sign in to Microsoft",
                  style="Header.TLabel").pack(anchor="w")
        bar = tk.Frame(outer, bg=ACCENT, height=2)
        bar.pack(fill="x", pady=(6, 12))

        ttk.Label(outer,
                  text="1. Open the link below\n"
                       "2. Sign in with your Microsoft account and enter the code.",
                  style="BG.TLabel", justify="left").pack(anchor="w", pady=(0, 6))

        # Link row — plain tk.Entry with explicit colors so the URL is always
        # clearly visible (the ttk readonly style was invisible on dark bg).
        link_wrap = ttk.Frame(outer, style="Card.TFrame")
        link_wrap.pack(fill="x", pady=(0, 10))
        link_var = tk.StringVar(value=self.uri)
        link_entry = tk.Entry(link_wrap, textvariable=link_var,
                               state="readonly", readonlybackground=CARD_HOVER,
                               bg=CARD_HOVER, fg=TEXT, bd=0,
                               highlightthickness=1, highlightbackground=OUTLINE,
                               highlightcolor=ACCENT, relief="flat",
                               font=(FONT, 9))
        link_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(link_wrap, text="Copy Link",
                   command=lambda: self._copy(self.uri)).pack(
            side="left", padx=(8, 0))

        # Code display
        code_lbl = ttk.Frame(outer, style="Card.TFrame", padding=(10, 8))
        code_lbl.pack(fill="x")
        tk.Label(code_lbl, text=self.user_code, bg=CARD, fg=ACCENT,
                 font=(FONT, 26, "bold")).pack(side="left")
        ttk.Button(code_lbl, text="Copy Code",
                   command=lambda: self._copy(self.user_code)).pack(
            side="right")

        self.status = ttk.Label(outer, text="Waiting for approval...",
                                style="Muted.TLabel", anchor="w",
                                wraplength=400, justify="left")
        self.status.pack(fill="x", pady=(10, 0))

        btns = ttk.Frame(outer, style="BG.TFrame")
        btns.pack(fill="x", pady=(14, 0))
        ttk.Button(btns, text="Cancel", style="Secondary.TButton",
                   command=self.destroy).pack(side="right")

        self.protocol("WM_DELETE_WINDOW", self.destroy)
        threading_work(self._poll_work)

    def _copy(self, text):
        self.clipboard_clear()
        self.clipboard_append(text)

    def _set_status(self, text, color=None):
        if not self.winfo_exists():
            return
        self.status.config(text=text, foreground=color or MUTED)

    def _poll_work(self):
        """Run on a worker thread: poll for approval, then exchange tokens."""
        try:
            def display(msg, self_ref=self):
                # Queue a status update back on the main (Tk) thread.
                if not self_ref.winfo_exists():
                    return
                try:
                    self_ref.after(0, lambda: self_ref._set_status(msg))
                except Exception:
                    pass
            result = auth.complete_login(self._session, display=display)
            if not self.winfo_exists():
                return
            self.after(0, lambda: self._on_success(result))
        except Exception as e:
            err = str(e)
            if self.winfo_exists():
                self.after(0, lambda err=err: self._set_status(
                    f"Failed: {err}", DANGER))

    def _on_success(self, result):
        if not self.winfo_exists():
            return
        try:
            callback = self.on_result
        except Exception:
            callback = None
        self.destroy()
        if callback:
            callback(result)


# --- Fabric / Forge meta helpers ------------------------------------------- #
FABRIC_META_URL = "https://meta.fabricmc.net/v2"
FORGE_MAVEN_META = "https://maven.minecraftforge.net/net/minecraftforge/forge/maven-metadata.xml"
FORGE_INSTALLER_URL = "https://maven.minecraftforge.net/net/minecraftforge/forge/{ver}/forge-{ver}-installer.jar"


def _fetch_fabric_game_versions():
    """Return list of MC version IDs that Fabric supports (newest first)."""
    url = FABRIC_META_URL + "/versions/game"
    data = core.http_get_json(url)
    return [v["version"] for v in data if v.get("stable")]


def _fetch_fabric_loader_versions():
    """Return list of Fabric loader version strings (newest first)."""
    url = FABRIC_META_URL + "/versions/loader"
    data = core.http_get_json(url)
    return [v["version"] for v in data]


def _install_fabric(root: Path, mc_version: str, loader_version: str,
                    progress=None, cancel_event=None):
    """Download the Fabric profile JSON and its libraries."""
    profile_url = (FABRIC_META_URL +
                   f"/versions/loader/{mc_version}/{loader_version}/profile/json")
    if progress:
        progress("Fetching Fabric profile...", 0.05)
    profile = core.http_get_json(profile_url)
    version_id = profile.get("id", f"fabric-loader-{loader_version}-{mc_version}")
    ver_dir = root / "versions" / version_id
    ver_dir.mkdir(parents=True, exist_ok=True)
    json_path = ver_dir / f"{version_id}.json"
    json_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False), "utf-8")

    if cancel_event and cancel_event.is_set():
        raise core.DownloadCancelled("Download cancelled")

    # Make sure the parent vanilla version is present
    inherits = profile.get("inheritsFrom", mc_version)
    parent_json = root / "versions" / inherits / f"{inherits}.json"
    if not parent_json.exists():
        if progress:
            progress(f"Downloading vanilla {inherits}...", 0.1)
        manifest = core.VersionManifest.fetch()
        meta_url = manifest.get_version_meta_url(inherits)
        if meta_url:
            ver_obj = core.MinecraftVersion.load(root, inherits, meta_url)
            eff = ver_obj.effective()
            dl = eff.get("downloads", {}).get("client")
            vanilla_jar = root / "versions" / inherits / f"{inherits}.jar"
            if dl and not vanilla_jar.exists():
                if progress:
                    progress(f"Downloading {inherits} client jar...", 0.15)
                core.http_download(dl["url"], vanilla_jar)

    if cancel_event and cancel_event.is_set():
        raise core.DownloadCancelled("Download cancelled")

    # Download Fabric's libraries
    ver = core.MinecraftVersion.load(root, version_id)
    effective = ver.effective()
    if progress:
        progress("Downloading Fabric libraries...", 0.3)
    core.download_missing_libraries(root, effective, progress,
                                    cancel_event=cancel_event)

    # Create mods folder
    (root / "mods").mkdir(exist_ok=True)

    if progress:
        progress(f"Fabric {loader_version} for {mc_version} installed!", 1.0)
    return version_id


def _fetch_forge_versions_for(mc_version: str):
    """Return list of Forge version strings for a given MC version."""
    import xml.etree.ElementTree as ET
    xml_text = core.http_get(FORGE_MAVEN_META)
    tree_root = ET.fromstring(xml_text)
    versions = []
    for v in tree_root.iter("version"):
        text = v.text or ""
        if text.startswith(mc_version + "-"):
            versions.append(text)
    return list(reversed(versions))


def _fetch_forge_mc_versions():
    """Return list of MC versions that have at least one Forge build."""
    import xml.etree.ElementTree as ET
    xml_text = core.http_get(FORGE_MAVEN_META)
    tree_root = ET.fromstring(xml_text)
    seen = []
    seen_set = set()
    for v in tree_root.iter("version"):
        text = v.text or ""
        mc = text.split("-")[0]
        if mc and mc not in seen_set:
            seen_set.add(mc)
            seen.append(mc)
    return list(reversed(seen))


def _install_forge(root: Path, forge_version: str, progress=None,
                   cancel_event=None):
    """Download and run the Forge installer jar."""
    import subprocess as sp
    mc_version = forge_version.split("-")[0]

    parent_json = root / "versions" / mc_version / f"{mc_version}.json"
    if not parent_json.exists():
        if progress:
            progress(f"Downloading vanilla {mc_version}...", 0.05)
        manifest = core.VersionManifest.fetch()
        meta_url = manifest.get_version_meta_url(mc_version)
        if meta_url:
            ver_obj = core.MinecraftVersion.load(root, mc_version, meta_url)
            eff = ver_obj.effective()
            dl = eff.get("downloads", {}).get("client")
            vanilla_jar = root / "versions" / mc_version / f"{mc_version}.jar"
            if dl and not vanilla_jar.exists():
                core.http_download(dl["url"], vanilla_jar)

    if cancel_event and cancel_event.is_set():
        raise core.DownloadCancelled("Download cancelled")

    _ensure_launcher_profiles(root)

    installer_url = FORGE_INSTALLER_URL.format(ver=forge_version)
    installer_jar = root / "forge-installer.jar"
    if progress:
        progress("Downloading Forge installer...", 0.15)
    core.http_download(installer_url, installer_jar)

    if cancel_event and cancel_event.is_set():
        installer_jar.unlink(missing_ok=True)
        raise core.DownloadCancelled("Download cancelled")

    from .java import JavaManager as _JM
    jm = _JM(root)
    java = jm.bundled_javaw()
    if java:
        java_exe = str(java).replace("javaw.exe", "java.exe")
    else:
        java_exe = "java"

    if progress:
        progress("Running Forge installer (may take a minute)...", 0.4)
    cmd = [java_exe, "-jar", str(installer_jar), "--installClient", str(root)]
    try:
        result = sp.run(cmd, cwd=str(root), capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            err = (result.stderr or result.stdout or "")[-500:]
            raise RuntimeError(f"Forge installer failed (exit {result.returncode}):\n{err}")
    finally:
        installer_jar.unlink(missing_ok=True)

    (root / "mods").mkdir(exist_ok=True)
    if progress:
        progress(f"Forge {forge_version} installed!", 1.0)
    return forge_version


# --- downloader window ----------------------------------------------------- #
class DownloaderWindow(tk.Toplevel):
    def __init__(self, master, launcher, on_done=None):
        super().__init__(master)
        self.launcher = launcher
        self.on_done = on_done
        self.downloading = False
        self._cancelled = None  # threading.Event, created per download

        setup_style(self)
        self.title("Downloader")
        self.transient(master)
        centre_win(self, 440, 400)
        self.resizable(False, False)
        self.configure(bg=BG)
        self.protocol("WM_DELETE_WINDOW", self._close)

        style = ttk.Style(self)
        style.configure("Tab.TButton", background=PANEL, foreground=MUTED,
                         padding=(16, 8), font=(FONT, 10))
        style.map("Tab.TButton",
                   background=[("active", CARD_HOVER)],
                   foreground=[("active", TEXT)])
        style.configure("TabActive.TButton", background=ACCENT_DIM,
                         foreground="#ffffff", padding=(16, 8),
                         font=(FONT, 10, "bold"))
        style.map("TabActive.TButton",
                   background=[("active", ACCENT_DARK)],
                   foreground=[("active", "#ffffff")])

        outer = ttk.Frame(self, style="BG.TFrame", padding=(14, 12))
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="DOWNLOADER", style="Subhead.TLabel"
                  ).pack(anchor="w")
        bar = tk.Frame(outer, bg=ACCENT, height=2)
        bar.pack(fill="x", pady=(6, 10))

        # --- Tab bar ---
        tab_row = ttk.Frame(outer, style="BG.TFrame")
        tab_row.pack(fill="x", pady=(0, 10))
        self._tab_btns = []
        for i, label in enumerate(("Vanilla", "Fabric", "Forge")):
            b = ttk.Button(tab_row, text=label,
                           command=lambda idx=i: self._switch_tab(idx))
            b.pack(side="left", padx=(0, 4))
            self._tab_btns.append(b)

        # --- Content area (stacked frames) ---
        self._content = ttk.Frame(outer, style="BG.TFrame")
        self._content.pack(fill="both", expand=True)

        self._pages = []
        self._build_vanilla_page()
        self._build_fabric_page()
        self._build_forge_page()

        # --- Shared status / progress ---
        self.status = ttk.Label(outer, text="", style="BG.TLabel", anchor="w")
        self.status.pack(fill="x", pady=(8, 2))
        self.progress = ttk.Progressbar(outer, mode="determinate")
        self.progress.pack(fill="x", pady=(0, 4))

        # --- Bottom buttons ---
        row = ttk.Frame(outer, style="BG.TFrame")
        row.pack(fill="x", pady=(8, 0))
        self.btn_download = ttk.Button(row, text="Download",
                                       style="Accent.TButton",
                                       command=self._download)
        self.btn_download.pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(row, text="Cancel", style="Secondary.TButton",
                   command=self._close).pack(side="left", fill="x",
                                              expand=True, padx=(3, 0))

        self._current_tab = -1
        self._switch_tab(0)

    # --- page builders ----------------------------------------

    def _build_vanilla_page(self):
        page = ttk.Frame(self._content, style="BG.TFrame")
        self._pages.append(page)

        self.van_version_cb = ttk.Combobox(page, state="readonly")
        self.van_version_cb.pack(fill="x", pady=2)
        self.van_version_cb.set("Loading versions...")

        self.van_show_all = tk.BooleanVar(value=False)
        ttk.Checkbutton(page, text="Show all versions (snapshots)",
                        variable=self.van_show_all,
                        command=self._reload_vanilla).pack(anchor="w", pady=(8, 0))

    def _build_fabric_page(self):
        page = ttk.Frame(self._content, style="BG.TFrame")
        self._pages.append(page)

        ttk.Label(page, text="Minecraft version", style="BG.TLabel"
                  ).pack(anchor="w", pady=(0, 2))
        self.fab_mc_cb = ttk.Combobox(page, state="readonly")
        self.fab_mc_cb.pack(fill="x", pady=2)
        self.fab_mc_cb.set("Loading...")

        ttk.Label(page, text="Fabric Loader version", style="BG.TLabel"
                  ).pack(anchor="w", pady=(8, 2))
        self.fab_loader_cb = ttk.Combobox(page, state="readonly")
        self.fab_loader_cb.pack(fill="x", pady=2)
        self.fab_loader_cb.set("Loading...")

    def _build_forge_page(self):
        page = ttk.Frame(self._content, style="BG.TFrame")
        self._pages.append(page)

        ttk.Label(page, text="Minecraft version", style="BG.TLabel"
                  ).pack(anchor="w", pady=(0, 2))
        self.forge_mc_cb = ttk.Combobox(page, state="readonly")
        self.forge_mc_cb.pack(fill="x", pady=2)
        self.forge_mc_cb.set("Loading...")
        self.forge_mc_cb.bind("<<ComboboxSelected>>", self._on_forge_mc_selected)

        ttk.Label(page, text="Forge version", style="BG.TLabel"
                  ).pack(anchor="w", pady=(8, 2))
        self.forge_ver_cb = ttk.Combobox(page, state="readonly")
        self.forge_ver_cb.pack(fill="x", pady=2)
        self.forge_ver_cb.set("Select MC version first")

    # --- tab switching ----------------------------------------

    def _switch_tab(self, idx):
        if idx == self._current_tab:
            return
        self._current_tab = idx
        for i, b in enumerate(self._tab_btns):
            b.configure(style="TabActive.TButton" if i == idx else "Tab.TButton")
        for p in self._pages:
            p.pack_forget()
        self._pages[idx].pack(in_=self._content, fill="both", expand=True)
        self.status.config(text="")
        self.progress["value"] = 0
        if idx == 0:
            self._reload_vanilla()
        elif idx == 1:
            self._reload_fabric()
        elif idx == 2:
            self._reload_forge()

    # --- Vanilla ----------------------------------------

    def _reload_vanilla(self):
        self.van_version_cb.set("Loading versions...")
        self.btn_download.config(state="disabled")
        def work():
            try:
                manifest = core.VersionManifest.fetch()
                vers = manifest.list_versions(all_versions=self.van_show_all.get())
            except Exception as e:
                vers = []
                err = str(e)
            else:
                err = None
            self.after(0, lambda: self._fill_vanilla(vers, err))
        threading_work(work)

    def _fill_vanilla(self, vers, err):
        if err:
            self.van_version_cb["values"] = []
            self.van_version_cb.set(f"Error: {err}")
            return
        if not vers:
            self.van_version_cb.set("No versions found")
            return
        self.van_version_cb["values"] = vers
        self.van_version_cb.current(0)
        self.btn_download.config(state="normal")

    # --- Fabric ----------------------------------------

    def _reload_fabric(self):
        self.fab_mc_cb.set("Loading...")
        self.fab_loader_cb.set("Loading...")
        self.btn_download.config(state="disabled")
        def work():
            try:
                mc_vers = _fetch_fabric_game_versions()
                loader_vers = _fetch_fabric_loader_versions()
                err = None
            except Exception as e:
                mc_vers, loader_vers = [], []
                err = str(e)
            self.after(0, lambda: self._fill_fabric(mc_vers, loader_vers, err))
        threading_work(work)

    def _fill_fabric(self, mc_vers, loader_vers, err):
        if err:
            self.fab_mc_cb.set(f"Error: {err}")
            return
        self.fab_mc_cb["values"] = mc_vers
        if mc_vers:
            self.fab_mc_cb.current(0)
        self.fab_loader_cb["values"] = loader_vers
        if loader_vers:
            self.fab_loader_cb.current(0)
        self.btn_download.config(state="normal")

    # --- Forge ----------------------------------------

    def _reload_forge(self):
        self.forge_mc_cb.set("Loading...")
        self.forge_ver_cb.set("Select MC version first")
        self.btn_download.config(state="disabled")
        def work():
            try:
                mc_vers = _fetch_forge_mc_versions()
                err = None
            except Exception as e:
                mc_vers = []
                err = str(e)
            self.after(0, lambda: self._fill_forge_mc(mc_vers, err))
        threading_work(work)

    def _fill_forge_mc(self, mc_vers, err):
        if err:
            self.forge_mc_cb.set(f"Error: {err}")
            return
        self.forge_mc_cb["values"] = mc_vers
        if mc_vers:
            self.forge_mc_cb.current(0)
            self._load_forge_versions(mc_vers[0])

    def _on_forge_mc_selected(self, _e=None):
        mc = self.forge_mc_cb.get()
        if mc:
            self._load_forge_versions(mc)

    def _load_forge_versions(self, mc_version):
        self.forge_ver_cb.set("Loading...")
        self.btn_download.config(state="disabled")
        def work():
            try:
                vers = _fetch_forge_versions_for(mc_version)
                err = None
            except Exception as e:
                vers = []
                err = str(e)
            self.after(0, lambda: self._fill_forge_ver(vers, err))
        threading_work(work)

    def _fill_forge_ver(self, vers, err):
        if err:
            self.forge_ver_cb.set(f"Error: {err}")
            return
        if not vers:
            self.forge_ver_cb.set("No Forge versions found")
            return
        self.forge_ver_cb["values"] = vers
        self.forge_ver_cb.current(0)
        self.btn_download.config(state="normal")

    # --- download (dispatches by tab) ----------------------------------------

    def _download(self):
        if self.downloading:
            return
        tab = self._current_tab
        if tab == 0:
            self._download_vanilla()
        elif tab == 1:
            self._download_fabric()
        elif tab == 2:
            self._download_forge()

    def _download_vanilla(self):
        version_id = self.van_version_cb.get()
        if not version_id or version_id.startswith(("Loading", "Error", "No ")):
            return
        if version_id in self.launcher.downloaded_versions():
            if not messagebox.askyesno(
                    "Already installed",
                    f"Version '{version_id}' is already installed.\nDownload again?"):
                return
        import threading as _thr
        self._cancelled = _thr.Event()
        self.downloading = True
        self.btn_download.config(state="disabled")
        self.status.config(text=f"Downloading {version_id}...")
        self.progress["value"] = 0
        cancel = self._cancelled
        def work():
            def report(msg, frac):
                if cancel.is_set():
                    return
                self.after(0, lambda: (self.status.config(text=msg),
                                       self.progress.__setitem__("value", int(frac * 100))))
            try:
                self.launcher.download_version(version_id, progress=report,
                                               cancel_event=cancel)
                if not cancel.is_set():
                    self.after(0, lambda: (
                        self.status.config(text=f"{version_id} installed \u2713"),
                        self.progress.__setitem__("value", 100)))
                    if self.on_done:
                        self.after(0, self.on_done)
            except core.DownloadCancelled:
                self.after(0, lambda: self.status.config(text="Download cancelled"))
            except Exception as e:
                if not cancel.is_set():
                    err = str(e)
                    self.after(0, lambda err=err: messagebox.showerror(
                        "Download error", err))
            finally:
                self.after(0, self._finish_download)
        threading_work(work)

    def _download_fabric(self):
        mc = self.fab_mc_cb.get()
        loader = self.fab_loader_cb.get()
        if not mc or not loader or mc.startswith(("Loading", "Error")):
            return
        import threading as _thr
        self._cancelled = _thr.Event()
        self.downloading = True
        self.btn_download.config(state="disabled")
        self.status.config(text=f"Installing Fabric {loader} for {mc}...")
        self.progress["value"] = 0
        cancel = self._cancelled
        def work():
            def report(msg, frac):
                if cancel.is_set():
                    return
                self.after(0, lambda: (self.status.config(text=msg),
                                       self.progress.__setitem__("value", int(frac * 100))))
            try:
                vid = _install_fabric(self.launcher.root, mc, loader,
                                      progress=report, cancel_event=cancel)
                if not cancel.is_set():
                    self.after(0, lambda: (
                        self.status.config(text=f"Fabric installed \u2713  ({vid})"),
                        self.progress.__setitem__("value", 100)))
                    if self.on_done:
                        self.after(0, self.on_done)
            except core.DownloadCancelled:
                self.after(0, lambda: self.status.config(text="Download cancelled"))
            except Exception as e:
                if not cancel.is_set():
                    err = str(e)
                    self.after(0, lambda err=err: messagebox.showerror(
                        "Fabric install error", err))
            finally:
                self.after(0, self._finish_download)
        threading_work(work)

    def _download_forge(self):
        forge_ver = self.forge_ver_cb.get()
        if not forge_ver or forge_ver.startswith(("Loading", "Error", "No ", "Select")):
            return
        import threading as _thr
        self._cancelled = _thr.Event()
        self.downloading = True
        self.btn_download.config(state="disabled")
        self.status.config(text=f"Installing Forge {forge_ver}...")
        self.progress["value"] = 0
        cancel = self._cancelled
        def work():
            def report(msg, frac):
                if cancel.is_set():
                    return
                self.after(0, lambda: (self.status.config(text=msg),
                                       self.progress.__setitem__("value", int(frac * 100))))
            try:
                _install_forge(self.launcher.root, forge_ver,
                               progress=report, cancel_event=cancel)
                if not cancel.is_set():
                    self.after(0, lambda: (
                        self.status.config(text=f"Forge {forge_ver} installed \u2713"),
                        self.progress.__setitem__("value", 100)))
                    if self.on_done:
                        self.after(0, self.on_done)
            except core.DownloadCancelled:
                self.after(0, lambda: self.status.config(text="Download cancelled"))
            except Exception as e:
                if not cancel.is_set():
                    err = str(e)
                    self.after(0, lambda err=err: messagebox.showerror(
                        "Forge install error", err))
            finally:
                self.after(0, self._finish_download)
        threading_work(work)

    def _finish_download(self):
        self.downloading = False
        self._cancelled = None
        try:
            self.btn_download.config(state="normal")
        except Exception:
            pass

    def _close(self):
        if self.downloading:
            if self._cancelled and not self._cancelled.is_set():
                self._cancelled.set()
                self.status.config(text="Cancelling...")
                self.btn_download.config(state="disabled")
            return
        self.destroy()

# --- settings window ------------------------------------------------------- #
class SettingsWindow(tk.Toplevel):
    def __init__(self, master, app):
        super().__init__(master)
        self.app = app
        self.cfg = app.cfg

        setup_style(self)
        self.title("Settings")
        self.transient(master)
        centre_win(self, 420, 420)
        self.resizable(False, False)
        self.configure(bg=BG)

        outer = ttk.Frame(self, style="BG.TFrame", padding=(16, 12))
        outer.pack(fill="both", expand=True)

        ttk.Label(outer, text="SETTINGS", style="Subhead.TLabel").pack(anchor="w")
        tk.Frame(outer, bg=ACCENT, height=2).pack(fill="x", pady=(6, 14))

        # Game directory
        ttk.Label(outer, text="Game directory", style="BG.TLabel").pack(anchor="w")
        self.game_dir_var = tk.StringVar(value=self.cfg.get("game_dir", str(DEFAULT_GAME_DIR)))
        row = ttk.Frame(outer, style="BG.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ent = ttk.Entry(row, textvariable=self.game_dir_var)
        ent.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse", command=self._browse).pack(
            side="left", padx=(6, 0))
        ttk.Label(outer,
                  text="All game data (versions, libraries, assets, Java, mods)\n"
                       "is stored here. Restart the launcher after changing.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 2))

        # Close on launch
        self.close_var = tk.BooleanVar(value=self.cfg.get("close_on_launch", True))
        ttk.Checkbutton(outer, text="Close the launcher after launching the game",
                        variable=self.close_var).pack(anchor="w", pady=4)

        tk.Frame(outer, bg=OUTLINE, height=1).pack(fill="x", pady=6)

        # Download threads
        ttk.Label(outer, text="Download threads", style="BG.TLabel").pack(anchor="w")
        self.threads_var = tk.StringVar(
            value=str(self.cfg.get("download_threads", core.DOWNLOAD_WORKERS)))
        ttk.Entry(outer, textvariable=self.threads_var).pack(fill="x", pady=(4, 0))
        ttk.Label(outer,
                  text="Number of files downloaded at the same time.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 2))

        tk.Frame(outer, bg=OUTLINE, height=1).pack(fill="x", pady=6)

        # Bundled Java info
        ttk.Label(outer, text="Java", style="BG.TLabel").pack(anchor="w")
        self.java_info = ttk.Label(
            outer, text=self._java_descr(), style="Muted.TLabel",
            wraplength=370, justify="left")
        self.java_info.pack(fill="x", pady=(2, 0))
        ttk.Label(outer,
                  text="Bundled Java is auto-downloaded; no system Java required.",
                  style="Muted.TLabel").pack(anchor="w", pady=(2, 2))

        row = ttk.Frame(outer, style="BG.TFrame")
        row.pack(fill="x", pady=(16, 0))
        ttk.Button(row, text="Save", style="Accent.TButton",
                   command=self._save).pack(side="left", fill="x",
                                            expand=True, padx=(0, 3))
        ttk.Button(row, text="Close", style="Secondary.TButton",
                   command=self.destroy).pack(side="left", fill="x",
                                              expand=True, padx=(3, 0))

    def _java_descr(self):
        b = self.app.launcher.java.bundled_javaw()
        if b:
            return "Installed: " + self.app.launcher.java.java_ver(b)
        return "Not installed"

    def _browse(self):
        from tkinter import filedialog
        p = filedialog.askdirectory(initialdir=self.game_dir_var.get() or str(Path.home()))
        if p:
            self.game_dir_var.set(p)

    def _save(self):
        try:
            threads = int(self.threads_var.get())
        except ValueError:
            threads = core.DOWNLOAD_WORKERS
        self.cfg["download_threads"] = threads
        self.cfg["close_on_launch"] = self.close_var.get()
        gd = self.game_dir_var.get().strip()
        if gd:
            self.cfg["game_dir"] = str(Path(gd))
        core.set_download_workers(threads)
        save_config(self.cfg)
        self.destroy()
        messagebox.showinfo(
            "Settings",
            "Settings saved.\n\nIf you changed the game directory, restart the "
            "launcher to use the new location.")
