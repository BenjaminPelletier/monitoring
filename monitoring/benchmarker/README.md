# benchmarker

`benchmarker` is a tool for evaluating the performance, throughput, and stability of UTM systems under load.

## Running benchmarker

To execute `benchmarker`, run the following command from the root of the `monitoring` repo:

```bash
PYTHONPATH=. uv run python monitoring/benchmarker/benchmark.py --config file://monitoring/benchmarker/configurations/interuss/isas_uncontended.jsonnet
```
