# Portfolio one-shot example

User request: “In my existing Core CNY Portfolio, record the already executed purchase of 100 shares of 600000 on Shanghai at CNY 10.00 per share, executed on 2025-01-02 at 10:00 Asia/Shanghai. Commission was CNY 5.00, tax zero, other fees zero. Show the accounting result.”

Inputs: the user supplies every trade fact, including explicit zero fees and historical time. This example starts with one active Core Portfolio, CNY 10,000 opening cash, no holdings, and a single opening Ledger entry. The following returns are **illustrative example JSON**, not real account or execution evidence. `example-core-id` is obtained from discovery in this example; use the actual returned ID when executing. Normal mutation approval applies.

## Load the domain instructions

```json
{
  "tool": "load_skill",
  "arguments": {
    "name": "investorch-portfolio"
  }
}
```
Selected fields of the illustrative return (the actual return also includes full Skill content and description):

```json
{
  "name": "investorch-portfolio",
  "version": "1.1.0",
  "resources": [
    "references/examples.md"
  ]
}
```

## Identify and inspect

```json
{
  "tool": "list_portfolios",
  "arguments": {
    "include_archived": false
  }
}
```
```json
{
  "portfolios": [
    {
      "portfolio_id": "example-core-id",
      "name": "Core",
      "description": null,
      "status": "ACTIVE",
      "base_currency": "CNY",
      "strategy_binding": null
    }
  ]
}
```

Exactly one active Core CNY match is returned. If the name is ambiguous, resolve identity before mutation. Use its returned `portfolio_id` for both reads:

```json
{
  "tool": "get_portfolio",
  "arguments": {
    "portfolio_id": "example-core-id"
  }
}
```
```json
{
  "portfolio": {
    "portfolio_id": "example-core-id",
    "name": "Core",
    "description": null,
    "status": "ACTIVE",
    "base_currency": "CNY",
    "strategy_binding": null,
    "created_at": "2025-01-01T09:00:00+08:00",
    "updated_at": "2025-01-01T09:00:00+08:00"
  },
  "state": {
    "portfolio_id": "example-core-id",
    "cash": {
      "CNY": "10000"
    },
    "holdings": []
  }
}
```
```json
{
  "tool": "get_portfolio_ledger",
  "arguments": {
    "portfolio_id": "example-core-id",
    "limit": 50
  }
}
```
```json
{
  "portfolio_id": "example-core-id",
  "entries": [
    {
      "entry_id": "example-opening-entry",
      "operation_id": "example-opening-operation",
      "sequence": 1,
      "entry_type": "OPENING_CASH",
      "effective_at": "2025-01-01T09:00:00+08:00",
      "recorded_at": "2025-01-01T09:00:01+08:00",
      "source": "user",
      "external_ref": null,
      "payload": {
        "currency": "CNY",
        "amount": "10000"
      }
    }
  ],
  "returned": 1,
  "total": 1,
  "has_older": false
}
```

The Portfolio is active, has enough logical cash, and contains no matching trade. `has_older=false` makes this a complete history for this fixture. A truncated history is not proof that an older trade is absent. This is an executed trade, so use `record_portfolio_trade`, rather than opening state, external capital, income, or an adjustment. Derive the timezone offset +08:00 from the supplied Asia/Shanghai historical time.

## Record the executed fact

```json
{
  "tool": "record_portfolio_trade",
  "arguments": {
    "portfolio_id": "example-core-id",
    "code": "600000",
    "market": "XSHG",
    "side": "BUY",
    "quantity": "100",
    "price": "10.00",
    "commission": "5.00",
    "tax": "0",
    "other_fee": "0",
    "effective_at": "2025-01-02T10:00:00+08:00"
  }
}
```
```json
{
  "operation_id": "example-trade-operation",
  "entries": [
    {
      "entry_id": "example-trade-entry",
      "sequence": 2,
      "entry_type": "TRADE",
      "effective_at": "2025-01-02T10:00:00+08:00",
      "recorded_at": "2025-01-02T10:05:00+08:00",
      "portfolio_id": "example-core-id"
    }
  ],
  "states": {
    "example-core-id": {
      "portfolio_id": "example-core-id",
      "cash": {
        "CNY": "8995.00"
      },
      "holdings": [
        {
          "instrument": {
            "code": "600000",
            "market": "XSHG"
          },
          "quantity": "100",
          "total_cost": "1005.00",
          "average_cost": "10.05"
        }
      ]
    }
  }
}
```

Retain the returned operation and entry identifiers. Deterministic accounting is 100 × 10.00 + 5.00 = CNY 1,005.00; expected cash is CNY 8,995.00 and recognized cost is CNY 1,005.00. The tool records that fact; it submits no broker order.

## Read back the durable result

```json
{
  "tool": "get_portfolio",
  "arguments": {
    "portfolio_id": "example-core-id"
  }
}
```
```json
{
  "portfolio": {
    "portfolio_id": "example-core-id",
    "name": "Core",
    "description": null,
    "status": "ACTIVE",
    "base_currency": "CNY",
    "strategy_binding": null,
    "created_at": "2025-01-01T09:00:00+08:00",
    "updated_at": "2025-01-01T09:00:00+08:00"
  },
  "state": {
    "portfolio_id": "example-core-id",
    "cash": {
      "CNY": "8995.00"
    },
    "holdings": [
      {
        "instrument": {
          "code": "600000",
          "market": "XSHG"
        },
        "quantity": "100",
        "total_cost": "1005.00",
        "average_cost": "10.05"
      }
    ]
  }
}
```
```json
{
  "tool": "get_portfolio_ledger",
  "arguments": {
    "portfolio_id": "example-core-id",
    "limit": 50
  }
}
```
```json
{
  "portfolio_id": "example-core-id",
  "entries": [
    {
      "entry_id": "example-opening-entry",
      "operation_id": "example-opening-operation",
      "sequence": 1,
      "entry_type": "OPENING_CASH",
      "effective_at": "2025-01-01T09:00:00+08:00",
      "recorded_at": "2025-01-01T09:00:01+08:00",
      "source": "user",
      "external_ref": null,
      "payload": {
        "currency": "CNY",
        "amount": "10000"
      }
    },
    {
      "entry_id": "example-trade-entry",
      "operation_id": "example-trade-operation",
      "sequence": 2,
      "entry_type": "TRADE",
      "effective_at": "2025-01-02T10:00:00+08:00",
      "recorded_at": "2025-01-02T10:05:00+08:00",
      "source": "agent",
      "external_ref": null,
      "payload": {
        "instrument": {
          "code": "600000",
          "market": "XSHG"
        },
        "side": "BUY",
        "quantity": "100",
        "price": "10.00",
        "commission": "5.00",
        "tax": "0",
        "other_fee": "0"
      }
    }
  ],
  "returned": 2,
  "total": 2,
  "has_older": false
}
```

Match the new entry’s `entry_id` and `operation_id` to the mutation return, then compare its payload and economic timestamp with the user facts. The readback confirms quantity 100, total cost 1,005.00, average cost 10.05, and logical cash 8,995.00. If any mismatch occurs, report it and investigate through Portfolio tools; do not repeat a potentially successful mutation or edit storage directly.

Final answer example: “Recorded the executed purchase in Core: 100 shares of 600000/XSHG at CNY 10.00, plus CNY 5.00 commission, at 2025-01-02 10:00 +08:00. Ledger entry example-trade-entry in operation example-trade-operation reads back correctly. Logical cash is CNY 8,995.00; the holding is 100 shares with cost CNY 1,005.00. No broker order was submitted.”
