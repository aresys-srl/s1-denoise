# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Extract noise power estimates from Sentinel-1 L0N raw data.

Reads the raw packets of an L0N SAFE product directly, producing one noise power sample per instrument burst: noise
pulse (NOISE signal) packets where present (plain BAQ 5-bit, decoded by `s1_denoise.baq_decoder`), otherwise the
rank echo packets of the burst (FDBAQ, decoded with ``sentinel1decoder``).

The per-burst samples are then filtered according to mission-specific rank echo sequences (IW/EW, S1A/B/C/D).
"""

import sys
from pathlib import Path

import numpy as np
import sentinel1decoder as s1decoder
from sentinel1decoder import BaqMode, ECCNumber, RxChannelId, SignalType

from perseo_core.timing import PreciseDateTime

from s1_denoise.baq_decoder import decode_baq_packet, quads_to_complex
from s1_denoise.logger import denoise_logger

# Seconds between the GPS time epoch (1980-01-06T00:00:00) used by the "Coarse Time"/"Fine Time" packet fields and PreciseDateTime's own epoch (1985-01-01T00:00:00 UTC).
_GPS_EPOCH_TO_PRECISEDATETIME_EPOCH_SEC = 157420800.0
# GPS-UTC leap second offset. Constant since the last leap second insertion on 2016-12-31; would need bumping if IERS schedules a new one.
_GPS_UTC_LEAP_SECONDS = 18


def iq_bias_correction(raw_data):
    """
    Apply the I/Q bias correction to complex raw data.

    Parameters
    ----------
    raw_data : numpy.ndarray
        Complex raw samples.

    Returns
    -------
    numpy.ndarray
        Complex samples with zero-mean I and Q components.

    Notes
    -----
    For Sentinel-1 data the demodulation is performed by the instrument's receive module in the digital domain, so the
    I/Q gain imbalance and non-orthogonality corrections are not necessary: only the constant biases are removed.
    """

    i_values = raw_data.real
    q_values = raw_data.imag

    # remove constant biases: enforce zero mean
    i_out = i_values - np.mean(i_values)
    q_out = q_values - np.mean(q_values)

    corrected_data = i_out + 1j * q_out

    return corrected_data


def _packet_time(coarse_time, fine_time) -> PreciseDateTime:
    """
    Convert the packet "Coarse Time"/"Fine Time" fields (GPS time) into a UTC PreciseDateTime.

    Parameters
    ----------
    coarse_time : int or float
        Coarse time [s] since the GPS epoch.
    fine_time : float
        Fine time [s].

    Returns
    -------
    PreciseDateTime
        UTC packet time.
    """
    sec85 = float(coarse_time) + float(fine_time) - _GPS_EPOCH_TO_PRECISEDATETIME_EPOCH_SEC - _GPS_UTC_LEAP_SECONDS
    return PreciseDateTime.from_sec85(sec85)


def _subswath_name(ecc_number, swath_num):
    """
    Map an (ECC number, swath number) pair to a subswath label, e.g. ``"IW1"`` or ``"EW3"``.

    Parameters
    ----------
    ecc_number : sentinel1decoder.ECCNumber
        ECC number of the packet.
    swath_num : int
        Swath number of the packet.

    Returns
    -------
    str or None
        Subswath label, or None for modes other than IW and EW.
    """

    if ecc_number == ECCNumber.INTERFEROMETRIC_WIDE_SWATH:
        return f"IW{swath_num - 9}"
    if ecc_number == ECCNumber.EXTRA_WIDE_SWATH:
        return f"EW{swath_num - 19}"
    return None


def _polarization_label(polarisation, rx_channel_id):
    """
    Build the polarization label (e.g. ``"VH"``) from the transmit polarisation and the receive channel.

    Parameters
    ----------
    polarisation : enum
        Packet "Polarisation" field; its name contains ``"TX_H"`` for H transmission.
    rx_channel_id : sentinel1decoder.RxChannelId
        Receive channel identifier.

    Returns
    -------
    str
        Two-letter polarization label.
    """

    tx = "H" if "TX_H" in polarisation.name else "V"
    rx = "H" if rx_channel_id == RxChannelId.RXH_POL_CHANNEL else "V"

    return tx + rx


def _channel_lines(dat_path: Path):
    """
    Decode the noise lines of one L0N measurement data file, grouped per (subswath, polarization) channel.

    NOISE-signal chunks are fully decoded with the plain BAQ 5-bit decoder; for ECHO chunks, only the first ``Rank``
    lines (rank echoes) are decoded with ``sentinel1decoder``.

    Parameters
    ----------
    dat_path : pathlib.Path
        Path of the ``.dat`` measurement data file.

    Returns
    -------
    dict
        Dictionary keyed by ``(subswath, pol)``, whose values are dictionaries with:

        - ``"lines"``: list of complex numpy.ndarray, one per decoded line;
        - ``"times"``: list of PreciseDateTime, one per decoded line;
        - ``"burst_sizes"``: list of int, number of lines of each burst.

    Raises
    ------
    NotImplementedError
        If NOISE packets are not compressed in BAQ 5-bit mode.
    """

    l0 = s1decoder.Level0File(str(dat_path))
    bounds = l0._decoder._user_data_bounds

    # group chunks per (subswath, pol)
    channels = {}
    with open(dat_path, "rb") as fh:
        for chunk_id in l0.acquisition_chunks:
            const = l0.get_acquisition_chunk_constants(chunk_id)
            if const["signal_type"] not in (SignalType.NOISE, SignalType.ECHO):
                continue
            chunk_meta = l0.get_acquisition_chunk_metadata(chunk_id)
            subswath = _subswath_name(chunk_meta["ECC Number"].iloc[0], const["swath_num"])
            if subswath is None:
                continue
            pol = _polarization_label(chunk_meta["Polarisation"].iloc[0], chunk_meta["Rx Channel ID"].iloc[0])
            key = (subswath, pol)
            channels.setdefault(key, {"lines": [], "times": [], "burst_sizes": [], "_chunks": []})
            channels[key]["_chunks"].append((chunk_id, const, chunk_meta))

        for key, data in channels.items():
            for chunk_id, const, chunk_meta in sorted(data["_chunks"], key=lambda x: x[0]):
                if const["signal_type"] == SignalType.NOISE:
                    if const["baq_mode"] != BaqMode.BAQ_5_BIT_MODE:
                        raise NotImplementedError(f"Unsupported BAQ mode for NOISE packets in {key}: {const['baq_mode']}")
                    burst_meta = chunk_meta
                    burst_lines = []
                    for packet_num, num_quads in zip(
                        burst_meta.index.get_level_values("Packet Number"), burst_meta["Number of Quads"].astype(int)
                    ):
                        start, length = bounds[packet_num]
                        fh.seek(start)
                        raw = fh.read(length)
                        IE, IO, QE, QO = decode_baq_packet(raw, num_quads, code_bits=5)
                        burst_lines.append(quads_to_complex(IE, IO, QE, QO))
                else:
                    rank = int(chunk_meta["Rank"].iloc[0])
                    burst_meta = chunk_meta.iloc[:rank]
                    decoded = l0.get_acquisition_chunk_data(chunk_id, try_load_from_file=False)
                    burst_lines = list(decoded[: len(burst_meta)])

                data["lines"].extend(burst_lines)
                data["times"].extend(_packet_time(c, f) for c, f in zip(burst_meta["Coarse Time"], burst_meta["Fine Time"]))
                data["burst_sizes"].append(len(burst_lines))

            del data["_chunks"]

    return channels


def _global_bias_correct_power(lines):
    """
    Apply the I/Q bias correction globally over all lines of a channel and return the mean power of each line.

    Parameters
    ----------
    lines : list of numpy.ndarray
        Complex lines of one channel.

    Returns
    -------
    list of float
        Mean power of each bias-corrected line.
    """

    all_real = np.concatenate([line.real for line in lines])
    all_imag = np.concatenate([line.imag for line in lines])
    i_bias = np.mean(all_real)
    q_bias = np.mean(all_imag)
    return [np.mean(np.abs((line.real - i_bias) + 1j * (line.imag - q_bias)) ** 2) for line in lines]

def filter_rank_echoes(noise: dict, mission_id: str):
    """
    Discard biased noise measurements according to mission-specific rank echo sequences.

    Parameters
    ----------
    noise : dict
        Per-burst noise power, keyed by ``(subswath, pol)``, with values ``[az_time, burst_powers]`` (lists of
        PreciseDateTime and of float).
    mission_id : str
        Mission identifier (e.g. ``"s1a"``), case-insensitive.

    Returns
    -------
    dict
        Filtered noise power, keyed by ``(subswath, pol)``, with values ``[times, powers]``.

    Notes
    -----
    For S1A/B, the spikes in the first burst of the data-take are removed for IW1 and EW1 (second value), the last EW1
    value is removed (possible nadir returns), and values biased by calibration pulses are discarded in an alternating
    pattern for IW1, IW2 and IW3. For all missions, EW2 values (except the first) are replaced in place with the EW3 ones
    of the same polarization.
    """

    filtered = {}
    for (subswath, pol), (az_time, burst_powers) in noise.items():

        # the rank echo belongs to the first burst of the data take:
        # remove noise spike occurring in S1A/B, IW1 and EW1
        if mission_id.upper() in ["S1A", "S1B"]:
            if subswath == "IW1":
                l0n_noise = [burst_powers[0]] + burst_powers[2:]
                l0n_time = [az_time[0]] + az_time[2:]
            # in EW1 case, also remove last echo (nadir returns might be present)
            elif subswath == "EW1":
                l0n_noise = [burst_powers[0]] + burst_powers[2:-1]
                l0n_time = [az_time[0]] + az_time[2:-1]
            else:
                l0n_noise = burst_powers
                l0n_time = az_time
        else:
            l0n_noise = burst_powers
            l0n_time = az_time

        # the rank echo belongs to an odd-numbered burst: remove
        if mission_id.upper() in ["S1A", "S1B"]:
            if subswath == "IW1":
                # in this case the first rank of the first burst was already removed
                # (so odd-numbered burst = even-indexed values)
                final_times = np.concatenate(([l0n_time[0]], l0n_time[1::2], [l0n_time[-1]]))
                final_noise = np.concatenate(([l0n_noise[0]], l0n_noise[1::2], [l0n_noise[-1]]))
            elif subswath in ["IW2", "IW3"]:
                final_times = np.concatenate((l0n_time[0::2], [l0n_time[-1]]))
                final_noise = np.concatenate((l0n_noise[0::2], [l0n_noise[-1]]))
            else:
                final_times = l0n_time
                final_noise = l0n_noise
        else:
            final_times = l0n_time
            final_noise = l0n_noise

        filtered[(subswath, pol)] = [final_times, final_noise]

    # for EW2, substitute noise values with ones from EW3
    keys_to_replace = [k for k in filtered if k[0] == "EW2"]
    for subswath, pol in keys_to_replace:
        filtered[("EW2", pol)][1][1:] = filtered[("EW3", pol)][1][1:]

    return filtered


def calculate_noise_power(l0n_path: Path, mission_id: str, rank_echoes_present: bool):
    """
    Compute the noise power sequences of each channel from the raw packets of an L0N SAFE product.

    All ``.dat`` files of the product are decoded; the I/Q bias correction is applied per channel, and the line powers
    are averaged per burst (pre-amble, each rank echo or noise sequence, post-amble), keeping the time of the first
    line. The result is then filtered with `filter_rank_echoes`.

    Parameters
    ----------
    l0n_path : pathlib.Path
        Path of the L0N SAFE directory.
    mission_id : str
        Mission identifier (e.g. ``"s1a"``), case-insensitive.
    rank_echoes_present : bool
        Whether the L0N product contains rank echoes.

    Returns
    -------
    dict
        Noise power keyed by ``(subswath, pol)``, with values ``[times, powers]`` (azimuth times and mean noise power
        per burst, linear scale).

    Raises
    ------
    SystemExit
        For S1C/S1D products with rank echoes, not supported yet (exit code 128).
    """

    if mission_id.upper() in ["S1C", "S1D"] and rank_echoes_present:
        denoise_logger.error(
            "S1C/S1D rank-echo handling not implemented yet. Aborting..."
        )
        sys.exit(128)

    channels = {}
    for dat_path in sorted(Path(l0n_path).glob("*.dat")):
        denoise_logger.info(f"Decoding raw packets from {dat_path.name}...")
        for key, data in _channel_lines(dat_path).items():
            channels.setdefault(key, {"lines": [], "times": [], "burst_sizes": []})
            channels[key]["lines"].extend(data["lines"])
            channels[key]["times"].extend(data["times"])
            channels[key]["burst_sizes"].extend(data["burst_sizes"])

    noise = {}
    for key, data in channels.items():
        powers = _global_bias_correct_power(data["lines"])
        times = data["times"]
        burst_sizes = data["burst_sizes"]

        # reduce each burst's lines to one representative (mean power, first time) sample:
        # burst_sizes = [n_preamble, rank_burst_1, rank_burst_2, ... , n_postamble]
        burst_powers = []
        az_time = []
        idx = 0
        for size in burst_sizes:
            burst_powers.append(np.mean(powers[idx : idx + size]))
            az_time.append(times[idx])
            idx += size

        noise[key] = [az_time, burst_powers]

    # rank echoes filtering based on mission ID
    filtered = filter_rank_echoes(noise, mission_id)

    return filtered


def get_noise_pwr_from_l0n(l0n_path: Path, aux_ins_path: Path = None):
    """
    Extract the noise power sequences of each channel from a Sentinel-1 L0N SAFE product.

    The mission is taken from the product name, and the presence of rank echoes is detected from the manifest.

    Parameters
    ----------
    l0n_path : pathlib.Path
        Path of the L0N SAFE directory.
    aux_ins_path : pathlib.Path, optional
        Path of the AUX_INS file. Currently unused. Default is None.

    Returns
    -------
    dict
        Noise power keyed by ``(subswath, pol)``, as returned by `calculate_noise_power`.
    """

    mission_id = l0n_path.stem[0:3].lower()

    rank_echoes_present = "echoCompressionType" in Path(l0n_path.resolve() / "manifest.safe").read_text()
    if rank_echoes_present:
        denoise_logger.info("Rank echoes present in L0N file")
    else:
        denoise_logger.info("Rank echoes not present in L0N file")

    noise = calculate_noise_power(l0n_path, mission_id, rank_echoes_present)

    denoise_logger.info("Noise power calculation completed successfully")

    return noise