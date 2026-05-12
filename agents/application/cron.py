from agents.application.trade import Trader

import time

from scheduler import Scheduler as _LibScheduler
from scheduler.trigger import Monday


class TradingScheduler:
    def __init__(self) -> None:
        self.trader = Trader()
        self.schedule = _LibScheduler()

    def start(self) -> None:
        while True:
            self.schedule.exec_jobs()
            time.sleep(1)


class TradingAgent(TradingScheduler):
    def __init__(self) -> None:
        super().__init__()
        self.trader = Trader()
        self.schedule.weekly(Monday(), self.trader.one_best_trade)
