# Level 1 Noise Processor

The Level 1 Noise Processor calculates noise vectors from Level 1 annotations and outputs a file analogous to the annotation noise files.

Such processor will process all Level 1 annotation products pointed by the Job Order and create a CSV file for further analysis called `denoise_errors_TIMESTAMP.csv`, ready for import in Excel if needed.

Optionally, if a Level 0 Noise product folder is associated with a Level 1 annotation product folder in the Job Order, the processor derives the noise power correction values from the Level 0 data rather than reading them directly from the annotation files. 

In both cases, a Z-method test is performed on noise power correction values to filter out RFIs.

You can plot results using 
```bash
./scripts/noise_compare.py <noise_xml_filepath> <denoise_xml_filepath> --colorbar 0.01
```
where the `--colorbar` argument sets the symmetric boundary values for the colorbar in the 2D plot.

## How to use

The L1N Processor can be operated as a Docker container or it can be run as a stand-alone Python package. A Docker image is always provided as part of the official release package.

The first option is preferred as it simplifies the installation procedure and minimizes the dependency of the error processor on the host machine.

### Option 1: Python package

Install the package with 
```bash
pip install -e .[dev,cli]
```
and launch the processor with the command
```bash 
denoise "/path/to/job/order"
```

Check other processing options with
```bash
denoise --help
```

### Option 2: Docker image

Build the Docker image with `build_docker.sh` script and launch the processor with
```bash
docker run --rm --volume $(pwd):$(pwd) -w $(pwd) s1_l1n_processor:1.0.0 "/path/to/job/order"
```

## Current status

The current quality of the noise vector reconstruction is displayed below, where the processor analyzed almost 240 annotation files and aggregated the results.

![alt text](./current_status.png)
