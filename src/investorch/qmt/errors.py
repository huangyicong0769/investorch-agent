"""Separate unknown transport outcomes from explicit node rejections."""


class QMTError(Exception):
    pass


class QMTTransportError(QMTError):
    pass


class QMTProtocolError(QMTTransportError):
    """A response cannot establish the outcome of the requested operation."""


class QMTRejectedError(QMTError):
    def __init__(self, code: str, message: str, retryable: bool, status_code: int) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.status_code = status_code
