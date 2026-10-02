"""
Identity exceptions.
"""
from __future__ import annotations


class IdentityError(Exception):
    pass


class AuthenticationError(IdentityError):
    pass


class MissingCredentialsError(AuthenticationError):
    pass


class InvalidSessionError(AuthenticationError):
    pass


class ExpiredSessionError(AuthenticationError):
    pass


__all__ = [
    "IdentityError",
    "AuthenticationError",
    "MissingCredentialsError",
    "InvalidSessionError",
    "ExpiredSessionError",
]
