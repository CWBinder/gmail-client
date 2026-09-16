# Email Contract

`gmail` reads, searches, drafts, and sends Gmail for the three accounts
from the terminal. It talks to the Gmail REST API directly over stdlib
`urllib` — no MCP server process, no third-party packages, no curl/jq.

## Accounts

| Name       | Address                           | Credential folder |
|------------|-----------------------------------|-------------------|
| `qmt`      | `<work-address>`                  | `<gmail-dir>/`    |
| `personal` | `<personal-address>`              | `<gmail-dir>/personal/` |
| `oxai`     | `<committee-address>`             | `<gmail-dir>/oxai/`     |

`<gmail-dir>` is `~/Projects/gmail` (override: `WS_GMAIL_DIR`). Select
per call with `--account`; the default is `qmt` (override: `WS_EMAIL_ACCOUNT`).

## Credential sharing (single source of truth)

The credential store belongs to the Gmail MCP server; `gmail` only reads
it. Each account is a `credentials.json` (OAuth client) plus `token.json`
(refresh token) pair. Rules:

- A fresh access token is fetched on every invocation; `token.json` is
  **never written** by `gmail`, so the CLI cannot interfere with the MCP's
  own token management.
- No credential material lives in this repository — only the path above.
- When a refresh token expires or is revoked (`invalid_grant`), re-run the
  MCP auth flow, which rewrites the token file both tools use:
  `cd ~/Projects/gmail && GMAIL_CREDENTIALS_PATH=<acct>/credentials.json
  GMAIL_TOKEN_PATH=<acct>/token.json npm run auth`

### Re-auth pitfalls (both bitten 2026-08-07)

- **Each account has its own OAuth client.** The env vars above are not
  optional: without them the flow falls back to the root `credentials.json`,
  whose OAuth client is *internal to the quantummotion.tech org* — signing in
  with the personal or oxai account then fails with `403 org_internal`. The
  auth script prints which credentials file it is using; check that line
  matches the intended account before anyone signs in.
- **A failed auth run keeps port 3456.** If Google blocks the sign-in, the
  script waits forever holding the callback port. A retry then dies with
  `EADDRINUSE` — and the browser tab still open is the *old* run's, pointing
  at the wrong client, so retrying in it reproduces the original error.
  Fix: `lsof -ti :3456 | xargs kill`, re-run, and sign in only via the tab
  the new run opens.
- **Verify, then trust.** After re-auth, confirm with
  `ws connectors check email --live` (authenticates every account) rather
  than relying on the "token saved" message.
- If the personal token dies with `invalid_grant` again after ~7 days, its
  OAuth app is still in "Testing" publishing status, which expires refresh
  tokens weekly. Permanent fix: set that app to "In production" on the OAuth
  consent screen of its Google Cloud project.

## Verification

`gmail accounts` — and every bare `ws check` — verifies offline that each
account above keeps its `credentials.json` + `token.json` pair in the
credential store. `ws connectors check email` prints the same state per
account; with `--live` it goes further and authenticates every account
against the Gmail API.

## Commands

```text
gmail accounts
gmail threads [QUERY] [--from ADDRESS] [--since TIME] [-n N]
gmail search [QUERY] [--thread ID] [--from ADDRESS] [--since TIME] [--native] [-n N]
gmail read --message MESSAGE-ID
gmail read --thread THREAD-ID [-n N]
gmail attachments ID [--save DIR] [--force]
gmail drafts create --to ... [...]
gmail drafts list [-n N]
gmail drafts show DRAFT-ID
gmail drafts send DRAFT-ID
gmail drafts delete DRAFT-ID
gmail send --to ... [...]
gmail trash ID
```

Every search result has a self-contained message `id` and a `thread` id.
`read --message` retrieves exactly one message; `read --thread` retrieves the
complete email chain directly unless `-n` explicitly limits it. `threads`
returns independently readable thread summaries.
A bare search term searches Gmail-visible content. `--native` documents that
the query intentionally uses Gmail operators such as `is:unread` or `from:`.

The draft-first workflow is closed end to end: `drafts create` composes,
`drafts list`/`show` review, `drafts send` releases -- nothing has to touch
the Gmail UI. `attachments --save` refuses to overwrite existing files
unless `--force` is given, and sender-supplied filenames are stripped to
their basename so they cannot escape the target folder.

Draft and send accept the same composition flags (`--subject`, `--body`,
`--cc`, `--bcc`, `--attach FILE`, `--reply-to MSG-ID`, `--account`) and share
one MIME builder, so attachments and reply threading behave identically in
both. `--reply-to` sets the thread id and `In-Reply-To`/`References` headers
and derives `Re: <subject>` when `--subject` is omitted. With no `--body`,
the body is read from stdin (piping; a bare terminal is refused).

## Deliberate limits

- **Trash is the deletion ceiling for messages.** The OAuth scopes are
  `gmail.readonly`, `gmail.send`, `gmail.modify` — permanent message deletion
  needs the full `https://mail.google.com/` scope, which is intentionally not
  granted. The one exception is `draft-delete`: the API has no draft trash,
  so deleting a draft is immediate and permanent. That is acceptable because
  a draft is an unsent working copy, never received mail.
- **`send` and `drafts send` have no confirmation prompt.** Typing the command
  is the confirmation. Prefer `draft` when in doubt: it lands in the Gmail
  drafts folder for review and nothing leaves the account.
- **One attachment per message**, base64-inlined; practical size limit is
  roughly 25 MB (the API caps the encoded request at ~35 MB).
- Agent sessions (Claude Code) are blocked by the permission layer from
  executing `gmail send` / `drafts send` — sends are typed by a human.

## Relationship to the Gmail MCP

Same accounts, same credentials, same API underneath. The MCP server
(`~/Projects/gmail`, Node) serves interactive Claude sessions in
`~/Documents`; `gmail` serves the terminal and scripts. The CLI surface
is a superset of the MCP's: drafts management, attachment download, and
trash exist only here (the MCP exposes no deletion at all).
