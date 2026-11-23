# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Decode Sentinel-1 plain BAQ NOISE-packet samples (ICD "Data Format C", 5-bit), which ``sentinel1decoder`` cannot
decode.

The user data field is made of four 16-bit-aligned sections (IE, IO, QE, QO) of `num_quads` sign-magnitude
`code_bits`-wide codes (MSB first); QE alone has an extra 8-bit Threshold Index (THIDX) field every 128 samples, which
applies to the samples of that block in all four sections. The sample values are reconstructed as in the ICD: with
the simple reconstruction (the code itself, or the simple reconstruction level for the saturated code) up to the THIDX
threshold, with the normal reconstruction (normalized reconstruction level times sigma factor) above it.
"""

import sys
import numpy as np

from s1_denoise.logger import denoise_logger

QE_BLOCK_LEN = 128
QE_HEADER_BITS = 8

# BAQ 5-bit reconstruction tables (ICD; identical to the decodingParams of all the AUX_INS files)
BAQ5_THIDX_THRESHOLD = 10   # simple reconstruction up to this THIDX (included), normal reconstruction above
BAQ5_M_CODE = 15            # saturated code magnitude, replaced by the simple reconstruction level
BAQ5_SIMPLE_RECONSTRUCTION_LEVELS = np.array([15, 15, 15, 15, 15, 15, 15.44, 15.56, 16.11, 16.38, 16.65])
BAQ5_NORMALIZED_RECONSTRUCTION_LEVELS = np.array([0.066, 0.1985, 0.332, 0.4677, 0.6061, 0.7487, 0.8964, 1.051, 1.2143,
                                                  1.3896, 1.58, 1.7914, 2.0329, 2.3234, 2.6971, 3.2692])
SIGMA_FACTORS = np.array([
    0, 0.63, 1.25, 1.88, 2.51, 3.13, 3.76, 4.39, 5.01, 5.64, 6.27, 6.89, 7.52, 8.15, 8.77, 9.4,
    10.03, 10.65, 11.28, 11.91, 12.53, 13.16, 13.79, 14.41, 15.04, 15.67, 16.29, 16.92, 17.55, 18.17, 18.8, 19.43,
    20.05, 20.68, 21.31, 21.93, 22.56, 23.19, 23.81, 24.44, 25.07, 25.69, 26.32, 26.95, 27.57, 28.2, 28.83, 29.45,
    30.08, 30.71, 31.33, 31.96, 32.59, 33.21, 33.84, 34.47, 35.09, 35.72, 36.35, 36.97, 37.6, 38.23, 38.85, 39.48,
    40.11, 40.73, 41.36, 41.99, 42.61, 43.24, 43.87, 44.49, 45.12, 45.75, 46.37, 47, 47.63, 48.25, 48.88, 49.51,
    50.13, 50.76, 51.39, 52.01, 52.64, 53.27, 53.89, 54.52, 55.15, 55.77, 56.4, 57.03, 57.65, 58.28, 58.91, 59.53,
    60.16, 60.79, 61.41, 62.04, 62.98, 64.24, 65.49, 66.74, 68, 69.25, 70.5, 71.76, 73.01, 74.26, 75.52, 76.77,
    78.02, 79.28, 80.53, 81.78, 83.04, 84.29, 85.54, 86.8, 88.05, 89.3, 90.56, 91.81, 93.06, 94.32, 95.57, 96.82,
    98.08, 99.33, 100.58, 101.84, 103.09, 104.34, 105.6, 106.85, 108.1, 109.35, 110.61, 111.86, 113.11, 114.37, 115.62, 116.87,
    118.13, 119.38, 120.63, 121.89, 123.14, 124.39, 125.65, 126.9, 128.15, 129.41, 130.66, 131.91, 133.17, 134.42, 135.67, 136.93,
    138.18, 139.43, 140.69, 141.94, 143.19, 144.45, 145.7, 146.95, 148.21, 149.46, 150.71, 151.97, 153.22, 154.47, 155.73, 156.98,
    158.23, 159.49, 160.74, 161.99, 163.25, 164.5, 165.75, 167.01, 168.26, 169.51, 170.77, 172.02, 173.27, 174.53, 175.78, 177.03,
    178.29, 179.54, 180.79, 182.05, 183.3, 184.55, 185.81, 187.06, 188.31, 189.57, 190.82, 192.07, 193.33, 194.58, 195.83, 197.09,
    198.34, 199.59, 200.85, 202.1, 203.35, 204.61, 205.86, 207.11, 208.37, 209.62, 210.87, 212.13, 213.38, 214.63, 215.89, 217.14,
    218.39, 219.65, 220.9, 222.15, 223.41, 224.66, 225.91, 227.17, 228.42, 229.67, 230.93, 232.18, 233.43, 234.69, 235.94, 237.19,
    238.45, 239.7, 240.95, 242.21, 243.46, 244.71, 245.97, 247.22, 248.47, 249.73, 250.98, 252.23, 253.49, 254.74, 255.99, 255.99,
])


def _section_align(bit_pos: int) -> int:
    """
    Round a bit position up to the next 16-bit boundary, where IE/IO/QE/QO sections start.

    Parameters
    ----------
    bit_pos : int
        Bit position.

    Returns
    -------
    int
        Aligned bit position.
    """

    rem = bit_pos % 16

    return bit_pos if rem == 0 else bit_pos + (16 - rem)


def _decode_plain_section(bits: np.ndarray, offset: int, nbits: int, count: int):
    """
    Decode a contiguous run of `count` sign-magnitude `nbits`-wide codes, without headers.

    Parameters
    ----------
    bits : numpy.ndarray
        Unpacked user data bits (0/1).
    offset : int
        Bit position of the first code.
    nbits : int
        Code width in bits, sign bit included.
    count : int
        Number of codes to decode.

    Returns
    -------
    codes : numpy.ndarray
        Sign-magnitude codes, as an array of shape ``(2, count)`` with the sign (+1 or -1, kept also for zero
        magnitude) and the magnitude of each code.
    offset : int
        Bit position following the last decoded code.
    """

    total = nbits * count
    chunk = bits[offset : offset + total].reshape(count, nbits)
    weights = 1 << np.arange(nbits - 1, -1, -1)
    raw = chunk @ weights
    sign = np.where((raw >> (nbits - 1)) & 1, -1, 1)
    magnitude = raw & ((1 << (nbits - 1)) - 1)

    return np.stack((sign, magnitude)), offset + total


def _decode_qe_section(bits: np.ndarray, offset: int, nbits: int, count: int):
    """
    Decode the QE section, reading the 8-bit THIDX field before every 128-sample block.

    Parameters
    ----------
    bits : numpy.ndarray
        Unpacked user data bits (0/1).
    offset : int
        Bit position of the first THIDX field.
    nbits : int
        Code width in bits, sign bit included.
    count : int
        Number of codes to decode.

    Returns
    -------
    codes : numpy.ndarray
        Sign-magnitude codes, as returned by `_decode_plain_section`.
    thidx : numpy.ndarray
        THIDX of each 128-sample block.
    offset : int
        Bit position following the last decoded code.
    """

    codes = np.zeros((2, count), dtype=int)
    thidx_list = []
    pos = offset
    idx = 0

    while idx < count:
        n = min(QE_BLOCK_LEN, count - idx)

        thidx = 0
        for bit in bits[pos : pos + QE_HEADER_BITS]:
            thidx = (thidx << 1) | int(bit)
        thidx_list.append(thidx)

        pos += QE_HEADER_BITS
        block_codes, pos = _decode_plain_section(bits, pos, nbits, n)
        codes[:, idx : idx + n] = block_codes
        idx += n

    return codes, np.array(thidx_list, dtype=int), pos


def reconstruct_baq5(codes: np.ndarray, thidx: np.ndarray) -> np.ndarray:
    """
    Reconstruct the sample values of one section from the BAQ 5-bit codes, as in the ICD.

    For blocks with THIDX up to the threshold (10), the value is the code itself, except for the saturated codes
    (magnitude 15), replaced by the simple reconstruction level of the THIDX. Above the threshold, the value is the
    normalized reconstruction level of the code magnitude times the sigma factor of the THIDX.

    Parameters
    ----------
    codes : numpy.ndarray
        Sign-magnitude codes of one section, as returned by `_decode_plain_section`.
    thidx : numpy.ndarray
        THIDX of each 128-sample block.

    Returns
    -------
    numpy.ndarray
        Reconstructed sample values.
    """

    sign, magnitude = codes
    block_thidx = np.repeat(thidx, QE_BLOCK_LEN)[: magnitude.size]

    simple = np.where(
        magnitude < BAQ5_M_CODE,
        magnitude,
        BAQ5_SIMPLE_RECONSTRUCTION_LEVELS[np.minimum(block_thidx, BAQ5_THIDX_THRESHOLD)],
    )
    normal = BAQ5_NORMALIZED_RECONSTRUCTION_LEVELS[magnitude] * SIGMA_FACTORS[block_thidx]

    return sign * np.where(block_thidx <= BAQ5_THIDX_THRESHOLD, simple, normal)


def decode_baq_packet(raw_user_data: bytes, num_quads: int, code_bits: int = 5):
    """
    Decode the user data field of one plain BAQ packet and reconstruct its sample values.

    Parameters
    ----------
    raw_user_data : bytes
        Packet user data field.
    num_quads : int
        Number of quads, i.e. of codes in each section.
    code_bits : int, optional
        BAQ code width in bits. Only 5 (BAQ 5-bit) is supported. Default is 5.

    Returns
    -------
    IE, IO, QE, QO : numpy.ndarray
        Reconstructed values of the even/odd I and even/odd Q samples, one array per section (see
        `reconstruct_baq5`).

    Raises
    ------
    SystemExit
        If `code_bits` is not 5 (exit code 128).
    """

    if code_bits != 5:
        denoise_logger.error(
            f"BAQ {code_bits}-bit decoding is not validated against real data yet (only 5-bit noise packets have been verified); refusing to decode silently to avoid producing unverified noise power values."
        )
        sys.exit(128)

    bits = np.unpackbits(np.frombuffer(raw_user_data, dtype=np.uint8)).astype(np.int64)

    IE, pos = _decode_plain_section(bits, 0, code_bits, num_quads)
    pos = _section_align(pos)
    IO, pos = _decode_plain_section(bits, pos, code_bits, num_quads)
    pos = _section_align(pos)
    QE, thidx, pos = _decode_qe_section(bits, pos, code_bits, num_quads)
    pos = _section_align(pos)
    QO, _ = _decode_plain_section(bits, pos, code_bits, num_quads)

    # the THIDX of each block applies to the samples of the block in all four sections
    return tuple(reconstruct_baq5(codes, thidx) for codes in (IE, IO, QE, QO))


def quads_to_complex(IE: np.ndarray, IO: np.ndarray, QE: np.ndarray, QO: np.ndarray) -> np.ndarray:
    """
    Interleave the decoded sections into complex samples in the standard order.

    The output is ``IE[0] + 1j*QE[0], IO[0] + 1j*QO[0], IE[1] + 1j*QE[1], ...``.

    Parameters
    ----------
    IE, IO, QE, QO : numpy.ndarray
        Reconstructed values of the even/odd I and even/odd Q samples, as returned by `decode_baq_packet`.

    Returns
    -------
    numpy.ndarray
        Complex64 samples, of length ``2 * len(IE)``.
    """

    num_quads = len(IE)
    out = np.empty(num_quads * 2, dtype=np.complex64)
    out[0::2] = IE + 1j * QE
    out[1::2] = IO + 1j * QO

    return out