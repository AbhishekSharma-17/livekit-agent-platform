# Recipe: add a knowledge base from text

Goal: give an agent knowledge it can search and cite, starting from text you
already have (pasted, a local file, or a url) rather than a document upload
flow.

## 1. Create the knowledge base

`kb_create(...)`
```json
{ "name": "Return policy", "description": "Customer-facing return and refund rules" }
```

## 2. Add content — pick one source

Pasted text:

`kb_add_document(...)`
```json
{
  "kb_id": "<kb id>",
  "text": "Items may be returned within 30 days of delivery for a full refund. Final-sale items are marked at checkout and are not returnable.",
  "filename": "return-policy.md",
  "wait": true
}
```

A url the api fetches itself (never the MCP process — see
`lkap_explain("knowledge")` for why that boundary matters):

`kb_add_document(...)`
```json
{ "kb_id": "<kb id>", "url": "https://example.com/policies/returns.md", "wait": true }
```

A local file (stdio mode only):

`kb_add_document(...)`
```json
{ "kb_id": "<kb id>", "file_path": "/Users/alex/docs/return-policy.pdf", "wait": true }
```

## 3. Check it's ready

`wait=true` already returns the final `KbDocumentOut`; if `status` came back
`"failed"`, read `error` — an unsupported content type or a blocked url are
the common causes.

## 4. Try a search before attaching

`kb_search(...)`
```json
{ "kb_id": "<kb id>", "query": "can I return a final sale item", "top_k": 3 }
```

## 5. Attach to an agent

`agent_attach(...)`
```json
{ "id_or_slug": "<agent>", "kb_ids": ["<kb id>"] }
```

## Related concepts

`lkap_explain("knowledge")`.
