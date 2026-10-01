# Webhooks

A webhook endpoint (`WebhookEndpointCreate{url, events, description,
enabled}`) gets a signed `POST` for each subscribed event (session ended, an
`end` flow node fired with `webhook_event=true`, a QA verdict, …).
`url` must be `https://` outside dev.

## Creating one

`webhook_create(url, events=[...], description=)` returns the
`WebhookEndpointOut` plus a `secret_file`. In local (stdio) mode the signing
secret is written once to `~/.config/lkap/webhooks/<endpoint_id>.secret`
(mode `0600`) and the response gives you the path, never the value itself.
The same non-negotiable as every other secret this platform issues. In
remote (HTTP) mode `webhook_create` is not available at all. Create the
endpoint from the console instead, where the one-time reveal has somewhere
safe to be shown.

`webhook_list()` shows every endpoint (needs `webhooks:write`. There is no
separate read scope for webhooks). `webhook_test(endpoint_id)` fires one
test delivery. `webhook_deliveries(endpoint_id, redeliver=, limit=)` lists
past attempts and can replay one by its delivery id.

## Related tools

`webhook_list`, `webhook_create`, `webhook_test`, `webhook_deliveries`.

## Related schemas

`WebhookEndpointCreate`, `WebhookEndpointOut`, `WebhookDeliveryOut`,
`WebhookEvent`.
