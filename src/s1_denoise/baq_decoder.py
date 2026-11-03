# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Decode Sentinel-1 plain BAQ NOISE-packet samples (ICD "Data Format C", 3/4/5-bit), which ``sentinel1decoder`` cannot
decode.

The user data field is made of four 16-bit-aligned sections (IE, IO, QE, QO) of `num_quads` sign-magnitude
`code_bits`-wide codes (MSB first); QE alone has an extra 8-bit Threshold Index (THIDX) field every 128 samples.
The raw quantizer code is returned, not the NRL/sigma-corrected amplitude: this is valid only while THIDX stays below
the ICD reconstruction threshold, which is checked explicitly (see `QE_THIDX_THRESHOLD`).
"""

import sys
import numpy as np

from s1_denoise.logger import denoise_logger

QE_BLOCK_LEN = 128
QE_HEADER_BITS = 8

# THIDX threshold per BAQ mode below which the ICD-decoded sample value equals the raw
# quantizer code (ICD section 9.1.2.2); at or above it, NRL/sigma-factor reconstruction
# would be needed instead. Matches AUX_INS's thresholdLutList (thidxThreshold).
QE_THIDX_THRESHOLD = {3: 3, 4: 5, 5: 10}


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
    values : numpy.ndarray
        Signed integer quantizer codes.
    offset : int
        Bit position following the last decoded code.
    """

    total = nbits * count
    chunk = bits[offset : offset + total].reshape(count, nbits)
    weights = 1 << np.arange(nbits - 1, -1, -1)
    codes = chunk @ weights
    sign = (codes >> (nbits - 1)) & 1
    mag = codes & ((1 << (nbits - 1)) - 1)
    values = np.where(sign == 1, -mag, mag)

    return values, offset + total


def _decode_qe_section(bits: np.ndarray, offset: int, nbits: int, count: int):
    """
    Decode the QE section, skipping the 8-bit THIDX field before every 128-sample block.

    Each THIDX is checked to stay below the ICD reconstruction threshold, where the decoded value is the raw quantizer
    code (see `QE_THIDX_THRESHOLD`).

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
    values : numpy.ndarray
        Signed integer quantizer codes.
    offset : int
        Bit position following the last decoded code.

    Raises
    ------
    SystemExit
        If a THIDX is at or above the reconstruction threshold (exit code 128).
    """

    threshold = QE_THIDX_THRESHOLD[nbits]
    values = np.zeros(count, dtype=int)
    pos = offset
    idx = 0

    while idx < count:
        n = min(QE_BLOCK_LEN, count - idx)

        thidx = 0
        for bit in bits[pos : pos + QE_HEADER_BITS]:
            thidx = (thidx << 1) | int(bit)
        if thidx >= threshold:
            denoise_logger.error(
                f"QE block THIDX={thidx} is at or above the ICD reconstruction threshold ({threshold}) for BAQ {nbits}-bit mode; the raw quantizer code no longer equals the decoded sample value (NRL/sigma-factor reconstruction would be needed instead). Aborting rather than returning silently wrong noise power values."
            )
            sys.exit(128)

        pos += QE_HEADER_BITS
        block_values, pos = _decode_plain_section(bits, pos, nbits, n)
        values[idx : idx + n] = block_values
        idx += n

    return values, pos


def decode_baq_packet(raw_user_data: bytes, num_quads: int, code_bits: int = 5):
    """
    Decode the user data field of one plain BAQ packet.

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
        Quantizer codes of the even/odd I and even/odd Q samples, one array per section.

    Raises
    ------
    SystemExit
        If `code_bits` is not 5, or a QE THIDX is at or above the reconstruction threshold (exit code 128).
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
    QE, pos = _decode_qe_section(bits, pos, code_bits, num_quads)
    pos = _section_align(pos)
    QO, _ = _decode_plain_section(bits, pos, code_bits, num_quads)

    return IE, IO, QE, QO


def quads_to_complex(IE: np.ndarray, IO: np.ndarray, QE: np.ndarray, QO: np.ndarray) -> np.ndarray:
    """
    Interleave the decoded sections into complex samples in the standard order.

    The output is ``IE[0] + 1j*QE[0], IO[0] + 1j*QO[0], IE[1] + 1j*QE[1], ...``.

    Parameters
    ----------
    IE, IO, QE, QO : numpy.ndarray
        Quantizer codes of the even/odd I and even/odd Q samples, as returned by `decode_baq_packet`.

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