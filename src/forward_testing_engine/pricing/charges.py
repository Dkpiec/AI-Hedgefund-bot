"""Multi-Asset Statutory Charges and Fee Calculator.
Calculates exact real-world statutory taxes and brokerage:
- Options: ₹20 brokerage, 0.1% STT on sell premium, exchange fees, GST, stamp duty.
- Equities: ₹20 brokerage, 0.1% STT both ways, DP charges, GST, stamp duty.
- Currency Futures: ₹20 brokerage, exchange fees, GST, stamp duty (No STT).
- Crypto: Binance 0.02% maker / 0.05% taker fees.
"""

from typing import Dict, Any


class OptionsCharges:
    @staticmethod
    def calculate(entry_price: float, exit_price: float, qty: int) -> float:
        """Total roundtrip charges for options contracts."""
        brokerage = 40.0  # ₹20 buy + ₹20 sell
        stt = (exit_price * qty) * 0.0010  # 0.1% on sell turnover
        turnover = (entry_price + exit_price) * qty
        exchange_turnover_fee = turnover * 0.0005
        gst = (brokerage + exchange_turnover_fee) * 0.18
        stamp_duty = (entry_price * qty) * 0.00003
        sebi = turnover * 0.000001
        total = brokerage + stt + exchange_turnover_fee + gst + stamp_duty + sebi
        return round(total, 2)


class EquityCharges:
    @staticmethod
    def calculate(entry_price: float, exit_price: float, qty: int) -> float:
        """Total roundtrip charges for delivery / intraday equity."""
        brokerage = 40.0
        turnover = (entry_price + exit_price) * qty
        stt = turnover * 0.0010
        exchange_fee = turnover * 0.0000345
        gst = (brokerage + exchange_fee) * 0.18
        stamp_duty = (entry_price * qty) * 0.00015
        dp_charge = 15.93  # Per sell transaction
        total = brokerage + stt + exchange_fee + gst + stamp_duty + dp_charge
        return round(total, 2)


class CurrencyCharges:
    @staticmethod
    def calculate(entry_price: float, exit_price: float, qty: int) -> float:
        """Total roundtrip charges for currency futures."""
        brokerage = 40.0
        turnover = (entry_price + exit_price) * qty
        exchange_fee = turnover * 0.000009
        gst = (brokerage + exchange_fee) * 0.18
        stamp_duty = (entry_price * qty) * 0.000001
        total = brokerage + exchange_fee + gst + stamp_duty
        return round(total, 2)


class CryptoCharges:
    @staticmethod
    def calculate(entry_price: float, exit_price: float, qty: float, is_maker: bool = True) -> float:
        """Binance post-only maker fee (0.02%) vs taker (0.05%)."""
        rate = 0.0002 if is_maker else 0.0005
        turnover = (entry_price + exit_price) * qty
        return round(turnover * rate, 4)
