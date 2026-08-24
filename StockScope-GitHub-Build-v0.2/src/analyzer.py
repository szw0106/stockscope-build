from __future__ import annotations

from dataclasses import dataclass, field
from statistics import mean

from .models import Board, DailyBar, MarketIndex, Quote, TrendPoint


def sma(values: list[float], period: int) -> float | None:
    return mean(values[-period:]) if len(values) >= period else None


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(alpha * value + (1 - alpha) * result[-1])
    return result


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]
    window = changes[-period:]
    gains = sum(max(change, 0) for change in window) / period
    losses = sum(max(-change, 0) for change in window) / period
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def atr(bars: list[DailyBar], period: int = 14) -> float | None:
    if len(bars) <= period:
        return None
    ranges: list[float] = []
    for previous, current in zip(bars[-period - 1 : -1], bars[-period:]):
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous.close),
                abs(current.low - previous.close),
            )
        )
    return mean(ranges)


@dataclass(slots=True)
class Check:
    label: str
    passed: bool
    detail: str
    weight: int


@dataclass(slots=True)
class StockAnalysis:
    score: int
    verdict: str
    tone: str
    checks: list[Check]
    sma5: float | None
    sma10: float | None
    sma20: float | None
    rsi14: float | None
    macd: float | None
    vwap: float | None
    support1: float
    support2: float
    resistance1: float
    resistance2: float
    watch_price: float
    stop_reference: float
    initial_position: int
    risk_reward: float | None
    notes: list[str] = field(default_factory=list)


def analyze_stock(quote: Quote, trends: list[TrendPoint], daily: list[DailyBar]) -> StockAnalysis:
    closes = [bar.close for bar in daily]
    highs = [bar.high for bar in daily]
    lows = [bar.low for bar in daily]
    prices = [point.price for point in trends if point.price > 0]
    volumes = [point.volume for point in trends]
    daily_sma5 = sma(closes, 5)
    daily_sma10 = sma(closes, 10)
    daily_sma20 = sma(closes, 20)
    daily_rsi = rsi(closes)
    fast = ema_series(closes, 12)
    slow = ema_series(closes, 26)
    macd = fast[-1] - slow[-1] if fast and slow else None
    vwap = None
    if trends:
        total_volume = sum(max(point.volume, 0) for point in trends)
        if total_volume:
            vwap = sum(point.price * max(point.volume, 0) for point in trends) / total_volume
        elif trends[-1].avg_price > 0:
            vwap = trends[-1].avg_price

    checks: list[Check] = []
    recent = prices[-12:]
    earlier = prices[-36:-12]
    no_new_low = bool(recent and earlier and min(recent) >= min(earlier) * 0.999)
    checks.append(Check("低点不再下移", no_new_low, "近12分钟未跌破前段低点" if no_new_low else "短线仍可能刷新低点", 18))

    reclaim_vwap = bool(vwap and quote.price >= vwap)
    checks.append(Check("收回分时均价", reclaim_vwap, f"现价 {'≥' if reclaim_vwap else '<'} 均价 {vwap:.2f}" if vwap else "均价数据不足", 18))

    short_up = len(prices) >= 10 and mean(prices[-5:]) > mean(prices[-10:-5])
    checks.append(Check("短均线拐头向上", short_up, "最近5分钟均价上移" if short_up else "短线均价尚未上移", 17))

    range_size = max(quote.high - quote.low, 0)
    recovery = (quote.price - quote.low) / range_size if range_size else 0
    off_low = recovery >= 0.35
    checks.append(Check("脱离日内低点", off_low, f"位于日内振幅的 {recovery * 100:.0f}% 位置", 15))

    above_sma5 = bool(daily_sma5 and quote.price >= daily_sma5)
    checks.append(Check("站上5日均线", above_sma5, f"5日均线 {daily_sma5:.2f}" if daily_sma5 else "日线数据不足", 14))

    volume_turn = False
    if len(volumes) >= 20:
        earlier_volume = mean(volumes[-20:-10]) or 1
        volume_turn = mean(volumes[-10:]) >= earlier_volume * 1.1 and quote.price >= quote.open
    checks.append(Check("反弹量能配合", volume_turn, "近10分钟量能放大且价格不弱于开盘" if volume_turn else "尚未看到量价共振", 12))

    rsi_ok = daily_rsi is not None and 32 <= daily_rsi <= 68
    checks.append(Check("RSI脱离极弱区", rsi_ok, f"RSI(14) {daily_rsi:.1f}" if daily_rsi is not None else "RSI数据不足", 6))

    score = sum(check.weight for check in checks if check.passed)
    if quote.price < quote.prev_close and not reclaim_vwap:
        score = max(0, score - 5)
    score = min(100, score)
    if score >= 75:
        verdict, tone, initial_position = "止跌信号较强", "positive", 20
    elif score >= 58:
        verdict, tone, initial_position = "初步企稳，继续观察", "watch", 10
    elif score >= 40:
        verdict, tone, initial_position = "信号不足，不宜抢反弹", "neutral", 0
    else:
        verdict, tone, initial_position = "弱势风险较高", "negative", 0

    intraday_low = min(prices) if prices else quote.low
    support1 = min([value for value in [intraday_low, quote.low] if value > 0], default=quote.price)
    support2 = min(lows[-20:]) if len(lows) >= 20 else support1
    resistance1 = max(highs[-5:]) if len(highs) >= 5 else quote.high
    resistance2 = max(highs[-20:]) if len(highs) >= 20 else resistance1
    daily_atr = atr(daily) or max(quote.price * 0.02, 0.01)
    stop_reference = max(0.01, support1 - daily_atr * 0.35)
    watch_candidates = [quote.price]
    if vwap:
        watch_candidates.append(vwap)
    if daily_sma5:
        watch_candidates.append(daily_sma5)
    watch_price = max(watch_candidates)
    risk = max(watch_price - stop_reference, 0)
    reward = max(resistance1 - watch_price, 0)
    risk_reward = reward / risk if risk > 0 else None
    notes: list[str] = []
    if quote.is_demo:
        notes.append("当前含演示数据，不能据此交易。")
    if daily_rsi is not None and daily_rsi < 30:
        notes.append("RSI处于超卖区，但超卖不等于立即反转。")
    if risk_reward is not None and risk_reward < 1.5:
        notes.append("到第一压力位的盈亏比偏低，追涨性价比不足。")
    return StockAnalysis(
        score, verdict, tone, checks, daily_sma5, daily_sma10, daily_sma20,
        daily_rsi, macd, vwap, support1, support2, resistance1, resistance2,
        watch_price, stop_reference, initial_position, risk_reward, notes,
    )


@dataclass(slots=True)
class MarketAnalysis:
    score: int
    regime: str
    summary: str
    index_average: float
    board_breadth: float
    positive_boards: int
    total_boards: int


def analyze_market(markets: list[MarketIndex], boards: list[Board]) -> MarketAnalysis:
    index_average = mean([item.change_pct for item in markets]) if markets else 0.0
    valid_boards = [board for board in boards if -20 < board.change_pct < 20]
    positive = sum(board.change_pct > 0 for board in valid_boards)
    breadth = positive / len(valid_boards) if valid_boards else 0.5
    score = round(max(0, min(100, 50 + index_average * 12 + (breadth - 0.5) * 55)))
    if score >= 68:
        regime = "偏强"
        summary = "指数和板块扩散较好，可关注强势方向中的回踩确认。"
    elif score >= 45:
        regime = "震荡"
        summary = "市场分化明显，单股信号需要更严格，避免追高。"
    else:
        regime = "偏弱"
        summary = "市场承压，抢反弹仓位宜更轻，优先等待确认。"
    return MarketAnalysis(score, regime, summary, index_average, breadth, positive, len(valid_boards))
