# Dependency Injection Policy

---
status: current
---

This document defines when and how to wire dependencies in GPT-Trader.

## Overview

The retained bot runtime uses `ApplicationContainer` as its composition root.
The [recorded experiment](decisions/recorded-experiment-product.md) composes
its pure transition and explicit journal directly; it has no shared service
lifecycle. Pass dependencies explicitly. Neither route uses module-level
singletons to hide state.

## Decision Matrix

| Pattern | When to Use | Example |
|---------|-------------|---------|
| **ApplicationContainer** (preferred) | Stateful services, cross-cutting concerns, anything requiring lifecycle management | `RiskManager`, `EventStore`, `NotificationService` |
| **Explicit factories** (acceptable) | Pure functions, stateless transformations, configuration builders | `create_brokerage()`, `build_profile_config()` |
| **Module singletons** (discouraged) | OS-level hooks, logging infrastructure, truly global state | `logging.getLogger()` |

## Container Usage

### Adding a New Service

1. Add private field to `ApplicationContainer.__init__`:
   ```python
   self._my_service: MyService | None = None
   ```

2. Add lazy property:
   ```python
   @property
   def my_service(self) -> MyService:
       if self._my_service is None:
           self._my_service = MyService(
               dependency=self.other_service,
           )
       return self._my_service
   ```

3. (Optional) Add reset method if service needs recreation:
   ```python
   def reset_my_service(self) -> None:
       self._my_service = None
   ```

### Accessing the Container

**From application code (preferred):**
```python
def my_function(container: ApplicationContainer) -> None:
    service = container.my_service
```

**From legacy code (transitional):**
```python
from gpt_trader.app.container import get_application_container

container = get_application_container()
if container is None:
    raise RuntimeError("No application container set")
service = container.my_service
```

### Testing

Tests should create their own container instances:
```python
@pytest.fixture
def container(mock_config):
    return ApplicationContainer(mock_config)

def test_my_feature(container):
    service = container.my_service
    assert service.do_something() == expected
```

## Anti-Patterns

### Avoid: Module-Level Singletons

```python
# BAD: Hidden global state
_my_service: MyService | None = None

def get_my_service() -> MyService:
    global _my_service
    if _my_service is None:
        _my_service = MyService()
    return _my_service
```

**Why:** Makes testing difficult, hides dependencies, creates implicit coupling.

### Avoid: Service Locator in Business Logic

```python
# BAD: Service lookup inside business logic
def process_order(order: Order) -> None:
    container = get_application_container()
    risk = container.risk_manager  # Hidden dependency
    risk.validate(order)
```

**Better:** Pass dependencies explicitly:
```python
def process_order(order: Order, risk_manager: RiskManager) -> None:
    risk_manager.validate(order)
```

### Avoid: Circular Dependencies

If service A needs service B and B needs A, refactor to:
- Extract shared logic into a third service
- Use lazy initialization with callbacks
- Restructure the dependency graph

## Migration Guide

When migrating existing singletons to container:

1. Add the service to `ApplicationContainer`
2. Update call sites to receive container or service as parameter
3. Keep the old accessor as a deprecated shim (one release cycle):
   ```python
   def get_my_service() -> MyService:
       """Deprecated: Use container.my_service instead."""
       warnings.warn(
           "get_my_service() is deprecated, use container.my_service",
           DeprecationWarning,
           stacklevel=2,
       )
       container = get_application_container()
       if container is None:
           raise RuntimeError("No application container set")
       return container.my_service
   ```
4. Remove deprecated accessor after migration is complete

## References

- Container implementation: `src/gpt_trader/app/container.py`
- Architecture overview: `docs/ARCHITECTURE.md`
- Development guidelines: `docs/DEVELOPMENT_GUIDELINES.md`
