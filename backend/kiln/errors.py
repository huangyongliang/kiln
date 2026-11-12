"""Application errors mapped to HTTP responses by the API shell."""


class AppError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class Unauthorized(AppError):
    def __init__(self, detail: str = "需要访问令牌") -> None:
        super().__init__(401, detail)


class Forbidden(AppError):
    def __init__(self, detail: str = "没有权限") -> None:
        super().__init__(403, detail)


class BadRequest(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class NotFound(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(404, detail)


class Conflict(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(409, detail)


class Gone(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(410, detail)


class PayloadTooLarge(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(413, detail)


class StorageError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(500, detail)


class ModelDisabled(AppError):
    def __init__(self) -> None:
        super().__init__(
            503,
            "模型未启用。将 LLM_PROVIDER 设为 openai 并配置 LLM_BASE_URL，或在本地使用 mock。",
        )


class ModelRequestError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(400, detail)


class ModelUpstreamError(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(502, detail)


class ModelTimeout(AppError):
    def __init__(self) -> None:
        super().__init__(504, "模型端点超时。")


class UpstreamFailure(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(502, detail)


# Same detail get_jenkins and get_git_source raise when that endpoint is unset.
JENKINS_UNCONFIGURED_DETAIL = (
    "执行器未配置。请设置 JENKINS_URL、JENKINS_USER 和 JENKINS_API_TOKEN。"
)
GIT_SOURCE_UNCONFIGURED_DETAIL = (
    "代码源未配置。请将 GIT_PROVIDER 设为 gitea（实验室）或 gitlab（生产代码源），"
    "并设置 GIT_BASE_URL 与 GIT_TOKEN。"
)


class NotConfigured(AppError):
    def __init__(self, detail: str) -> None:
        super().__init__(503, detail)
