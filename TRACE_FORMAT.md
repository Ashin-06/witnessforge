# Action traces and parser records

Traces are JSON arrays in execution order. The CLI and browser import also accept an object with a `trace` array. Use synthetic data. Maximum trace length is 500 actions; every text value is limited to 10,000 characters, and identifiers and names to 256 characters. The browser/API input limit is 1 MiB.

## Exact action shapes

Unknown fields are rejected to avoid silently ignoring a misspelled instruction. Every `id` is unique. A message ID can be created only once. Referenced messages must already exist and be live.

```json
[
  {"id":"a1","op":"send","message_id":"m1","sender":"Noor","recipient":"Maya","text":"Draft"},
  {"id":"a2","op":"forward","message_id":"m2","source_id":"m1","sender":"Maya","recipient":"Ishan"},
  {"id":"a3","op":"edit","message_id":"m2","text":"Approved"},
  {"id":"a4","op":"react","message_id":"m2","sender":"Ishan","text":"acknowledged"},
  {"id":"a5","op":"delete","message_id":"m2"}
]
```

| Operation | Behavior |
| --- | --- |
| `send` | Creates a message with the supplied sender, recipient and text. |
| `forward` | Creates a new message containing the source's text at this point in the trace. The supplied sender is the actor forwarding it, not the original author. |
| `edit` | Replaces text and sets the edited flag. Empty text is valid. |
| `react` | Adds a `{sender,text}` reaction; duplicate reactions remain duplicate records in this model. |
| `delete` | Creates a tombstone: text becomes `null`, deleted becomes true, and other metadata and reactions remain. Later actions on this message are invalid. |

The forwarding source must be live when the forward is created, but deleting the source later does not delete its forwards. Message text is copied, not a live reference. These are the explicit reference-app semantics, not a statement about any commercial messaging app.

## Canonical parser output

A parser returns a Python list of dictionaries with exactly these fields:

```json
{
  "message_id": "m2",
  "sender": "Maya",
  "recipient": "Ishan",
  "text": null,
  "source_id": "m1",
  "edited": true,
  "deleted": true,
  "reactions": [{"sender":"Ishan","text":"acknowledged"}]
}
```

`source_id` is `null` for an original message. `edited` and `deleted` must be actual booleans, not integers or strings. Records and reactions may arrive in any order; comparison normalizes their order. Duplicate message IDs, missing canonical fields, extra fields, invalid values, and exceptions are unresolved parser results. A valid record with an incorrect value is a semantic failure.

## Finding and failure identity

```json
{
  "code": "FIELD_SENDER",
  "message_id": "m2",
  "expected": "Maya",
  "actual": "Noor"
}
```

Missing and unexpected records use `MISSING_MESSAGE` and `UNEXPECTED_MESSAGE`. The reduction target signature is `code:message_id`, for example `FIELD_SENDER:m2`. It preserves the error category/field and affected message, not all unrelated records or a hash of every expected/actual value. Repeat runs of any one candidate must agree on the full observations.

## Reference schemas

All database versions contain `reference_app(schema_version, scope)`.

| Version | Messages | Reactions |
| --- | --- | --- |
| 1 | `messages(id,sender,recipient,body,forwarded_from,source_author,edited,deleted)` | `reactions(mid,actor,emoji)` |
| 2 | `messages(message_key,actor,peer,payload,origin_key,origin_actor,edit_version,tombstone)` | `reaction_events(message_key,actor,reaction)` |
| 3 | `message_records(mid,envelope,payload,state)` | `message_reactions(mid,actor,value)` |

In version 3, `envelope` contains `actor`, `peer`, `source`, and `origin`; `payload` contains `text` or is SQL NULL for a tombstone. State bit 1 denotes edited and bit 2 denotes deleted. `origin`/`source_author` retains the author at the start of a forwarding chain; `actor`/`sender` identifies the actor creating this specific message. Confusing these fields is the deliberately seeded parser bug.
