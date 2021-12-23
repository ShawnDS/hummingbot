from decimal import Decimal
from datetime import datetime
import time
from hummingbot.script.script_base import ScriptBase
from os.path import realpath, join
from hummingbot.core.event.events import (
    BuyOrderCompletedEvent,
    SellOrderCompletedEvent
)

s_decimal_1 = Decimal("1")
LOGS_PATH = realpath(join(__file__, "../../logs/"))
SCRIPT_LOG_FILE = f"{LOGS_PATH}/logs_script.log"

def log_to_file(file_name, message):
    with open(file_name, "a+") as f:
        f.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " - " + message + "\n")


class SpreadsAdjustedOnVolatility(ScriptBase):
    """
    Demonstrates how to adjust bid and ask spreads based on price volatility.
    The volatility, in this example, is simply a price change compared to the previous cycle regardless of its
    direction, e.g. if price changes -3% (or 3%), the volatility is 3%.
    To update our pure market making spreads, we're gonna smooth out the volatility by averaging it over a short period
    (short_period), and we need a benchmark to compare its value against. In this example the benchmark is a median
    long period price volatility (you can also use a fixed number, e.g. 3% - if you expect this to be the norm for your
    market).
    For example, if our bid_spread and ask_spread are at 0.8%, and the median long term volatility is 1.5%.
    Recently the volatility jumps to 2.6% (on short term average), we're gonna adjust both our bid and ask spreads to
    1.9%  (the original spread - 0.8% plus the volatility delta - 1.1%). Then after a short while the volatility drops
    back to 1.5%, our spreads are now adjusted back to 0.8%.
    """

    # Let's set interval and sample sizes as below.
    # These numbers are for testing purposes only (in reality, they should be larger numbers)
    # interval is a interim which to pick historical mid price samples from, if you set it to 5, the first sample is
    # the last (current) mid price, the second sample is a past mid price 5 seconds before the last, and so on.
    interval = 60
    # short_period is how many interval to pick the samples for the average short term volatility calculation,
    # for short_period of 3, this is 3 samples (5 seconds interval), of the last 15 seconds
    short_period = 5
    # long_period is how many interval to pick the samples for the median long term volatility calculation,
    # for long_period of 10, this is 10 samples (5 seconds interval), of the last 50 seconds
    long_period = 30
    last_stats_logged = 0

    def __init__(self):
        super().__init__()
        self.original_bid_spread = None
        self.original_ask_spread = None
        self.avg_short_volatility = None
        self.median_long_volatility = None
        self.num_bid_completed = 0  # 单位时间内成交的买单数
        self.num_ask_completed = 0
        self.all_bid_completed = 0  # 所有成交的买单数
        self.all_ask_completed = 0

        self.last_completed_price = None
        self.last_time = 0  # 上次的时间，用于计算订单成交频率
        
        self.base_asset = None
        self.quote_asset = None
        self.available_base_balance = Decimal("0.0000")
        self.available_quote_balance = Decimal("0.0000")


    def volatility_msg(self, include_mid_price=False):
        if self.avg_short_volatility is None or self.median_long_volatility is None:
            return "short_volatility: N/A  long_volatility: N/A  " \
                   f"bid_spread={self.pmm_parameters.bid_spread:.2%}  " \
                   f"ask_spread={self.pmm_parameters.ask_spread:.2%}  " \
                   f" order_refresh_tolerance_pct={self.pmm_parameters.order_refresh_tolerance_pct:.2%}  "
        mid_price_msg = f"  mid_price: {self.mid_price:<15}" if include_mid_price else ""
        return f"short_volatility: {self.avg_short_volatility:.2%}  " \
               f"long_volatility: {self.median_long_volatility:.2%}{mid_price_msg}" \
               f"bid_spread={self.pmm_parameters.bid_spread:.2%}  " \
               f"ask_spread={self.pmm_parameters.ask_spread:.2%}  " \
               f" order_refresh_tolerance_pct={self.pmm_parameters.order_refresh_tolerance_pct:.2%}  "

    def on_tick(self):
        # Separate and store the assets of the market the bot is working on
        if self.base_asset is None or self.quote_asset is None:
            self.base_asset, self.quote_asset = self.pmm_market_info.trading_pair.split("-")

        # Check what is the current balance of each asset
        self.available_base_balance = self.all_available_balances[f"{self.pmm_market_info.exchange}"].get(self.base_asset, self.available_base_balance)
        self.available_quote_balance = self.all_available_balances[f"{self.pmm_market_info.exchange}"].get(self.quote_asset, self.available_quote_balance)

        # First, let's keep the original spreads.
        if self.original_bid_spread is None:
            self.original_bid_spread = self.pmm_parameters.bid_spread
            self.original_ask_spread = self.pmm_parameters.ask_spread

        # Average volatility (price change) over a short period of time, this is to detect recent sudden changes.
        self.avg_short_volatility = self.avg_price_volatility(self.interval, self.short_period)
        # Median volatility over a long period of time, this is to find the market norm volatility.
        # We use median (instead of average) to find the middle volatility value - this is to avoid recent
        # spike affecting the average value.
        self.median_long_volatility = self.median_price_volatility(self.interval, self.long_period)

        # # If the bot just got started, we'll not have these numbers yet as there is not enough mid_price sample size.
        # # We'll start to have these numbers after interval * long_term_period (150 seconds in this example).
        # if self.avg_short_volatility is None or self.median_long_volatility is None:
        #     return

        # # Let's log some stats once every 5 minutes
        # if time.time() - self.last_stats_logged > 60 * 5:
        #     log_to_file(SCRIPT_LOG_FILE, self.volatility_msg(True))
        #     self.last_stats_logged = time.time()
        
        # # This volatility delta will be used to adjust spreads.
        # delta = self.avg_short_volatility - self.median_long_volatility
        # # Let's round the delta into 0.25% increment to ignore noise and to avoid adjusting the spreads too often.
        # spread_adjustment = self.round_by_step(delta, Decimal("0.0025"))
        # # Show the user on what's going, you can remove this statement to stop the notification.
        # # self.notify(f"avg_short_volatility: {avg_short_volatility} median_long_volatility: {median_long_volatility} "
        # #             f"spread_adjustment: {spread_adjustment}")
        # new_bid_spread = self.original_bid_spread + spread_adjustment
        # # Let's not set the spreads below the originals, this is to avoid having spreads to be too close
        # # to the mid price.
        # new_bid_spread = max(self.original_bid_spread, new_bid_spread)
        # old_bid_spread = self.pmm_parameters.bid_spread
        # if new_bid_spread != self.pmm_parameters.bid_spread:
        #     self.pmm_parameters.bid_spread = new_bid_spread

        # new_ask_spread = self.original_ask_spread + spread_adjustment
        # new_ask_spread = max(self.original_ask_spread, new_ask_spread)
        # if new_ask_spread != self.pmm_parameters.ask_spread:
        #     self.pmm_parameters.ask_spread = new_ask_spread
        # if old_bid_spread != new_bid_spread:
        #     log_to_file(SCRIPT_LOG_FILE, self.volatility_msg(True))
        #     log_to_file(SCRIPT_LOG_FILE, f"spreads adjustment: Old Value: {old_bid_spread:.2%} "
        #                                  f"New Value: {new_bid_spread:.2%}")

        # 根据一段时间的订单成交情况来调整order_refresh_tolerance_pct和价差
        if (time.time() - self.last_time > 60 * 60 * 0.5):
            if (0 == self.num_ask_completed + self.num_bid_completed and self.available_quote_balance > Decimal("0.1000") and self.available_base_balance > Decimal("0.1000")): # 有余额但一个时间周期内无成交 
                self.pmm_parameters.order_refresh_tolerance_pct += Decimal("0.001")
                self.pmm_parameters.bid_spread -= Decimal("0.001")
                self.pmm_parameters.ask_spread -= Decimal("0.001")
                self.pmm_parameters.bid_spread = max(Decimal("0.005"), self.pmm_parameters.bid_spread)
                self.pmm_parameters.ask_spread = max(Decimal("0.005"), self.pmm_parameters.ask_spread)
                self.pmm_parameters.order_refresh_tolerance_pct = max(Decimal("0.01"), self.pmm_parameters.ask_spread, self.pmm_parameters.bid_spread, self.pmm_parameters.order_refresh_tolerance_pct)
                self.pmm_parameters.order_refresh_tolerance_pct = min(Decimal("0.02"), self.pmm_parameters.order_refresh_tolerance_pct)
                self.notify(f"超过30分钟无订单成交，减小价差和增大订单刷新容差, bid_spread={self.pmm_parameters.bid_spread:.2%}, ask_spread={self.pmm_parameters.ask_spread:.2%}, order_refresh_tolerance_pct={self.pmm_parameters.order_refresh_tolerance_pct:.2%}")
            if (self.num_ask_completed + self.num_bid_completed >= 3):
                self.pmm_parameters.order_refresh_tolerance_pct -= Decimal("0.001")
                self.pmm_parameters.bid_spread += Decimal("0.001")
                self.pmm_parameters.ask_spread += Decimal("0.001")
                self.pmm_parameters.bid_spread = min(Decimal("0.015"), self.pmm_parameters.bid_spread)
                self.pmm_parameters.ask_spread = min(Decimal("0.015"), self.pmm_parameters.ask_spread)
                self.pmm_parameters.order_refresh_tolerance_pct = min(self.pmm_parameters.ask_spread, self.pmm_parameters.bid_spread, self.pmm_parameters.order_refresh_tolerance_pct)
                self.pmm_parameters.order_refresh_tolerance_pct = max(self.pmm_parameters.order_refresh_tolerance_pct, Decimal("0.002"))
                self.notify(f"30分钟内有多个订单成交，增大价差和降低订单刷新容差, bid_spread={self.pmm_parameters.bid_spread:.2%}, ask_spread={self.pmm_parameters.ask_spread:.2%}, order_refresh_tolerance_pct={self.pmm_parameters.order_refresh_tolerance_pct:.2%}")
            self.notify(f"过去30分钟订单成交数：{self.num_ask_completed + self.num_bid_completed}")
            self.num_ask_completed = 0
            self.num_bid_completed = 0
            self.last_time = time.time()


    def on_status(self) -> str:
        return self.volatility_msg()

    def on_buy_order_completed(self, event: BuyOrderCompletedEvent):
        self.num_bid_completed += 1
        self.all_bid_completed += 1
        if (self.all_bid_completed >= 3) and (self.num_bid_completed - self.num_ask_completed >= 2):  #  已成交的买单比卖单多2个以上时（市价跌了），停止下卖单并扩大买单价差
            self.last_completed_price = self.mid_price  # 记录成交价，应记录最近几次成交价，新的订单应该加上第一次成交价和最后一次成交价的价差。短时间多个订单成交时也应增加卖单的价差，不然容易出现低买低卖.短时间多个订单成交时应该增加下新订单delay时间
            if self.avg_short_volatility is None or self.median_long_volatility is None:
                self.pmm_parameters.bid_spread += Decimal("0.01")
            else:
                self.pmm_parameters.bid_spread *= 2
                self.pmm_parameters.bid_spread = min(Decimal("0.2"), self.pmm_parameters.bid_spread)
                self.pmm_parameters.ask_spread *= 2
                self.pmm_parameters.ask_spread += self.avg_short_volatility
            self.notify(f"down down down! 更新bid_spread={self.pmm_parameters.bid_spread}")
            self.num_ask_completed = 0
            self.num_bid_completed = 0
        #if self.all_bid_completed - self.all_ask_completed >= 2:  # 一般都会开启挂单模式，这里防止买单成交后过多的卖单堆积
        #    self.pmm_parameters.sell_levels = 1
        #    self.notify(f"卖单挂单太多，减少新卖单")
        return

    def on_sell_order_completed(self, event: SellOrderCompletedEvent):
        self.num_ask_completed += 1
        self.all_ask_completed += 1
        if (self.all_ask_completed >= 3) and (self.num_ask_completed - self.num_bid_completed >= 2):  #  已成交的卖单比买单多2个以上时（市价涨了），停止下买单并扩大卖单价差
            self.last_completed_price = self.mid_price  # 记录成交价
            if self.avg_short_volatility is None or self.median_long_volatility is None:
                self.pmm_parameters.ask_spread += Decimal("0.01")
            else:
                self.pmm_parameters.ask_spread *= 2
                self.pmm_parameters.bid_spread *= 2
                self.pmm_parameters.bid_spread += self.avg_short_volatility
            self.notify(f"up up up! 更新ask_spread={self.pmm_parameters.ask_spread}")
            self.num_bid_completed = 0
            self.num_ask_completed = 0
        if (self.pmm_parameters.hanging_orders_enabled == True) and (self.all_ask_completed - self.all_bid_completed >= 2): # 一般都会开启挂单模式，这里防止卖单成交后过多的买单挂住
            self.pmm_parameters.bid_levels = 0
            self.notify(f"买单挂单太多，减少新买单")
        else:
            self.pmm_parameters.bid_levels = 1
            self.notify(f"恢复正常买单数")
        return
