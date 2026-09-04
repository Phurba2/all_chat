import json
from unittest import mock

from django.test import TestCase

from . import messenger
from .models import Message


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


class MessengerWebhookTests(TestCase):
    URL = "/messenger/webhook/"

    def test_get_verification_with_wrong_token(self):
        resp = self.client.get(self.URL, {
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "challenge-123",
        })
        self.assertEqual(resp.status_code, 403)

    def test_get_verification_with_correct_token(self):
        with mock.patch("inbox.messenger.VERIFY_TOKEN", "secret-token"):
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
        resp = self.client.post(self.URL, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        msg = Message.objects.get()
        self.assertEqual(msg.channel, "messenger")
        self.assertEqual(msg.contact, "psid-1")
        self.assertEqual(msg.direction, "in")
        self.assertEqual(msg.text, "Hello from Messenger")
        self.assertEqual(msg.message_id, "mid.abc")
        self.assertEqual(msg.user_email, "")
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
        resp = self.client.post(self.URL, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(Message.objects.count(), 0)

    def test_post_skips_duplicate_message_id(self):
        Message.objects.create(channel="messenger", contact="psid-1", direction="in",
                               text="Hello", message_id="mid.abc", user_email="")
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
        self.client.post(self.URL, data=json.dumps(payload), content_type="application/json")

        self.assertEqual(Message.objects.count(), 1)

    def test_post_invalid_json(self):
        resp = self.client.post(self.URL, data="not json", content_type="application/json")
        self.assertEqual(resp.status_code, 400)


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
        self.assertEqual(msg.user_email, "")
        self.assertEqual(msg.summary, "")

        calls = mock_get.call_args_list
        self.assertEqual(calls[0].args[0], "https://graph.facebook.com/v21.0/123/conversations")
        self.assertEqual(calls[1].args[0], "https://graph.facebook.com/v21.0/t_conv1/messages")
        for call in calls:
            self.assertEqual(call.kwargs["headers"]["Authorization"], "Bearer tok")

    @mock.patch("inbox.messenger.requests.get")
    def test_fetch_skips_outbound_duplicates_and_attachments(self, mock_get):
        Message.objects.create(channel="messenger", contact="psid-9", direction="in",
                               text="already here", message_id="mid.x", user_email="")
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


class MessengerConversationTests(TestCase):

    def setUp(self):
        self.msg = Message.objects.create(channel="messenger", contact="psid-1", direction="in",
                                          text="Hello", user_email="")

    @mock.patch("inbox.views.messenger.is_configured", return_value=True)
    @mock.patch("inbox.views.messenger.send_message")
    def test_reply_sends_via_send_api_and_stores_outgoing(self, mock_send, _configured):
        resp = self.client.post("/channel/messenger/psid-1/", {"text": "Hello back"})

        self.assertRedirects(resp, "/channel/messenger/psid-1/")
        mock_send.assert_called_once_with("psid-1", "Hello back")
        out = Message.objects.filter(direction="out").get()
        self.assertEqual(out.channel, "messenger")
        self.assertEqual(out.contact, "psid-1")
        self.assertEqual(out.text, "Hello back")
        self.assertEqual(out.user_email, "")  # server-level, not tied to a Gmail session

    @mock.patch("inbox.views.messenger.is_configured", return_value=True)
    @mock.patch("inbox.views.messenger.send_message", side_effect=RuntimeError("API down"))
    def test_reply_stored_locally_when_send_api_fails(self, mock_send, _configured):
        resp = self.client.post("/channel/messenger/psid-1/", {"text": "Hello back"})

        self.assertRedirects(resp, "/channel/messenger/psid-1/")
        self.assertEqual(Message.objects.filter(direction="out").count(), 1)

    def test_messenger_thread_visible_without_gmail_session(self):
        resp = self.client.get("/channel/messenger/psid-1/")
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello")
