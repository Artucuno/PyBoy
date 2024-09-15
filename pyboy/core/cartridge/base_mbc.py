#
# License: See LICENSE.md file
# GitHub: https://github.com/Baekalfen/PyBoy
#

import array
import os

import pyboy
from pyboy import utils
from pyboy.utils import IntIOWrapper

from .rtc import RTC

logger = pyboy.logging.get_logger(__name__)


class BaseMBC:
    def __init__(self, filename, rombanks, external_ram_count, carttype, sram, battery, rtc_enabled):
        self.filename = filename + ".ram"
        self.rombanks = rombanks
        self.carttype = carttype

        self.battery = battery
        self.rtc_enabled = rtc_enabled

        if self.rtc_enabled:
            self.rtc = RTC(filename)
        else:
            self.rtc = None

        self.rambank_initialized = False
        self.external_rom_count = len(rombanks)
        self.external_ram_count = external_ram_count
        self.init_rambanks(external_ram_count)
        self.gamename = self.getgamename(rombanks)
        self.gametype = self.getgametype(rombanks)
        self.destinationcode = self.getdestinationcode(rombanks)
        self.isgenuine = self.getisgenuine(rombanks)

        self.memorymodel = 0
        self.rambank_enabled = False
        self.rambank_selected = 0
        self.rombank_selected = 1
        self.rombank_selected_low = 0

        self.cgb = bool(self.rombanks[0, 0x0143] >> 7)

        if not os.path.exists(self.filename):
            logger.debug("No RAM file found. Skipping.")
        else:
            with open(self.filename, "rb") as f:
                self.load_ram(IntIOWrapper(f))

    def stop(self):
        with open(self.filename, "wb") as f:
            self.save_ram(IntIOWrapper(f))

        if self.rtc_enabled:
            self.rtc.stop()

    def save_state(self, f):
        f.write(self.rombank_selected)
        f.write(self.rambank_selected)
        f.write(self.rambank_enabled)
        f.write(self.memorymodel)
        self.save_ram(f)
        if self.rtc_enabled:
            self.rtc.save_state(f)

    def load_state(self, f, state_version):
        self.rombank_selected = f.read()
        self.rambank_selected = f.read()
        self.rambank_enabled = f.read()
        self.memorymodel = f.read()
        self.load_ram(f)
        if self.rtc_enabled:
            self.rtc.load_state(f, state_version)

    def save_ram(self, f):
        if not self.rambank_initialized:
            logger.warning("Saving RAM is not supported on %0.2x", self.carttype)
            return

        for bank in range(self.external_ram_count):
            for byte in range(8 * 1024):
                f.write(self.rambanks[bank, byte])

        logger.debug("RAM saved.")

    def load_ram(self, f):
        if not self.rambank_initialized:
            logger.warning("Loading RAM is not supported on %0.2x", self.carttype)
            return

        for bank in range(self.external_ram_count):
            for byte in range(8 * 1024):
                self.rambanks[bank, byte] = f.read()

        logger.debug("RAM loaded.")

    def init_rambanks(self, n):
        self.rambank_initialized = True
        # In real life the values in RAM are scrambled on initialization.
        # Allocating the maximum, as it is easier in Cython. And it's just 128KB...
        self.rambanks = memoryview(array.array("B", [0] * (8*1024*16))).cast("B", shape=(16, 8 * 1024))

    def getgamename(self, rombanks):
        return "".join([chr(rombanks[0, x]) for x in range(0x0134, 0x0142)]).split("\0")[0]

    def getgametype(self, rombanks):
        """
        This byte specifies which model of Gameboy the game is for.
        """
        if rombanks[0, 0x143] == 0x80 or rombanks[0, 0x143] == 0xc0:
            return "cgb" # Gameboy Color
        elif rombanks[0, 0x146] == 0x03:
            return "sgb" # Super Gameboy
        else:
            return "dmg" # Gameboy

    def getdestinationcode(self, rombanks):
        """
        This byte specifies whether this version of the game is intended to be sold in Japan or elsewhere.

        0x00: Japanese
        0x01: Non-Japanese

        https://gbdev.io/pandocs/The_Cartridge_Header.html#014a--destination-code
        """
        if self.gametype == "dmg":
            return
        return rombanks[0, 0x014A] #

    def setitem(self, address, value):
        raise Exception("Cannot set item in MBC")

    def getisgenuine(self, rombanks):
        """
        Check if the nintendo logo is correct
        CE ED 66 66 CC 0D 00 0B 03 73 00 83 00 0C 00 0D
        00 08 11 1F 88 89 00 0E DC CC 6E E6 DD DD D9 99
        BB BB 67 63 6E 0E EC CC DD DC 99 9F BB B9 33 3E

        https://gbdev.io/pandocs/The_Cartridge_Header.html#0104-0133--nintendo-logo
        """

        nintendo_logo = [
            0xCE, 0xED, 0x66, 0x66, 0xCC, 0x0D, 0x00, 0x0B, 0x03, 0x73, 0x00, 0x83, 0x00, 0x0C, 0x00, 0x0D, 0x00, 0x08,
            0x11, 0x1F, 0x88, 0x89, 0x00, 0x0E, 0xDC, 0xCC, 0x6E, 0xE6, 0xDD, 0xDD, 0xD9, 0x99, 0xBB, 0xBB, 0x67, 0x63,
            0x6E, 0x0E, 0xEC, 0xCC, 0xDD, 0xDC, 0x99, 0x9F, 0xBB, 0xB9, 0x33, 0x3E
        ]

        for i in range(0x104, 0x134):
            if rombanks[0, i] != nintendo_logo[i - 0x104]:
                return False

        return True

    def overrideitem(self, rom_bank, address, value):
        if 0x0000 <= address < 0x4000:
            logger.debug(
                "Performing overwrite on address: 0x%04x:0x%04x. New value: 0x%04x Old value: 0x%04x", rom_bank,
                address, value, self.rombanks[rom_bank, address]
            )
            self.rombanks[rom_bank, address] = value
        else:
            logger.error("Invalid override address: %0.4x", address)

    def getitem(self, address):
        if 0xA000 <= address < 0xC000:
            # if not self.rambank_initialized:
            #     logger.error("RAM banks not initialized: 0.4x", address)

            if not self.rambank_enabled:
                return 0xFF

            if self.rtc_enabled and 0x08 <= self.rambank_selected <= 0x0C:
                return self.rtc.getregister(self.rambank_selected)
            else:
                return self.rambanks[self.rambank_selected, address - 0xA000]
        # else:
        #     logger.error("Reading address invalid: %0.4x", address)

    def __repr__(self):
        return "\n".join([
            "MBC class: %s" % self.__class__.__name__,
            "Filename: %s" % self.filename,
            "Game name: %s" % self.gamename,
            "Game type: %s" % self.gametype,
            "Destination code: %s" % hex(self.destinationcode),
            "Is genuine: %s" % self.isgenuine,
            "GB Color: %s" % str(self.rombanks[0, 0x143] == 0x80),
            "Cartridge type: %s" % hex(self.carttype),
            "Number of ROM banks: %s" % self.external_rom_count,
            "Active ROM bank: %s" % self.rombank_selected,
            # "Memory bank type: %s" % self.ROMBankController,
            "Number of RAM banks: %s" % len(self.rambanks),
            "Active RAM bank: %s" % self.rambank_selected,
            "Battery: %s" % self.battery,
            "RTC: %s" % self.rtc_enabled
        ])


class ROMOnly(BaseMBC):
    def setitem(self, address, value):
        if 0x2000 <= address < 0x4000:
            if value == 0:
                value = 1
            self.rombank_selected = (value & 0b1)
            logger.debug("Switching bank 0x%0.4x, 0x%0.2x", address, value)
        elif 0xA000 <= address < 0xC000:
            self.rambanks[self.rambank_selected, address - 0xA000] = value
        # else:
        #     logger.debug("Unexpected write to 0x%0.4x, value: 0x%0.2x", address, value)
