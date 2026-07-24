# ADR-0001: Modular Monolith

**Status:** Accepted

Keep one Python deployment with `cli -> application -> domain` and `application -> ports <- adapters`. Microservices add no capability to a single-user local experiment loop. Import-linter enforces the boundary; a later process split can preserve the same ports if measured load requires it.
