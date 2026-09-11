---
name: gmail
description: Read, search, draft and send Gmail from the shell across one or more accounts; save attachments. Use when asked to check, find, summarise or reply to email.
---

# gmail

Every command takes `--account NAME`; `gmail accounts` lists the names and
checks that each authenticates. Without the flag the configured default is
used. Name the account in every report.

```bash
gmail accounts                                   # first; a failed line names the account to re-authenticate
gmail search "is:unread" -n 20 --account NAME    # Gmail query syntax; quote it
gmail read ID [--thread] --account NAME
gmail attachments ID [--save DIR] [--force] --account NAME
gmail draft --to X [--subject S] [--body B] [--reply-to ID] [--attach FILE] --account NAME
gmail drafts [-n 10] | draft-show DRAFT_ID
```

`--reply-to ID` threads the reply and derives the subject. A draft is
saved, not sent. If a search is empty, broaden it before concluding the
message does not exist.

## Only on explicit instruction

```bash
gmail send ... | gmail draft-send DRAFT_ID      # sends
gmail trash ID                                  # reversible in Gmail for ~30 days
gmail draft-delete DRAFT_ID                     # immediate and permanent
```

## Re-authentication

`invalid_grant` on `gmail accounts` means that account's refresh token has
expired. The account owner runs, from this repo:

```bash
npm run auth                                                       # the root account
GMAIL_CREDENTIALS_PATH=$PWD/NAME/credentials.json GMAIL_TOKEN_PATH=$PWD/NAME/token.json npm run auth   # a subfolder account
```

signed in as the matching Google account. A failed run keeps port 3456;
`lsof -ti :3456 | xargs kill` before retrying.
