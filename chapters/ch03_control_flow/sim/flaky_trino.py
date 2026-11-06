"""Fake Trino client that times out a fixed number of times before answering."""


class FlakyTrino:
    def __init__(self, failures_before_success: int):
        self.failures_left = failures_before_success

    def query(self, sql: str) -> str:
        if self.failures_left > 0:
            self.failures_left -= 1
            raise TimeoutError("Trino coordinator timed out")
        return "rows=1 (sales.orders: 1,240 snapshots)"
