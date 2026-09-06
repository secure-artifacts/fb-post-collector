import socket
import unittest

from fb_collector.tray import can_bind, pick_port


class TrayPortTests(unittest.TestCase):
    def test_occupied_port_cannot_be_reused_and_next_port_is_selected(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        occupied_port = listener.getsockname()[1]
        try:
            self.assertFalse(can_bind("127.0.0.1", occupied_port))
            selected_port, existing = pick_port("127.0.0.1", occupied_port, count=2)
            self.assertEqual(selected_port, occupied_port + 1)
            self.assertFalse(existing)
        finally:
            listener.close()


if __name__ == "__main__":
    unittest.main()
