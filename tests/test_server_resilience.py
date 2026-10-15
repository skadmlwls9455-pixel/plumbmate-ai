"""Regression cases adapted from Fable PR #1 (d45a4d0), with local lifecycle checks."""

import errno
import socket
import threading
import time
import unittest
from unittest import mock

from plumbmate.event_socket import BridgeClient, EventSocketServer, SocketBridgeError

TOKEN = "a" * 64


class AcceptorResilienceTests(unittest.TestCase):
    """The accept loop must survive transient failures instead of dying while
    the server still looks healthy."""

    def test_transient_accept_error_does_not_kill_the_acceptor(self):
        server = EventSocketServer(TOKEN, handshake_timeout=30.0, idle_timeout=60.0)
        server.start()
        self.addCleanup(server.stop)
        real_listener = server._listener

        class FlakyListener:
            def __init__(self):
                self.failures = 2

            def accept(self):
                if self.failures:
                    self.failures -= 1
                    raise OSError(errno.ECONNABORTED, "aborted in the backlog")
                return real_listener.accept()

            def __getattr__(self, name):
                return getattr(real_listener, name)

        server._listener = FlakyListener()
        deadline = time.monotonic() + 15.0
        while server.stats.acceptor_errors < 2 and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertGreaterEqual(server.stats.acceptor_errors, 2)
        server._listener = real_listener
        with BridgeClient(TOKEN, server.port, timeout=30.0) as client:
            self.assertEqual(client.send_event("heartbeat")["kind"], "ack")

    def test_worker_spawn_failure_releases_the_slot_and_keeps_accepting(self):
        server = EventSocketServer(
            TOKEN, max_connections=1, handshake_timeout=30.0, idle_timeout=60.0,
        )
        server.start()
        self.addCleanup(server.stop)

        real_thread = threading.Thread

        class ExplodingThread:
            def __init__(self, *args, **kwargs):
                self._name = kwargs.get("name", "")
                self._inner = real_thread(*args, **kwargs)

            def start(self):
                if self._name == "plumbmate-bridge":
                    raise RuntimeError("can't start new thread")
                self._inner.start()

            def __getattr__(self, name):
                return getattr(self._inner, name)

        threading.Thread = ExplodingThread
        try:
            doomed = socket.create_connection(server.address, 10.0)
            doomed.settimeout(3.0)
            try:
                # The server closes this connection without a challenge.
                self.assertEqual(doomed.recv(4096), b"")
            except OSError:
                pass
            finally:
                doomed.close()
        finally:
            threading.Thread = real_thread

        # With max_connections=1, this succeeds only if the failed spawn
        # released its slot and the acceptor thread survived.
        with BridgeClient(TOKEN, server.port, timeout=30.0) as client:
            self.assertEqual(client.send_event("heartbeat")["kind"], "ack")

    def test_byte_trickling_peer_cannot_stretch_the_handshake(self):
        server = EventSocketServer(
            TOKEN, max_connections=1, handshake_timeout=1.0, idle_timeout=60.0,
        )
        server.start()
        self.addCleanup(server.stop)
        trickler = socket.create_connection(server.address, 10.0)
        self.addCleanup(trickler.close)
        trickler.settimeout(0.1)
        buffer = bytearray()
        challenge_deadline = time.monotonic() + 10.0
        while b"\n" not in buffer and time.monotonic() < challenge_deadline:
            try:
                buffer.extend(trickler.recv(4096))
            except socket.timeout:
                continue
        self.assertIn(b"challenge", bytes(buffer))

        started = time.monotonic()
        closed_at = None
        while time.monotonic() - started < 6.0:
            try:
                trickler.sendall(b"x")
                chunk = trickler.recv(64)
                if chunk == b"":
                    closed_at = time.monotonic()
                    break
            except socket.timeout:
                continue
            except OSError:
                closed_at = time.monotonic()
                break
            time.sleep(0.25)
        self.assertIsNotNone(closed_at, "server never enforced the handshake deadline")
        self.assertLess(closed_at - started, 4.0)
        # And the slot must be free again for the real adapter.
        with BridgeClient(TOKEN, server.port, timeout=30.0) as client:
            self.assertEqual(client.send_event("heartbeat")["kind"], "ack")


class RestartTests(unittest.TestCase):
    def test_same_server_can_restart_with_connected_client(self):
        server = EventSocketServer(TOKEN, handshake_timeout=1.0)
        self.addCleanup(server.stop)
        for _ in range(3):
            server.start()
            with BridgeClient(TOKEN, server.port, timeout=2.0) as client:
                self.assertEqual(client.send_event()["kind"], "ack")
                server.stop()
            self.assertFalse(any(worker.is_alive() for worker in server._workers))
            self.assertEqual(len(server._connections), 0)

    def test_restart_refuses_to_mix_live_previous_workers(self):
        server = EventSocketServer(TOKEN)
        server._workers = [mock.Mock(is_alive=lambda: True)]
        with mock.patch("plumbmate.event_socket.socket.socket") as factory:
            with self.assertRaises(SocketBridgeError):
                server.start()
            factory.assert_not_called()

    def test_acceptor_spawn_failure_closes_listener(self):
        server = EventSocketServer(TOKEN)
        with mock.patch("plumbmate.event_socket.threading.Thread", side_effect=RuntimeError("spawn failed")):
            with self.assertRaises(SocketBridgeError):
                server.start()
        self.assertIsNone(server._listener)

    def test_stop_race_closes_late_accept_without_worker(self):
        server = EventSocketServer(TOKEN)
        connection = mock.Mock()
        listener = mock.Mock()
        def accept():
            server._stopping.set()
            return connection, ("127.0.0.1", 123)
        listener.accept.side_effect = accept
        server._listener = listener
        with mock.patch("plumbmate.event_socket.threading.Thread") as factory:
            server._accept_loop()
        connection.close.assert_called_once()
        factory.assert_not_called()
