# KVonset

The Woonsocket project focused on measuring network applications' performance. 
This project, KVonset, will add a focus on improving applications' performance.
The project replaces Woonsocket's synthetic workload with one closer to reality - an in-memory key-value store. 
In addition to evaluating the performance trends each configuration offers, our goal is to improve this performance.
So, one configuration should meet an SLO, described below.

Along the way, we will evaluate techniques that will help achieve high performance.

## Deliverables

As before, the primary deliverable is a report in the style of a Jupyter Notebook with **at least** the following:

- Evaluation showing that your implementation has both correct application behavior and correct performance testing behavior. 
This includes throughput-latency graphs for median, 95th, and 99th percentile latency (these can be on the same graph) across the different system configurations.

- A written analysis, using the data the graphs show, of the performance characteristics of the system configurations. 
The project will consider four configurations: 
(i) a traditional send/recv implementation, 
(ii) an implementation using vectored IO, 
(iii) an implementation using `io_uring`, 
(iv) an implementation that meets the SLO. 
Implementation (iv), can be the same as any of (i)--(iii) if they meet the SLO, or otherwise separate implementation(s).

- An explanation and evaluation of any optimizations you implemented, or design decisions you made, in your implementations.

### Midpoint Check-In

This is a long project, so we will have a midpoint check-in the week of October 19 during office hours. You should attend a staff member's office hours and demonstrate at least:
- Your basic server design, with strategy (i) above working.
- Some progress on either strategy (ii) or (iii).

### SLO

We will publish the SLO on EdStem once the grading server is ready.

Note that the load is expressed in keys / second, not requests / second, since a request in this project comprises multiple keys. See [Mixed Workload](#mixed-workload) for how to account for this.

The system should meet the SLO *on the grading server*. 
The grading server environment will be two CPU cores of a VM. Many students' personal computers have more compute resources, so the SLO may be more easily achieved on your laptop. 
As a result, please do *only* report results from the grading server in your report; we want to measure your implementation and not your laptop. 
If you wish to test your implementation's performance locally, you may want to use the run script's `--server-cpus` option, `taskset`, or a similar mechanism to limit the number of resources available.

### Grading

As before, we will assign a grade following an interactive 1-1 meeting between you and one of the course staff in which you describe and defend the contents of your report. 
The idea is to emulate a meeting between you and a skeptical user, where you use your report's contents as evidence that your findings are correct. 
You should expect questions of the form, "Why does this graph look the way it does?"

We will use a coarse 5-point scale as follows:

0. (0.5 points) Midpoint check-in
1. Implementation correctness
2. Demonstrating and explaining the performance implications of vectored IO
3. Demonstrating and explaining the performance implications of `io_uring`
4. (1.5 points) Demonstrating and explaining that a configuration of your implementation meets the target SLO


Successful reports will generally include headings for each of the points.
Remember that if you lose points on a project, you can earn up to 3 points per project back at the subsequent grading meeting by fixing any problems in your report that arise.

### Leaderboard

We will additionally offer an opt-in leaderboard. You can opt-in by:
1. modifying the `leaderboard.toml` file provided in the handout repo (opt-out by default). Feel free to use an anonymized name, but keep the same name for future submissions.
2. writing `leaderboard.csv` either using `SummaryStats::write_leaderboard` in [`kvonset-work`'s `stats` module](https://github.com/cs1675/kvonset-work/blob/main/src/stats.rs) (enable it with `features = ["stats"]`) or following its format. Of course, `leaderboard.csv` should contain actual results you measure from your implementation.

Your results will automatically be recorded on the grading server and the most recent one for the `optimized` strategy will be displayed on a graph together with other people's results.

As usual, your project grade will be determined by your report and grading meeting; the leaderboard won't affect grading and is just for fun. You can optimize for whatever metric you want to in the `optimized` strategy.
The run script requires a `--seed` for the `optimized` strategy and passes it to the client, so that leaderboard entries use the same sequence of requests.

## Implementation

To provide you with maximum implementation flexibility, there is no skeleton code for this project.
It is allowed and encouraged (but not required) to re-use code from your Woonsocket implementation, *unless* you used the `tokio` crate or another crate that provides a runtime for async Rust or an event loop; these are not allowed for this project (see below).

We provide two pieces of code:

### Rust Crate

The `kvonset-work` crate implements a key-value workload:

```
kvonset-work = { git = "https://github.com/cs1675/kvonset-work", features = ["request-gen", "argparse", "stats"] } 
```

You can view this crate's documentation in a convenient webpage form with `cargo doc -p kvonset-work`.
 
A new aspect of KVonset is that, unlike in Woonsocket's synthetic work, the workload concerns operations on map keys.  There are three types of operations:

- `mget([keys]) -> [(key, value)]`: The server should, for each key in the provided list, return the currently set value, or nothing if the key is not set.
- `mput([(key, value)]) -> [(key, old_value)]`: The server should, for each key and value in the provided list, (a) set `key = value` and (b) return the previously set value, `old_value` (or nothing if the key was not set). A `value` of nothing (`None`) deletes `key`; `kvonset-work` does this for about 5% of the keys in an `mput`.
- `range(key_start, key_end) -> [(key, value)]`: The server should return a list of keys and their currently set values for all *set* keys `k` such that `key_start <= k <= key_end`, sorted by key.

1.6% (= 4 / 256) of the workload will be `range`, 
12.5% (= 32 / 256) of the workload will be `mput`, 
and 86% (the remainder) will be `mget`. 

For `range`, `kvonset-work` will pick two keys uniformly at random in the key space, and use the smaller as `key_start` and the larger as `key_end`.
For `mget` and `mput`, the number of keys in the list will follow a [standard geometric](https://docs.rs/rand_distr/latest/rand_distr/struct.StandardGeometric.html) distribution.
`kvonset-work` will use a [Zipfian(1024, 1)](https://docs.rs/rand_distr/latest/rand_distr/struct.Zipf.html) distribution to determine which keys to get and put. 
This means that a small subset of a total of 1024 keys will be proportionally more popular than the remaining keys.

#### Mixed Workload

Unlike the previous project, `kvonset-work` mixes multiple types of requests in the same connection.

Using multiple request types also causes us to express our offered load in keys / second rather than requests / second, since some requests require less work.
The mean of a Geom(0.5) distribution is 2, so the average mget and mput request will have 2 keys in it.
Meanwhile, the mean of the difference of two keys in [0, 1023] sampled uniformly at random is about 1024 / 3 = 341.
So, 1.6% of requests have 341 keys (on average), and 98.4% of requests have 2 keys (on average).
So, the average request will have 7.3 keys.
We use this observation to set our script's request inter-arrival times.
For example, an attempted load of 250,000 keys / second would be 34,236 requests / second, or an average request inter-arrival time of 29 microseconds.
Similarly, an attempted load of 50,000 keys / second would require an average request inter-arrival of 146 microseconds.

You will have to decide how to present the mixed workload's performance data in your report. For example, it might be helpful to separate the impact of vectored IO on range requests specifically.

#### Warmup Phase

`kvonset-work`'s `GenerateRequests` type offers a warmup mode (`GenerateRequests::warmup_puts()`) which sets a value for each key in the keyspace using mput requests.
We strongly suggest that your client warm up the server with these requests before it starts the mixed workload.

#### Argument Parsing

Like `woonsocket-work`, `kvonset-work` also provides an optional argument parsing struct. Activate this with:

```
kvonset-work = { git = "https://github.com/cs1675/kvonset-work", features = ["argparse"] } 
```

### Local Running Script

We provide a Python script and Dockerfile to run your code in Docker containers on your machine.
This script tests that your implementation exposes the command-line argument interface that the course VMs expect.

The Python script (and grading server script) iterate through several attempted load values by default: 50,000, 100,000, 250,000, 500,000, 750,000, 1,000,000, and 1,250,000 keys / second.
To run a single attempted load instead, pass `--load <KEYS_PER_SEC>`.
To approximate the grading server's two CPU cores, pass `--server-cpus` (e.g., `--server-cpus 0,1`) to restrict the server container to those CPUs.

The script supports `--flamegraph` to run the server with `perf`. Some notes on this:
- You will need:

```
[profile.release]
debug = true
```

in your `Cargo.toml` to enable debug symbols (i.e., function names) in the flamegraph.
- To save disk space, the script deletes `perf.data`. Feel free to modify the script to keep it if you want.
- This will only work on Linux, and `perf` requires setting the `kernel.perf_event_paranoid` setting, which requires root. To run locally on other OSes or settings, you may want to use a VM.
- We will attempt to provide flamegraphs in the run server output to help you debug, but might need to turn this off close to the submission deadline.

### Third-Party Crates

Your implementation can use third-party Rust crates for basic functionality such as command-line argument functionality, serialization, error handling, or logging, but you *may not* use third-party crates that implement project components. 
Specifically, it is explicitly *disallowed* to use async Rust crates such as `tokio`.
Using `std` is fine.

Here is a list of third-party crates you do not need permission to use:

- This [io_uring](https://crates.io/crates/io-uring) crate.
- Crates that `kvonset-work` uses: [serde](https://serde.rs), [rand](https://crates.io/crates/rand), [rand_distr](https://crates.io/crates/rand_distr), [clap](https://crates.io/crates/clap), [quanta](https://crates.io/crates/quanta), and [hdrhistogram](https://crates.io/crates/hdrhistogram).
- Serialization-focused crates and [`serde`](https://serde.rs). [This benchmark](https://david.kolo.ski/rust_serialization_benchmark/) is a good rundown of available options.
- Hashmap crates such as [ahash](https://crates.io/crates/ahash) or [flurry](https://crates.io/crates/flurry).
- Synchronization primitive crates such as [parking_lot](https://crates.io/crates/parking_lot), [crossbeam](https://crates.io/crates/crossbeam), or [flume](https://crates.io/crates/flume).
- Logging crates such as [tracing](https://crates.io/crates/tracing) and [tracing-subscriber](https://crates.io/crates/tracing-subscriber).
- Error handling crates such as [anyhow](https://crates.io/crates/anyhow) or [eyre](https://crates.io/crates/eyre).
- Timing and instrumentation crates such as [quanta](https://crates.io/crates/quanta) or [perf_event](https://crates.io/crates/perf-event).

If there's a specific crate you're not sure about and want permission to use, please ask on EdStem.

## Required Behaviors

### Client

The client should use open-loop Poisson request arrivals. The client should use the `Iterator` provided by `kvonset_work` to generate requests.

A client sends a `kvonset_work::Request` to the server. `Request` is an enum with a variant for each of the three operations above, plus `Ping`, which is for local testing only and which `kvonset-work` never generates.

The client should use the command-line options as described below:

- `-r, --runtime-secs <RUNTIME_SECS>`: Provided hint to the client for when to finish writing logs and exit before the runner script terminates this process
- `--ip <IP>`: Server IP.
- `-p, --port <PORT>`: Port that the server is listening on.
- `-s, --seed <SEED>`: Accept a seed to pass to the workload generator. The run script always passes one for the `optimized` strategy.
- `-l, --load-keys-attempted <LOAD_KEYS_ATTEMPTED>`: Attempted load to offer, in keys / second. See [Mixed Workload](#mixed-workload) for how to convert this to a mean request inter-arrival time.
- `-o, --outpath <OUTPATH>`: The directory in which to store the results. Only files written to this directory will be preserved; the runner script will delete all other files.

The run script will terminate the client soon after the CLI-specified duration of `runtime-secs`, so implementations should definitely exit gracefully before this.

### Server

You should evaluate at least the following implementation configurations:

1. A baseline server that just uses socket `recv`/`send` or similar to communicate with its clients
2. A server that uses vectored I/O operations on sockets to communicate with its clients
3. A server that communicates with its clients using socket I/O operations batched with `io_uring`
4. If the configurations above don't already meet the SLO, then additionally an optimized implementation that does achieve the target SLO.

#### Linearizability

Each implementation must provide *linearizability*. 
This means that, although the server could process multiple requests at the same time, there must be some linear ordering of requests that produces the responses your server generates, but this need not be the order in which they were sent. Specifically:

- If an read `g` returns a particular value `v` and there are no further writes to the key, all subsequent reads sent after the response to `g` arrives should return the same value `v`.

- A get request or range request `r` or should return a snapshot of values after an entire `put` request. 

For example:

```
t0: [k1 = v11, k2 = v21, ..., k8 = v81, ...]
t1: req_r1 = range(k1, k10) arrives
t2: read into resp_r1 = [k1 = v11, k2 = v21]
t3: req_p1 = mput(k1 = v12, k8 = v82) arrives
t4: do the put
t5: read into resp_r1 = [k3=...., k8 = v82, ...]
t6: return resp_r2, resp_p1
```

In this case `resp_r1` contains `[k1 = v11, ..., k8 = v82]`, which violates linearizability. Instead, the valid responses would be either `[k1 = v11, ..., k8 = v81]` or `[k1 = v12, ... k8 = v82]`

- It's possible for put requests to have the same key multiple times in their payload; in these cases the *last* put should count, but `old_value` can be the value before the entire `mput`. Meanwhile, if a get request has the same key multiple times in its payload, linearizability implies that the two values for that key should be the same.

Your report should discuss how you ensured this property in your implementation.

#### CLI

Regardless of implementation, the server will listen for clients on the CLI-specified port, on all interfaces. It will accept client connections as clients arrive, serve clients concurrently, and write its logs to the CLI-specified directory. 
The run script will terminate the server soon after the CLI-specified duration, but implementations may wish to exit gracefully before this.

The server should respect command-line options as described below:

- `-r, --runtime-secs <RUNTIME_SECS>`: Hint for how long we expect the clients will run for.
- `-p, --port <PORT>`: Port to listen on.
- `-s, --strategy <STRATEGY>`: Implementation to use. [possible values: `send-recv`, `vectored-io`, `io-uring`, `optimized`]
- `-o, --outpath <OUTPATH>`: The directory in which to store any output logs. Only files written to this directory will be preserved; the runner script will delete all other files.
