from __future__ import annotations

import os
import sys
from pathlib import Path
from tkinter import messagebox


def application_dir() -> Path:
    if getattr(sys, "frozen", False):
        local = os.environ.get("LOCALAPPDATA")
        target = Path(local) / "StockScope" if local else Path(sys.executable).resolve().parent / "StockScope-data"
        target.mkdir(parents=True, exist_ok=True)
        return target
    return Path(__file__).resolve().parent


def main() -> int:
    try:
        from src.ui import run_app
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("matplotlib"):
            messagebox.showerror(
                "缺少运行组件",
                "尚未安装图表组件。请双击 start_windows.bat，程序会自动完成首次安装。",
            )
            return 2
        raise
    run_app(application_dir())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
