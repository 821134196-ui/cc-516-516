"""业务错误类型。"""


class FarmError(Exception):
    """可展示给前端的业务错误。"""


class TicketNotFound(FarmError):
    pass


class TicketStateError(FarmError):
    pass


class ClosureError(FarmError):
    pass


class CapacityError(FarmError):
    pass
