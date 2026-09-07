import hashlib
import hmac
import json
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase

from . import messenger
from .models import Message


class ServerConfigSandboxMixin:
    """Point messenger.CONFIG_PATH at a temp file so tests don't touch the repo's.

    setUp creates the sandbox; tearDown restores the real path.
    """

    def setUp(self):
        super().setUp()
        self._config_tmpdir = Path(tempfile.mkdtemp(prefix="msgr-cfg-"))
        self._real_config_path = messenger.CONFIG_PATH
        messenger.CONFIG_PATH = self._config_tmpdir / "config.json"

    def tearDown(self):
        messenger.CONFIG_PATH = self._real_config_path
        shutil.rmtree(self._config_tmpdir, ignore_errors=True)
        super().tearDown()

    @staticmethod
    def _sandbox_config_path():
        return messenger.CONFIG_PATH


class MessengerApiTests(TestCase):

    @mock.patch("inbox.messenger.requests.post")
    def test_send_message_posts_to_graph_api(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"message_id": "mid.1"}
        with mock.patch("inbox.messenger.GRAPH_VERSION", "v21.0"), \
             mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"):
            result = messenger.send_message("psid-1", "Hello")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://graph.facebook.com/v21.0/123/messages")
        self.assertEqual(kwargs["json"], {"recipient": {"id": "psid-1"}, "message": {"text": "Hello"}})
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(result, {"message_id": "mid.1"})

    @mock.patch("inbox.messenger.requests.post")
    def test_send_message_raises_when_not_configured(self, mock_post):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None), \
             mock.patch("inbox.messenger.GRAPH_VERSION", ""):
            with self.assertRaises(RuntimeError):
                messenger.send_message("psid-1", "Hello")
        mock_post.assert_not_called()

    @mock.patch("inbox.messenger.requests.post")
    def test_send_message_uses_session_credentials(self, mock_post):
        mock_post.return_value.raise_for_status.return_value = None
        mock_post.return_value.json.return_value = {"message_id": "mid.1"}
        # No env config — only the credentials passed by the caller (like a session)
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None), \
             mock.patch("inbox.messenger.GRAPH_VERSION", ""):
            result = messenger.send_message("psid-1", "Hello",
                                            page_id="456", access_token="sess-tok",
                                            graph_version="v22.0")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://graph.facebook.com/v22.0/456/messages")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer sess-tok")
        self.assertEqual(result, {"message_id": "mid.1"})

    def test_is_configured_falls_back_to_env(self):
        with mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"), \
             mock.patch("inbox.messenger.GRAPH_VERSION", "v21.0"):
            self.assertTrue(messenger.is_configured())

    def test_is_configured_from_session_values_only(self):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None), \
             mock.patch("inbox.messenger.GRAPH_VERSION", ""):
            self.assertFalse(messenger.is_configured())
            self.assertTrue(messenger.is_configured("456", "sess-tok"))
            self.assertFalse(messenger.is_configured("456", ""))


class MessengerWebhookTests(ServerConfigSandboxMixin, TestCase):
    URL = "/messenger/webhook/"

    def test_get_verification_with_wrong_token(self):
        with mock.patch("inbox.messenger.APP_SECRET", None), \
             mock.patch("inbox.messenger.VERIFY_TOKEN", "env-verify"):
            resp = self.client.get(self.URL, {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge-123",
            })
        self.assertEqual(resp.status_code, 403)

    def test_get_verification_with_correct_token(self):
        with mock.patch("inbox.messenger.APP_SECRET", None), \
             mock.patch("inbox.messenger.VERIFY_TOKEN", "secret-token"):
            resp = self.client.get(self.URL, {
                "hub.mode": "subscribe",
                "hub.verify_token": "secret-token",
                "hub.challenge": "challenge-123",
            })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode(), "challenge-123")

    def test_unsupported_method(self):
        resp = self.client.put(self.URL)
        self.assertEqual(resp.status_code, 405)

    def test_post_saves_incoming_message(self):
        payload = {
            "object": "page",
            "entry": [{
                "id": "123",
                "messaging": [{
                    "sender": {"id": "psid-1"},
                    "recipient": {"id": "123"},
                    "message": {"mid": "mid.abc", "text": "Hello from Messenger"},
                }],
            }],
        }
        # No app secret configured → signature check is skipped (env only)
        with mock.patch("inbox.messenger.APP_SECRET", None):
            resp = self.client.post(self.URL, data=json.dumps(payload),
                                    content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        msg = Message.objects.get()
        self.assertEqual(msg.channel, "messenger")
        self.assertEqual(msg.contact, "psid-1")
        self.assertEqual(msg.direction, "in")
        self.assertEqual(msg.text, "Hello from Messenger")
        self.assertEqual(msg.message_id, "mid.abc")
        self.assertEqual(msg.user_email, "123")  # belongs to the Page from the payload
        self.assertEqual(msg.summary, "")

    def test_post_ignores_echo_and_non_message_events(self):
        payload = {
            "object": "page",
            "entry": [{
                "id": "123",
                "messaging": [
                    {"sender": {"id": "psid-1"}, "recipient": {"id": "123"},
                     "message": {"mid": "mid.1", "is_echo": True, "text": "our own reply"}},
                    {"sender": {"id": "psid-1"}, "recipient": {"id": "123"},
                     "delivery": {"mids": ["mid.1"]}},
                    {"sender": {"id": "psid-1"}, "recipient": {"id": "123"},
                     "postback": {"payload": "GET_STARTED"}},
                    {"sender": {"id": "psid-1"}, "recipient": {"id": "123"},
                     "message": {"mid": "mid.2", "attachments": [{"type": "image"}]}},
                ],
            }],
        }
        with mock.patch("inbox.messenger.APP_SECRET", None):
            resp = self.client.post(self.URL, data=json.dumps(payload),
                                    content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Message.objects.count(), 0)

    def test_post_skips_duplicate_message_id(self):
        Message.objects.create(channel="messenger", contact="psid-1", direction="in",
                               text="Hello", message_id="mid.abc", user_email="123")
        payload = {
            "object": "page",
            "entry": [{
                "id": "123",
                "messaging": [{
                    "sender": {"id": "psid-1"},
                    "recipient": {"id": "123"},
                    "message": {"mid": "mid.abc", "text": "Hello"},
                }],
            }],
        }
        with mock.patch("inbox.messenger.APP_SECRET", None):
            self.client.post(self.URL, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(Message.objects.count(), 1)

    def test_post_invalid_json(self):
        with mock.patch("inbox.messenger.APP_SECRET", None):
            resp = self.client.post(self.URL, data="not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)


class MessengerWebhookServerConfigTests(ServerConfigSandboxMixin, TestCase):
    """Webhook behavior when App Secret / Verify Token were saved from the setup page."""
    URL = "/messenger/webhook/"

    def setUp(self):
        super().setUp()
        messenger.save_server_config(app_secret="cfg-secret", verify_token="cfg-verify")

    def test_get_verification_uses_saved_verify_token(self):
        resp = self.client.get(self.URL, {
            "hub.mode": "subscribe",
            "hub.verify_token": "cfg-verify",
            "hub.challenge": "challenge-123",
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content.decode(), "challenge-123")

    def test_get_verification_rejects_wrong_token(self):
        resp = self.client.get(self.URL, {
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "challenge-123",
        })
        self.assertEqual(resp.status_code, 403)

    def test_post_without_signature_is_rejected(self):
        resp = self.client.post(self.URL, data=json.dumps({"entry": []}),
                                content_type="application/json")
        self.assertEqual(resp.status_code, 403)

    def test_post_with_invalid_signature_is_rejected(self):
        resp = self.client.post(self.URL, data=json.dumps({"entry": []}),
                                content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256="sha256=" + "0" * 64)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(Message.objects.count(), 0)

    def test_post_without_page_id_in_payload_uses_saved_config(self):
        messenger.save_server_config(page_id="cfg-page")
        payload = json.dumps({
            "object": "page",
            "entry": [{
                "messaging": [{
                    "sender": {"id": "psid-1"},
                    "message": {"mid": "mid.nopage", "text": "hi"},
                }],
            }],
        })
        with mock.patch.object(messenger, "verify_payload_signature", return_value=True):
            resp = self.client.post(self.URL, data=payload, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Message.objects.get().user_email, "cfg-page")

    def test_post_with_valid_signature_processes_payload(self):
        payload = json.dumps({
            "object": "page",
            "entry": [{
                "id": "123",
                "messaging": [{
                    "sender": {"id": "psid-1"},
                    "recipient": {"id": "123"},
                    "message": {"mid": "mid.sig", "text": "signed hello"},
                }],
            }],
        })
        expected = "sha256=" + hmac.new(
            b"cfg-secret", payload.encode(), hashlib.sha256
        ).hexdigest()
        resp = self.client.post(self.URL, data=payload, content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=expected)
        self.assertEqual(resp.status_code, 200)
        msg = Message.objects.get()
        self.assertEqual(msg.text, "signed hello")
        self.assertEqual(msg.user_email, "123")


class MessengerConfigFileTests(ServerConfigSandboxMixin, TestCase):
    """Server-level persistence of webhook credentials entered on the setup page."""

    def test_save_server_config_persists_values(self):
        messenger.save_server_config(app_secret="secret-1", verify_token="vt-1")
        self.assertTrue(self._sandbox_config_path().exists())
        self.assertEqual(messenger.webhook_app_secret(), "secret-1")
        self.assertEqual(messenger.webhook_verify_token(), "vt-1")

    def test_saved_config_overrides_env_vars(self):
        messenger.save_server_config(app_secret="secret-1", verify_token="vt-1")
        with mock.patch("inbox.messenger.APP_SECRET", "env-secret"), \
             mock.patch("inbox.messenger.VERIFY_TOKEN", "env-verify"):
            self.assertEqual(messenger.webhook_app_secret(), "secret-1")
            self.assertEqual(messenger.webhook_verify_token(), "vt-1")

    def test_no_config_falls_back_to_env_vars(self):
        with mock.patch("inbox.messenger.APP_SECRET", "env-secret"), \
             mock.patch("inbox.messenger.VERIFY_TOKEN", "env-verify"):
            self.assertEqual(messenger.webhook_app_secret(), "env-secret")
            self.assertEqual(messenger.webhook_verify_token(), "env-verify")

    def test_verify_payload_signature(self):
        messenger.save_server_config(app_secret="secret-1")
        raw = b'{"entry": []}'
        good = "sha256=" + hmac.new(b"secret-1", raw, hashlib.sha256).hexdigest()
        self.assertTrue(messenger.verify_payload_signature(raw, good))
        self.assertFalse(messenger.verify_payload_signature(raw, "sha256=" + "0" * 64))
        self.assertFalse(messenger.verify_payload_signature(raw, None))
        self.assertFalse(messenger.verify_payload_signature(raw, "bogus"))

    def test_verify_payload_signature_passes_when_no_secret_configured(self):
        with mock.patch("inbox.messenger.APP_SECRET", None):
            self.assertTrue(messenger.verify_payload_signature(b'{}', None))


class MessengerFetchTests(TestCase):

    @staticmethod
    def _mock_response(payload):
        resp = mock.Mock()
        resp.raise_for_status.return_value = None
        resp.json.return_value = payload
        return resp

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_messages_backfills_inbound(self, mock_get):
        mock_get.side_effect = [
            self._mock_response({"data": [{"id": "t_conv1"}], "paging": {}}),
            self._mock_response({"data": [{"id": "mid.x", "message": "Hi there",
                                            "from": {"id": "psid-9"}}], "paging": {}}),
        ]
        with mock.patch("inbox.messenger.GRAPH_VERSION", "v21.0"), \
             mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"):
            result = messenger.fetch_messages()

        self.assertEqual(result, 1)
        msg = Message.objects.get()
        self.assertEqual(msg.channel, "messenger")
        self.assertEqual(msg.contact, "psid-9")
        self.assertEqual(msg.direction, "in")
        self.assertEqual(msg.text, "Hi there")
        self.assertEqual(msg.message_id, "mid.x")
        self.assertEqual(msg.user_email, "123")  # belongs to the fetched Page
        self.assertEqual(msg.summary, "")

        calls = mock_get.call_args_list
        self.assertEqual(calls[0].args[0], "https://graph.facebook.com/v21.0/123/conversations")
        self.assertEqual(calls[1].args[0], "https://graph.facebook.com/v21.0/t_conv1/messages")
        for call in calls:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer tok")

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_skips_outbound_duplicates_and_attachments(self, mock_get):
        Message.objects.create(channel="messenger", contact="psid-9", direction="in",
                               text="already here", message_id="mid.x", user_email="123")
        mock_get.side_effect = [
            self._mock_response({"data": [{"id": "t_conv1"}, {"id": "t_conv2"}], "paging": {}}),
            # t_conv1: page's own outbound, a duplicate of a stored message, an attachment-only message
            self._mock_response({"data": [
                {"id": "mid.1", "message": "Our reply", "from": {"id": "123"}},
                {"id": "mid.x", "message": "already here", "from": {"id": "psid-9"}},
                {"id": "mid.2", "attachments": [{"type": "image"}]},
            ], "paging": {}}),
            # t_conv2: one genuinely new inbound message
            self._mock_response({"data": [{"id": "mid.new", "message": "New one",
                                            "from": {"id": "psid-9"}}], "paging": {}}),
        ]
        with mock.patch("inbox.messenger.GRAPH_VERSION", "v21.0"), \
             mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"):
            result = messenger.fetch_messages()

        self.assertEqual(result, 1)  # only mid.new is new
        self.assertEqual(Message.objects.count(), 2)
        self.assertTrue(Message.objects.filter(message_id="mid.new").exists())

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_follows_cursor_pagination(self, mock_get):
        mock_get.side_effect = [
            self._mock_response({"data": [{"id": "t_1"}],
                                 "paging": {"next": "https://graph.facebook.com/v21.0/123/conversations?after=cursor2"}}),
            self._mock_response({"data": [{"id": "t_2"}], "paging": {}}),
            self._mock_response({"data": [{"id": "mid.a", "message": "from t1",
                                            "from": {"id": "psid-1"}}], "paging": {}}),
            self._mock_response({"data": [{"id": "mid.b", "message": "from t2",
                                            "from": {"id": "psid-2"}}], "paging": {}}),
        ]
        with mock.patch("inbox.messenger.GRAPH_VERSION", "v21.0"), \
             mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"):
            result = messenger.fetch_messages()

        self.assertEqual(result, 2)
        # the paging.next URL is fetched as-is (cursor is in the URL)
        self.assertEqual(mock_get.call_args_list[1].args[0],
                         "https://graph.facebook.com/v21.0/123/conversations?after=cursor2")

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_raises_when_not_configured(self, mock_get):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None), \
             mock.patch("inbox.messenger.GRAPH_VERSION", ""):
            with self.assertRaises(RuntimeError):
                messenger.fetch_messages()
        mock_get.assert_not_called()

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_uses_session_credentials(self, mock_get):
        mock_get.side_effect = [
            self._mock_response({"data": [{"id": "t_conv1"}], "paging": {}}),
            self._mock_response({"data": [{"id": "mid.x", "message": "Hi there",
                                            "from": {"id": "psid-9"}}], "paging": {}}),
        ]
        # No env config — only the credentials passed by the caller (like a session)
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None), \
             mock.patch("inbox.messenger.GRAPH_VERSION", ""):
            result = messenger.fetch_messages(page_id="456", access_token="sess-tok",
                                              graph_version="v22.0")

        self.assertEqual(result, 1)
        self.assertEqual(Message.objects.get().user_email, "456")  # belongs to the connected Page
        calls = mock_get.call_args_list
        self.assertEqual(calls[0].args[0], "https://graph.facebook.com/v22.0/456/conversations")
        for call in calls:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer sess-tok")


class MessengerConversationTests(TestCase):
    PAGE_ID = "123"

    def _connect_messenger(self):
        session = self.client.session
        session["META_PAGE_ID"] = self.PAGE_ID
        session["META_PAGE_ACCESS_TOKEN"] = "sess-tok"
        session.save()

    def setUp(self):
        self.msg = Message.objects.create(channel="messenger", contact="psid-1", direction="in",
                                          text="Hello", user_email=self.PAGE_ID)

    @mock.patch("inbox.views.messenger.send_message")
    def test_reply_sends_via_send_api_and_stores_outgoing(self, mock_send):
        self._connect_messenger()
        resp = self.client.post("/channel/messenger/psid-1/", {"text": "Hello back"})

        self.assertRedirects(resp, "/channel/messenger/psid-1/")
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        self.assertEqual(args, ("psid-1", "Hello back"))
        self.assertEqual(kwargs, {"page_id": self.PAGE_ID, "access_token": "sess-tok",
                                  "graph_version": None})
        out = Message.objects.filter(direction="out").get()
        self.assertEqual(out.channel, "messenger")
        self.assertEqual(out.contact, "psid-1")
        self.assertEqual(out.text, "Hello back")
        self.assertEqual(out.user_email, self.PAGE_ID)  # belongs to the connected Page

    @mock.patch("inbox.views.messenger.send_message", side_effect=RuntimeError("API down"))
    def test_reply_stored_locally_when_send_api_fails(self, mock_send):
        self._connect_messenger()
        resp = self.client.post("/channel/messenger/psid-1/", {"text": "Hello back"})

        self.assertRedirects(resp, "/channel/messenger/psid-1/")
        self.assertEqual(Message.objects.filter(direction="out").count(), 1)

    def test_thread_not_visible_without_connected_page(self):
        # No session creds → messenger conversations are not accessible at all
        resp = self.client.get("/channel/messenger/psid-1/")
        self.assertEqual(resp.status_code, 404)

    def test_messenger_thread_visible_with_connected_page_no_gmail(self):
        self._connect_messenger()
        resp = self.client.get("/channel/messenger/psid-1/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello")

    def test_messenger_thread_not_visible_for_other_page(self):
        session = self.client.session
        session["META_PAGE_ID"] = "999"  # connected to a different Page
        session["META_PAGE_ACCESS_TOKEN"] = "sess-tok"
        session.save()
        resp = self.client.get("/channel/messenger/psid-1/")
        self.assertEqual(resp.status_code, 404)


class MessengerSetupPageTests(ServerConfigSandboxMixin, TestCase):
    URL = "/setup/messenger/"

    def test_get_shows_form_when_not_configured(self):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Connect Messenger")
        self.assertContains(resp, "Page Access Token")

    def test_get_redirects_to_messenger_when_session_connected(self):
        session = self.client.session
        session["META_PAGE_ID"] = "456"
        session["META_PAGE_ACCESS_TOKEN"] = "sess-tok"
        session.save()
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None):
            resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/channel/messenger/")

    @mock.patch("inbox.messenger.fetch_messages", return_value=2)
    def test_post_stores_credentials_and_backfills(self, mock_fetch):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None):
            resp = self.client.post(self.URL, {
                "page_id": "456",
                "access_token": "sess-tok",
                "graph_version": "v22.0",
                "app_secret": "secret-1",
                "verify_token": "vt-1",
            })

        self.assertRedirects(resp, "/channel/messenger/")
        mock_fetch.assert_called_once_with(page_id="456", access_token="sess-tok",
                                           graph_version="v22.0")
        session = self.client.session
        self.assertEqual(session["META_PAGE_ID"], "456")
        self.assertEqual(session["META_PAGE_ACCESS_TOKEN"], "sess-tok")
        self.assertEqual(session["META_GRAPH_VERSION"], "v22.0")
        # Webhook credentials were saved server-side, not in the session
        self.assertNotIn("META_APP_SECRET", session)
        self.assertNotIn("META_VERIFY_TOKEN", session)
        self.assertEqual(messenger.webhook_app_secret(), "secret-1")
        self.assertEqual(messenger.webhook_verify_token(), "vt-1")

    @mock.patch("inbox.messenger.fetch_messages", side_effect=RuntimeError("bad token"))
    def test_post_keeps_credentials_when_fetch_fails(self, mock_fetch):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None):
            resp = self.client.post(self.URL, {
                "page_id": "456",
                "access_token": "sess-tok",
                "graph_version": "",
            })

        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/channel/messenger/")
        mock_fetch.assert_called_once_with(page_id="456", access_token="sess-tok",
                                           graph_version="v21.0")  # defaults when blank
        session = self.client.session
        self.assertEqual(session["META_PAGE_ID"], "456")
        self.assertEqual(session["META_PAGE_ACCESS_TOKEN"], "sess-tok")
        self.assertNotIn("META_GRAPH_VERSION", session)
        # setup_message is stored for the redirect target to display
        self.assertTrue(session["setup_message"].startswith("Connected, but couldn't"))

    def test_post_missing_fields_shows_error(self):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None):
            resp = self.client.post(self.URL, {"page_id": "456"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please enter both")
        self.assertNotIn("META_PAGE_ID", self.client.session)

    def test_post_webhook_fields_saved_even_when_page_fields_missing(self):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None):
            resp = self.client.post(self.URL, {
                "app_secret": "secret-1",
                "verify_token": "vt-1",
            })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Please enter both")
        self.assertEqual(messenger.webhook_app_secret(), "secret-1")
        self.assertEqual(messenger.webhook_verify_token(), "vt-1")

    def test_get_form_shows_webhook_fields(self):
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "App Secret")
        self.assertContains(resp, "Verify Token")

    def test_messenger_channel_redirects_to_setup_when_not_connected_in_session(self):
        # Even with env vars configured, Messenger is per-session: a browser that
        # hasn't connected a Page gets sent to the setup page.
        with mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"), \
             mock.patch("inbox.messenger.GRAPH_VERSION", "v21.0"):
            resp = self.client.get("/channel/messenger/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/setup/messenger/")

    def test_messenger_channel_visible_when_session_configured(self):
        session = self.client.session
        session["META_PAGE_ID"] = "456"
        session["META_PAGE_ACCESS_TOKEN"] = "sess-tok"
        session.save()
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None), \
             mock.patch("inbox.messenger.GRAPH_VERSION", ""):
            resp = self.client.get("/channel/messenger/")
        self.assertEqual(resp.status_code, 200)

    def test_messenger_header_shows_page_and_logout(self):
        session = self.client.session
        session["META_PAGE_ID"] = "456"
        session["META_PAGE_ACCESS_TOKEN"] = "sess-tok"
        session.save()
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None), \
             mock.patch("inbox.messenger.GRAPH_VERSION", ""):
            resp = self.client.get("/channel/messenger/")
        self.assertContains(resp, "💬 Page 456")
        self.assertContains(resp, "Logout")

    def test_messenger_logout_clears_session_and_goes_to_messenger_setup(self):
        session = self.client.session
        session["META_PAGE_ID"] = "456"
        session["META_PAGE_ACCESS_TOKEN"] = "sess-tok"
        session.save()
        resp = self.client.get("/logout/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/setup/messenger/")
        self.assertNotIn("META_PAGE_ID", self.client.session)
        self.assertNotIn("META_PAGE_ACCESS_TOKEN", self.client.session)

    def test_messenger_logout_when_gmail_also_logged_in_goes_to_gmail_setup(self):
        session = self.client.session
        session["GMAIL_EMAIL"] = "you@gmail.com"
        session["GMAIL_APP_PASSWORD"] = "app-pass"
        session["META_PAGE_ID"] = "456"
        session["META_PAGE_ACCESS_TOKEN"] = "sess-tok"
        session.save()
        resp = self.client.get("/logout/")
        self.assertEqual(resp.status_code, 302)
        self.assertEqual(resp.url, "/setup/")
        self.assertNotIn("GMAIL_EMAIL", self.client.session)
        self.assertNotIn("META_PAGE_ID", self.client.session)
