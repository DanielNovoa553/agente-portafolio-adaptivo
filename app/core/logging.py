import json
import logging
import sys
from datetime import datetime, timezone

_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Formateador que serializa cada registro de log como una línea JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Convierte un registro de logging en una línea JSON.

        Args:
            record: Registro de logging que se va a serializar.

        Returns:
            Cadena JSON con la marca de tiempo, nivel, logger, mensaje y campos extra.
        """
        data = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        data.update({k: v for k, v in vars(record).items() if k not in _STANDARD})
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configura el logger raíz para escribir logs JSON en stdout.

    Args:
        level: Nivel mínimo de logging; se normaliza a mayúsculas.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())
