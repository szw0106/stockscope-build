from __future__ import annotations

import json
import queue
import threading
import time
import tkinter as tk
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import matplotlib

matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle

from .analyzer import MarketAnalysis, StockAnalysis, analyze_market, analyze_stock, sma
from .data_provider import EastmoneyProvider, normalize_code
from .models import Board, DataBundle, Quote


COLORS = {
    "bg": "#071019",
    "panel": "#0d1824",
    "panel2": "#111f2d",
    "line": "#1c3145",
    "text": "#dbe7f3",
    "muted": "#7890a6",
    "red": "#ff5468",      # China market: up
    "green": "#23c78e",    # China market: down
    "gold": "#f2bf62",
    "cyan": "#4bc7e8",
    "blue": "#4f87ff",
    "white": "#f7fbff",
}


def money(value: float | None) -> str:
    if value is None:
        return "--"
    value = float(value)
    if abs(value) >= 1e8:
        return f"{value / 1e8:.2f}亿"
    if abs(value) >= 1e4:
        return f"{value / 1e4:.1f}万"
    return f"{value:,.0f}"


def price_color(change_pct: float) -> str:
    if change_pct > 0:
        return COLORS["red"]
    if change_pct < 0:
        return COLORS["green"]
    return COLORS["muted"]


class MetricCard(tk.Frame):
    def __init__(self, master: tk.Misc, title: str, value: str = "--", sub: str = "") -> None:
        super().__init__(master, bg=COLORS["panel2"], highlightthickness=1, highlightbackground=COLORS["line"])
        self.columnconfigure(0, weight=1)
        self.title = tk.Label(self, text=title, fg=COLORS["muted"], bg=COLORS["panel2"], font=("Microsoft YaHei UI", 9))
        self.title.grid(row=0, column=0, sticky="w", padx=13, pady=(9, 1))
        self.value = tk.Label(self, text=value, fg=COLORS["white"], bg=COLORS["panel2"], font=("Microsoft YaHei UI", 15, "bold"))
        self.value.grid(row=1, column=0, sticky="w", padx=13)
        self.sub = tk.Label(self, text=sub, fg=COLORS["muted"], bg=COLORS["panel2"], font=("Microsoft YaHei UI", 8))
        self.sub.grid(row=2, column=0, sticky="w", padx=13, pady=(1, 8))

    def set(self, value: str, sub: str = "", color: str | None = None) -> None:
        self.value.configure(text=value, fg=color or COLORS["white"])
        self.sub.configure(text=sub)


class StockScopeApp:
    def __init__(self, root: tk.Tk, base_dir: Path) -> None:
        self.root = root
        self.base_dir = base_dir
        self.cache_dir = base_dir / "cache"
        self.config_path = base_dir / "config.json"
        self.provider = EastmoneyProvider(self.cache_dir)
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="quotes")
        self.results: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.bundle: DataBundle | None = None
        self.analysis: StockAnalysis | None = None
        self.market_analysis: MarketAnalysis | None = None
        self.fetching = False
        self.closed = False
        self.last_full_fetch = 0.0
        self.last_success = 0.0
        self.next_fetch_at = time.monotonic()
        self.chart_mode = tk.StringVar(value="intraday")
        self.board_filter = tk.StringVar(value="全部")
        self.code_var = tk.StringVar(value=self._read_default_code())
        self.status_var = tk.StringVar(value="正在准备行情…")
        self.page_name = "stock"

        self._configure_root()
        self._configure_styles()
        self._build_shell()
        self._show_demo_immediately()
        self.root.after(150, self._poll_results)
        self.root.after(300, lambda: self._start_fetch(full=True))
        self.root.after(1_000, self._update_clock)
        self.root.protocol("WM_DELETE_WINDOW", self._close)

    def _read_default_code(self) -> str:
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            return normalize_code(str(payload.get("default_code", "002025")))
        except Exception:
            return "002025"

    def _save_default_code(self, code: str) -> None:
        try:
            self.config_path.write_text(
                json.dumps({"default_code": code}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _configure_root(self) -> None:
        self.root.title("StockScope A股实时分析")
        self.root.geometry("1440x900")
        self.root.minsize(1180, 760)
        self.root.configure(bg=COLORS["bg"])

    def _configure_styles(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Scope.Horizontal.TProgressbar",
            troughcolor=COLORS["line"],
            background=COLORS["cyan"],
            bordercolor=COLORS["panel"],
            lightcolor=COLORS["cyan"],
            darkcolor=COLORS["cyan"],
            thickness=10,
        )
        style.configure(
            "Scope.Treeview",
            background=COLORS["panel"],
            foreground=COLORS["text"],
            fieldbackground=COLORS["panel"],
            rowheight=32,
            borderwidth=0,
            font=("Microsoft YaHei UI", 9),
        )
        style.configure(
            "Scope.Treeview.Heading",
            background=COLORS["panel2"],
            foreground=COLORS["muted"],
            relief="flat",
            font=("Microsoft YaHei UI", 9),
        )
        style.map("Scope.Treeview", background=[("selected", "#173248")])
        style.configure(
            "Scope.TCombobox",
            fieldbackground=COLORS["panel2"],
            background=COLORS["panel2"],
            foreground=COLORS["text"],
            arrowcolor=COLORS["muted"],
        )

    def _build_shell(self) -> None:
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)
        header = tk.Frame(self.root, bg=COLORS["panel"], height=68, highlightthickness=1, highlightbackground=COLORS["line"])
        header.grid(row=0, column=0, sticky="ew")
        header.grid_propagate(False)
        header.columnconfigure(4, weight=1)

        brand = tk.Frame(header, bg=COLORS["panel"])
        brand.grid(row=0, column=0, sticky="w", padx=(22, 30), pady=10)
        tk.Label(brand, text="STOCK", fg=COLORS["white"], bg=COLORS["panel"], font=("Segoe UI", 15, "bold")).pack(side="left")
        tk.Label(brand, text="SCOPE", fg=COLORS["gold"], bg=COLORS["panel"], font=("Segoe UI", 15, "bold")).pack(side="left")
        tk.Label(brand, text="  A股实时分析", fg=COLORS["muted"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 9)).pack(side="left")

        self.nav_stock = self._nav_button(header, "个股深度", lambda: self._switch_page("stock"), True)
        self.nav_stock.grid(row=0, column=1, padx=4, pady=14)
        self.nav_market = self._nav_button(header, "大盘与板块", lambda: self._switch_page("market"), False)
        self.nav_market.grid(row=0, column=2, padx=4, pady=14)

        search = tk.Frame(header, bg=COLORS["panel2"], highlightthickness=1, highlightbackground=COLORS["line"])
        search.grid(row=0, column=3, sticky="e", padx=(28, 14), pady=14)
        tk.Label(search, text="股票代码", fg=COLORS["muted"], bg=COLORS["panel2"], font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(12, 6))
        entry = tk.Entry(
            search, textvariable=self.code_var, width=10, bg=COLORS["panel2"], fg=COLORS["white"],
            insertbackground=COLORS["white"], relief="flat", font=("Consolas", 12, "bold"),
        )
        entry.pack(side="left", padx=3, ipady=6)
        entry.bind("<Return>", lambda _event: self._change_symbol())
        tk.Button(
            search, text="分析", command=self._change_symbol, bg=COLORS["blue"], fg="white",
            activebackground="#6296ff", activeforeground="white", relief="flat", cursor="hand2",
            font=("Microsoft YaHei UI", 9, "bold"), padx=15, pady=6,
        ).pack(side="left")

        meta = tk.Frame(header, bg=COLORS["panel"])
        meta.grid(row=0, column=5, sticky="e", padx=(8, 22))
        self.clock_label = tk.Label(meta, text="--:--:--", fg=COLORS["text"], bg=COLORS["panel"], font=("Consolas", 11, "bold"))
        self.clock_label.pack(anchor="e")
        tk.Label(meta, textvariable=self.status_var, fg=COLORS["muted"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 8)).pack(anchor="e")

        self.content = tk.Frame(self.root, bg=COLORS["bg"])
        self.content.grid(row=1, column=0, sticky="nsew")
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)
        self.stock_page = tk.Frame(self.content, bg=COLORS["bg"])
        self.market_page = tk.Frame(self.content, bg=COLORS["bg"])
        self._build_stock_page()
        self._build_market_page()
        self.stock_page.grid(row=0, column=0, sticky="nsew")

        footer = tk.Frame(self.root, bg=COLORS["panel"], height=30)
        footer.grid(row=2, column=0, sticky="ew")
        footer.grid_propagate(False)
        tk.Label(
            footer,
            text="仅作信息分析与决策辅助，不构成投资建议；免费行情可能延迟或中断，请以券商终端为准。",
            fg=COLORS["muted"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 8),
        ).pack(side="left", padx=22, pady=6)
        self.source_label = tk.Label(footer, text="数据源：准备中", fg=COLORS["muted"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 8))
        self.source_label.pack(side="right", padx=22)

    def _nav_button(self, master: tk.Misc, text: str, command: Any, selected: bool) -> tk.Button:
        return tk.Button(
            master, text=text, command=command, relief="flat", cursor="hand2", padx=18, pady=8,
            bg=COLORS["blue"] if selected else COLORS["panel"],
            fg="white" if selected else COLORS["muted"],
            activebackground=COLORS["blue"], activeforeground="white",
            font=("Microsoft YaHei UI", 10, "bold" if selected else "normal"),
        )

    def _build_stock_page(self) -> None:
        page = self.stock_page
        page.rowconfigure(2, weight=1)
        page.columnconfigure(0, weight=1)

        hero = tk.Frame(page, bg=COLORS["bg"])
        hero.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 10))
        hero.columnconfigure(1, weight=1)
        identity = tk.Frame(hero, bg=COLORS["bg"])
        identity.grid(row=0, column=0, sticky="w")
        self.stock_name = tk.Label(identity, text="航天电器", fg=COLORS["white"], bg=COLORS["bg"], font=("Microsoft YaHei UI", 19, "bold"))
        self.stock_name.pack(side="left")
        self.stock_code = tk.Label(identity, text="002025 · 深市", fg=COLORS["muted"], bg=COLORS["bg"], font=("Consolas", 10))
        self.stock_code.pack(side="left", padx=12, pady=(8, 0))
        self.demo_badge = tk.Label(identity, text="演示", fg=COLORS["gold"], bg="#322817", font=("Microsoft YaHei UI", 8, "bold"), padx=8, pady=3)
        self.demo_badge.pack(side="left", padx=4, pady=(5, 0))

        price_box = tk.Frame(hero, bg=COLORS["bg"])
        price_box.grid(row=0, column=2, sticky="e")
        self.price_label = tk.Label(price_box, text="--", fg=COLORS["white"], bg=COLORS["bg"], font=("Segoe UI", 28, "bold"))
        self.price_label.pack(side="left")
        self.change_label = tk.Label(price_box, text="--  --%", fg=COLORS["muted"], bg=COLORS["bg"], font=("Segoe UI", 13, "bold"))
        self.change_label.pack(side="left", padx=(14, 0), pady=(12, 0))

        metrics = tk.Frame(page, bg=COLORS["bg"])
        metrics.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 12))
        for col in range(8):
            metrics.columnconfigure(col, weight=1, uniform="metric")
        metric_titles = ["今开", "最高", "最低", "昨收", "成交额", "换手率", "市盈率", "所属行业"]
        self.metric_cards: list[MetricCard] = []
        for col, title in enumerate(metric_titles):
            card = MetricCard(metrics, title)
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 4, 0 if col == 7 else 4))
            self.metric_cards.append(card)

        body = tk.Frame(page, bg=COLORS["bg"])
        body.grid(row=2, column=0, sticky="nsew", padx=22, pady=(0, 16))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=7)
        body.columnconfigure(1, weight=3)

        chart_panel = tk.Frame(body, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        chart_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        chart_panel.rowconfigure(1, weight=1)
        chart_panel.columnconfigure(0, weight=1)
        chart_head = tk.Frame(chart_panel, bg=COLORS["panel"])
        chart_head.grid(row=0, column=0, sticky="ew", padx=15, pady=(12, 0))
        tk.Label(chart_head, text="价格走势", fg=COLORS["text"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
        self.chart_stats = tk.Label(chart_head, text="", fg=COLORS["muted"], bg=COLORS["panel"], font=("Consolas", 9))
        self.chart_stats.pack(side="left", padx=16)
        self.day_button = self._mode_button(chart_head, "日K", "daily")
        self.day_button.pack(side="right", padx=(4, 0))
        self.minute_button = self._mode_button(chart_head, "分时", "intraday")
        self.minute_button.pack(side="right")

        self.figure = Figure(figsize=(8.8, 5.4), dpi=100, facecolor=COLORS["panel"])
        self.chart_canvas = FigureCanvasTkAgg(self.figure, master=chart_panel)
        self.chart_canvas.get_tk_widget().configure(bg=COLORS["panel"], highlightthickness=0)
        self.chart_canvas.get_tk_widget().grid(row=1, column=0, sticky="nsew", padx=8, pady=6)

        signal_panel = tk.Frame(body, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        signal_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        signal_panel.columnconfigure(0, weight=1)
        tk.Label(signal_panel, text="止跌确认仪", fg=COLORS["text"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=0, sticky="w", padx=18, pady=(15, 2))
        score_line = tk.Frame(signal_panel, bg=COLORS["panel"])
        score_line.grid(row=1, column=0, sticky="ew", padx=18)
        self.score_label = tk.Label(score_line, text="--", fg=COLORS["cyan"], bg=COLORS["panel"], font=("Segoe UI", 29, "bold"))
        self.score_label.pack(side="left")
        tk.Label(score_line, text=" / 100", fg=COLORS["muted"], bg=COLORS["panel"], font=("Segoe UI", 10)).pack(side="left", pady=(13, 0))
        self.verdict_label = tk.Label(score_line, text="等待数据", fg=COLORS["muted"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 10, "bold"))
        self.verdict_label.pack(side="right", pady=(13, 0))
        self.score_bar = ttk.Progressbar(signal_panel, style="Scope.Horizontal.TProgressbar", orient="horizontal", maximum=100)
        self.score_bar.grid(row=2, column=0, sticky="ew", padx=18, pady=(2, 10))

        self.check_frame = tk.Frame(signal_panel, bg=COLORS["panel"])
        self.check_frame.grid(row=3, column=0, sticky="ew", padx=18)
        self.check_rows: list[tuple[tk.Label, tk.Label]] = []
        for row in range(7):
            line = tk.Frame(self.check_frame, bg=COLORS["panel2"], highlightthickness=1, highlightbackground=COLORS["line"])
            line.grid(row=row, column=0, sticky="ew", pady=2)
            line.columnconfigure(1, weight=1)
            icon = tk.Label(line, text="·", width=2, fg=COLORS["muted"], bg=COLORS["panel2"], font=("Segoe UI Symbol", 11, "bold"))
            icon.grid(row=0, column=0, rowspan=2, padx=(5, 0))
            label = tk.Label(line, text="等待分析", fg=COLORS["text"], bg=COLORS["panel2"], font=("Microsoft YaHei UI", 8, "bold"))
            label.grid(row=0, column=1, sticky="w", padx=3, pady=(4, 0))
            detail = tk.Label(line, text="", fg=COLORS["muted"], bg=COLORS["panel2"], font=("Microsoft YaHei UI", 7))
            detail.grid(row=1, column=1, sticky="w", padx=3, pady=(0, 4))
            self.check_rows.append((icon, label, detail))

        tk.Label(signal_panel, text="观察计划（仅供参考）", fg=COLORS["text"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 10, "bold")).grid(row=4, column=0, sticky="w", padx=18, pady=(13, 4))
        plan = tk.Frame(signal_panel, bg=COLORS["panel2"], highlightthickness=1, highlightbackground=COLORS["line"])
        plan.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 14))
        plan.columnconfigure(0, weight=1)
        plan.columnconfigure(1, weight=1)
        self.plan_labels: dict[str, tk.Label] = {}
        for idx, (key, title) in enumerate([("watch", "确认价"), ("stop", "失效参考"), ("resistance", "第一压力"), ("position", "试仓上限")]):
            cell = tk.Frame(plan, bg=COLORS["panel2"])
            cell.grid(row=idx // 2, column=idx % 2, sticky="ew", padx=10, pady=7)
            tk.Label(cell, text=title, fg=COLORS["muted"], bg=COLORS["panel2"], font=("Microsoft YaHei UI", 8)).pack(anchor="w")
            value = tk.Label(cell, text="--", fg=COLORS["white"], bg=COLORS["panel2"], font=("Consolas", 11, "bold"))
            value.pack(anchor="w")
            self.plan_labels[key] = value

    def _mode_button(self, master: tk.Misc, text: str, mode: str) -> tk.Button:
        return tk.Button(
            master, text=text, command=lambda: self._set_chart_mode(mode), relief="flat", cursor="hand2",
            bg=COLORS["blue"] if self.chart_mode.get() == mode else COLORS["panel2"],
            fg="white" if self.chart_mode.get() == mode else COLORS["muted"],
            activebackground=COLORS["blue"], activeforeground="white",
            font=("Microsoft YaHei UI", 8, "bold"), padx=12, pady=4,
        )

    def _build_market_page(self) -> None:
        page = self.market_page
        page.rowconfigure(2, weight=1)
        page.columnconfigure(0, weight=1)
        title = tk.Frame(page, bg=COLORS["bg"])
        title.grid(row=0, column=0, sticky="ew", padx=22, pady=(18, 10))
        tk.Label(title, text="大盘与板块强弱", fg=COLORS["white"], bg=COLORS["bg"], font=("Microsoft YaHei UI", 19, "bold")).pack(side="left")
        tk.Label(title, text="先判断环境，再判断个股", fg=COLORS["muted"], bg=COLORS["bg"], font=("Microsoft YaHei UI", 9)).pack(side="left", padx=14, pady=(8, 0))

        cards = tk.Frame(page, bg=COLORS["bg"])
        cards.grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 12))
        for col in range(4):
            cards.columnconfigure(col, weight=1, uniform="index")
        self.market_cards: list[MetricCard] = []
        for col, name in enumerate(["上证指数", "深证成指", "创业板指", "沪深300"]):
            card = MetricCard(cards, name)
            card.grid(row=0, column=col, sticky="ew", padx=(0 if col == 0 else 5, 0 if col == 3 else 5))
            self.market_cards.append(card)

        body = tk.Frame(page, bg=COLORS["bg"])
        body.grid(row=2, column=0, sticky="nsew", padx=22, pady=(0, 16))
        body.rowconfigure(0, weight=1)
        body.columnconfigure(0, weight=1)
        body.columnconfigure(1, weight=3)

        regime = tk.Frame(body, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        regime.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        regime.columnconfigure(0, weight=1)
        tk.Label(regime, text="市场环境", fg=COLORS["text"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 12, "bold")).grid(row=0, column=0, sticky="w", padx=20, pady=(19, 2))
        self.market_score = tk.Label(regime, text="--", fg=COLORS["cyan"], bg=COLORS["panel"], font=("Segoe UI", 42, "bold"))
        self.market_score.grid(row=1, column=0, pady=(18, 0))
        self.market_regime = tk.Label(regime, text="等待数据", fg=COLORS["muted"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 15, "bold"))
        self.market_regime.grid(row=2, column=0)
        self.market_bar = ttk.Progressbar(regime, style="Scope.Horizontal.TProgressbar", orient="horizontal", maximum=100)
        self.market_bar.grid(row=3, column=0, sticky="ew", padx=25, pady=15)
        self.market_summary = tk.Label(
            regime, text="", wraplength=290, justify="left", fg=COLORS["text"], bg=COLORS["panel2"],
            font=("Microsoft YaHei UI", 10), padx=14, pady=14,
        )
        self.market_summary.grid(row=4, column=0, sticky="ew", padx=20, pady=(2, 12))
        self.market_breadth = tk.Label(regime, text="板块上涨家数  --", fg=COLORS["muted"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 9))
        self.market_breadth.grid(row=5, column=0, sticky="w", padx=22, pady=5)
        self.market_index_avg = tk.Label(regime, text="核心指数均值  --", fg=COLORS["muted"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 9))
        self.market_index_avg.grid(row=6, column=0, sticky="w", padx=22, pady=5)

        boards_panel = tk.Frame(body, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        boards_panel.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        boards_panel.rowconfigure(1, weight=1)
        boards_panel.columnconfigure(0, weight=1)
        boards_head = tk.Frame(boards_panel, bg=COLORS["panel"])
        boards_head.grid(row=0, column=0, sticky="ew", padx=16, pady=13)
        tk.Label(boards_head, text="领涨板块", fg=COLORS["text"], bg=COLORS["panel"], font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
        combo = ttk.Combobox(boards_head, textvariable=self.board_filter, values=("全部", "行业", "概念"), width=8, state="readonly", style="Scope.TCombobox")
        combo.pack(side="right")
        combo.bind("<<ComboboxSelected>>", lambda _event: self._update_board_table())

        columns = ("rank", "name", "type", "pct", "turnover", "flow")
        self.board_tree = ttk.Treeview(boards_panel, columns=columns, show="headings", style="Scope.Treeview")
        headings = {"rank": "排名", "name": "板块", "type": "类型", "pct": "涨跌幅", "turnover": "换手率", "flow": "主力净流入"}
        widths = {"rank": 55, "name": 150, "type": 75, "pct": 95, "turnover": 95, "flow": 130}
        for key in columns:
            self.board_tree.heading(key, text=headings[key])
            self.board_tree.column(key, width=widths[key], anchor="center" if key != "name" else "w")
        self.board_tree.tag_configure("up", foreground=COLORS["red"])
        self.board_tree.tag_configure("down", foreground=COLORS["green"])
        self.board_tree.tag_configure("flat", foreground=COLORS["text"])
        scrollbar = ttk.Scrollbar(boards_panel, orient="vertical", command=self.board_tree.yview)
        self.board_tree.configure(yscrollcommand=scrollbar.set)
        self.board_tree.grid(row=1, column=0, sticky="nsew", padx=(16, 0), pady=(0, 16))
        scrollbar.grid(row=1, column=1, sticky="ns", padx=(0, 12), pady=(0, 16))

    def _show_demo_immediately(self) -> None:
        bundle = self.provider.demo_bundle(self.code_var.get())
        self._apply_bundle(bundle)
        self.status_var.set("正在连接实时行情…")

    def _change_symbol(self) -> None:
        try:
            code = normalize_code(self.code_var.get())
        except ValueError as exc:
            messagebox.showwarning("代码格式不正确", str(exc), parent=self.root)
            return
        self.code_var.set(code)
        self._save_default_code(code)
        self._apply_bundle(self.provider.demo_bundle(code))
        self.last_full_fetch = 0
        self._start_fetch(full=True)

    def _switch_page(self, page: str) -> None:
        self.page_name = page
        if page == "stock":
            self.market_page.grid_forget()
            self.stock_page.grid(row=0, column=0, sticky="nsew")
        else:
            self.stock_page.grid_forget()
            self.market_page.grid(row=0, column=0, sticky="nsew")
        self.nav_stock.configure(bg=COLORS["blue"] if page == "stock" else COLORS["panel"], fg="white" if page == "stock" else COLORS["muted"])
        self.nav_market.configure(bg=COLORS["blue"] if page == "market" else COLORS["panel"], fg="white" if page == "market" else COLORS["muted"])

    def _set_chart_mode(self, mode: str) -> None:
        self.chart_mode.set(mode)
        self.minute_button.configure(bg=COLORS["blue"] if mode == "intraday" else COLORS["panel2"], fg="white" if mode == "intraday" else COLORS["muted"])
        self.day_button.configure(bg=COLORS["blue"] if mode == "daily" else COLORS["panel2"], fg="white" if mode == "daily" else COLORS["muted"])
        if self.bundle:
            self._draw_chart(self.bundle)

    def _start_fetch(self, full: bool = False) -> None:
        if self.closed or self.fetching:
            return
        self.fetching = True
        self.next_fetch_at = float("inf")
        code = self.code_var.get()
        self.status_var.set("正在更新实时行情…")

        def task() -> None:
            try:
                if full or self.bundle is None:
                    result = self.provider.get_bundle(code)
                    self.results.put(("bundle", result))
                else:
                    quote = self.provider.get_quote(code)
                    trends = self.provider.get_trends(code)
                    self.results.put(("fast", (code, quote, trends)))
            except Exception as exc:
                self.results.put(("error", str(exc)))

        self.executor.submit(task)

    def _poll_results(self) -> None:
        if self.closed:
            return
        try:
            while True:
                kind, payload = self.results.get_nowait()
                self.fetching = False
                if kind == "bundle":
                    if payload.quote.code == self.code_var.get():
                        self.bundle = payload
                        self.last_full_fetch = time.monotonic()
                        self.last_success = time.monotonic()
                        self._apply_bundle(payload)
                    self.next_fetch_at = time.monotonic() + 12
                elif kind == "fast":
                    code, quote, trends = payload
                    if self.bundle and code == self.code_var.get():
                        was_demo = self.bundle.quote.is_demo
                        if was_demo:
                            quote.source = "实时价格 + 等待完整图表"
                            quote.is_demo = True
                            self.last_full_fetch = 0
                        self.bundle.quote = quote
                        self.bundle.trends = trends
                        self.last_success = time.monotonic()
                        self._apply_bundle(self.bundle)
                    self.next_fetch_at = time.monotonic() + 12
                elif kind == "error":
                    self.status_var.set(f"更新失败，保留最近数据 · {payload[:28]}")
                    self.next_fetch_at = time.monotonic() + 15
        except queue.Empty:
            pass

        if not self.fetching and time.monotonic() >= self.next_fetch_at:
            full_due = time.monotonic() - self.last_full_fetch > 90
            self._start_fetch(full=full_due)
        self.root.after(200, self._poll_results)

    def _apply_bundle(self, bundle: DataBundle) -> None:
        self.bundle = bundle
        self.analysis = analyze_stock(bundle.quote, bundle.trends, bundle.daily)
        self.market_analysis = analyze_market(bundle.markets, bundle.boards)
        self._update_stock_header(bundle.quote)
        self._update_metrics(bundle.quote)
        self._update_signal(self.analysis)
        self._draw_chart(bundle)
        self._update_market(bundle)
        self._update_board_table()
        source = bundle.quote.source
        self.source_label.configure(text=f"数据源：{source}")
        if bundle.quote.is_demo:
            self.demo_badge.pack(side="left", padx=4, pady=(5, 0))
            self.status_var.set("演示数据 · 正在尝试连接")
        else:
            self.demo_badge.pack_forget()
            suffix = f" · {len(bundle.errors)}项降级" if bundle.errors else ""
            self.status_var.set(f"已更新 {bundle.quote.updated_at:%H:%M:%S}{suffix}")

    def _update_stock_header(self, quote: Quote) -> None:
        market = "沪市" if quote.code.startswith(("5", "6", "9")) else "深市"
        self.stock_name.configure(text=quote.name)
        self.stock_code.configure(text=f"{quote.code} · {market}")
        color = price_color(quote.change_pct)
        self.price_label.configure(text=f"{quote.price:.2f}", fg=color)
        sign = "+" if quote.change > 0 else ""
        self.change_label.configure(text=f"{sign}{quote.change:.2f}  {sign}{quote.change_pct:.2f}%", fg=color)

    def _update_metrics(self, quote: Quote) -> None:
        values = [
            (f"{quote.open:.2f}", ""),
            (f"{quote.high:.2f}", ""),
            (f"{quote.low:.2f}", ""),
            (f"{quote.prev_close:.2f}", ""),
            (money(quote.amount), f"成交量 {money(quote.volume)}"),
            (f"{quote.turnover:.2f}%", f"振幅 {quote.amplitude:.2f}%"),
            (f"{quote.pe:.1f}" if quote.pe is not None else "--", f"PB {quote.pb:.2f}" if quote.pb is not None else "PB --"),
            (quote.industry[:12], "免费源分类"),
        ]
        for card, (value, sub) in zip(self.metric_cards, values):
            card.set(value, sub)

    def _update_signal(self, analysis: StockAnalysis) -> None:
        tone_colors = {"positive": COLORS["red"], "watch": COLORS["gold"], "neutral": COLORS["cyan"], "negative": COLORS["green"]}
        color = tone_colors.get(analysis.tone, COLORS["cyan"])
        self.score_label.configure(text=str(analysis.score), fg=color)
        self.verdict_label.configure(text=analysis.verdict, fg=color)
        self.score_bar["value"] = analysis.score
        for widgets, check in zip(self.check_rows, analysis.checks):
            icon, label, detail = widgets
            icon.configure(text="✓" if check.passed else "×", fg=COLORS["red"] if check.passed else COLORS["muted"])
            label.configure(text=check.label)
            detail.configure(text=check.detail)
        self.plan_labels["watch"].configure(text=f"{analysis.watch_price:.2f}")
        self.plan_labels["stop"].configure(text=f"{analysis.stop_reference:.2f}", fg=COLORS["green"])
        self.plan_labels["resistance"].configure(text=f"{analysis.resistance1:.2f}")
        self.plan_labels["position"].configure(text=f"{analysis.initial_position}%", fg=COLORS["gold"] if analysis.initial_position else COLORS["muted"])
        stats = [
            f"MA5 {analysis.sma5:.2f}" if analysis.sma5 else "MA5 --",
            f"MA20 {analysis.sma20:.2f}" if analysis.sma20 else "MA20 --",
            f"RSI {analysis.rsi14:.1f}" if analysis.rsi14 is not None else "RSI --",
            f"VWAP {analysis.vwap:.2f}" if analysis.vwap else "VWAP --",
        ]
        self.chart_stats.configure(text="  ·  ".join(stats))

    def _draw_chart(self, bundle: DataBundle) -> None:
        self.figure.clear()
        grid = self.figure.add_gridspec(4, 1, hspace=0.04)
        price_ax = self.figure.add_subplot(grid[:3, 0])
        volume_ax = self.figure.add_subplot(grid[3, 0], sharex=price_ax)
        for axis in (price_ax, volume_ax):
            axis.set_facecolor(COLORS["panel"])
            axis.tick_params(colors=COLORS["muted"], labelsize=8, length=0)
            axis.grid(True, color=COLORS["line"], linewidth=0.55, alpha=0.75)
            axis.spines[:].set_visible(False)
            axis.yaxis.tick_right()
        if self.chart_mode.get() == "daily":
            self._draw_daily(price_ax, volume_ax, bundle)
        else:
            self._draw_intraday(price_ax, volume_ax, bundle)
        price_ax.tick_params(labelbottom=False)
        self.figure.subplots_adjust(left=0.035, right=0.94, top=0.96, bottom=0.08)
        self.chart_canvas.draw_idle()

    def _draw_intraday(self, price_ax: Any, volume_ax: Any, bundle: DataBundle) -> None:
        trends = bundle.trends
        if not trends:
            return
        x = list(range(len(trends)))
        prices = [point.price for point in trends]
        averages = [point.avg_price for point in trends]
        volume = [point.volume / 10_000 for point in trends]
        prev = bundle.quote.prev_close
        price_ax.plot(x, prices, color=COLORS["cyan"], linewidth=1.45, label="价格")
        if any(averages):
            price_ax.plot(x, averages, color=COLORS["gold"], linewidth=1.05, alpha=0.9, label="均价")
        price_ax.axhline(prev, color=COLORS["muted"], linewidth=0.8, linestyle="--", alpha=0.8)
        price_ax.fill_between(x, prices, prev, where=[value >= prev for value in prices], color=COLORS["red"], alpha=0.06)
        price_ax.fill_between(x, prices, prev, where=[value < prev for value in prices], color=COLORS["green"], alpha=0.06)
        price_ax.legend(loc="upper left", frameon=False, labelcolor=COLORS["muted"], fontsize=8, ncol=2)
        bar_colors = [COLORS["red"] if i == 0 or prices[i] >= prices[i - 1] else COLORS["green"] for i in x]
        volume_ax.bar(x, volume, color=bar_colors, width=0.8, alpha=0.65)
        volume_ax.set_ylabel("万手", color=COLORS["muted"], fontsize=7)
        ticks = [0, min(60, len(x) - 1), min(120, len(x) - 1), min(180, len(x) - 1), len(x) - 1]
        labels = [trends[index].time.strftime("%H:%M") for index in ticks]
        volume_ax.set_xticks(ticks, labels)
        price_ax.set_xlim(0, max(240, len(x) - 1))

    def _draw_daily(self, price_ax: Any, volume_ax: Any, bundle: DataBundle) -> None:
        bars = bundle.daily[-80:]
        if not bars:
            return
        x = list(range(len(bars)))
        closes = [bar.close for bar in bars]
        full_closes = [bar.close for bar in bundle.daily]
        for index, bar in enumerate(bars):
            color = COLORS["red"] if bar.close >= bar.open else COLORS["green"]
            price_ax.vlines(index, bar.low, bar.high, color=color, linewidth=0.8)
            lower = min(bar.open, bar.close)
            height = max(abs(bar.close - bar.open), bundle.quote.price * 0.0005)
            price_ax.add_patch(Rectangle((index - 0.32, lower), 0.64, height, facecolor=color, edgecolor=color, linewidth=0.5))
            volume_ax.bar(index, bar.volume / 1e6, color=color, width=0.65, alpha=0.65)
        for period, color in [(5, COLORS["cyan"]), (10, COLORS["gold"]), (20, COLORS["blue"])]:
            values: list[float] = []
            offset = len(bundle.daily) - len(bars)
            for absolute_index in range(offset, len(bundle.daily)):
                window = full_closes[: absolute_index + 1]
                values.append(sma(window, period) or closes[absolute_index - offset])
            price_ax.plot(x, values, color=color, linewidth=1.0, label=f"MA{period}")
        price_ax.legend(loc="upper left", frameon=False, labelcolor=COLORS["muted"], fontsize=8, ncol=3)
        ticks = sorted(set([0, len(x) // 4, len(x) // 2, len(x) * 3 // 4, len(x) - 1]))
        volume_ax.set_xticks(ticks, [bars[index].date.strftime("%m-%d") for index in ticks])
        volume_ax.set_ylabel("百万手", color=COLORS["muted"], fontsize=7)
        price_ax.set_xlim(-1, len(x))

    def _update_market(self, bundle: DataBundle) -> None:
        for index, card in enumerate(self.market_cards):
            if index >= len(bundle.markets):
                card.set("--", "")
                continue
            item = bundle.markets[index]
            sign = "+" if item.change_pct > 0 else ""
            card.title.configure(text=item.name)
            card.set(f"{item.price:,.2f}", f"{sign}{item.change:.2f}  {sign}{item.change_pct:.2f}%", price_color(item.change_pct))
        analysis = self.market_analysis
        if not analysis:
            return
        color = COLORS["red"] if analysis.score >= 68 else COLORS["gold"] if analysis.score >= 45 else COLORS["green"]
        self.market_score.configure(text=str(analysis.score), fg=color)
        self.market_regime.configure(text=analysis.regime, fg=color)
        self.market_bar["value"] = analysis.score
        self.market_summary.configure(text=analysis.summary)
        self.market_breadth.configure(text=f"样本板块上涨  {analysis.positive_boards} / {analysis.total_boards}  ({analysis.board_breadth * 100:.0f}%)")
        sign = "+" if analysis.index_average > 0 else ""
        self.market_index_avg.configure(text=f"核心指数平均涨跌  {sign}{analysis.index_average:.2f}%")

    def _update_board_table(self) -> None:
        if not self.bundle:
            return
        selected = self.board_filter.get()
        boards = [item for item in self.bundle.boards if selected == "全部" or item.board_type == selected]
        boards.sort(key=lambda item: item.change_pct, reverse=True)
        for item in self.board_tree.get_children():
            self.board_tree.delete(item)
        for rank, board in enumerate(boards[:30], start=1):
            sign = "+" if board.change_pct > 0 else ""
            flow_sign = "+" if board.main_net_inflow > 0 else ""
            tag = "up" if board.change_pct > 0 else "down" if board.change_pct < 0 else "flat"
            self.board_tree.insert(
                "", "end",
                values=(rank, board.name, board.board_type, f"{sign}{board.change_pct:.2f}%", f"{board.turnover:.2f}%", f"{flow_sign}{money(board.main_net_inflow)}"),
                tags=(tag,),
            )

    def _update_clock(self) -> None:
        if self.closed:
            return
        now = datetime.now()
        self.clock_label.configure(text=now.strftime("%H:%M:%S"))
        self.root.after(1_000, self._update_clock)

    def _close(self) -> None:
        self.closed = True
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.root.destroy()


def run_app(base_dir: Path) -> None:
    root = tk.Tk()
    StockScopeApp(root, base_dir)
    root.mainloop()
