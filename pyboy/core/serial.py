# BGB Link Cable Server
# https://bgb.bircd.org/bgblink.html

import queue
import socket
import struct
import threading
import time

import pyboy

logger = pyboy.logging.get_logger(__name__)

INTR_VBLANK, INTR_LCDC, INTR_TIMER, INTR_SERIAL, INTR_HIGHTOLOW = [1 << x for x in range(5)]
SERIAL_FREQ = 8192 # Hz
CPU_FREQ = 4213440 # Hz

PACKET_FORMAT = "<4BI" # BGB Link Cable Packet Format
PACKET_SIZE_BYTES = 8 # BGB Link Cable Packet Size
BGB_VERSION = (1, 4, 0) # Major, Minor, Patch

async_recv = queue.Queue()


class Serial:
    """Gameboy Link Cable Emulation"""
    def __init__(self, mb, serial_address, serial_bind, serial_interrupt_based):
        self.mb = mb
        self.SC = 0b0 # Serial transfer control
        self.SB = 0b0 # Serial transfer data
        self.connection = None

        self.trans_bits = 0 # Number of bits transferred
        self.cycles_count = 0 # Number of cycles since last transfer
        self.cycles_target = CPU_FREQ // SERIAL_FREQ

        self._handlers = {
            1: self._handle_version,
            101: self._handle_joypad_update,
            104: self._handle_sync1,
            105: self._handle_sync2,
            106: self._handle_sync3,
            108: self._handle_status,
            109: self._handle_want_disconnect
        }
        # Both sides maintain a "timestamp",
        # which is in 2 MiHz clocks (2^21 cycles per second).
        # Each side sends its own local timestamp in packets, and maintains the difference between its own timestamp,
        # and the received timestamp. Timestamps only contain the lowest 31 bits, the highest bit is always 0.
        # Timestamps can wrap over. Timestamps are used so each side can, at the right times, wait for the remote side,
        # for synchronization.
        self._timestamp = 0
        self._ts_cycles = 0
        self._start_time = 0
        self._got_version = False
        self._last_received_timestamp = 0

        self.recv = queue.Queue()

        self.quitting = False

        if not serial_address:
            logger.info("No serial address supplied. Link Cable emulation disabled.")
            return

        if not serial_address.count(".") == 3 and serial_address.count(":") == 1:
            logger.info("Only IP-addresses of the format x.y.z.w:abcd is supported")
            return

        address_ip, address_port = serial_address.split(":")
        address_tuple = (address_ip, int(address_port))

        self.is_master = True

        if serial_bind:
            self.binding_connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.binding_connection.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.binding_connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) # BGB requires this
            logger.info(f"Binding to {serial_address}")
            self.binding_connection.bind(address_tuple)
            self.binding_connection.listen(1)
            self.connection, _ = self.binding_connection.accept()
            logger.info(f"Client has connected!")
        else:
            self.connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            logger.info(f"Connecting to {serial_address}")
            self.connection.connect(address_tuple)
            logger.info(f"Connection successful!")
            self.is_master = False
        # self.connection.setblocking(False)
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1) # BGB requires this
        self.recv_t = threading.Thread(target=lambda: self.recv_thread())
        self.recv_t.daemon = True
        self.recv_t.start()

    def recv_thread(self):
        self.connection.send(struct.pack(  # Send version packet
            PACKET_FORMAT,
            1,  # Version packet
            BGB_VERSION[0],  # Major
            BGB_VERSION[1],  # Minor
            BGB_VERSION[2],  # Patch
            0   # Timestamp
        ))
        while not self.quitting:
            try:
                data = self.connection.recv(PACKET_SIZE_BYTES)
                if not data:
                    print("Connection closed")
                    break
                b1, b2, b3, b4, timestamp = struct.unpack(PACKET_FORMAT, data)
                self._last_received_timestamp = timestamp
                if b1 in self._handlers:
                    response = self._handlers[b1](b2, b3, b4, timestamp)
                    if response:
                        self.connection.send(response)
                else:
                    logger.warning(f"Unknown packet received: {b1}")
            except BlockingIOError:
                pass
            except ConnectionResetError as e:
                print(f"Connection reset by peer: {e}")
                break

    def _handle_version(self, major, minor, patch, timestamp):
        logger.info(f"Connected to BGB version {major}.{minor}.{patch}")
        if (major, minor, patch) != BGB_VERSION:
            logger.error(f"BGB version mismatch! Expected {BGB_VERSION}, got {major}.{minor}.{patch}")
            raise Exception("BGB version mismatch!")
        if not self._got_version: # Only send status packet once
            self._got_version = True
            return self._get_status_packet()
        return None

    def _handle_joypad_update(self, b2, b3, b4, timestamp):
        # Unused for now
        return None

    def _client_data_handler(self, data, timestamp):
        # Handle data received from master and return response

        # Check if transfer is enabled
        # if self.SC & 0x80 == 0:
        #     return None

        send_bit = (self.SB >> 7) & 1

        self.recv.put((data, timestamp))

        return send_bit

    def _handle_sync1(self, data, _control, _b4, timestamp):
        # Data received from master
        # print("sync1", data, _control, _b4, timestamp)
        response = self._client_data_handler(data, timestamp)
        if response is not None:
            return struct.pack(
                PACKET_FORMAT,
                104 if self.is_master else 105,
                response, # Data value
                self.SC, # Control value
                0, # Unused
                self._timestamp
            )
        return None

    def _handle_sync2(self, data, _control, _b4, timestamp):
        # Data received from slave
        # print("sync2", data, _control, _b4, timestamp)

        response = self._client_data_handler(data, timestamp)
        if response is not None:
            return struct.pack(
                PACKET_FORMAT,
                104 if self.is_master else 105,
                response, # Data value
                self.SC, # Control value
                0, # Unused
                self._timestamp
            )
        return None

    def _handle_sync3(self, b2, b3, b4, timestamp):
        # Ack/echo
        # print("sync3", b2, b3, b4)
        return struct.pack(
            PACKET_FORMAT,
            106, # Sync3 packet
            b2,
            b3,
            b4,
            self._timestamp
        )

    def _handle_status(self, b2, b3, b4, timestamp):
        # Status packet
        # TODO: stop logic when client is paused
        print("Received status packet:")
        print("\tRunning:", (b2 & 1) == 1)
        print("\tPaused:", (b2 & 2) == 2)
        print("\tSupports reconnect:", (b2 & 4) == 4)

        # The docs say not to respond to status with status,
        # but not doing this causes link instability
        return self._get_status_packet()

    def _handle_want_disconnect(self, b2, b3, b4, timestamp):
        # Disconnect packet
        print("Received disconnect packet")
        self.connection.close()
        return None

    def _get_status_packet(self):
        # TODO: Include correct state flags in status packet (EG: pyboy.paused)
        return struct.pack(
            PACKET_FORMAT,
            108, # Status packet
            1, # State=running
            0, # State=paused
            0, # State=supportreconnect
            self._timestamp
        )

    def send_bit(self):
        self.connection.send(
            struct.pack(
                PACKET_FORMAT,
                104 if self.is_master else 105, # Sync1 packet
                (self.SB >> 7) & 1, # Data value
                self.SC, # Control value
                0, # Unused
                self._timestamp
            )
        )

    def tick(self, cycles):
        if self.connection is None:
            # No connection, no serial
            self.SB = 0xFF
            return False

        self.cycles_count += cycles

        # Update timestamp every 2 MiHz clocks
        # (2^21 cycles per second)
        # Timestamps only contain the lowest 31 bits, the highest bit is always 0.
        # Timestamps can wrap over. Timestamps are used so each side can, at the right times,
        # wait for the remote side, for synchronization.
        # NOTE: Check if this is correct
        self._ts_cycles += cycles
        if self._ts_cycles >= (1 << 21):
            self._ts_cycles -= (1 << 21)
            self._timestamp += 1

        print(bin(self.SB))

        if self.cycles_to_transmit() == 0:
            if self._timestamp > self._last_received_timestamp:
                return False

            if (self.SC >> 7) & 1:
                self.send_bit()

            # TODO: Keep in sync based on timestamp
            byte, timestamp = self.recv.get()
            self.SB = ((self.SB << 1) & 0xFF) | byte
            self.trans_bits += 1

            self.cycles_count = 0 # Reset cycle count after transmission

            if self.trans_bits == 8:
                self.trans_bits = 0
                # Clear transfer start flag
                self.SC &= 0x7F
                return True
        return False

    def cycles_to_transmit(self):
        if self.connection and self.SC & 0x80:
            return max(self.cycles_target - self.cycles_count, 0)
        return 1 << 16

    def stop(self):
        self.quitting = True
        if self.connection:
            self.connection.close()
        if hasattr(self, "binding_connection"):
            self.binding_connection.close()
        self.connection = None
        self.binding_connection = None
        self.recv_t.join()
