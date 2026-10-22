# Architecture

How the Wallet & Transaction Service is built. GitHub renders the diagrams below. In VS Code, install a Mermaid preview extension to see them.

## 1. Layers

Every request goes down one layer at a time. A layer talks only to the layer directly below it.

```mermaid
flowchart TB
    Client(["Client"]) --> MW["Middleware: request ID, client IP, request log"]
    MW --> API["API layer: routers and dependencies. HTTP only, no business logic"]
    API --> SVC["Service layer: all business rules. Never imports FastAPI, owns the commit"]
    SVC --> REPO["Repository layer: SQL only. Flushes, never commits"]
    REPO --> DB[("PostgreSQL: constraints and immutability triggers")]
    SVC -. "raises AppError" .-> EH["Global exception handlers: standard error JSON"]
    EH -. "HTTP response" .-> Client
```

Where things live:

| Layer | Folder | Rule |
|---|---|---|
| API | `app/api/` | Parses the request, calls one service, returns the response. `deps.py` is the only place repositories are wired into services. |
| Service | `app/services/` | Validates, applies business rules, and owns the DB transaction. |
| Repository | `app/repositories/` | One class per table. No business rules. |
| Models and schemas | `app/models/`, `app/schemas/` | SQLAlchemy tables and Pydantic request/response shapes. |
| Core | `app/core/` | Settings, security, money validation, idempotency hash, pagination, rate limiter. |

## 2. Request flow: a deposit

One deposit is one database transaction. The wallet row is locked first, so parallel requests for the same wallet queue up instead of overwriting each other.

```mermaid
sequenceDiagram
    autonumber
    participant C as Client
    participant R as Router and dependencies
    participant S as TransactionService
    participant DB as PostgreSQL

    C->>R: POST /wallets/deposit with token and Idempotency-Key
    R->>R: check token, then rate limit per user
    R->>S: deposit(user, key, amount, reference)
    S->>S: validate the amount, build the request hash
    S->>DB: SELECT wallet FOR UPDATE (row lock)
    S->>DB: look up this user and key
    alt key already used with the same request hash
        S-->>R: the original transaction
        R-->>C: 200 and Idempotent-Replayed true
    else key already used with a different request
        S-->>R: DuplicateTransactionError
        R-->>C: 409
    else new key
        S->>S: check the wallet is active
        S->>DB: INSERT transaction and 2 ledger entries, UPDATE balance, INSERT audit row
        S->>DB: COMMIT (releases the lock)
        S-->>R: the new transaction
        R-->>C: 201
    end
```

Withdrawals follow the same path, with two extra checks after the frozen check and before anything is written: enough balance, then the daily limit. A rejected request raises an error, nothing is written, and the lock is released when the session closes.

## 3. Why transfers cannot deadlock

A transfer touches two wallets. If A to B locked A then B, while B to A locked B then A, each would wait for the other forever. Instead, both wallets are locked in one query ordered by ID, so every transfer takes locks in the same order.

```mermaid
sequenceDiagram
    participant T1 as Transfer A to B
    participant T2 as Transfer B to A
    participant DB as PostgreSQL

    Note over T1,T2: Wallet A has the smaller ID, so it always sorts first
    T1->>DB: SELECT wallets A and B ORDER BY id FOR UPDATE
    Note right of DB: T1 locks A, then B
    T2->>DB: SELECT wallets A and B ORDER BY id FOR UPDATE
    Note right of DB: T2 waits for A. It never holds B first, so no deadlock
    T1->>DB: write transaction, ledger, balances, audit row, COMMIT
    DB-->>T2: locks released, T2 reads the fresh balances
    T2->>DB: write transaction, ledger, balances, audit row, COMMIT
```

The balance check and the daily limit run **after** the locks are taken, so they see committed data and cannot be beaten by parallel requests.

## 4. The ledger model

Convention, from the wallet's point of view: a **credit** puts money in, a **debit** takes money out. Every transaction has exactly two entries, and its debits equal its credits.

| Transaction | Wallet entry | Other entry |
|---|---|---|
| Deposit | credit the user's wallet | debit **external** (no wallet) |
| Withdrawal | debit the user's wallet | credit **external** (no wallet) |
| Transfer | debit the source wallet | credit the destination wallet |

"External" is a ledger entry with `wallet_id` empty and `balance_after` empty. There is no system wallet, so there is no hot row that every deposit would fight over, and `CHECK (balance >= 0)` never needs an exception.

Each wallet entry stores `balance_after`, the wallet balance right after it. That gives statements a running balance, and lets an auditor verify that each wallet's balance equals the sum of its entries.

```mermaid
flowchart LR
    subgraph Deposit
        D1["External: debit 100"] -->|"money in"| D2["Wallet: credit 100, balance_after 100"]
    end
    subgraph Withdrawal
        W1["Wallet: debit 30, balance_after 70"] -->|"money out"| W2["External: credit 30"]
    end
    subgraph Transfer
        T1["Source wallet: debit 20, balance_after 50"] -->|"moves"| T2["Destination wallet: credit 20, balance_after 20"]
    end
```

## 5. Database schema

```mermaid
erDiagram
    USERS ||--|| WALLETS : "owns one"
    USERS ||--o{ TRANSACTIONS : "initiates"
    USERS ||--o{ AUDIT_LOGS : "is the actor of"
    WALLETS ||--o{ TRANSACTIONS : "source or destination of"
    WALLETS ||--o{ LEDGER_ENTRIES : "has"
    TRANSACTIONS ||--|{ LEDGER_ENTRIES : "has exactly 2"

    USERS {
        uuid id PK
        string email UK
        string password_hash
        string status
        string role
    }
    WALLETS {
        uuid id PK
        uuid user_id FK
        string currency
        numeric balance
        string status
        int version
    }
    TRANSACTIONS {
        uuid id PK
        uuid user_id FK
        string type
        string status
        numeric amount
        uuid source_wallet_id FK
        uuid destination_wallet_id FK
        string idempotency_key
        string request_hash
    }
    LEDGER_ENTRIES {
        uuid id PK
        uuid transaction_id FK
        uuid wallet_id FK
        string direction
        numeric amount
        numeric balance_after
    }
    AUDIT_LOGS {
        uuid id PK
        uuid actor_id FK
        string action
        string entity_type
        uuid entity_id
        jsonb metadata
        string ip_address
    }
```

Rules the database enforces by itself, whatever the application does:

- `wallets.balance >= 0`, and one wallet per user.
- `amount > 0` on transactions and ledger entries.
- Deposits have only a destination, withdrawals only a source, and transfers both (and they differ).
- `UNIQUE (user_id, idempotency_key)`.
- Triggers reject any `UPDATE` or `DELETE` on `ledger_entries` and `audit_logs`, any `DELETE` on `transactions`, and any `UPDATE` of a completed transaction.

## 6. Safety nets, from outside in

| Layer | What it catches |
|---|---|
| Rate limiter | Floods and brute force, before any database work |
| Validation | Bad amounts, bad IDs, bad headers (422) |
| Idempotency | Retries and duplicates, so money never moves twice |
| Row locks | Lost updates, overdrafts and daily-limit races |
| Database constraints | A bug that slips past all of the above (negative balance, wrong shape) |
| Triggers | Any attempt to rewrite history |
| Ledger auditor | Whether the books add up (used in tests, and usable as a nightly job) |