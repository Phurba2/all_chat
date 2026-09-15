import hashlib
import hmac
import json
import shutil
import tempfile
from datetime import datetime, timezone as dt_timezone
from pathlib import Path
from unittest import mock

from django.test import TestCase

from . import gmail, messenger, whatsapp
from .models import Message


class ServerConfigSandboxMixin:
    def setUp(self):
        super().setUp()
        self._config_tmpdir = Path(tempfile.mkdtemp(prefix="msgr-cfg-"))
        self._real_config_path = messenger.CONFIG_PATH
        self._real_whatsapp_config_path = whatsapp.CONFIG_PATH
        messenger.CONFIG_PATH = self._config_tmpdir / "config.json"
        whatsapp.CONFIG_PATH = self._config_tmpdir / "whatsapp-config.json"

    def tearDown(self):
        messenger.CONFIG_PATH = self._real_config_path
        whatsapp.CONFIG_PATH = self._real_whatsapp_config_path
        shutil.rmtree(self._config_tmpdir, ignore_errors=True)
        super().tearDown()


class MessengerApiTests(ServerConfigSandboxMixin, TestCase):

    @mock.patch("inbox.messenger.requests.post")
    def test_send_message_posts_to_graph_api(self, mock_post):
        mock_post.return_value.json.return_value = {"message_id": "mid.1"}
        with mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"):
            result = messenger.send_message("psid-1", "Hello")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://graph.facebook.com/v26.0/123/messages")
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

    def test_send_message_prefers_server_config_file(self):
        messenger.save_server_config(page_id="cfg-page", access_token="cfg-tok")
        with mock.patch("inbox.messenger.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {"message_id": "mid.1"}
            messenger.send_message("psid-1", "Hello")

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://graph.facebook.com/v26.0/cfg-page/messages")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer cfg-tok")

    def test_is_configured_false_without_config(self):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None):
            self.assertFalse(messenger.is_configured())


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
        with mock.patch("inbox.messenger.APP_SECRET", None), \
             mock.patch.object(messenger, "fetch_user_name", return_value="psid-1"):
            resp = self.client.post(self.URL, data=json.dumps(payload),
                                    content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        msg = Message.objects.get()
        self.assertEqual(msg.channel, "messenger")
        self.assertEqual(msg.contact, "psid-1")
        self.assertEqual(msg.direction, "in")
        self.assertEqual(msg.text, "Hello from Messenger")
        self.assertEqual(msg.message_id, "mid.abc")
        self.assertEqual(msg.user_email, "123")
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
        with mock.patch.object(messenger, "fetch_user_name", return_value="psid-1"):
            resp = self.client.post(self.URL, data=payload, content_type="application/json",
                                    HTTP_X_HUB_SIGNATURE_256=expected)
        self.assertEqual(resp.status_code, 200)
        msg = Message.objects.filter(direction="in").get()
        self.assertEqual(msg.text, "signed hello")
        self.assertEqual(msg.user_email, "123")


class MessengerConfigFileTests(ServerConfigSandboxMixin, TestCase):

    def test_save_server_config_persists_values(self):
        messenger.save_server_config(app_secret="secret-1", verify_token="vt-1")
        self.assertTrue(self._config_tmpdir.joinpath("config.json").exists())
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


class MessengerFetchTests(ServerConfigSandboxMixin, TestCase):

    @staticmethod
    def _mock_response(payload):
        resp = mock.Mock()
        resp.json.return_value = payload
        return resp

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_messages_backfills_inbound(self, mock_get):
        mock_get.side_effect = [
            self._mock_response({"data": [{"id": "t_conv1", "participants": {"data": [
                {"id": "psid-9", "name": "Pat"},
            ]}}], "paging": {}}),
            self._mock_response({"data": [{"id": "mid.x", "message": "Hi there",
                                            "from": {"id": "psid-9"}}], "paging": {}}),
        ]
        with mock.patch("inbox.messenger.GRAPH_VERSION", "v26.0"), \
             mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"), \
             mock.patch.object(messenger, "fetch_user_name", return_value="Pat"):
            result = messenger.fetch_messages()

        self.assertEqual(result, 1)
        msg = Message.objects.get()
        self.assertEqual(msg.channel, "messenger")
        self.assertEqual(msg.contact, "Pat")
        self.assertEqual(msg.direction, "in")
        self.assertEqual(msg.text, "Hi there")
        self.assertEqual(msg.message_id, "mid.x")
        self.assertEqual(msg.user_email, "123")
        self.assertEqual(msg.summary, "")

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_skips_outbound_duplicates_and_attachments(self, mock_get):
        Message.objects.create(channel="messenger", contact="Pat", direction="in",
                               text="already here", message_id="mid.x", user_email="123")
        mock_get.side_effect = [
            self._mock_response({"data": [
                {"id": "t_conv1", "participants": {"data": [{"id": "psid-9", "name": "Pat"}]}},
                {"id": "t_conv2", "participants": {"data": [{"id": "psid-9", "name": "Pat"}]}},
            ], "paging": {}}),
            self._mock_response({"data": [
                {"id": "mid.1", "message": "Our reply", "from": {"id": "123"}},
                {"id": "mid.x", "message": "already here", "from": {"id": "psid-9"}},
                {"id": "mid.2", "attachments": [{"type": "image"}]},
            ], "paging": {}}),
            self._mock_response({"data": [{"id": "mid.new", "message": "New one",
                                            "from": {"id": "psid-9"}}], "paging": {}}),
        ]
        with mock.patch("inbox.messenger.GRAPH_VERSION", "v26.0"), \
             mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"), \
             mock.patch.object(messenger, "fetch_user_name", return_value="Pat"):
            result = messenger.fetch_messages()

        self.assertEqual(result, 1)
        self.assertEqual(Message.objects.count(), 2)
        self.assertTrue(Message.objects.filter(message_id="mid.new").exists())

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_stores_outbound_messages_when_requested(self, mock_get):
        mock_get.side_effect = [
            self._mock_response({"data": [
                {"id": "t_conv1", "participants": {"data": [{"id": "psid-9", "name": "Pat"}]}},
            ], "paging": {}}),
            self._mock_response({"data": [
                {"id": "mid.out1", "message": "Outbound reply", "from": {"id": "123"},
                 "to": {"data": [{"id": "psid-9", "name": "Pat"}]}},
            ], "paging": {}}),
        ]
        with mock.patch("inbox.messenger.GRAPH_VERSION", "v26.0"), \
             mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"), \
             mock.patch.object(messenger, "fetch_user_name", return_value="Pat"):
            result = messenger.fetch_messages(include_outbound=True)

        self.assertEqual(result, 1)
        msg = Message.objects.get(message_id="mid.out1")
        self.assertEqual(msg.direction, "out")
        self.assertEqual(msg.contact, "Pat")
        self.assertEqual(msg.contact_id, "psid-9")
        self.assertTrue(msg.is_read)

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_follows_cursor_pagination(self, mock_get):
        mock_get.side_effect = [
            self._mock_response({"data": [{"id": "t_1", "participants": {"data": [
                {"id": "psid-1", "name": "Ada"},
            ]}}],
                                 "paging": {"next": "https://graph.facebook.com/v26.0/123/conversations?after=cursor2"}}),
            self._mock_response({"data": [{"id": "t_2", "participants": {"data": [
                {"id": "psid-2", "name": "Bob"},
            ]}}], "paging": {}}),
            self._mock_response({"data": [{"id": "mid.a", "message": "from t1",
                                            "from": {"id": "psid-1"}}], "paging": {}}),
            self._mock_response({"data": [{"id": "mid.b", "message": "from t2",
                                            "from": {"id": "psid-2"}}], "paging": {}}),
        ]
        with mock.patch("inbox.messenger.GRAPH_VERSION", "v26.0"), \
             mock.patch("inbox.messenger.PAGE_ID", "123"), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", "tok"), \
             mock.patch.object(messenger, "fetch_user_name", return_value="Ada"):
            result = messenger.fetch_messages()

        self.assertEqual(result, 2)
        self.assertEqual(mock_get.call_args_list[1].args[0],
                         "https://graph.facebook.com/v26.0/123/conversations?after=cursor2")

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_raises_when_not_configured(self, mock_get):
        with mock.patch("inbox.messenger.PAGE_ID", None), \
             mock.patch("inbox.messenger.PAGE_ACCESS_TOKEN", None), \
             mock.patch("inbox.messenger.GRAPH_VERSION", ""):
            with self.assertRaises(RuntimeError):
                messenger.fetch_messages()
        mock_get.assert_not_called()


class ConversationViewTests(TestCase):

    def test_inbox_shows_conversations(self):
        Message.objects.create(channel="messenger", contact="Pat", direction="in",
                               text="Hello", user_email="123")
        resp = self.client.get("/channel/messenger/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Pat")

    def test_thread_not_found_returns_404(self):
        resp = self.client.get("/channel/messenger/nobody/")
        self.assertEqual(resp.status_code, 404)

    @mock.patch("inbox.views.messenger.send_message")
    def test_reply_sends_and_stores_outgoing(self, mock_send):
        Message.objects.create(channel="messenger", contact="psid-1", direction="in",
                               text="Hello", contact_id="psid-1", user_email="123")
        mock_send.return_value = {"message_id": "mid.sent"}

        resp = self.client.post("/channel/messenger/psid-1/", {"text": "Hello back"})

        self.assertRedirects(resp, "/channel/messenger/psid-1/")
        mock_send.assert_called_once_with("psid-1", "Hello back")
        out = Message.objects.filter(direction="out").get()
        self.assertEqual(out.text, "Hello back")
        self.assertEqual(out.message_id, "mid.sent")

    @mock.patch("inbox.views.messenger.send_message", side_effect=RuntimeError("API down"))
    def test_reply_stored_locally_when_send_fails(self, mock_send):
        Message.objects.create(channel="messenger", contact="psid-1", direction="in",
                               text="Hello", contact_id="psid-1", user_email="123")

        resp = self.client.post("/channel/messenger/psid-1/", {"text": "Hello back"})

        self.assertRedirects(resp, "/channel/messenger/psid-1/")
        self.assertEqual(Message.objects.filter(direction="out").count(), 1)

    def test_thread_marks_messages_read_on_open(self):
        Message.objects.create(channel="messenger", contact="psid-1", direction="in",
                               text="Hello", is_read=False, user_email="123")
        self.client.get("/channel/messenger/psid-1/")
        self.assertTrue(Message.objects.get().is_read)


class GmailFetchTests(TestCase):

    @mock.patch("inbox.gmail.Message.objects.create")
    @mock.patch("inbox.gmail.summarize_message", return_value="s")
    @mock.patch("inbox.gmail.imaplib.IMAP4_SSL")
    def test_fetch_emails_stores_messages(self, mock_imap_cls, mock_sum, mock_create):
        mock_msg = mock.Mock()
        mock_msg.get.side_effect = lambda k, d="": {
            "Message-ID": "<m1@x>", "From": "Pat <pat@x>", "Subject": "Hi"}.get(k, d)
        mock_msg.is_multipart.return_value = False
        mock_msg.get_payload.return_value = b"Body"
        mock_msg.get_content_charset.return_value = "utf-8"

        mail = mock_imap_cls.return_value
        mail.search.return_value = (None, [b"1"])
        mail.fetch.return_value = (None, [(None, b"raw")])

        with mock.patch.dict("inbox.gmail.os.environ",
                             {"GMAIL_EMAIL": "me@x", "GMAIL_APP_PASSWORD": "pw"}), \
             mock.patch("inbox.gmail.message_from_bytes", return_value=mock_msg):
            new = gmail.fetch_emails()

        self.assertEqual(new, 1)
        kwargs = mock_create.call_args.kwargs
        self.assertEqual(kwargs["channel"], "email")
        self.assertEqual(kwargs["contact"], "pat@x")
        self.assertEqual(kwargs["user_email"], "me@x")

    def test_fetch_emails_raises_when_not_configured(self):
        with mock.patch.dict("inbox.gmail.os.environ", {}, clear=True):
            with self.assertRaises(RuntimeError):
                gmail.fetch_emails()

    @mock.patch("inbox.gmail.smtplib.SMTP_SSL")
    def test_send_reply_uses_smtp(self, mock_smtp_cls):
        with mock.patch.dict("inbox.gmail.os.environ",
                             {"GMAIL_EMAIL": "me@x", "GMAIL_APP_PASSWORD": "pw"}):
            gmail.send_reply("pat@x", "Hello", in_reply_to="<m1@x>")

        mock_smtp_cls.assert_called_once()
        sent = mock_smtp_cls.return_value.__enter__.return_value.send_message
        sent.assert_called_once()
        msg = sent.call_args.args[0]
        self.assertEqual(msg["To"], "pat@x")
        self.assertEqual(msg["In-Reply-To"], "<m1@x>")


class WhatsAppApiTests(ServerConfigSandboxMixin, TestCase):

    @mock.patch("inbox.whatsapp.requests.post")
    def test_send_message_posts_to_cloud_api(self, mock_post):
        mock_post.return_value.json.return_value = {"messages": [{"id": "wamid.1"}]}
        with mock.patch("inbox.whatsapp.PHONE_NUMBER_ID", "106540352242922"), \
             mock.patch("inbox.whatsapp.ACCESS_TOKEN", "tok"):
            result = whatsapp.send_message("16505551234", "Hello")

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://graph.facebook.com/v26.0/106540352242922/messages")
        self.assertEqual(kwargs["json"], {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": "16505551234",
            "type": "text",
            "text": {"body": "Hello"},
        })
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer tok")
        self.assertEqual(result, {"messages": [{"id": "wamid.1"}]})

    @mock.patch("inbox.whatsapp.requests.post")
    def test_send_message_raises_when_not_configured(self, mock_post):
        with mock.patch("inbox.whatsapp.PHONE_NUMBER_ID", None), \
             mock.patch("inbox.whatsapp.ACCESS_TOKEN", None), \
             mock.patch("inbox.whatsapp.GRAPH_VERSION", ""):
            with self.assertRaises(RuntimeError):
                whatsapp.send_message("16505551234", "Hello")
        mock_post.assert_not_called()

    def test_send_message_prefers_server_config_file(self):
        whatsapp.save_server_config(phone_number_id="cfg-phone", access_token="cfg-tok")
        with mock.patch("inbox.whatsapp.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {"messages": [{"id": "wamid.1"}]}
            whatsapp.send_message("16505551234", "Hello")

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], "https://graph.facebook.com/v26.0/cfg-phone/messages")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer cfg-tok")

    def test_is_configured_false_without_config(self):
        with mock.patch("inbox.whatsapp.PHONE_NUMBER_ID", None), \
             mock.patch("inbox.whatsapp.ACCESS_TOKEN", None):
            self.assertFalse(whatsapp.is_configured())


class WhatsAppWebhookTests(ServerConfigSandboxMixin, TestCase):
    URL = "/whatsapp/webhook/"

    def setUp(self):
        super().setUp()
        patcher = mock.patch("inbox.views.whatsapp.send_message")
        self.mock_send = patcher.start()
        self.addCleanup(patcher.stop)

    def test_get_verification_with_wrong_token(self):
        with mock.patch("inbox.whatsapp.APP_SECRET", None), \
             mock.patch("inbox.whatsapp.VERIFY_TOKEN", "env-verify"):
            resp = self.client.get(self.URL, {
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge-123",
            })
        self.assertEqual(resp.status_code, 403)

    def test_get_verification_with_correct_token(self):
        with mock.patch("inbox.whatsapp.APP_SECRET", None), \
             mock.patch("inbox.whatsapp.VERIFY_TOKEN", "secret-token"):
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
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "102290129340398",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "metadata": {
                            "display_phone_number": "15550783881",
                            "phone_number_id": "106540352242922",
                        },
                        "contacts": [{
                            "profile": {"name": "Sheena Nelson"},
                            "wa_id": "16505551234",
                        }],
                        "messages": [{
                            "from": "16505551234",
                            "id": "wamid.abc",
                            "timestamp": "1749416383",
                            "type": "text",
                            "text": {"body": "Does it come in another color?"},
                        }],
                    },
                }],
            }],
        }
        with mock.patch("inbox.whatsapp.APP_SECRET", None):
            resp = self.client.post(self.URL, data=json.dumps(payload),
                                    content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        msg = Message.objects.filter(direction="in").get()
        self.assertEqual(msg.channel, "whatsapp")
        self.assertEqual(msg.contact, "Sheena Nelson")
        self.assertEqual(msg.contact_id, "16505551234")
        self.assertEqual(msg.direction, "in")
        self.assertEqual(msg.text, "Does it come in another color?")
        self.assertEqual(msg.message_id, "wamid.abc")
        self.assertEqual(msg.user_email, "106540352242922")
        self.assertEqual(
            msg.created_at,
            datetime(2025, 6, 8, 20, 59, 43, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(msg.summary, "")

    def test_post_ignores_statuses_and_non_text_messages(self):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "102290129340398",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "106540352242922"},
                        "statuses": [{
                            "id": "wamid.status",
                            "status": "delivered",
                            "recipient_id": "16505551234",
                        }],
                        "messages": [{
                            "from": "16505551234",
                            "id": "wamid.img",
                            "timestamp": "1749416383",
                            "type": "image",
                            "image": {"id": "asset-1"},
                        }],
                    },
                }],
            }],
        }
        with mock.patch("inbox.whatsapp.APP_SECRET", None):
            resp = self.client.post(self.URL, data=json.dumps(payload),
                                    content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Message.objects.count(), 0)

    def test_post_skips_duplicate_message_id(self):
        Message.objects.create(channel="whatsapp", contact="Sheena", direction="in",
                               text="Hello", message_id="wamid.abc",
                               user_email="106540352242922")
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "102290129340398",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "106540352242922"},
                        "messages": [{
                            "from": "16505551234",
                            "id": "wamid.abc",
                            "timestamp": "1749416383",
                            "type": "text",
                            "text": {"body": "Hello"},
                        }],
                    },
                }],
            }],
        }
        with mock.patch("inbox.whatsapp.APP_SECRET", None):
            self.client.post(self.URL, data=json.dumps(payload),
                             content_type="application/json")

        self.assertEqual(Message.objects.count(), 1)

    def test_post_invalid_json(self):
        with mock.patch("inbox.whatsapp.APP_SECRET", None):
            resp = self.client.post(self.URL, data="not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)


class WhatsAppWebhookServerConfigTests(ServerConfigSandboxMixin, TestCase):
    URL = "/whatsapp/webhook/"

    def setUp(self):
        super().setUp()
        whatsapp.save_server_config(app_secret="cfg-secret", verify_token="cfg-verify")
        patcher = mock.patch("inbox.views.whatsapp.send_message")
        self.mock_send = patcher.start()
        self.addCleanup(patcher.stop)

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

    def test_post_with_valid_signature_processes_payload(self):
        payload = json.dumps({
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "102290129340398",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "106540352242922"},
                        "messages": [{
                            "from": "16505551234",
                            "id": "wamid.sig",
                            "timestamp": "1749416383",
                            "type": "text",
                            "text": {"body": "signed hello"},
                        }],
                    },
                }],
            }],
        })
        expected = "sha256=" + hmac.new(
            b"cfg-secret", payload.encode(), hashlib.sha256
        ).hexdigest()
        resp = self.client.post(self.URL, data=payload, content_type="application/json",
                                HTTP_X_HUB_SIGNATURE_256=expected)
        self.assertEqual(resp.status_code, 200)
        msg = Message.objects.filter(direction="in").get()
        self.assertEqual(msg.text, "signed hello")
        self.assertEqual(msg.user_email, "106540352242922")


class WhatsAppAutoReplyTests(ServerConfigSandboxMixin, TestCase):
    URL = "/whatsapp/webhook/"

    def setUp(self):
        super().setUp()
        patcher = mock.patch("inbox.whatsapp.APP_SECRET", None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def post_message(self, text, mid="wamid.reply"):
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{
                "id": "102290129340398",
                "changes": [{
                    "field": "messages",
                    "value": {
                        "metadata": {"phone_number_id": "106540352242922"},
                        "contacts": [{"profile": {"name": "Sheena"}, "wa_id": "16505551234"}],
                        "messages": [{
                            "from": "16505551234",
                            "id": mid,
                            "timestamp": "1749416383",
                            "type": "text",
                            "text": {"body": text},
                        }],
                    },
                }],
            }],
        }
        return self.client.post(self.URL, data=json.dumps(payload),
                                content_type="application/json")

    @mock.patch("inbox.views.whatsapp.send_message")
    def test_greeting_gets_auto_reply(self, mock_send):
        mock_send.return_value = {"messages": [{"id": "wamid.out1"}]}
        resp = self.post_message("hi")

        self.assertEqual(resp.status_code, 200)
        mock_send.assert_called_once_with("16505551234", mock.ANY)
        reply_text = mock_send.call_args[0][1]
        self.assertIn("Hello!", reply_text)
        out = Message.objects.filter(direction="out").get()
        self.assertEqual(out.text, reply_text)
        self.assertEqual(out.message_id, "wamid.out1")
        self.assertEqual(out.contact_id, "16505551234")

    @mock.patch("inbox.views.whatsapp.send_message")
    def test_unknown_text_gets_fallback_reply(self, mock_send):
        mock_send.return_value = {}
        self.post_message("what is your refund policy", mid="wamid.x2")

        reply_text = mock_send.call_args[0][1]
        self.assertIn("Thanks for your message", reply_text)
        self.assertTrue(Message.objects.filter(direction="out").exists())

    @mock.patch("inbox.views.whatsapp.send_message")
    def test_reply_failure_does_not_crash_webhook(self, mock_send):
        mock_send.side_effect = RuntimeError("Cloud API error")
        resp = self.post_message("ping", mid="wamid.x3")

        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Message.objects.filter(direction="in").exists())
        self.assertFalse(Message.objects.filter(direction="out").exists())


class WhatsAppConversationViewTests(ServerConfigSandboxMixin, TestCase):

    @mock.patch("inbox.views.whatsapp.send_message")
    def test_reply_sends_and_stores_outgoing(self, mock_send):
        Message.objects.create(channel="whatsapp", contact="Sheena", direction="in",
                               text="Hello", contact_id="16505551234",
                               user_email="106540352242922")
        mock_send.return_value = {"messages": [{"id": "wamid.sent"}]}

        resp = self.client.post("/channel/whatsapp/Sheena/", {"text": "Hello back"})

        self.assertRedirects(resp, "/channel/whatsapp/Sheena/")
        mock_send.assert_called_once_with("16505551234", "Hello back")
        out = Message.objects.filter(direction="out").get()
        self.assertEqual(out.text, "Hello back")
        self.assertEqual(out.message_id, "wamid.sent")
        self.assertEqual(out.contact_id, "16505551234")

    @mock.patch("inbox.views.whatsapp.send_message", side_effect=RuntimeError("API down"))
    def test_reply_stored_locally_when_send_fails(self, mock_send):
        Message.objects.create(channel="whatsapp", contact="Sheena", direction="in",
                               text="Hello", contact_id="16505551234",
                               user_email="106540352242922")

        resp = self.client.post("/channel/whatsapp/Sheena/", {"text": "Hello back"})

        self.assertRedirects(resp, "/channel/whatsapp/Sheena/")
        self.assertEqual(Message.objects.filter(direction="out").count(), 1)

    def test_thread_marks_messages_read_on_open(self):
        Message.objects.create(channel="whatsapp", contact="Sheena", direction="in",
                               text="Hello", is_read=False, user_email="106540352242922")
        self.client.get("/channel/whatsapp/Sheena/")
        self.assertTrue(Message.objects.get().is_read)
