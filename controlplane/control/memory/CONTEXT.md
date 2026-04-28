# Memory Module Context

## Goal
- Provide a shared memory platform that any bot can plug into without owning database details.
- Keep short-term working memory separate from durable/common memory.
- Let QueryBot read selected long-term SalesBot learnings.
- Prevent SalesBot from reading QueryBot memory.

## Runtime Shape
- `Redis` stores recent working turns for fast recall.
- `Postgres` stores durable events, summaries, tasks, semantic facts, and episodic learnings.
- Bots call one `MemoryService`; they do not talk to databases directly.
- SalesBot also feeds recent memory context into its clarification/correction dialogue prompts so follow-up questions feel conversational instead of form-driven.
- Inside group chats, SalesBot tracks correction memory per staff member (`chat_id + sender_id`) so clarifications stay tied to the right person.

## Bootstrap Flow
- Run `scripts/setup_memory_stores.sh` once on a new laptop to seed the `env` file with Postgres and Redis credentials.
- `docker compose up -d memory-postgres memory-redis` starts the stores with persistent Docker volumes.
- Postgres runs `infrastructure/postgres/init/001-memory-init.sh` on the first boot of a fresh volume to create the `memory` schema, enable `pgcrypto`, and set the app user's search path.
- Redis uses password auth from the same `env` file; there is no password-rotation workflow in this bootstrap script.

## Current Shared Rules
- `QueryBot` reads:
  - its own recent turns
  - its own summaries and facts
  - shared/common SalesBot semantic and episodic memories
- `SalesBot` reads:
  - its own recent turns
  - its own summaries, tasks, and facts
  - common memories that SalesBot itself promoted
- `SalesBot` correction resolutions are promoted into shared/common memory.
- `QueryBot` memory stays private unless future work explicitly promotes safe shared items.

## Near-Term Extension Points
- swap deterministic summaries for LLM-based summaries
- add semantic/vector recall behind the same service
- add confidence/occurrence aggregation for repeated correction learnings
- add admin tooling to inspect, redact, or delete memories
