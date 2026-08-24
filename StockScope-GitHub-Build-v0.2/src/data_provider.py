from __future__ import annotations

import json
import math
import random
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .models import Board, DailyBar, DataBundle, MarketIndex, Quote, TrendPoint


class DataProviderError(RuntimeError):
    pass


def normalize_code(code: str) -> str:
    digits = "".join(char for char in code.strip() if char.isdigit())
    if len(digits) != 6:
        raise ValueError("请输入6位A股代码，例如 002025")
    return digits


def to_secid(code: str) -> str:
    code = normalize_code(code)
    market = "1" if code.startswith(("5", "6", "9")) else "0"
    return f"{market}.{code}"


def _num(value: Any, default: float = 0.0) -> float:
    if value in (None, "", "-"):
        return default
    try:
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


class EastmoneyProvider:
    """Small, dependency-free adapter for Eastmoney's public quote endpoints.

    These endpoints are free and require no account, but are not an SLA-backed
    licensed feed. The UI makes that limitation visible and caches good data.
    """

    QUOTE_URL = "https://push2.eastmoney.com/api/qt/stock/get"
    TREND_URL = "https://push2his.eastmoney.com/api/qt/stock/trends2/get"
    KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    MULTI_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"
    BOARD_URL = "https://push2.eastmoney.com/api/qt/clist/get"

    def __init__(self, cache_dir: Path, timeout: float = 5.0) -> None:
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.user_agent = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
        )

    def _request(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        request = Request(
            f"{url}?{urlencode(params)}",
            headers={"User-Agent": self.user_agent, "Referer": "https://quote.eastmoney.com/"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
            payload = json.loads(body)
        except Exception as exc:
            raise DataProviderError(f"行情请求失败：{exc}") from exc
        if payload.get("data") is None:
            raise DataProviderError("行情接口未返回有效数据")
        return payload

    def get_quote(self, code: str) -> Quote:
        code = normalize_code(code)
        fields = ",".join(
            [
                "f43", "f44", "f45", "f46", "f47", "f48", "f57", "f58",
                "f60", "f86", "f116", "f117", "f127", "f162", "f167",
                "f168", "f169", "f170", "f171",
            ]
        )
        payload = self._request(
            self.QUOTE_URL,
            {"secid": to_secid(code), "fields": fields, "fltt": 2, "invt": 2},
        )
        data = payload["data"]
        timestamp = _num(data.get("f86"))
        updated = datetime.fromtimestamp(timestamp) if timestamp > 1_000_000_000 else datetime.now()
        quote = Quote(
            code=str(data.get("f57") or code),
            name=str(data.get("f58") or code),
            price=_num(data.get("f43")),
            high=_num(data.get("f44")),
            low=_num(data.get("f45")),
            open=_num(data.get("f46")),
            volume=_num(data.get("f47")),
            amount=_num(data.get("f48")),
            prev_close=_num(data.get("f60")),
            total_market_cap=_num(data.get("f116")) or None,
            float_market_cap=_num(data.get("f117")) or None,
            industry=str(data.get("f127") or "--"),
            pe=_num(data.get("f162")) or None,
            pb=_num(data.get("f167")) or None,
            turnover=_num(data.get("f168")),
            change=_num(data.get("f169")),
            change_pct=_num(data.get("f170")),
            amplitude=_num(data.get("f171")),
            updated_at=updated,
        )
        if quote.price <= 0:
            raise DataProviderError("股票代码无效或当前没有报价")
        self._save_cache(f"quote_{code}.json", data)
        return quote

    def get_trends(self, code: str) -> list[TrendPoint]:
        code = normalize_code(code)
        payload = self._request(
            self.TREND_URL,
            {
                "secid": to_secid(code),
                "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
                "iscr": 0,
                "ndays": 1,
            },
        )
        rows = payload["data"].get("trends") or []
        trends: list[TrendPoint] = []
        for row in rows:
            parts = row.split(",")
            if len(parts) < 7:
                continue
            try:
                point_time = datetime.strptime(parts[0], "%Y-%m-%d %H:%M")
            except ValueError:
                continue
            trends.append(
                TrendPoint(
                    time=point_time,
                    price=_num(parts[1]),
                    volume=_num(parts[2]),
                    amount=_num(parts[3]),
                    avg_price=_num(parts[4]),
                    high=_num(parts[5]),
                    low=_num(parts[6]),
                )
            )
        if not trends:
            raise DataProviderError("分时数据为空")
        return trends

    def get_daily(self, code: str, limit: int = 180) -> list[DailyBar]:
        code = normalize_code(code)
        payload = self._request(
            self.KLINE_URL,
            {
                "secid": to_secid(code),
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "klt": 101,
                "fqt": 1,
                "end": 20500101,
                "lmt": limit,
            },
        )
        rows = payload["data"].get("klines") or []
        bars: list[DailyBar] = []
        for row in rows:
            parts = row.split(",")
            if len(parts) < 11:
                continue
            try:
                date = datetime.strptime(parts[0], "%Y-%m-%d")
            except ValueError:
                continue
            bars.append(
                DailyBar(
                    date=date,
                    open=_num(parts[1]),
                    close=_num(parts[2]),
                    high=_num(parts[3]),
                    low=_num(parts[4]),
                    volume=_num(parts[5]),
                    amount=_num(parts[6]),
                    change_pct=_num(parts[8]),
                    turnover=_num(parts[10]),
                )
            )
        if len(bars) < 20:
            raise DataProviderError("日线数据不足")
        return bars

    def get_markets(self) -> list[MarketIndex]:
        secids = "1.000001,0.399001,0.399006,1.000300"
        payload = self._request(
            self.MULTI_URL,
            {
                "secids": secids,
                "fields": "f12,f14,f2,f3,f4",
                "fltt": 2,
                "invt": 2,
            },
        )
        rows = payload["data"].get("diff") or []
        return [
            MarketIndex(
                code=str(row.get("f12") or ""),
                name=str(row.get("f14") or "指数"),
                price=_num(row.get("f2")),
                change_pct=_num(row.get("f3")),
                change=_num(row.get("f4")),
            )
            for row in rows
        ]

    def get_boards(self, board_type: str = "行业", limit: int = 100) -> list[Board]:
        fs = "m:90+t:2" if board_type == "行业" else "m:90+t:3"
        payload = self._request(
            self.BOARD_URL,
            {
                "pn": 1,
                "pz": limit,
                "po": 1,
                "np": 1,
                "fltt": 2,
                "invt": 2,
                "fid": "f3",
                "fs": fs,
                "fields": "f2,f3,f4,f8,f12,f14,f62",
            },
        )
        rows = payload["data"].get("diff") or []
        return [
            Board(
                code=str(row.get("f12") or ""),
                name=str(row.get("f14") or "--"),
                price=_num(row.get("f2")),
                change_pct=_num(row.get("f3")),
                change=_num(row.get("f4")),
                turnover=_num(row.get("f8")),
                main_net_inflow=_num(row.get("f62")),
                board_type=board_type,
            )
            for row in rows
        ]

    def get_bundle(self, code: str) -> DataBundle:
        errors: list[str] = []
        try:
            quote = self.get_quote(code)
        except Exception as exc:
            errors.append(str(exc))
            return self.demo_bundle(code, errors)

        def guarded(label: str, call: Any, default: Any) -> Any:
            try:
                return call()
            except Exception as exc:
                errors.append(f"{label}：{exc}")
                return default

        trends = guarded("分时", lambda: self.get_trends(code), [])
        daily = guarded("日线", lambda: self.get_daily(code), [])
        markets = guarded("大盘", self.get_markets, [])
        industries = guarded("行业板块", lambda: self.get_boards("行业"), [])
        concepts = guarded("概念板块", lambda: self.get_boards("概念"), [])

        if not trends or not daily:
            demo = self.demo_bundle(code, errors)
            demo.quote = quote
            demo.quote.source = "实时价格 + 演示图表"
            demo.quote.is_demo = True
            trends = trends or demo.trends
            daily = daily or demo.daily
        boards = industries + concepts
        return DataBundle(quote, trends, daily, markets, boards, errors=errors)

    def demo_bundle(self, code: str = "002025", errors: list[str] | None = None) -> DataBundle:
        code = normalize_code(code)
        seed = int(code) + datetime.now().date().toordinal()
        rng = random.Random(seed)
        base = 58.20 if code == "002025" else 10 + (int(code[-3:]) % 700) / 20
        prev = round(base * (1 + rng.uniform(-0.02, 0.02)), 2)
        now = datetime.now().replace(second=0, microsecond=0)
        start = now.replace(hour=9, minute=30)
        points: list[TrendPoint] = []
        price = prev * (1 + rng.uniform(-0.006, 0.006))
        cumulative_amount = 0.0
        cumulative_volume = 0.0
        for i in range(240):
            moment = start + timedelta(minutes=i + (90 if i >= 120 else 0))
            price = max(0.5, price * (1 + rng.gauss(0.00003, 0.0012)))
            volume = rng.randint(100, 2800) * 100
            cumulative_volume += volume
            cumulative_amount += price * volume
            avg = cumulative_amount / cumulative_volume
            points.append(
                TrendPoint(moment, round(price, 2), round(price * 1.001, 2), round(price * 0.999, 2), volume, price * volume, round(avg, 2))
            )
        daily: list[DailyBar] = []
        day_price = base * 0.92
        day = now - timedelta(days=250)
        while len(daily) < 180:
            day += timedelta(days=1)
            if day.weekday() >= 5:
                continue
            open_price = day_price * (1 + rng.gauss(0, 0.008))
            close = open_price * (1 + rng.gauss(0.0005, 0.018))
            high = max(open_price, close) * (1 + rng.uniform(0.002, 0.018))
            low = min(open_price, close) * (1 - rng.uniform(0.002, 0.018))
            volume = rng.randint(50_000, 320_000) * 100
            pct = (close / day_price - 1) * 100
            daily.append(DailyBar(day, open_price, close, high, low, volume, volume * close, pct, rng.uniform(0.5, 5)))
            day_price = close
        latest = points[min(len(points) - 1, max(0, (now.hour - 9) * 60 + now.minute - 30))].price
        name = "航天电器" if code == "002025" else f"演示股票 {code}"
        quote = Quote(
            code=code,
            name=name,
            price=latest,
            change=latest - prev,
            change_pct=(latest / prev - 1) * 100,
            open=points[0].price,
            high=max(p.price for p in points),
            low=min(p.price for p in points),
            prev_close=prev,
            volume=sum(p.volume for p in points),
            amount=sum(p.amount for p in points),
            turnover=2.18,
            amplitude=(max(p.price for p in points) / min(p.price for p in points) - 1) * 100,
            pe=42.6,
            pb=6.8,
            industry="航空航天装备",
            source="离线演示数据",
            is_demo=True,
        )
        index_names = [("000001", "上证指数", 3328.4), ("399001", "深证成指", 10526.1), ("399006", "创业板指", 2158.7), ("000300", "沪深300", 3888.3)]
        markets = [MarketIndex(c, n, p, p * pct / 100, pct) for (c, n, p), pct in zip(index_names, [0.32, 0.61, -0.18, 0.27])]
        board_rows = [
            ("航空装备", 3.12, 8.6e8, "行业"), ("商业航天", 2.78, 6.2e8, "概念"),
            ("军工电子", 2.34, 4.7e8, "行业"), ("机器人", 1.92, 3.9e8, "概念"),
            ("半导体", 1.51, 9.3e8, "行业"), ("卫星导航", 1.43, 2.8e8, "概念"),
            ("银行", 0.38, 3.1e8, "行业"), ("白酒", -0.42, -2.1e8, "行业"),
            ("房地产", -1.05, -5.3e8, "行业"), ("光伏设备", -1.32, -6.8e8, "行业"),
        ]
        boards = [Board(f"BK{i:04d}", n, 1000 + i * 20, pct * 10, pct, abs(pct) * 1.2, flow, kind) for i, (n, pct, flow, kind) in enumerate(board_rows)]
        return DataBundle(quote, points, daily, markets, boards, errors=list(errors or []))

    def _save_cache(self, filename: str, payload: Any) -> None:
        try:
            path = self.cache_dir / filename
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            temp.replace(path)
        except OSError:
            pass
