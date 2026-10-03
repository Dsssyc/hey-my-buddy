"""Structured errors shared by the service, the client and the CLI."""


class BoardError(RuntimeError):
    """One structured, actionable failure.

    ``code`` is the stable machine value returned in ``{"error":{"code":...}}``;
    ``details`` carries machine-readable context such as the current ``revision``,
    a resumable ``cursor`` or the conflicting field.
    """

    def __init__(self, code: str, message: str, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def payload(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.details:
            error["details"] = {key: value for key, value in self.details.items() if value is not None}
        return error
