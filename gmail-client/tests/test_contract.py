import contextlib
import io
import json
import unittest
from argparse import Namespace
from unittest.mock import patch

from gmail_cli import cli


def message(mid, thread, subject, text, sender="alice@example.org", when="1760000000000"):
    return {
        "id": mid, "threadId": thread, "internalDate": when, "labelIds": [],
        "snippet": text,
        "payload": {"headers": [
            {"name": "From", "value": sender},
            {"name": "To", "value": "me@example.org"},
            {"name": "Subject", "value": subject},
        ], "parts": [], "body": {"data": ""}},
    }


class ContractTests(unittest.TestCase):
    def output(self, func, args):
        with contextlib.redirect_stdout(io.StringIO()) as stream:
            func(args)
        return json.loads(stream.getvalue())

    def test_threads_returns_standard_record(self):
        msgs = [message("m1", "t1", "Budget", "First"),
                message("m2", "t1", "Budget", "Latest", when="1760000010000")]
        def api(_token, _method, path, query=None):
            return {"threads": [{"id": "t1"}]} if path == "/threads" else {"id": "t1", "messages": msgs}
        args = Namespace(account="qmt", query="Budget", sender=None, since=None, max=10, json=True)
        with patch.object(cli, "access_token", return_value="token"), patch.object(cli, "api_request", side_effect=api):
            rows = self.output(cli.command_email_threads, args)
        self.assertEqual(rows[0]["id"], "t1")
        self.assertEqual(rows[0]["type"], "email")
        self.assertEqual(rows[0]["message_count"], 2)
        self.assertEqual(rows[0]["snippet"], "Latest")

    def test_read_message_and_thread_are_independent(self):
        msg = message("m1", "t1", "Budget", "Full")
        for args, expected_path in (
            (Namespace(account="qmt", message_id="m1", thread_id=None, legacy_id=None, max=None, json=True), "/messages/m1"),
            (Namespace(account="qmt", message_id=None, thread_id="t1", legacy_id=None, max=None, json=True), "/threads/t1"),
        ):
            calls = []
            def api(_token, _method, path, query=None):
                calls.append(path)
                return {"id": "t1", "messages": [msg]} if path.startswith("/threads/") else msg
            with patch.object(cli, "access_token", return_value="token"), patch.object(cli, "api_request", side_effect=api):
                rows = self.output(cli.command_email_read, args)
            self.assertEqual(calls[0], expected_path)
            self.assertEqual(rows[0]["id"], "m1")
            self.assertEqual(rows[0]["thread"], "t1")

    def test_thread_read_is_complete_by_default(self):
        msgs = [message(f"m{i}", "t1", "Budget", str(i), when=str(1760000000000 + i))
                for i in range(25)]
        args = Namespace(account="qmt", message_id=None, thread_id="t1",
                         legacy_id=None, max=None, json=True)
        with patch.object(cli, "access_token", return_value="token"), \
             patch.object(cli, "api_request", return_value={"id": "t1", "messages": msgs}):
            rows = self.output(cli.command_email_read, args)
        self.assertEqual(len(rows), 25)

    def test_search_can_filter_inside_thread(self):
        msgs = [message("m1", "t1", "Budget", "Alpha"),
                message("m2", "t1", "Other", "Beta", sender="bob@example.org")]
        args = Namespace(account="qmt", thread_id="t1", query="Budget", sender="alice",
                         since=None, max=10, json=True, native=False)
        with patch.object(cli, "access_token", return_value="token"), \
             patch.object(cli, "api_request", return_value={"id": "t1", "messages": msgs}):
            rows = self.output(cli.command_email_search, args)
        self.assertEqual([row["id"] for row in rows], ["m1"])

    def test_parser_exposes_new_contract(self):
        parser = cli.build_parser()
        self.assertEqual(parser.parse_args(["read", "--message", "m1"]).message_id, "m1")
        self.assertEqual(parser.parse_args(["read", "--thread", "t1"]).thread_id, "t1")
        self.assertEqual(parser.parse_args(["search", "x", "--thread", "t1"]).thread_id, "t1")
        self.assertEqual(parser.parse_args(["threads", "budget"]).email_command, "threads")

    def test_native_search_is_explicit(self):
        common = Namespace(query="from:alice", sender=None, since=None, native=False)
        native = Namespace(query="from:alice", sender=None, since=None, native=True)
        self.assertEqual(cli._search_query(common), '"from:alice"')
        self.assertEqual(cli._search_query(native), "from:alice")


if __name__ == "__main__":
    unittest.main()
