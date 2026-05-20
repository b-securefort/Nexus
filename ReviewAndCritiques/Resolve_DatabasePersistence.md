# Critical Finding: Database Persistence (SQLite WAL on Azure Files)

## Overview
Running SQLite in WAL (Write-Ahead Logging) mode over networked filesystems (like Azure Files / SMB) is fundamentally unsafe. Network locking delays and disconnections will inevitably lead to database corruption under concurrent load. 

## Recommendation
Migrate the primary backend persistence to **Azure SQL** (which is already provisioned for the organization). 
- Replace SQLite/SQLModel engine configuration to point to the Azure SQL connection string.
- Adapt the local `sqlite-vec` extension logic. If the Azure SQL tier supports native vector capabilities (e.g., `VECTOR_DISTANCE`), migrate to that. Otherwise, store vectors as `varbinary` and calculate cosine similarity in Python as a fallback since the KB corpus is currently small.

## Impact of Recommendation
*   **Positive:** Completely eliminates the network locking and corruption risk. Provides enterprise-grade point-in-time recovery and seamless scaling.
*   **Negative/Cost:** Requires database migration and translating the `sqlite-vec` specific queries (`kb_chunks_vec`) into T-SQL compatible vector operations or Python fallback logic. 
