"""One error type for everything mdk tells the developer."""


class MdkError(Exception):
    """A problem with a stable code, what it means and what to do next.

    `code` is stable and searchable (E-ARCH, E-SIZE, ...); `message` says what
    is wrong in one sentence; `remedy` says what to do; `detail` carries the
    evidence (a path, a tool's output)."""

    def __init__(self, code: str, message: str, remedy: str = "", detail: str = ""):
        super().__init__(message)
        self.code, self.message, self.remedy, self.detail = code, message, remedy, detail

    def render(self) -> str:
        lines = [f"{self.code}  {self.message}"]
        if self.detail:
            lines += [f"        {line}" for line in self.detail.splitlines()]
        if self.remedy:
            lines += [f"        {line}" for line in self.remedy.splitlines()]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "remedy": self.remedy, "detail": self.detail}
