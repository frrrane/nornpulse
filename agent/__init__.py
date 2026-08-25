"""
agent package

VerdandiOrchestrator is exposed lazily (PEP 562) rather than imported eagerly here.
Eagerly importing it at package-load time means ANY import touching the
`agent` package — even `import agent.some_unrelated_module` — triggers the
full verdandi_orchestrator -> urdr_analytics -> clickhouse_mcp_client
chain before agent/__init__.py has finished executing, which is exactly
what caused the earlier circular-import error. Deferring the import until
VerdandiOrchestrator is actually accessed avoids that class of bug entirely.
"""

__all__ = ['VerdandiOrchestrator']


def __getattr__(name):
    if name == 'VerdandiOrchestrator':
        from .verdandi_orchestrator import VerdandiOrchestrator
        return VerdandiOrchestrator
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")