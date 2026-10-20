"""Public errors never include request bodies, credentials or signed URLs."""
import re


def safe_text(value, secret=""):
    text = str(value)
    if secret:
        text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)bearer\s+\S+|\bsk-[\w-]+", "[redacted]", text)
    text = re.sub(r"https?://[^\s\"'<>]+", "[URL]", text)
    text = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return text[:600]


def field_error(body, secret="", parameters=None):
    """Expose the server's field guidance, never its echoed payload or credentials."""
    message = body.get("message")
    if not isinstance(message, str):
        return ""
    if secret:
        message = message.replace(secret, "[redacted]")
    pending = [parameters]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
        elif isinstance(value, str) and value:
            message = message.replace(value, "[input omitted]")
    message = re.sub(r"(?s)\{.*\}", "[request data omitted]", message)
    message = re.sub(r'''(?i)\b(?:api[_-]?key|access[_-]?token|authorization|password|secret)\b\s*[:=]\s*(?:"[^"]*"|'[^']*'|\S+)''',
                     "[credential omitted]", message)
    return safe_text(message, secret)


class ModellixError(RuntimeError):
    def __init__(self, message, *, status=None, task_id=None, unknown=False, model_slug=None):
        self.message = message
        self.status = status
        self.task_id = task_id
        self.unknown = unknown
        self.model_slug = model_slug
        suffix = f" Task ID: {task_id}." if task_id else ""
        if model_slug:
            suffix += f" Model slug: {model_slug}."
        super().__init__(message + suffix)

    def with_context(self, *, task_id=None, model_slug=None):
        return ModellixError(self.message, status=self.status, task_id=task_id or self.task_id,
                             model_slug=model_slug or self.model_slug, unknown=self.unknown)


def api_error(status, task_id=None, unknown=False, *, detail="", model_slug=None):
    hints = {
        400: "Invalid model parameters. Review the selected schema and field constraints.",
        401: "Authentication failed. Configure a valid key in Settings > Modellix.",
        402: "Insufficient balance. Top up at https://www.modellix.ai/console/billing.",
        404: "Model, schema or task unavailable. Refresh the catalog or check the task ID.",
        413: "Media exceeds the server upload limit (REST default: 200 MiB).",
        429: "Rate or file quota exceeded. Check your quota; uploads are not retried.",
    }
    message = hints.get(status, "Modellix request failed. Check task history before a new attempt.")
    if detail:
        message += " Field details: " + detail
    if unknown:
        message += " Submission outcome unknown. Check task history; this attempt will not replay."
    return ModellixError(f"HTTP {status}: {message}", status=status,
                         task_id=task_id, unknown=unknown, model_slug=model_slug)
