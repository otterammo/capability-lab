# ADR-0005: Canonical Configuration Hashing

**Status:** Accepted

Validate layered authored configuration with Pydantic, serialize the resolved value as sorted compact JSON, and identify it with SHA-256. Whitespace and key-order changes therefore do not change experiment identity. Provenance separately records every contributing layer.
