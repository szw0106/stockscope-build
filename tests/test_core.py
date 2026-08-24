from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

from src.analyzer import analyze_market, analyze_stock, atr, ema_series, rsi, sma
from src.data_provider import EastmoneyProvider, normalize_code, to_secid
from src.models import Board, DailyBar, MarketIndex, Quote, TrendPoint


class CodeTests(unittest.TestCase):
    def test_normalize_code(self) -> None:
        self.assertEqual(normalize_code("SZ002025"), "002025")
        self.assertEqual(normalize_code("600519"), "600519")
        with self.assertRaises(ValueError):
            normalize_code("2025")

    def test_secid(self) -> None:
        self.assertEqual(to_secid("002025"), "0.002025")
        self.assertEqual(to_secid("600519"), "1.600519")
        self.assertEqual(to_secid("300750"), "0.300750")


class IndicatorTests(unittest.TestCase):
    def test_moving_averages_and_rsi(self) -> None:
        values = list(range(1, 31))
        self.assertEqual(sma(values, 5), 28)
        self.assertEqual(len(ema_series(values, 12)), 30)
        self.assertEqual(rsi(values, 14), 100)

    def test_atr(self) -> None:
        start = datetime(2026, 1, 1)
        bars = [DailyBar(start + timedelta(days=i), 10, 10, 11, 9, 100, 1000) for i in range(20)]
        self.assertAlmostEqual(atr(bars) or 0, 2.0)

    def test_stock_analysis_strong_recovery(self) -> None:
        start = datetime(2026, 1, 1)
        daily = []
        close = 45.0
        for i in range(40):
            close += 0.25
            daily.append(DailyBar(start + timedelta(days=i), close - 0.1, close, close + 0.3, close - 0.4, 1000 + i * 10, 100000))
        trends = []
        for i in range(60):
            price = 54.0 + i * 0.025
            trends.append(TrendPoint(start + timedelta(minutes=i), price, price + 0.05, price - 0.05, 1000 + i * 40, price * (1000 + i * 40), price - 0.03))
        quote = Quote("002025", "航天电器", trends[-1].price, 1.0, 1.8, 54.1, trends[-1].price, 54.0, 54.0)
        result = analyze_stock(quote, trends, daily)
        self.assertGreaterEqual(result.score, 58)
        self.assertGreater(result.watch_price, 0)
        self.assertLess(result.stop_reference, quote.price)


class ProviderTests(unittest.TestCase):
    def test_demo_bundle_is_complete(self) -> None:
        provider = EastmoneyProvider(Path("/tmp/stockscope-test-cache"))
        bundle = provider.demo_bundle("002025")
        self.assertTrue(bundle.quote.is_demo)
        self.assertEqual(bundle.quote.code, "002025")
        self.assertEqual(len(bundle.trends), 240)
        self.assertEqual(len(bundle.daily), 180)
        self.assertEqual(len(bundle.markets), 4)
        self.assertGreaterEqual(len(bundle.boards), 8)
        market = analyze_market(bundle.markets, bundle.boards)
        self.assertGreaterEqual(market.score, 0)
        self.assertLessEqual(market.score, 100)


if __name__ == "__main__":
    unittest.main()
