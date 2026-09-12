from __future__ import annotations

import argparse
import base64
import datetime
import email.utils
import html
import json
import mimetypes
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import tomllib

from . import __version__



def fail(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)

# Credentials live with the Gmail MCP server -- one source of truth for both
# tools. Each account is a (credentials.json, token.json) pair in a subfolder.
GMAIL_DIR = Path(os.environ.get("GMAIL_ROOT") or Path(__file__).resolve().parents[2]).expanduser()
CONFIG = GMAIL_DIR / "config.toml"


def _load_config() -> dict:
    if not CONFIG.is_file():
        return {}
    with CONFIG.open("rb") as fh:
        return tomllib.load(fh)


_cfg = _load_config()
# Each account is a folder holding credentials.json + token.json. Without a
# config.toml there is one account, "default", at the repo root.
ACCOUNTS: dict[str, str] = {
    name: str(spec.get("dir", name)) for name, spec in (_cfg.get("accounts") or {}).items()
} or {"default": "."}
DEFAULT_ACCOUNT = os.environ.get("GMAIL_ACCOUNT") or str(_cfg.get("default") or next(iter(ACCOUNTS)))

TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://gmail.googleapis.com/gmail/v1/users/me"
TIMEOUT = 30


class MailError(RuntimeError):
    pass


def account_dir(account: str) -> Path:
    if account not in ACCOUNTS:
        raise MailError(f"unknown account '{account}' (choices: {', '.join(ACCOUNTS)})")
    return GMAIL_DIR / ACCOUNTS[account]


def _load_json(path: Path) -> dict:
    if not path.exists():
        raise MailError(f"missing {path}")
    return json.loads(path.read_text())


def access_token(account: str) -> str:
    """Exchange the stored refresh token for a fresh access token."""
    folder = account_dir(account)
    raw = _load_json(folder / "credentials.json")
    creds = raw.get("installed") or raw.get("web")
    if not creds:
        raise MailError(f"invalid credentials.json for account '{account}'")
    token = _load_json(folder / "token.json")
    if not token.get("refresh_token"):
        raise MailError(f"no refresh token for account '{account}'; re-run `npm run auth` for it")
    payload = urllib.parse.urlencode({
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=payload), timeout=TIMEOUT) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")
        raise MailError(f"token refresh failed for '{account}': {err.code} {detail}") from err
    except urllib.error.URLError as err:
        raise MailError(f"network error refreshing token: {err.reason}") from err
    return data["access_token"]


def api_request(token: str, method: str, path: str, query: dict | None = None, payload: dict | None = None) -> dict:
    url = f"{API}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query, doseq=True)
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode(errors="replace")
        try:
            detail = json.loads(detail)["error"]["message"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        raise MailError(f"Gmail API {err.code}: {detail}") from err
    except urllib.error.URLError as err:
        raise MailError(f"network error: {err.reason}") from err
    return json.loads(body) if body else {}


# ---------------------------------------------------------------- MIME


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _b64url_decode(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def build_raw(
    to: str,
    subject: str,
    body: str,
    cc: str | None = None,
    bcc: str | None = None,
    in_reply_to: str | None = None,
    references: str | None = None,
    attachment_path: Path | None = None,
) -> str:
    """Assemble an RFC 2822 message and return it base64url-encoded.

    Mirrors the Gmail MCP server's makeRawEmail: plain text, optionally
    wrapped in multipart/mixed with one attachment.
    """
    headers = [f"To: {to}", f"Subject: {subject}", "MIME-Version: 1.0"]
    if cc:
        headers.append(f"Cc: {cc}")
    if bcc:
        headers.append(f"Bcc: {bcc}")
    if in_reply_to:
        headers.append(f"In-Reply-To: {in_reply_to}")
    if references:
        headers.append(f"References: {references}")

    if attachment_path is not None:
        boundary = f"ws-email-{uuid.uuid4().hex}"
        filename = attachment_path.name
        mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        file_b64 = base64.b64encode(attachment_path.read_bytes()).decode()
        lines = [
            *headers,
            f'Content-Type: multipart/mixed; boundary="{boundary}"',
            "",
            f"--{boundary}",
            "Content-Type: text/plain; charset=UTF-8",
            "",
            body,
            "",
            f"--{boundary}",
            f'Content-Type: {mime}; name="{filename}"',
            "Content-Transfer-Encoding: base64",
            f'Content-Disposition: attachment; filename="{filename}"',
            "",
            file_b64,
            f"--{boundary}--",
        ]
    else:
        lines = [*headers, "Content-Type: text/plain; charset=UTF-8", "", body]
    return _b64url("\r\n".join(lines).encode())


def _decode_part(part: dict) -> str:
    data = part.get("body", {}).get("data")
    if data:
        return _b64url_decode(data).decode(errors="replace")
    for sub in part.get("parts", []) or []:
        text = _decode_part(sub)
        if text:
            return text
    return ""


def message_body(payload: dict) -> str:
    """Prefer text/plain, fall back to tag-stripped text/html."""
    if payload.get("mimeType") == "text/plain":
        return _decode_part(payload).strip()
    parts = payload.get("parts", []) or []
    plain = next((p for p in parts if p.get("mimeType") == "text/plain"), None)
    if plain:
        return _decode_part(plain).strip()
    html = next((p for p in parts if p.get("mimeType") == "text/html"), None)
    if html:
        return re.sub(r"<[^>]+>", " ", _decode_part(html)).strip()
    return _decode_part(payload).strip()


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []) or []:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _local_date(rfc2822: str) -> str:
    try:
        return email.utils.parsedate_to_datetime(rfc2822).astimezone().strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return rfc2822[:16]


def _epoch_date(millis: str | None) -> str:
    try:
        return datetime.datetime.fromtimestamp(int(millis) / 1000).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return ""


def iter_attachment_parts(payload: dict):
    """Yield every part of a message payload that is a real attachment."""
    for part in payload.get("parts", []) or []:
        if part.get("filename") and part.get("body", {}).get("attachmentId"):
            yield part
        yield from iter_attachment_parts(part)


def safe_filename(name: str) -> str:
    """Attachment names come from the sender -- never let them escape the target folder."""
    return Path(name).name or "attachment"


def _iso(millis: str | None) -> str:
    if not millis:
        return ""
    return datetime.datetime.fromtimestamp(int(millis) / 1000).astimezone().isoformat(timespec="seconds")


def _addresses(header: str) -> list[str]:
    return [addr for _name, addr in email.utils.getaddresses([header]) if addr] if header else []


def _record(msg: dict, account: str, full: bool) -> dict:
    """The connector contract's message record. `full` includes the decoded
    body; search results carry Gmail's snippet instead."""
    payload = msg.get("payload", {})
    name, sender = email.utils.parseaddr(_header(payload, "From"))
    return {
        "id": msg.get("id"),
        "account": account,
        "when": _iso(msg.get("internalDate")),
        "from": sender,
        "from_name": name,
        "to": _addresses(_header(payload, "To")),
        "cc": _addresses(_header(payload, "Cc")),
        "subject": _header(payload, "Subject"),
        "text": message_body(payload) if full else html.unescape(msg.get("snippet") or ""),
        "unread": "UNREAD" in (msg.get("labelIds") or []),
        "thread": msg.get("threadId"),
        "attachments": [
            {"name": p["filename"], "type": p.get("mimeType"), "size": p.get("body", {}).get("size", 0)}
            for p in iter_attachment_parts(payload)
        ],
    }


def _emit(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def _print_message(msg: dict) -> None:
    payload = msg.get("payload", {})
    for label, value in (
        ("From", _header(payload, "From")),
        ("To", _header(payload, "To")),
        ("Date", _header(payload, "Date")),
        ("Subject", _header(payload, "Subject")),
    ):
        if value:
            print(f"{label + ':':8} {value}")
    if msg.get("labelIds"):
        print(f"{'Labels:':8} {', '.join(msg['labelIds'])}")
    attachments = list(iter_attachment_parts(payload))
    if attachments:
        names = ", ".join(
            f"{p['filename']} ({p.get('mimeType')}, {p.get('body', {}).get('size', 0)} bytes)"
            for p in attachments
        )
        print(f"{'Attach:':8} {names}")
    print()
    print(message_body(payload))


# ---------------------------------------------------------------- compose


def _reply_context(token: str, message_id: str) -> dict:
    meta = api_request(token, "GET", f"/messages/{message_id}", query={
        "format": "metadata",
        "metadataHeaders": ["Message-ID", "References", "Subject"],
    })
    payload = meta.get("payload", {})
    msg_id_header = _header(payload, "Message-ID")
    ref_header = _header(payload, "References")
    subject = _header(payload, "Subject")
    references = f"{ref_header} {msg_id_header}".strip() if ref_header else msg_id_header
    if subject and not subject.startswith("Re:"):
        subject = f"Re: {subject}"
    return {
        "thread_id": meta.get("threadId"),
        "in_reply_to": msg_id_header or None,
        "references": references or None,
        "subject": subject or None,
    }


def _compose(args: argparse.Namespace, token: str) -> tuple[str, str | None]:
    """Shared draft/send assembly. Returns (raw, thread_id)."""
    body = args.body
    if body is None:
        if sys.stdin.isatty():
            raise MailError("no --body given and stdin is a terminal; pass --body or pipe the body in")
        body = sys.stdin.read()
    thread_id = in_reply_to = references = None
    subject = args.subject
    if args.reply_to:
        ctx = _reply_context(token, args.reply_to)
        thread_id, in_reply_to, references = ctx["thread_id"], ctx["in_reply_to"], ctx["references"]
        subject = subject or ctx["subject"]
    if not subject:
        raise MailError("need --subject (or --reply-to to derive it)")
    attachment = None
    if args.attach:
        attachment = Path(args.attach).expanduser()
        if not attachment.is_file():
            raise MailError(f"attachment not found: {attachment}")
    raw = build_raw(
        to=args.to, subject=subject, body=body, cc=args.cc, bcc=args.bcc,
        in_reply_to=in_reply_to, references=references, attachment_path=attachment,
    )
    return raw, thread_id


# ---------------------------------------------------------------- commands


def command_email_accounts(args: argparse.Namespace) -> None:
    ok, rows = True, []
    for account in ACCOUNTS:
        try:
            token = access_token(account)
            profile = api_request(token, "GET", "/profile")
            rows.append({"name": account, "address": profile.get("emailAddress"), "ok": True,
                         "messages": profile.get("messagesTotal"), "default": account == DEFAULT_ACCOUNT})
        except MailError as err:
            ok = False
            rows.append({"name": account, "address": None, "ok": False, "error": str(err), "default": account == DEFAULT_ACCOUNT})
    if getattr(args, "json", False):
        _emit(rows)
    else:
        for r in rows:
            print(f"ok: {r['name']:9} {r['address']} ({r['messages']} messages)" if r["ok"] else f"failed: {r['name']:9} {r['error']}")
    if not ok:
        raise SystemExit(1)


def command_capabilities(args: argparse.Namespace) -> None:
    _emit({
        "connector": "gmail", "version": __version__, "account_flag": "--account",
        "verbs": ["accounts", "search", "read", "send", "resolve", "capabilities"],
        "optional": ["draft", "drafts", "draft-show", "draft-send", "draft-delete", "attachments", "trash"],
        "features": {"threads": True, "subject": True, "attach": True, "drafts": True, "groups": False},
        "address": "email address; 'Name <addr>' accepted",
    })


def command_resolve(args: argparse.Namespace) -> None:
    """Mail has no contact store: an address is its own canonical form."""
    pairs = [(n, a) for n, a in email.utils.getaddresses([args.who]) if a and "@" in a]
    if not pairs:
        if args.json:
            _emit({"ok": False, "reason": "gmail resolves email addresses only; pass one", "candidates": []})
        else:
            print("gmail resolves email addresses only; pass one", file=sys.stderr)
        raise SystemExit(2)
    rows = [{"address": a, "name": n} for n, a in pairs]
    if args.json:
        _emit({"ok": True, "address": rows[0]["address"], "name": rows[0]["name"], "candidates": rows})
    else:
        for r in rows:
            print(f"{r['address']}" + (f"  ({r['name']})" if r["name"] else ""))


def _since_query(text: str) -> str:
    """'24h', '7d', '30m' or a date -> Gmail after: value (epoch seconds)."""
    units = {"d": 86400, "h": 3600, "m": 60}
    now = datetime.datetime.now()
    if text and text[-1] in units and text[:-1].isdigit():
        return str(int((now - datetime.timedelta(seconds=int(text[:-1]) * units[text[-1]])).timestamp()))
    return str(int(datetime.datetime.fromisoformat(text).timestamp()))


def command_email_search(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        query = {"maxResults": args.max}
        if args.query:
            query["q"] = args.query
        if getattr(args, "since", None):
            query["q"] = (query.get("q", "") + " after:" + _since_query(args.since)).strip()
        listing = api_request(token, "GET", "/messages", query=query)
        messages = listing.get("messages", []) or []
        if getattr(args, "json", False):
            rows = []
            for stub in messages:
                meta = api_request(token, "GET", f"/messages/{stub['id']}", query={
                    "format": "metadata", "metadataHeaders": ["From", "To", "Cc", "Subject", "Date"],
                })
                rows.append(_record(meta, args.account, full=False))
            _emit(rows)
            return
        if not messages:
            print("no messages found")
            return
        for stub in messages:
            meta = api_request(token, "GET", f"/messages/{stub['id']}", query={
                "format": "metadata",
                "metadataHeaders": ["From", "Subject", "Date"],
            })
            payload = meta.get("payload", {})
            flag = "unread" if "UNREAD" in (meta.get("labelIds") or []) else "      "
            date = _local_date(_header(payload, "Date"))
            sender = _header(payload, "From")[:34]
            subject = _header(payload, "Subject")[:60] or "(no subject)"
            print(f"{stub['id']}  {flag}  {date:16}  {sender:34}  {subject}")
    except MailError as err:
        fail(str(err))


def command_email_read(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        message = api_request(token, "GET", f"/messages/{args.message_id}", query={"format": "full"})
        if args.thread:
            thread = api_request(token, "GET", f"/threads/{message['threadId']}", query={"format": "full"})
            messages = thread.get("messages", []) or []
        else:
            messages = [message]
        if getattr(args, "json", False):
            _emit([_record(m, args.account, full=True) for m in messages])
            return
        for index, msg in enumerate(messages):
            if len(messages) > 1:
                print(f"--- message {index + 1}/{len(messages)} ---")
            _print_message(msg)
            if index < len(messages) - 1:
                print()
    except MailError as err:
        fail(str(err))


def command_email_draft(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        raw, thread_id = _compose(args, token)
        message: dict = {"raw": raw}
        if thread_id:
            message["threadId"] = thread_id
        result = api_request(token, "POST", "/drafts", payload={"message": message})
        if getattr(args, "json", False):
            _emit({"ok": True, "draft": result.get("id"), "thread": thread_id, "to": _addresses(args.to), "account": args.account})
            return
        print(f"draft created on '{args.account}': {result.get('id')}")
        print("review and send it from the Gmail drafts folder")
    except MailError as err:
        fail(str(err))


def command_email_send(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        raw, thread_id = _compose(args, token)
        payload: dict = {"raw": raw}
        if thread_id:
            payload["threadId"] = thread_id
        result = api_request(token, "POST", "/messages/send", payload=payload)
        if getattr(args, "json", False):
            _emit({"ok": True, "id": result.get("id"), "thread": result.get("threadId"),
                   "to": _addresses(args.to), "account": args.account})
            return
        print(f"sent from '{args.account}': message id {result.get('id')}")
    except MailError as err:
        if getattr(args, "json", False):
            _emit({"ok": False, "reason": str(err), "account": args.account}); raise SystemExit(1)
        fail(str(err))


def command_email_attachments(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        message = api_request(token, "GET", f"/messages/{args.message_id}", query={"format": "full"})
        parts = list(iter_attachment_parts(message.get("payload", {})))
        if not parts:
            print("no attachments")
            return
        if not args.save:
            for part in parts:
                print(f"{part['filename']}  {part.get('mimeType')}  {part.get('body', {}).get('size', 0)} bytes")
            return
        target_dir = Path(args.save).expanduser()
        target_dir.mkdir(parents=True, exist_ok=True)
        for part in parts:
            target = target_dir / safe_filename(part["filename"])
            if target.exists() and not args.force:
                raise MailError(f"refusing to overwrite {target} (use --force)")
            data = api_request(token, "GET", f"/messages/{args.message_id}/attachments/{part['body']['attachmentId']}")
            target.write_bytes(_b64url_decode(data["data"]))
            print(f"saved: {target}")
    except MailError as err:
        fail(str(err))


def command_email_drafts(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        listing = api_request(token, "GET", "/drafts", query={"maxResults": args.max})
        drafts = listing.get("drafts", []) or []
        if not drafts:
            print("no drafts")
            return
        for stub in drafts:
            draft = api_request(token, "GET", f"/drafts/{stub['id']}", query={
                "format": "metadata",
                "metadataHeaders": ["To", "Subject"],
            })
            msg = draft.get("message", {})
            payload = msg.get("payload", {})
            updated = _epoch_date(msg.get("internalDate"))
            to = _header(payload, "To")[:30]
            subject = _header(payload, "Subject")[:50] or "(no subject)"
            print(f"{stub['id']}  {updated:16}  {to:30}  {subject}")
    except MailError as err:
        fail(str(err))


def command_email_draft_show(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        draft = api_request(token, "GET", f"/drafts/{args.draft_id}", query={"format": "full"})
        _print_message(draft.get("message", {}))
    except MailError as err:
        fail(str(err))


def command_email_draft_send(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        result = api_request(token, "POST", f"/drafts/{args.draft_id}/send")
        print(f"draft sent from '{args.account}': message id {result.get('id')}")
    except MailError as err:
        fail(str(err))


def command_email_draft_delete(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        api_request(token, "DELETE", f"/drafts/{args.draft_id}")
        print(f"draft permanently deleted on '{args.account}': {args.draft_id} (drafts have no trash)")
    except MailError as err:
        fail(str(err))


def command_email_trash(args: argparse.Namespace) -> None:
    try:
        token = access_token(args.account)
        api_request(token, "POST", f"/messages/{args.message_id}/trash")
        print(f"moved to trash on '{args.account}': {args.message_id} (recoverable in Gmail for ~30 days)")
    except MailError as err:
        fail(str(err))


def command_email_help(args: argparse.Namespace) -> None:
    args.parser.print_help()


# ---------------------------------------------------------------- parser


def _add_account_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="machine-readable output (connector contract)")
    parser.add_argument(
        "--account",
        default=DEFAULT_ACCOUNT,
        help=f"gmail account (choices: {', '.join(ACCOUNTS)}; default: {DEFAULT_ACCOUNT}, override with GMAIL_ACCOUNT)",
    )


def _add_compose_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--to", required=True, help="recipient (comma-separated for multiple)")
    parser.add_argument("--subject", help="subject line (derived as 'Re: ...' when --reply-to is set)")
    parser.add_argument("--body", help="plain-text body (omit to read it from stdin)")
    parser.add_argument("--cc", help="CC recipients (comma-separated)")
    parser.add_argument("--bcc", help="BCC recipients (comma-separated)")
    parser.add_argument("--attach", help="path of a file to attach")
    parser.add_argument("--reply-to", help="message id to reply to (threads the conversation)")
    _add_account_arg(parser)


def build_parser() -> argparse.ArgumentParser:
    email_parser = argparse.ArgumentParser(
        prog="gmail",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Read, search, draft, and send Gmail from the shell, one or more accounts.",
        epilog=f"""Accounts ({', '.join(ACCOUNTS)}; default {DEFAULT_ACCOUNT}) are folders under
{GMAIL_DIR}, each holding credentials.json and token.json, declared in config.toml.
Authenticate with `npm run auth` (GMAIL_CREDENTIALS_PATH/GMAIL_TOKEN_PATH for a subfolder).

Examples:
  gmail search "is:unread" -n 5
  gmail read 19f76a1bd98cdf6a --thread
  gmail draft --to x@y.org --subject Hi --body "Text." --attach notes.pdf
  git diff | gmail send --to me@me.org --subject "Diff" --account personal
""",
    )
    email_parser.add_argument("--version", action="version", version=__version__)
    email_parser.set_defaults(func=command_email_help, parser=email_parser)
    email_sub = email_parser.add_subparsers(dest="email_command")

    accounts = email_sub.add_parser("accounts", help="list the accounts and check that each authenticates")
    accounts.add_argument("--json", action="store_true")
    accounts.set_defaults(func=command_email_accounts)

    caps = email_sub.add_parser("capabilities", help="what this connector implements (JSON)")
    caps.set_defaults(func=command_capabilities)

    resolve = email_sub.add_parser("resolve", help="canonical form of a recipient (an address is its own)")
    resolve.add_argument("who")
    _add_account_arg(resolve)
    resolve.set_defaults(func=command_resolve)

    search = email_sub.add_parser("search", help="list messages matching a Gmail query")
    search.add_argument("query", nargs="?", help='Gmail search syntax, e.g. "is:unread from:x@y.org" (omit for most recent)')
    search.add_argument("-n", "--max", type=int, default=10, help="maximum results (default: 10)")
    search.add_argument("--since", help="24h, 7d, or an ISO date; adds an after: clause")
    _add_account_arg(search)
    search.set_defaults(func=command_email_search)

    read = email_sub.add_parser("read", help="print one message (or its whole thread)")
    read.add_argument("message_id", help="message id from `gmail search`")
    read.add_argument("--thread", action="store_true", help="print the entire conversation")
    _add_account_arg(read)
    read.set_defaults(func=command_email_read)

    attachments = email_sub.add_parser("attachments", help="list a message's attachments, or save them to disk")
    attachments.add_argument("message_id", help="message id from `gmail search`")
    attachments.add_argument("--save", help="folder to save the attachments into (omit to just list them)")
    attachments.add_argument("--force", action="store_true", help="overwrite existing files when saving")
    _add_account_arg(attachments)
    attachments.set_defaults(func=command_email_attachments)

    draft = email_sub.add_parser("draft", help="create a Gmail draft (saved, not sent)")
    _add_compose_args(draft)
    draft.set_defaults(func=command_email_draft)

    drafts = email_sub.add_parser("drafts", help="list the drafts folder")
    drafts.add_argument("-n", "--max", type=int, default=10, help="maximum results (default: 10)")
    _add_account_arg(drafts)
    drafts.set_defaults(func=command_email_drafts)

    draft_show = email_sub.add_parser("draft-show", help="print one draft (headers, body, attachments)")
    draft_show.add_argument("draft_id", help="draft id from `gmail drafts`")
    _add_account_arg(draft_show)
    draft_show.set_defaults(func=command_email_draft_show)

    draft_send = email_sub.add_parser("draft-send", help="send an existing draft as-is")
    draft_send.add_argument("draft_id", help="draft id from `gmail drafts`")
    _add_account_arg(draft_send)
    draft_send.set_defaults(func=command_email_draft_send)

    draft_delete = email_sub.add_parser("draft-delete", help="delete a draft (immediate and permanent; drafts have no trash)")
    draft_delete.add_argument("draft_id", help="draft id from `gmail drafts`")
    _add_account_arg(draft_delete)
    draft_delete.set_defaults(func=command_email_draft_delete)

    send = email_sub.add_parser("send", help="send an email immediately")
    _add_compose_args(send)
    send.set_defaults(func=command_email_send)

    trash = email_sub.add_parser("trash", help="move a message to trash (reversible)")
    trash.add_argument("message_id", help="message id from `gmail search`")
    _add_account_arg(trash)
    trash.set_defaults(func=command_email_trash)
    return email_parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except BrokenPipeError:
        pass
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
