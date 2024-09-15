from libc.stdint cimport uint8_t, uint16_t, uint32_t

from pyboy.core.mb cimport Motherboard


cdef class Cartridge:
    cdef Motherboard mb
    cdef readonly str cartridge_title
    cdef readonly uint8_t cartridge_type
    cdef readonly str game_name
    cdef readonly str game_type
    cdef readonly int destination_code
    cdef readonly bint is_genuine

    cpdef void save_ram(self, IntIOInterface) noexcept
    cpdef void load_ram(self, IntIOInterface) noexcept
