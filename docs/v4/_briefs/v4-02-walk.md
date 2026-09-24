# V4-02 walk: the New agent dialog

**Status: not run on a live stack.** The session that built V4-02 was told the dev api must stay read-only, so it created no real agents. The card's two live creates are still open (`_asks.md` #7).

## What was verified instead
The dialog was driven against the dev web server and the dev api with Playwright:
- Reads were real. `GET /v1/templates` returned the eight starters, and the editor was opened on an existing agent.
- `POST /v1/agents` was intercepted and answered from a stub. Every other write request was aborted.

The runs covered:
- Step 1 for every starter at desktop and phone widths, in light and dark. This includes the preview pane, the "Advanced example" divider before the insurance starter, and the `?template=` preselect.
- Step 2 prefilled, and the "Name is required." error.
- A stubbed 422 for a keyless starter on a connection without LiveKit Inference. The issue list renders, the "Add a provider key" hint shows and the dialog stays open.
- A stubbed 201 for `phone_agent` with `capabilities.dtmf: false`. The toast reads "Created from Phone agent" with the DTMF line.
- The editor's "Next steps" card on an existing agent with `?from=receptionist`: four steps and Dismiss.
- axe on every dialog state and on the editor: 0 critical, 0 serious.

## Still to do on a scratch api (its own port and DB)
1. Create `knowledge_assistant`. The editor should land on Knowledge with its two seeded knowledge bases listed and the Next steps card showing.
2. Create `phone_agent` on a connection without SIP. The toast should carry the DTMF note.
