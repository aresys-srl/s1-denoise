# SPDX-FileCopyrightText: Aresys S.r.l. <info@aresys.it>
# SPDX-License-Identifier: MIT

"""
Module for extracting noise power estimates from Sentinel-1 L0N raw data.

Invokes the Sentinel1L0ImportQC tool to convert L0N products into a temporary
product folder, then computes per-channel mean noise power in dB by grouping
echoes according to mission-specific rank sequences (IW/EW, S1A/B/C/D). Exposes
the result as a dictionary keyed by (subswath, polarisation).
"""

import os
import numpy as np
import subprocess
import tempfile
from pathlib import Path
import re
import sys

from arepytools.timing.precisedatetime import PreciseDateTime
from arepytools.io import open_product_folder, read_metadata, read_raster_with_raster_info

from denoise.logger import denoise_logger

# Precompute groups
EW = [[1, 6], [2, 7], [3, 8], [4, 9], [5, 10]]
IW = [[1, 4, 7, 10], [2, 5, 8], [3, 6, 9]]

# Rank mapping
NRANK_MAP = {
    "EW": {
        0: 8, 
        1: 10, 
        2: 9, 
        3: 11, 
        4: 10
    },
    "IW": {
        0: 9, 
        1: 8, 
        2: 10
    }
}

# Combined lookup
GROUPS = {"IW": IW, "EW": EW}

def get_nrank(swath, channel_id):
    """Return nrank based on swath type and channel ID."""
    key = "IW" if "IW" in swath else "EW" if "EW" in swath else None
    if not key:
        return None
    for idx, group in enumerate(GROUPS[key]):
        if channel_id in group:
            return NRANK_MAP[key].get(idx)
    return None

def iq_bias_correction(raw_data):
    """Raw data correction removing constant biases, correcting gain imbalance and non-orthogonality for both components of the signal.
    For Sentinel-1 data, however, the instrument's receive module performs the demodulation in the digital domain, therefore the I/Q gain 
    imbalance and I/Q non-orthogonality corrections are not necessary."""

    i_values = raw_data.real
    q_values = raw_data.imag

    # remove constant biases: enforce zero mean
    i_out = i_values - np.mean(i_values)
    q_out = q_values - np.mean(q_values)

    corrected_data = i_out + 1j * q_out

    return corrected_data

def compute_sequence(array, nrank, mission_id, subswath, rank_echoes_present):
    """Compute the sequence, rank, and noise arrays."""

    rank_echoes = []
    ARRAY_IS_TIME = isinstance(array[0], PreciseDateTime)

    if mission_id.upper() in ["S1A", "S1B"]:
        step = nrank
    elif mission_id.upper() in ["S1C", "S1D"]:
        if subswath.startswith("IW"):
            step = 8 + (2 * nrank if rank_echoes_present else 0)
        elif subswath.startswith("EW"):
            step = 3 + (nrank if rank_echoes_present else 0)
    else:
        denoise_logger.error("Mission ID unknown in noise power calculation. Aborting...")
        sys.exit(128)

    # append preamble power
    preamble = array[0] if ARRAY_IS_TIME else np.mean(array[0:8])
    rank_echoes.append(preamble)

    acquisition = array[8:-8] # filter out preamble and postamble

    for index in range(int(len(acquisition) / step)):
        segment = acquisition[step*index : step*(index+1)]

        if rank_echoes_present and mission_id.upper() in ["S1C", "S1D"]:        
            if ARRAY_IS_TIME:
                rank_echoes.append(segment[0])
                rank_echoes.append(segment[nrank])
                rank_echoes.append(segment[2*nrank])
            else:
                rank_echoes.append(np.mean(segment[0 : nrank]))
                rank_echoes.append(np.mean(segment[nrank : 2*nrank]))
                rank_echoes.append(np.mean(segment[2*nrank :]))
        else:
            if ARRAY_IS_TIME:
                rank_echoes.append(segment[0])
            else:
                rank_echoes.append(np.mean(segment[0 : step]))

    # append postamble power
    postamble = array[-8] if ARRAY_IS_TIME else np.mean(array[-8:])
    rank_echoes.append(postamble)

    return rank_echoes

def calculate_noise_db(pf_path, mission_id, rank_echoes_present):

    pf = open_product_folder(pf_path)

    noise = {}

    for channel_id in pf.get_channels_list():
        metadata = read_metadata(pf.get_channel_metadata(channel_id))
        raster_info = metadata.get_raster_info()
        swath_info = metadata.get_swath_info()
        subswath = swath_info.swath
        pol = swath_info.polarization.name.upper()

        data = read_raster_with_raster_info(pf.get_channel_data(channel_id), raster_info)
        corrected_data = iq_bias_correction(data)

        az_time = [metadata.get_swath_info().acquisition_start_time + time_ 
                   for time_ in metadata.get_acquisition_time_line().noise_packet[1]]

        gain_lin = np.abs(corrected_data)
        power_lin = gain_lin ** 2
        power_mean = np.nanmean(power_lin, axis=1)

        nrank = get_nrank(subswath, channel_id)
        if nrank is None:
            continue # skip unknown swath/channel combos

        rank_db = compute_sequence(power_mean, nrank, mission_id, subswath, rank_echoes_present)
        az_time = compute_sequence(az_time, nrank, mission_id, subswath, rank_echoes_present)

        ######################## RANK ECHOES FILTERING ########################

        # the rank echo belongs to the first burst of the data take:
        # remove noise spike occurring in S1A/B, IW1 and EW1
        if mission_id.upper() in ["S1A", "S1B"]:
            if subswath == "IW1":
                l0n_noise = [rank_db[0]] + rank_db[2:]
                l0n_time = [az_time[0]] + az_time[2:]
            # in EW1 case, also remove last echo (nadir returns might be present)
            elif subswath == "EW1":
                l0n_noise = [rank_db[0]] + rank_db[2:-1]
                l0n_time = [az_time[0]] + az_time[2:-1]          
            else:
                l0n_noise = rank_db
                l0n_time = az_time      
        else:
            l0n_noise = rank_db
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

        noise.update({
            (subswath, pol): [final_times, final_noise]
        })

    # for EW2, substitute noise values with ones from EW3
    keys_to_replace = [k for k in noise if k[0] == "EW2"]
    for subswath, pol in keys_to_replace:
        noise[("EW2", pol)][1][1:] = noise[("EW3", pol)][1][1:]

    return noise

def get_noise_pwr_from_l0n(l0n_path: Path, aux_ins_path: Path):

    mission_id = l0n_path.stem[0:3].lower()

    rank_echoes_present = "echoCompressionType" in Path(l0n_path.resolve() / "manifest.safe").read_text()
    if rank_echoes_present:
        denoise_logger.info("Rank echoes present in L0N file")
    else:
        denoise_logger.info("Rank echoes not present in L0N file")

    binary = os.environ.get("S1_L0IMPORTQC_PATH")
    if not binary:
        denoise_logger.error("Environment variable S1_L0IMPORTQC_PATH is not set. Aborting...")
        sys.exit(128)

    BASE = Path(__file__).resolve().parent

    # if folder already exists, subprocess will give error: use a temp dir to avoid permission issues
    with tempfile.TemporaryDirectory() as tmp_dir:
        output_folder = Path(tmp_dir) / "tmp_s1l0"

        l0import_run = subprocess.Popen([
                binary,
                "-i", f"{l0n_path.resolve()}",
                "-a", f"{aux_ins_path.resolve()}",
                "-r", f"{BASE}/aux_rules/{mission_id}-aux-rules.xml",
                "-n", output_folder.resolve()
            ], 
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=os.getcwd()
        )

        # regex pattern to extract the message part after last "]: " 
        log_message_pattern = re.compile(r".*\]: (.*)$")

        # stream output line by line
        for chunk in l0import_run.stdout:
            for line in chunk.splitlines():
                line = line.strip()
                if not line:
                    continue

                # extract the meaningful message
                match = log_message_pattern.match(line)
                if match:
                    message = match.group(1).strip()
                else:
                    message = line

                # filter out SET_, CLOSE_, USE_ lines
                if not message.startswith(("SET_", "CLOSE_", "USE_", "TOTAL")):
                    if "error" in message.lower():
                        denoise_logger.error(message)
                    else:
                        denoise_logger.info(message)

        # wait for process to finish
        l0import_run.wait()

        if l0import_run.returncode != 0:
            raise RuntimeError(f"Sentinel1L0ImportQC failed with exit code {l0import_run.returncode}")
        else:
            denoise_logger.info("Noise power calculation completed successfully")

        noise = calculate_noise_db(output_folder, mission_id, rank_echoes_present)

    # remove temporary folder (handled automatically by TemporaryDirectory context manager)
    return noise