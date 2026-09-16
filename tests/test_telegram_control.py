import unittest
from unittest.mock import patch

from telegram_control import handle_callback


class TelegramControlTests(unittest.TestCase):
    def query(self, chat_id: str) -> dict:
        return {
            "id": "callback-1",
            "data": "restart_all",
            "message": {"chat": {"id": chat_id}},
        }

    @patch("telegram_control.send_telegram_message")
    @patch("telegram_control.answer_callback")
    @patch("telegram_control.restart_local_services", return_value=True)
    @patch("telegram_control.config.TELEGRAM_CHAT_ID", "1234")
    def test_authorized_chat_can_restart(self, restart, answer, send):
        handle_callback(self.query("1234"))
        restart.assert_called_once_with()
        answer.assert_called_once_with("callback-1", "Relance demandée.")
        send.assert_called_once()

    @patch("telegram_control.answer_callback")
    @patch("telegram_control.restart_local_services")
    @patch("telegram_control.config.TELEGRAM_CHAT_ID", "1234")
    def test_another_chat_is_rejected(self, restart, answer):
        handle_callback(self.query("9999"))
        restart.assert_not_called()
        answer.assert_called_once_with("callback-1", "Commande non autorisée.", show_alert=True)


if __name__ == "__main__":
    unittest.main()
