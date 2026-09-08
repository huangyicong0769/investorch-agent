class MarketDataError(RuntimeError):
    def __init__(self, code: str, message: str = "", *, transient: bool = False):
        self.code = code
        self.transient = transient
        super().__init__(f"{code}: {message}" if message else code)
