# Created by model-proxy on 2026/05/18
# Copyright © 2026

"""调度器异常定义，独立模块以避免循环引用。"""

from __future__ import annotations


class DispatchError(Exception):
    pass


class RateLimitExceeded(DispatchError):
    pass


class ModelNotFound(DispatchError):
    pass


class AllModelsUnavailable(DispatchError):
    pass


class ProviderCallError(DispatchError):
    pass


class PayloadTooLarge(DispatchError):
    pass
