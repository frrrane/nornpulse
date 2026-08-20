"""
agent package

VerdandiADK is exposed lazily (PEP 562) rather than imported eagerly here.
Eagerly importing it at package-load time means ANY import touching the
`agent` package — even `import agent.some_unrelated_module` — triggers the
full verdandi_orchestrator -> urdr_analytics -> clickhouse_mcp_client
chain before agent/__init__.py has finished executing, which is exactly
what caused the earlier circular-import error. Deferring the import until
VerdandiADK is actually accessed avoids that class of bug entirely.
"""

__all__ = ['VerdandiADK']


def __getattr__(name):
    if name == 'VerdandiADK':
        from .verdandi_orchestrator import VerdandiADK
        return VerdandiADK
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")