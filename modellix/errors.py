"""Public errors never include request bodies, credentials or signed URLs."""
import re


def safe_text(value, secret=""):
    text = str(value)
    if secret:
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)bearer\s+\S+|\bsk-[\w-]+", "[redacted]", text)
    text = re.sub(r"https?://[^\s\"'<>]+", "[URL]", text)
    return text[:600]


class ModellixError(RuntimeError):
    def __init__(self, message, *, status=None, task_id=None, unknown=False):
        self.status = status
        self.task_id = task_id
        self.unknown = unknown
        suffix = f" Task ID: {task_id}." if task_id else ""
        super().__init__(message + suffix)


def api_error(status, task_id=None, unknown=False):
    hints = {
        400: "Invalid model parameters. Review the selected schema and field constraints.",
        401: "Authentication failed. Configure a valid key in Settings > Modellix.",
        402: "Insufficient balance. Top up at https://www.modellix.ai/console/billing.",
        404: "Model, schema or task unavailable. Refresh the catalog or check the task ID.",
        413: "Media exceeds the server upload limit (REST default: 200 MiB).",
        429: "Rate or file quota exceeded. Check your quota; uploads are not retried.",
    }
    message = hints.get(status, "Modellix request failed. Check task history before a new attempt.")
    if unknown:
        message += " Submission outcome unknown. Check task history; this attempt will not replay."
    return ModellixError(f"HTTP {status}: {message}", status=status,
                         task_id=task_id, unknown=unknown)
