"""Exception types shared across process management."""


class ProfileRunningError(Exception):
    def __init__(self, message: str, stopped: bool = False) -> None:
        super().__init__(message)
        self.stopped = stopped


class BrowserLaunchError(Exception):
    def __init__(self, message: str, category: str = "browser_launch_failed") -> None:
        super().__init__(message)
        self.category = category
