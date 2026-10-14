"""Releases custom minecraft launcher - entry point (GUI)."""

import os
import sys
from pathlib import Path

# Make `launcher` package importable regardless of cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))


def main():
    from launcher.gui import LauncherApp
    import tkinter as tk
    root = tk.Tk()
    app = LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
