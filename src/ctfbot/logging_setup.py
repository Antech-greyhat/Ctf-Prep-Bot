import logging


class RedactingFormatter(logging.Formatter):
    # PTB's InvalidToken message contains the raw token, so redact it everywhere.
    def __init__(self, fmt: str, secret: str | None) -> None:
        super().__init__(fmt)
        self._secret = secret

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if self._secret:
            text = text.replace(self._secret, "[REDACTED]")
        return text


def configure_logging(secret: str | None = None) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        RedactingFormatter("%(asctime)s %(levelname)s %(name)s: %(message)s", secret)
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # httpx logs request URLs at INFO, and Telegram URLs contain the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
