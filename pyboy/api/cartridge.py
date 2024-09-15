from pyboy.logging import get_logger

logger = get_logger(__name__)


class Cartridge:
    def __init__(self, mb):
        self.mb = mb
        self.cartridge_type = self.mb.cartridge.carttype
        self.game_name = self.mb.cartridge.gamename
        self.game_type = self.mb.cartridge.gametype
        self.destination_code = self.mb.cartridge.destinationcode
        self.is_genuine = self.mb.cartridge.isgenuine

    def save_ram(self, f):
        """Save SRAM to a file object."""
        if not self.rambank_initialized:
            logger.warning("RAM is not initialized, skipping save.")
            return

        self.mb.cartridge.save_ram(f)

    def load_ram(self, f):
        """Load SRAM from a file object."""
        if not self.rambank_initialized:
            logger.warning("RAM is not initialized, skipping load.")
            return

        self.mb.cartridge.load_ram(f)
